"""Paper launch orchestration -- the owner of starting a Paper AutoQuant session.

One capability lives here: :class:`PaperOrchestrator`, which sequences READY ->
CONNECTING -> validated -> built -> armed -> published -> promoted -> RUNNING.  It
owns no phase, no plan, no lease and no broker connection of its own -- it asks
``PaperWorkflowController`` for transitions, ``PaperTradingService`` for a candidate,
and the injected build seam for the runtime.  Everything after ``RUNNING`` -- polling,
stream ingress, pause/resume, an orderly stop, HALT recovery, manual reconciliation and
finalization -- is v2O-E2/E3 and is not named here.

The module is the only published entry point; ``models``, ``queries`` and
``orchestrator`` are its implementation and are imported by path, so this is a stable
name rather than a re-export surface.
"""

from __future__ import annotations

from us_quant.desktop_v2.orchestration.paper.orchestrator import PaperOrchestrator

__all__ = ["PaperOrchestrator"]
