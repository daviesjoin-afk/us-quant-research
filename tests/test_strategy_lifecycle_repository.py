"""Stage 6-C: durable lifecycle policies, decisions and the crash-safe protocol."""

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from us_quant.trading.adapters.sqlite.strategy_lifecycle_repository import (
    SQLiteStrategyLifecycleRepository,
)
from us_quant.trading.domain.strategy import StrategyStatus
from us_quant.trading.domain.strategy_lifecycle import (
    StrategyLifecycleAction,
    StrategyLifecycleBlocker as Blocker,
    StrategyLifecycleDecision,
    StrategyLifecycleDecisionState,
    StrategyLifecyclePolicy,
)
from us_quant.trading.ports.strategy_lifecycle_repository import (
    StrategyLifecycleRepositoryConflict,
    StrategyLifecycleRepositoryError,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _policy(**changes):
    values = dict(
        policy_id="lifecycle-policy-1", revision=1,
        policy_version="strategy-lifecycle-v1",
        permitted_actions=(
            StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
            StrategyLifecycleAction.PAUSE,
        ),
        required_gate_policy_version="independent-review-v1",
        required_coverage_policy_version="evidence-coverage-v1",
        maximum_evidence_age=timedelta(days=30), created_at=NOW,
    )
    values.update(changes)
    return StrategyLifecyclePolicy(**values)


def _decision(**changes):
    values = dict(
        decision_id="sld-1", strategy_version_id="version-1", strategy_semver="1.0.0",
        parameter_hash="parameter-1", universe_hash="universe-1", code_hash="code-1",
        action=StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
        source_status=StrategyStatus.RESEARCH, target_status=StrategyStatus.PAPER_SHADOW,
        state=StrategyLifecycleDecisionState.PREPARED, blockers=(), triggers=(),
        policy_id="lifecycle-policy-1", policy_revision=1,
        policy_version="strategy-lifecycle-v1", authentication_id="sea-1",
        gate_evaluation_id="sge-1", coverage_evaluation_id="sce-1",
        coverage_policy_id="coverage-policy-1", coverage_policy_revision=1,
        controller_version="strategy-lifecycle-v1", authorized_at=NOW, applied_at=None,
    )
    values.update(changes)
    return StrategyLifecycleDecision(**values)


def _blocked(**changes):
    values = dict(
        decision_id="sld-blocked", policy_id=None, policy_revision=None,
        policy_version=None, authentication_id=None, gate_evaluation_id=None,
        coverage_evaluation_id=None, coverage_policy_id=None,
        coverage_policy_revision=None, state=StrategyLifecycleDecisionState.BLOCKED,
        blockers=(Blocker.POLICY_MISSING,),
    )
    return _decision(**{**values, **changes})


def _paused(**changes):
    values = dict(
        decision_id="sld-pause", action=StrategyLifecycleAction.PAUSE,
        source_status=StrategyStatus.PAPER_SHADOW, target_status=StrategyStatus.PAUSED,
        triggers=(Blocker.REVOKED_SIGNING_KEY,),
    )
    return _decision(**{**values, **changes})


def _repository(tmp_path):
    return SQLiteStrategyLifecycleRepository(tmp_path / "lifecycle.sqlite3")


def _raw_update(tmp_path, sql, parameters):
    connection = sqlite3.connect(tmp_path / "lifecycle.sqlite3")
    try:
        with connection:
            connection.execute(sql, parameters)
    finally:
        connection.close()


# -- policies ------------------------------------------------------------


def test_policy_first_revision_and_bump(tmp_path):
    repository = _repository(tmp_path)
    repository.append_policy_revision(_policy(), expected_current_revision=None)
    repository.append_policy_revision(
        _policy(revision=2), expected_current_revision=1
    )

    assert repository.policy_revisions("lifecycle-policy-1") == (1, 2)
    assert repository.active_policy("lifecycle-policy-1") == _policy(revision=2)
    assert repository.get_policy("lifecycle-policy-1", 1) == _policy()


def test_policy_cas_refuses_a_stale_writer(tmp_path):
    repository = _repository(tmp_path)
    repository.append_policy_revision(_policy(), expected_current_revision=None)
    repository.append_policy_revision(
        _policy(revision=2), expected_current_revision=1
    )

    with pytest.raises(StrategyLifecycleRepositoryError):
        repository.append_policy_revision(
            _policy(revision=2, permitted_actions=(StrategyLifecycleAction.PAUSE,)),
            expected_current_revision=1,
        )

    assert repository.policy_revisions("lifecycle-policy-1") == (1, 2)


def test_cas_refuses_when_the_stored_history_has_a_hole(tmp_path):
    """Only the compare-and-set notices a gap; the primary key cannot.

    A writer that believes revision 2 is current would append revision 3 over a
    store that actually stops at 1.  Revision 3 genuinely does not exist, so
    nothing but the CAS refuses it -- without the CAS the store would silently
    record a lineage that never happened.
    """

    repository = _repository(tmp_path)
    repository.append_policy_revision(_policy(), expected_current_revision=None)
    repository.append_policy_revision(
        _policy(revision=2), expected_current_revision=1
    )
    _raw_update(
        tmp_path, "DELETE FROM strategy_lifecycle_policy WHERE revision = 2", ()
    )

    with pytest.raises(StrategyLifecycleRepositoryConflict):
        repository.append_policy_revision(
            _policy(revision=3), expected_current_revision=2
        )

    assert repository.policy_revisions("lifecycle-policy-1") == (1,)


def test_corrupt_policy_payload_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.append_policy_revision(_policy(), expected_current_revision=None)
    _raw_update(
        tmp_path,
        "UPDATE strategy_lifecycle_policy SET payload_hash = ?",
        ("0" * 64,),
    )

    with pytest.raises(StrategyLifecycleRepositoryError):
        repository.get_policy("lifecycle-policy-1", 1)


# -- decisions -----------------------------------------------------------


def test_decision_roundtrip_and_restart(tmp_path):
    repository = _repository(tmp_path)
    repository.record_decision(_decision())

    assert repository.get_decision("sld-1") == _decision()

    reopened = SQLiteStrategyLifecycleRepository(tmp_path / "lifecycle.sqlite3")
    assert reopened.get_decision("sld-1") == _decision()


def test_record_is_idempotent_across_clocks(tmp_path):
    repository = _repository(tmp_path)
    repository.record_decision(_decision())
    repository.record_decision(_decision(authorized_at=NOW + timedelta(hours=4)))

    rows = repository.decisions_for_version("version-1")
    assert len(rows) == 1
    assert rows[0].authorized_at == NOW


def test_applied_and_superseded_round_trip(tmp_path):
    repository = _repository(tmp_path)
    repository.record_decision(_decision())

    applied = repository.mark_applied("sld-1", applied_at=NOW + timedelta(minutes=1))
    assert applied.state is StrategyLifecycleDecisionState.APPLIED
    assert applied.applied_at == NOW + timedelta(minutes=1)
    assert repository.get_decision("sld-1").state is StrategyLifecycleDecisionState.APPLIED


def test_mark_applied_is_idempotent(tmp_path):
    repository = _repository(tmp_path)
    repository.record_decision(_decision())

    first = repository.mark_applied("sld-1", applied_at=NOW)
    second = repository.mark_applied("sld-1", applied_at=NOW + timedelta(days=1))

    assert first == second


def test_supersede_carries_no_applied_timestamp(tmp_path):
    repository = _repository(tmp_path)
    repository.record_decision(_decision())

    superseded = repository.mark_superseded("sld-1")

    assert superseded.state is StrategyLifecycleDecisionState.SUPERSEDED
    assert superseded.applied_at is None


def test_a_blocked_decision_cannot_be_applied(tmp_path):
    repository = _repository(tmp_path)
    repository.record_decision(_blocked())

    with pytest.raises(StrategyLifecycleRepositoryError):
        repository.mark_applied("sld-blocked", applied_at=NOW)


def test_prepared_decisions_are_ordered_oldest_first(tmp_path):
    repository = _repository(tmp_path)
    repository.record_decision(_decision())
    repository.record_decision(
        _decision(decision_id="sld-2", authorized_at=NOW + timedelta(hours=1))
    )
    repository.record_decision(_blocked())

    prepared = repository.prepared_decisions("version-1")

    assert [item.decision_id for item in prepared] == ["sld-1", "sld-2"]


def test_an_authorised_pause_keeps_its_trigger(tmp_path):
    repository = _repository(tmp_path)
    repository.record_decision(_paused())

    stored = repository.get_decision("sld-pause")

    assert stored.triggers == (Blocker.REVOKED_SIGNING_KEY,)
    assert stored.blockers == ()


def test_corrupt_decision_payload_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.record_decision(_decision())
    _raw_update(
        tmp_path,
        "UPDATE strategy_lifecycle_decision SET payload_hash = ?",
        ("0" * 64,),
    )

    with pytest.raises(StrategyLifecycleRepositoryError):
        repository.get_decision("sld-1")


def test_corrupt_indexed_columns_fail_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.record_decision(_decision())
    _raw_update(
        tmp_path,
        "UPDATE strategy_lifecycle_decision SET state = ?",
        ("APPLIED",),
    )

    with pytest.raises(StrategyLifecycleRepositoryError):
        repository.get_decision("sld-1")


def test_unknown_state_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    repository.record_decision(_decision())
    _raw_update(
        tmp_path,
        "UPDATE strategy_lifecycle_decision SET state = ?",
        ("PROBABLY_FINE",),
    )

    with pytest.raises(StrategyLifecycleRepositoryError):
        repository.get_decision("sld-1")


def test_the_store_never_touches_the_frozen_strategy_tables(tmp_path):
    repository = _repository(tmp_path)
    repository.append_policy_revision(_policy(), expected_current_revision=None)
    repository.record_decision(_decision())

    connection = sqlite3.connect(tmp_path / "lifecycle.sqlite3")
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
        "strategy_lifecycle_policy", "strategy_lifecycle_decision",
    } <= tables
    for frozen in (
        "strategy_version", "strategy_deployment", "strategy_gate_evaluation",
        "strategy_evidence_authentication", "strategy_coverage_evaluation",
    ):
        assert frozen not in tables


def test_a_decision_payload_survives_a_full_serialisation_round_trip(tmp_path):
    repository = _repository(tmp_path)
    repository.record_decision(_paused())

    payload = json.loads(
        sqlite3.connect(tmp_path / "lifecycle.sqlite3")
        .execute(
            "SELECT payload_json FROM strategy_lifecycle_decision "
            "WHERE decision_id = 'sld-pause'"
        )
        .fetchone()[0]
    )

    assert payload["triggers"] == ["REVOKED_SIGNING_KEY"]
    assert payload["blockers"] == []
    assert payload["state"] == "PREPARED"
