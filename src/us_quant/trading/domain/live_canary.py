"""Provider-neutral account facts used by the Stage 4 Live canary guard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from us_quant.trading.domain.live_safety import LiveAccountFingerprint


class LiveCanaryTruthError(ValueError):
    """A broker truth snapshot is malformed and cannot authorize trading."""


def _finite_nonnegative(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise LiveCanaryTruthError(f"{name} must be a finite nonnegative Decimal")


@dataclass(frozen=True, slots=True)
class LiveCanaryPosition:
    symbol: str
    quantity: int
    market_value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise LiveCanaryTruthError("position symbol must not be blank")
        if type(self.quantity) is not int or self.quantity < 0:
            raise LiveCanaryTruthError("Live canary positions must be whole-share longs")
        _finite_nonnegative(self.market_value, "position market value")


@dataclass(frozen=True, slots=True)
class LiveCanaryTruth:
    """One fresh, exact-account snapshot; missing broker facts cannot be guessed."""

    account_fingerprint: LiveAccountFingerprint
    observed_at: datetime
    net_liquidation: Decimal
    daily_pnl: Decimal
    open_order_count: int
    open_buy_notional: Decimal
    positions: tuple[LiveCanaryPosition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.account_fingerprint, LiveAccountFingerprint):
            raise LiveCanaryTruthError("account fingerprint is required")
        if (
            not isinstance(self.observed_at, datetime)
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise LiveCanaryTruthError("observed_at must be timezone-aware")
        _finite_nonnegative(self.net_liquidation, "net liquidation")
        if not isinstance(self.daily_pnl, Decimal) or not self.daily_pnl.is_finite():
            raise LiveCanaryTruthError("daily P&L must be a finite Decimal")
        if type(self.open_order_count) is not int or self.open_order_count < 0:
            raise LiveCanaryTruthError("open_order_count must be a nonnegative integer")
        _finite_nonnegative(self.open_buy_notional, "open BUY notional")
        if not isinstance(self.positions, tuple) or any(
            not isinstance(position, LiveCanaryPosition) for position in self.positions
        ):
            raise LiveCanaryTruthError("positions must be a tuple of LiveCanaryPosition")
        symbols = [position.symbol.strip().upper() for position in self.positions]
        if len(set(symbols)) != len(symbols):
            raise LiveCanaryTruthError("Live canary truth has duplicate position symbols")

    @property
    def gross_exposure(self) -> Decimal:
        return sum((position.market_value for position in self.positions), Decimal("0"))

    def quantity_for(self, symbol: str) -> int:
        normalized = symbol.strip().upper()
        for position in self.positions:
            if position.symbol.strip().upper() == normalized:
                return position.quantity
        return 0

    @property
    def position_count(self) -> int:
        return sum(position.quantity > 0 for position in self.positions)


__all__ = ["LiveCanaryPosition", "LiveCanaryTruth", "LiveCanaryTruthError"]
