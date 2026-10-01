"""D2: durable performance facts through the sole lifecycle authority."""
from dataclasses import fields, replace
from datetime import datetime, timedelta
from hashlib import sha256
import json
import sqlite3

import pytest

from paper_performance_support import NOW as PERFORMANCE_NOW, history
from test_strategy_paper_performance_application import setup_app, evaluate
from test_strategy_lifecycle_evaluator import NOW, _Chain, _controller, _policy, _version
from test_strategy_lifecycle_service import _decision
from test_trading_strategy_repository import _audit
from us_quant.trading.adapters.sqlite.strategy_lifecycle_repository import SQLiteStrategyLifecycleRepository
from us_quant.trading.adapters.sqlite.strategy_paper_performance_repository import SQLiteStrategyPaperPerformanceRepository
from us_quant.trading.adapters.sqlite.strategy_repository import SQLiteStrategyRepository
from us_quant.trading.application.strategies import StrategyApplication
from us_quant.trading.application.strategy_lifecycle import StrategyLifecycleService
from us_quant.trading.domain.strategy import StrategyMode, StrategyStatus
from us_quant.trading.domain.strategy_lifecycle import StrategyLifecycleAction as Action, StrategyLifecycleBlocker as B, StrategyLifecycleDecisionState as State
from us_quant.trading.domain.strategy_paper_performance import StrategyPaperPerformanceEvaluation, StrategyPaperPerformanceVerdict as Verdict, stable_strategy_paper_performance_evaluation_id
from us_quant.trading.ports.strategy_lifecycle_repository import StrategyLifecycleRepositoryError


def _fact(tmp_path, kind=Verdict.FAIL, *, time_offset=timedelta(0), **changes):
    # Build an actual D1 evaluation from durable partial-fill truth; adapt only
    # its version/time identity to the existing sealed research-chain fixture.
    components, *_ = setup_app(tmp_path, data=history(sell_price='90' if kind is Verdict.FAIL else '110'))
    if kind is Verdict.INSUFFICIENT:
        components.repository.append_policy_revision(
            replace(components.repository.active_policy('paper-policy'), revision=2,
                    minimum_distinct_sessions=3), expected_current_revision=1
        )
    source = evaluate(components)
    assert source.verdict is kind
    offset = NOW - PERFORMANCE_NOW + time_offset
    metric_values = {f.name: getattr(source.metrics, f.name) for f in fields(source.metrics)}
    metric_values = {k: v + offset if isinstance(v, datetime) else v for k, v in metric_values.items()}
    metric_values['strategy_version_id'] = 'version-1'
    metrics = type(source.metrics)(**metric_values)
    values = {f.name: getattr(source, f.name) for f in fields(source) if f.name != 'evaluation_id'}
    values = {k: v + offset if isinstance(v, datetime) else v for k, v in values.items()}
    values.update(strategy_version_id='version-1', metrics=metrics)
    values.update(changes)
    values['metrics'] = replace(values['metrics'], strategy_version_id=values['strategy_version_id'])
    return StrategyPaperPerformanceEvaluation(evaluation_id=stable_strategy_paper_performance_evaluation_id(values), **values)


def _paper():
    return _version(status=StrategyStatus.PAPER_SHADOW, mode=StrategyMode.PAPER_SHADOW)


def _decide(chain, fact, **changes):
    values = dict(version=_paper(), action=Action.PAUSE, policy=_policy(), coverage=chain.coverage,
                  decided_at=NOW, paper_performance=fact)
    values.update(changes)
    return _controller(chain).decide(**values)


def _service(tmp_path, chain, version=None, *, with_performance=True):
    version = version or _paper()
    strategies_store = SQLiteStrategyRepository(tmp_path/'strategies.sqlite')
    strategies_store.insert_version(version, audit=_audit(version))
    strategies = StrategyApplication(strategies_store)
    decisions = SQLiteStrategyLifecycleRepository(tmp_path/'lifecycle.sqlite')
    facts = SQLiteStrategyPaperPerformanceRepository(tmp_path/'durable-performance.sqlite')
    service = StrategyLifecycleService(controller=_controller(chain), decisions=decisions, strategies=strategies,
        paper_performance_repository=facts if with_performance else None)
    return service, strategies, decisions, facts


def test_fail_is_a_named_pause_trigger_and_binds_decision_identity(tmp_path):
    chain = _Chain(tmp_path).build()
    fact = _fact(tmp_path/'facts')
    result = _decide(chain, fact)
    assert result.authorised
    assert result.decision.triggers == (B.PAPER_PERFORMANCE_FAILED,)
    assert result.decision.target_status is StrategyStatus.PAUSED
    assert result.decision.paper_performance_evaluation_id == fact.evaluation_id
    newer = _fact(tmp_path/'newer', source_digest='1'*64)
    assert _decide(chain, newer).decision.decision_id != result.decision.decision_id
    assert _decide(chain, fact, decided_at=NOW+timedelta(seconds=1)).decision.decision_id == result.decision.decision_id


@pytest.mark.parametrize('kind', [Verdict.PASS, Verdict.INSUFFICIENT, None])
def test_pass_insufficient_or_absent_fact_cannot_justify_pause(tmp_path, kind):
    chain = _Chain(tmp_path).build()
    fact = _fact(tmp_path/'facts', kind) if kind else None
    result = _decide(chain, fact)
    assert not result.authorised
    assert result.decision.blockers == (B.PAUSE_NOT_JUSTIFIED,)
    assert result.decision.triggers == ()


def test_insufficient_does_not_suppress_another_governance_failure(tmp_path):
    chain = _Chain(tmp_path).build()
    fact = _fact(tmp_path/'facts', Verdict.INSUFFICIENT)
    result = _decide(chain, fact, coverage=None)
    assert result.authorised
    assert result.decision.triggers == (B.COVERAGE_MISSING,)


@pytest.mark.parametrize('kind', [Verdict.FAIL, Verdict.INSUFFICIENT, None])
def test_initial_promotion_needs_no_performance_prerequisite(tmp_path, kind):
    chain = _Chain(tmp_path).build()
    fact = _fact(tmp_path/'facts', kind) if kind else None
    result = _decide(chain, fact, version=_version(), action=Action.PROMOTE_TO_PAPER_SHADOW)
    assert result.authorised
    assert result.decision.target_status is StrategyStatus.PAPER_SHADOW
    assert result.decision.paper_performance_evaluation_id is None
    assert result.decision.triggers == ()


@pytest.mark.parametrize('offset', [timedelta(seconds=1), timedelta(days=-31)])
def test_future_or_stale_fact_does_not_grant_authority(tmp_path, offset):
    chain = _Chain(tmp_path).build()
    fact = _fact(tmp_path/'facts', time_offset=offset)
    result = _decide(chain, fact)
    assert not result.authorised
    assert B.PAPER_PERFORMANCE_NOT_CURRENT in result.decision.blockers
    assert result.decision.triggers == ()


def test_cross_version_fact_cannot_grant_pause_authority(tmp_path):
    chain = _Chain(tmp_path).build()
    fact = _fact(tmp_path/'facts', strategy_version_id='other')
    result = _decide(chain, fact)
    assert not result.authorised
    assert B.VERSION_IDENTITY_MISMATCH in result.decision.blockers
    assert result.decision.triggers == ()


@pytest.mark.parametrize('offset', [timedelta(seconds=1), timedelta(days=-31)])
def test_invalid_performance_cannot_veto_other_pause_triggers(tmp_path, offset):
    chain = _Chain(tmp_path).build()
    fact = _fact(tmp_path/'facts', time_offset=offset)
    result = _decide(chain, fact, coverage=None)
    assert result.authorised
    assert result.decision.triggers == (B.COVERAGE_MISSING,)
    assert result.decision.blockers == ()


@pytest.mark.parametrize('coverage_missing', [False, True])
def test_erased_performance_link_with_rehashed_payload_is_rejected(tmp_path, coverage_missing):
    chain = _Chain(tmp_path).build()
    fact = _fact(tmp_path/'facts', Verdict.INSUFFICIENT)
    decision = _decide(chain, fact, coverage=None if coverage_missing else chain.coverage).decision
    path = tmp_path/'lifecycle.sqlite'
    store = SQLiteStrategyLifecycleRepository(path)
    store.record_decision(decision)
    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute('SELECT payload_json FROM strategy_lifecycle_decision').fetchone()[0])
        del payload['paper_performance_evaluation_id']
        text = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        connection.execute('UPDATE strategy_lifecycle_decision SET payload_json=?, payload_hash=?', (text, sha256(text.encode()).hexdigest()))
    with pytest.raises(StrategyLifecycleRepositoryError, match='identity'):
        store.get_decision(decision.decision_id)


def test_service_loads_durable_fail_and_restart_preserves_applied_fact(tmp_path):
    chain = _Chain(tmp_path).build()
    service, strategies, decisions, facts = _service(tmp_path, chain)
    fact = _fact(tmp_path/'facts')
    facts.record_evaluation(fact)
    result = service.apply(version=_paper(), action=Action.PAUSE, policy=_policy(), coverage=chain.coverage, applied_at=NOW)
    assert result.authorised and result.decision.state is State.APPLIED
    assert strategies.get_version('version-1').status is StrategyStatus.PAUSED
    restarted = SQLiteStrategyLifecycleRepository(tmp_path/'lifecycle.sqlite')
    stored = restarted.get_decision(result.decision.decision_id)
    assert stored == result.decision
    assert SQLiteStrategyPaperPerformanceRepository(tmp_path/'durable-performance.sqlite').get_evaluation(stored.paper_performance_evaluation_id) == fact


def test_service_rejects_noncurrent_supplied_fact_and_consumes_latest(tmp_path):
    chain = _Chain(tmp_path).build()
    service, strategies, decisions, facts = _service(tmp_path, chain)
    old = _fact(tmp_path/'old')
    current = _fact(tmp_path/'current', Verdict.INSUFFICIENT, evaluated_at=NOW+timedelta(seconds=1))
    facts.record_evaluation(old)
    facts.record_evaluation(current)
    args = dict(version=_paper(), action=Action.PAUSE, policy=_policy(), coverage=chain.coverage, applied_at=NOW+timedelta(seconds=1))
    with pytest.raises(ValueError, match='current durable'):
        service.apply(**args, paper_performance=old)
    outcome = service.apply(**args)
    assert not outcome.authorised
    assert outcome.decision.paper_performance_evaluation_id == current.evaluation_id
    assert strategies.get_version('version-1').status is StrategyStatus.PAPER_SHADOW


def test_performance_pause_interruption_recovers_with_same_fact(tmp_path, monkeypatch):
    chain = _Chain(tmp_path).build()
    service, strategies, decisions, facts = _service(tmp_path, chain)
    fact = _fact(tmp_path/'facts')
    facts.record_evaluation(fact)
    def interrupted(*args, **kwargs):
        raise RuntimeError('interrupted before transition')
    monkeypatch.setattr(strategies, 'transition', interrupted)
    args = dict(version=_paper(), action=Action.PAUSE, policy=_policy(), coverage=chain.coverage, applied_at=NOW)
    with pytest.raises(RuntimeError, match='interrupted'):
        service.apply(**args)
    prepared, = decisions.prepared_decisions('version-1')
    assert prepared.paper_performance_evaluation_id == fact.evaluation_id
    restarted = StrategyLifecycleService(controller=_controller(chain),
        strategies=StrategyApplication(SQLiteStrategyRepository(tmp_path/'strategies.sqlite')),
        decisions=SQLiteStrategyLifecycleRepository(tmp_path/'lifecycle.sqlite'),
        paper_performance_repository=SQLiteStrategyPaperPerformanceRepository(tmp_path/'durable-performance.sqlite'))
    assert restarted.reconcile(version_id='version-1', now=NOW) == (prepared,)
    applied = restarted.apply(**args).decision
    assert applied.decision_id == prepared.decision_id
    assert applied.state is State.APPLIED
    assert applied.paper_performance_evaluation_id == fact.evaluation_id


def test_supplied_fact_requires_a_durable_repository(tmp_path):
    chain = _Chain(tmp_path).build()
    service, *_ = _service(tmp_path, chain, with_performance=False)
    with pytest.raises(TypeError, match='durable repository'):
        service.apply(version=_paper(), action=Action.PAUSE, policy=_policy(), coverage=chain.coverage,
                      applied_at=NOW, paper_performance=_fact(tmp_path/'facts'))


def test_rehashed_performance_link_tamper_fails_closed(tmp_path):
    chain = _Chain(tmp_path).build()
    decision = _decide(chain, _fact(tmp_path/'facts')).decision
    path = tmp_path/'lifecycle.sqlite'
    store = SQLiteStrategyLifecycleRepository(path)
    store.record_decision(decision)
    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute('SELECT payload_json FROM strategy_lifecycle_decision').fetchone()[0])
        payload['paper_performance_evaluation_id'] = 'different-fact'
        text = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        connection.execute('UPDATE strategy_lifecycle_decision SET payload_json=?, payload_hash=?', (text, sha256(text.encode()).hexdigest()))
    with pytest.raises(StrategyLifecycleRepositoryError, match='identity'):
        store.get_decision(decision.decision_id)


def test_legacy_decision_payload_and_hash_are_unchanged(tmp_path):
    path = tmp_path/'lifecycle.sqlite'
    store = SQLiteStrategyLifecycleRepository(path)
    old = _decision()
    store.record_decision(old)
    with sqlite3.connect(path) as connection:
        before = connection.execute('SELECT payload_json,payload_hash FROM strategy_lifecycle_decision').fetchone()
    assert 'paper_performance_evaluation_id' not in json.loads(before[0])
    assert SQLiteStrategyLifecycleRepository(path).get_decision(old.decision_id) == old
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT payload_json,payload_hash FROM strategy_lifecycle_decision').fetchone() == before


def test_failure_trigger_requires_a_bound_fact():
    with pytest.raises(ValueError, match='performance'):
        _decision(action=Action.PAUSE, source_status=StrategyStatus.PAPER_SHADOW, target_status=StrategyStatus.PAUSED,
                  triggers=(B.PAPER_PERFORMANCE_FAILED,))
