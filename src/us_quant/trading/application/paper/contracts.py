"""The port contracts ``PaperTradingService`` reads and calls through.

Every one of these is a *slice*: the service asks for the smallest surface it
needs from a collaborator, so the collaborator can be a real IBKR order service,
a test double, or something not written yet.  None of them names a concrete
adapter, a store, a widget toolkit or the desktop, which is what keeps this
package provider-neutral.

``PaperOrderServiceFactory`` is the one that is injected rather than found: its
default is the execution composition root, so this package never learns which
concrete adapter or order store is in use.
"""

from __future__ import annotations

from typing import Callable, Protocol, Sequence

from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase


class PaperConnectionPort(Protocol):
    """The connection fact this service reads; order detail stays in the service."""

    connected: bool


class PaperOrderServicePort(Protocol):
    """The read/lifecycle slice of ``IBKRPaperOrderService`` used here."""

    def connect(self) -> object: ...
    def connection_snapshot(self) -> PaperConnectionPort: ...
    def broker_state(self) -> object: ...
    def disconnect(self) -> None: ...
    def reconciliation_rows_with_latency(
        self, *, session_id: str | None = None, limit: int = 1000
    ) -> Sequence[dict]: ...


class PaperOrderServiceFactory(Protocol):
    """How this service builds an execution channel; injectable for tests.

    The default is the execution composition root, so the window never names
    the concrete IBKR adapter or the concrete order store.
    """

    def __call__(
        self,
        config: object,
        *,
        repository: object,
        extended_hours_enabled: bool,
    ) -> PaperOrderServicePort: ...


class PaperSessionStatePort(Protocol):
    """The finalized flag carried by a coordinator result."""

    finalized: bool


class PaperSessionResultPort(Protocol):
    """The coordinator result slice this service reads."""

    state: PaperSessionStatePort


class PaperWorkflowPort(Protocol):
    """The workflow controller slice this service reads."""

    @property
    def phase(self) -> PaperWorkflowPhase: ...
    @property
    def result(self) -> PaperSessionResultPort | None: ...
    @property
    def reconciliation_evidence(self) -> object | None: ...


WorkflowGetter = Callable[[], PaperWorkflowPort]


__all__ = [
    "PaperConnectionPort",
    "PaperOrderServiceFactory",
    "PaperOrderServicePort",
    "PaperSessionResultPort",
    "PaperSessionStatePort",
    "PaperWorkflowPort",
    "WorkflowGetter",
]
