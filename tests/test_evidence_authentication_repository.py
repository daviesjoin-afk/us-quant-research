"""Stage 6-B1: durable, fail-closed persistence of authentication records."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from us_quant.trading.adapters.sqlite.evidence_authentication_repository import (
    SQLiteEvidenceAuthenticationRepository,
)
from us_quant.trading.domain.evidence_auth import (
    EvidenceAuthenticationBlocker as Blocker,
    EvidenceAuthenticationResult,
    EvidenceAuthenticationVerdict as Verdict,
)
from us_quant.trading.ports.evidence_authentication_repository import (
    EvidenceAuthenticationRepositoryConflict,
    EvidenceAuthenticationRepositoryError,
    EvidenceAuthenticationRepositoryNotFound,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _pass_result(**changes):
    values = dict(
        authentication_id="sea-1",
        strategy_version_id="version-1",
        review_run_id="review-1",
        artifact_digest="digest-1",
        key_id="key-1",
        algorithm="ed25519",
        signature_digest="sig-1",
        verdict=Verdict.PASS,
        blockers=(),
        authenticator_version="evidence-authentication-v1",
        policy_version="evidence-trust-v1",
        verified_at=NOW,
    )
    values.update(changes)
    return EvidenceAuthenticationResult(**values)


def _fail_result(**changes):
    values = dict(
        authentication_id="sea-fail",
        strategy_version_id="version-1",
        review_run_id=None,
        artifact_digest=None,
        key_id=None,
        algorithm=None,
        signature_digest=None,
        verdict=Verdict.FAIL,
        blockers=(Blocker.AUTHENTICATION_MISSING,),
        authenticator_version="evidence-authentication-v1",
        policy_version="evidence-trust-v1",
        verified_at=NOW,
    )
    values.update(changes)
    return EvidenceAuthenticationResult(**values)


def _repository(tmp_path):
    return SQLiteEvidenceAuthenticationRepository(tmp_path / "evidence.sqlite3")


def _raw_update(tmp_path, sql, parameters):
    connection = sqlite3.connect(tmp_path / "evidence.sqlite3")
    try:
        with connection:
            connection.execute(sql, parameters)
    finally:
        connection.close()


def test_roundtrip_and_restart_persistence(tmp_path):
    repository = _repository(tmp_path)
    repository.record(_pass_result())

    assert repository.get("sea-1") == _pass_result()

    # A brand new instance over the same file must see the same immutable row.
    reopened = SQLiteEvidenceAuthenticationRepository(tmp_path / "evidence.sqlite3")
    assert reopened.get("sea-1") == _pass_result()
    assert reopened.latest_for_version("version-1") == _pass_result()


def test_semantic_retry_is_idempotent_and_keeps_first_timestamp(tmp_path):
    repository = _repository(tmp_path)
    repository.record(_pass_result())
    repository.record(_pass_result(verified_at=NOW + timedelta(hours=5)))

    records = repository.authentications_for_version("version-1")
    assert len(records) == 1
    assert records[0].verified_at == NOW


def test_same_id_with_different_payload_conflicts(tmp_path):
    repository = _repository(tmp_path)
    repository.record(_pass_result())

    with pytest.raises(EvidenceAuthenticationRepositoryConflict):
        repository.record(_pass_result(artifact_digest="digest-2"))


def test_pass_and_fail_never_share_an_identity(tmp_path):
    repository = _repository(tmp_path)
    repository.record(_pass_result(authentication_id="sea-shared"))

    with pytest.raises(EvidenceAuthenticationRepositoryConflict):
        repository.record(
            _fail_result(
                authentication_id="sea-shared",
                review_run_id="review-1",
                artifact_digest="digest-1",
                key_id="key-1",
                algorithm="ed25519",
                signature_digest="sig-1",
                blockers=(Blocker.KEY_REVOKED,),
            )
        )


def test_revocation_row_coexists_with_the_earlier_pass(tmp_path):
    """Rotation must not erase audit history: both rows survive."""

    repository = _repository(tmp_path)
    repository.record(_pass_result())
    repository.record(
        _fail_result(
            authentication_id="sea-revoked",
            review_run_id="review-1",
            artifact_digest="digest-1",
            key_id="key-1",
            algorithm="ed25519",
            signature_digest="sig-1",
            blockers=(Blocker.KEY_REVOKED,),
            verified_at=NOW + timedelta(days=1),
        )
    )

    records = repository.authentications_for_version("version-1")
    assert [record.authentication_id for record in records] == ["sea-revoked", "sea-1"]
    assert records[0].verdict is Verdict.FAIL
    assert records[1].verdict is Verdict.PASS


def test_lookup_by_review_run(tmp_path):
    repository = _repository(tmp_path)
    repository.record(_pass_result())
    repository.record(_pass_result(authentication_id="sea-2", review_run_id="review-2"))

    assert [
        record.authentication_id
        for record in repository.authentications_for_review("review-1")
    ] == ["sea-1"]
    assert repository.authentications_for_review("review-unknown") == ()


def test_missing_identity_raises_not_found(tmp_path):
    repository = _repository(tmp_path)

    with pytest.raises(EvidenceAuthenticationRepositoryNotFound):
        repository.get("sea-absent")
    assert repository.latest_for_version("version-absent") is None


def test_corrupt_payload_hash_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.record(_pass_result())
    _raw_update(
        tmp_path,
        "UPDATE strategy_evidence_authentication SET payload_hash = ?",
        ("0" * 64,),
    )

    with pytest.raises(EvidenceAuthenticationRepositoryError):
        repository.get("sea-1")


def test_corrupt_indexed_columns_fail_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.record(_pass_result())
    _raw_update(
        tmp_path,
        "UPDATE strategy_evidence_authentication SET key_id = ?",
        ("tampered-key",),
    )

    with pytest.raises(EvidenceAuthenticationRepositoryError):
        repository.get("sea-1")


def test_corrupt_blockers_json_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.record(_pass_result())
    _raw_update(
        tmp_path,
        "UPDATE strategy_evidence_authentication SET blockers_json = ?",
        ("{not-json",),
    )

    with pytest.raises(EvidenceAuthenticationRepositoryError):
        repository.get("sea-1")


def test_non_canonical_payload_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.record(_pass_result())
    payload = json.loads(
        sqlite3.connect(tmp_path / "evidence.sqlite3")
        .execute(
            "SELECT payload_json FROM strategy_evidence_authentication "
            "WHERE authentication_id = 'sea-1'"
        )
        .fetchone()[0]
    )
    payload["verdict"] = "FAIL"
    _raw_update(
        tmp_path,
        "UPDATE strategy_evidence_authentication SET payload_json = ?",
        (json.dumps(payload, indent=2),),
    )

    with pytest.raises(EvidenceAuthenticationRepositoryError):
        repository.get("sea-1")


def test_unknown_verdict_value_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.record(_pass_result())
    _raw_update(
        tmp_path,
        "UPDATE strategy_evidence_authentication SET verdict = ?",
        ("MAYBE",),
    )

    with pytest.raises(EvidenceAuthenticationRepositoryError):
        repository.get("sea-1")


def test_repository_never_touches_the_frozen_strategy_tables(tmp_path):
    repository = _repository(tmp_path)
    repository.record(_pass_result())

    connection = sqlite3.connect(tmp_path / "evidence.sqlite3")
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        connection.close()

    assert "strategy_evidence_authentication" in tables
    for frozen in ("strategy_version", "strategy_deployment"):
        assert frozen not in tables
