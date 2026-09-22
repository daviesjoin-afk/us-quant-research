"""Cross-section orchestration: the desktop owner of the research report.

The Cross Section workspace's desktop truth used to be two ``MainWindow``
attributes -- ``self.cross_section_report`` and ``self.cross_section_path`` --
read and written by five handlers, with the report schema parsed by hand on
startup.  This class owns them now.

It owns three things and nothing else:

* **the report.**  :attr:`_report` is the last *valid* cross-sectional research
  report.  It is deliberately not published: no other workflow reads a raw
  report today -- the Dashboard sees the refreshed artifact catalogue and the
  Cross Section page sees a projection -- and an accessor added "in case" is an
  accessor the next capability starts using.  ``BacktestOrchestrator`` holds its
  runs privately for the same reason;
* **the research scenario capital.**  Not stored here: this class holds a
  ``ResearchScenarioCapitalState`` and delegates.  The scalar has seven
  consumers across four workspaces, so it is not this workspace's fact, and a
  copy here would be a second truth.  This class owns exactly one thing about
  it -- that a Cross Section run freezes the current value, and that the page is
  the only editor;
* **the one render entry point.**  :meth:`render_current` is the only caller of
  ``CrossSectionResearchPage.render`` in production.

The timing rules are the contract, and the two halves are opposites:

* the universe is checked **now** (so an operator with nothing loaded is told
  immediately) and then read **again inside the task**, so a refresh that landed
  while the run queued is the universe that gets researched.  If it is gone by
  then the task fails closed rather than researching a snapshot the operator can
  no longer see;
* the research capital is frozen **now**, on the UI thread, so editing the
  control while the run is queued cannot change the run about to start.

What it deliberately does not own:

* **the task lifecycle.**  ``TaskThread``, ``DesktopTaskController``, the busy
  dialog, the worker list and the cancellation handling stay on the window.
  This class receives one narrow callable, ``submit_task``, and never learns
  which worker it started;
* **the dialog.**  A refusal is published through :attr:`refused` and the window
  shows it, which is what keeps this module free of widgets;
* **cross-workflow fan-out.**  A successful run rewrites the research artifact
  directory, so the Dashboard's catalogue is stale -- but whether the Dashboard,
  the preflights or anything else reacts is not a cross-section decision.  The
  window subscribes to :attr:`report_changed` and routes it.  Startup
  restoration publishes nothing, because re-reading a local file is not new
  research.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from us_quant.desktop_cross_section_service import (
    DesktopCrossSectionService,
)
from us_quant.desktop_v2.orchestration.research.scenario_capital import (
    ResearchScenarioCapitalState,
)
from us_quant.desktop_v2.orchestration.tasking import TaskSubmitter
from us_quant.desktop_v2.pages.research.cross_section.models import (
    CrossSectionResearchDraft,
)
from us_quant.desktop_v2.pages.research.cross_section.presenter import (
    build_cross_section_view,
)
from us_quant.universe import UniverseSnapshot

#: The resource group that serializes cross-sectional research.  A second
#: concurrent run would rewrite the report artifact while the first is still
#: producing it.
CROSS_SECTION_RESOURCE_GROUP = "strategy"

#: The status line written when the research task is admitted.
CROSS_SECTION_START_MESSAGE = "组合走样本外研究开始…"

#: The progress line the task reports before it starts reading.  The copy is
#: unchanged from the inline handler: it names what the run is comparing, and
#: the operator's expectations of the runtime are calibrated on it.
CROSS_SECTION_PROGRESS_MESSAGE = (
    "正在按整股、组合风险预算、替代品风险倍数和买不起回填规则"
    "比较8组参数；预计需要1–2分钟…"
)

#: The refusal shown when research is requested with no universe loaded.
MISSING_UNIVERSE_TITLE = "缺少标的池"
MISSING_UNIVERSE_MESSAGE = "请先刷新官方标的。"


class CrossSectionOrchestrator(QObject):
    """Owns the cross-section report, the run request and the render."""

    #: The canonical research scenario capital changed because of a Cross
    #: Section edit.  The window routes it; the Account page's presentation of a
    #: scenario number is the one consumer today, and it is not a cross-section
    #: decision.
    capital_changed = Signal(int)

    #: A run succeeded and the research artifact directory was rewritten.  The
    #: window reloads whatever reads that directory.  Startup restoration does
    #: not emit it: nothing changed, the process merely re-read a file.
    report_changed = Signal()

    #: A request was refused before it reached the service.  The window owns the
    #: dialog, so this module stays widget-free.
    refused = Signal(str, str)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    def __init__(
        self,
        *,
        service: DesktopCrossSectionService,
        page: object,
        submit_task: TaskSubmitter,
        universe_provider: Callable[[], UniverseSnapshot | None],
        capital_state: ResearchScenarioCapitalState,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._page = page
        self._submit_task = submit_task
        self._universe_provider = universe_provider
        self._capital_state = capital_state
        self._report: dict | None = None

    # -- capital editing ---------------------------------------------------

    def request_capital_change(self, value: int) -> None:
        """Adopt the page's edited scenario capital.

        The Cross Section page is the only editor of this scalar, so this is the
        whole write path.  Publishing the change is this class's job rather than
        the state object's: the state is a scalar and has no listeners.
        """

        if self._capital_state.set(value):
            self.capital_changed.emit(value)

    # -- request intents ---------------------------------------------------

    def request_run(self, draft: CrossSectionResearchDraft) -> None:
        """Validate, freeze the run inputs, then hand one task to the window.

        The order is the contract: the universe is checked first so an operator
        with nothing loaded is told that rather than having a run silently start
        against nothing, and the capital is adopted before the freeze so the
        value the page shows is the value the run uses.
        """

        if self._universe_provider() is None:
            self.refused.emit(
                MISSING_UNIVERSE_TITLE, MISSING_UNIVERSE_MESSAGE
            )
            return

        self.request_capital_change(draft.research_capital)
        research_capital = self._capital_state.value

        def task(progress: Callable[[str], None]) -> dict:
            progress(CROSS_SECTION_PROGRESS_MESSAGE)
            universe = self._universe_provider()
            if universe is None:
                # Fail closed.  Researching the snapshot captured at request
                # time would silently run against a pool the operator can no
                # longer see, which is worse than a failed task.
                raise RuntimeError(MISSING_UNIVERSE_MESSAGE)
            return self._service.run(
                universe,
                research_capital=research_capital,
            )

        self._submit_task(
            task,
            on_success=self._report_finished,
            start_message=CROSS_SECTION_START_MESSAGE,
            resource_group=CROSS_SECTION_RESOURCE_GROUP,
        )

    # -- lifecycle ---------------------------------------------------------

    def restore_saved(self) -> None:
        """Adopt the report the startup loader read from disk.

        Startup restoration is **not** a new run.  It seeds the truth and paints
        it, and publishes nothing: the Dashboard's catalogue is read from the
        same directory during the same startup pass, so announcing a change here
        would fan out a second time for a file that was merely re-read.

        A malformed artifact leaves the truth empty, still paints exactly once
        and logs -- a corrupt report must not stop the desktop from starting,
        the same tolerance the window had when it parsed the JSON itself.  The
        projection is inside the guard for the same reason: valid JSON can still
        be a partially written or incompatible report.
        """

        try:
            restored = self._service.load_saved()
            view = build_cross_section_view(restored)
        except Exception as error:
            self._report = None
            # Nothing was adopted, so this is the page's first and only paint.
            self.render_current()
            self.log_requested.emit(
                f"风险一致研究产物读取失败："
                f"{type(error).__name__}: {error}"
            )
            return
        self._report = restored
        self._page.render(view)

    # -- rendering ---------------------------------------------------------

    def render_current(self) -> None:
        """Draw the page from this capability's own state.

        It never fetches: the report is whatever the last successful run or
        startup restore committed.  A render that could start a run would be a
        second run path, and this is the only place the page is painted.
        """

        self._page.render(build_cross_section_view(self._report))

    # -- success path ------------------------------------------------------

    def _report_finished(self, result: object) -> None:
        """Publish one successful run: report, page, event, log.

        **Everything the success path can fail on happens before the commit.**
        That is the contract, and it is stronger than "project first": no step
        below the commit line may raise because of the *result*, so a result
        that reached the truth is one the whole success path can finish.

        Three things are prepared up front, in this order:

        * the type check;
        * the projection -- valid JSON can still be a partly written or
          incompatible report, and the page must be able to draw whatever the
          truth ends up holding;
        * the completion message, including its number formatting.

        The message is the step that used to be missing, and its absence was a
        real defect rather than a tidiness issue.  The presenter coerces with
        ``float(...)``, so a report carrying numeric *strings* projects fine --
        and formatting that same string with a raw ``{:+.1%}`` raised
        ``ValueError`` *after* ``_report`` was replaced, the page repainted and
        ``report_changed`` emitted: a "successful" run that moved the truth and
        fired the artifact bridge, with the failure surfacing from a logger.

        A wrong or malformed object therefore fails loudly while the last
        complete report stays exactly where it was, and the page keeps drawing
        it.  That is the same "keep the last good result" rule Account, Universe
        and Backtest follow.
        """

        if not isinstance(result, dict):
            raise TypeError("unexpected cross-sectional research report")

        try:
            view = build_cross_section_view(result)
            strategy = result["out_of_sample"]["strategy"]
            completion_message = (
                f"组合研究完成：OOS "
                f"{float(strategy['total_return']):+.1%}，"
                f"最大回撤 "
                f"{float(strategy['max_drawdown']):.1%}。"
            )
        except (KeyError, TypeError, ValueError, IndexError) as error:
            # The presenter is a pure projection and keeps raising whatever the
            # schema violation was; normalising here means the capability
            # reports one failure rather than leaking the bug's shape, while
            # ``__cause__`` keeps the original for whoever debugs the artifact.
            raise TypeError(
                "unexpected cross-sectional research report"
            ) from error

        # Only past this line may the capability's truth change.
        self._report = result
        self._page.render(view)
        self.report_changed.emit()
        self.log_requested.emit(completion_message)


__all__ = [
    "CROSS_SECTION_PROGRESS_MESSAGE",
    "CROSS_SECTION_RESOURCE_GROUP",
    "CROSS_SECTION_START_MESSAGE",
    "MISSING_UNIVERSE_MESSAGE",
    "MISSING_UNIVERSE_TITLE",
    "CrossSectionOrchestrator",
]
