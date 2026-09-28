from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.trading.domain.portfolio import PortfolioCapitalPolicy, PortfolioStrategyAllocation
from us_quant.trading.domain.portfolio_operations import PortfolioOperatingPlan
from us_quant.trading.runtime.portfolio_cycle import PortfolioPaperCycleDriver


NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)


def _plan():
    policy = PortfolioCapitalPolicy(
        total_capital_limit=Decimal("1000"),
        max_gross_exposure=Decimal("1000"),
        max_net_exposure=Decimal("1000"),
        max_single_position_notional=Decimal("500"),
        max_symbol_concentration=Decimal("1"),
        max_strategy_concentration=Decimal("1"),
        max_positions=10,
        max_open_orders=10,
        allocations=(PortfolioStrategyAllocation(
            "strategy-a", Decimal("1"), Decimal("1000"), Decimal("1000"), True
        ),),
    )
    return PortfolioOperatingPlan(
        plan_id="plan-a", revision=1, selected_version_ids=("strategy-a",),
        policy=policy, created_at=NOW, updated_at=NOW,
    )


class _PlanApplication:
    def __init__(self, plan):
        self.plan = plan

    def load(self):
        return self.plan


class _Workers:
    def __init__(self):
        self.observations = []

    def observe(self, market_snapshot, *, observed_at, entries_enabled, flatten):
        self.observations.append((market_snapshot, observed_at, entries_enabled, flatten))


class _BrokerObservation:
    def set_market_snapshot(self, snapshot):
        self.market_snapshot = snapshot


class _Operations:
    def __init__(self):
        self.runtime = object()
        self.calls = []

    def evaluate_cycle(self, *, gates, request):
        self.calls.append((gates, request))
        return object()


def _driver(permission, *, plan=None):
    plan = plan or _plan()
    workers = _Workers()
    operations = _Operations()
    broker = _BrokerObservation()
    published = []
    driver = PortfolioPaperCycleDriver(
        operations=operations,
        plan_application=_PlanApplication(plan),
        frozen_plan=plan,
        workers=workers,
        session_id="session-a",
        account_alias="DU***01",
        broker_observation_source=broker,
        autonomous_entries_allowed=permission,
        publish_operations_view=lambda: published.append(True),
    )
    return driver, workers, operations, broker, published


def test_persisted_autonomy_kill_blocks_new_entries_before_portfolio_runtime():
    driver, workers, operations, broker, published = _driver(lambda: False)
    market = object()

    result = driver(market, NOW, True, False)

    assert result is not None
    assert workers.observations == [(market, NOW, False, False)]
    assert len(operations.calls) == 1
    assert broker.market_snapshot is market
    assert published == [True]


def test_autonomy_control_plane_read_error_fails_before_risk_or_dispatch():
    def unreadable():
        raise RuntimeError("control plane unavailable")

    driver, workers, operations, _broker, published = _driver(unreadable)
    with pytest.raises(RuntimeError, match="control plane unavailable"):
        driver(object(), NOW, True, False)
    assert workers.observations == []
    assert operations.calls == []
    assert published == [True]


def test_paused_or_manual_cycle_does_not_read_autonomy_control_plane():
    def must_not_read():
        raise AssertionError("autonomy control read")

    driver, workers, operations, _broker, _published = _driver(must_not_read)
    driver(object(), NOW, False, False)
    assert workers.observations[0][2] is False
    assert len(operations.calls) == 1
