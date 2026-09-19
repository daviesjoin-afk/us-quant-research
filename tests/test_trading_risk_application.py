"""RiskApplication tests: the single pre-trade risk authority.

The behaviour pinned here is the contract the transitional runtime depends on,
and each group exists because of a specific way the old arrangement could go
wrong.

* **Buys are reduced, not vetoed.**  A proposal larger than a ceiling comes
  back approved *for less*, with the ceilings named.  Refusing instead would
  have changed the sizing the runtime already relied on.
* **A halt never blocks a reduction.**  Daily-loss, drawdown, symbol
  permission, an already-breached ceiling and missing prices of unrelated
  holdings all refuse a *purchase*; none of them may refuse an exit.  A risk
  control that stops the account getting out is worse than no control.
* **A symbol override may only tighten.**  The retired engine let a
  per-symbol ``max_position_exposure_pct`` *replace* the account limit, so a
  symbol entry could raise the account's own hard ceiling.  That is the
  specific widening this suite forbids.
* **Buys fail closed on missing information.**  An unpriced holding makes the
  gross exposure unknowable, and assuming zero would understate the book.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal

import pytest

from us_quant.trading.application.risk import RiskApplication
from us_quant.trading.domain.account import (
    Position,
    RiskAccountSnapshot,
)
from us_quant.trading.domain.common import ONE, ZERO
from us_quant.trading.domain.risk import (
    LayeredRiskLimits,
    RiskEvaluationRequest,
    RiskLimits,
    SessionRiskOverrides,
    SymbolRiskOverrides,
)
from us_quant.trading.domain.strategy import (
    StrategyIdentity,
    TradeAction,
    TradeProposal,
)

NOW = datetime(2026, 9, 19, 18, 30, tzinfo=timezone.utc)

_STRATEGY = StrategyIdentity(
    strategy_id="intraday-auto-rotation",
    version_id="version-1",
    parameter_hash="hash-1",
)

#: 1500 net liquidation, fully in cash, and 10% per symbol / 50% gross.  At
#: $36 a share the position ceiling is four whole shares, which is small
#: enough that every other ceiling can be made to bind on purpose.
_DEFAULT_MULTIPLIER_PRICE = Decimal("36")


def account(
    *,
    net_liquidation: Decimal = Decimal("1500"),
    cash: Decimal = Decimal("1500"),
    day_start_equity: Decimal = Decimal("1500"),
    high_watermark: Decimal = Decimal("1500"),
) -> RiskAccountSnapshot:
    return RiskAccountSnapshot(
        net_liquidation=net_liquidation,
        cash=cash,
        day_start_equity=day_start_equity,
        high_watermark=high_watermark,
        timestamp=NOW,
    )


def application(
    *,
    account_limits: RiskLimits | None = None,
    symbols: dict[str, SymbolRiskOverrides] | None = None,
    session: SessionRiskOverrides | None = None,
    exposure_multipliers: dict[str, Decimal] | None = None,
) -> RiskApplication:
    return RiskApplication(
        LayeredRiskLimits(
            account=account_limits
            or RiskLimits(
                max_gross_exposure_pct=Decimal("0.50"),
                max_position_exposure_pct=Decimal("0.10"),
                daily_loss_halt_pct=Decimal("0.02"),
                drawdown_halt_pct=Decimal("0.08"),
            ),
            symbols=symbols or {},
            session=session or SessionRiskOverrides(),
        ),
        exposure_multipliers=exposure_multipliers,
    )


def proposal(
    *,
    action: TradeAction = TradeAction.BUY,
    quantity: int = 100,
    symbol: str = "MUU",
    price: Decimal = _DEFAULT_MULTIPLIER_PRICE,
) -> TradeProposal:
    return TradeProposal(
        strategy=_STRATEGY,
        symbol=symbol,
        action=action,
        desired_quantity=quantity,
        reference_price=price,
        reason="unit test fixture",
        generated_at=NOW,
    )


def request(
    *,
    action: TradeAction = TradeAction.BUY,
    quantity: int = 100,
    symbol: str = "MUU",
    price: Decimal = _DEFAULT_MULTIPLIER_PRICE,
    commission: Decimal = Decimal("0.35"),
) -> RiskEvaluationRequest:
    return RiskEvaluationRequest(
        proposal=proposal(
            action=action, quantity=quantity, symbol=symbol, price=price
        ),
        execution_symbol=symbol,
        estimated_commission=commission,
    )


def held(
    symbol: str, quantity: int, price: Decimal = Decimal("36")
) -> Position:
    return Position(
        symbol=symbol, quantity=quantity, average_price=price
    )


# -- the application holds policy, not state ------------------------------


def test_the_application_keeps_only_immutable_policy() -> None:
    """Spec 41: positions, cash and pending orders arrive with each call."""

    app = application(exposure_multipliers={"MUU": Decimal("2")})
    assert set(vars(app)) == {"_limits", "_exposure_multipliers"}
    assert app.limits.account.max_gross_exposure_pct == Decimal("0.50")


def test_the_application_refuses_a_non_positive_configured_multiplier() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        application(exposure_multipliers={"MUU": ZERO})


# -- multiplier resolution ------------------------------------------------


def test_the_exposure_multiplier_resolution_order_is_fixed() -> None:
    app = application(
        symbols={"MUU": SymbolRiskOverrides(exposure_multiplier=Decimal("3"))},
        exposure_multipliers={"MUU": Decimal("2"), "AAPL": Decimal("4")},
    )
    # A symbol override that says something other than one wins outright...
    assert app.exposure_multiplier("MUU") == Decimal("3")
    # ...otherwise the configured substitution multiplier applies...
    assert app.exposure_multiplier("AAPL") == Decimal("4")
    # ...and an unknown symbol is unweighted.
    assert app.exposure_multiplier("MSFT") == ONE


def test_a_symbol_override_of_one_falls_through_to_the_configured_value() -> None:
    app = application(
        symbols={"MUU": SymbolRiskOverrides(exposure_multiplier=ONE)},
        exposure_multipliers={"MUU": Decimal("2")},
    )
    assert app.exposure_multiplier("MUU") == Decimal("2")


def test_the_session_overrides_are_exposed_without_reparsing_the_layers() -> None:
    session = SessionRiskOverrides(
        entry_start=time(10, 0),
        last_entry=time(15, 30),
        maximum_trades_per_day=3,
        daily_loss_limit=Decimal("50"),
        max_position_fraction=Decimal("0.05"),
    )
    app = application(session=session)
    assert app.session_overrides == session


def test_symbol_overrides_are_exposed_and_defaulted() -> None:
    app = application(
        symbols={"MUU": SymbolRiskOverrides(allowed=False)}
    )
    assert app.symbol_overrides("MUU").allowed is False
    assert app.symbol_overrides("AAPL") == SymbolRiskOverrides()


# -- BUY: approved in full ------------------------------------------------


def test_a_purchase_inside_every_ceiling_is_approved_in_full() -> None:
    decision = application().evaluate(
        request=request(quantity=4),
        account=account(),
        positions={},
        market_prices={},
        allowed_symbols={"MUU"},
    )
    assert decision.approved is True
    assert decision.requested_quantity == 4
    assert decision.approved_quantity == 4
    assert decision.adjustments == ()
    assert decision.reasons == ()


# -- BUY: each ceiling reduces --------------------------------------------


def test_the_position_ceiling_reduces_the_quantity() -> None:
    decision = application().evaluate(
        request=request(quantity=100),
        account=account(),
        positions={},
        market_prices={},
    )
    assert decision.approved is True
    assert decision.requested_quantity == 100
    assert decision.approved_quantity == 4
    assert decision.adjustments == (
        "position exposure cap reduced quantity 100 → 4",
    )


def test_the_gross_ceiling_reduces_the_quantity() -> None:
    decision = application(
        account_limits=RiskLimits(
            max_gross_exposure_pct=Decimal("0.10"),
            max_position_exposure_pct=ONE,
            daily_loss_halt_pct=Decimal("0.02"),
            drawdown_halt_pct=Decimal("0.08"),
        )
    ).evaluate(
        request=request(quantity=100),
        account=account(),
        positions={},
        market_prices={},
    )
    assert decision.approved_quantity == 4
    assert decision.adjustments == (
        "gross exposure cap reduced quantity 100 → 4",
    )


def test_the_cash_ceiling_reduces_the_quantity() -> None:
    decision = application(
        account_limits=RiskLimits(
            max_gross_exposure_pct=ONE,
            max_position_exposure_pct=ONE,
            daily_loss_halt_pct=Decimal("0.02"),
            drawdown_halt_pct=Decimal("0.08"),
        )
    ).evaluate(
        request=request(quantity=100),
        account=account(cash=Decimal("100")),
        positions={},
        market_prices={},
    )
    assert decision.approved_quantity == 2
    assert decision.adjustments == (
        "cash cap reduced quantity 100 → 2",
    )


def test_every_binding_ceiling_is_named() -> None:
    """The operator must be able to tell *why* a quantity changed."""

    decision = application(
        account_limits=RiskLimits(
            max_gross_exposure_pct=Decimal("0.10"),
            max_position_exposure_pct=Decimal("0.10"),
            daily_loss_halt_pct=Decimal("0.02"),
            drawdown_halt_pct=Decimal("0.08"),
        )
    ).evaluate(
        request=request(quantity=100),
        account=account(),
        positions={},
        market_prices={},
    )
    assert decision.approved_quantity == 4
    assert decision.adjustments == (
        "position exposure cap reduced quantity 100 → 4",
        "gross exposure cap reduced quantity 100 → 4",
    )


def test_a_ceiling_that_did_not_bind_is_not_named() -> None:
    decision = application().evaluate(
        request=request(quantity=100),
        account=account(),
        positions={},
        market_prices={},
    )
    assert not any(
        "gross" in adjustment for adjustment in decision.adjustments
    )
    assert not any(
        "cash" in adjustment for adjustment in decision.adjustments
    )


def test_the_requested_quantity_is_never_exceeded() -> None:
    """Risk reduces; it does not top an order up."""

    decision = application(
        account_limits=RiskLimits(
            max_gross_exposure_pct=ONE,
            max_position_exposure_pct=ONE,
            daily_loss_halt_pct=Decimal("0.02"),
            drawdown_halt_pct=Decimal("0.08"),
        )
    ).evaluate(
        request=request(quantity=3),
        account=account(),
        positions={},
        market_prices={},
    )
    assert decision.approved_quantity == 3
    assert decision.adjustments == ()


# -- BUY: quantity adjustments and rejections -----------------------------


def test_a_reduced_approval_reports_both_quantities() -> None:
    """Spec 102: requested 10, capped at 6, is an approval of 6."""

    decision = application(
        account_limits=RiskLimits(
            max_gross_exposure_pct=ONE,
            max_position_exposure_pct=ONE,
            daily_loss_halt_pct=Decimal("0.02"),
            drawdown_halt_pct=Decimal("0.08"),
        )
    ).evaluate(
        request=request(quantity=10, price=Decimal("60")),
        account=account(cash=Decimal("400")),
        positions={},
        market_prices={},
    )
    # (400 - 0.35) / 60 = 6.66 -> 6 whole shares.
    assert decision.approved is True
    assert decision.requested_quantity == 10
    assert decision.approved_quantity == 6
    assert decision.adjustments


def test_no_safe_whole_share_is_a_rejection_that_names_the_ceiling() -> None:
    decision = application(
        account_limits=RiskLimits(
            max_gross_exposure_pct=ONE,
            max_position_exposure_pct=Decimal("0.0001"),
            daily_loss_halt_pct=Decimal("0.02"),
            drawdown_halt_pct=Decimal("0.08"),
        )
    ).evaluate(
        request=request(quantity=10),
        account=account(),
        positions={},
        market_prices={},
    )
    assert decision.approved is False
    assert decision.approved_quantity == 0
    assert decision.requested_quantity == 10
    assert "no safe whole-share quantity remains" in decision.reasons
    assert (
        "position exposure cap left no whole share" in decision.reasons
    )


def test_a_hold_proposal_produces_no_order() -> None:
    decision = application().evaluate(
        request=request(action=TradeAction.HOLD, quantity=0),
        account=account(),
        positions={},
        market_prices={},
    )
    assert decision.approved is False
    assert decision.approved_quantity == 0
    assert decision.reasons == ("hold proposal requires no order",)


# -- BUY: gates -----------------------------------------------------------


def test_a_blocked_symbol_refuses_a_purchase() -> None:
    decision = application(
        symbols={"MUU": SymbolRiskOverrides(allowed=False)}
    ).evaluate(
        request=request(quantity=1),
        account=account(),
        positions={},
        market_prices={},
    )
    assert decision.approved is False
    assert (
        "execution symbol is blocked by risk overrides"
        in decision.reasons
    )


def test_a_symbol_outside_the_runtime_scope_refuses_a_purchase() -> None:
    decision = application().evaluate(
        request=request(quantity=1),
        account=account(),
        positions={},
        market_prices={},
        allowed_symbols={"AAPL"},
    )
    assert decision.approved is False
    assert "execution symbol is not allowed" in decision.reasons


def test_an_absent_scope_check_does_not_block_anything() -> None:
    decision = application().evaluate(
        request=request(quantity=1),
        account=account(),
        positions={},
        market_prices={},
        allowed_symbols=None,
    )
    assert decision.approved is True


def test_a_daily_account_loss_halt_refuses_a_purchase() -> None:
    """(day start − net liquidation) ÷ day start, unchanged from v1."""

    decision = application().evaluate(
        request=request(quantity=1),
        account=account(
            net_liquidation=Decimal("1469"),
            cash=Decimal("1469"),
        ),
        positions={},
        market_prices={},
    )
    assert decision.approved is False
    assert "daily account loss halt is active" in decision.reasons


def test_a_drawdown_halt_refuses_a_purchase() -> None:
    """(high watermark − net liquidation) ÷ high watermark, unchanged."""

    decision = application().evaluate(
        request=request(quantity=1),
        account=account(
            net_liquidation=Decimal("1365"),
            cash=Decimal("1365"),
            day_start_equity=Decimal("1500"),
        ),
        positions={},
        market_prices={},
    )
    assert decision.approved is False
    assert "account drawdown halt is active" in decision.reasons


def test_a_non_positive_net_liquidation_refuses_a_purchase() -> None:
    decision = application().evaluate(
        request=request(quantity=1),
        account=account(
            net_liquidation=ZERO,
            cash=ZERO,
            day_start_equity=ZERO,
            high_watermark=ZERO,
        ),
        positions={},
        market_prices={},
    )
    assert decision.approved is False
    assert (
        "account net liquidation is not positive" in decision.reasons
    )


def test_a_purchase_fails_closed_when_a_held_symbol_has_no_price() -> None:
    """Assuming zero exposure would understate the book."""

    decision = application().evaluate(
        request=request(quantity=1),
        account=account(),
        positions={"AAPL": held("AAPL", 5)},
        market_prices={},
    )
    assert decision.approved is False
    assert "missing market price for AAPL" in decision.reasons


def test_the_purchased_symbol_needs_no_quote_of_its_own() -> None:
    """Its exposure comes from the proposal's own reference price."""

    decision = application().evaluate(
        request=request(quantity=1),
        account=account(),
        positions={"MUU": held("MUU", 1)},
        market_prices={},
    )
    assert decision.approved is True


# -- BUY: exposure arithmetic --------------------------------------------


def test_an_exposure_multiplier_increases_the_measured_exposure() -> None:
    """Twice the weight per share means half the shares."""

    decision = application(
        exposure_multipliers={"MUU": Decimal("2")}
    ).evaluate(
        request=request(quantity=100),
        account=account(),
        positions={},
        market_prices={},
    )
    assert decision.approved_quantity == 2
    assert decision.adjustments == (
        "position exposure cap reduced quantity 100 → 2",
    )


def test_an_existing_holding_consumes_its_own_symbol_ceiling() -> None:
    # Two of the four available shares are already held.
    decision = application().evaluate(
        request=request(quantity=100),
        account=account(),
        positions={"MUU": held("MUU", 2)},
        market_prices={"MUU": _DEFAULT_MULTIPLIER_PRICE},
    )
    assert decision.approved_quantity == 2


def test_an_existing_holding_consumes_gross_room() -> None:
    decision = application(
        account_limits=RiskLimits(
            max_gross_exposure_pct=Decimal("0.10"),
            max_position_exposure_pct=ONE,
            daily_loss_halt_pct=Decimal("0.02"),
            drawdown_halt_pct=Decimal("0.08"),
        )
    ).evaluate(
        request=request(quantity=100),
        account=account(),
        positions={"AAPL": held("AAPL", 2)},
        market_prices={"AAPL": Decimal("36")},
    )
    # Gross room is 150 − 72 = 78 -> two whole shares.
    assert decision.approved_quantity == 2
    assert decision.adjustments == (
        "gross exposure cap reduced quantity 100 → 2",
    )


# -- BUY: overrides may only tighten -------------------------------------


def test_a_symbol_ceiling_can_only_tighten_the_account_ceiling() -> None:
    """The widening the retired engine allowed, now refused by construction."""

    tightening = application(
        symbols={"MUU": SymbolRiskOverrides(
            max_position_exposure_pct=Decimal("0.02")
        )}
    ).evaluate(
        request=request(quantity=100),
        account=account(),
        positions={},
        market_prices={},
    )
    assert tightening.approved_quantity == 0
    assert tightening.reasons

    # A symbol override far above the account limit must not raise it: the
    # account ceiling is 10%, which is still four shares.
    loosening = application(
        symbols={"MUU": SymbolRiskOverrides(
            max_position_exposure_pct=Decimal("0.90")
        )}
    ).evaluate(
        request=request(quantity=100),
        account=account(),
        positions={},
        market_prices={},
    )
    assert loosening.approved_quantity == 4


def test_a_session_fraction_can_only_tighten_the_account_ceiling() -> None:
    tighter = application(
        session=SessionRiskOverrides(
            max_position_fraction=Decimal("0.05")
        )
    ).evaluate(
        request=request(quantity=100),
        account=account(),
        positions={},
        market_prices={},
    )
    assert tighter.approved_quantity == 2

    looser = application(
        session=SessionRiskOverrides(
            max_position_fraction=Decimal("0.90")
        )
    ).evaluate(
        request=request(quantity=100),
        account=account(),
        positions={},
        market_prices={},
    )
    assert looser.approved_quantity == 4


def test_a_non_positive_session_fraction_leaves_no_room() -> None:
    decision = application(
        session=SessionRiskOverrides(max_position_fraction=ZERO)
    ).evaluate(
        request=request(quantity=10),
        account=account(),
        positions={},
        market_prices={},
    )
    assert decision.approved is False
    assert "no safe whole-share quantity remains" in decision.reasons


# -- BUY: cash, not buying power ----------------------------------------

def test_the_cash_ceiling_pays_the_commission_out_of_the_cash() -> None:
    """Spec 103: $100 cash, $36 a share, $0.35 commission -> two shares."""

    decision = application(
        account_limits=RiskLimits(
            max_gross_exposure_pct=ONE,
            max_position_exposure_pct=ONE,
            daily_loss_halt_pct=Decimal("0.02"),
            drawdown_halt_pct=Decimal("0.08"),
        )
    ).evaluate(
        request=request(
            quantity=10,
            price=Decimal("36"),
            commission=Decimal("0.35"),
        ),
        account=account(cash=Decimal("100")),
        positions={},
        market_prices={},
    )
    assert decision.approved_quantity == 2

    # A commission large enough to matter removes another share: (100 − 30)
    # ÷ 36 = 1.94 -> one.
    costly = application(
        account_limits=RiskLimits(
            max_gross_exposure_pct=ONE,
            max_position_exposure_pct=ONE,
            daily_loss_halt_pct=Decimal("0.02"),
            drawdown_halt_pct=Decimal("0.08"),
        )
    ).evaluate(
        request=request(
            quantity=10, price=Decimal("36"), commission=Decimal("30")
        ),
        account=account(cash=Decimal("100")),
        positions={},
        market_prices={},
    )
    assert costly.approved_quantity == 1


def test_buying_power_is_never_consulted() -> None:
    """The cash cap is about the account's own cash, not a credit line."""

    snapshot = account(cash=Decimal("100"))
    assert not hasattr(snapshot, "buying_power")
    decision = application().evaluate(
        request=request(quantity=10),
        account=snapshot,
        positions={},
        market_prices={},
    )
    assert decision.approved_quantity == 2


def test_margin_borrowing_removes_the_cash_ceiling_when_configured() -> None:
    app = application(
        account_limits=RiskLimits(
            max_gross_exposure_pct=ONE,
            max_position_exposure_pct=Decimal("0.10"),
            daily_loss_halt_pct=Decimal("0.02"),
            drawdown_halt_pct=Decimal("0.08"),
            allow_margin_borrowing=True,
        )
    )
    decision = app.evaluate(
        request=request(quantity=100),
        account=account(cash=Decimal("10")),
        positions={},
        market_prices={},
    )
    assert decision.approved_quantity == 4


# -- SELL: a reduction is always allowed ---------------------------------


def test_a_risk_reducing_sell_is_approved_in_full() -> None:
    decision = application().evaluate(
        request=request(action=TradeAction.SELL, quantity=1),
        account=account(),
        positions={"MUU": held("MUU", 2)},
        market_prices={},
    )
    assert decision.approved is True
    assert decision.approved_quantity == 1
    assert decision.adjustments == ()


def test_a_sell_is_allowed_during_a_daily_loss_halt() -> None:
    decision = application().evaluate(
        request=request(action=TradeAction.SELL, quantity=1),
        account=account(
            net_liquidation=Decimal("1469"), cash=Decimal("1397")
        ),
        positions={"MUU": held("MUU", 2)},
        market_prices={},
    )
    assert decision.approved is True


def test_a_sell_is_allowed_during_a_drawdown_halt() -> None:
    decision = application().evaluate(
        request=request(action=TradeAction.SELL, quantity=1),
        account=account(
            net_liquidation=Decimal("1200"), cash=Decimal("1100")
        ),
        positions={"MUU": held("MUU", 2)},
        market_prices={},
    )
    assert decision.approved is True


def test_a_sell_is_allowed_when_the_symbol_is_now_blocked() -> None:
    decision = application(
        symbols={"MUU": SymbolRiskOverrides(allowed=False)}
    ).evaluate(
        request=request(action=TradeAction.SELL, quantity=2),
        account=account(),
        positions={"MUU": held("MUU", 2)},
        market_prices={},
    )
    assert decision.approved is True


def test_a_sell_is_allowed_outside_the_runtime_scope() -> None:
    decision = application().evaluate(
        request=request(action=TradeAction.SELL, quantity=1),
        account=account(),
        positions={"MUU": held("MUU", 2)},
        market_prices={},
        allowed_symbols={"AAPL"},
    )
    assert decision.approved is True


def test_a_sell_is_allowed_when_the_account_is_already_over_its_limits() -> None:
    """The account that most needs to sell must be able to."""

    decision = application().evaluate(
        request=request(action=TradeAction.SELL, quantity=1),
        account=account(
            net_liquidation=Decimal("100"),
            cash=Decimal("0"),
            day_start_equity=Decimal("500"),
            high_watermark=Decimal("500"),
        ),
        positions={"MUU": held("MUU", 4)},
        market_prices={"MUU": Decimal("36")},
    )
    assert decision.approved is True


def test_a_sell_needs_no_price_for_any_holding() -> None:
    decision = application().evaluate(
        request=request(action=TradeAction.SELL, quantity=1),
        account=account(),
        positions={"MUU": held("MUU", 2, Decimal("99")), "AAPL": held("AAPL", 3)},
        market_prices={},
    )
    assert decision.approved is True


def test_an_oversell_is_refused_rather_than_trimmed() -> None:
    """Selling more than is held means the caller and broker disagree."""

    decision = application().evaluate(
        request=request(action=TradeAction.SELL, quantity=6),
        account=account(),
        positions={"MUU": held("MUU", 5)},
        market_prices={},
    )
    assert decision.approved is False
    assert decision.approved_quantity == 0
    assert decision.requested_quantity == 6
    assert decision.reasons == ("short positions are disabled",)


def test_a_sell_of_the_whole_position_is_allowed() -> None:
    decision = application().evaluate(
        request=request(action=TradeAction.SELL, quantity=5),
        account=account(),
        positions={"MUU": held("MUU", 5)},
        market_prices={},
    )
    assert decision.approved is True
    assert decision.approved_quantity == 5


def test_a_sell_without_a_position_is_refused() -> None:
    decision = application().evaluate(
        request=request(action=TradeAction.SELL, quantity=1),
        account=account(),
        positions={},
        market_prices={},
    )
    assert decision.approved is False
    assert decision.reasons == ("no long position to reduce",)


def test_a_flat_position_is_treated_as_no_position() -> None:
    """A zero-quantity row is a bookkeeping artefact, not something to sell."""

    decision = application().evaluate(
        request=request(action=TradeAction.SELL, quantity=1),
        account=account(),
        positions={"MUU": held("MUU", 0)},
        market_prices={},
    )
    assert decision.approved is False
    assert decision.reasons == ("no long position to reduce",)
