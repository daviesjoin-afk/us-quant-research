"""The backtest workspace run loop, without Qt.

``MainWindow._run_backtest_workspace`` used to run the batch inline inside
its task callback: for each request, report progress, run the backtest, save
the run, collect the result.  This module owns that loop.

The window keeps the Qt half -- the busy / empty / invalid-date dialogs, the
``BacktestRequest`` construction from the form controls, the progress copy,
the ``TaskThread`` and the rendering in ``_backtest_workspace_finished``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from us_quant.backtest_workspace import (
    BacktestRequest,
    BacktestRun,
    run_backtest,
    save_backtest_run,
)

BacktestProgress = Callable[[int, int, BacktestRequest], None]


class DesktopBacktestService:
    """Runs a batch of backtests serially and persists each result.

    The constructor performs no I/O: it only remembers the three roots.
    Reading daily bars, running the engine and writing the run JSON all
    happen inside :meth:`run`.
    """

    def __init__(
        self,
        *,
        data_root: Path,
        fallback_data_root: Path | None,
        output_root: Path,
    ) -> None:
        self.data_root = data_root
        self.fallback_data_root = fallback_data_root
        self.output_root = output_root

    def run(
        self,
        requests: Sequence[BacktestRequest],
        *,
        on_progress: BacktestProgress | None = None,
    ) -> tuple[BacktestRun, ...]:
        """Run every request in order and return the runs.

        Per request the order is ``progress`` -> ``run_backtest`` ->
        ``save_backtest_run`` -> append, so the progress line is emitted
        *before* the request that is about to run, exactly as the old
        closure did.

        Execution is serial and stays inside the caller's thread: there is
        no executor, no asyncio and no cancellation here.

        A failure propagates unchanged.  Whatever was already saved stays
        saved -- this is deliberately a partial-commit batch, not a
        transaction, and nothing is rolled back.  A failed save means that
        request is not appended and the remaining requests do not run.
        """

        runs: list[BacktestRun] = []
        total = len(requests)

        for index, request in enumerate(requests, start=1):
            if on_progress is not None:
                on_progress(index, total, request)

            run = run_backtest(
                request,
                data_root=self.data_root,
                fallback_data_root=self.fallback_data_root,
            )
            save_backtest_run(run, output_root=self.output_root)
            runs.append(run)

        return tuple(runs)
