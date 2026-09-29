"""Stage 6-B1 domain: canonical signed material, seals, and the trust store."""

from base64 import b64encode
from datetime import datetime, timedelta, timezone
import json

import pytest

from us_quant.trading.domain.evidence_auth import (
    ED25519_ALGORITHM,
    EVIDENCE_SEAL_SCHEMA_VERSION,
    EVIDENCE_TRUST_STORE_SCHEMA_VERSION,
    EvidenceAuthenticationBlocker as Blocker,
    EvidenceAuthenticationResult,
    EvidenceAuthenticationVerdict as Verdict,
    EvidenceKeyTrustStatus,
    EvidenceSealMalformed,
    EvidenceTrustStoreMalformed,
    EvidenceVerificationKey,
    ResearchEvidenceSeal,
    canonical_artifact_payload_digest,
    canonical_signed_material,
    canonical_timestamp,
    default_seal_path,
    seal_from_payload,
    seal_to_payload,
    stable_evidence_authentication_id,
    trust_store_from_payload,
    trust_store_to_payload,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATER = datetime(2026, 6, 1, 12, 30, 45, 123456, tzinfo=timezone.utc)


def _seal(**changes):
    values = dict(
        schema_version=EVIDENCE_SEAL_SCHEMA_VERSION,
        review_run_id="review-1",
        strategy_version_id="version-1",
        strategy_semver="1.0.0",
        parameter_hash="parameter-1",
        universe_hash="universe-1",
        code_hash="code-1",
        symbol="AAPL",
        data_hash="data-1",
        artifact_payload_sha256="a" * 64,
        algorithm=ED25519_ALGORITHM,
        key_id="key-1",
        signed_at=NOW,
        signature=b"\x01" * 64,
    )
    values.update(changes)
    return ResearchEvidenceSeal(**values)


# -- canonical signed material ------------------------------------------


def test_signature_is_not_part_of_its_own_signed_material():
    first = _seal(signature=b"\x01" * 64)
    second = _seal(signature=b"\xff" * 64)

    assert canonical_signed_material(first) == canonical_signed_material(second)


@pytest.mark.parametrize(
    "field",
    [
        "review_run_id", "strategy_version_id", "strategy_semver", "parameter_hash",
        "universe_hash", "code_hash", "symbol", "data_hash",
        "artifact_payload_sha256", "algorithm", "key_id",
    ],
)
def test_every_signed_field_changes_the_signed_material(field):
    baseline = canonical_signed_material(_seal())
    mutated = canonical_signed_material(_seal(**{field: "different-value"}))

    assert baseline != mutated


def test_signed_at_change_moves_the_signed_material():
    assert canonical_signed_material(_seal(signed_at=NOW)) != canonical_signed_material(
        _seal(signed_at=NOW + timedelta(seconds=1))
    )


def test_signed_material_is_utf8_canonical_json():
    material = canonical_signed_material(_seal())

    assert isinstance(material, bytes)
    text = material.decode("utf-8")
    assert " " not in text
    assert text.startswith("{")
    # Sorted keys and no ASCII escaping, so non-ASCII symbols stay readable.
    assert text.index('"algorithm"') < text.index('"artifact_payload_sha256"')
    assert canonical_signed_material(_seal(symbol="\u4e2d\u6587")) == canonical_signed_material(
        _seal(symbol="\u4e2d\u6587")
    )


def test_canonical_timestamp_normalises_aware_values_to_utc():
    offset = datetime(2026, 1, 1, 8, 0, tzinfo=timezone(timedelta(hours=8)))

    assert canonical_timestamp(offset) == "2026-01-01T00:00:00Z"
    assert canonical_timestamp(LATER) == "2026-06-01T12:30:45.123456Z"


def test_canonical_timestamp_leaves_naive_values_alone():
    naive = datetime(2026, 1, 1, 0, 0, 0)

    assert canonical_timestamp(naive) == "2026-01-01T00:00:00"


def test_naive_and_aware_signed_at_are_deterministic_and_distinct():
    naive = canonical_signed_material(_seal(signed_at=datetime(2026, 1, 1)))
    aware = canonical_signed_material(_seal(signed_at=NOW))

    assert naive == canonical_signed_material(_seal(signed_at=datetime(2026, 1, 1)))
    assert naive != aware


# -- artifact digest -----------------------------------------------------


def test_artifact_digest_ignores_key_order_and_formatting():
    first = {"run_id": "review-1", "symbol": "AAPL", "gates": [{"code": "x"}]}
    second = {"gates": [{"code": "x"}], "symbol": "AAPL", "run_id": "review-1"}

    assert canonical_artifact_payload_digest(first) == canonical_artifact_payload_digest(second)
    # Same object, re-serialised with indentation: still the same digest.
    assert canonical_artifact_payload_digest(first) == canonical_artifact_payload_digest(
        json.loads(json.dumps(first, indent=4))
    )


def test_artifact_digest_moves_on_any_semantic_change():
    baseline = {"run_id": "review-1", "symbol": "AAPL", "passed_gates": 22}

    assert canonical_artifact_payload_digest(baseline) != canonical_artifact_payload_digest(
        {**baseline, "symbol": "AAPQ"}
    )
    assert canonical_artifact_payload_digest(baseline) != canonical_artifact_payload_digest(
        {**baseline, "passed_gates": 21}
    )


# -- seal serialisation --------------------------------------------------


def test_seal_roundtrip_preserves_every_signed_field():
    seal = _seal(signed_at=LATER)
    restored = seal_from_payload(json.loads(json.dumps(seal_to_payload(seal))))

    assert restored == seal
    assert canonical_signed_material(restored) == canonical_signed_material(seal)


def test_seal_payload_serialises_the_signature_as_base64():
    payload = seal_to_payload(_seal())

    assert payload["signature"] == b64encode(b"\x01" * 64).decode("ascii")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schema_version": "something-else"},
        "not-an-object",
        [],
    ],
)
def test_seal_parsing_refuses_unsupported_shapes(payload):
    with pytest.raises(EvidenceSealMalformed):
        seal_from_payload(payload)


def test_seal_parsing_refuses_a_missing_signed_field():
    payload = seal_to_payload(_seal())
    payload.pop("code_hash")

    with pytest.raises(EvidenceSealMalformed):
        seal_from_payload(payload)


def test_seal_parsing_refuses_a_non_base64_signature():
    payload = seal_to_payload(_seal())
    payload["signature"] = "not base64 !!!"

    with pytest.raises(EvidenceSealMalformed):
        seal_from_payload(payload)


def test_seal_parsing_refuses_an_unparseable_timestamp():
    payload = seal_to_payload(_seal())
    payload["signed_at"] = "yesterday"

    with pytest.raises(EvidenceSealMalformed):
        seal_from_payload(payload)


def test_seal_parsing_accepts_a_naive_timestamp_for_later_policy_rejection():
    """Structural parsing must not pre-empt the SIGNED_AT_INVALID blocker."""

    payload = seal_to_payload(_seal())
    payload["signed_at"] = "2026-01-01T00:00:00"

    assert seal_from_payload(payload).signed_at.tzinfo is None


def test_seal_requires_a_signature():
    with pytest.raises(ValueError):
        _seal(signature=b"")


def test_signature_digest_identifies_the_signature():
    assert _seal(signature=b"\x01" * 64).signature_digest == _seal(
        signature=b"\x01" * 64
    ).signature_digest
    assert _seal(signature=b"\x01" * 64).signature_digest != _seal(
        signature=b"\x02" * 64
    ).signature_digest


def test_default_seal_path_is_detached_from_the_artifact():
    assert default_seal_path("store/review-1.json") == default_seal_path(
        "store/review-1.json"
    )
    assert str(default_seal_path("store/review-1.json")).endswith("review-1.seal.json")


# -- trust store ---------------------------------------------------------


def _key(key_id="key-1", status=EvidenceKeyTrustStatus.ACTIVE, public_key=b"\x02" * 32):
    return EvidenceVerificationKey(
        key_id=key_id, algorithm=ED25519_ALGORITHM, public_key=public_key,
        trust_status=status,
    )


def test_trust_store_roundtrip_carries_no_private_material():
    keys = (
        _key("key-1", EvidenceKeyTrustStatus.ACTIVE),
        _key("key-2", EvidenceKeyTrustStatus.REVOKED, public_key=b"\x03" * 32),
        _key("key-3", EvidenceKeyTrustStatus.VERIFY_ONLY, public_key=b"\x04" * 32),
    )
    payload = trust_store_to_payload(keys)
    text = json.dumps(payload)

    assert payload["schema_version"] == EVIDENCE_TRUST_STORE_SCHEMA_VERSION
    assert "private" not in text.lower()
    assert trust_store_from_payload(payload) == keys


def test_trust_store_refuses_duplicate_key_ids():
    with pytest.raises(ValueError):
        trust_store_to_payload((_key("key-1"), _key("key-1")))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row.__setitem__("schema_version", "nope"),
        lambda row: row.__setitem__("keys", "not-a-list"),
        lambda row: row["keys"][0].__setitem__("trust_status", "PROBABLY_FINE"),
        lambda row: row["keys"][0].__setitem__("public_key", "!!!not-base64!!!"),
        lambda row: row["keys"][0].__setitem__("key_id", ""),
        lambda row: row["keys"][0].__setitem__("algorithm", ""),
    ],
)
def test_trust_store_refuses_malformed_entries(mutate):
    payload = trust_store_to_payload((_key(),))
    mutate(payload)

    with pytest.raises(EvidenceTrustStoreMalformed):
        trust_store_from_payload(payload)


def test_trust_store_refuses_a_duplicated_key_id_on_read():
    payload = trust_store_to_payload((_key("key-1"),))
    payload["keys"].append(dict(payload["keys"][0]))

    with pytest.raises(EvidenceTrustStoreMalformed):
        trust_store_from_payload(payload)


def test_only_active_keys_may_sign():
    assert _key(status=EvidenceKeyTrustStatus.ACTIVE).may_sign is True
    assert _key(status=EvidenceKeyTrustStatus.VERIFY_ONLY).may_sign is False
    assert _key(status=EvidenceKeyTrustStatus.REVOKED).may_sign is False


def test_verification_key_requires_public_bytes():
    with pytest.raises(ValueError):
        EvidenceVerificationKey(
            key_id="key-1", algorithm=ED25519_ALGORITHM, public_key=b"",
            trust_status=EvidenceKeyTrustStatus.ACTIVE,
        )


# -- result invariants ---------------------------------------------------


def _result(**changes):
    values = dict(
        authentication_id="sea-1",
        strategy_version_id="version-1",
        review_run_id="review-1",
        artifact_digest="a" * 64,
        key_id="key-1",
        algorithm=ED25519_ALGORITHM,
        signature_digest="b" * 64,
        verdict=Verdict.PASS,
        blockers=(),
        authenticator_version="evidence-authentication-v1",
        policy_version="evidence-trust-v1",
        verified_at=NOW,
    )
    values.update(changes)
    return EvidenceAuthenticationResult(**values)


def test_pass_requires_no_blockers():
    with pytest.raises(ValueError):
        _result(blockers=(Blocker.KEY_REVOKED,))


def test_fail_requires_a_blocker():
    with pytest.raises(ValueError):
        _result(verdict=Verdict.FAIL, blockers=())


def test_pass_requires_full_artifact_identity():
    for field in (
        "review_run_id", "artifact_digest", "key_id", "algorithm", "signature_digest",
    ):
        with pytest.raises(ValueError):
            _result(**{field: None})


def test_fail_may_omit_identity_it_never_established():
    result = _result(
        verdict=Verdict.FAIL,
        blockers=(Blocker.AUTHENTICATION_MISSING,),
        review_run_id=None,
        artifact_digest=None,
        key_id=None,
        algorithm=None,
        signature_digest=None,
    )

    assert result.verdict is Verdict.FAIL


def test_blockers_are_canonicalised_and_deduplicated():
    result = _result(
        verdict=Verdict.FAIL,
        blockers=(
            Blocker.UNKNOWN_KEY,
            Blocker.KEY_REVOKED,
            Blocker.UNKNOWN_KEY,
        ),
    )

    assert result.blockers == (Blocker.KEY_REVOKED, Blocker.UNKNOWN_KEY)


def test_verified_at_must_be_timezone_aware():
    with pytest.raises(ValueError):
        _result(verified_at=datetime(2026, 1, 1))


def test_authentication_identity_excludes_the_verification_clock():
    material = dict(
        strategy_version_id="version-1",
        review_run_id="review-1",
        artifact_digest="a" * 64,
        key_id="key-1",
        algorithm=ED25519_ALGORITHM,
        signature_digest="b" * 64,
        verdict=Verdict.PASS,
        blockers=(),
        policy_version="evidence-trust-v1",
        authenticator_version="evidence-authentication-v1",
    )

    assert stable_evidence_authentication_id(**material) == stable_evidence_authentication_id(
        **material
    )
    assert stable_evidence_authentication_id(**material) != stable_evidence_authentication_id(
        **{**material, "key_id": "key-2"}
    )
    assert stable_evidence_authentication_id(**material) != stable_evidence_authentication_id(
        **{**material, "verdict": Verdict.FAIL, "blockers": (Blocker.KEY_REVOKED,)}
    )
