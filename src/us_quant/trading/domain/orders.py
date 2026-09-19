"""Order domain: sides, lifecycle status, intents, events and fills.

These four types are the only order vocabulary the layers above the adapters
know.  ``OrderIntent`` is the single order identity in the tree: a strategy
produces a ``TradeProposal`` and an execution service turns a risk-approved one
into an intent, so nothing on the strategy path can construct an order.

The whole-share and positive-price invariants are load bearing safety rules, so
they are reproduced exactly rather than rewritten: ``quantity`` stays an ``int``
(a bool is rejected explicitly, since ``isinstance(True, int)`` is true in
Python) and ``limit_price`` must be strictly positive.

Two deliberate departures from the provisional ``#17`` shape:

* ``estimated_price`` became ``limit_price``.  The value the engine computed was
  never an estimate -- it is the limit price the order is actually submitted
  with -- and keeping both names would have left two prices free to drift.
* ``ExecutionFill.quantity`` is a ``Decimal``, because it records what the
  broker reported rather than what we hoped for.  A fractional fill is a fact to
  report and halt on, never a number to round down to a whole share.

``OrderEvent`` carries the order-status facts a runtime policy reads as fields
(``filled``, ``remaining``, the two fill prices, the message).  They are
deliberately not stuffed into a free-form ``payload`` dict: a runtime that has
to spell ``payload["filled"]`` is one typo away from reading a missing key as
zero.  The broker's own status text is kept beside the mapped ``OrderStatus``
for diagnostics only.
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

    @property
    def order_text(self) -> str:
        """The uppercase spelling order stores, broker channels and UI use."""

        return self.value.upper()

    @classmethod
    def from_order_text(cls, value: str) -> Side:
        """Read a side written ``buy``/``BUY``; refuse every other spelling."""

        normalized = str(value).strip().casefold()
        try:
            return cls(normalized)
        except ValueError as error:
            raise ValueError(f"unsupported order side: {value!r}") from error


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
    INACTIVE = "inactive"
    BROKER_REJECTED = "broker_rejected"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        """Whether the order can no longer change at the broker.

        ``UNKNOWN`` is deliberately not terminal: an unrecognised status means
        this system does not know what the broker did, and treating "I do not
        know" as "finished" is how an order silently leaves the book.
        """

        return self in _TERMINAL_ORDER_STATUSES


#: The statuses after which no further broker report can change the order.
_TERMINAL_ORDER_STATUSES = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.INACTIVE,
        OrderStatus.BROKER_REJECTED,
    }
)


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """One order, with the identity only an execution service may create.

    The session and strategy-version identities travel with the order because
    the order store has always recorded them and reconciliation reads them back;
    dropping either would have made the durable row unreconstructable.  ``reason``
    is here for the same reason: it is what makes a trimmed order readable as a
    trimmed order at reconciliation time.
    """

    order_id: str
    client_order_id: str
    session_id: str
    strategy_version_id: str
    signal_symbol: str
    execution_symbol: str
    side: Side
    quantity: int
    limit_price: Decimal
    reason: str
    idempotency_key: str
    exposure_multiplier: Decimal = ONE
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        strategy_version_id: str,
        signal_symbol: str,
        execution_symbol: str,
        side: Side,
        quantity: int,
        limit_price: Decimal,
        reason: str,
        exposure_multiplier: Decimal = ONE,
    ) -> OrderIntent:
        order_id = str(uuid4())
        return cls(
            order_id=order_id,
            client_order_id=f"uq-{order_id}",
            session_id=session_id,
            strategy_version_id=strategy_version_id,
            signal_symbol=signal_symbol.strip().upper(),
            execution_symbol=execution_symbol.strip().upper(),
            side=side,
            quantity=quantity,
            limit_price=limit_price,
            reason=reason,
            idempotency_key=uuid4().hex,
            exposure_multiplier=exposure_multiplier,
        )

    def __post_init__(self) -> None:
        if self.quantity <= 0 or isinstance(self.quantity, bool):
            raise ValueError("order quantity must be a positive whole number")
        if self.limit_price <= ZERO:
            raise ValueError("limit price must be positive")
        if self.exposure_multiplier <= ZERO:
            raise ValueError("exposure multiplier must be positive")


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """One broker order-status observation, in provider-neutral form.

    ``broker_order_id`` defaults to ``0`` and ``broker_status`` to ``""``
    because not every order event comes from a broker: the legacy
    ``us_quant.oms`` lifecycle records a ``CREATED`` event before any broker
    identity exists, and forcing a fabricated id there would be worse than an
    explicit "not assigned yet".
    """

    order_id: str
    status: OrderStatus
    #: ``0`` means no broker identity has been assigned yet.
    broker_order_id: int = 0
    #: The channel's own status text, when the event came from one.
    broker_status: str = ""
    filled: Decimal = ZERO
    remaining: Decimal = ZERO
    average_fill_price: Decimal | None = None
    last_fill_price: Decimal | None = None
    message: str = ""
    idempotency_key: str = ""
    occurred_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    #: Only for stores that persist a whole snapshot of the order alongside the
    #: event (the legacy ``us_quant.oms`` event log does).  Policy reads the
    #: fields above; a decision that reached into ``payload["filled"]`` would be
    #: one typo away from reading a missing key as zero.
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionFill:
    """One broker execution, at the precision the broker reported.

    ``quantity`` is deliberately not coerced to ``int``: a 1.5-share fill means
    the broker and this system disagree about the order, and the honest
    recording of that is a fractional quantity that the runtime then fails
    closed on.  Truncating it here would hide the disagreement and leave the
    local book quietly wrong.
    """

    execution_id: str
    order_id: str
    broker_order_id: int
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    occurred_at: datetime