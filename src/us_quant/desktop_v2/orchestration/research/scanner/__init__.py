"""The scanner orchestration capability.

``ScannerOrchestrator`` owns the desktop Scanner route's runtime: the canonical
``MarketScan``, the manual scan request, the startup restore, the cross-workflow
adoption of a scan another workflow produced, and the chart read.  The scan is a
single owner rather than a copy -- ``DesktopMarketScanService`` is a stateless
procedure -- and every other capability reads it through
``ScannerOrchestrator.scan`` instead of keeping its own ``self.scan``.

It owns nothing cross-workflow.  The intraday watchlist, the market-scope
summary and the AutoQuant candidate selection stay on the window, because each
of them combines the scan with market, account or Paper facts that Scanner must
not know about; AutoQuant only *publishes* its finished scan into this
capability.
"""

from __future__ import annotations

from us_quant.desktop_v2.orchestration.research.scanner.models import (
    ScannerRunInputs,
)
from us_quant.desktop_v2.orchestration.research.scanner.orchestrator import (
    MISSING_UNIVERSE_MESSAGE,
    MISSING_UNIVERSE_TITLE,
    SCAN_PROGRESS_MESSAGE,
    SCAN_RESOURCE_GROUP,
    SCAN_START_MESSAGE,
    ScannerOrchestrator,
)

__all__ = [
    "MISSING_UNIVERSE_MESSAGE",
    "MISSING_UNIVERSE_TITLE",
    "SCAN_PROGRESS_MESSAGE",
    "SCAN_RESOURCE_GROUP",
    "SCAN_START_MESSAGE",
    "ScannerOrchestrator",
    "ScannerRunInputs",
]
