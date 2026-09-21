"""The account orchestration capability.

``AccountOrchestrator`` owns the desktop account route's runtime: the refresh
request, the ledger append, the page render and the header facts.  It owns
nothing cross-workflow -- the dashboard, the auto-quant and targeted preflights
subscribe to its published facts from the window -- and it owns no account
truth of its own: ``BrokerAccountApplication`` stays canonical, and
``AccountOrchestrator.portfolio`` is a read-only delegation to it.

The Qt-free half lives beside it: :mod:`models` holds the immutable facts the
boundary crosses with, and :mod:`queries` holds the fresh-Paper rule.
"""

from __future__ import annotations

from us_quant.desktop_v2.orchestration.account.models import (
    AccountPresentationInputs,
    AccountRuntimeEvent,
    AccountShellHealthView,
)
from us_quant.desktop_v2.orchestration.account.orchestrator import (
    AccountOrchestrator,
)
from us_quant.desktop_v2.orchestration.account.queries import (
    FRESH_PAPER_MAX_AGE_SECONDS,
    fresh_paper_net_liquidation,
)

__all__ = [
    "AccountOrchestrator",
    "AccountPresentationInputs",
    "AccountRuntimeEvent",
    "AccountShellHealthView",
    "FRESH_PAPER_MAX_AGE_SECONDS",
    "fresh_paper_net_liquidation",
]
