"""Durable multi-strategy fixtures shared by performance regression tests."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from test_portfolio_reconciliation import (
    NOW, ACCOUNT_ALIAS, Side, OrderStatus, decision_record, linked_order, fill, broker,
)
from us_quant.trading.domain.risk import RiskDecision
from us_quant.trading.domain.portfolio_ledger import execution_contributions_for_quantity
from us_quant.trading.domain.portfolio_reconciliation import (
    PortfolioOrderTruth, BrokerOpenOrderTruth, replay_portfolio_execution_truth, reconcile_portfolio_truth,
)
from us_quant.trading.domain.strategy_paper_performance import (
    POLICY_VERSION, StrategyPaperPerformancePolicy, project_strategy_paper_performance,
    evaluate_strategy_paper_performance_policy,
)


def policy(**changes):
    return replace(StrategyPaperPerformancePolicy(
        'paper-policy', 1, POLICY_VERSION, 2, 1, timedelta(hours=1),
        Decimal('1000'), Decimal('1000'), Decimal('1000'), True, True, True, True,
        timedelta(minutes=5), NOW,
    ), **changes)


def history(*, sell_price='110', missing_fee=False, pre_window=False, partial_sell=False):
    buy = decision_record('buy', (('a', 'pa', 60), ('b', 'pb', 40)), order_id='z-buy')
    buy = replace(buy, risk_decision=RiskDecision(approved=True, requested_quantity=100, approved_quantity=80, adjustments=("budget",)))
    buy_time = NOW-timedelta(days=30) if pre_window else NOW-timedelta(days=2)
    bo, ba = linked_order(buy, fills=(
        fill('b2', 'z-buy', 50, price='100', fee=None if missing_fee else '1', occurred_at=buy_time+timedelta(hours=1)),
        fill('b1', 'z-buy', 30, price='100', fee='1', occurred_at=buy_time),
    ))
    bo = replace(bo, intent=replace(bo.intent, quantity=80, session_id='buy-session', created_at=buy_time),
                 events=(replace(bo.events[0], status=OrderStatus.FILLED, filled=Decimal(80), remaining=Decimal(0)),))
    ba = replace(ba, quantity=80, contributions=execution_contributions_for_quantity(buy.decision, 80))
    sell = decision_record('sell', (('a', 'sa', -48), ('b', 'sb', -32)), order_id='a-sell')
    so, sa = linked_order(sell, fills=(fill('s1', 'a-sell', 40 if partial_sell else 80, side=Side.SELL,
                                        price=sell_price, fee='1', occurred_at=NOW-timedelta(days=1)),))
    so = replace(so, intent=replace(so.intent, session_id='sell-session', created_at=NOW-timedelta(days=1)), broker_order_id=8, events=tuple(replace(e,broker_order_id=8) for e in so.events), fills=tuple(replace(f,broker_order_id=8) for f in so.fills))
    return (buy, sell), (ba, sa), PortfolioOrderTruth((so, bo))


def project(*, data=None, start=None, end=NOW, strategy='a', quantities=None):
    decisions, attrs, orders = data or history()
    replay = replay_portfolio_execution_truth(now=NOW, account_alias=ACCOUNT_ALIAS,
                                             order_truth=orders, decisions=decisions, execution_attributions=attrs)
    reconciliation = reconcile_portfolio_truth(
        now=NOW, broker=broker(quantities=quantities or {}),
        broker_order_truth=BrokerOpenOrderTruth(ACCOUNT_ALIAS, NOW, True, ()),
        order_truth=orders, decisions=decisions, execution_attributions=attrs,
    )
    metrics = project_strategy_paper_performance(strategy_version_id=strategy, window_start=start or NOW-timedelta(days=3),
                                                window_end=end, replay=replay, reconciliation=reconciliation)
    return metrics, replay, reconciliation


def verdict(metrics, reconciliation, **changes):
    return evaluate_strategy_paper_performance_policy(policy=policy(**changes), metrics=metrics,
                                                       reconciliation=reconciliation, evaluated_at=NOW)
