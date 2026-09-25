"""The strategy governance orchestration capability (G2-A).

``StrategyGovernanceOrchestrator`` owns the desktop strategy governance
route's sequencing: the catalogue read that repaints the page, the clone, the
lifecycle transition, the account-notice text for the version being viewed,
and the ``STATUS_CHANGE`` runtime event.  It owns no truth of its own --
``StrategyApplication`` stays the catalogue authority and this package caches
nothing -- and it never touches the runtime selection service: viewing a
version is not running it.

The Qt-free half lives beside it: :mod:`models` holds the immutable runtime
event the window routes, and :mod:`queries` holds the clone-parameter adapter
and the evidence-notice wording.
"""

from __future__ import annotations

from us_quant.desktop_v2.orchestration.strategy.models import (
    StrategyRuntimeEvent,
)
from us_quant.desktop_v2.orchestration.strategy.orchestrator import (
    StrategyGovernanceOrchestrator,
)
from us_quant.desktop_v2.orchestration.strategy.queries import (
    parse_clone_parameters,
    strategy_account_notice,
)

__all__ = [
    "StrategyGovernanceOrchestrator",
    "StrategyRuntimeEvent",
    "parse_clone_parameters",
    "strategy_account_notice",
]
