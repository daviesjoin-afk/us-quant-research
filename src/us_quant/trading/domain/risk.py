"""Risk domain: the limits an operator configures and the verdict a proposal gets.

Everything in this module is pure.  There is no broker client, no storage, no
UI and no execution import, because a risk *rule* must be assertable without a
running system -- and because a domain type that could reach an adapter would
eventually be asked to do that adapter's job.

Three boundaries are load-bearing.

**A limit is a policy value, not a permission.**  ``RiskLimits`` and its
layered overrides describe what the account is allowed to carry.  They say
nothing about what is permitted *right now*: that answer needs a live account
and a live proposal, and it is ``RiskApplication``'s to give.

**A symbol override may only tighten.**  ``SymbolRiskOverrides.max_position_exposure_pct``
used to *replace* the account-wide position limit, so a per-symbol entry could
quietly raise the ceiling above the account's own hard limit.  The effective
limit is now a ``min`` over every layer -- see ``RiskApplication`` -- which
makes "a symbol override relaxed the account limit" unrepresentable rather than
merely discouraged.

**A decision names a quantity, not a boolean.**  ``RiskDecision`` carries the
requested and approved whole-share counts plus the adjustments that explain any
difference, so a reduction is never silent.  A rejection approves zero shares
and states why; an approval approves at least one share and states nothing
against it.  Those are enforced in ``__post_init__``, which means an
inconsistent verdict cannot be constructed, let alone returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
from typing import Mapping

from us_quant.trading.domain.common import ONE, ZERO
from us_quant.trading.domain.strategy import (
    TradeAction,
    TradeProposal,
)


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """The account-wide ceilings, as fractions of net liquidation.

    Every ratio is in ``(0, 1]`` and the rule is unchanged from the retired
    ``us_quant.risk``: a zero or negative ceiling would be a limit that can
    never be satisfied, and this migration is not the place to loosen it.
    """

    max_gross_exposure_pct: Decimal
    max_position_exposure_pct: Decimal
    daily_loss_halt_pct: Decimal
    drawdown_halt_pct: Decimal
    allow_margin_borrowing: bool = False

    def __post_init__(self) -> None:
        percentages = (
            self.max_gross_exposure_pct,
            self.max_position_exposure_pct,
            self.daily_loss_halt_pct,
            self.drawdown_halt_pct,
        )
        if any(value <= ZERO or value > ONE for value in percentages):
            raise ValueError("risk percentages must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class SymbolRiskOverrides:
    """Per-symbol policy layered on top of the account limits.

    Validation is new here, and it fails closed.  An ``exposure_multiplier``
    of zero would make a leveraged position invisible to every exposure
    calculation -- the risk layer would report room that does not exist -- so
    it is refused at construction rather than silently treated as one.
    """

    max_position_exposure_pct: Decimal | None = None
    exposure_multiplier: Decimal = ONE
    allowed: bool = True

    def __post_init__(self) -> None:
        if self.max_position_exposure_pct is not None and not (
            ZERO < self.max_position_exposure_pct <= ONE
        ):
            raise ValueError(
                "symbol position exposure limit must be in (0, 1]"
            )
        if self.exposure_multiplier <= ZERO:
            raise ValueError("symbol exposure multiplier must be positive")


@dataclass(frozen=True, slots=True)
class SessionRiskOverrides:
    """Session-scoped policy that is not an account-limit question.

    ``entry_start`` / ``last_entry`` / ``maximum_trades_per_day`` /
    ``daily_loss_limit`` / ``force_flat`` remain session/runtime gating --
    ``RiskApplication`` exposes them read-only so the transitional AutoQuant
    engine can keep reading them without parsing ``LayeredRiskLimits`` again.
    ``max_position_fraction`` is the one field that *is* a risk ceiling, and it
    may only tighten the account limit.
    """

    maximum_trades_per_day: int | None = None
    entry_start: time | None = None
    last_entry: time | None = None
    force_flat: time | None = None
    daily_loss_limit: Decimal | None = None
    max_position_fraction: Decimal | None = None


@dataclass(frozen=True, slots=True)
class LayeredRiskLimits:
    """Account limits plus their per-symbol and per-session refinements."""

    account: RiskLimits
    symbols: Mapping[str, SymbolRiskOverrides] = field(
        default_factory=dict
    )
    session: SessionRiskOverrides = field(
        default_factory=SessionRiskOverrides
    )


def resolve_symbol_risk_overrides(
    symbol: str,
    layered_limits: LayeredRiskLimits,
) -> SymbolRiskOverrides:
    """The overrides for ``symbol``, or the neutral default.

    An absent entry means "no refinement", not "unlimited": the returned
    default carries the account limit and a multiplier of one.
    """

    overrides = layered_limits.symbols.get(symbol)
    if overrides is not None:
        return overrides
    return SymbolRiskOverrides()


def resolve_session_risk_overrides(
    layered_limits: LayeredRiskLimits,
) -> SessionRiskOverrides:
    """The session refinements, or the neutral default."""

    return layered_limits.session


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """The verdict on one proposal, in whole shares.

    ``approved_quantity`` is what may actually be sent, and it is never more
    than ``requested_quantity``.  When it is *less*, ``adjustments`` says which
    ceilings did the trimming -- a quantity that changed without explanation is
    exactly the failure this type exists to prevent.
    """

    approved: bool
    requested_quantity: int
    approved_quantity: int
    reasons: tuple[str, ...] = ()
    adjustments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("requested_quantity", self.requested_quantity),
            ("approved_quantity", self.approved_quantity),
        ):
            # ``isinstance(True, int)`` is true in Python, so a bool would pass
            # a plain numeric check and become a quantity of one.
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be a whole number")
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.approved_quantity > self.requested_quantity:
            raise ValueError(
                "approved quantity cannot exceed the requested quantity"
            )
        if self.approved:
            if self.approved_quantity <= 0:
                raise ValueError(
                    "an approved decision must approve at least one share"
                )
            if self.reasons:
                raise ValueError(
                    "an approved decision cannot carry rejection reasons"
                )
            if (
                self.approved_quantity < self.requested_quantity
                and not self.adjustments
            ):
                raise ValueError(
                    "a reduced approval must state what reduced it"
                )
        else:
            if self.approved_quantity != 0:
                raise ValueError(
                    "a rejected decision approves no quantity"
                )
            if not self.reasons:
                raise ValueError(
                    "a rejected decision must state at least one reason"
                )

    @classmethod
    def approve(
        cls,
        *,
        requested_quantity: int,
        approved_quantity: int | None = None,
        adjustments: tuple[str, ...] = (),
    ) -> RiskDecision:
        """Approve the whole request, or a stated part of it."""

        return cls(
            approved=True,
            requested_quantity=requested_quantity,
            approved_quantity=(
                requested_quantity
                if approved_quantity is None
                else approved_quantity
            ),
            adjustments=tuple(adjustments),
        )

    @classmethod
    def reject(
        cls,
        *reasons: str,
        requested_quantity: int = 0,
    ) -> RiskDecision:
        """Refuse the request, keeping the first mention of each reason.

        Duplicates are dropped because the same ceiling is often reached by
        more than one code path, and a repeated sentence reads as two
        distinct problems.
        """

        return cls(
            approved=False,
            requested_quantity=requested_quantity,
            approved_quantity=0,
            reasons=tuple(dict.fromkeys(reasons)),
        )


@dataclass(frozen=True, slots=True)
class RiskEvaluationRequest:
    """Everything the risk layer needs about *this* proposal.

    Deliberately not an order: there is no ``order_id``,
    ``client_order_id``, ``broker_order_id``, ``tif`` or ``transmit``, and none
    may be added.  Order identity is created after risk has spoken, by
    execution -- a request that carried one would be submittable, which is the
    boundary the two layers exist to hold.

    ``execution_symbol`` is separate from ``proposal.symbol`` because a
    proposal names the *signal* symbol and execution may need the substituted
    instrument; the risk layer must judge the thing that will actually be
    traded.
    """

    proposal: TradeProposal
    execution_symbol: str
    estimated_commission: Decimal = ZERO

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, TradeProposal):
            raise TypeError("request must carry a TradeProposal")
        if not self.execution_symbol.strip():
            raise ValueError("execution symbol must not be empty")
        if isinstance(self.estimated_commission, bool) or not isinstance(
            self.estimated_commission, Decimal
        ):
            raise TypeError("estimated commission must be a Decimal")
        if self.estimated_commission < ZERO:
            raise ValueError("estimated commission cannot be negative")

    @property
    def action(self) -> TradeAction:
        """The action being evaluated.  A convenience, not a second truth."""

        return self.proposal.action


__all__ = [
    "LayeredRiskLimits",
    "RiskDecision",
    "RiskEvaluationRequest",
    "RiskLimits",
    "SessionRiskOverrides",
    "SymbolRiskOverrides",
    "resolve_session_risk_overrides",
    "resolve_symbol_risk_overrides",
]
