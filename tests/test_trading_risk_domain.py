"""Risk domain tests.

The domain is where the two rules that make the risk layer trustworthy are
enforced structurally rather than by convention:

* a symbol override may only ever *tighten* a limit, so the value it produces
  is validated to lie in ``(0, 1]`` and can never be a widening;
* a ``RiskDecision`` cannot describe a nonsensical outcome -- approving zero
  shares, rejecting while approving some, reducing a quantity without saying
  what reduced it.

Both are checked by construction here, independently of the application
service that consumes them, because a domain type that permits an incoherent
value will eventually be handed one.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal
from typing import get_type_hints

import pytest

from us_quant.trading.domain.common import ONE, ZERO
from us_quant.trading.domain.risk import (
    LayeredRiskLimits,
    RiskDecision,
    RiskEvaluationRequest,
    RiskLimits,
    SessionRiskOverrides,
    SymbolRiskOverrides,
    resolve_session_risk_overrides,
    resolve_symbol_risk_overrides,
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


def limits(**overrides: Decimal) -> RiskLimits:
    values = {
        "max_gross_exposure_pct": Decimal("0.50"),
        "max_position_exposure_pct": Decimal("0.10"),
        "daily_loss_halt_pct": Decimal("0.02"),
        "drawdown_halt_pct": Decimal("0.08"),
        "allow_margin_borrowing": False,
    }
    values.update(overrides)
    return RiskLimits(**values)


def proposal(
    *,
    action: TradeAction = TradeAction.BUY,
    quantity: int = 10,
    symbol: str = "MUU",
    price: Decimal = Decimal("36"),
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


# -- RiskLimits -----------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    (
        "max_gross_exposure_pct",
        "max_position_exposure_pct",
        "daily_loss_halt_pct",
        "drawdown_halt_pct",
    ),
)
def test_risk_limits_require_ratios_in_the_unit_interval(field: str) -> None:
    for value in (ZERO, Decimal("-0.01"), Decimal("1.01")):
        with pytest.raises(ValueError, match=r"must be in \(0, 1\]"):
            limits(**{field: value})
    # The document boundary itself is allowed: "all of it" is a valid ceiling.
    assert getattr(limits(**{field: ONE}), field) == ONE


def test_risk_limits_default_to_refusing_margin_borrowing() -> None:
    """The Paper posture must not have to be requested explicitly."""

    assert limits().allow_margin_borrowing is False


# -- SymbolRiskOverrides --------------------------------------------------


def test_symbol_overrides_default_to_no_refinement() -> None:
    overrides = SymbolRiskOverrides()
    assert overrides.max_position_exposure_pct is None
    assert overrides.exposure_multiplier == ONE
    assert overrides.allowed is True


def test_symbol_override_position_limit_must_be_a_ratio_or_absent() -> None:
    assert (
        SymbolRiskOverrides(
            max_position_exposure_pct=Decimal("0.05")
        ).max_position_exposure_pct
        == Decimal("0.05")
    )
    for value in (ZERO, Decimal("-0.1"), Decimal("1.5")):
        with pytest.raises(ValueError, match=r"must be in \(0, 1\]"):
            SymbolRiskOverrides(max_position_exposure_pct=value)


def test_symbol_override_multiplier_must_be_positive() -> None:
    """A zero multiplier would make a leveraged position invisible."""

    for value in (ZERO, Decimal("-1")):
        with pytest.raises(ValueError, match="must be positive"):
            SymbolRiskOverrides(exposure_multiplier=value)
    assert SymbolRiskOverrides(
        exposure_multiplier=Decimal("2")
    ).exposure_multiplier == Decimal("2")


# -- SessionRiskOverrides -------------------------------------------------


def test_session_overrides_default_to_nothing_and_resolve_their_hints() -> None:
    overrides = SessionRiskOverrides()
    for field in (
        "maximum_trades_per_day",
        "entry_start",
        "last_entry",
        "force_flat",
        "daily_loss_limit",
        "max_position_fraction",
    ):
        assert getattr(overrides, field) is None
    # ``from __future__ import annotations`` turns every hint into a string;
    # this is what proves they still resolve to real types at runtime.
    hints = get_type_hints(SessionRiskOverrides)
    assert hints["entry_start"] == time | None
    assert hints["maximum_trades_per_day"] == int | None


def test_session_overrides_carry_session_policy_and_the_one_risk_ceiling() -> None:
    overrides = SessionRiskOverrides(
        entry_start=time(10, 0),
        last_entry=time(15, 30),
        maximum_trades_per_day=4,
        daily_loss_limit=Decimal("50"),
        max_position_fraction=Decimal("0.05"),
    )
    assert overrides.entry_start == time(10, 0)
    assert overrides.max_position_fraction == Decimal("0.05")


# -- LayeredRiskLimits and the resolvers ----------------------------------


def test_layered_limits_default_to_no_symbols_and_no_session_overrides() -> None:
    layered = LayeredRiskLimits(account=limits())
    assert dict(layered.symbols) == {}
    assert isinstance(layered.session, SessionRiskOverrides)


def test_resolvers_return_the_declared_entry_or_a_neutral_default() -> None:
    layered = LayeredRiskLimits(
        account=limits(),
        symbols={"MUU": SymbolRiskOverrides(exposure_multiplier=Decimal("2"))},
        session=SessionRiskOverrides(maximum_trades_per_day=3),
    )
    assert resolve_symbol_risk_overrides(
        "MUU", layered
    ).exposure_multiplier == Decimal("2")
    # An absent entry means "no refinement", not "unlimited".
    missing = resolve_symbol_risk_overrides("AAPL", layered)
    assert missing == SymbolRiskOverrides()
    assert resolve_session_risk_overrides(layered).maximum_trades_per_day == 3


# -- RiskDecision ---------------------------------------------------------


def test_an_approval_may_reduce_but_never_exceed_the_request() -> None:
    full = RiskDecision.approve(requested_quantity=10)
    assert full.approved_quantity == 10
    assert full.adjustments == ()

    with pytest.raises(ValueError, match="cannot exceed the requested"):
        RiskDecision(
            approved=True, requested_quantity=10, approved_quantity=11
        )


def test_an_approval_of_nothing_is_not_an_approval() -> None:
    with pytest.raises(ValueError, match="at least one share"):
        RiskDecision(
            approved=True, requested_quantity=10, approved_quantity=0
        )
    with pytest.raises(ValueError, match="at least one share"):
        RiskDecision.approve(requested_quantity=0)


def test_a_reduction_must_say_what_reduced_it() -> None:
    """Spec 39: a quantity must never change silently."""

    with pytest.raises(ValueError, match="must state what reduced it"):
        RiskDecision.approve(
            requested_quantity=10, approved_quantity=4
        )
    trimmed = RiskDecision.approve(
        requested_quantity=10,
        approved_quantity=4,
        adjustments=("cash cap reduced quantity 10 → 4",),
    )
    assert trimmed.adjustments == ("cash cap reduced quantity 10 → 4",)


def test_an_approved_decision_cannot_carry_rejection_reasons() -> None:
    with pytest.raises(ValueError, match="cannot carry rejection reasons"):
        RiskDecision(
            approved=True,
            requested_quantity=10,
            approved_quantity=10,
            reasons=("nope",),
        )


def test_a_rejection_approves_nothing_and_must_state_why() -> None:
    rejected = RiskDecision.reject("halt", requested_quantity=10)
    assert rejected.approved is False
    assert rejected.approved_quantity == 0
    with pytest.raises(ValueError, match="approves no quantity"):
        RiskDecision(
            approved=False,
            requested_quantity=10,
            approved_quantity=1,
            reasons=("halt",),
        )
    with pytest.raises(ValueError, match="at least one reason"):
        RiskDecision.reject()


def test_a_rejection_deduplicates_its_reasons() -> None:
    """The same ceiling is often reached by more than one code path."""

    rejected = RiskDecision.reject("halt", "halt", "no cash")
    assert rejected.reasons == ("halt", "no cash")


def test_quantities_must_be_real_non_negative_whole_numbers() -> None:
    for value in (Decimal("1"), "1", None, 1.0):
        with pytest.raises(ValueError, match="must be a whole number"):
            RiskDecision.reject("halt", requested_quantity=value)  # type: ignore[arg-type]
    # ``isinstance(True, int)`` is true in Python, so a bool needs refusing
    # explicitly rather than by the numeric check.
    with pytest.raises(ValueError, match="must be a whole number"):
        RiskDecision.reject("halt", requested_quantity=True)
    for field in ("requested_quantity", "approved_quantity"):
        with pytest.raises(ValueError, match="cannot be negative"):
            RiskDecision(
                approved=False,
                requested_quantity=-1 if field == "requested_quantity" else 0,
                approved_quantity=-1 if field == "approved_quantity" else 0,
                reasons=("halt",),
            )


# -- RiskEvaluationRequest ------------------------------------------------


def test_risk_evaluation_request_defaults_its_commission_to_zero() -> None:
    request = RiskEvaluationRequest(
        proposal=proposal(), execution_symbol="MUU"
    )
    assert request.estimated_commission == ZERO
    assert request.action is TradeAction.BUY


def test_risk_evaluation_request_validates_its_inputs() -> None:
    with pytest.raises(TypeError, match="must carry a TradeProposal"):
        RiskEvaluationRequest(
            proposal="not a proposal",  # type: ignore[arg-type]
            execution_symbol="MUU",
        )
    with pytest.raises(ValueError, match="execution symbol must not be empty"):
        RiskEvaluationRequest(proposal=proposal(), execution_symbol="   ")
    with pytest.raises(ValueError, match="cannot be negative"):
        RiskEvaluationRequest(
            proposal=proposal(),
            execution_symbol="MUU",
            estimated_commission=Decimal("-1"),
        )
    with pytest.raises(TypeError, match="must be a Decimal"):
        RiskEvaluationRequest(
            proposal=proposal(),
            execution_symbol="MUU",
            estimated_commission=0.35,  # type: ignore[arg-type]
        )


def test_the_request_is_not_an_order() -> None:
    """Order identity is created after risk has spoken, by execution."""

    request = RiskEvaluationRequest(
        proposal=proposal(), execution_symbol="MUU"
    )
    for forbidden in (
        "order_id",
        "client_order_id",
        "broker_order_id",
        "tif",
        "transmit",
    ):
        assert not hasattr(request, forbidden), forbidden
