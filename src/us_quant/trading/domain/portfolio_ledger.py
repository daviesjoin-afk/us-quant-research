"""Durable audit records for portfolio decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from us_quant.trading.domain.portfolio import PortfolioDecision


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
    revision: int = 0
    risk_outcome: str | None = None
    order_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, PortfolioDecision):
            raise ValueError("decision must be PortfolioDecision")
        for name in ("portfolio_cycle_id", "policy_identity", "policy_revision"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonblank")
        for name in ("observed_at", "created_at"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        for name in ("risk_outcome", "order_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be nonblank when present")
