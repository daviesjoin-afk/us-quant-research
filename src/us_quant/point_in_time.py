"""Point-in-time research schemas kept separate from the live universe.

The current official universe is a useful present-day scanner input, but it is
not historical membership evidence.  These immutable models define the data
contract that execution-candidate research must satisfy before it can remove
the survivorship-bias gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class UniverseMembership:
    symbol: str
    effective_from: date
    effective_to: date | None
    eligible_for_research: bool
    leader_tier: int
    sector: str
    country_status: str
    source_snapshot_hash: str

    def contains(self, trading_date: date) -> bool:
        """Use half-open membership intervals: ``from <= date < to``."""

        return bool(
            self.effective_from <= trading_date
            and (
                self.effective_to is None
                or trading_date < self.effective_to
            )
        )


@dataclass(frozen=True, slots=True)
class CorporateAction:
    symbol: str
    effective_date: date
    action_type: str
    ratio_or_cash: Decimal
    source: str
    verified_at: datetime


@dataclass(frozen=True, slots=True)
class DelistingEvent:
    symbol: str
    delisting_date: date
    settlement_price: Decimal | None
    reason: str
    source: str


class PointInTimeCoverageError(ValueError):
    """Raised when current-only membership is used as historical evidence."""


@dataclass(frozen=True, slots=True)
class PointInTimeUniverse:
    memberships: tuple[UniverseMembership, ...]
    coverage_start: date
    coverage_end: date
    source_hash: str
    complete_history: bool

    def members_at(
        self,
        trading_date: date,
        *,
        leader_tiers: frozenset[int] | None = None,
    ) -> tuple[UniverseMembership, ...]:
        """Return historically eligible rows using attributes valid that day."""

        self.require_coverage(trading_date)
        tiers = leader_tiers
        return tuple(
            sorted(
                (
                    row
                    for row in self.memberships
                    if row.contains(trading_date)
                    and row.eligible_for_research
                    and (tiers is None or row.leader_tier in tiers)
                ),
                key=lambda row: row.symbol,
            )
        )

    def require_coverage(self, trading_date: date) -> None:
        if not self.complete_history:
            raise PointInTimeCoverageError(
                "current-only universe cannot support historical membership"
            )
        if not self.coverage_start <= trading_date <= self.coverage_end:
            raise PointInTimeCoverageError(
                f"no point-in-time universe coverage for {trading_date.isoformat()}"
            )


def delisting_settlement_value(
    event: DelistingEvent, *, quantity: int
) -> Decimal:
    """Resolve an explicit delisting cash settlement or fail closed."""

    if quantity < 0:
        raise ValueError("quantity must be non-negative")
    if event.settlement_price is None:
        raise PointInTimeCoverageError(
            f"delisting settlement is unknown for {event.symbol}"
        )
    if event.settlement_price < 0:
        raise ValueError("delisting settlement price cannot be negative")
    return event.settlement_price * quantity
