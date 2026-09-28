from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.trading.domain.portfolio import (
    PortfolioCapitalPolicy,
    PortfolioError,
    PortfolioOpenOrder,
    PortfolioPosition,
    PortfolioSnapshot,
    PortfolioStrategyAllocation,
    PortfolioStrategyExposure,
    StrategyPortfolioIntent,
    PortfolioSide,
)


def test_default_portfolio_policy_is_fail_closed() -> None:
    policy = PortfolioCapitalPolicy()

    assert policy.total_capital_limit == Decimal("0")
    assert policy.allocations == ()
    assert policy.is_configured is False


def test_allocation_weights_and_capital_cannot_exceed_the_budget() -> None:
    allocation_a = PortfolioStrategyAllocation(
        "strategy-a", Decimal("0.7"), Decimal("700"), Decimal("700"), True
    )
    allocation_b = PortfolioStrategyAllocation(
        "strategy-b", Decimal("0.4"), Decimal("400"), Decimal("400"), True
    )

    with pytest.raises(PortfolioError, match="weights exceed"):
        PortfolioCapitalPolicy(
            total_capital_limit=Decimal("1000"),
            allocations=(allocation_a, allocation_b),
        )


def test_policy_rejects_non_finite_or_negative_limits() -> None:
    with pytest.raises(PortfolioError, match="finite"):
        PortfolioCapitalPolicy(total_capital_limit=Decimal("NaN"))
    with pytest.raises(PortfolioError, match="non-negative"):
        PortfolioCapitalPolicy(total_capital_limit=Decimal("-1"))
    with pytest.raises(PortfolioError, match="at most 1"):
        PortfolioCapitalPolicy(max_symbol_concentration=Decimal("1.1"))


def test_snapshot_is_frozen_and_requires_an_aware_observation_time() -> None:
    snapshot = PortfolioSnapshot(
        cash=Decimal("1000"),
        equity=Decimal("1000"),
        observed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
        positions=(PortfolioPosition("AAPL", 10, Decimal("100")),),
        gross_exposure=Decimal("100"),
    )

    assert snapshot.symbol_exposure[0].symbol == "AAPL"
    assert snapshot.symbol_exposure[0].notional == Decimal("100")
    with pytest.raises((AttributeError, TypeError)):
        snapshot.cash = Decimal("0")  # type: ignore[misc]
    with pytest.raises(PortfolioError, match="timezone-aware"):
        PortfolioSnapshot(observed_at=datetime(2026, 9, 28))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"requested_quantity": True},
        {"requested_quantity": 0},
        {"reference_price": Decimal("NaN")},
        {"reference_price": Decimal("Infinity")},
        {"reference_price": Decimal("-1")},
    ],
)
def test_strategy_portfolio_intent_rejects_invalid_numeric_values(kwargs) -> None:
    values = {
        "strategy_version_id": "strategy-a",
        "symbol": "AAPL",
        "side": PortfolioSide.BUY,
        "requested_quantity": 1,
        "reference_price": Decimal("10"),
        "proposal_id": "proposal-1",
    }
    values.update(kwargs)

    with pytest.raises(PortfolioError):
        StrategyPortfolioIntent(**values)


def test_strategy_intent_is_a_proposal_without_broker_order_fields() -> None:
    intent = StrategyPortfolioIntent(
        strategy_version_id="strategy-a",
        symbol="AAPL",
        side=PortfolioSide.BUY,
        requested_quantity=10,
        reference_price=Decimal("10"),
        proposal_id="proposal-1",
    )

    assert not hasattr(intent, "broker_order_id")
    assert not hasattr(intent, "client_id")
    assert not hasattr(intent, "account_credentials")


@pytest.mark.parametrize("quantity", [True, 0, -1])
def test_snapshot_attribution_and_open_order_quantities_must_be_positive_integers(
    quantity,
):
    with pytest.raises(PortfolioError):
        PortfolioStrategyExposure("strategy-a", "AAPL", Decimal("10"), quantity)

    with pytest.raises(PortfolioError):
        PortfolioOpenOrder(
            "strategy-a", "AAPL", PortfolioSide.SELL, Decimal("10"), quantity
        )
