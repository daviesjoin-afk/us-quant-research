"""Wiring tests: the real window's Runtime Events route, end to end.

The Runtime Events page's own tests prove it emits, and the orchestrator's own
tests prove the sequencing.  These prove the *composition*: the page's three
intents reach the capability, every capability's ``runtime_event_requested``
lands in the one store write, the task count is re-read from the live provider
and the export payload is unchanged.

Since v2O-F1 the window holds no store handle, no refresh stamp, no pending flag
and no last-export fact.  That is what these tests have to work *around*, and it
is the point: persisted truth is read through the capability that owns it
(``runtime_events_orchestrator``), never through the window.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.desktop_targeted_evidence_models import (
    TargetedEvidenceRuntimeEvent,
)
from us_quant.desktop_v2.orchestration.account.models import (
    AccountRuntimeEvent,
)
from us_quant.desktop_v2.orchestration.market.models import MarketRuntimeEvent
from us_quant.desktop_v2.orchestration.paper.models import (
    PaperRuntimeEventRequest,
)
from us_quant.desktop_v2.orchestration.shadow.models import (
    ShadowRuntimeEvent,
)
from us_quant.desktop_v2.pages.system.runtime_events.models import (
    RuntimeEventRowView,
    RuntimeEventsPageView,
    RuntimeEventTone,
)
from us_quant.paths import STATE_ROOT_ENV


_APP = QApplication.instance() or QApplication([])


def _window(monkeypatch, tmp_path) -> MainWindow:
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path / "state"))
    window = MainWindow()
    _APP.processEvents()
    return window


def _readable(window: MainWindow) -> object:
    """The store the capability owns, read by the test that has no window handle.

    ``MainWindow`` deliberately holds no reference to it, so the only honest way
    to assert what was *persisted* -- as opposed to what was rendered -- is to
    ask the owner.
    """

    return window.runtime_events_orchestrator._store


def _force_immediate(window: MainWindow) -> None:
    """Close the coalescing window so the next arrival paints at once."""

    window.runtime_events_orchestrator._last_refresh_at = None


def _row(event_id: int, severity: str = "info") -> RuntimeEventRowView:
    tone = {
        "error": RuntimeEventTone.ERROR,
        "warning": RuntimeEventTone.WARNING,
    }.get(severity, RuntimeEventTone.NEUTRAL)
    return RuntimeEventRowView(
        event_id=event_id,
        event_id_text=str(event_id),
        occurred_at="2026-01-01T00:00:00+00:00",
        severity=severity,
        severity_text={"info": "信息", "warning": "警告", "error": "错误"}[
            severity
        ],
        component="market_data",
        code="STREAM_START",
        message=f"event {event_id}",
        status_text="待确认",
        tone=tone,
    )


def _latest(window: MainWindow):
    return _readable(window).list_recent(1)[0]


# -- the page's intents reach the capability ----------------------------


def test_refresh_reads_the_latest_five_hundred_and_repaints(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        limits: list[int] = []
        store = _readable(window)
        real = store.list_recent

        def list_recent(limit=500):
            limits.append(limit)
            return real(limit)

        monkeypatch.setattr(store, "list_recent", list_recent)
        window.runtime_events_page._refresh_button.click()
        assert limits == [500]
    finally:
        window.close()
        window.deleteLater()


def test_recording_an_event_refreshes_the_page(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        before = window.runtime_events_page.table.rowCount()
        window.runtime_events_orchestrator.record(
            severity="info",
            component="test",
            code="UNIT",
            message="unit test event",
        )
        assert window.runtime_events_page.table.rowCount() == before + 1
    finally:
        window.close()
        window.deleteLater()


def test_resolve_without_a_selection_shows_the_existing_message(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        calls: list[tuple] = []
        monkeypatch.setattr(
            "us_quant.desktop.QMessageBox.information",
            lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        # Nothing is selected and nothing needs to be: the page reports ``None``
        # and the capability asks the window to say so.
        window.runtime_events_page._resolve_button.click()
        assert len(calls) == 1
        args, _kwargs = calls[0]
        assert "未选择事件" in args[1]
        assert args[2] == "请先选择一条运行事件。"
    finally:
        window.close()
        window.deleteLater()


def test_a_real_click_resolves_by_event_id_not_row_index(
    monkeypatch, tmp_path
) -> None:
    """The non-vacuity check: sorting must not turn an id into a row index."""

    window = _window(monkeypatch, tmp_path)
    try:
        orchestrator = window.runtime_events_orchestrator
        _force_immediate(window)
        first = orchestrator.record(
            severity="info",
            component="market_data",
            code="STREAM_START",
            message="first",
        )
        _force_immediate(window)
        second = orchestrator.record(
            severity="warning",
            component="market_data",
            code="STREAM_STOP",
            message="second",
        )
        orchestrator.refresh()

        page = window.runtime_events_page
        # Descending by id, so the *older* row is second in the table.
        page.table._select_event(first.event_id)
        page._resolve_button.click()

        resolved = {
            event.event_id: event.resolved
            for event in _readable(window).list_recent(10)
        }
        assert resolved[first.event_id] is True
        assert resolved[second.event_id] is False
    finally:
        window.close()
        window.deleteLater()


# -- the task count is read from the live provider ----------------------


class _FakeSignal:
    """A Qt-signal stand-in: records slots, never emits by itself."""

    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot) -> None:
        self._slots.append(slot)


class _FakeTaskThread:
    """A deterministic task lifecycle, shaped like the real ``TaskThread``.

    ``running`` starts False and only ``start()`` flips it -- exactly the
    timing of a real QThread.  No thread, no scheduling: the count transitions
    the Runtime Events card draws are fully synchronous and reproducible.
    """

    def __init__(self, task, resource_group: str = "research") -> None:
        self.task = task
        self.resource_group = resource_group
        self.running = False
        self.progress = _FakeSignal()
        self.failed = _FakeSignal()
        self.cancelled = _FakeSignal()
        self.succeeded = _FakeSignal()
        self.finished = _FakeSignal()

    def isRunning(self) -> bool:
        return self.running

    def start(self) -> None:
        self.running = True


def test_task_lifecycle_republishes_active_task_count(
    monkeypatch, tmp_path
) -> None:
    """Blocker A: a real worker lifecycle must republish the active count.

    This drives the window's own ``_start_task`` / ``_worker_finished`` and
    reads the count back off the rendered page, so deleting either lifecycle
    notification turns the test red instead of merely asserting a call happened.

    The lifecycle is a deterministic fake whose ``running`` starts False and
    flips only in ``start()`` -- the real QThread timing.  That is the point:
    the "task started" notification must be sent *after* ``start()``, because
    the card draws ``active_count`` and a pre-start notification would paint 0
    with no second notification to correct it.  The ``card == 1`` assertion
    below fails if that notification is moved back before ``start()``.
    """

    window = _window(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(
            "us_quant.desktop.TaskThread", _FakeTaskThread
        )
        _force_immediate(window)
        assert window.runtime_events_page.task_card.value_label.text() == "0"

        accepted = window._start_task(
            lambda _report: None,
            on_success=lambda _result: None,
            start_message="单元测试后台任务",
            resource_group="unit-test",
        )
        assert accepted is True
        assert (
            window.runtime_events_page.task_card.value_label.text() == "1"
        ), (
            "the started task is not on the card -- the notification was sent "
            "before start() (active_count is still 0 there) or not sent at all"
        )

        worker = window.task_controller.running_workers()[0]
        _force_immediate(window)
        window._worker_finished(worker)
        assert (
            window.runtime_events_page.task_card.value_label.text() == "0"
        )
    finally:
        # A fake worker left registered-and-running would make the window's
        # close path see an active task and block the suite on a dialog no
        # offscreen run can answer -- release every straggler first.
        for worker in window.task_controller.running_workers():
            window.task_controller.finish(worker)
        window.close()
        window.deleteLater()


# -- fan-in: six sources, one write -------------------------------------


def _fan_in_cases():
    return (
        (
            "market",
            lambda window: window.market_orchestrator.runtime_event_requested,
            MarketRuntimeEvent(
                severity="warning",
                component="market_data",
                code="STREAM_DROPPED",
                message="market fan-in",
            ),
        ),
        (
            "account",
            lambda window: window.account_orchestrator.runtime_event_requested,
            AccountRuntimeEvent(
                severity="info",
                component="account",
                code="SNAPSHOT_OK",
                message="account fan-in",
            ),
        ),
        (
            "shadow",
            lambda window: window.shadow_orchestrator.runtime_event_requested,
            ShadowRuntimeEvent(
                severity="info",
                component="shadow",
                code="RUN_STARTED",
                message="shadow fan-in",
            ),
        ),
        (
            "paper",
            lambda window: window.paper_orchestrator.runtime_event_requested,
            PaperRuntimeEventRequest(
                severity="error",
                component="paper_execution",
                code="ORDER_REFUSED",
                message="paper fan-in",
            ),
        ),
        (
            "targeted evidence",
            lambda window: (
                window.targeted_evidence_orchestrator.runtime_event_requested
            ),
            TargetedEvidenceRuntimeEvent(
                severity="info",
                component="targeted_evidence",
                code="REPLAY_OK",
                message="evidence fan-in",
            ),
        ),
    )


@pytest.mark.parametrize(
    "name,signal_of,event",
    _fan_in_cases(),
    ids=[case[0] for case in _fan_in_cases()],
)
def test_every_capability_runtime_event_lands_once_in_the_store(
    monkeypatch, tmp_path, name, signal_of, event
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        before = len(_readable(window).list_recent(500))
        _force_immediate(window)
        signal_of(window).emit(event)
        events = _readable(window).list_recent(500)
        assert len(events) == before + 1, name
        newest = events[0]
        assert (newest.severity, newest.component, newest.code) == (
            event.severity,
            event.component,
            event.code,
        )
        assert newest.message == event.message
    finally:
        window.close()
        window.deleteLater()


def test_a_window_owned_task_failure_lands_in_the_same_store(
    monkeypatch, tmp_path
) -> None:
    """The generic task failure is a fact like any other, through the same door."""

    window = _window(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(
            "us_quant.desktop.QMessageBox.warning", lambda *a, **k: None
        )
        _force_immediate(window)
        window._task_failed("单元测试任务失败")
        newest = _latest(window)
        assert (newest.severity, newest.component, newest.code) == (
            "error",
            "task",
            "TASK_FAILED",
        )
        assert newest.message == "单元测试任务失败"
    finally:
        window.close()
        window.deleteLater()


# -- the export keeps its payload and its visible semantics --------------


def test_the_export_payload_is_unchanged_and_carries_the_stored_events(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        _force_immediate(window)
        marker = window.runtime_events_orchestrator.record(
            severity="info",
            component="unit",
            code="MARKER",
            message="export payload marker",
        )
        seen: dict = {}

        def recorder(*args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs
            return tmp_path / "terminal-2.zip"

        monkeypatch.setattr(
            "us_quant.desktop.export_terminal_bundle", recorder
        )
        monkeypatch.setattr(
            "us_quant.desktop.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        window.runtime_events_orchestrator.export()

        kwargs = seen["kwargs"]
        expected = {
            "portfolio",
            "stream",
            "strategies",
            "events",
            "shadow_fills",
            "targeted_replays",
            "targeted_robustness",
            "targeted_walk_forward",
            "targeted_overfit",
            "targeted_data_quality",
            "targeted_execution_stress",
            "targeted_review",
            "paper_order_audit",
            "paper_execution_audit",
        }
        assert expected <= set(kwargs)
        assert seen["args"] == (window.paths.exports_root,)
        # The events handed over are the store's own rows: same reader, same
        # limit, so the export cannot describe a different set from the screen.
        ids = {event.event_id for event in kwargs["events"]}
        assert marker.event_id in ids
    finally:
        window.close()
        window.deleteLater()


def test_successful_export_records_the_fact_and_refreshes(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        target = tmp_path / "terminal-1.zip"
        monkeypatch.setattr(
            "us_quant.desktop.export_terminal_bundle",
            lambda *args, **kwargs: target,
        )
        monkeypatch.setattr(
            "us_quant.desktop.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        _force_immediate(window)
        window.runtime_events_orchestrator.export()

        assert window.runtime_events_orchestrator.last_export == (
            target.name,
            str(target),
        )
        assert (
            window.runtime_events_page.export_card.value_label.text()
            == target.name
        )
        newest = _latest(window)
        assert (newest.severity, newest.component, newest.code) == (
            "info",
            "export",
            "EXPORT_OK",
        )
    finally:
        window.close()
        window.deleteLater()


def test_a_failed_export_shows_the_existing_warning_and_changes_nothing(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        def explode(*args, **kwargs):
            raise OSError("磁盘已满")

        monkeypatch.setattr("us_quant.desktop.export_terminal_bundle", explode)
        dialogs: list[tuple] = []
        monkeypatch.setattr(
            "us_quant.desktop.QMessageBox.information",
            lambda *args, **kwargs: dialogs.append((args, "info")),
        )
        monkeypatch.setattr(
            "us_quant.desktop.QMessageBox.warning",
            lambda *args, **kwargs: dialogs.append((args, "warning")),
        )
        before = len(_readable(window).list_recent(500))
        window.runtime_events_orchestrator.export()

        assert window.runtime_events_orchestrator.last_export is None
        assert [kind for _args, kind in dialogs] == ["warning"]
        args, _kind = dialogs[0]
        assert args[1] == "导出失败"
        assert "磁盘已满" in args[2]
        assert window.runtime_events_page.export_card.value_label.text() == "无"
        assert len(_readable(window).list_recent(500)) == before
    finally:
        window.close()
        window.deleteLater()


# -- the page is still render/emit only ---------------------------------


def test_the_page_never_decides_a_resolution(monkeypatch, tmp_path) -> None:
    """The page reports the id it was handed; the capability decides."""

    window = _window(monkeypatch, tmp_path)
    try:
        window.runtime_events_page.render(
            RuntimeEventsPageView(
                error_count="0",
                warning_count="0",
                active_task_count="0",
                last_export_value="无",
                last_export_note="脱敏 CSV / JSON",
                rows=(_row(10), _row(2)),
                info_text="info",
            )
        )
        orchestrator = window.runtime_events_orchestrator
        seen: list[object] = []
        monkeypatch.setattr(orchestrator, "resolve", seen.append)

        page = window.runtime_events_page
        page.table._select_event(2)
        page._resolve_button.click()

        assert seen == [2]
    finally:
        window.close()
        window.deleteLater()
