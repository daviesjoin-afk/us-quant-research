"""Account and position domain.

``Position`` and ``AccountSnapshot`` keep the validation they had before the
move.  ``BrokerConnectionState`` is new and describes the broker link in
transport terms only: whether the socket is up, whether the account is
readable, and whether an execution channel is armed.

There is deliberately no second account type here.  ``AccountSnapshot``
already models account state; a parallel ``AccountState`` would create two
canonical answers to the same question, which is the duplication this
migration exists to remove.  Field-level upgrades belong to a later Account
v2 change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from us_quant.trading.domain.common import ONE, ZERO


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
class BrokerConnectionState:
    connected: bool
    account_ready: bool
    execution_ready: bool
    message: str
