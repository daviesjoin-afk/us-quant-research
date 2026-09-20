"""The Paper trading application boundary.

``PaperTradingService`` owns one thing: *who currently holds the broker order
connection*.  It builds and connects candidate order services, promotes the one a
validated launch published into the single active slot, reconnects that service
for manual reconciliation, disconnects it for the zero-state finalization proof,
and clears the slot only after the workflow reports the session finalized.  It
owns no trading semantics.

Split by responsibility so the ports, the data shapes and the lifecycle
behaviour can each be read on their own; the three modules import only downward.
"""

from __future__ import annotations

from us_quant.trading.application.paper.contracts import (
    PaperConnectionPort,
    PaperOrderServiceFactory,
    PaperOrderServicePort,
    PaperSessionResultPort,
    PaperSessionStatePort,
    PaperWorkflowPort,
    WorkflowGetter,
)
from us_quant.trading.application.paper.models import (
    PaperReconciliationStatus,
    PaperTradingLifecycleError,
    PaperTradingSnapshot,
)
from us_quant.trading.application.paper.service import PaperTradingService

__all__ = [
    "PaperConnectionPort",
    "PaperOrderServiceFactory",
    "PaperOrderServicePort",
    "PaperReconciliationStatus",
    "PaperSessionResultPort",
    "PaperSessionStatePort",
    "PaperTradingLifecycleError",
    "PaperTradingService",
    "PaperTradingSnapshot",
    "PaperWorkflowPort",
    "WorkflowGetter",
]
