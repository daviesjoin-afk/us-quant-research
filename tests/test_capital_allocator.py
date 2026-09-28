from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.trading.application.portfolio import CapitalAllocator
from us_quant.trading.domain.portfolio import (
    PortfolioBlocker,
    PortfolioCapitalPolicy,
    PortfolioError,
    PortfolioOpenOrder,
    PortfolioPosition,
    PortfolioSide,
    PortfolioSnapshot,
    PortfolioStrategyAllocation,
    PortfolioStrategyExposure,
    PortfolioVerdict,
    StrategyPortfolioIntent,
)


NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


def _allocation(
    strategy: str,
    *,
    weight: str = "0.4",
    capital: str = "400",
    gross: str = "400",
    enabled: bool = True,
) -> PortfolioStrategyAllocation:
    return PortfolioStrategyAllocation(
        strategy,
        Decimal(weight),
        Decimal(capital),
        Decimal(gross),
        enabled,
    )


def _policy(*, allocations=None, **overrides) -> PortfolioCapitalPolicy:
    values = {
        "total_capital_limit": Decimal("1000"),
        "max_gross_exposure": Decimal("1000"),
        "max_net_exposure": Decimal("1000"),
        "max_single_position_notional": Decimal("500"),
        "max_symbol_concentration": Decimal("0.5"),
        "max_strategy_concentration": Decimal("0.5"),
        "max_positions": 5,
        "max_open_orders": 5,
        "allocations": (
            _allocation("strategy-a"),
            _allocation("strategy-b"),
        ) if allocations is None else allocations,
    }
    values.update(overrides)
    return PortfolioCapitalPolicy(**values)


def _snapshot(
    *,
    equity="1000",
    positions=(),
    open_orders=(),
    strategy_exposure=(),
    gross=None,
    net=None,
    observed_at=NOW,
) -> PortfolioSnapshot:
    positions = tuple(positions)
    open_orders = tuple(open_orders)
    exposure = sum((item.notional for item in positions), Decimal("0")) + sum(
        (
            item.notional
            for item in open_orders
            if item.side is PortfolioSide.BUY
        ),
        Decimal("0"),
    )
    return PortfolioSnapshot(
        cash=Decimal(equity) - exposure,
        equity=Decimal(equity),
        gross_exposure=exposure if gross is None else Decimal(gross),
        net_exposure=exposure if net is None else Decimal(net),
        positions=positions,
        open_orders=open_orders,
        strategy_exposure=tuple(strategy_exposure),
        observed_at=observed_at,
    )


def _intent(strategy, symbol, side, quantity, proposal, price="10"):
    return StrategyPortfolioIntent(
        strategy_version_id=strategy,
        symbol=symbol,
        side=side,
        requested_quantity=quantity,
        reference_price=Decimal(price),
        proposal_id=proposal,
    )


def _decision(intents, *, policy=None, snapshot=None):
    return CapitalAllocator().allocate(
        intents=intents,
        snapshot=_snapshot() if snapshot is None else snapshot,
        policy=_policy() if policy is None else policy,
    )


def test_unconfigured_default_policy_rejects_and_unallocated_strategy_cannot_trade():
    intent = _intent("strategy-a", "AAPL", PortfolioSide.BUY, 1, "p-1")
    missing = _decision((intent,), policy=PortfolioCapitalPolicy())
    assert missing[0].blocker is PortfolioBlocker.POLICY_MISSING

    unallocated = _decision(
        (_intent("strategy-c", "AAPL", PortfolioSide.BUY, 1, "p-2"),)
    )
    assert unallocated[0].decision is PortfolioVerdict.REJECT
    assert unallocated[0].blocker is PortfolioBlocker.UNKNOWN_STRATEGY


def test_zero_allocation_is_rejected_even_when_other_strategy_is_enabled():
    policy = _policy(
        allocations=(
            _allocation("strategy-a", weight="0", capital="0", gross="0"),
            _allocation("strategy-b"),
        )
    )
    result = _decision(
        (_intent("strategy-a", "AAPL", PortfolioSide.BUY, 1, "zero"),),
        policy=policy,
    )

    assert result[0].blocker is PortfolioBlocker.STRATEGY_ALLOCATION_EXCEEDED


def test_governed_but_disabled_strategy_is_not_allocated():
    policy = _policy(
        allocations=(
            _allocation("strategy-a", enabled=False),
            _allocation("strategy-b"),
        )
    )
    result = _decision(
        (_intent("strategy-a", "AAPL", PortfolioSide.BUY, 1, "disabled"),),
        policy=policy,
    )

    assert result[0].blocker is PortfolioBlocker.STRATEGY_NOT_ALLOCATED


def test_strategy_cap_is_checked_against_existing_attributed_exposure():
    snapshot = _snapshot(
        positions=(PortfolioPosition("AAPL", 30, Decimal("300")),),
        strategy_exposure=(
            PortfolioStrategyExposure("strategy-a", "AAPL", Decimal("300"), 30),
        ),
    )
    result = _decision(
        (_intent("strategy-a", "AAPL", PortfolioSide.BUY, 11, "over-cap"),),
        snapshot=snapshot,
    )

    assert result[0].blocker is PortfolioBlocker.STRATEGY_ALLOCATION_EXCEEDED


def test_strategy_concentration_uses_current_snapshot_equity():
    policy = _policy(
        total_capital_limit=Decimal("2000"),
        max_gross_exposure=Decimal("2000"),
        allocations=(
            _allocation("strategy-a", weight="0.8", capital="1500", gross="1500"),
            _allocation("strategy-b", weight="0.2", capital="500", gross="500"),
        ),
    )
    result = _decision(
        (_intent("strategy-a", "AAPL", PortfolioSide.BUY, 60, "equity-concentration"),),
        snapshot=_snapshot(equity="1000"),
        policy=policy,
    )

    assert result[0].blocker is PortfolioBlocker.STRATEGY_ALLOCATION_EXCEEDED


def test_total_capital_limit_rejects_without_resizing():
    snapshot = _snapshot(
        positions=(PortfolioPosition("MSFT", 95, Decimal("950")),),
    )
    result = _decision(
        (_intent("strategy-a", "AAPL", PortfolioSide.BUY, 10, "capital"),),
        snapshot=snapshot,
    )

    assert result[0].blocker is PortfolioBlocker.CAPITAL_EXCEEDED
    assert result[0].action is None


def test_symbol_concentration_is_portfolio_wide():
    snapshot = _snapshot(
        positions=(PortfolioPosition("AAPL", 40, Decimal("400")),),
        strategy_exposure=(
            PortfolioStrategyExposure("strategy-b", "AAPL", Decimal("400"), 40),
        ),
    )
    result = _decision(
        (_intent("strategy-a", "AAPL", PortfolioSide.BUY, 11, "concentration"),),
        snapshot=snapshot,
    )

    assert result[0].blocker is PortfolioBlocker.SYMBOL_CONCENTRATION_EXCEEDED


def test_position_and_open_order_limits_count_at_portfolio_symbol_level():
    snapshot = _snapshot(
        positions=(PortfolioPosition("MSFT", 10, Decimal("100")),),
    )
    policy = _policy(max_positions=1)
    result = _decision(
        (_intent("strategy-a", "AAPL", PortfolioSide.BUY, 1, "position"),),
        snapshot=snapshot,
        policy=policy,
    )
    assert result[0].blocker is PortfolioBlocker.POSITION_LIMIT_EXCEEDED

    with_open_order = _snapshot(
        open_orders=(
            PortfolioOpenOrder(
                "strategy-a", "MSFT", PortfolioSide.BUY, Decimal("10"), 1
            ),
        ),
    )
    order_limited = _decision(
        (_intent("strategy-a", "AAPL", PortfolioSide.BUY, 1, "open-order"),),
        snapshot=with_open_order,
        policy=_policy(max_open_orders=1),
    )
    assert order_limited[0].blocker is PortfolioBlocker.POSITION_LIMIT_EXCEEDED


def test_strategy_buys_on_one_symbol_are_aggregated_before_concentration_checks():
    result = _decision(
        (
            _intent("strategy-a", "AAPL", PortfolioSide.BUY, 30, "a"),
            _intent("strategy-b", "AAPL", PortfolioSide.BUY, 30, "b"),
        )
    )

    assert result[0].blocker is PortfolioBlocker.SYMBOL_CONCENTRATION_EXCEEDED


def test_same_direction_intents_aggregate_to_one_deterministic_action():
    result = _decision(
        (
            _intent("strategy-a", "AAPL", PortfolioSide.BUY, 10, "a"),
            _intent("strategy-b", "AAPL", PortfolioSide.BUY, 5, "b"),
        ),
        policy=_policy(max_positions=1, max_open_orders=1),
    )

    assert len(result) == 1
    assert result[0].action is not None
    assert result[0].action.side is PortfolioSide.BUY
    assert result[0].action.quantity == 15
    assert result[0].net_quantity == 15


def test_opposite_intents_net_and_keep_strategy_attribution():
    snapshot = _snapshot(
        positions=(PortfolioPosition("AAPL", 6, Decimal("60")),),
        strategy_exposure=(
            PortfolioStrategyExposure("strategy-b", "AAPL", Decimal("60"), 6),
        ),
    )
    result = _decision(
        (
            _intent("strategy-a", "AAPL", PortfolioSide.BUY, 10, "buy-a"),
            _intent("strategy-b", "AAPL", PortfolioSide.SELL, 6, "sell-b"),
        ),
        snapshot=snapshot,
        policy=_policy(max_symbol_concentration=Decimal("0.15")),
    )

    assert result[0].action is not None
    assert result[0].action.side is PortfolioSide.BUY
    assert result[0].action.quantity == 4
    assert {(item.strategy_version_id, item.signed_requested_quantity) for item in result[0].attribution} == {
        ("strategy-a", 10),
        ("strategy-b", -6),
    }
    assert all(item.portfolio_decision_id == result[0].decision_id for item in result[0].attribution)


def test_fully_offset_intents_approve_without_creating_an_action():
    snapshot = _snapshot(
        positions=(PortfolioPosition("AAPL", 10, Decimal("100")),),
        strategy_exposure=(
            PortfolioStrategyExposure("strategy-a", "AAPL", Decimal("100"), 10),
        ),
    )
    try:
        result = _decision(
            (
                _intent("strategy-a", "AAPL", PortfolioSide.SELL, 10, "sell-a"),
                _intent("strategy-b", "AAPL", PortfolioSide.BUY, 10, "buy-b"),
            ),
            snapshot=snapshot,
        )
    except PortfolioError as error:
        raise AssertionError(
            "allocator must return a zero-net no-action decision"
        ) from error

    assert result[0].decision is PortfolioVerdict.APPROVE
    assert result[0].net_quantity == 0
    assert result[0].action is None
    assert len(result[0].attribution) == 2


def test_intent_order_does_not_change_decisions_or_attribution():
    intents = (
        _intent("strategy-a", "AAPL", PortfolioSide.BUY, 10, "a"),
        _intent("strategy-b", "AAPL", PortfolioSide.SELL, 6, "b"),
        _intent("strategy-a", "MSFT", PortfolioSide.BUY, 2, "c"),
    )
    assert _decision(intents) == _decision(tuple(reversed(intents)))


def test_duplicate_proposal_identity_and_reference_conflict_reject():
    duplicate = _decision(
        (
            _intent("strategy-a", "AAPL", PortfolioSide.BUY, 1, "same"),
            _intent("strategy-b", "MSFT", PortfolioSide.BUY, 1, "same"),
        )
    )
    assert all(item.blocker is PortfolioBlocker.CONFLICTING_INTENT for item in duplicate)

    different_price = _decision(
        (
            _intent("strategy-a", "AAPL", PortfolioSide.BUY, 1, "a", "10"),
            _intent("strategy-b", "AAPL", PortfolioSide.BUY, 1, "b", "11"),
        )
    )
    assert different_price[0].blocker is PortfolioBlocker.CONFLICTING_INTENT


def test_snapshot_must_have_a_timezone_aware_as_of():
    result = _decision(
        (_intent("strategy-a", "AAPL", PortfolioSide.BUY, 1, "no-as-of"),),
        snapshot=PortfolioSnapshot(equity=Decimal("1000")),
    )

    assert result[0].blocker is PortfolioBlocker.INVALID_SNAPSHOT


def test_sell_cannot_exceed_strategy_owned_exposure():
    snapshot = _snapshot(
        positions=(PortfolioPosition("AAPL", 5, Decimal("50")),),
        strategy_exposure=(
            PortfolioStrategyExposure("strategy-a", "AAPL", Decimal("50"), 5),
        ),
    )
    result = _decision(
        (_intent("strategy-a", "AAPL", PortfolioSide.SELL, 6, "oversell"),),
        snapshot=snapshot,
    )

    assert result[0].blocker is PortfolioBlocker.STRATEGY_ALLOCATION_EXCEEDED


def test_sell_cannot_liquidate_shares_owned_by_another_strategy():
    snapshot = _snapshot(
        positions=(PortfolioPosition("AAPL", 7, Decimal("700")),),
        strategy_exposure=(
            PortfolioStrategyExposure("strategy-a", "AAPL", Decimal("500"), 5),
            PortfolioStrategyExposure("strategy-b", "AAPL", Decimal("200"), 2),
        ),
    )
    result = _decision(
        (_intent("strategy-a", "AAPL", PortfolioSide.SELL, 6, "sell-other", "50"),),
        snapshot=snapshot,
    )

    assert result[0].blocker is PortfolioBlocker.STRATEGY_ALLOCATION_EXCEEDED
    assert result[0].action is None


def test_open_sell_order_reserves_strategy_owned_shares():
    snapshot = _snapshot(
        positions=(PortfolioPosition("AAPL", 5, Decimal("50")),),
        open_orders=(
            PortfolioOpenOrder(
                "strategy-a", "AAPL", PortfolioSide.SELL, Decimal("40"), 4
            ),
        ),
        strategy_exposure=(
            PortfolioStrategyExposure("strategy-a", "AAPL", Decimal("50"), 5),
        ),
    )
    result = _decision(
        (_intent("strategy-a", "AAPL", PortfolioSide.SELL, 2, "sell-reserved"),),
        snapshot=snapshot,
    )

    assert result[0].blocker is PortfolioBlocker.STRATEGY_ALLOCATION_EXCEEDED
    assert result[0].action is None


def test_sell_cannot_be_backed_by_an_unfilled_buy_order():
    snapshot = _snapshot(
        open_orders=(
            PortfolioOpenOrder(
                "strategy-a", "AAPL", PortfolioSide.BUY, Decimal("100"), 10
            ),
        ),
    )
    result = _decision(
        (_intent("strategy-a", "AAPL", PortfolioSide.SELL, 10, "sell-unfilled"),),
        snapshot=snapshot,
    )

    # Per-strategy sell validation runs before account-level position checks.
    assert result[0].blocker is PortfolioBlocker.STRATEGY_ALLOCATION_EXCEEDED


def test_buy_delta_cannot_exceed_available_snapshot_cash():
    snapshot = PortfolioSnapshot(
        cash=Decimal("20"),
        equity=Decimal("1000"),
        observed_at=NOW,
    )
    result = _decision(
        (_intent("strategy-a", "AAPL", PortfolioSide.BUY, 3, "cash"),),
        snapshot=snapshot,
    )

    assert result[0].blocker is PortfolioBlocker.INSUFFICIENT_CASH
