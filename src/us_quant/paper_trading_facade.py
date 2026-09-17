"""Transitional boundary between the desktop and Paper trading internals.

The desktop reaches the Paper execution stack through this facade for *reads and
lifecycle only*: the current phase, whether a session is still awaiting
finalization, whether the order channel is connected, immutable lifecycle
snapshots, read-only order status queries, and the existing ``disconnect``
teardown.  Nothing here submits, cancels, replaces, reconciles, resumes, arms a
session, or takes an execution lease -- those paths stay exactly where they are
until a later step moves them on purpose.

Construction takes getters rather than owned objects.  ``MainWindow`` still
creates, arms, and clears ``IBKRPaperOrderService`` and the Paper workflow
controller, and the safety tests replace those two attributes to drive the
halted/refused-close paths.  Reading through a getter keeps this boundary
honest: the facade can never act on a service the window has already replaced
or dropped, and it never becomes a second owner of either object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from .workflow_state import PaperWorkflowPhase


class PaperConnectionPort(Protocol):
    """The connection fact this facade reads; order detail stays in the service."""

    connected: bool


class PaperOrderServicePort(Protocol):
    """The read/lifecycle slice of ``IBKRPaperOrderService`` used here."""

    def connection_snapshot(self) -> PaperConnectionPort: ...
    def broker_state(self) -> object: ...
    def reconciliation_rows_with_latency(
        self, *, session_id: str | None = None, limit: int = 1000
    ) -> Sequence[dict]: ...
    def disconnect(self) -> None: ...


class PaperSessionStatePort(Protocol):
    """The finalized flag carried by a coordinator result."""

    finalized: bool


class PaperSessionResultPort(Protocol):
    """The coordinator result slice this facade reads."""

    state: PaperSessionStatePort


class PaperWorkflowPort(Protocol):
    """The workflow controller slice this facade reads."""

    @property
    def phase(self) -> PaperWorkflowPhase: ...
    @property
    def result(self) -> PaperSessionResultPort | None: ...
    @property
    def reconciliation_evidence(self) -> object | None: ...


OrderServiceGetter = Callable[[], "PaperOrderServicePort | None"]
WorkflowGetter = Callable[[], PaperWorkflowPort]


@dataclass(frozen=True, slots=True)
class PaperTradingSnapshot:
    """One immutable reading of the Paper *application lifecycle*.

    Order truth deliberately stays in the existing service and journal: this
    snapshot never carries positions, orders, fills, or reconciliation rows.
    """

    phase: str
    connected: bool
    finalized: bool
    last_error: str | None


@dataclass(frozen=True, slots=True)
class PaperReconciliationStatus:
    """Whether a one-shot reconciliation proof waits for human confirmation."""

    awaiting_confirmation: bool


class PaperTradingFacade:
    """Read/lifecycle boundary over the existing Paper workflow and order service."""

    def __init__(
        self,
        *,
        workflow_getter: WorkflowGetter,
        order_service_getter: OrderServiceGetter,
    ) -> None:
        self._workflow_getter = workflow_getter
        self._order_service_getter = order_service_getter
        self._last_error: str | None = None

    # -- reads ---------------------------------------------------------

    def phase(self) -> PaperWorkflowPhase:
        """The workflow controller's current phase; never a second copy."""

        return self._workflow_getter().phase

    def is_finalized(self) -> bool:
        """Whether no Paper session is still awaiting finalization.

        The truth comes from the controller's own result, never from a guess
        here.  A window that has not started a session yet also answers ``True``
        -- nothing is outstanding -- which is exactly the close gate's meaning:
        it refuses to close while an unfinalized session still exists.
        """

        result = self._workflow_getter().result
        return result is None or bool(result.state.finalized)

    def has_order_service(self) -> bool:
        """Whether the window currently holds an order service at all."""

        return self._order_service_getter() is not None

    def is_connected(self) -> bool:
        """Whether the held order service reports a live broker connection."""

        service = self._order_service_getter()
        if service is None:
            return False
        return bool(service.connection_snapshot().connected)

    def reconciliation_status(self) -> PaperReconciliationStatus:
        """Whether a reconciliation proof is awaiting explicit confirmation."""

        evidence = self._workflow_getter().reconciliation_evidence
        return PaperReconciliationStatus(awaiting_confirmation=evidence is not None)

    def snapshot(self) -> PaperTradingSnapshot:
        """Derive one immutable lifecycle reading from the live components.

        This is an explicit status query, not a cheap accessor: ``is_connected``
        asks the order service for its connection fact, so do not call it on a
        per-tick render path.
        """

        return PaperTradingSnapshot(
            phase=self.phase().value,
            connected=self.is_connected(),
            finalized=self.is_finalized(),
            last_error=self._last_error,
        )

    # -- read-only order status ----------------------------------------

    def broker_state(self) -> object | None:
        """The service's last broker snapshot, or ``None`` when nothing is held."""

        service = self._order_service_getter()
        if service is None:
            return None
        return service.broker_state()

    def reconciliation_rows_with_latency(
        self, *, session_id: str | None = None, limit: int = 1000
    ) -> Sequence[dict]:
        """Journal rows with submit latency; empty when nothing is held."""

        service = self._order_service_getter()
        if service is None:
            return ()
        return service.reconciliation_rows_with_latency(
            session_id=session_id, limit=limit
        )

    # -- lifecycle -----------------------------------------------------

    def disconnect(self) -> None:
        """Run the existing ``disconnect`` semantics once and record any failure.

        This is the same teardown the window performed inline: no submit, no
        cancel, no reconciliation, no resume, and no lease change.  A failure is
        recorded in ``last_error`` and re-raised unchanged, so the caller keeps
        seeing exactly the exception it saw before and nothing is swallowed.
        """

        service = self._order_service_getter()
        if service is None:
            return
        try:
            service.disconnect()
        except Exception as error:
            self._last_error = f"Paper disconnect failed: {error}"
            raise
        self._last_error = None
