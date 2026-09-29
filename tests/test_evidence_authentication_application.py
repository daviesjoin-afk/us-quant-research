"""Stage 6-B1: authenticating research evidence against an external trust root.

These tests exercise the boundary Stage 6-A explicitly deferred: a locally
persisted artifact whose internal fields are all mutually consistent can still
have been edited by anyone who can write the file.  The seal is what makes that
detectable, and the trust root is what makes the seal meaningful.
"""

from base64 import b64decode, b64encode
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from us_quant.research_evidence_sealing import (
    EvidenceSealingError,
    load_private_key,
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
from us_quant.trading.adapters.evidence_signature import (
    Ed25519EvidenceSignatureVerifier,
)
from us_quant.trading.adapters.evidence_trust_store import (
    FileEvidenceVerificationKeySource,
)
from us_quant.trading.adapters.research_evidence import TargetedReviewArtifactSource
from us_quant.trading.application.evidence_authentication import (
    EvidenceAuthenticationApplication,
)
from us_quant.trading.domain.evidence_auth import (
    AuthenticatedStrategyResearchEvidence,
    ED25519_ALGORITHM,
    EVIDENCE_SEAL_SCHEMA_VERSION,
    EvidenceAuthenticationBlocker as Blocker,
    EvidenceAuthenticationPolicy,
    EvidenceAuthenticationResult,
    EvidenceAuthenticationVerdict as Verdict,
    EvidenceKeyTrustStatus,
    EvidenceVerificationKey,
    ResearchEvidenceSeal,
    canonical_artifact_payload_digest,
    canonical_signed_material,
    default_seal_path,
    seal_to_payload,
    trust_store_to_payload,
)
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
KEY_ID = "research-key-1"
UNIVERSE_HASH = "universe-1"
CODE_HASH = "code-1"


def _review(**changes):
    gates = tuple(
        EvidenceGate(code, name, True, "yes", required, evidence)
        for code, name, required, evidence in TARGETED_REVIEW_GATE_DEFINITIONS
    )
    values = dict(
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
    values.update(changes)
    return TargetedReviewResult(**values)


def _version(**changes):
    values = dict(
        definition=StrategyDefinition("family-1", "Example", "test"),
        identity=StrategyIdentity("family-1", "version-1", "parameter-1"),
        semver="1.0.0", status=StrategyStatus.RESEARCH, mode=StrategyMode.RESEARCH,
        parameters={"period": 5}, universe_hash=UNIVERSE_HASH, code_hash=CODE_HASH,
        risk_budget_pct=Decimal("0.01"), gate_passed=False, gate_reason="legacy",
        created_at=NOW, updated_at=NOW,
    )
    values.update(changes)
    return StrategyVersion(**values)


def _write_trust_store(path, entries):
    Path(path).write_text(
        json.dumps(trust_store_to_payload(tuple(entries))), encoding="utf-8"
    )


def _build(
    tmp_path,
    *,
    version_changes=None,
    generated_at=NOW,
    signed_at=NOW,
    sign=True,
    key_id=KEY_ID,
    universe_hash=UNIVERSE_HASH,
    code_hash=CODE_HASH,
):
    """Lay out an artifact store and a *separate* key/trust directory."""

    artifacts = tmp_path / "artifacts"
    keys = tmp_path / "keys"
    artifacts.mkdir(parents=True, exist_ok=True)
    keys.mkdir(parents=True, exist_ok=True)

    artifact_path = save_targeted_review(_review(), artifacts)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["generated_at"] = generated_at.isoformat()
    artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    private_key_path = keys / "research-signing.key"
    public_key = write_signing_key(private_key_path)
    trust_store_path = keys / "trust-store.json"
    _write_trust_store(
        trust_store_path,
        [
            EvidenceVerificationKey(
                key_id=key_id,
                algorithm=ED25519_ALGORITHM,
                public_key=public_key,
                trust_status=EvidenceKeyTrustStatus.ACTIVE,
            )
        ],
    )

    seal_path = default_seal_path(artifact_path)
    if sign:
        seal_review_artifact(
            artifact_path=artifact_path,
            private_key_path=private_key_path,
            trust_store_path=trust_store_path,
            key_id=key_id,
            universe_hash=universe_hash,
            code_hash=code_hash,
            seal_path=seal_path,
            signed_at=signed_at,
        )

    return {
        "artifact": artifact_path,
        "seal": seal_path,
        "private_key": private_key_path,
        "trust_store": trust_store_path,
        "public_key": public_key,
        "version": _version(**(version_changes or {})),
    }


def _authenticate(ws, *, policy=None, verified_at=NOW, version=None, artifact=None, seal=None):
    application = EvidenceAuthenticationApplication(
        artifact_source=TargetedReviewArtifactSource(),
        key_source=FileEvidenceVerificationKeySource(ws["trust_store"]),
        signature_verifier=Ed25519EvidenceSignatureVerifier(),
    )
    return application.authenticate(
        version=version if version is not None else ws["version"],
        artifact_path=artifact if artifact is not None else ws["artifact"],
        seal_path=seal if seal is not None else ws["seal"],
        policy=policy if policy is not None else EvidenceAuthenticationPolicy(),
        verified_at=verified_at,
    )


def _edit_artifact(ws, mutate):
    path = ws["artifact"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _craft_seal(ws, *, signed_at=NOW, key_id=None, overrides=None, signature=None):
    """Build and sign a seal with full control, for adversarial cases."""

    private_key, _ = load_private_key(ws["private_key"])
    payload = json.loads(Path(ws["artifact"]).read_text(encoding="utf-8"))
    values = dict(
        schema_version=EVIDENCE_SEAL_SCHEMA_VERSION,
        review_run_id=payload["run_id"],
        strategy_version_id=payload["strategy_version_id"],
        strategy_semver=payload["strategy_semver"],
        parameter_hash=payload["base_parameter_hash"],
        universe_hash=UNIVERSE_HASH,
        code_hash=CODE_HASH,
        symbol=payload["symbol"],
        data_hash=payload["data_hash"],
        artifact_payload_sha256=canonical_artifact_payload_digest(payload),
        algorithm=ED25519_ALGORITHM,
        key_id=key_id if key_id is not None else KEY_ID,
        signed_at=signed_at,
        signature=b"\x00" * 64,
    )
    values.update(overrides or {})
    unsigned = ResearchEvidenceSeal(**values)
    final_signature = (
        signature
        if signature is not None
        else private_key.sign(canonical_signed_material(unsigned))
    )
    seal = ResearchEvidenceSeal(**{**values, "signature": final_signature})
    Path(ws["seal"]).write_text(
        json.dumps(seal_to_payload(seal), indent=2), encoding="utf-8"
    )
    return seal


def _rewrite_seal_payload(ws, mutate):
    path = Path(ws["seal"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# -- the happy path ------------------------------------------------------


def test_valid_seal_authenticates_and_binds_universe_and_code_hash(tmp_path):
    ws = _build(tmp_path)
    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.PASS
    assert outcome.result.blockers == ()
    assert outcome.result.key_id == KEY_ID
    assert outcome.result.algorithm == ED25519_ALGORITHM
    assert outcome.result.review_run_id == "review-1"
    assert outcome.result.artifact_digest == canonical_artifact_payload_digest(
        json.loads(ws["artifact"].read_text(encoding="utf-8"))
    )

    authenticated = outcome.authenticated_evidence
    assert isinstance(authenticated, AuthenticatedStrategyResearchEvidence)
    assert authenticated.universe_hash == UNIVERSE_HASH
    assert authenticated.code_hash == CODE_HASH
    assert authenticated.review_run_id == "review-1"
    assert authenticated.strategy_version_id == "version-1"


def test_verify_only_key_still_verifies_already_issued_evidence(tmp_path):
    """Rotation must not orphan history: VERIFY_ONLY keeps old evidence valid."""

    ws = _build(tmp_path)
    _write_trust_store(
        ws["trust_store"],
        [
            EvidenceVerificationKey(
                key_id=KEY_ID,
                algorithm=ED25519_ALGORITHM,
                public_key=ws["public_key"],
                trust_status=EvidenceKeyTrustStatus.VERIFY_ONLY,
            )
        ],
    )
    assert _authenticate(ws).result.verdict is Verdict.PASS


# -- artifact tampering --------------------------------------------------


def test_artifact_payload_change_fails_closed(tmp_path):
    ws = _build(tmp_path)
    _edit_artifact(ws, lambda row: row.__setitem__("provider", "provider-2"))

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert outcome.authenticated_evidence is None
    assert Blocker.ARTIFACT_DIGEST_MISMATCH in outcome.result.blockers


def test_observed_change_fails_closed(tmp_path):
    ws = _build(tmp_path)
    _edit_artifact(ws, lambda row: row["gates"][0].__setitem__("observed", "no"))

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.ARTIFACT_DIGEST_MISMATCH in outcome.result.blockers


def test_passed_change_with_consistent_aggregates_fails_closed(tmp_path):
    """The Stage 6-A deferred case: a *self-consistent* forgery is still caught.

    Stage 6-A accepts this payload because every aggregate agrees with the
    gates.  Only the detached seal can tell that the file was rewritten.
    """

    ws = _build(tmp_path)

    def forge(row):
        row["gates"][0]["passed"] = False
        row["passed_gates"] = 21
        row["blocking_failures"] = 1
        row["eligible_for_independent_review"] = False
        row["decision"] = "BLOCKED"
        row["status"] = "FAIL"

    _edit_artifact(ws, forge)
    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert outcome.authenticated_evidence is None
    assert Blocker.ARTIFACT_DIGEST_MISMATCH in outcome.result.blockers


def test_generated_at_change_fails_closed(tmp_path):
    ws = _build(tmp_path)
    _edit_artifact(
        ws,
        lambda row: row.__setitem__(
            "generated_at", (NOW + timedelta(days=1)).isoformat()
        ),
    )

    outcome = _authenticate(ws, verified_at=NOW + timedelta(days=2))

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.ARTIFACT_DIGEST_MISMATCH in outcome.result.blockers


def test_parameter_hash_change_fails_closed(tmp_path):
    ws = _build(tmp_path)
    _edit_artifact(
        ws, lambda row: row.__setitem__("base_parameter_hash", "parameter-2")
    )

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.ARTIFACT_DIGEST_MISMATCH in outcome.result.blockers
    assert Blocker.PARAMETER_HASH_MISMATCH in outcome.result.blockers


def test_symbol_change_fails_closed(tmp_path):
    ws = _build(tmp_path)
    _edit_artifact(ws, lambda row: row.__setitem__("symbol", "AAPQ"))

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.ARTIFACT_DIGEST_MISMATCH in outcome.result.blockers
    assert Blocker.STRATEGY_IDENTITY_MISMATCH in outcome.result.blockers


def test_data_hash_change_fails_closed(tmp_path):
    ws = _build(tmp_path)
    _edit_artifact(ws, lambda row: row.__setitem__("data_hash", "data-2"))

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.ARTIFACT_DIGEST_MISMATCH in outcome.result.blockers
    assert Blocker.DATA_HASH_MISMATCH in outcome.result.blockers


# -- seal tampering ------------------------------------------------------


def test_signature_change_fails_closed(tmp_path):
    ws = _build(tmp_path)

    def flip(row):
        raw = bytearray(b64decode(row["signature"]))
        raw[0] ^= 0x01
        row["signature"] = b64encode(bytes(raw)).decode("ascii")

    _rewrite_seal_payload(ws, flip)
    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.SIGNATURE_INVALID in outcome.result.blockers


def test_seal_signed_material_change_fails_closed(tmp_path):
    """Editing a signed seal field without re-signing must not verify."""

    ws = _build(tmp_path)
    _rewrite_seal_payload(ws, lambda row: row.__setitem__("code_hash", "code-other"))

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.SIGNATURE_INVALID in outcome.result.blockers


#: One different value per signed seal field.  Tampering with *any* of these
#: without re-signing has to break the signature -- that is the whole point of
#: enumerating the fields into the canonical material.
_TAMPERED_FIELD_VALUES = {
    "review_run_id": "review-2",
    "strategy_version_id": "version-2",
    "strategy_semver": "2.0.0",
    "parameter_hash": "parameter-2",
    "universe_hash": "universe-2",
    "code_hash": "code-2",
    "symbol": "MSFT",
    "data_hash": "data-2",
    "artifact_payload_sha256": "f" * 64,
    "signed_at": "2026-01-01T00:00:01Z",
}


@pytest.mark.parametrize("field", sorted(_TAMPERED_FIELD_VALUES))
def test_tampering_with_any_signed_seal_field_fails_closed(tmp_path, field):
    ws = _build(tmp_path)
    _rewrite_seal_payload(
        ws, lambda row: row.__setitem__(field, _TAMPERED_FIELD_VALUES[field])
    )

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.SIGNATURE_INVALID in outcome.result.blockers


def test_seal_review_run_change_fails_closed(tmp_path):
    """The seal must describe the artifact actually being verified."""

    ws = _build(tmp_path)
    _rewrite_seal_payload(ws, lambda row: row.__setitem__("review_run_id", "review-2"))

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.REVIEW_RUN_MISMATCH in outcome.result.blockers


def test_version_parameter_hash_change_fails_closed(tmp_path):
    """The seal's parameter hash must equal the *governed* version's."""

    ws = _build(
        tmp_path,
        version_changes={
            "identity": StrategyIdentity("family-1", "version-1", "parameter-2")
        },
    )
    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.PARAMETER_HASH_MISMATCH in outcome.result.blockers


def test_version_semver_change_fails_closed(tmp_path):
    ws = _build(tmp_path, version_changes={"semver": "2.0.0"})
    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.STRATEGY_IDENTITY_MISMATCH in outcome.result.blockers


def test_code_hash_change_fails_closed(tmp_path):
    """The seal asserts code_hash; a governed version that disagrees is refused."""

    ws = _build(tmp_path, version_changes={"code_hash": "code-other"})
    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.CODE_HASH_MISMATCH in outcome.result.blockers


def test_universe_hash_change_fails_closed(tmp_path):
    ws = _build(tmp_path, version_changes={"universe_hash": "universe-other"})
    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.UNIVERSE_HASH_MISMATCH in outcome.result.blockers


def test_seal_for_a_different_version_fails_closed(tmp_path):
    ws = _build(tmp_path)
    _rewrite_seal_payload(
        ws, lambda row: row.__setitem__("strategy_version_id", "version-2")
    )

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert outcome.result.blockers


def test_missing_seal_fails_closed(tmp_path):
    ws = _build(tmp_path, sign=False)
    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert outcome.authenticated_evidence is None
    assert Blocker.AUTHENTICATION_MISSING in outcome.result.blockers


def test_malformed_seal_fails_closed(tmp_path):
    ws = _build(tmp_path)
    Path(ws["seal"]).write_text("{ not json", encoding="utf-8")

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.SIGNATURE_MALFORMED in outcome.result.blockers


def test_seal_with_wrong_schema_version_fails_closed(tmp_path):
    ws = _build(tmp_path)
    _rewrite_seal_payload(ws, lambda row: row.__setitem__("schema_version", "v0"))

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.SIGNATURE_MALFORMED in outcome.result.blockers


def test_unsupported_algorithm_fails_closed(tmp_path):
    ws = _build(tmp_path)
    _rewrite_seal_payload(ws, lambda row: row.__setitem__("algorithm", "rsa-pkcs1"))

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.UNSUPPORTED_ALGORITHM in outcome.result.blockers


# -- trust root ----------------------------------------------------------


def test_unknown_key_fails_closed(tmp_path):
    ws = _build(tmp_path)
    other_public = write_signing_key(tmp_path / "keys" / "other.key")
    _write_trust_store(
        ws["trust_store"],
        [
            EvidenceVerificationKey(
                key_id="some-other-key",
                algorithm=ED25519_ALGORITHM,
                public_key=other_public,
                trust_status=EvidenceKeyTrustStatus.ACTIVE,
            )
        ],
    )

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.UNKNOWN_KEY in outcome.result.blockers


def test_wrong_public_key_fails_closed(tmp_path):
    ws = _build(tmp_path)
    other_public = write_signing_key(tmp_path / "keys" / "other.key")
    _write_trust_store(
        ws["trust_store"],
        [
            EvidenceVerificationKey(
                key_id=KEY_ID,
                algorithm=ED25519_ALGORITHM,
                public_key=other_public,
                trust_status=EvidenceKeyTrustStatus.ACTIVE,
            )
        ],
    )

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.SIGNATURE_INVALID in outcome.result.blockers


def test_revoked_key_fails_closed(tmp_path):
    ws = _build(tmp_path)
    _write_trust_store(
        ws["trust_store"],
        [
            EvidenceVerificationKey(
                key_id=KEY_ID,
                algorithm=ED25519_ALGORITHM,
                public_key=ws["public_key"],
                trust_status=EvidenceKeyTrustStatus.REVOKED,
            )
        ],
    )

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert outcome.authenticated_evidence is None
    assert Blocker.KEY_REVOKED in outcome.result.blockers


def test_missing_trust_root_fails_closed(tmp_path):
    ws = _build(tmp_path)
    ws["trust_store"] = tmp_path / "keys" / "does-not-exist.json"

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.TRUST_ROOT_UNAVAILABLE in outcome.result.blockers


def test_malformed_trust_root_fails_closed(tmp_path):
    ws = _build(tmp_path)
    Path(ws["trust_store"]).write_text('{"schema_version": "nope"}', encoding="utf-8")

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.TRUST_ROOT_UNAVAILABLE in outcome.result.blockers


def test_revocation_takes_effect_without_restart(tmp_path):
    """The trust root is re-read per verification, so revocation is immediate."""

    ws = _build(tmp_path)
    assert _authenticate(ws).result.verdict is Verdict.PASS

    _write_trust_store(
        ws["trust_store"],
        [
            EvidenceVerificationKey(
                key_id=KEY_ID,
                algorithm=ED25519_ALGORITHM,
                public_key=ws["public_key"],
                trust_status=EvidenceKeyTrustStatus.REVOKED,
            )
        ],
    )

    assert _authenticate(ws).result.verdict is Verdict.FAIL


# -- timestamps ----------------------------------------------------------


def test_naive_signed_at_fails_closed(tmp_path):
    ws = _build(tmp_path)
    _craft_seal(ws, signed_at=datetime(2026, 1, 1))

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.SIGNED_AT_INVALID in outcome.result.blockers


def test_future_signed_at_fails_closed(tmp_path):
    ws = _build(tmp_path)
    _craft_seal(ws, signed_at=NOW + timedelta(hours=1))

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.SIGNED_AT_INVALID in outcome.result.blockers


def test_signed_at_within_clock_skew_is_accepted(tmp_path):
    ws = _build(tmp_path)
    _craft_seal(ws, signed_at=NOW + timedelta(minutes=1))

    assert _authenticate(ws).result.verdict is Verdict.PASS


def test_unparseable_signed_at_fails_closed(tmp_path):
    ws = _build(tmp_path)
    _rewrite_seal_payload(ws, lambda row: row.__setitem__("signed_at", "not-a-time"))

    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert Blocker.SIGNATURE_MALFORMED in outcome.result.blockers


# -- identity, idempotency, and lifecycle neutrality ---------------------


def test_verification_is_idempotent_across_clocks(tmp_path):
    ws = _build(tmp_path)
    first = _authenticate(ws, verified_at=NOW)
    second = _authenticate(ws, verified_at=NOW + timedelta(hours=3))

    assert first.result.authentication_id == second.result.authentication_id
    assert first.result.verdict is second.result.verdict is Verdict.PASS


def test_revocation_produces_a_distinct_identity(tmp_path):
    """A revocation is a new outcome, not an overwrite of the earlier PASS."""

    ws = _build(tmp_path)
    passing = _authenticate(ws).result
    _write_trust_store(
        ws["trust_store"],
        [
            EvidenceVerificationKey(
                key_id=KEY_ID,
                algorithm=ED25519_ALGORITHM,
                public_key=ws["public_key"],
                trust_status=EvidenceKeyTrustStatus.REVOKED,
            )
        ],
    )
    revoked = _authenticate(ws).result

    assert revoked.verdict is Verdict.FAIL
    assert revoked.authentication_id != passing.authentication_id


def test_authenticated_evidence_cannot_be_minted_directly(tmp_path):
    """Neither a missing token nor a forged one may mint authenticated evidence."""

    ws = _build(tmp_path)
    outcome = _authenticate(ws)
    authenticated = outcome.authenticated_evidence
    assert authenticated is not None

    with pytest.raises(TypeError):
        AuthenticatedStrategyResearchEvidence(
            authenticated.evidence,
            outcome.result,
            authenticated.seal,
        )

    with pytest.raises(TypeError):
        AuthenticatedStrategyResearchEvidence(
            authenticated.evidence,
            outcome.result,
            authenticated.seal,
            _authenticator_token=object(),
        )


def test_authentication_does_not_change_strategy_lifecycle_fields(tmp_path):
    ws = _build(tmp_path)
    version = ws["version"]
    before = (version.status, version.mode, version.gate_passed, version.gate_reason)

    outcome = _authenticate(ws, version=version)

    assert outcome.result.verdict is Verdict.PASS
    assert (version.status, version.mode, version.gate_passed, version.gate_reason) == before
    assert version.status is StrategyStatus.RESEARCH


def test_failed_authentication_yields_no_authenticated_evidence(tmp_path):
    ws = _build(tmp_path, sign=False)
    outcome = _authenticate(ws)

    assert outcome.result.verdict is Verdict.FAIL
    assert outcome.authenticated_evidence is None


def test_sealing_tool_refuses_to_sign_with_a_revoked_key(tmp_path):
    ws = _build(tmp_path, sign=False)
    _write_trust_store(
        ws["trust_store"],
        [
            EvidenceVerificationKey(
                key_id=KEY_ID,
                algorithm=ED25519_ALGORITHM,
                public_key=ws["public_key"],
                trust_status=EvidenceKeyTrustStatus.REVOKED,
            )
        ],
    )

    with pytest.raises(EvidenceSealingError):
        seal_review_artifact(
            artifact_path=ws["artifact"],
            private_key_path=ws["private_key"],
            trust_store_path=ws["trust_store"],
            key_id=KEY_ID,
            universe_hash=UNIVERSE_HASH,
            code_hash=CODE_HASH,
            seal_path=ws["seal"],
            signed_at=NOW,
        )
