"""Order repository port.

The store the execution application writes its durable correlation into.  It
carries only what the runtime actually needs -- record an intent, record a
status event, record a fill, and read an order back -- rather than mirroring the
order store's full query surface.  Reconciliation reports, session rollups and
the audit views stay on the concrete store, where the runtime that displays them
can reach them without this port growing a second reason to change.

No SQL, no schema and no ``sqlite3`` here.

``record_intent`` takes the broker identity as arguments instead of reading it
from the intent: the broker order id is the *broker's* fact about the order, and
putting it on ``OrderIntent`` would make the domain type a record of a
submission that has not happened yet.  The same applies to the account alias,
whose only purpose is to make the stored row attributable to the account it was
sent from.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
    OrderStatus,
)


class OrderRepositoryPort(Protocol):
    def record_intent(
        self,
        intent: OrderIntent,
        *,
        broker_order_id: int,
        account_alias: str,
    ) -> None: ...

    def record_event(self, event: OrderEvent) -> None: ...

    def record_fill(self, fill: ExecutionFill) -> bool:
        """Record one execution; ``True`` when it was not already stored.

        The return value is not decoration: the broker can replay an execution
        report, and the caller must be able to tell a fresh fill from a repeat
        before it touches positions or sellable quantities.
        """

        ...

    def status(self, order_id: str) -> OrderStatus | None: ...

    def intent(self, order_id: str) -> OrderIntent | None: ...

    def broker_order_id(self, order_id: str) -> int | None:
        """The broker's id for a stored order, or ``None`` when unstored."""

        ...

    def fills(self, order_id: str) -> tuple[ExecutionFill, ...]: ...

    def intent_for_idempotency_key(
        self, idempotency_key: str
    ) -> OrderIntent | None: ...

    def executed_quantity(self, order_id: str) -> Decimal: ...

    def max_broker_order_id(self) -> int:
        """The highest broker order id ever recorded.

        The floor for the next reserved id, so a gateway restart can never
        regress the counter into reusing an id a stored order already owns.
        """

        ...