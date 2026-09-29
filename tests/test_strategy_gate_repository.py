import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from us_quant.trading.adapters.sqlite.strategy_gate_repository import SQLiteStrategyGateRepository
from us_quant.trading.domain.strategy_gate import (
    StrategyGateBlocker, StrategyGateEvaluation, StrategyGateVerdict,
)
from us_quant.trading.ports.strategy_gate_repository import (
    StrategyGateRepositoryConflict, StrategyGateRepositoryError,
    StrategyGateRepositoryNotFound,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _evaluation(*, evaluation_id="eval-1", evaluated_at=NOW, blockers=(), review_run_id="review-1"):
    blockers = tuple(sorted(set(blockers), key=lambda item: item.value))
    return StrategyGateEvaluation(
        evaluation_id=evaluation_id, strategy_version_id="version-1",
        review_run_id=review_run_id, parameter_hash="parameter-1", data_hash="data-1",
        provider="provider-1", symbol="AAPL",
        verdict=StrategyGateVerdict.FAIL if blockers else StrategyGateVerdict.PASS,
        blockers=blockers, review_decision="ELIGIBLE_FOR_INDEPENDENT_REVIEW",
        review_blocking_failures=0, review_passed_gates=3, review_gate_count=3,
        evaluator_version="strategy-gate-v1", policy_version="independent-review-v1",
        evaluated_at=evaluated_at,
    )


def _mutate(path, sql, parameters=()):
    with sqlite3.connect(path) as connection:
        connection.execute(sql, parameters)


def test_roundtrip_restart_idempotency_conflict_and_latest_order(tmp_path):
    path = tmp_path / "strategy-gate.sqlite3"
    repository = SQLiteStrategyGateRepository(path)
    first = _evaluation()
    repository.record(first)
    repository.record(first)
    assert repository.get(first.evaluation_id) == first
    assert SQLiteStrategyGateRepository(path).get(first.evaluation_id) == first
    repository.record(_evaluation(evaluated_at=datetime(2026, 1, 2, tzinfo=timezone.utc)))
    assert len(repository.evaluations_for_version("version-1")) == 1
    with pytest.raises(StrategyGateRepositoryConflict):
        repository.record(_evaluation(blockers=(StrategyGateBlocker.STALE_EVIDENCE,)))
    older = _evaluation(evaluation_id="eval-0", evaluated_at=datetime(2025, 12, 31, tzinfo=timezone.utc), blockers=(StrategyGateBlocker.STALE_EVIDENCE,))
    tied = _evaluation(evaluation_id="eval-z")
    repository.record(older)
    repository.record(tied)
    assert repository.latest_for_version("version-1") == tied
    assert tuple(item.evaluation_id for item in repository.evaluations_for_version("version-1")) == ("eval-z", "eval-1", "eval-0")


def test_unknown_evaluation_is_typed_not_found(tmp_path):
    repository = SQLiteStrategyGateRepository(tmp_path / "gate.sqlite3")
    with pytest.raises(StrategyGateRepositoryNotFound):
        repository.get("absent")


@pytest.mark.parametrize(("column", "value"), [
    ("verdict", "UNKNOWN"),
    ("blockers_json", '["UNKNOWN_BLOCKER"]'),
    ("blockers_json", "{"),
    ("evaluated_at", "2026-01-01T00:00:00"),
    ("payload_hash", "0" * 64),
    ("review_run_id", ""),
    ("review_passed_gates", -1),
])
def test_corrupt_indexed_rows_fail_closed(tmp_path, column, value):
    path = tmp_path / "gate.sqlite3"
    repository = SQLiteStrategyGateRepository(path)
    repository.record(_evaluation())
    _mutate(path, f"UPDATE strategy_gate_evaluation SET {column} = ? WHERE evaluation_id = ?", (value, "eval-1"))
    with pytest.raises(StrategyGateRepositoryError):
        repository.get("eval-1")


def test_malformed_payload_json_fails_closed(tmp_path):
    path = tmp_path / "gate.sqlite3"
    repository = SQLiteStrategyGateRepository(path)
    repository.record(_evaluation())
    _mutate(path, "UPDATE strategy_gate_evaluation SET payload_json = ? WHERE evaluation_id = ?", ("{", "eval-1"))
    with pytest.raises(StrategyGateRepositoryError):
        repository.latest_for_version("version-1")


def test_naive_payload_timestamp_fails_closed_even_with_matching_hash(tmp_path):
    path = tmp_path / "gate.sqlite3"
    repository = SQLiteStrategyGateRepository(path)
    repository.record(_evaluation())
    payload = {
        "evaluation_id": "eval-1", "strategy_version_id": "version-1",
        "review_run_id": "review-1", "parameter_hash": "parameter-1",
        "data_hash": "data-1", "provider": "provider-1", "symbol": "AAPL",
        "verdict": "PASS", "blockers": [],
        "review_decision": "ELIGIBLE_FOR_INDEPENDENT_REVIEW",
        "review_blocking_failures": 0, "review_passed_gates": 3, "review_gate_count": 3,
        "evaluator_version": "strategy-gate-v1", "policy_version": "independent-review-v1",
        "evaluated_at": "2026-01-01T00:00:00",
    }
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload_hash = sha256(payload_json.encode()).hexdigest()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE strategy_gate_evaluation SET payload_json=?, payload_hash=?, evaluated_at=? WHERE evaluation_id=?", (payload_json, payload_hash, payload["evaluated_at"], "eval-1"))
    with pytest.raises(StrategyGateRepositoryError):
        repository.get("eval-1")


def test_pass_with_null_review_run_id_fails_closed_even_with_matching_hash(tmp_path):
    path = tmp_path / "gate.sqlite3"
    repository = SQLiteStrategyGateRepository(path)
    repository.record(_evaluation())
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT payload_json FROM strategy_gate_evaluation WHERE evaluation_id = ?",
            ("eval-1",),
        ).fetchone()
    payload = json.loads(row[0])
    payload["review_run_id"] = None
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload_hash = sha256(payload_json.encode()).hexdigest()
    _mutate(
        path,
        "UPDATE strategy_gate_evaluation SET review_run_id=NULL, payload_json=?, payload_hash=? WHERE evaluation_id=?",
        (payload_json, payload_hash, "eval-1"),
    )
    with pytest.raises(StrategyGateRepositoryError):
        repository.get("eval-1")


def test_existing_not_null_schema_migrates_and_preserves_rows(tmp_path):
    path = tmp_path / "gate.sqlite3"
    repository = SQLiteStrategyGateRepository(path)
    existing = _evaluation()
    repository.record(existing)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX idx_strategy_gate_version_time")
        connection.execute(
            "ALTER TABLE strategy_gate_evaluation RENAME TO stage6_old_gate"
        )
        connection.execute(
            "CREATE TABLE strategy_gate_evaluation ("
            "evaluation_id TEXT PRIMARY KEY, strategy_version_id TEXT NOT NULL, "
            "review_run_id TEXT NOT NULL, parameter_hash TEXT, data_hash TEXT, "
            "provider TEXT, symbol TEXT, verdict TEXT NOT NULL, blockers_json TEXT NOT NULL, "
            "review_decision TEXT, review_blocking_failures INTEGER, "
            "review_passed_gates INTEGER, review_gate_count INTEGER, "
            "evaluator_version TEXT NOT NULL, policy_version TEXT NOT NULL, "
            "evaluated_at TEXT NOT NULL, payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO strategy_gate_evaluation SELECT * FROM stage6_old_gate"
        )
        connection.execute("DROP TABLE stage6_old_gate")

    migrated = SQLiteStrategyGateRepository(path)
    assert migrated.get(existing.evaluation_id) == existing
    review_null = _evaluation(
        evaluation_id="missing-review", blockers=(StrategyGateBlocker.EVIDENCE_MISSING,),
        review_run_id=None,
    )
    migrated.record(review_null)
    assert migrated.get("missing-review") == review_null
    with sqlite3.connect(path) as connection:
        column = next(
            row for row in connection.execute("PRAGMA table_info(strategy_gate_evaluation)")
            if row[1] == "review_run_id"
        )
    assert column[3] == 0


@pytest.mark.parametrize(("verdict", "blockers"), [
    ("UNKNOWN", []),
    ("FAIL", ["UNKNOWN_BLOCKER"]),
])
def test_unknown_payload_enum_values_fail_closed_with_valid_hash(tmp_path, verdict, blockers):
    path = tmp_path / "gate.sqlite3"
    repository = SQLiteStrategyGateRepository(path)
    repository.record(_evaluation())
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT payload_json FROM strategy_gate_evaluation WHERE evaluation_id = ?",
            ("eval-1",),
        ).fetchone()
    payload = json.loads(row[0])
    payload["verdict"] = verdict
    payload["blockers"] = blockers
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload_hash = sha256(payload_json.encode()).hexdigest()
    blockers_json = json.dumps(blockers, separators=(",", ":"))
    _mutate(
        path,
        "UPDATE strategy_gate_evaluation SET verdict=?, blockers_json=?, payload_json=?, payload_hash=? WHERE evaluation_id=?",
        (verdict, blockers_json, payload_json, payload_hash, "eval-1"),
    )
    with pytest.raises(StrategyGateRepositoryError):
        repository.get("eval-1")
