"""Dashboard orchestration (G1): the owner of the Dashboard page's render.

Dashboard is not a business capability -- it has no command, no transaction and
no canonical business truth.  What it does have is one *presentation* problem
worth owning: the page is a projection of four other capabilities' published
facts plus one retained chart fact, and something has to own that projection and
the single ``render`` call that paints it.

What lives here:

* the retained chart presentation fact (``set_chart`` / :attr:`chart`);
* the projection of portfolio / market snapshot / artifact catalogue / market
  stop reason into one ``DashboardView``;
* the only ``DashboardPage.render`` caller.

What deliberately does not:

* the IBKR gateway probe -- it is a workbench *shell* diagnostic driven by the
  composition root (it reads the global current config and paints a shell
  badge), not a Dashboard fact, so it stays out of this package;
* account, market, research, paper or settings sequencing -- those are read
  here through narrow callables and never imported, so this module cannot grow
  a second owner of any of them;
* any cache of those facts: every ``render_current`` re-reads its providers, so
  a stale portfolio or market snapshot cannot be painted after the source has
  moved on.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from us_quant.desktop_v2.pages.dashboard.models import (
    DashboardChartView,
    DashboardView,
)
from us_quant.desktop_v2.pages.dashboard.presenter import build_dashboard_view

#: The chart fact before anything has been loaded: no symbol, no points.
EMPTY_CHART = DashboardChartView(None, ())


@dataclass(frozen=True, slots=True)
class DashboardProviders:
    """The four published facts the dashboard is projected from.

    Callables, not values: the dashboard is repainted when *another*
    capability's fact changes, so each read must happen at paint time.  Holding
    the portfolio or the snapshot here would be a second copy of canonical
    state that drifts from its owner.
    """

    portfolio: Callable[[], object]
    snapshot: Callable[[], object]
    artifacts: Callable[[], object]
    market_stop_reason: Callable[[], object]


class DashboardOrchestrator:
    """Own the Dashboard page's render and its retained chart fact."""

    def __init__(self, *, page: object, providers: DashboardProviders) -> None:
        # Deliberately not a QObject and no parent: this class emits no signals
        # and owns no Qt lifetime -- the page's lifetime is the composition
        # root's, and a parent parameter here would be a handle nothing uses.
        self._page = page
        self._providers = providers
        self._chart: DashboardChartView = EMPTY_CHART

    # -- read-only fact --------------------------------------------------

    @property
    def chart(self) -> DashboardChartView:
        """The retained chart presentation fact."""

        return self._chart

    # -- rendering -------------------------------------------------------

    def render_current(self) -> None:
        """Project the current published facts onto the page, once.

        Nothing is cached between repaints: the four providers are read here,
        so a repaint always draws the facts their owners have published *now*.
        """

        self._page.render(self.build_view())

    def build_view(self) -> DashboardView:
        """One immutable ``DashboardView`` from the current published facts."""

        return build_dashboard_view(
            portfolio=self._providers.portfolio(),
            snapshot=self._providers.snapshot(),
            artifacts=self._providers.artifacts(),
            chart=self._chart,
            market_stop_reason=self._providers.market_stop_reason(),
        )

    def set_chart(self, chart: DashboardChartView) -> None:
        """Adopt a new chart fact, then repaint the page exactly once.

        The chart is the one *retained* fact this capability owns: a research
        result hands a finished view in, and the repaint follows here rather
        than in the caller, so ``DashboardPage.render`` still has one caller.
        """

        self._chart = chart
        self.render_current()


__all__ = ["DashboardOrchestrator", "DashboardProviders", "EMPTY_CHART"]
