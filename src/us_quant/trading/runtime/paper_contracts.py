"""Provider-neutral contracts for the Paper session runtime.

The Paper session coordinator speaks only to these Protocols: an engine that
owns local trading state -- ``TradingRuntime`` satisfies it -- and an order port
that owns one already-armed broker session.  Nothing here may name a concrete
adapter, store or widget: a concrete type in this file would let the sequencing
logic depend on a provider, which is exactly the coupling the ports exist to
prevent.

``PaperOrderPort`` deliberately keeps the event reads on the same object as the
connection, account and reconciliation reads.  They describe the *session* the
events belong to, and splitting them would let the coordinator compare one
channel's events against another channel's snapshot.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol, Sequence

from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    Side,
)


class PendingOrder(Protocol):
    """One local pending order, as the coordinator reads it for timeouts."""

    order_id: str
    side: Side
    created_at: datetime


class EngineSnapshot(Protocol):
    """The engine's local execution state at one instant."""

    session_id: str | None
    active: bool
    positions: Sequence[object]
    pending_orders: Sequence[PendingOrder]
    entries_paused: bool
    stop_requested: bool


class PaperEngine(Protocol):
    """The session the coordinator sequences."""

    config: object

    def snapshot(self, *, observed_at: datetime | None = None) -> EngineSnapshot: ...
    def on_stream(self, snapshot: object, *, observed_at: datetime | None = None) -> EngineSnapshot: ...
    def on_execution(self, execution: ExecutionFill) -> EngineSnapshot: ...
    def on_order_event(self, event: OrderEvent) -> EngineSnapshot: ...
    def pause_entries(self) -> EngineSnapshot: ...
    def resume_entries(self) -> EngineSnapshot: ...
    def request_stop(self) -> EngineSnapshot: ...
    def halt_for_reconciliation(self, reason: str) -> EngineSnapshot: ...
    def resume_from_reconciliation(
        self, *, session_id: str, allow_force_flat_exit: bool = True
    ) -> EngineSnapshot: ...


class PaperOrderPort(Protocol):
    """The provider-neutral execution surface this coordinator sequences."""

    def fills(self) -> Sequence[ExecutionFill]: ...
    def events(self) -> Sequence[OrderEvent]: ...
    def cancel(self, order_id: str) -> bool: ...
    def connection_snapshot(self) -> object: ...
    def broker_state(self) -> object: ...
    def reconciliation_rows_with_latency(
        self, *, session_id: str | None, limit: int
    ) -> Sequence[object]: ...
    def reconciliation_summary(self, session_id: str) -> object: ...
    def refresh_reconciliation_snapshot(self, session_id: str) -> object: ...
    def reconciliation_snapshot_is_current(self, snapshot: object) -> bool: ...

    def armed_account_binding_is_valid(self) -> bool: ...
    def armed_account_fingerprint(self) -> str: ...


class PaperHealth(Protocol):
    """The injected verdict on whether the session may keep trading."""

    safe_to_continue: bool
    status: str


HealthEvaluator = Callable[..., PaperHealth]
Clock = Callable[[], datetime]