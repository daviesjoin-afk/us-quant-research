"""The universe orchestration capability.

``UniverseOrchestrator`` owns the desktop universe route's runtime: the official
snapshot, the refresh request, the cancel ``Event`` and the page render.  The
snapshot is a single owner rather than a copy -- ``DesktopUniverseService`` is a
stateless procedure -- and every other capability reads it through
``UniverseOrchestrator.snapshot`` instead of keeping its own ``self.universe``.

It owns nothing cross-workflow: the market-scope summary, the scanner and the
shadow gate subscribe to its published facts from the window.
"""

from __future__ import annotations

from us_quant.desktop_v2.orchestration.research.universe.orchestrator import (
    CANCEL_MESSAGE,
    REFRESH_RESOURCE_GROUP,
    REFRESH_START_MESSAGE,
    UniverseOrchestrator,
    progress_message,
)

__all__ = [
    "CANCEL_MESSAGE",
    "REFRESH_RESOURCE_GROUP",
    "REFRESH_START_MESSAGE",
    "UniverseOrchestrator",
    "progress_message",
]
