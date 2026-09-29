"""SQLite persistence for versioned coverage policies and coverage evaluations.

Two independent tables.  The frozen ``strategy_version`` / ``strategy_deployment``
schema is never altered, and neither table becomes lifecycle authority.

Stored payloads are hashed and cross-checked against their indexed columns on
every read, so a corrupted row fails closed instead of being served.  Policies
are appended by compare-and-set only: a stale writer loses rather than
rewriting the authorisation a past claim was made under, and the audit trail of
which revision authorised what is never lost.
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
from us_quant.trading.domain.strategy_coverage import (
    CoveragePolicyMalformed,
    StrategyCoverageBlocker,
    StrategyCoverageEvaluation,
    StrategyCoverageItem,
    StrategyCoveragePolicy,
    StrategyCoverageVerdict,
    policy_from_payload,
    policy_to_payload,
)
from us_quant.trading.ports.strategy_coverage_repository import (
    StrategyCoverageRepositoryConflict,
    StrategyCoverageRepositoryError,
    StrategyCoverageRepositoryNotFound,
)


_CREATE_POLICY_TABLE = """
CREATE TABLE strategy_coverage_policy (
    policy_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    PRIMARY KEY (policy_id, revision)
)
"""

_CREATE_EVALUATION_TABLE = """
CREATE TABLE strategy_coverage_evaluation (
    evaluation_id TEXT PRIMARY KEY,
    strategy_version_id TEXT NOT NULL,
    strategy_semver TEXT NOT NULL,
    parameter_hash TEXT NOT NULL,
    universe_hash TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    policy_id TEXT,
    policy_revision INTEGER,
    policy_version TEXT,
    verdict TEXT NOT NULL,
    blockers_json TEXT NOT NULL,
    covered_symbols_json TEXT NOT NULL,
    required_symbols_json TEXT NOT NULL,
    distinct_review_runs INTEGER NOT NULL,
    distinct_data_hashes INTEGER NOT NULL,
    evaluation_items_json TEXT NOT NULL,
    evaluator_version TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL
)
"""

_CREATE_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_strategy_coverage_version_time
    ON strategy_coverage_evaluation(strategy_version_id, evaluated_at, evaluation_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_strategy_coverage_policy_revision
    ON strategy_coverage_policy(policy_id, revision);
    """,
)

_POLICY_COLUMNS = (
    "policy_id, revision, policy_version, created_at, payload_json, payload_hash"
)
_POLICY_KEYS = frozenset(
    {
        "policy_id", "revision", "policy_version", "required_symbols",
        "required_universe_hash", "required_code_hash",
        "min_distinct_review_runs", "min_distinct_data_hashes",
        "maximum_evidence_age_seconds", "created_at",
    }
)

_EVALUATION_COLUMNS = (
    "evaluation_id, strategy_version_id, strategy_semver, parameter_hash, "
    "universe_hash, code_hash, policy_id, policy_revision, policy_version, "
    "verdict, blockers_json, covered_symbols_json, required_symbols_json, "
    "distinct_review_runs, distinct_data_hashes, evaluation_items_json, "
    "evaluator_version, evaluated_at, payload_json, payload_hash"
)
_EVALUATION_KEYS = frozenset(
    {
        "evaluation_id", "strategy_version_id", "strategy_semver",
        "parameter_hash", "universe_hash", "code_hash", "policy_id",
        "policy_revision", "policy_version", "verdict", "blockers", "items",
        "covered_symbols", "required_symbols", "distinct_review_runs",
        "distinct_data_hashes", "evaluator_version", "evaluated_at",
    }
)
_EVALUATION_PLACEHOLDERS = ", ".join("?" for _ in _EVALUATION_COLUMNS.split(","))
_ITEM_KEYS = frozenset(
    {
        "symbol", "review_run_id", "data_hash", "key_id",
        "authentication_id", "gate_evaluation_id", "signed_at", "generated_at",
    }
)


class SQLiteStrategyCoverageRepository:
    """Persist coverage policies and evaluations without owning lifecycle."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    # -- policies --------------------------------------------------------

    def append_policy_revision(
        self,
        policy: StrategyCoveragePolicy,
        *,
        expected_current_revision: int | None,
    ) -> None:
        if not isinstance(policy, StrategyCoveragePolicy):
            raise TypeError("policy must be StrategyCoveragePolicy")
        if expected_current_revision is not None and (
            type(expected_current_revision) is not int
            or expected_current_revision < 1
        ):
            raise ValueError(
                "expected_current_revision must be a positive integer or None"
            )
        expected_next = (
            1
            if expected_current_revision is None
            else expected_current_revision + 1
        )
        if policy.revision != expected_next:
            raise StrategyCoverageRepositoryConflict(
                "policy revision must be exactly one greater than the expected current"
            )
        payload_json = _canonical_json(policy_to_payload(policy))
        payload_hash = sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            with closing(self._connect()) as connection:
                with connection:
                    row = connection.execute(
                        "SELECT MAX(revision) FROM strategy_coverage_policy "
                        "WHERE policy_id = ?",
                        (policy.policy_id,),
                    ).fetchone()
                    current = row[0] if row is not None else None
                    if current != expected_current_revision:
                        raise StrategyCoverageRepositoryConflict(
                            "policy revision compare-and-set failed: expected "
                            f"{expected_current_revision}, found {current}"
                        )
                    connection.execute(
                        f"INSERT INTO strategy_coverage_policy ({_POLICY_COLUMNS}) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            policy.policy_id,
                            policy.revision,
                            policy.policy_version,
                            policy.created_at.isoformat(),
                            payload_json,
                            payload_hash,
                        ),
                    )
        except StrategyCoverageRepositoryError:
            raise
        except sqlite3.IntegrityError as error:
            raise StrategyCoverageRepositoryConflict(
                f"cannot append coverage policy: {error}"
            ) from error
        except sqlite3.Error as error:
            raise StrategyCoverageRepositoryError(
                f"cannot append coverage policy: {error}"
            ) from error

    def get_policy(self, policy_id: str, revision: int) -> StrategyCoveragePolicy:
        _require_text(policy_id, "policy_id")
        if type(revision) is not int or revision < 1:
            raise ValueError("revision must be a positive integer")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    f"SELECT {_POLICY_COLUMNS} FROM strategy_coverage_policy "
                    "WHERE policy_id = ? AND revision = ?",
                    (policy_id, revision),
                ).fetchone()
        except sqlite3.Error as error:
            raise StrategyCoverageRepositoryError(
                f"cannot read coverage policy: {error}"
            ) from error
        if row is None:
            raise StrategyCoverageRepositoryNotFound(f"{policy_id}@{revision}")
        return _row_to_policy(row)

    def active_policy(self, policy_id: str) -> StrategyCoveragePolicy | None:
        _require_text(policy_id, "policy_id")
        revisions = self.policy_revisions(policy_id)
        if not revisions:
            return None
        return self.get_policy(policy_id, revisions[-1])

    def policy_revisions(self, policy_id: str) -> tuple[int, ...]:
        _require_text(policy_id, "policy_id")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT revision FROM strategy_coverage_policy "
                    "WHERE policy_id = ? ORDER BY revision",
                    (policy_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise StrategyCoverageRepositoryError(
                f"cannot list coverage policy revisions: {error}"
            ) from error
        return tuple(int(row[0]) for row in rows)

    # -- evaluations -----------------------------------------------------

    def record_evaluation(self, evaluation: StrategyCoverageEvaluation) -> None:
        if not isinstance(evaluation, StrategyCoverageEvaluation):
            raise TypeError("evaluation must be StrategyCoverageEvaluation")
        payload = _evaluation_payload(evaluation)
        payload_json = _canonical_json(payload)
        payload_hash = sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            with closing(self._connect()) as connection:
                with connection:
                    existing = connection.execute(
                        f"SELECT {_EVALUATION_COLUMNS} FROM strategy_coverage_evaluation "
                        "WHERE evaluation_id = ?",
                        (evaluation.evaluation_id,),
                    ).fetchone()
                    if existing is not None:
                        stored = _row_to_evaluation(existing)
                        stored_payload = _canonical_json(_evaluation_payload(stored))
                        if _semantic_payload(stored_payload) == _semantic_payload(payload_json):
                            return
                        raise StrategyCoverageRepositoryConflict(
                            "evaluation id already has a different immutable payload"
                        )
                    connection.execute(
                        f"INSERT INTO strategy_coverage_evaluation "
                        f"({_EVALUATION_COLUMNS}) VALUES ({_EVALUATION_PLACEHOLDERS})",
                        _evaluation_column_values(
                            evaluation, payload_json, payload_hash
                        ),
                    )
        except StrategyCoverageRepositoryError:
            raise
        except sqlite3.IntegrityError as error:
            raise StrategyCoverageRepositoryConflict(
                f"cannot record coverage evaluation: {error}"
            ) from error
        except sqlite3.Error as error:
            raise StrategyCoverageRepositoryError(
                f"cannot record coverage evaluation: {error}"
            ) from error

    def get_evaluation(self, evaluation_id: str) -> StrategyCoverageEvaluation:
        _require_text(evaluation_id, "evaluation_id")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    f"SELECT {_EVALUATION_COLUMNS} FROM strategy_coverage_evaluation "
                    "WHERE evaluation_id = ?",
                    (evaluation_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise StrategyCoverageRepositoryError(
                f"cannot read coverage evaluation: {error}"
            ) from error
        if row is None:
            raise StrategyCoverageRepositoryNotFound(evaluation_id)
        return _row_to_evaluation(row)

    def evaluations_for_version(
        self, version_id: str
    ) -> tuple[StrategyCoverageEvaluation, ...]:
        _require_text(version_id, "version_id")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    f"SELECT {_EVALUATION_COLUMNS} FROM strategy_coverage_evaluation "
                    "WHERE strategy_version_id = ?",
                    (version_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise StrategyCoverageRepositoryError(
                f"cannot list coverage evaluations: {error}"
            ) from error
        values = tuple(_row_to_evaluation(row) for row in rows)
        return tuple(sorted(values, key=_sort_key, reverse=True))

    def latest_for_version(
        self, version_id: str
    ) -> StrategyCoverageEvaluation | None:
        evaluations = self.evaluations_for_version(version_id)
        return evaluations[0] if evaluations else None

    # -- storage ---------------------------------------------------------

    def _initialize(self) -> None:
        connection = None
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                policy_exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'strategy_coverage_policy'"
                ).fetchone() is not None
                if not policy_exists:
                    connection.execute(_CREATE_POLICY_TABLE)
                evaluation_exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'strategy_coverage_evaluation'"
                ).fetchone() is not None
                if not evaluation_exists:
                    connection.execute(_CREATE_EVALUATION_TABLE)
                if policy_exists:
                    _require_columns(
                        connection, "strategy_coverage_policy",
                        {
                            "policy_id", "revision", "policy_version",
                            "created_at", "payload_json", "payload_hash",
                        },
                    )
                if evaluation_exists:
                    _require_columns(
                        connection, "strategy_coverage_evaluation",
                        {
                            "evaluation_id", "strategy_version_id",
                            "strategy_semver", "parameter_hash", "universe_hash",
                            "code_hash", "policy_id", "policy_revision",
                            "policy_version", "verdict", "blockers_json",
                            "covered_symbols_json", "required_symbols_json",
                            "distinct_review_runs", "distinct_data_hashes",
                            "evaluation_items_json", "evaluator_version",
                            "evaluated_at", "payload_json", "payload_hash",
                        },
                    )
                for statement in _CREATE_INDEXES:
                    connection.execute(statement)
                connection.commit()
        except StrategyCoverageRepositoryError:
            raise
        except sqlite3.Error as error:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            raise StrategyCoverageRepositoryError(
                f"cannot initialize coverage store: {error}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        try:
            return connect_sqlite(self.path)
        except sqlite3.Error as error:
            raise StrategyCoverageRepositoryError(
                f"cannot open coverage store: {error}"
            ) from error


def _require_columns(
    connection: sqlite3.Connection, table: str, required: set[str]
) -> None:
    columns = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    missing = required - columns
    if missing:
        raise StrategyCoverageRepositoryError(
            f"{table} is missing columns: " + ", ".join(sorted(missing))
        )


def _item_payload(item: StrategyCoverageItem) -> dict[str, Any]:
    return {
        "symbol": item.symbol,
        "review_run_id": item.review_run_id,
        "data_hash": item.data_hash,
        "key_id": item.key_id,
        "authentication_id": item.authentication_id,
        "gate_evaluation_id": item.gate_evaluation_id,
        "signed_at": item.signed_at.isoformat(),
        "generated_at": item.generated_at.isoformat(),
    }


def _evaluation_payload(evaluation: StrategyCoverageEvaluation) -> dict[str, Any]:
    return {
        "evaluation_id": evaluation.evaluation_id,
        "strategy_version_id": evaluation.strategy_version_id,
        "strategy_semver": evaluation.strategy_semver,
        "parameter_hash": evaluation.parameter_hash,
        "universe_hash": evaluation.universe_hash,
        "code_hash": evaluation.code_hash,
        "policy_id": evaluation.policy_id,
        "policy_revision": evaluation.policy_revision,
        "policy_version": evaluation.policy_version,
        "verdict": evaluation.verdict.value,
        "blockers": [item.value for item in evaluation.blockers],
        "items": [_item_payload(item) for item in evaluation.items],
        "covered_symbols": list(evaluation.covered_symbols),
        "required_symbols": list(evaluation.required_symbols),
        "distinct_review_runs": evaluation.distinct_review_runs,
        "distinct_data_hashes": evaluation.distinct_data_hashes,
        "evaluator_version": evaluation.evaluator_version,
        "evaluated_at": evaluation.evaluated_at.isoformat(),
    }


def _evaluation_column_values(
    evaluation: StrategyCoverageEvaluation,
    payload_json: str,
    payload_hash: str,
) -> tuple[object, ...]:
    return (
        evaluation.evaluation_id,
        evaluation.strategy_version_id,
        evaluation.strategy_semver,
        evaluation.parameter_hash,
        evaluation.universe_hash,
        evaluation.code_hash,
        evaluation.policy_id,
        evaluation.policy_revision,
        evaluation.policy_version,
        evaluation.verdict.value,
        _canonical_json([item.value for item in evaluation.blockers]),
        _canonical_json(list(evaluation.covered_symbols)),
        _canonical_json(list(evaluation.required_symbols)),
        evaluation.distinct_review_runs,
        evaluation.distinct_data_hashes,
        _canonical_json([_item_payload(item) for item in evaluation.items]),
        evaluation.evaluator_version,
        evaluation.evaluated_at.isoformat(),
        payload_json,
        payload_hash,
    )


def _row_to_policy(row: tuple[Any, ...]) -> StrategyCoveragePolicy:
    if len(row) != 6:
        raise StrategyCoverageRepositoryError(
            "coverage policy row has an unexpected column count"
        )
    policy_id, revision, policy_version, created_at, payload_json, payload_hash = row
    _require_text(policy_id, "policy_id")
    _require_text(policy_version, "policy_version")
    _require_text(created_at, "created_at")
    parsed = _verified_payload(payload_json, payload_hash, _POLICY_KEYS, "coverage policy")
    try:
        policy = policy_from_payload(parsed)
    except CoveragePolicyMalformed as error:
        raise StrategyCoverageRepositoryError(
            f"stored coverage policy is invalid: {error}"
        ) from error
    if (
        policy.policy_id != policy_id
        or policy.revision != revision
        or policy.policy_version != policy_version
        or policy.created_at.isoformat() != created_at
    ):
        raise StrategyCoverageRepositoryError(
            "indexed coverage policy columns disagree with payload"
        )
    return policy


def _row_to_evaluation(row: tuple[Any, ...]) -> StrategyCoverageEvaluation:
    if len(row) != 20:
        raise StrategyCoverageRepositoryError(
            "coverage evaluation row has an unexpected column count"
        )
    (
        evaluation_id, strategy_version_id, strategy_semver, parameter_hash,
        universe_hash, code_hash, policy_id, policy_revision, policy_version,
        verdict, blockers_json, covered_symbols_json, required_symbols_json,
        distinct_review_runs, distinct_data_hashes, evaluation_items_json,
        evaluator_version, evaluated_at, payload_json, payload_hash,
    ) = row
    _require_text(evaluation_id, "evaluation_id")
    _require_text(strategy_version_id, "strategy_version_id")
    _require_text(evaluator_version, "evaluator_version")
    parsed = _verified_payload(
        payload_json, payload_hash, _EVALUATION_KEYS, "coverage evaluation"
    )
    try:
        parsed_verdict = StrategyCoverageVerdict(parsed["verdict"])
        blockers = tuple(
            StrategyCoverageBlocker(item) for item in parsed["blockers"]
        )
        items = _parse_items(parsed["items"])
        covered_symbols = _parse_text_list(parsed["covered_symbols"], "covered_symbols")
        required_symbols = _parse_text_list(parsed["required_symbols"], "required_symbols")
        evaluated_at_value = datetime.fromisoformat(parsed["evaluated_at"])
        evaluation = StrategyCoverageEvaluation(
            evaluation_id=_payload_text(parsed, "evaluation_id"),
            strategy_version_id=_payload_text(parsed, "strategy_version_id"),
            strategy_semver=_payload_text(parsed, "strategy_semver"),
            parameter_hash=_payload_text(parsed, "parameter_hash"),
            universe_hash=_payload_text(parsed, "universe_hash"),
            code_hash=_payload_text(parsed, "code_hash"),
            policy_id=_payload_optional_text(parsed, "policy_id"),
            policy_revision=_payload_optional_int(parsed, "policy_revision"),
            policy_version=_payload_optional_text(parsed, "policy_version"),
            verdict=parsed_verdict,
            blockers=blockers,
            items=items,
            covered_symbols=covered_symbols,
            required_symbols=required_symbols,
            distinct_review_runs=_payload_count(parsed, "distinct_review_runs"),
            distinct_data_hashes=_payload_count(parsed, "distinct_data_hashes"),
            evaluator_version=_payload_text(parsed, "evaluator_version"),
            evaluated_at=evaluated_at_value,
        )
    except StrategyCoverageRepositoryError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise StrategyCoverageRepositoryError(
            f"stored coverage evaluation is invalid: {error}"
        ) from error

    indexed = (
        evaluation.evaluation_id, evaluation.strategy_version_id,
        evaluation.strategy_semver, evaluation.parameter_hash,
        evaluation.universe_hash, evaluation.code_hash, evaluation.policy_id,
        evaluation.policy_revision, evaluation.policy_version,
        evaluation.verdict.value,
        _canonical_json([item.value for item in evaluation.blockers]),
        _canonical_json(list(evaluation.covered_symbols)),
        _canonical_json(list(evaluation.required_symbols)),
        evaluation.distinct_review_runs, evaluation.distinct_data_hashes,
        _canonical_json([_item_payload(item) for item in evaluation.items]),
        evaluation.evaluator_version, evaluation.evaluated_at.isoformat(),
    )
    stored = (
        evaluation_id, strategy_version_id, strategy_semver, parameter_hash,
        universe_hash, code_hash, policy_id, policy_revision, policy_version,
        verdict, blockers_json, covered_symbols_json, required_symbols_json,
        distinct_review_runs, distinct_data_hashes, evaluation_items_json,
        evaluator_version, evaluated_at,
    )
    if indexed != stored:
        raise StrategyCoverageRepositoryError(
            "indexed coverage evaluation columns disagree with payload"
        )
    return evaluation


def _parse_items(value: object) -> tuple[StrategyCoverageItem, ...]:
    if not isinstance(value, list):
        raise StrategyCoverageRepositoryError("stored coverage items are not a list")
    items = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != _ITEM_KEYS:
            raise StrategyCoverageRepositoryError(
                "stored coverage item has an unexpected shape"
            )
        for key in (
            "symbol", "review_run_id", "data_hash", "key_id",
            "authentication_id", "gate_evaluation_id",
        ):
            if not isinstance(entry[key], str) or not entry[key].strip():
                raise StrategyCoverageRepositoryError(
                    f"stored coverage item {key} is missing or blank"
                )
        try:
            signed_at = datetime.fromisoformat(entry["signed_at"])
            generated_at = datetime.fromisoformat(entry["generated_at"])
        except (TypeError, ValueError) as error:
            raise StrategyCoverageRepositoryError(
                "stored coverage item timestamp is invalid"
            ) from error
        items.append(
            StrategyCoverageItem(
                symbol=entry["symbol"],
                review_run_id=entry["review_run_id"],
                data_hash=entry["data_hash"],
                key_id=entry["key_id"],
                authentication_id=entry["authentication_id"],
                gate_evaluation_id=entry["gate_evaluation_id"],
                signed_at=signed_at,
                generated_at=generated_at,
            )
        )
    return tuple(items)


def _parse_text_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise StrategyCoverageRepositoryError(f"stored {name} is not a string array")
    return tuple(value)


def _verified_payload(
    payload_json: object, payload_hash: object, keys: frozenset[str], label: str
) -> dict[str, Any]:
    _require_text(payload_json, "payload_json")
    _require_text(payload_hash, "payload_hash")
    try:
        parsed = json.loads(payload_json)
    except (TypeError, ValueError) as error:
        raise StrategyCoverageRepositoryError(
            f"stored {label} payload is malformed"
        ) from error
    if not isinstance(parsed, dict) or _canonical_json(parsed) != payload_json:
        raise StrategyCoverageRepositoryError(
            f"stored {label} payload is not canonical JSON"
        )
    if sha256(payload_json.encode("utf-8")).hexdigest() != payload_hash:
        raise StrategyCoverageRepositoryError(f"{label} payload hash mismatch")
    if set(parsed) != keys:
        raise StrategyCoverageRepositoryError(
            f"{label} payload has an unexpected shape"
        )
    return parsed


def _payload_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise StrategyCoverageRepositoryError(f"stored {key} is missing or blank")
    return value


def _payload_optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise StrategyCoverageRepositoryError(
            f"stored {key} is not valid optional text"
        )
    return value


def _payload_optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is not None and (type(value) is not int or value < 1):
        raise StrategyCoverageRepositoryError(
            f"stored {key} is not a valid positive integer"
        )
    return value


def _payload_count(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or value < 0:
        raise StrategyCoverageRepositoryError(
            f"stored {key} is not a nonnegative integer"
        )
    return value


def _sort_key(value: StrategyCoverageEvaluation) -> tuple[datetime, str]:
    return value.evaluated_at.astimezone(timezone.utc), value.evaluation_id


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _semantic_payload(payload_json: str) -> str:
    """Ignore the first-recorded timestamp when comparing an ID retry."""

    payload = json.loads(payload_json)
    payload.pop("evaluated_at", None)
    return _canonical_json(payload)


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise StrategyCoverageRepositoryError(f"{name} must be nonblank text")


__all__ = ["SQLiteStrategyCoverageRepository"]
