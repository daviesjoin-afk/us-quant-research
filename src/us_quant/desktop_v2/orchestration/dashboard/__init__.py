"""Dashboard orchestration (G1): presentation owner for the Dashboard route.

See the module docstring in ``orchestrator.py`` for what this package is and,
just as importantly, what it is not: no gateway probe, no second copy of any
other capability's canonical state, and no import of any other orchestrator.
"""

from us_quant.desktop_v2.orchestration.dashboard.orchestrator import (
    DashboardOrchestrator,
    DashboardProviders,
    EMPTY_CHART,
)

__all__ = ["DashboardOrchestrator", "DashboardProviders", "EMPTY_CHART"]
