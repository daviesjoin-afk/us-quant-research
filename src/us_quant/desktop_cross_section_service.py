"""The cross-sectional research procedure and its artifact boundary, without Qt.

``MainWindow._run_cross_section_research`` used to build the research config,
call the executor, write the report and parse the saved report on startup -- all
inline in the window.  This module owns that, and owns it the same way
``DesktopMarketScanService`` and ``DesktopBacktestService`` own theirs: the
window keeps the Qt half (the missing-universe dialog, the progress copy, the
``TaskThread``) and the capability that owns the report truth keeps the
rendering.

Two timing rules are part of the contract, and they are opposites:

* **the config is read when the task executes.**  The window used to read
  ``self.config`` inside its task closure for exactly this reason: a settings
  change that landed while the run was queued is the config the run should use.
  So the dependency is a *provider* and :meth:`run` calls it, rather than the
  constructor capturing a snapshot that would go stale at startup;
* **the research capital is frozen before the task is submitted**, by the
  caller.  :meth:`run` receives a finished ``int`` and converts it once, so the
  value the operator saw when they clicked is the value that runs.

What is deliberately *not* here: the universe.  The caller re-reads it at
execution time and passes the snapshot in, so this boundary cannot accidentally
hold a request-time universe a refresh has since replaced.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from us_quant.config import AppConfig
from us_quant.executable_research import (
    load_executable_research,
    run_executable_cross_sectional_research,
    save_executable_research,
)
from us_quant.universe import UniverseSnapshot


class DesktopCrossSectionService:
    """Runs the cross-sectional research and reads its artifact back.

    The constructor performs no I/O and reads no config: it remembers the
    data roots, the report path and the config provider.  Which matters because
    calling the provider here would capture the start-up config, and the whole
    point of the provider is that the run reads the config that is current when
    it executes.
    """

    def __init__(
        self,
        *,
        config_provider: Callable[[], AppConfig],
        report_path: Path,
        data_root: Path,
        fallback_data_root: Path | None,
    ) -> None:
        self._config_provider = config_provider
        self._report_path = report_path
        self._data_root = data_root
        self._fallback_data_root = fallback_data_root

    def run(
        self,
        universe: UniverseSnapshot,
        *,
        research_capital: int,
    ) -> dict:
        """Research ``universe`` at ``research_capital`` and save the report.

        The order is ``replace(config)`` -> ``run`` -> ``save`` -> return, and
        it is unchanged from the inline window handler.  A failing run must not
        write anything, and a failing save must not report success: both
        exceptions propagate unchanged, which keeps the old
        ``TaskThread.failed`` behaviour and leaves the previous report on disk
        intact.

        ``research_capital`` is an ``int`` and becomes an exact ``Decimal``: the
        page's control and the Account card both present whole dollars, and a
        float round-trip here would be a silent precision change in a research
        artifact.
        """

        config = self._config_provider()
        research_config = replace(
            config,
            initial_equity=Decimal(research_capital),
        )
        result = run_executable_cross_sectional_research(
            research_config,
            universe,
            data_root=self._data_root,
            fallback_data_root=self._fallback_data_root,
        )
        save_executable_research(result, self._report_path)
        return result

    def load_saved(self) -> dict | None:
        """Re-read the report this service wrote, or ``None``.

        Three outcomes, and they are deliberately different:

        * the file is absent -> ``None``.  No research has been run yet, which
          is the ordinary first-run state rather than an error;
        * the file parses to an object -> the raw report.  Projecting it into a
          page view is the caller's business;
        * malformed JSON, or a non-object top level -> the exception
          propagates.  Startup recovery is the capability's decision, and it
          cannot make one if this boundary swallows the failure.
        """

        if not self._report_path.exists():
            return None
        return load_executable_research(self._report_path)


__all__ = ["DesktopCrossSectionService"]
