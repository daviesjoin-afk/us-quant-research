"""Backtest orchestration: the desktop owner of this session's backtest runs.

The Backtest workspace's desktop truth used to be three attributes on
``MainWindow`` -- ``self.backtest_runs``, ``self._selected_backtest_run_id`` and
``self._backtest_busy`` -- written and read by eight handlers spread over a
thousand lines of the window.  This class owns them now, and owns them *only*:

* **the runs.**  :attr:`_runs` is the current session's backtest truth.  It is
  deliberately not published: no other workflow reads a ``BacktestRun`` list
  today, and an accessor added "in case" is an accessor the next capability
  will start using, at which point the backtest workspace is no longer
  self-contained.  The page is not the truth either -- it is painted from here;
* **the selection intent.**  The comparison table's selected row is an intent
  (``select_run``), and it resolves against the runs this class holds.  A run id
  that no longer exists simply selects nothing rather than erroring: the
  presenter already falls back to the first run;
* **the busy flag.**  ``_busy`` gates the run buttons and is cleared by exactly
  three events -- admission refused, task failed, task succeeded.  It is *not*
  derived from a worker list, and nothing about an unrelated worker finishing
  can clear it, which is what the retired ``_worker_finished`` coupling got
  wrong.

What it deliberately does not own:

* **the task lifecycle.**  ``TaskThread``, ``DesktopTaskController``, the worker
  list, the closing admission gate and the cancellation handling stay on the
  window.  This class receives one narrow callable, ``submit_task``, and never
  learns which worker it started;
* **the dialog.**  A refusal is published through :attr:`refused` with its
  severity, and the window shows it, which is what keeps this module free of
  widgets;
* **the strategy catalogue.**  It takes a
  ``Callable[[], tuple[StrategyVersion, ...]]`` rather than a
  ``StrategySelectionService``, so a future change to how strategy application
  works does not reach this module at all.  What the provider returns is already
  filtered and ordered; this class only decides what a click means;
* **the domain run loop.**  ``DesktopBacktestService`` owns progress, the
  per-request run/save order and the partial-commit semantics, and this round
  does not touch any of it.  This class builds requests and reports what came
  back.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from us_quant.backtest_workspace import BacktestRun
from us_quant.desktop_backtest_service import DesktopBacktestService
from us_quant.desktop_v2.orchestration.research.backtest.queries import (
    build_backtest_requests,
    select_backtest_versions,
    strategy_options,
)
from us_quant.desktop_v2.orchestration.tasking import (
    TaskAdmissionQuery,
    TaskSubmitter,
)
from us_quant.desktop_v2.pages.research.backtest.models import (
    BacktestFormDraft,
)
from us_quant.desktop_v2.pages.research.backtest.presenter import (
    build_backtest_view,
)
from us_quant.trading.domain.strategy import StrategyVersion

#: The resource group that serializes backtest batches.  A second concurrent
#: batch would interleave run JSON in the user's research output directory.
BACKTEST_RESOURCE_GROUP = "backtest"

#: The three refusal severities, spelled as the ``QMessageBox`` static methods
#: the window calls.  Keeping the vocabulary here means the window's handler is
#: a two-line dispatch and never has to interpret a title.
REFUSAL_INFORMATION = "information"
REFUSAL_WARNING = "warning"

#: The refusal shown when a backtest resource group is already busy.
BUSY_TITLE = "任务忙"
BUSY_MESSAGE = "请等待当前数据或研究任务完成后再运行回测。"

#: The refusal shown when the selected purpose has no runnable version.
NO_STRATEGY_TITLE = "没有可运行版本"
NO_STRATEGY_MESSAGE = "策略目录中没有与回测工厂匹配的研究版本。"

#: The refusal shown when the draft's date range is inverted.
INVALID_DATE_TITLE = "日期无效"
INVALID_DATE_MESSAGE = "起始日期不能晚于结束日期。"


class BacktestOrchestrator(QObject):
    """Owns this session's backtest runs, the selection and the busy state."""

    #: A request was refused before it reached the service.  The window owns
    #: the dialog, so this module stays widget-free.  The severity travels with
    #: the message because busy is an ``information`` and the two validation
    #: refusals are ``warning`` -- a distinction the operator sees.
    refused = Signal(str, str, str)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    def __init__(
        self,
        *,
        service: DesktopBacktestService,
        page: object,
        submit_task: TaskSubmitter,
        task_available: TaskAdmissionQuery,
        strategy_versions_provider: Callable[
            [], tuple[StrategyVersion, ...]
        ],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._page = page
        self._submit_task = submit_task
        self._task_available = task_available
        self._strategy_versions_provider = strategy_versions_provider
        self._runs: tuple[BacktestRun, ...] = ()
        self._selected_run_id: str | None = None
        self._busy = False

    # -- strategy options --------------------------------------------------

    def refresh_strategy_options(self) -> None:
        """Repoint the page's strategy combo at the current catalogue.

        The catalogue is a provider rather than a service, so this is the one
        place that knows both what the page wants and where the versions come
        from.  Called once during composition and again whenever the catalogue
        changes; the window never recomputes the option order itself.
        """

        versions = self._strategy_versions_provider()
        self._page.set_strategy_options(strategy_options(versions))

    # -- request intents ---------------------------------------------------

    def request_selected(self, draft: BacktestFormDraft) -> None:
        """Run the one strategy version the draft names."""

        self._request(draft, compare_all=False)

    def request_compare_all(self, draft: BacktestFormDraft) -> None:
        """Run the newest version of every strategy family, for comparison."""

        self._request(draft, compare_all=True)

    def select_run(self, run_id: str) -> None:
        """Point the detail panel at one run.

        Selection is an intent, not a fact: an id that no longer matches a run
        is recorded as-is and the presenter falls back to the first run, so a
        stale click cannot leave the page without a detail panel.
        """

        self._selected_run_id = run_id
        self.render_current()

    # -- rendering ---------------------------------------------------------

    def render_current(self) -> None:
        """Draw the page from this capability's own state.

        It never fetches: the runs are whatever the last successful batch
        committed.  A render that could start a run would be a second run path,
        and this is the only place ``BacktestPage.render`` is called.
        """

        self._page.render(
            build_backtest_view(
                self._runs,
                self._selected_run_id,
                busy=self._busy,
            )
        )

    # -- the request path --------------------------------------------------

    def _request(
        self,
        draft: BacktestFormDraft,
        *,
        compare_all: bool,
    ) -> None:
        """Validate, freeze the requests, then hand one task to the window.

        The refusal order is the contract and is unchanged from the inline
        handler: busy is checked **first**, before the version and date checks,
        so an operator whose task is already running is told that rather than
        being sent to fix a form that was never read.
        """

        if not self._task_available(BACKTEST_RESOURCE_GROUP):
            self._refuse(REFUSAL_INFORMATION, BUSY_TITLE, BUSY_MESSAGE)
            return

        versions = select_backtest_versions(
            self._strategy_versions_provider(),
            compare_all=compare_all,
            selected_version_id=draft.strategy_version_id,
        )
        if not versions:
            self._refuse(
                REFUSAL_WARNING,
                NO_STRATEGY_TITLE,
                NO_STRATEGY_MESSAGE,
            )
            return
        if draft.start_date > draft.end_date:
            self._refuse(
                REFUSAL_WARNING,
                INVALID_DATE_TITLE,
                INVALID_DATE_MESSAGE,
            )
            return

        requests = build_backtest_requests(versions, draft)

        def task(progress: Callable[[str], None]) -> tuple[BacktestRun, ...]:
            return self._service.run(
                requests,
                on_progress=lambda index, total, request: progress(
                    f"回测 {index}/{total}："
                    f"{request.strategy_id} {request.symbol}"
                ),
            )

        self._busy = True
        self.render_current()
        started = self._submit_task(
            task,
            on_success=self._runs_finished,
            on_failure=self._runs_failed,
            start_message=f"正在运行 {len(requests)} 个版本绑定回测…",
            resource_group=BACKTEST_RESOURCE_GROUP,
        )
        if not started:
            # Admission was refused: the task never ran, so the flag this class
            # optimistically set has to come back down.  This is a different
            # event from a task that started and failed.
            self._busy = False
            self.render_current()

    def _refuse(self, level: str, title: str, message: str) -> None:
        self.refused.emit(level, title, message)

    # -- outcome paths -----------------------------------------------------

    def _runs_failed(self, _message: str) -> None:
        """Release the busy flag and keep the last complete result.

        The runs are deliberately left alone.  A failed batch is not evidence
        that the previous one was wrong, and clearing the table would destroy
        something the operator can still read -- the same "keep the last good
        result" rule Account and Universe follow.
        """

        self._busy = False
        self.render_current()

    def _runs_finished(self, result: object) -> None:
        """Publish one successful batch: runs, selection, page, log.

        The result is checked rather than coerced.  ``list(result)`` on an
        arbitrary object would put whatever the service returned into the page
        state, and a wrong object in the table is far harder to diagnose than a
        loud failure here.

        The busy flag is released **before** the check, and that order is the
        contract rather than an accident.  This handler runs as the worker's
        ``succeeded`` slot, so an exception here propagates out of the signal
        emission: the generic cleanup that follows releases the *worker*, not
        this capability's presentation state.  Raising first would therefore
        leave ``_busy`` set forever, with the run buttons disabled and no task
        left to clear them -- a wrong result would permanently wedge the page.

        So the three requirements are met in this order: fail loudly (the
        ``TypeError`` still propagates), keep the last good result (``_runs``
        and the selection are untouched on the invalid path, and the page is
        repainted from them), and stay operable (the controls come back).  The
        bad object never reaches the truth.
        """

        self._busy = False

        if not isinstance(result, (tuple, list)) or not all(
            isinstance(run, BacktestRun) for run in result
        ):
            self.render_current()
            raise TypeError("unexpected backtest run batch")

        self._runs = tuple(result)
        self._selected_run_id = self._runs[0].run_id if self._runs else None
        self.render_current()
        self.log_requested.emit(
            f"回测完成：{len(self._runs)} 个不可变 run 已保存到用户研究目录"
        )


__all__ = [
    "BACKTEST_RESOURCE_GROUP",
    "BUSY_MESSAGE",
    "BUSY_TITLE",
    "INVALID_DATE_MESSAGE",
    "INVALID_DATE_TITLE",
    "NO_STRATEGY_MESSAGE",
    "NO_STRATEGY_TITLE",
    "REFUSAL_INFORMATION",
    "REFUSAL_WARNING",
    "BacktestOrchestrator",
]
