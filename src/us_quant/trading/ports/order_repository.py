"""Order repository port.

The future replacement for the runtime's direct knowledge of
``PaperOrderJournal``.  It captures only what the runtime actually needs --
record an intent, record a status update, record a fill, and look an order up
by id -- rather than mirroring the journal's full surface.

No SQL, no schema and no ``sqlite3`` here.  The current journal keeps its
SQLite implementation untouched; a later change moves it behind this port as
``trading/adapters/sqlite/order_repository.py``.
"""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
    OrderStatus,
)


class OrderRepositoryPort(Protocol):
    def record_intent(self, intent: OrderIntent) -> None: ...

    def record_event(self, event: OrderEvent) -> None: ...

    def record_fill(self, fill: ExecutionFill) -> None: ...

    def status(self, order_id: str) -> OrderStatus | None: ...

    def intent(self, order_id: str) -> OrderIntent | None: ...

    def fills(self, order_id: str) -> tuple[ExecutionFill, ...]: ...
