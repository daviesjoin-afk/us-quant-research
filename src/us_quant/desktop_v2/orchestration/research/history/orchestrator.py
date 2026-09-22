"""History orchestration: task intent and progress for the local history queue.

History is *not* structured like Universe.  The canonical queue truth already
lives in ``DesktopHistoryService`` / ``HistoryJobStore`` -- the service owns the
job rows, the prioritised symbol order, the public-source fallback and the
"put the failures back" rule.  This class therefore owns no queue at all:

* **it stores no jobs.**  There is no ``_jobs``, no ``_queue`` and no cached
  ``HistoryQueueSnapshot``.  :meth:`render_current` asks the service for a fresh
  snapshot on every call, so a job that moved while the desktop was busy can
  never be displayed from a stale mirror;
* **it does not restate the service's business rules.**  Which symbols are
  prioritised, what ``reset_failed`` means and when the public source is the
  fallback are all decided below this boundary.

What it does own is the part that is genuinely desktop presentation and task
intent: the four page intents, the progress percentage shown next to them, and
the single :meth:`render_current` call site that paints the queue.

Two dependencies are callables rather than objects, and that is the point:

* ``universe_provider`` returns the snapshot to schedule against.  History must
  not import ``UniverseOrchestrator`` -- the universe implementation will keep
  changing, and a History that depended on it would have to change with it.  A
  ``Callable[[], UniverseSnapshot | None]`` is the stable fact-shaped boundary;
* ``ibkr_config_provider`` is read on **every** IBKR run.  Capturing the config
  once at construction would mean that changing the connection parameters in
  Settings leaves History talking to the old endpoint for the rest of the
  session.

A missing universe is refused rather than dialled: :attr:`refused` carries the
title and message and the window shows the dialog, matching the Market route's
refusal boundary.  This module never imports a widget.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from us_quant.desktop_history_service import DesktopHistoryService
from us_quant.desktop_v2.orchestration.tasking import TaskSubmitter
from us_quant.desktop_v2.pages.research.history.presenter import (
    build_history_view,
)
from us_quant.ibkr import IBKRConnectionConfig
from us_quant.universe import UniverseSnapshot

#: The resource group that serializes history downloads; two batches would write
#: the same day-K files concurrently.
HISTORY_RESOURCE_GROUP = "history"

#: The status line written when an IBKR download batch is admitted.
IBKR_START_MESSAGE = "IBKR 历史日 K 下载中…"

#: The status line written when the public fallback batch is admitted.
PUBLIC_START_MESSAGE = (
    "备用免费日 K 下载中；只用于历史研究，"
    "不会替代 IBKR 实时行情…"
)

#: The refusal shown when a schedule is requested with no universe loaded.
MISSING_UNIVERSE_TITLE = "缺少标的池"
MISSING_UNIVERSE_MESSAGE = "请先刷新官方标的。"


class HistoryOrchestrator(QObject):
    """Owns the history queue's task intent and progress presentation."""

    #: The queue changed in a way the rest of the desktop should notice.  The
    #: window routes it; what else cares is not a history decision.
    history_changed = Signal()

    #: A request was refused before it reached the service.  The window owns
    #: the dialog, so this module stays widget-free.
    refused = Signal(str, str)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    def __init__(
        self,
        *,
        service: DesktopHistoryService,
        page: object,
        submit_task: TaskSubmitter,
        universe_provider: Callable[[], UniverseSnapshot | None],
        ibkr_config_provider: Callable[[], IBKRConnectionConfig],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._page = page
        self._submit_task = submit_task
        self._universe_provider = universe_provider
        self._ibkr_config_provider = ibkr_config_provider
        #: Desktop presentation state, not queue truth: the queue itself lives
        #: in the store, and this is only the bar the page draws.
        self._progress_percent = 0

    # -- page intents -----------------------------------------------------

    def request_schedule(self) -> None:
        """Add the whole research pool to the download queue."""

        universe = self._universe_provider()
        if universe is None:
            self.refused.emit(
                MISSING_UNIVERSE_TITLE, MISSING_UNIVERSE_MESSAGE
            )
            return
        result = self._service.schedule_universe(universe)
        self.render_current()
        self.history_changed.emit()
        self.log_requested.emit(
            f"全部非中概研究池已加入历史队列：新增 {result.inserted} 个，"
            f"队列合计 {result.total:,} 个；"
            "下载仍按页面所选批量执行。"
        )

    def request_run_ibkr(self, maximum_jobs: int) -> None:
        """Download the next batch through IBKR, with the current config."""

        def task(progress: Callable[[str], None]) -> dict[str, int]:
            return self._service.run_ibkr(
                self._ibkr_config_provider(),
                maximum_jobs=maximum_jobs,
                progress=lambda done, total, symbol, status: progress(
                    f"{done}/{total} {symbol}：{status}"
                ),
            )

        self._progress_percent = 1
        self.render_current()
        self._submit_task(
            task,
            on_success=self._finished,
            on_failure=self._failed,
            start_message=IBKR_START_MESSAGE,
            resource_group=HISTORY_RESOURCE_GROUP,
        )

    def request_run_public(self, maximum_jobs: int) -> None:
        """Download the next batch from the free public source.

        The rule that a public run first returns the failed jobs to the queue is
        the service's, not this class's.
        """

        def task(progress: Callable[[str], None]) -> dict[str, int]:
            return self._service.run_public(
                maximum_jobs=maximum_jobs,
                progress=lambda done, total, symbol, status: progress(
                    f"{done}/{total} {symbol}：{status}"
                ),
            )

        self._progress_percent = 1
        self.render_current()
        self._submit_task(
            task,
            on_success=self._finished,
            on_failure=self._failed,
            start_message=PUBLIC_START_MESSAGE,
            resource_group=HISTORY_RESOURCE_GROUP,
        )

    def retry_failed(self) -> None:
        """Put the failed jobs back in the pending queue."""

        count = self._service.reset_failed()
        self.render_current()
        self.log_requested.emit(
            f"已将 {count} 个失败任务放回待处理队列。"
        )

    # -- rendering --------------------------------------------------------

    def render_current(self) -> None:
        """Draw the page from a snapshot read *now*, plus the progress bar.

        The snapshot is never cached: it is the queue's truth, not this
        capability's, and a mirror would go stale the moment a download
        finished behind the desktop's back.
        """

        snapshot = self._service.snapshot()
        self._page.render(
            build_history_view(
                snapshot,
                progress_percent=self._progress_percent,
            )
        )

    # -- completion paths -------------------------------------------------

    def _finished(self, result: object) -> None:
        """Turn the batch's outcome counts into a percentage."""

        counts: dict[str, int] = result  # type: ignore[assignment]
        total = sum(counts.values())
        completed = counts.get("completed", 0)
        self._progress_percent = (
            int(completed / total * 100) if total else 0
        )
        self.render_current()
        self.history_changed.emit()
        self.log_requested.emit(
            f"本批结束：累计完成 {completed}，"
            f"失败 {counts.get('failed', 0)}。"
        )

    def _failed(self, _message: str) -> None:
        """A failed batch resets the bar; the window reports the message."""

        self._progress_percent = 0
        self.render_current()


__all__ = [
    "HISTORY_RESOURCE_GROUP",
    "IBKR_START_MESSAGE",
    "MISSING_UNIVERSE_MESSAGE",
    "MISSING_UNIVERSE_TITLE",
    "PUBLIC_START_MESSAGE",
    "HistoryOrchestrator",
]
