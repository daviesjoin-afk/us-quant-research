"""Stage 6-B2: durable coverage policies (CAS) and coverage evaluations."""

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from us_quant.trading.adapters.sqlite.strategy_coverage_repository import (
    SQLiteStrategyCoverageRepository,
)
from us_quant.trading.domain.strategy_coverage import (
    StrategyCoverageBlocker as Blocker,
    StrategyCoverageEvaluation,
    StrategyCoverageItem,
    StrategyCoveragePolicy,
    StrategyCoverageVerdict as Verdict,
)
from us_quant.trading.ports.strategy_coverage_repository import (
    StrategyCoverageRepositoryConflict,
    StrategyCoverageRepositoryError,
    StrategyCoverageRepositoryNotFound,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _policy(**changes):
    values = dict(
        policy_id="coverage-policy-1", revision=1,
        policy_version="evidence-coverage-v1", required_symbols=("AAPL", "MSFT"),
        required_universe_hash="universe-1", required_code_hash="code-1",
        min_distinct_review_runs=1, min_distinct_data_hashes=1,
        maximum_evidence_age=timedelta(days=30), created_at=NOW,
    )
    values.update(changes)
    return StrategyCoveragePolicy(**values)


def _item(**changes):
    values = dict(
        symbol="AAPL", review_run_id="review-1", data_hash="data-1", key_id="key-1",
        authentication_id="sea-1", gate_evaluation_id="sge-1",
        signed_at=NOW, generated_at=NOW,
    )
    values.update(changes)
    return StrategyCoverageItem(**values)


def _evaluation(**changes):
    values = dict(
        evaluation_id="sce-1", strategy_version_id="version-1",
        strategy_semver="1.0.0", parameter_hash="parameter-1",
        universe_hash="universe-1", code_hash="code-1",
        policy_id="coverage-policy-1", policy_revision=1,
        policy_version="evidence-coverage-v1", verdict=Verdict.PASS, blockers=(),
        items=(_item(),), covered_symbols=("AAPL",),
        required_symbols=("AAPL", "MSFT"), distinct_review_runs=1,
        distinct_data_hashes=1, evaluator_version="strategy-coverage-v1",
        evaluated_at=NOW,
    )
    values.update(changes)
    return StrategyCoverageEvaluation(**values)


def _failed(**changes):
    values = dict(
        evaluation_id="sce-fail", policy_id=None, policy_revision=None,
        policy_version=None, verdict=Verdict.FAIL,
        blockers=(Blocker.POLICY_MISSING,), items=(), covered_symbols=(),
        required_symbols=(), distinct_review_runs=0, distinct_data_hashes=0,
    )
    return _evaluation(**{**values, **changes})


def _repository(tmp_path):
    return SQLiteStrategyCoverageRepository(tmp_path / "coverage.sqlite3")


def _raw_update(tmp_path, sql, parameters):
    connection = sqlite3.connect(tmp_path / "coverage.sqlite3")
    try:
        with connection:
            connection.execute(sql, parameters)
    finally:
        connection.close()


# -- policy versioning ---------------------------------------------------


def test_first_revision_requires_an_empty_store(tmp_path):
    repository = _repository(tmp_path)
    repository.append_policy_revision(_policy(), expected_current_revision=None)

    with pytest.raises(StrategyCoverageRepositoryConflict):
        repository.append_policy_revision(_policy(), expected_current_revision=None)


def test_revision_bump_roundtrip_and_restart(tmp_path):
    repository = _repository(tmp_path)
    repository.append_policy_revision(_policy(), expected_current_revision=None)
    second = _policy(revision=2, required_symbols=("AAPL", "MSFT", "NVDA"))
    repository.append_policy_revision(second, expected_current_revision=1)

    assert repository.policy_revisions("coverage-policy-1") == (1, 2)
    assert repository.active_policy("coverage-policy-1") == second
    assert repository.get_policy("coverage-policy-1", 1) == _policy()

    reopened = SQLiteStrategyCoverageRepository(tmp_path / "coverage.sqlite3")
    assert reopened.active_policy("coverage-policy-1") == second


def test_cas_refuses_when_the_stored_history_has_a_hole(tmp_path):
    """A gap in the stored revisions must block the next append.

    The primary key alone is not enough: a writer that believes revision 2 is
    current would append revision 3 over a store that actually stops at 1,
    silently recording a lineage that never existed.  Only the compare-and-set
    notices, because revision 3 genuinely does not exist yet.
    """

    repository = _repository(tmp_path)
    repository.append_policy_revision(_policy(), expected_current_revision=None)
    repository.append_policy_revision(
        _policy(revision=2), expected_current_revision=1
    )
    _raw_update(
        tmp_path, "DELETE FROM strategy_coverage_policy WHERE revision = 2", ()
    )

    with pytest.raises(StrategyCoverageRepositoryConflict):
        repository.append_policy_revision(
            _policy(revision=3), expected_current_revision=2
        )

    assert repository.policy_revisions("coverage-policy-1") == (1,)


def test_revision_must_be_exactly_one_greater(tmp_path):
    repository = _repository(tmp_path)

    with pytest.raises(StrategyCoverageRepositoryConflict):
        repository.append_policy_revision(
            _policy(revision=5), expected_current_revision=None
        )

    assert repository.policy_revisions("coverage-policy-1") == ()


def test_absent_policy_reads_as_none(tmp_path):
    repository = _repository(tmp_path)

    assert repository.active_policy("nothing-here") is None
    assert repository.policy_revisions("nothing-here") == ()
    with pytest.raises(StrategyCoverageRepositoryNotFound):
        repository.get_policy("nothing-here", 1)


def test_corrupt_policy_payload_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.append_policy_revision(_policy(), expected_current_revision=None)
    _raw_update(
        tmp_path,
        "UPDATE strategy_coverage_policy SET payload_hash = ?",
        ("0" * 64,),
    )

    with pytest.raises(StrategyCoverageRepositoryError):
        repository.get_policy("coverage-policy-1", 1)


def test_corrupt_policy_indexed_columns_fail_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.append_policy_revision(_policy(), expected_current_revision=None)
    _raw_update(
        tmp_path,
        "UPDATE strategy_coverage_policy SET policy_version = ?",
        ("tampered",),
    )

    with pytest.raises(StrategyCoverageRepositoryError):
        repository.get_policy("coverage-policy-1", 1)


def test_policy_json_tamper_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.append_policy_revision(_policy(), expected_current_revision=None)
    _raw_update(
        tmp_path,
        "UPDATE strategy_coverage_policy SET payload_json = ?",
        ('{"policy_id":"coverage-policy-1"}',),
    )

    with pytest.raises(StrategyCoverageRepositoryError):
        repository.get_policy("coverage-policy-1", 1)


# -- evaluations ---------------------------------------------------------


def test_evaluation_roundtrip_and_restart(tmp_path):
    repository = _repository(tmp_path)
    repository.record_evaluation(_evaluation())

    assert repository.get_evaluation("sce-1") == _evaluation()

    reopened = SQLiteStrategyCoverageRepository(tmp_path / "coverage.sqlite3")
    assert reopened.get_evaluation("sce-1") == _evaluation()
    assert reopened.latest_for_version("version-1") == _evaluation()


def test_semantic_retry_is_idempotent_and_keeps_first_timestamp(tmp_path):
    repository = _repository(tmp_path)
    repository.record_evaluation(_evaluation())
    repository.record_evaluation(_evaluation(evaluated_at=NOW + timedelta(hours=5)))

    evaluations = repository.evaluations_for_version("version-1")
    assert len(evaluations) == 1
    assert evaluations[0].evaluated_at == NOW


def test_same_evaluation_id_with_different_payload_conflicts(tmp_path):
    repository = _repository(tmp_path)
    repository.record_evaluation(_evaluation())

    with pytest.raises(StrategyCoverageRepositoryConflict):
        repository.record_evaluation(_evaluation(verdict=Verdict.FAIL, blockers=(Blocker.EVIDENCE_MISSING,)))


def test_failed_evaluation_is_persistable(tmp_path):
    repository = _repository(tmp_path)
    repository.record_evaluation(_failed())

    stored = repository.get_evaluation("sce-fail")
    assert stored.verdict is Verdict.FAIL
    assert stored.blockers == (Blocker.POLICY_MISSING,)
    assert stored.items == ()
    assert stored.policy_identity is None


def test_newest_first_ordering(tmp_path):
    repository = _repository(tmp_path)
    repository.record_evaluation(_evaluation())
    repository.record_evaluation(
        _failed(evaluation_id="sce-2", evaluated_at=NOW + timedelta(days=1))
    )

    assert [
        item.evaluation_id for item in repository.evaluations_for_version("version-1")
    ] == ["sce-2", "sce-1"]


def test_missing_evaluation_raises_not_found(tmp_path):
    repository = _repository(tmp_path)

    with pytest.raises(StrategyCoverageRepositoryNotFound):
        repository.get_evaluation("sce-absent")
    assert repository.latest_for_version("version-absent") is None


def test_corrupt_evaluation_payload_hash_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.record_evaluation(_evaluation())
    _raw_update(
        tmp_path,
        "UPDATE strategy_coverage_evaluation SET payload_hash = ?",
        ("0" * 64,),
    )

    with pytest.raises(StrategyCoverageRepositoryError):
        repository.get_evaluation("sce-1")


def test_corrupt_indexed_columns_fail_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.record_evaluation(_evaluation())
    _raw_update(
        tmp_path,
        "UPDATE strategy_coverage_evaluation SET covered_symbols_json = ?",
        ('["TSLA"]',),
    )

    with pytest.raises(StrategyCoverageRepositoryError):
        repository.get_evaluation("sce-1")


def test_corrupt_items_json_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.record_evaluation(_evaluation())
    _raw_update(
        tmp_path,
        "UPDATE strategy_coverage_evaluation SET evaluation_items_json = ?",
        ("[{}]",),
    )

    with pytest.raises(StrategyCoverageRepositoryError):
        repository.get_evaluation("sce-1")


def test_non_canonical_payload_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.record_evaluation(_evaluation())
    connection = sqlite3.connect(tmp_path / "coverage.sqlite3")
    try:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM strategy_coverage_evaluation "
                "WHERE evaluation_id = 'sce-1'"
            ).fetchone()[0]
        )
    finally:
        connection.close()
    payload["verdict"] = "FAIL"
    _raw_update(
        tmp_path,
        "UPDATE strategy_coverage_evaluation SET payload_json = ?",
        (json.dumps(payload, indent=2),),
    )

    with pytest.raises(StrategyCoverageRepositoryError):
        repository.get_evaluation("sce-1")


def test_unknown_verdict_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.record_evaluation(_evaluation())
    _raw_update(
        tmp_path,
        "UPDATE strategy_coverage_evaluation SET verdict = ?",
        ("MAYBE",),
    )

    with pytest.raises(StrategyCoverageRepositoryError):
        repository.get_evaluation("sce-1")


def test_repository_never_touches_the_frozen_strategy_tables(tmp_path):
    repository = _repository(tmp_path)
    repository.append_policy_revision(_policy(), expected_current_revision=None)
    repository.record_evaluation(_evaluation())

    connection = sqlite3.connect(tmp_path / "coverage.sqlite3")
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()

    assert {
        "strategy_coverage_policy", "strategy_coverage_evaluation",
    } <= tables
    for frozen in ("strategy_version", "strategy_deployment", "strategy_gate_evaluation"):
        assert frozen not in tables
