from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from us_quant.domain import OrderIntent


@dataclass(frozen=True, slots=True)
class BrokerFill:
    broker_order_id: str
    client_order_id: str
    symbol: str
    quantity: int
    fill_price: Decimal
    commission: Decimal


class Broker(Protocol):
    def submit(self, intent: OrderIntent) -> BrokerFill:
        """Submit an already risk-approved order."""


class LiveBrokerDisabled:
    """Hard guard used until IBKR Paper acceptance tests are complete."""

    def submit(self, intent: OrderIntent) -> BrokerFill:
        raise RuntimeError(
            "live brokerage submission is disabled; use IBKR Paper integration"
        )

