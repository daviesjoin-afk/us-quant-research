"""Broker execution port: order submission and execution reporting.

``submit`` is deliberately two phases.  The broker order id has to be reserved
and written to the durable store *before* anything can reach the broker, so the
port exposes ``reserve`` (allocate the broker id, send nothing) separately from
``submit`` (send it).  The caller that owns persistence -- the execution
application -- writes the correlation in between, which is what keeps a
crash between the two from leaving a live broker order nobody can name.

A single ``submit(intent)`` could not express that order, and collapsing it into
one call would have moved the durable write to after the send: exactly the
failure the two-phase contract exists to prevent.

The errors are provider-neutral on purpose.  Every hard execution safety gate --
session not armed, account binding changed, incomplete connection snapshot,
symbol outside the execution allowlist, oversized notional, oversell, routing
closed, channel down -- crosses this boundary as :class:`ExecutionRefused`, and
the one case that cannot be resolved locally arrives as
:class:`ExecutionSubmissionUncertain`.  No upper layer ever sees a broker's own
exception type, which is what lets the strategy runtime stay vendor-blind.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
)


class ExecutionRefused(RuntimeError):
    """The execution channel refused the order before it could be sent."""


class ExecutionSubmissionUncertain(ExecutionRefused):
    """Submission may have reached the broker and requires reconciliation.

    Both identities travel with the error so the caller can keep the order in
    its pending book and name it to a human.  Retrying is never correct: the
    order may already be live, and a second attempt is how one uncertain
    submission becomes two broker orders.

    ``intent`` is the order the caller asked for, when the raising layer knows
    it.  It is carried so the runtime can keep that order in its pending book
    instead of forgetting an order the broker may be holding.
    """

    def __init__(
        self,
        message: str,
        *,
        order_id: str,
        broker_order_id: int,
        intent: OrderIntent | None = None,
    ) -> None:
        super().__init__(message)
        self.order_id = order_id
        self.broker_order_id = broker_order_id
        self.intent = intent


@dataclass(frozen=True, slots=True)
class BrokerOrderReservation:
    """A broker order id reserved for one intent, before anything is sent.

    ``broker_order_id`` is an ``int`` because every consumer below this boundary
    is integral -- the order store's column, the reconciliation comparison and
    the gateway's order-id counter -- so a string form would add a conversion
    whose only possible outcomes were "lossless" or "silently wrong".
    """

    order_id: str
    broker_order_id: int
    account_alias: str


class BrokerExecutionPort(Protocol):
    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def reserve(self, intent: OrderIntent) -> BrokerOrderReservation: ...

    def submit(self, reservation: BrokerOrderReservation) -> None: ...

    def cancel(self, order_id: str) -> None: ...

    def events(self) -> tuple[OrderEvent, ...]: ...

    def fills(self) -> tuple[ExecutionFill, ...]: ...