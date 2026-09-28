"""Provider-neutral account facts used by the Stage 4 Live canary guard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from us_quant.trading.domain.live_safety import LiveAccountFingerprint
from us_quant.trading.domain.orders import Side


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
class LiveCanaryOpenOrder:
    """One broker-visible order with enough facts to account its remaining risk."""

    broker_order_id: int
    symbol: str
    side: Side
    remaining_quantity: int
    limit_price: Decimal

    def __post_init__(self) -> None:
        if type(self.broker_order_id) is not int or self.broker_order_id <= 0:
            raise LiveCanaryTruthError("open-order broker id must be positive")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise LiveCanaryTruthError("open-order symbol must not be blank")
        if not isinstance(self.side, Side):
            raise LiveCanaryTruthError("open-order side is invalid")
        if type(self.remaining_quantity) is not int or self.remaining_quantity <= 0:
            raise LiveCanaryTruthError("open-order remaining quantity must be positive whole shares")
        if (
            not isinstance(self.limit_price, Decimal)
            or not self.limit_price.is_finite()
            or self.limit_price <= 0
        ):
            raise LiveCanaryTruthError("open-order limit price must be finite and positive")

    @property
    def remaining_notional(self) -> Decimal:
        return Decimal(self.remaining_quantity) * self.limit_price


@dataclass(frozen=True, slots=True)
class LiveCanaryTruth:
    """One fresh, exact-account snapshot; missing broker facts cannot be guessed."""

    account_fingerprint: LiveAccountFingerprint
    observed_at: datetime
    net_liquidation: Decimal
    daily_pnl: Decimal
    open_orders: tuple[LiveCanaryOpenOrder, ...]
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
        if not isinstance(self.open_orders, tuple) or any(
            not isinstance(order, LiveCanaryOpenOrder) for order in self.open_orders
        ):
            raise LiveCanaryTruthError("open_orders must be a tuple of LiveCanaryOpenOrder")
        order_ids = [order.broker_order_id for order in self.open_orders]
        if len(set(order_ids)) != len(order_ids):
            raise LiveCanaryTruthError("Live canary truth has duplicate open-order ids")
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

    @property
    def open_order_count(self) -> int:
        return len(self.open_orders)

    @property
    def open_buy_notional(self) -> Decimal:
        return sum(
            (
                order.remaining_notional
                for order in self.open_orders
                if order.side is Side.BUY
            ),
            Decimal("0"),
        )

    def quantity_for(self, symbol: str) -> int:
        normalized = symbol.strip().upper()
        for position in self.positions:
            if position.symbol.strip().upper() == normalized:
                return position.quantity
        return 0

    @property
    def position_count(self) -> int:
        return sum(position.quantity > 0 for position in self.positions)


__all__ = [
    "LiveCanaryOpenOrder",
    "LiveCanaryPosition",
    "LiveCanaryTruth",
    "LiveCanaryTruthError",
]
