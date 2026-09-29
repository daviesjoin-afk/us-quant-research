"""SQLite persistence for immutable strategy gate evaluations.

The adapter uses an independent table and never alters the frozen strategy
version/deployment schema. Stored payloads are hashed and cross-checked against
their indexed columns on every read.
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
from us_quant.trading.domain.strategy_gate import (
    StrategyGateBlocker,
    StrategyGateEvaluation,
    StrategyGateVerdict,
)
from us_quant.trading.ports.strategy_gate_repository import (
    StrategyGateRepositoryConflict,
    StrategyGateRepositoryError,
    StrategyGateRepositoryNotFound,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_gate_evaluation (
    evaluation_id TEXT PRIMARY KEY,
    strategy_version_id TEXT NOT NULL,
    review_run_id TEXT NOT NULL,
    parameter_hash TEXT,
    data_hash TEXT,
    provider TEXT,
    symbol TEXT,
    verdict TEXT NOT NULL,
    blockers_json TEXT NOT NULL,
    review_decision TEXT,
    review_blocking_failures INTEGER,
    review_passed_gates INTEGER,
    review_gate_count INTEGER,
    evaluator_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strategy_gate_version_time
ON strategy_gate_evaluation(strategy_version_id, evaluated_at, evaluation_id);
"""

_COLUMNS = (
    "evaluation_id, strategy_version_id, review_run_id, parameter_hash, "
    "data_hash, provider, symbol, verdict, blockers_json, review_decision, "
    "review_blocking_failures, review_passed_gates, review_gate_count, "
    "evaluator_version, policy_version, evaluated_at, payload_json, payload_hash"
)
_PAYLOAD_KEYS = frozenset(
    {
        "evaluation_id", "strategy_version_id", "review_run_id", "parameter_hash",
        "data_hash", "provider", "symbol", "verdict", "blockers", "review_decision",
        "review_blocking_failures", "review_passed_gates", "review_gate_count",
        "evaluator_version", "policy_version", "evaluated_at",
    }
)


class SQLiteStrategyGateRepository:
    """Persist strategy-gate evaluations without owning lifecycle policy."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def record(self, evaluation: StrategyGateEvaluation) -> None:
        if not isinstance(evaluation, StrategyGateEvaluation):
            raise TypeError("evaluation must be StrategyGateEvaluation")
        if not evaluation.review_run_id:
            raise StrategyGateRepositoryError(
                "cannot persist an evaluation without a review run id"
            )
        payload = _evaluation_payload(evaluation)
        payload_json = _canonical_json(payload)
        payload_hash = sha256(payload_json.encode("utf-8")).hexdigest()
        row_values = _column_values(evaluation, payload_json, payload_hash)

        try:
            with closing(self._connect()) as connection:
                with connection:
                    existing = connection.execute(
                        f"SELECT {_COLUMNS} FROM strategy_gate_evaluation WHERE evaluation_id = ?",
                        (evaluation.evaluation_id,),
                    ).fetchone()
                    if existing is not None:
                        stored = _row_to_evaluation(existing)
                        stored_payload = _canonical_json(_evaluation_payload(stored))
                        if stored_payload == payload_json:
                            return
                        raise StrategyGateRepositoryConflict(
                            "evaluation id already has a different immutable payload"
                        )
                    connection.execute(
                        f"INSERT INTO strategy_gate_evaluation ({_COLUMNS}) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        row_values,
                    )
        except StrategyGateRepositoryError:
            raise
        except sqlite3.IntegrityError as error:
            raise StrategyGateRepositoryConflict(
                f"cannot record strategy gate evaluation: {error}"
            ) from error
        except sqlite3.Error as error:
            raise StrategyGateRepositoryError(
                f"cannot record strategy gate evaluation: {error}"
            ) from error

    def get(self, evaluation_id: str) -> StrategyGateEvaluation:
        _require_text(evaluation_id, "evaluation_id")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM strategy_gate_evaluation WHERE evaluation_id = ?",
                    (evaluation_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise StrategyGateRepositoryError(
                f"cannot read strategy gate evaluation: {error}"
            ) from error
        if row is None:
            raise StrategyGateRepositoryNotFound(evaluation_id)
        return _row_to_evaluation(row)

    def latest_for_version(self, version_id: str) -> StrategyGateEvaluation | None:
        evaluations = self.evaluations_for_version(version_id)
        return evaluations[0] if evaluations else None

    def evaluations_for_version(
        self, version_id: str
    ) -> tuple[StrategyGateEvaluation, ...]:
        _require_text(version_id, "version_id")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    f"SELECT {_COLUMNS} FROM strategy_gate_evaluation "
                    "WHERE strategy_version_id = ?",
                    (version_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise StrategyGateRepositoryError(
                f"cannot list strategy gate evaluations: {error}"
            ) from error
        values = tuple(_row_to_evaluation(row) for row in rows)
        return tuple(sorted(values, key=_sort_key, reverse=True))

    def _initialize(self) -> None:
        try:
            with closing(self._connect()) as connection:
                with connection:
                    connection.executescript(_SCHEMA)
        except sqlite3.Error as error:
            raise StrategyGateRepositoryError(
                f"cannot initialize strategy gate store: {error}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        try:
            return connect_sqlite(self.path)
        except sqlite3.Error as error:
            raise StrategyGateRepositoryError(
                f"cannot open strategy gate store: {error}"
            ) from error


def _evaluation_payload(evaluation: StrategyGateEvaluation) -> dict[str, Any]:
    return {
        "evaluation_id": evaluation.evaluation_id,
        "strategy_version_id": evaluation.strategy_version_id,
        "review_run_id": evaluation.review_run_id,
        "parameter_hash": evaluation.parameter_hash,
        "data_hash": evaluation.data_hash,
        "provider": evaluation.provider,
        "symbol": evaluation.symbol,
        "verdict": evaluation.verdict.value,
        "blockers": [item.value for item in evaluation.blockers],
        "review_decision": evaluation.review_decision,
        "review_blocking_failures": evaluation.review_blocking_failures,
        "review_passed_gates": evaluation.review_passed_gates,
        "review_gate_count": evaluation.review_gate_count,
        "evaluator_version": evaluation.evaluator_version,
        "policy_version": evaluation.policy_version,
        "evaluated_at": evaluation.evaluated_at.isoformat(),
    }


def _column_values(
    evaluation: StrategyGateEvaluation,
    payload_json: str,
    payload_hash: str,
) -> tuple[object, ...]:
    return (
        evaluation.evaluation_id,
        evaluation.strategy_version_id,
        evaluation.review_run_id,
        evaluation.parameter_hash,
        evaluation.data_hash,
        evaluation.provider,
        evaluation.symbol,
        evaluation.verdict.value,
        _canonical_json([item.value for item in evaluation.blockers]),
        evaluation.review_decision,
        evaluation.review_blocking_failures,
        evaluation.review_passed_gates,
        evaluation.review_gate_count,
        evaluation.evaluator_version,
        evaluation.policy_version,
        evaluation.evaluated_at.isoformat(),
        payload_json,
        payload_hash,
    )


def _row_to_evaluation(row: tuple[Any, ...]) -> StrategyGateEvaluation:
    if len(row) != 18:
        raise StrategyGateRepositoryError("strategy gate row has an unexpected column count")
    (
        evaluation_id, strategy_version_id, review_run_id, parameter_hash,
        data_hash, provider, symbol, verdict, blockers_json, review_decision,
        review_blocking_failures, review_passed_gates, review_gate_count,
        evaluator_version, policy_version, evaluated_at, payload_json, payload_hash,
    ) = row
    indexed_verdict = verdict
    indexed_evaluated_at = evaluated_at
    _require_text(evaluation_id, "evaluation_id")
    _require_text(strategy_version_id, "strategy_version_id")
    _require_text(review_run_id, "review_run_id")
    _require_text(evaluator_version, "evaluator_version")
    _require_text(policy_version, "policy_version")
    _require_text(payload_json, "payload_json")
    _require_text(payload_hash, "payload_hash")
    if not isinstance(blockers_json, str):
        raise StrategyGateRepositoryError("stored blockers_json is not text")
    try:
        indexed_blockers = json.loads(blockers_json)
    except (TypeError, ValueError) as error:
        raise StrategyGateRepositoryError("stored blockers_json is malformed") from error
    if not isinstance(indexed_blockers, list) or any(not isinstance(item, str) for item in indexed_blockers):
        raise StrategyGateRepositoryError("stored blockers_json is not a string array")
    try:
        parsed = json.loads(payload_json)
    except (TypeError, ValueError) as error:
        raise StrategyGateRepositoryError("stored strategy gate payload is malformed") from error
    if not isinstance(parsed, dict) or _canonical_json(parsed) != payload_json:
        raise StrategyGateRepositoryError("stored strategy gate payload is not canonical JSON")
    if sha256(payload_json.encode("utf-8")).hexdigest() != payload_hash:
        raise StrategyGateRepositoryError("strategy gate payload hash mismatch")

    if set(parsed) != _PAYLOAD_KEYS:
        raise StrategyGateRepositoryError("strategy gate payload has an unexpected shape")
    try:
        verdict_value = parsed["verdict"]
        if not isinstance(verdict_value, str):
            raise StrategyGateRepositoryError("stored verdict is not text")
        verdict = StrategyGateVerdict(verdict_value)
        blocker_values = parsed["blockers"]
        if not isinstance(blocker_values, list) or any(not isinstance(item, str) for item in blocker_values):
            raise StrategyGateRepositoryError("stored blockers are not a string array")
        blockers = tuple(StrategyGateBlocker(item) for item in blocker_values)
        timestamp_text = parsed["evaluated_at"]
        if not isinstance(timestamp_text, str):
            raise StrategyGateRepositoryError("stored evaluated_at is not text")
        evaluated_at = datetime.fromisoformat(timestamp_text)
        evaluation = StrategyGateEvaluation(
            evaluation_id=_payload_text(parsed, "evaluation_id"),
            strategy_version_id=_payload_text(parsed, "strategy_version_id"),
            review_run_id=_payload_text(parsed, "review_run_id"),
            parameter_hash=_payload_optional_text(parsed, "parameter_hash"),
            data_hash=_payload_optional_text(parsed, "data_hash"),
            provider=_payload_optional_text(parsed, "provider"),
            symbol=_payload_optional_text(parsed, "symbol"),
            verdict=verdict,
            blockers=blockers,
            review_decision=_payload_optional_text(parsed, "review_decision"),
            review_blocking_failures=_payload_optional_count(parsed, "review_blocking_failures"),
            review_passed_gates=_payload_optional_count(parsed, "review_passed_gates"),
            review_gate_count=_payload_optional_count(parsed, "review_gate_count"),
            evaluator_version=_payload_text(parsed, "evaluator_version"),
            policy_version=_payload_text(parsed, "policy_version"),
            evaluated_at=evaluated_at,
        )
    except StrategyGateRepositoryError:
        raise
    except (TypeError, ValueError) as error:
        raise StrategyGateRepositoryError(
            f"stored strategy gate payload is invalid: {error}"
        ) from error

    indexed = (
        evaluation.evaluation_id, evaluation.strategy_version_id, evaluation.review_run_id,
        evaluation.parameter_hash, evaluation.data_hash, evaluation.provider, evaluation.symbol,
        evaluation.verdict.value, list(item.value for item in evaluation.blockers),
        evaluation.review_decision, evaluation.review_blocking_failures,
        evaluation.review_passed_gates, evaluation.review_gate_count,
        evaluation.evaluator_version, evaluation.policy_version,
        evaluation.evaluated_at.isoformat(),
    )
    stored = (
        evaluation_id, strategy_version_id, review_run_id, parameter_hash, data_hash,
        provider, symbol, indexed_verdict, indexed_blockers, review_decision,
        review_blocking_failures, review_passed_gates, review_gate_count,
        evaluator_version, policy_version, indexed_evaluated_at,
    )
    if indexed != stored:
        raise StrategyGateRepositoryError("indexed strategy gate columns disagree with payload")
    return evaluation


def _payload_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise StrategyGateRepositoryError(f"stored {key} is missing or blank")
    return value


def _payload_optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise StrategyGateRepositoryError(f"stored {key} is not valid optional text")
    return value


def _payload_optional_count(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is not None and (type(value) is not int or value < 0):
        raise StrategyGateRepositoryError(f"stored {key} is not a valid count")
    return value


def _sort_key(value: StrategyGateEvaluation) -> tuple[datetime, str]:
    return value.evaluated_at.astimezone(timezone.utc), value.evaluation_id


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise StrategyGateRepositoryError(f"{name} must be nonblank text")


__all__ = ["SQLiteStrategyGateRepository"]
