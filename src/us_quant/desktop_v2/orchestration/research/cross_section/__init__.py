"""The cross-section orchestration capability.

``CrossSectionOrchestrator`` owns the Cross Section route's desktop runtime: the
last valid research report, the research-scenario-capital edit path, the run
request and the one render entry point.  The report lives here and nowhere else
-- there is no ``MainWindow.cross_section_report``, no ``cross_section_path``
and no compatibility property -- and the page is painted only from here.

The executable research procedure and the artifact boundary are in
``DesktopCrossSectionService``; the pure projection rules are in the page's own
``presenter``.  What this package owns is the desktop *timing*: the capital is
frozen at request time, the universe is re-read at execution time, the report is
projected before it is committed, and a failed run keeps the last good report.

This capability is deliberately self-contained: it imports no other Research
workspace, no Dashboard and no artifact catalogue.  It reads the universe
through a ``Callable`` provider rather than a ``UniverseOrchestrator``, so a
change to how the universe is implemented cannot reach it.
"""

from __future__ import annotations

from .orchestrator import (
    CROSS_SECTION_PROGRESS_MESSAGE,
    CROSS_SECTION_RESOURCE_GROUP,
    CROSS_SECTION_START_MESSAGE,
    MISSING_UNIVERSE_MESSAGE,
    MISSING_UNIVERSE_TITLE,
    CrossSectionOrchestrator,
)

__all__ = [
    "CROSS_SECTION_PROGRESS_MESSAGE",
    "CROSS_SECTION_RESOURCE_GROUP",
    "CROSS_SECTION_START_MESSAGE",
    "MISSING_UNIVERSE_MESSAGE",
    "MISSING_UNIVERSE_TITLE",
    "CrossSectionOrchestrator",
]
