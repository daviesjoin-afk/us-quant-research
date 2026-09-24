"""The Paper trading application boundary.

``PaperTradingService`` owns one thing: *who currently holds the broker order
connection*.  It builds and connects candidate order services, promotes the one a
validated launch published into the single active slot -- in two phases, a
``reserve_candidate_promotion`` that installs the owner and a commit or cancel that
ends the launch's claim, because a launch has to be able to take its own promotion
back until publication succeeds -- reconnects that service for manual
reconciliation, disconnects it for the zero-state finalization proof, and releases the
slot only after the workflow reports the session finalized, in two phases of the same
shape: a ``reserve_active_release`` that proves and locks the slot before the workflow's
execution-lease gate is asked, and a commit or cancel that ends the release's claim.  It
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
    PaperActiveReleaseReservation,
    PaperPromotionReservation,
    PaperReconciliationStatus,
    PaperTradingLifecycleError,
    PaperTradingSnapshot,
)
from us_quant.trading.application.paper.service import PaperTradingService

__all__ = [
    "PaperActiveReleaseReservation",
    "PaperConnectionPort",
    "PaperOrderServiceFactory",
    "PaperOrderServicePort",
    "PaperPromotionReservation",
    "PaperReconciliationStatus",
    "PaperSessionResultPort",
    "PaperSessionStatePort",
    "PaperTradingLifecycleError",
    "PaperTradingService",
    "PaperTradingSnapshot",
    "PaperWorkflowPort",
    "WorkflowGetter",
]
