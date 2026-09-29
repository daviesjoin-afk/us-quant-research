"""SQLite persistence for research-evidence authentication records.

An independent table: the frozen ``strategy_version`` / ``strategy_deployment``
schema is never altered, and this store never becomes promotion authority.
Stored payloads are hashed and cross-checked against their indexed columns on
every read, so a corrupted row fails closed instead of being served.

Rotation preserves history.  Because the authentication identity covers the
verdict and blockers but not the verification clock, re-verifying identical
evidence is idempotent, while a later revocation writes a *new* FAIL row and
leaves the earlier PASS row intact for audit.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any

from us_quant.sqlite_support import connect_sqlite
from us_quant.trading.domain.evidence_auth import (
    EvidenceAuthenticationBlocker,
    EvidenceAuthenticationResult,
    EvidenceAuthenticationVerdict,
)
from us_quant.trading.ports.evidence_authentication_repository import (
    EvidenceAuthenticationRepositoryConflict,
    EvidenceAuthenticationRepositoryError,
    EvidenceAuthenticationRepositoryNotFound,
)


_CREATE_TABLE = """
CREATE TABLE strategy_evidence_authentication (
    authentication_id TEXT PRIMARY KEY,
    review_run_id TEXT,
    strategy_version_id TEXT NOT NULL,
    artifact_digest TEXT,
    key_id TEXT,
    algorithm TEXT,
    signature_digest TEXT,
    verdict TEXT NOT NULL,
    blockers_json TEXT NOT NULL,
    authenticator_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL
)
"""
_CREATE_INDEX_VERSION = """
CREATE INDEX IF NOT EXISTS idx_evidence_auth_version_time
ON strategy_evidence_authentication(strategy_version_id, verified_at, authentication_id);
"""
_CREATE_INDEX_REVIEW = """
CREATE INDEX IF NOT EXISTS idx_evidence_auth_review_time
ON strategy_evidence_authentication(review_run_id, verified_at, authentication_id);
"""

_COLUMNS = (
    "authentication_id, review_run_id, strategy_version_id, artifact_digest, "
    "key_id, algorithm, signature_digest, verdict, blockers_json, "
    "authenticator_version, policy_version, verified_at, payload_json, payload_hash"
)
_PAYLOAD_KEYS = frozenset(
    {
        "authentication_id", "review_run_id", "strategy_version_id",
        "artifact_digest", "key_id", "algorithm", "signature_digest", "verdict",
        "blockers", "authenticator_version", "policy_version", "verified_at",
    }
)
_PLACEHOLDERS = ", ".join("?" for _ in _COLUMNS.split(","))


class SQLiteEvidenceAuthenticationRepository:
    """Persist authentication records without owning lifecycle policy."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def record(self, result: EvidenceAuthenticationResult) -> None:
        if not isinstance(result, EvidenceAuthenticationResult):
            raise TypeError("result must be EvidenceAuthenticationResult")
        payload = _result_payload(result)
        payload_json = _canonical_json(payload)
        payload_hash = sha256(payload_json.encode("utf-8")).hexdigest()
        row_values = _column_values(result, payload_json, payload_hash)

        try:
            with closing(self._connect()) as connection:
                with connection:
                    existing = connection.execute(
                        f"SELECT {_COLUMNS} FROM strategy_evidence_authentication "
                        "WHERE authentication_id = ?",
                        (result.authentication_id,),
                    ).fetchone()
                    if existing is not None:
                        stored = _row_to_result(existing)
                        stored_payload = _canonical_json(_result_payload(stored))
                        if _semantic_payload(stored_payload) == _semantic_payload(payload_json):
                            return
                        raise EvidenceAuthenticationRepositoryConflict(
                            "authentication id already has a different immutable payload"
                        )
                    connection.execute(
                        f"INSERT INTO strategy_evidence_authentication ({_COLUMNS}) "
                        f"VALUES ({_PLACEHOLDERS})",
                        row_values,
                    )
        except EvidenceAuthenticationRepositoryError:
            raise
        except sqlite3.IntegrityError as error:
            raise EvidenceAuthenticationRepositoryConflict(
                f"cannot record evidence authentication: {error}"
            ) from error
        except sqlite3.Error as error:
            raise EvidenceAuthenticationRepositoryError(
                f"cannot record evidence authentication: {error}"
            ) from error

    def get(self, authentication_id: str) -> EvidenceAuthenticationResult:
        _require_text(authentication_id, "authentication_id")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM strategy_evidence_authentication "
                    "WHERE authentication_id = ?",
                    (authentication_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise EvidenceAuthenticationRepositoryError(
                f"cannot read evidence authentication: {error}"
            ) from error
        if row is None:
            raise EvidenceAuthenticationRepositoryNotFound(authentication_id)
        return _row_to_result(row)

    def latest_for_version(
        self, version_id: str
    ) -> EvidenceAuthenticationResult | None:
        results = self.authentications_for_version(version_id)
        return results[0] if results else None

    def authentications_for_version(
        self, version_id: str
    ) -> tuple[EvidenceAuthenticationResult, ...]:
        _require_text(version_id, "version_id")
        return self._query(
            "WHERE strategy_version_id = ?", (version_id,), "list authentications"
        )

    def authentications_for_review(
        self, review_run_id: str
    ) -> tuple[EvidenceAuthenticationResult, ...]:
        _require_text(review_run_id, "review_run_id")
        return self._query(
            "WHERE review_run_id = ?", (review_run_id,), "list authentications"
        )

    def _query(
        self, clause: str, parameters: tuple[object, ...], action: str
    ) -> tuple[EvidenceAuthenticationResult, ...]:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    f"SELECT {_COLUMNS} FROM strategy_evidence_authentication {clause}",
                    parameters,
                ).fetchall()
        except sqlite3.Error as error:
            raise EvidenceAuthenticationRepositoryError(
                f"cannot {action}: {error}"
            ) from error
        values = tuple(_row_to_result(row) for row in rows)
        return tuple(sorted(values, key=_sort_key, reverse=True))

    def _initialize(self) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'strategy_evidence_authentication'"
                ).fetchone() is not None
                if not exists:
                    connection.execute(_CREATE_TABLE)
                else:
                    columns = connection.execute(
                        "PRAGMA table_info(strategy_evidence_authentication)"
                    ).fetchall()
                    names = {row[1] for row in columns}
                    required = {
                        "authentication_id", "review_run_id", "strategy_version_id",
                        "artifact_digest", "key_id", "algorithm", "signature_digest",
                        "verdict", "blockers_json", "authenticator_version",
                        "policy_version", "verified_at", "payload_json", "payload_hash",
                    }
                    missing = required - names
                    if missing:
                        raise EvidenceAuthenticationRepositoryError(
                            "evidence authentication table is missing columns: "
                            + ", ".join(sorted(missing))
                        )
                connection.execute(_CREATE_INDEX_VERSION)
                connection.execute(_CREATE_INDEX_REVIEW)
                connection.commit()
        except EvidenceAuthenticationRepositoryError:
            raise
        except sqlite3.Error as error:
            try:
                connection.rollback()
            except Exception:
                pass
            raise EvidenceAuthenticationRepositoryError(
                f"cannot initialize evidence authentication store: {error}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        try:
            return connect_sqlite(self.path)
        except sqlite3.Error as error:
            raise EvidenceAuthenticationRepositoryError(
                f"cannot open evidence authentication store: {error}"
            ) from error


def _result_payload(result: EvidenceAuthenticationResult) -> dict[str, Any]:
    return {
        "authentication_id": result.authentication_id,
        "review_run_id": result.review_run_id,
        "strategy_version_id": result.strategy_version_id,
        "artifact_digest": result.artifact_digest,
        "key_id": result.key_id,
        "algorithm": result.algorithm,
        "signature_digest": result.signature_digest,
        "verdict": result.verdict.value,
        "blockers": [item.value for item in result.blockers],
        "authenticator_version": result.authenticator_version,
        "policy_version": result.policy_version,
        "verified_at": result.verified_at.isoformat(),
    }


def _column_values(
    result: EvidenceAuthenticationResult,
    payload_json: str,
    payload_hash: str,
) -> tuple[object, ...]:
    return (
        result.authentication_id,
        result.review_run_id,
        result.strategy_version_id,
        result.artifact_digest,
        result.key_id,
        result.algorithm,
        result.signature_digest,
        result.verdict.value,
        _canonical_json([item.value for item in result.blockers]),
        result.authenticator_version,
        result.policy_version,
        result.verified_at.isoformat(),
        payload_json,
        payload_hash,
    )


def _row_to_result(row: tuple[Any, ...]) -> EvidenceAuthenticationResult:
    if len(row) != 14:
        raise EvidenceAuthenticationRepositoryError(
            "evidence authentication row has an unexpected column count"
        )
    (
        authentication_id, review_run_id, strategy_version_id, artifact_digest,
        key_id, algorithm, signature_digest, verdict, blockers_json,
        authenticator_version, policy_version, verified_at, payload_json,
        payload_hash,
    ) = row
    indexed_verdict = verdict
    indexed_verified_at = verified_at
    _require_text(authentication_id, "authentication_id")
    _require_text(strategy_version_id, "strategy_version_id")
    _require_text(authenticator_version, "authenticator_version")
    _require_text(policy_version, "policy_version")
    _require_text(payload_json, "payload_json")
    _require_text(payload_hash, "payload_hash")
    if not isinstance(blockers_json, str):
        raise EvidenceAuthenticationRepositoryError("stored blockers_json is not text")
    try:
        indexed_blockers = json.loads(blockers_json)
    except (TypeError, ValueError) as error:
        raise EvidenceAuthenticationRepositoryError(
            "stored blockers_json is malformed"
        ) from error
    if not isinstance(indexed_blockers, list) or any(
        not isinstance(item, str) for item in indexed_blockers
    ):
        raise EvidenceAuthenticationRepositoryError(
            "stored blockers_json is not a string array"
        )
    try:
        parsed = json.loads(payload_json)
    except (TypeError, ValueError) as error:
        raise EvidenceAuthenticationRepositoryError(
            "stored evidence authentication payload is malformed"
        ) from error
    if not isinstance(parsed, dict) or _canonical_json(parsed) != payload_json:
        raise EvidenceAuthenticationRepositoryError(
            "stored evidence authentication payload is not canonical JSON"
        )
    if sha256(payload_json.encode("utf-8")).hexdigest() != payload_hash:
        raise EvidenceAuthenticationRepositoryError(
            "evidence authentication payload hash mismatch"
        )
    if set(parsed) != _PAYLOAD_KEYS:
        raise EvidenceAuthenticationRepositoryError(
            "evidence authentication payload has an unexpected shape"
        )
    try:
        verdict_value = parsed["verdict"]
        if not isinstance(verdict_value, str):
            raise EvidenceAuthenticationRepositoryError("stored verdict is not text")
        parsed_verdict = EvidenceAuthenticationVerdict(verdict_value)
        blocker_values = parsed["blockers"]
        if not isinstance(blocker_values, list) or any(
            not isinstance(item, str) for item in blocker_values
        ):
            raise EvidenceAuthenticationRepositoryError(
                "stored blockers are not a string array"
            )
        blockers = tuple(EvidenceAuthenticationBlocker(item) for item in blocker_values)
        timestamp_text = parsed["verified_at"]
        if not isinstance(timestamp_text, str):
            raise EvidenceAuthenticationRepositoryError("stored verified_at is not text")
        verified_at_value = datetime.fromisoformat(timestamp_text)
        result = EvidenceAuthenticationResult(
            authentication_id=_payload_text(parsed, "authentication_id"),
            strategy_version_id=_payload_text(parsed, "strategy_version_id"),
            review_run_id=_payload_optional_text(parsed, "review_run_id"),
            artifact_digest=_payload_optional_text(parsed, "artifact_digest"),
            key_id=_payload_optional_text(parsed, "key_id"),
            algorithm=_payload_optional_text(parsed, "algorithm"),
            signature_digest=_payload_optional_text(parsed, "signature_digest"),
            verdict=parsed_verdict,
            blockers=blockers,
            authenticator_version=_payload_text(parsed, "authenticator_version"),
            policy_version=_payload_text(parsed, "policy_version"),
            verified_at=verified_at_value,
        )
    except EvidenceAuthenticationRepositoryError:
        raise
    except (TypeError, ValueError) as error:
        raise EvidenceAuthenticationRepositoryError(
            f"stored evidence authentication payload is invalid: {error}"
        ) from error

    indexed = (
        result.authentication_id, result.review_run_id, result.strategy_version_id,
        result.artifact_digest, result.key_id, result.algorithm,
        result.signature_digest, result.verdict.value,
        list(item.value for item in result.blockers), result.authenticator_version,
        result.policy_version, result.verified_at.isoformat(),
    )
    stored = (
        authentication_id, review_run_id, strategy_version_id, artifact_digest,
        key_id, algorithm, signature_digest, indexed_verdict, indexed_blockers,
        authenticator_version, policy_version, indexed_verified_at,
    )
    if indexed != stored:
        raise EvidenceAuthenticationRepositoryError(
            "indexed evidence authentication columns disagree with payload"
        )
    return result


def _payload_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EvidenceAuthenticationRepositoryError(f"stored {key} is missing or blank")
    return value


def _payload_optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise EvidenceAuthenticationRepositoryError(
            f"stored {key} is not valid optional text"
        )
    return value


def _sort_key(value: EvidenceAuthenticationResult) -> tuple[datetime, str]:
    return value.verified_at.astimezone(timezone.utc), value.authentication_id


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _semantic_payload(payload_json: str) -> str:
    """Ignore the first-recorded timestamp when comparing an ID retry."""

    payload = json.loads(payload_json)
    payload.pop("verified_at", None)
    return _canonical_json(payload)


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceAuthenticationRepositoryError(f"{name} must be nonblank text")


__all__ = ["SQLiteEvidenceAuthenticationRepository"]
