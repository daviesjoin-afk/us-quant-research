"""The history orchestration capability.

``HistoryOrchestrator`` owns the local history route's task intent and progress
presentation.  It owns **no queue**: the job rows, the prioritised order and the
public-source fallback belong to ``DesktopHistoryService`` / ``HistoryJobStore``,
and this class asks the service for a fresh snapshot on every render instead of
caching one.

It reaches the universe only through a ``Callable[[], UniverseSnapshot | None]``
so that the two Research capabilities never import each other.
"""

from __future__ import annotations

from us_quant.desktop_v2.orchestration.research.history.orchestrator import (
    HISTORY_RESOURCE_GROUP,
    IBKR_START_MESSAGE,
    MISSING_UNIVERSE_MESSAGE,
    MISSING_UNIVERSE_TITLE,
    PUBLIC_START_MESSAGE,
    HistoryOrchestrator,
)

__all__ = [
    "HISTORY_RESOURCE_GROUP",
    "IBKR_START_MESSAGE",
    "MISSING_UNIVERSE_MESSAGE",
    "MISSING_UNIVERSE_TITLE",
    "PUBLIC_START_MESSAGE",
    "HistoryOrchestrator",
]
