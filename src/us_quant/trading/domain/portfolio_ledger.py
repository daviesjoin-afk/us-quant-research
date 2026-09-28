"""Durable audit records for portfolio decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from us_quant.trading.domain.portfolio import (
    PortfolioDecision,
    PortfolioOrderAttribution,
    PortfolioSide,
)
from us_quant.trading.domain.risk import RiskDecision


class PortfolioStoreUnreadable(RuntimeError):
    """Stored portfolio truth is malformed or violates its invariants."""


@dataclass(frozen=True, slots=True)
class PortfolioDecisionRecord:
    """A decision and the observation/policy context that produced it."""

    decision: PortfolioDecision
    portfolio_cycle_id: str
    observed_at: datetime
    policy_identity: str
    policy_revision: str
    created_at: datetime
    # Stage 5-B ledgers predate the observation and cutoff identity fields.
    snapshot_identity: str = "legacy-unrecorded"
    proposal_cutoff: datetime | None = None
    revision: int = 0
    risk_outcome: str | None = None
    risk_decision: RiskDecision | None = None
    order_id: str | None = None
    dispatch_outcome_recorded: bool = False
    dispatch_submitted: bool = False
    dispatch_halt: bool = False
    dispatch_status: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, PortfolioDecision):
            raise ValueError("decision must be PortfolioDecision")
        for name in (
            "portfolio_cycle_id",
            "policy_identity",
            "policy_revision",
            "snapshot_identity",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonblank")
        for name in ("observed_at", "created_at"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.proposal_cutoff is not None and (
            not isinstance(self.proposal_cutoff, datetime)
            or self.proposal_cutoff.tzinfo is None
            or self.proposal_cutoff.utcoffset() is None
        ):
            raise ValueError("proposal_cutoff must be timezone-aware when present")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        for name in ("risk_outcome", "order_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be nonblank when present")
        if self.risk_decision is not None:
            if not isinstance(self.risk_decision, RiskDecision):
                raise ValueError("risk_decision must be RiskDecision when present")
            if self.risk_outcome != ("approved" if self.risk_decision.approved else "rejected"):
                raise ValueError("risk_decision must match risk_outcome")
            if (
                self.decision.action is None
                or self.decision.decision.value != "approve"
                or self.risk_decision.requested_quantity != self.decision.action.quantity
            ):
                raise ValueError("risk decision must describe this approved portfolio action")
        for name in ("dispatch_outcome_recorded", "dispatch_submitted", "dispatch_halt"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be bool")
        if self.dispatch_status is not None and not isinstance(self.dispatch_status, str):
            raise ValueError("dispatch_status must be str or None")
        if not self.dispatch_outcome_recorded and (
            self.dispatch_submitted or self.dispatch_halt or self.dispatch_status is not None
        ):
            raise ValueError("dispatch outcome fields require dispatch_outcome_recorded")
        if self.dispatch_outcome_recorded and (
            self.risk_outcome != "approved"
            or self.risk_decision is None
            or (self.dispatch_submitted and self.order_id is None)
        ):
            raise ValueError("dispatch outcome requires its approved Risk and order facts")


@dataclass(frozen=True, slots=True)
class PortfolioExecutionContribution:
    """One strategy/proposal's signed whole-share part of a submitted order."""

    portfolio_decision_id: str
    strategy_version_id: str
    proposal_id: str
    symbol: str
    signed_quantity: int

    def __post_init__(self) -> None:
        for name in (
            "portfolio_decision_id",
            "strategy_version_id",
            "proposal_id",
            "symbol",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonblank")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if type(self.signed_quantity) is not int or self.signed_quantity == 0:
            raise ValueError("signed_quantity must be a non-zero integer")


@dataclass(frozen=True, slots=True)
class PortfolioExecutionAttribution:
    """Durable link from one order to its actual approved strategy quantities."""

    order_id: str
    portfolio_decision_id: str
    symbol: str
    side: PortfolioSide
    quantity: int
    contributions: tuple[PortfolioExecutionContribution, ...]

    def __post_init__(self) -> None:
        for name in ("order_id", "portfolio_decision_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonblank")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be nonblank")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if not isinstance(self.side, PortfolioSide):
            raise ValueError("side must be PortfolioSide")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        if not isinstance(self.contributions, tuple) or not self.contributions:
            raise ValueError("contributions must be a non-empty tuple")
        if any(
            not isinstance(item, PortfolioExecutionContribution)
            or item.portfolio_decision_id != self.portfolio_decision_id
            or item.symbol != self.symbol
            for item in self.contributions
        ):
            raise ValueError("contributions must belong to this decision and symbol")
        signed_total = sum(item.signed_quantity for item in self.contributions)
        expected_total = self.quantity if self.side is PortfolioSide.BUY else -self.quantity
        if signed_total != expected_total:
            raise ValueError("contributions must sum to the approved order quantity")


def execution_contributions_for_quantity(
    decision: PortfolioDecision, approved_quantity: int
) -> tuple[PortfolioExecutionContribution, ...]:
    """Scale signed proposal attribution to Risk's approved quantity deterministically."""

    if decision.net_quantity == 0:
        raise ValueError("zero-net decision cannot have execution attribution")
    if type(approved_quantity) is not int or not 0 < approved_quantity <= abs(decision.net_quantity):
        raise ValueError("approved quantity must fit the portfolio net action")
    denominator = abs(decision.net_quantity)
    signed_target = approved_quantity if decision.net_quantity > 0 else -approved_quantity
    allocated = []
    for item in sorted(
        decision.attribution,
        key=lambda entry: (entry.strategy_version_id, entry.proposal_id),
    ):
        base, remainder = divmod(
            item.signed_requested_quantity * approved_quantity,
            denominator,
        )
        allocated.append([item, base, remainder])
    remaining = signed_target - sum(entry[1] for entry in allocated)
    for entry in sorted(
        allocated,
        key=lambda value: (-value[2], value[0].strategy_version_id, value[0].proposal_id),
    )[:remaining]:
        entry[1] += 1
    return tuple(
        PortfolioExecutionContribution(
            portfolio_decision_id=decision.decision_id,
            strategy_version_id=item.strategy_version_id,
            proposal_id=item.proposal_id,
            symbol=item.symbol,
            signed_quantity=quantity,
        )
        for item, quantity, _ in allocated
        if quantity != 0
    )
