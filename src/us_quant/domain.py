from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import uuid4


ZERO = Decimal("0")
ONE = Decimal("1")


def decimal(value: Decimal | str | int | float) -> Decimal:
    """Convert external numeric input without silently keeping binary floats."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class Environment(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    CREATED = "created"
    RISK_APPROVED = "risk_approved"
    RISK_REJECTED = "risk_rejected"
    SUBMITTING = "submitting"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELED = "canceled"
    BROKER_REJECTED = "broker_rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol is required")
        if self.timestamp.tzinfo is None:
            raise ValueError("bar timestamp must be timezone-aware")
        prices = (self.open, self.high, self.low, self.close)
        if any(price <= ZERO for price in prices):
            raise ValueError("bar prices must be positive")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high price is inconsistent")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low price is inconsistent")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")


@dataclass(frozen=True, slots=True)
class MarketSlice:
    timestamp: datetime
    bars: dict[str, Bar]

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("market slice timestamp must be timezone-aware")
        if not self.bars:
            raise ValueError("market slice requires at least one bar")
        for symbol, bar in self.bars.items():
            if symbol != bar.symbol or bar.timestamp != self.timestamp:
                raise ValueError("bar key/timestamp does not match market slice")


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    quantity: int
    average_price: Decimal
    exposure_multiplier: Decimal = ONE

    def __post_init__(self) -> None:
        if self.quantity < 0:
            raise ValueError("MVP positions cannot be short")
        if self.average_price < ZERO:
            raise ValueError("average price cannot be negative")
        if self.exposure_multiplier <= ZERO:
            raise ValueError("exposure multiplier must be positive")

    def market_value(self, price: Decimal) -> Decimal:
        return price * self.quantity

    def risk_exposure(self, price: Decimal) -> Decimal:
        return self.market_value(price) * self.exposure_multiplier


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    net_liquidation: Decimal
    cash: Decimal
    day_start_equity: Decimal
    high_watermark: Decimal
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if min(
            self.net_liquidation,
            self.cash,
            self.day_start_equity,
            self.high_watermark,
        ) < ZERO:
            raise ValueError("account values cannot be negative")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    order_id: str
    client_order_id: str
    signal_symbol: str
    execution_symbol: str
    side: Side
    quantity: int
    estimated_price: Decimal
    exposure_multiplier: Decimal = ONE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(
        cls,
        *,
        signal_symbol: str,
        execution_symbol: str,
        side: Side,
        quantity: int,
        estimated_price: Decimal,
        exposure_multiplier: Decimal = ONE,
    ) -> OrderIntent:
        order_id = str(uuid4())
        return cls(
            order_id=order_id,
            client_order_id=f"uq-{order_id}",
            signal_symbol=signal_symbol,
            execution_symbol=execution_symbol,
            side=side,
            quantity=quantity,
            estimated_price=estimated_price,
            exposure_multiplier=exposure_multiplier,
        )

    def __post_init__(self) -> None:
        if self.quantity <= 0 or isinstance(self.quantity, bool):
            raise ValueError("order quantity must be a positive whole number")
        if self.estimated_price <= ZERO:
            raise ValueError("estimated price must be positive")
        if self.exposure_multiplier <= ZERO:
            raise ValueError("exposure multiplier must be positive")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...] = ()

    @classmethod
    def approve(cls) -> RiskDecision:
        return cls(approved=True)

    @classmethod
    def reject(cls, *reasons: str) -> RiskDecision:
        return cls(approved=False, reasons=tuple(reasons))


@dataclass(frozen=True, slots=True)
class OrderEvent:
    order_id: str
    status: OrderStatus
    idempotency_key: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = field(default_factory=dict)

