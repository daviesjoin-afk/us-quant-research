"""Order domain: sides, lifecycle status, intents, events and fills.

``Side``, ``OrderStatus``, ``OrderIntent`` and ``OrderEvent`` moved without
behavioural change.  The whole-share and positive-price invariants are load
bearing safety rules, so they are reproduced exactly rather than rewritten:
``quantity`` stays an ``int`` (a bool is rejected explicitly, since
``isinstance(True, int)`` is true in Python) and ``estimated_price`` must be
strictly positive.

``ExecutionFill`` is new.  It is the domain's record of a broker execution,
which is what the execution port will report; it is not the Paper-specific
``PaperExecution`` DTO, which stays where it is for now.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import uuid4

from us_quant.trading.domain.common import ONE, ZERO


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
class OrderEvent:
    order_id: str
    status: OrderStatus
    idempotency_key: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionFill:
    execution_id: str
    order_id: str
    symbol: str
    side: Side
    quantity: int
    price: Decimal
    occurred_at: datetime
