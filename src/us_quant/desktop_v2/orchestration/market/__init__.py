"""The market orchestration capability.

``MarketOrchestrator`` owns the market runtime -- the ``StreamWorker``, the
snapshot, the poll timer, the pending provider switch, the recently-ready cache
and the render of the market page.  It owns nothing cross-workflow: the Paper
and Shadow interlocks around a stop or a switch stay on the window, and the
shell badges are published as facts rather than painted here.
"""

from __future__ import annotations

from us_quant.desktop_v2.orchestration.market.models import (
    MarketReadinessInputs,
    MarketRuntimeEvent,
    MarketShellHealthView,
)
from us_quant.desktop_v2.orchestration.market.orchestrator import (
    MarketOrchestrator,
)

__all__ = [
    "MarketOrchestrator",
    "MarketReadinessInputs",
    "MarketRuntimeEvent",
    "MarketShellHealthView",
]
