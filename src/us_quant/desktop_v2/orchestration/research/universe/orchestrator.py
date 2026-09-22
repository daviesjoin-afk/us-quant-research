"""Universe orchestration: the desktop owner of the official universe truth.

``DesktopUniverseService`` runs the official refresh *procedure*; it is a
procedure and not a state holder -- it returns a :class:`UniverseSnapshot` and
remembers nothing.  Something in the desktop layer has to own that snapshot and
the UI lifecycle around refreshing it, and before this module that something was
``MainWindow``: ``self.universe``, the cancel ``Event``, the ``TaskThread``
handle, the page render and the "which worker just finished" special case were
all touched from a handful of handlers.

This class now owns them, and it owns them *only*:

* **the snapshot.**  :attr:`snapshot` is the canonical desktop universe truth.
  The service is stateless, so this is a single owner rather than a second copy
  of something that already lived elsewhere.  Every consumer -- Scanner,
  cross-section research, auto-quant preparation, targeted preflight and replay,
  shadow eligibility, the market-scope summary -- reads it from here.  The
  window keeps no ``self.universe`` and no compatibility property, so answering
  "who owns universe truth?" takes one grep;
* **the refresh lifecycle.**  ``_refresh_active`` plus the cancel ``Event``
  replace the old "store the worker, then compare identity in
  ``_worker_finished``" trick.  The generic task boundary now hands back a
  completion callback (``on_finished``), so this class never learns which
  ``TaskThread`` it started and cannot depend on the worker list's ordering.

What it deliberately does not own:

* **the generic task lifecycle.**  ``TaskThread``, the controller, the worker
  list, the busy dialog and the cancellation handling stay on the window.  This
  class receives one narrow callable, ``submit_task``, and knows none of it --
  in particular it never stores the worker object;
* **cross-workflow fan-out.**  Whether the market-scope summary, the scanner or
  the shadow gate react to a new universe is not a universe decision: the window
  subscribes to :attr:`snapshot_changed` and routes it;
* **the refresh procedure.**  The stage vocabulary and the download/enrichment
  rules belong to ``DesktopUniverseService``.  This class only translates a
  stage into an operator-facing progress line;
* **startup artifact loading.**  The window still reads ``universe.json`` itself
  and calls :meth:`restore_snapshot`; restoring is not a successful refresh, so
  it deliberately publishes nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import Event

from PySide6.QtCore import QObject, Signal

from us_quant.desktop_universe_service import (
    STAGE_DOWNLOAD_OFFICIAL,
    STAGE_ENRICH_SEC,
    STAGE_ENRICH_SEC_START,
    STAGE_PREPARE_REFERENCE,
    DesktopUniverseService,
    UniverseRefreshProgress,
)
from us_quant.desktop_v2.orchestration.tasking import TaskSubmitter
from us_quant.desktop_v2.pages.research.universe.presenter import (
    build_universe_view,
)
from us_quant.universe import UniverseSnapshot

#: The resource group that serializes official-universe refreshes.  A second
#: concurrent refresh would rewrite the same reference files while the first is
#: still downloading them.
REFRESH_RESOURCE_GROUP = "universe"

#: The status line written when the refresh task is admitted.
REFRESH_START_MESSAGE = "刷新官方标的中…"

#: The status line written when the operator asks a live refresh to stop.  The
#: eight seconds is the domain module's own network timeout, quoted so the
#: operator knows the button is not instant.
CANCEL_MESSAGE = "已请求取消官方标的刷新；当前网络请求最多再等待 8 秒。"


def progress_message(event: UniverseRefreshProgress) -> str:
    """Translate one domain refresh stage into the operator's status line.

    An unknown stage raises instead of being ignored: a stage the desktop
    silently drops would leave the operator watching a frozen progress line
    while the refresh is in fact still running.
    """

    if event.stage == STAGE_PREPARE_REFERENCE:
        return "正在准备可写的用户参考数据目录…"
    if event.stage == STAGE_DOWNLOAD_OFFICIAL:
        return "正在下载 Nasdaq Trader 与 SEC 官方标的清单…"
    if event.stage == STAGE_ENRICH_SEC_START:
        return "正在增量核验 500 家 SEC 注册地与行业…"
    if event.stage == STAGE_ENRICH_SEC:
        return f"SEC 核验 {event.done}/{event.total}：{event.detail}"
    raise ValueError(f"unknown universe refresh stage: {event.stage}")


class UniverseOrchestrator(QObject):
    """Owns the desktop universe snapshot and its refresh UI lifecycle."""

    #: A refresh succeeded and a new snapshot is published.  The window fans
    #: this out to whatever else consumes universe facts (the market-scope
    #: summary today, and every capability that reads :attr:`snapshot`).
    snapshot_changed = Signal(object)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    def __init__(
        self,
        *,
        service: DesktopUniverseService,
        page: object,
        submit_task: TaskSubmitter,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._page = page
        self._submit_task = submit_task
        self._snapshot: UniverseSnapshot | None = None
        self._refresh_active = False
        self._cancel_event: Event | None = None

    # -- read-only fact the rest of the desktop consumes -------------------

    @property
    def snapshot(self) -> UniverseSnapshot | None:
        """The canonical desktop universe truth, or ``None`` before any load.

        This is a stored fact, not a delegation: ``DesktopUniverseService`` is
        stateless, so the desktop layer is where the snapshot lives.  Consumers
        read this property rather than holding their own copy -- a mirrored copy
        would be a second truth, and the two would disagree the first time a
        refresh updated only one of them.
        """

        return self._snapshot

    # -- lifecycle --------------------------------------------------------

    def restore_snapshot(self, snapshot: UniverseSnapshot) -> None:
        """Adopt the snapshot the startup loader read from disk.

        Startup restoration is **not** a successful refresh.  It seeds the truth
        and paints it, and publishes nothing: no :attr:`snapshot_changed` and no
        log line, because neither the market-scope summary nor the operator
        should be told that an official refresh just completed when the process
        only re-read a local file.
        """

        self._snapshot = snapshot
        self.render_current()

    def request_refresh(self) -> None:
        """Run the official refresh on the shared background task boundary.

        This class owns *what* the refresh is and which state surrounds it, and
        nothing about how a background task is admitted, tracked or torn down.
        That is what ``submit_task`` is for.
        """

        cancel_event = Event()

        def task(progress: Callable[[str], None]) -> UniverseSnapshot:
            def report(event: UniverseRefreshProgress) -> None:
                progress(progress_message(event))

            return self._service.refresh(
                should_stop=cancel_event.is_set,
                progress=report,
            )

        admitted = self._submit_task(
            task,
            on_success=self._refresh_succeeded,
            start_message=REFRESH_START_MESSAGE,
            resource_group=REFRESH_RESOURCE_GROUP,
            on_finished=self._refresh_finished,
        )
        if not admitted:
            # Nothing started, so nothing may be refreshing.  The state is set
            # only *after* admission for exactly this reason: the controls must
            # never enter "refreshing" for a task that was refused, or the
            # button stays lit until the next refresh.  A refused *duplicate*
            # leaves the running refresh's event alone, so its cancel button
            # keeps working.
            return
        self._cancel_event = cancel_event
        self._refresh_active = True
        self.render_current()

    def request_cancel(self) -> None:
        """Operator intent: ask the live refresh to stop.

        The thread is never terminated.  A half-written reference file is worse
        than a slow close, so this only sets the event the domain module polls
        between network calls.
        """

        if self._cancel_event is None:
            return
        self._cancel_event.set()
        self.render_current()
        self.log_requested.emit(CANCEL_MESSAGE)

    def cancel_for_shutdown(self) -> None:
        """Shutdown lifecycle: set the same event, without operator noise.

        Separate from :meth:`request_cancel` on purpose.  A close is not an
        operator asking for a cancellation, so it must not write a status line
        about a request nobody made, and it must not repaint the page while the
        window is tearing itself down.
        """

        event = self._cancel_event
        if event is not None:
            event.set()

    # -- rendering --------------------------------------------------------

    def render_current(self) -> None:
        """Draw the page from this capability's own state.

        It never fetches: the snapshot is whatever the last refresh or startup
        restore published.  A render that could start a read would be a second
        refresh path.
        """

        cancel_event = self._cancel_event
        self._page.render(
            build_universe_view(
                self._snapshot,
                refreshing=self._refresh_active,
                cancel_requested=bool(
                    cancel_event and cancel_event.is_set()
                ),
            )
        )

    # -- success path -----------------------------------------------------

    def _refresh_succeeded(self, result: object) -> None:
        """Publish one successful refresh: snapshot, page, event, log."""

        if not isinstance(result, UniverseSnapshot):
            raise TypeError("unexpected universe snapshot")
        self._snapshot = result
        self.render_current()
        self.snapshot_changed.emit(result)
        summary = result.summary()
        self.log_requested.emit(
            f"官方标的已刷新：{summary['total']:,} 个，"
            f"研究池 {summary['research_eligible']} 个。"
        )

    def _refresh_finished(self) -> None:
        """The single completion path: success, failure or cancellation.

        Every terminal state of the task lands here, which is what lets the
        window stop special-casing universe workers in ``_worker_finished``.
        It runs *after* the generic lifecycle has released the worker, so the
        repaint below already sees a freed resource group.
        """

        self._refresh_active = False
        self._cancel_event = None
        self.render_current()


__all__ = [
    "CANCEL_MESSAGE",
    "REFRESH_RESOURCE_GROUP",
    "REFRESH_START_MESSAGE",
    "UniverseOrchestrator",
    "progress_message",
]
