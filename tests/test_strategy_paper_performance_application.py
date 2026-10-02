from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
import pytest

from paper_performance_support import NOW, ACCOUNT_ALIAS, history, policy, broker
from test_trading_strategy_repository import _new_version, _audit
from us_quant.trading.domain.strategy import StrategyStatus, StrategyMode
from us_quant.trading.domain.portfolio_reconciliation import BrokerOpenOrderTruth, PortfolioOrderTruth
from us_quant.trading.domain.strategy_paper_performance import StrategyPaperPerformanceVerdict as V, StrategyPaperPerformanceBlocker as B
from us_quant.trading.adapters.sqlite.strategy_repository import SQLiteStrategyRepository
from us_quant.trading.adapters.sqlite.portfolio_repository import SQLitePortfolioRepository
from us_quant.trading.adapters.sqlite.order_repository import SQLiteOrderRepository
from us_quant.trading.composition.strategy_paper_performance import build_strategy_paper_performance_components


def setup_app(tmp_path, *, data=None, store_policy=True, performance_policy_id='paper-policy'):
    strategies = SQLiteStrategyRepository(tmp_path/'strategy.sqlite')
    version = _new_version(version_id='a', status=StrategyStatus.PAPER_SHADOW, mode=StrategyMode.PAPER_SHADOW)
    strategies.insert_version(version, audit=_audit(version))
    decisions, attrs, orders = data or history()
    portfolio = SQLitePortfolioRepository(tmp_path/'portfolio.sqlite')
    order_store = SQLiteOrderRepository(tmp_path/'orders.sqlite')
    for decision, attr in zip(decisions, attrs):
        stored = portfolio.record_decision(decision)
        portfolio.record_dispatch_outcome(replace(stored, dispatch_outcome_recorded=True,dispatch_submitted=True,dispatch_status='submitted'), attr, expected_revision=stored.revision)
    for order in orders.orders:
        order_store.record_intent(order.intent, broker_order_id=order.broker_order_id, account_alias=ACCOUNT_ALIAS)
        for event in order.events:
            order_store.record_event(event)
        for execution in order.fills:
            order_store.record_fill(execution)
    source = SimpleNamespace(broker_open_order_truth=lambda: BrokerOpenOrderTruth(ACCOUNT_ALIAS,NOW,True,()))
    components = build_strategy_paper_performance_components(database_path=tmp_path/'performance.sqlite', strategies=strategies,
        portfolio_repository=portfolio,order_truth=order_store,broker_order_truth=source)
    if store_policy:
        components.repository.append_policy_revision(replace(policy(maximum_adverse_slippage=Decimal(10000)),
            policy_id=performance_policy_id),expected_current_revision=None)
    return components, strategies, portfolio, order_store, source


def evaluate(components, **changes):
    args = dict(strategy_version_id='a', policy_id='paper-policy', window_start=NOW-timedelta(days=3),window_end=NOW,
                broker=broker(quantities={}), evaluated_at=NOW)
    args.update(changes)
    return components.application.evaluate(**args)


def test_restart_rebuilds_same_evaluation_from_real_sqlite_truth(tmp_path):
    components, strategies, portfolio, orders, source = setup_app(tmp_path)
    first = evaluate(components)
    assert first.verdict is V.PASS and first.metrics.realized_pnl == Decimal(480)
    restarted = build_strategy_paper_performance_components(database_path=tmp_path/'performance.sqlite',
        strategies=SQLiteStrategyRepository(tmp_path/'strategy.sqlite'),portfolio_repository=SQLitePortfolioRepository(tmp_path/'portfolio.sqlite'),
        order_truth=SQLiteOrderRepository(tmp_path/'orders.sqlite'),broker_order_truth=source)
    second = evaluate(restarted,evaluated_at=NOW+timedelta(seconds=1))
    assert second == first
    assert second.source_execution_ids == ('b1','b2','s1')
    assert second.source_order_ids == ('a-sell','z-buy')
    assert second.source_portfolio_decision_ids == ('buy','sell')
    assert len(restarted.repository.evaluations_for_version('a')) == 1
    assert strategies.get_version('a').status is StrategyStatus.PAPER_SHADOW


def test_query_order_and_callback_order_do_not_change_identity(tmp_path):
    components, strategies, portfolio, orders, source = setup_app(tmp_path)
    first = evaluate(components)
    truth = orders.portfolio_order_truth()
    shuffled = SimpleNamespace(portfolio_order_truth=lambda: PortfolioOrderTruth(tuple(
        replace(order,fills=tuple(reversed(order.fills)),events=tuple(reversed(order.events))) for order in reversed(truth.orders))))
    shuffled_portfolio = SimpleNamespace(decisions=lambda:tuple(reversed(portfolio.decisions())),
                                         execution_attributions=lambda:tuple(reversed(portfolio.execution_attributions())))
    other = build_strategy_paper_performance_components(database_path=tmp_path/'other.sqlite',strategies=strategies,
        portfolio_repository=shuffled_portfolio,order_truth=shuffled,broker_order_truth=source)
    other.repository.append_policy_revision(policy(maximum_adverse_slippage=Decimal(10000)),expected_current_revision=None)
    assert evaluate(other) == first


def test_source_digest_binds_fills_attribution_and_decision_revision(tmp_path):
    components, strategies, portfolio, orders, source = setup_app(tmp_path)
    first = evaluate(components)
    decisions = portfolio.decisions()
    attrs = portfolio.execution_attributions()
    truth = orders.portfolio_order_truth()
    for changed_decisions, changed_attrs, changed_truth in (
        (tuple(replace(d,revision=d.revision+1) for d in decisions),attrs,truth),
        (decisions,tuple(replace(a,contributions=tuple(replace(c,proposal_id=c.proposal_id+'-changed') for c in a.contributions)) for a in attrs),truth),
        (decisions,attrs,PortfolioOrderTruth(tuple(replace(o,fills=tuple(replace(f,price=f.price+1) for f in o.fills)) for o in truth.orders))),
    ):
        components.application._portfolio = SimpleNamespace(decisions=lambda:changed_decisions,execution_attributions=lambda:changed_attrs)
        components.application._orders = SimpleNamespace(portfolio_order_truth=lambda:changed_truth)
        other = evaluate(components)
        assert other.source_digest != first.source_digest and other.evaluation_id != first.evaluation_id


def test_policy_revision_binds_evaluation_identity(tmp_path):
    components,*_ = setup_app(tmp_path)
    first = evaluate(components)
    components.repository.append_policy_revision(policy(revision=2,maximum_adverse_slippage=Decimal(10000)),expected_current_revision=1)
    second = evaluate(components)
    assert second.policy_revision == 2 and second.evaluation_id != first.evaluation_id


def test_missing_policy_is_durable_fail(tmp_path):
    components,*_ = setup_app(tmp_path,store_policy=False)
    evaluation = evaluate(components)
    assert evaluation.verdict is V.FAIL and B.POLICY_MISSING in evaluation.blockers
    assert components.repository.latest_for_version('a') == evaluation


def test_missing_policy_lookup_identity_is_durable_and_not_deduplicated(tmp_path):
    components,*_ = setup_app(tmp_path,store_policy=False)
    first = evaluate(components,policy_id='missing-A')
    second = evaluate(components,policy_id='missing-B')
    assert first.requested_policy_id == 'missing-A'
    assert second.requested_policy_id == 'missing-B'
    assert first.policy_id is None and second.policy_id is None
    assert first.verdict is V.FAIL and second.verdict is V.FAIL
    assert first.evaluation_id != second.evaluation_id
    assert components.repository.get_evaluation(first.evaluation_id) == first
    assert components.repository.get_evaluation(second.evaluation_id) == second
    assert len(components.repository.evaluations_for_version('a')) == 2


def test_failure_has_no_lifecycle_side_effect(tmp_path):
    components,strategies,*_ = setup_app(tmp_path,data=history(missing_fee=True))
    evaluation = evaluate(components)
    assert evaluation.verdict is V.FAIL and B.FEES_INCOMPLETE in evaluation.blockers
    assert strategies.get_version('a').status is StrategyStatus.PAPER_SHADOW


def test_stale_broker_truth_cannot_gain_freshness_from_evaluator_time(tmp_path):
    components,*_ = setup_app(tmp_path)
    evaluation = evaluate(components,broker=broker(quantities={},observed_at=NOW-timedelta(minutes=6)))
    assert evaluation.verdict is V.FAIL and B.RECONCILIATION_STALE in evaluation.blockers


def test_explicit_policy_age_applies_to_reconciliation_and_performance(tmp_path):
    components,_,_,_,source = setup_app(tmp_path)
    components.repository.append_policy_revision(
        policy(revision=2,maximum_adverse_slippage=Decimal(10000),maximum_reconciliation_age=timedelta(minutes=30)),
        expected_current_revision=1,
    )
    observed = NOW-timedelta(minutes=10)
    source.broker_open_order_truth = lambda: BrokerOpenOrderTruth(ACCOUNT_ALIAS,observed,True,())
    accepted = evaluate(components,broker=broker(quantities={},observed_at=observed))
    assert accepted.verdict is V.PASS and accepted.metrics.reconciliation_clean
    assert accepted.reconciliation_observed_at == observed
    observed = NOW-timedelta(minutes=31)
    refused = evaluate(components,broker=broker(quantities={},observed_at=observed))
    assert refused.verdict is V.FAIL and B.RECONCILIATION_STALE in refused.blockers


def test_cutoff_excludes_future_sell_and_keeps_pre_window_basis(tmp_path):
    components,*_ = setup_app(tmp_path,data=history(pre_window=True))
    evaluation = evaluate(components,window_end=NOW-timedelta(days=2))
    assert evaluation.metrics.attributed_fill_count == 0
    assert evaluation.metrics.realized_pnl == 0
    assert evaluation.metrics.max_cost_basis_exposure == 4800
    assert evaluation.source_execution_ids == ('b1','b2')
    assert evaluation.verdict is V.INSUFFICIENT


def test_application_replay_stops_before_future_executions(tmp_path, monkeypatch):
    import us_quant.trading.application.strategy_paper_performance as module
    original = module.replay_portfolio_execution_truth
    captured = []
    def capture(**kwargs):
        result = original(**kwargs)
        captured.append(result)
        return result
    monkeypatch.setattr(module, 'replay_portfolio_execution_truth', capture)
    components,*_ = setup_app(tmp_path,data=history(pre_window=True))
    end = NOW-timedelta(days=2)
    evaluate(components,window_end=end)
    assert captured and all(f.occurred_at <= end for f in captured[0].attributed_fills)
    assert all(item.quantity > 0 for item in captured[0].strategy_accounting)


def test_research_version_and_live_truth_are_refused(tmp_path):
    components,strategies,*_ = setup_app(tmp_path)
    current = strategies.get_version('a')
    components.application._strategies = SimpleNamespace(get_version=lambda _:replace(current,status=StrategyStatus.RESEARCH,mode=StrategyMode.RESEARCH))
    with pytest.raises(ValueError,match='Paper'):
        evaluate(components)


def test_evaluations_cannot_change_semantics_under_existing_id(tmp_path):
    components,*_ = setup_app(tmp_path)
    evaluation = evaluate(components)
    with pytest.raises(ValueError,match='semantic ID'):
        replace(evaluation,policy_revision=9)
    with pytest.raises(ValueError):
        replace(evaluation,window_end=NOW+timedelta(seconds=1))
