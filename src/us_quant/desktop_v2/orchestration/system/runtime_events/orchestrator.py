"""Runtime Events orchestration: the desktop owner of the System event workspace.

Before this module the Runtime Events sequence was spread across ``MainWindow``:
``self.runtime_events`` (the store), ``_last_runtime_events_refresh``,
``_runtime_events_refresh_pending``, ``_last_runtime_export``, six handlers and
five per-capability forwarding adapters.  The page rendered and emitted, the
store persisted and redacted, and the *sequence* -- when to write, when to
repaint, when to coalesce, what an export outcome means -- was the window's.
This class owns that sequence now.

The rules that make the boundary real:

* **the store stays the only persisted truth.**  ``RuntimeEventStore`` still owns
  the SQLite schema, the insert, the resolve and the redaction, and this class
  does not keep a list of events beside it.  Every repaint and every export
  re-reads ``list_recent``; a cached row list would be a second truth, and the
  first path that forgot to refresh it would show the operator an event that had
  already been confirmed -- or hide one that had just happened;
* **one write path.**  :meth:`record` is the only public way in and
  :meth:`_write` is the only place ``store.add`` is called, so "who writes a
  runtime event?" has exactly one answer.  Redaction is not repeated here;
  ``store.add`` already does it, and a second pass would be a second policy;
* **a failed write is a failure.**  ``store.add`` raising propagates: nothing is
  swallowed, no repaint pretends the row landed, and the caller sees the error
  it would have seen before this class existed;
* **explicit commands are immediate, arrivals coalesce.**  A Refresh click, a
  Resolve and the first event after a quiet second paint at once; a burst of
  events, or a burst of task start/finish notifications, arms *one* deferred
  flush.  That is the retired ``_schedule_runtime_events_refresh`` behaviour,
  moved whole: the point of it is that one event per repaint would rebuild a
  500-row table for every line the operator's run produces;
* **no widget, no dialog, no other capability.**  The page is a render seam, the
  dialogs are three signals, and the cross-capability facts a terminal export
  carries arrive as one injected callable.  That is what keeps this module from
  importing Account, Market, Strategy, Shadow, Paper, Targeted or the order
  repository -- and what keeps it from becoming the next god object;
* **the generic task lifecycle is not ours.**  ``TaskThread``, the controller,
  the worker list, the closing admission gate and the busy dialog stay on the
  window; this class receives one ``active_task_count`` provider and is told,
  not asked, that the count changed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from time import monotonic
from typing import Protocol

from PySide6.QtCore import QObject, QTimer, Signal

from us_quant.desktop_v2.orchestration.system.runtime_events.models import (
    RuntimeEventsEnvironment,
)
from us_quant.desktop_v2.pages.system.runtime_events.models import (
    RuntimeEventsPageView,
)
from us_quant.desktop_v2.pages.system.runtime_events.presenter import (
    build_runtime_events_view,
    runtime_info_text,
)
from us_quant.runtime_events import RuntimeEvent, RuntimeEventStore

#: How many recent events one repaint and one export read.  A behaviour constant
#: rather than a budget: it is the retired refresh's ``list_recent(500)`` and the
#: table the operator scrolls, and both must stay the same number or the export
#: and the screen would disagree about what "recent" means.
RECENT_EVENT_LIMIT = 500

#: The coalescing window, in seconds.  Within it, arrivals share one flush.
REFRESH_COALESCE_SECONDS = 1.0

#: The event the successful export writes back through the store, so the export
#: is part of the audit trail it produced rather than a fact only the UI knows.
EXPORT_COMPONENT = "export"
EXPORT_OK = "EXPORT_OK"

#: The wording of the messages this capability publishes.  The decision to say
#: one is the capability's; the dialog is the window's.
NO_SELECTION_TITLE = "未选择事件"
NO_SELECTION_MESSAGE = "请先选择一条运行事件。"
EXPORT_FAILED_TITLE = "导出失败"


class FlushTimer(Protocol):
    """The narrow seam the coalesced repaint is armed on.

    This class owns *when* a repaint is due; a timer owns *how* a callback is
    deferred.  Behind the protocol, a test can drive a burst of events with no
    event loop and no ``sleep``, and the real implementation can be a Qt timer
    that is cancelled when the repaint it was armed for is superseded.
    """

    def arm(
        self, delay_seconds: float, callback: Callable[[], None]
    ) -> None:
        """Ask for ``callback`` once, ``delay_seconds`` from now."""

    def disarm(self) -> None:
        """Cancel an armed callback, if one is armed."""


class QtFlushTimer:
    """The real seam: one single-shot Qt timer, owned by its orchestrator.

    The timer is a child of the orchestrator and therefore dies with it, and
    :meth:`disarm` drops the callback as well as stopping the timer, so a
    timeout that was already queued cannot repaint a page that has been
    superseded -- let alone one that is being torn down.
    """

    def __init__(self, owner: QObject) -> None:
        self._callback: Callable[[], None] | None = None
        self._timer = QTimer(owner)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)

    def _fire(self) -> None:
        callback = self._callback
        if callback is not None:
            callback()

    def arm(
        self, delay_seconds: float, callback: Callable[[], None]
    ) -> None:
        self._callback = callback
        self._timer.start(round(delay_seconds * 1000))

    def disarm(self) -> None:
        self._callback = None
        self._timer.stop()


class RuntimeEventsOrchestrator(QObject):
    """Owns the Runtime Events workspace and renders the Runtime Events page."""

    #: The operator must be told something.  The window owns the dialog.
    information_requested = Signal(str, str)

    #: Something the operator asked for failed, and the wording is ours.
    warning_requested = Signal(str, str)

    #: A terminal export succeeded, carrying the artifact path that was written.
    export_succeeded = Signal(object)

    def __init__(
        self,
        *,
        store: RuntimeEventStore,
        page: object,
        environment: RuntimeEventsEnvironment,
        active_task_count: Callable[[], int],
        export_bundle: Callable[[Sequence[RuntimeEvent]], Path],
        clock: Callable[[], float] = monotonic,
        timer: FlushTimer | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._page = page
        self._environment = environment
        self._active_task_count = active_task_count
        self._export_bundle = export_bundle
        self._clock = clock
        self._last_export: tuple[str, str] | None = None
        # ``None``, not ``0.0``: "no repaint has happened yet" and "a repaint
        # happened at the clock's origin" are different facts, and under an
        # injected clock the second one silently suppresses the first paint.
        self._last_refresh_at: float | None = None
        self._refresh_pending = False
        self._timer = timer if timer is not None else QtFlushTimer(self)

    # -- read-only facts -------------------------------------------------

    @property
    def last_export(self) -> tuple[str, str] | None:
        """The last successful export of this session, as ``(name, path)``.

        A *presentation* fact: it is deliberately not persisted, and it has
        exactly one owner.  Before v2O-F1 the window held it beside the store;
        a copy on both sides would disagree the first time one path forgot to
        update the other.
        """

        return self._last_export

    # -- recording -------------------------------------------------------

    def record(
        self,
        *,
        severity: str,
        component: str,
        code: str,
        message: str,
    ) -> RuntimeEvent:
        """Write one event through the store, then coalesce a repaint.

        This is the single entry point every ``runtime_event_requested`` signal
        and every window-owned fact reaches, so there is one writer and one
        redaction policy (``RuntimeEventStore.add``), not two.
        """

        event = self._write(
            severity=severity,
            component=component,
            code=code,
            message=message,
        )
        self._schedule_refresh()
        return event

    def _write(
        self,
        *,
        severity: str,
        component: str,
        code: str,
        message: str,
    ) -> RuntimeEvent:
        """The one ``store.add`` call.  It raises rather than swallowing.

        A refused write is not reported as a repaint: the exception travels to
        whoever recorded the event, exactly as it did when the window owned the
        store.  Silently surviving it would leave a page that looks healthy over
        a database that never received the row.
        """

        return self._store.add(
            severity=severity,
            component=component,
            code=code,
            message=message,
        )

    # -- refresh ---------------------------------------------------------

    def refresh(self) -> None:
        """Paint the store's current truth now, superseding a coalesced flush.

        Immediate by construction: the operator asked, or a command changed the
        truth, and neither may wait out a coalescing window.  Any armed flush is
        cancelled -- the paint that is happening now is the one it was armed
        for.

        An explicit repaint deliberately does *not* move the coalescing stamp.
        The retired window behaved the same way, and the two are different
        questions: "when did an arrival last paint?" is what bounds a burst,
        while "the operator pressed Refresh" says nothing about what the next
        arrival should cost.
        """

        self._timer.disarm()
        self._refresh_pending = False
        self._page.render(self._build_view())

    def notify_task_count_changed(self) -> None:
        """A generic task started or finished, so the task card is stale.

        This class does not model the task lifecycle and must not start: it is
        told that the count moved and re-reads the provider on the next repaint.
        A burst of starts and finishes coalesces like a burst of events.
        """

        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        """Paint at once if the last arrival-paint is old, else arm one flush.

        The ``_refresh_pending`` check is what makes a burst cheap: without it a
        hundred events in one second would arm a hundred flushes, which is the
        per-event table rebuild the coalescing exists to prevent.
        """

        last = self._last_refresh_at
        now = self._clock()
        if last is None or now - last >= REFRESH_COALESCE_SECONDS:
            self._last_refresh_at = now
            self.refresh()
            return
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self._timer.arm(REFRESH_COALESCE_SECONDS, self._flush_pending_refresh)

    def _flush_pending_refresh(self) -> None:
        """The coalesced repaint, identical to an explicit one by construction.

        A late callback -- one whose window an explicit refresh already closed --
        finds nothing pending and paints nothing, so a superseded flush cannot
        add a second render to the sequence.
        """

        if not self._refresh_pending:
            return
        self.refresh()

    # -- commands --------------------------------------------------------

    def resolve(self, event_id: int | None) -> None:
        """Confirm one event by its full integer id, or ask for a selection.

        ``None`` is not an error and not a store command: nothing is written,
        no truth changes, and the window is asked to say so.  A real id is the
        stable identity the row carries -- never a row index, a sort position or
        the text the operator reads -- and the repaint is immediate because the
        operator's click produced it.
        """

        if event_id is None:
            self.information_requested.emit(
                NO_SELECTION_TITLE, NO_SELECTION_MESSAGE
            )
            return
        self._store.resolve(event_id)
        self.refresh()

    def export(self) -> None:
        """Export the terminal bundle and sequence the outcome.

        The cross-capability facts the bundle carries are *not* gathered here;
        they arrive through the injected ``export_bundle``, which asks this
        class for nothing but the events.  What this class decides is the
        sequence: read the events once, hand them over, and only once the write
        has actually produced a path treat the export as done -- record it,
        repaint, then tell the window.  A failure leaves the previous export
        fact untouched, records nothing and reports the failure instead.
        """

        events = self._store.list_recent(RECENT_EVENT_LIMIT)
        try:
            target = self._export_bundle(events)
        except (OSError, ValueError) as error:
            self.warning_requested.emit(EXPORT_FAILED_TITLE, str(error))
            return
        self._last_export = (target.name, str(target))
        self._write(
            severity="info",
            component=EXPORT_COMPONENT,
            code=EXPORT_OK,
            message=f"终端状态已脱敏导出到 {target}",
        )
        self.refresh()
        self.export_succeeded.emit(target)

    # -- projection ------------------------------------------------------

    def _build_view(self) -> RuntimeEventsPageView:
        """Project the store's current truth into one immutable view.

        Every value is read *now*: the recent events from the store, the active
        task count from the provider.  Nothing is cached between repaints, and
        the task count deliberately is not captured in the constructor -- a
        count read once at composition time would keep showing the number of
        tasks that happened to be running when the window was built.
        """

        environment = self._environment
        return build_runtime_events_view(
            events=self._store.list_recent(RECENT_EVENT_LIMIT),
            active_task_count=self._active_task_count(),
            last_export=self._last_export,
            info_text=runtime_info_text(
                version=environment.version,
                resource_root=environment.resource_root,
                state_root=environment.state_root,
                runtime_root=environment.runtime_root,
                exports_root=environment.exports_root,
            ),
        )


__all__ = [
    "EXPORT_COMPONENT",
    "EXPORT_FAILED_TITLE",
    "EXPORT_OK",
    "FlushTimer",
    "NO_SELECTION_MESSAGE",
    "NO_SELECTION_TITLE",
    "QtFlushTimer",
    "RECENT_EVENT_LIMIT",
    "REFRESH_COALESCE_SECONDS",
    "RuntimeEventsOrchestrator",
]
