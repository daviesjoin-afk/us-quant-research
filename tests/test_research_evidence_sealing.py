"""Stage 6-B1: the research-side sealing tool.

Signing is research tooling, not runtime.  These tests pin the properties that
make that split real: the tool refuses keys it should not use, refuses to write
a private key where it must never live, and produces a seal the *runtime*
independently verifies.
"""

from base64 import b64decode
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat

import pytest

from us_quant.research_evidence_sealing import (
    EvidenceSealingError,
    assert_private_key_is_outside,
    load_private_key,
    main,
    read_trust_store,
    seal_review_artifact,
    write_signing_key,
)
from us_quant.targeted_review import (
    DependenceDiagnostic,
    EvidenceGate,
    TargetedReviewResult,
    TARGETED_REVIEW_GATE_DEFINITIONS,
    save_targeted_review,
)
from us_quant.trading.domain.evidence_auth import (
    ED25519_ALGORITHM,
    EvidenceKeyTrustStatus,
    EvidenceVerificationKey,
    default_seal_path,
    trust_store_to_payload,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
KEY_ID = "research-key-1"


def _artifact(tmp_path):
    gates = tuple(
        EvidenceGate(code, name, True, "yes", required, evidence)
        for code, name, required, evidence in TARGETED_REVIEW_GATE_DEFINITIONS
    )
    result = TargetedReviewResult(
        run_id="review-1", robustness_run_id="robust-1", validation_run_id="validation-1",
        overfit_run_id="overfit-1", data_quality_run_id="quality-1",
        execution_stress_run_id="stress-1", symbol="AAPL", strategy_version_id="version-1",
        strategy_semver="1.0.0", base_parameter_hash="parameter-1", data_hash="data-1",
        provider="provider-1", evidence_origins=("captured_stream",),
        dependence=DependenceDiagnostic(12, None, None, 0, None, None, None, "pass"),
        gates=gates, passed_gates=len(gates), blocking_failures=0, warnings=(),
        decision="ELIGIBLE_FOR_INDEPENDENT_REVIEW", eligible_for_independent_review=True,
        status="PASS",
    )
    store = tmp_path / "artifacts"
    store.mkdir(parents=True, exist_ok=True)
    return save_targeted_review(result, store)


def _trust_store(tmp_path, public_key, *, status=EvidenceKeyTrustStatus.ACTIVE, key_id=KEY_ID):
    path = tmp_path / "keys" / "trust-store.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            trust_store_to_payload(
                (
                    EvidenceVerificationKey(
                        key_id=key_id, algorithm=ED25519_ALGORITHM,
                        public_key=public_key, trust_status=status,
                    ),
                )
            )
        ),
        encoding="utf-8",
    )
    return path


def _workspace(tmp_path, *, status=EvidenceKeyTrustStatus.ACTIVE):
    artifact = _artifact(tmp_path)
    private_key = tmp_path / "keys" / "research.key"
    private_key.parent.mkdir(parents=True, exist_ok=True)
    public_key = write_signing_key(private_key)
    return {
        "artifact": artifact,
        "private_key": private_key,
        "trust_store": _trust_store(tmp_path, public_key, status=status),
        "public_key": public_key,
    }


def _seal(ws, **changes):
    values = dict(
        artifact_path=ws["artifact"],
        private_key_path=ws["private_key"],
        trust_store_path=ws["trust_store"],
        key_id=KEY_ID,
        universe_hash="universe-1",
        code_hash="code-1",
        signed_at=NOW,
    )
    values.update(changes)
    return seal_review_artifact(**values)


# -- key generation ------------------------------------------------------


def test_generate_key_writes_a_base64_private_key_and_returns_the_public_half(tmp_path):
    path = tmp_path / "keys" / "research.key"
    public_key = write_signing_key(path)

    assert len(public_key) == 32
    assert len(b64decode(path.read_text(encoding="utf-8").strip())) == 32
    _, derived_public = load_private_key(path)
    assert derived_public == public_key


def test_generated_keys_are_distinct(tmp_path):
    first = write_signing_key(tmp_path / "keys" / "a.key")
    second = write_signing_key(tmp_path / "keys" / "b.key")

    assert first != second


def test_private_key_is_created_owner_only_and_never_clobbered(tmp_path, monkeypatch):
    """A signing key another local user can read is one they can forge with.

    Asserted on the ``os.open`` call rather than on ``st_mode`` so the property
    is checked on every platform: Windows does not report group/other bits.
    """

    recorded: dict = {}
    real_open = os.open

    def spy(path, flags, mode=0o777):
        recorded["mode"] = mode
        recorded["flags"] = flags
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", spy)
    write_signing_key(tmp_path / "keys" / "research.key")

    assert recorded["mode"] == 0o600
    assert recorded["flags"] & os.O_EXCL


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")
def test_private_key_mode_is_owner_only_on_disk(tmp_path):
    path = tmp_path / "keys" / "research.key"
    write_signing_key(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_refuses_to_overwrite_an_existing_private_key(tmp_path):
    """Replacing a key in place would invalidate every seal it ever signed."""

    path = tmp_path / "keys" / "research.key"
    write_signing_key(path)
    original = path.read_bytes()

    with pytest.raises(EvidenceSealingError):
        write_signing_key(path)

    assert path.read_bytes() == original


# -- where a private key must never live ---------------------------------


def test_refuses_a_private_key_inside_a_git_working_tree(tmp_path):
    checkout = tmp_path / "repo"
    (checkout / ".git").mkdir(parents=True)

    with pytest.raises(EvidenceSealingError):
        write_signing_key(checkout / "keys" / "research.key")


def test_refuses_a_private_key_inside_the_artifact_store(tmp_path):
    ws = _workspace(tmp_path)
    stray = ws["artifact"].parent / "research.key"
    stray.write_bytes(ws["private_key"].read_bytes())

    with pytest.raises(EvidenceSealingError):
        _seal(ws, private_key_path=stray)


def test_accepts_a_private_key_outside_the_artifact_store(tmp_path):
    ws = _workspace(tmp_path)
    assert_private_key_is_outside(ws["private_key"], artifact_path=ws["artifact"])


# -- what the tool will sign with ----------------------------------------


def test_refuses_an_unknown_key(tmp_path):
    ws = _workspace(tmp_path)

    with pytest.raises(EvidenceSealingError):
        _seal(ws, key_id="not-in-the-store")


def test_refuses_a_verify_only_key(tmp_path):
    ws = _workspace(tmp_path, status=EvidenceKeyTrustStatus.VERIFY_ONLY)

    with pytest.raises(EvidenceSealingError):
        _seal(ws)


def test_refuses_a_revoked_key(tmp_path):
    ws = _workspace(tmp_path, status=EvidenceKeyTrustStatus.REVOKED)

    with pytest.raises(EvidenceSealingError):
        _seal(ws)


def test_refuses_a_private_key_that_does_not_match_the_registered_public_key(tmp_path):
    ws = _workspace(tmp_path)
    other_public = write_signing_key(tmp_path / "keys" / "other.key")
    ws["trust_store"] = _trust_store(tmp_path, other_public)

    with pytest.raises(EvidenceSealingError):
        _seal(ws)


def test_refuses_a_naive_signed_at(tmp_path):
    ws = _workspace(tmp_path)

    with pytest.raises(EvidenceSealingError):
        _seal(ws, signed_at=datetime(2026, 1, 1))


def test_refuses_blank_universe_or_code_hash(tmp_path):
    ws = _workspace(tmp_path)

    with pytest.raises(EvidenceSealingError):
        _seal(ws, universe_hash="   ")
    with pytest.raises(EvidenceSealingError):
        _seal(ws, code_hash="")


def test_refuses_an_artifact_that_does_not_load(tmp_path):
    ws = _workspace(tmp_path)
    Path(ws["artifact"]).write_text("{ not json", encoding="utf-8")

    with pytest.raises(EvidenceSealingError):
        _seal(ws)


# -- the seal itself -----------------------------------------------------


def test_seal_is_detached_and_never_modifies_its_artifact(tmp_path):
    ws = _workspace(tmp_path)
    before = Path(ws["artifact"]).read_bytes()

    seal, destination = _seal(ws)

    assert Path(ws["artifact"]).read_bytes() == before
    assert destination == default_seal_path(ws["artifact"])
    assert destination.exists()
    assert destination.parent == Path(ws["artifact"]).parent
    assert destination != Path(ws["artifact"])
    assert seal.review_run_id == "review-1"
    assert seal.universe_hash == "universe-1"
    assert seal.code_hash == "code-1"
    assert seal.key_id == KEY_ID


def test_seal_binds_the_artifact_canonical_digest(tmp_path):
    ws = _workspace(tmp_path)
    seal, _ = _seal(ws)
    payload = json.loads(Path(ws["artifact"]).read_text(encoding="utf-8"))

    from us_quant.trading.domain.evidence_auth import (
        canonical_artifact_payload_digest,
    )

    assert seal.artifact_payload_sha256 == canonical_artifact_payload_digest(payload)


def test_seal_can_be_written_to_an_explicit_path(tmp_path):
    ws = _workspace(tmp_path)
    explicit = tmp_path / "elsewhere" / "custom.seal.json"
    explicit.parent.mkdir(parents=True, exist_ok=True)

    _, destination = _seal(ws, seal_path=explicit)

    assert destination == explicit
    assert not default_seal_path(ws["artifact"]).exists()


def test_signed_at_is_recorded_as_utc(tmp_path):
    ws = _workspace(tmp_path)
    seal, destination = _seal(ws)

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["signed_at"] == "2026-01-01T00:00:00Z"
    assert seal.signed_at == NOW


# -- trust store helpers -------------------------------------------------


def test_read_trust_store_rejects_a_missing_file(tmp_path):
    with pytest.raises(EvidenceSealingError):
        read_trust_store(tmp_path / "absent.json")


def test_read_trust_store_rejects_malformed_json(tmp_path):
    path = tmp_path / "trust.json"
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(EvidenceSealingError):
        read_trust_store(path)


# -- CLI -----------------------------------------------------------------


def test_cli_generate_key_writes_the_key_and_a_usable_trust_store(tmp_path, capsys):
    private_key = tmp_path / "keys" / "research.key"
    trust_store = tmp_path / "keys" / "trust-store.json"

    exit_code = main(
        [
            "generate-key",
            "--private-key", str(private_key),
            "--key-id", KEY_ID,
            "--trust-store", str(trust_store),
        ]
    )

    assert exit_code == 0
    assert private_key.exists()
    keys = read_trust_store(trust_store)
    assert [key.key_id for key in keys] == [KEY_ID]
    assert keys[0].trust_status is EvidenceKeyTrustStatus.ACTIVE
    _, derived_public = load_private_key(private_key)
    assert keys[0].public_key == derived_public


def test_cli_refuses_to_replace_an_existing_key_id(tmp_path):
    """Re-registering a key id would silently invalidate all of its history."""

    private_key = tmp_path / "keys" / "a.key"
    trust_store = tmp_path / "keys" / "trust-store.json"
    assert main(
        [
            "generate-key",
            "--private-key", str(private_key),
            "--key-id", KEY_ID,
            "--trust-store", str(trust_store),
        ]
    ) == 0
    before = trust_store.read_text(encoding="utf-8")

    exit_code = main(
        [
            "generate-key",
            "--private-key", str(tmp_path / "keys" / "b.key"),
            "--key-id", KEY_ID,
            "--trust-store", str(trust_store),
        ]
    )

    assert exit_code == 2
    assert trust_store.read_text(encoding="utf-8") == before
    # The original key must still be the one the store trusts.
    _, derived_public = load_private_key(private_key)
    assert read_trust_store(trust_store)[0].public_key == derived_public


def test_cli_generate_key_preserves_other_trust_store_entries(tmp_path):
    trust_store = tmp_path / "keys" / "trust-store.json"
    first_public = write_signing_key(tmp_path / "keys" / "a.key")
    trust_store.parent.mkdir(parents=True, exist_ok=True)
    trust_store.write_text(
        json.dumps(
            trust_store_to_payload(
                (
                    EvidenceVerificationKey(
                        key_id="existing-key",
                        algorithm=ED25519_ALGORITHM,
                        public_key=first_public,
                        trust_status=EvidenceKeyTrustStatus.VERIFY_ONLY,
                    ),
                )
            )
        ),
        encoding="utf-8",
    )

    assert main(
        [
            "generate-key",
            "--private-key", str(tmp_path / "keys" / "b.key"),
            "--key-id", "new-key",
            "--trust-store", str(trust_store),
        ]
    ) == 0

    keys = {key.key_id: key for key in read_trust_store(trust_store)}
    assert set(keys) == {"existing-key", "new-key"}
    assert keys["existing-key"].trust_status is EvidenceKeyTrustStatus.VERIFY_ONLY
    assert keys["existing-key"].public_key == first_public


def test_cli_seal_writes_a_detached_seal(tmp_path, capsys):
    ws = _workspace(tmp_path)

    exit_code = main(
        [
            "seal",
            "--artifact", str(ws["artifact"]),
            "--private-key", str(ws["private_key"]),
            "--trust-store", str(ws["trust_store"]),
            "--key-id", KEY_ID,
            "--universe-hash", "universe-1",
            "--code-hash", "code-1",
            "--signed-at", NOW.isoformat(),
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["key_id"] == KEY_ID
    assert Path(report["seal_path"]).exists()


def test_cli_reports_an_unparseable_signed_at_without_traceback(tmp_path, capsys):
    ws = _workspace(tmp_path)

    exit_code = main(
        [
            "seal",
            "--artifact", str(ws["artifact"]),
            "--private-key", str(ws["private_key"]),
            "--trust-store", str(ws["trust_store"]),
            "--key-id", KEY_ID,
            "--universe-hash", "universe-1",
            "--code-hash", "code-1",
            "--signed-at", "yesterday",
        ]
    )

    assert exit_code == 2
    assert "error:" in capsys.readouterr().err


def test_cli_reports_a_naive_signed_at_without_traceback(tmp_path, capsys):
    ws = _workspace(tmp_path)

    exit_code = main(
        [
            "seal",
            "--artifact", str(ws["artifact"]),
            "--private-key", str(ws["private_key"]),
            "--trust-store", str(ws["trust_store"]),
            "--key-id", KEY_ID,
            "--universe-hash", "universe-1",
            "--code-hash", "code-1",
            "--signed-at", "2026-01-01T00:00:00",
        ]
    )

    assert exit_code == 2
    assert "error:" in capsys.readouterr().err


def test_cli_reports_a_refusal_without_traceback(tmp_path, capsys):
    ws = _workspace(tmp_path, status=EvidenceKeyTrustStatus.REVOKED)

    exit_code = main(
        [
            "seal",
            "--artifact", str(ws["artifact"]),
            "--private-key", str(ws["private_key"]),
            "--trust-store", str(ws["trust_store"]),
            "--key-id", KEY_ID,
            "--universe-hash", "universe-1",
            "--code-hash", "code-1",
        ]
    )

    assert exit_code == 2
    assert "error:" in capsys.readouterr().err
