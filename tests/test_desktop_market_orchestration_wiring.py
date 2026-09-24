"""Real ``MainWindow`` wiring for the v2O-A market orchestration extraction.

The orchestrator's own tests prove it decides correctly; these prove the
*window* is still listening.  The extraction moved the runtime out of
``MainWindow``, and the failure mode it could hide is a signal nobody
connected -- a dashboard card that never repaints, a Paper session that never
sees a tick -- which is invisible to a test that only drives the orchestrator.

Nothing here reaches a feed.  The page signals are driven directly, and the
snapshot is handed to the orchestrator the way a worker would.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.desktop_v2.pages.market import presenter
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)
from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase


_APP = QApplication.instance() or QApplication([])
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class _RunningWorker:
    """A worker that reports itself live; nothing here starts a thread."""

    def __init__(self, source_id: str = "ibkr") -> None:
        self.source_id = source_id
        self.market_exchange = "SMART"

    def isRunning(self) -> bool:  # noqa: N802 - Qt spelling
        return True

    def request_stop(self) -> None:
        return None

    def wait(self, _timeout: int) -> bool:
        return True


@pytest.fixture()
def window():
    widget = MainWindow()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


@pytest.fixture()
def quiet_minutes(window, monkeypatch):
    """Stub the minute recorder, which persists to SQLite on a real path.

    The fan-out is asserted separately, against the stub; every other test here
    only needs it not to run.
    """

    recorded: list[object] = []
    monkeypatch.setattr(window, "_record_minute_snapshot", recorded.append)
    return recorded


def _quote(**overrides) -> MarketQuote:
    values = dict(
        symbol="SPY",
        bid=Decimal("100"),
        ask=Decimal("100.1"),
        last=Decimal("100.05"),
        close=Decimal("99"),
        bid_size=None,
        ask_size=None,
        mode=MarketDataMode.REALTIME,
        updated_at=NOW,
        age_seconds=0.2,
        stale=False,
        stale_reason=None,
        generation=1,
        source_id="alpaca_iex",
        source_label="Alpaca",
        coverage="IEX",
    )
    values.update(overrides)
    return MarketQuote(**values)  # type: ignore[arg-type]


def _snapshot(*, quotes=None, message="") -> MarketSnapshot:
    return MarketSnapshot(
        generation=1,
        connected=True,
        ready=True,
        reconnect_attempt=0,
        quotes=tuple(quotes) if quotes is not None else (_quote(),),
        error_code=None,
        message=message,
        observed_at=NOW,
        source_id="alpaca_iex",
        source_label="Alpaca",
        coverage="IEX",
    )


# -- the route table -----------------------------------------------------


def test_the_market_route_is_the_native_page(window) -> None:
    assert window.shell.page("market") is window.market_page


def test_the_window_composes_the_orchestrator_with_its_own_services(
    window,
) -> None:
    """Composition, not ownership: the window builds it and then stays out.

    Asserted behaviourally rather than by reading the orchestrator's private
    attributes -- a write the orchestrator accepts must land on *this*
    window's page, which is only true if it was handed the same page.
    """

    assert window.market_orchestrator is not None

    window.market_orchestrator.set_scope("组合根探针")
    assert window.market_page.scope_label.text() == "组合根探针"

    # And it holds the window's own market data application: the session
    # rotation reads the venue through it.
    window.market_orchestrator.maybe_request_extended_session_rotation()
    assert window.market_orchestrator.snapshot is None


def test_the_page_intents_land_on_the_window_bridges(window, monkeypatch) -> None:
    """The three gates stay on the window; the page must reach them.

    The real handlers are replaced by recorders before the signals are
    re-wired: the handlers under test are the ones that would *start* a feed,
    and a page cannot run them itself.  Replacing them is not a shortcut around
    the wiring -- the wiring is exactly what is asserted -- it is what keeps
    the test from reaching a provider.
    """

    seen: list[str] = []
    wiring = (
        ("start_requested", "_request_market_start"),
        ("stop_requested", "_request_market_stop"),
        ("load_scan_watchlist_requested", "_apply_intraday_watchlist"),
    )
    for signal, handler in wiring:
        assert hasattr(window, handler), handler
        monkeypatch.setattr(
            window,
            handler,
            lambda _signal=signal: seen.append(_signal),
        )
        getattr(window.market_page, signal).disconnect()
    window._connect_market_page()

    for signal, _handler in wiring:
        getattr(window.market_page, signal).emit()

    assert seen == [
        "start_requested",
        "stop_requested",
        "load_scan_watchlist_requested",
    ]


def test_the_window_has_no_market_runtime_attribute(window) -> None:
    """The ownership the extraction removed, asserted on the live object."""

    for name in (
        "stream_worker",
        "stream_snapshot",
        "stream_timer",
        "_pending_stream_switch",
        "_stream_stop_pending",
        "_market_scope",
        "_market_watchlist_note",
        "_dashboard_market_stop_reason",
        "_quote_last_ready_monotonic",
        "_last_stream_event_key",
        "_last_stream_status_key",
        "_last_stream_status_log_at",
        "_last_stream_push_monotonic",
    ):
        assert not hasattr(window, name), name


# -- snapshot fan-out ----------------------------------------------------


def test_a_snapshot_repaints_the_dashboard_card(window, quiet_minutes) -> None:
    """Read the rendered fact, not "was the callback called"."""

    window.market_orchestrator._on_snapshot(_snapshot())

    card = window.dashboard_page._intraday_market_card
    assert card.value_label.text() == "可用"
    assert card.note_label.text() == "Alpaca IEX 单交易所实时"


def test_a_snapshot_publishes_the_workflow_market_readiness(window, quiet_minutes) -> None:
    window.market_orchestrator._on_snapshot(_snapshot())

    snapshot = window.workflow_controller.market_account.snapshot
    assert snapshot.market_ready is True


def test_a_snapshot_reaches_the_auto_quant_preflight(window, quiet_minutes) -> None:
    """The preflight reads the market truth, so its line must move."""

    window.market_orchestrator._on_snapshot(_snapshot())
    window._refresh_auto_quant_preflight()

    line = window.execution_page.controls.preflight_label.text()
    assert "准备检查" in line


def test_a_snapshot_is_recorded_as_minute_evidence(
    window, quiet_minutes
) -> None:
    """The recorder is a different capability; the fan-out must not drop it."""

    window.market_orchestrator._on_snapshot(_snapshot())

    assert len(quiet_minutes) == 1


def test_an_invalidated_snapshot_repaints_the_dashboard_as_stopped(
    window, quiet_minutes
) -> None:
    window.market_orchestrator._on_snapshot(_snapshot())

    window.market_orchestrator._invalidate("行情流正在停止")

    card = window.dashboard_page._intraday_market_card
    assert (card.value_label.text(), card.note_label.text()) == (
        "不可用",
        "行情流正在停止",
    )


def test_a_snapshot_reaches_a_running_paper_session(window, quiet_minutes) -> None:
    """The Paper fan-out is the one a careless extraction would drop.

    Since v2O-E2 the phase gate that decides this lives in ``PaperOrchestrator``, so the
    session is put into ``RUNNING`` on the *workflow* -- the canonical phase the
    capability reads -- rather than on the facade.  The recorder is installed on the
    workflow too, which is where the capability's ingress lands, so this still asserts
    the whole path: market fact -> fan-out -> capability -> workflow.
    """

    received: list[object] = []
    original_phase = window.paper_workflow._phase
    window.paper_workflow._phase = PaperWorkflowPhase.RUNNING
    original_on_stream = window.paper_workflow.on_stream

    def recording_on_stream(snapshot):
        received.append(snapshot)
        return original_on_stream(snapshot)

    window.paper_workflow.on_stream = recording_on_stream  # type: ignore[assignment]
    try:
        window.market_orchestrator._on_snapshot(_snapshot())
    finally:
        window.paper_workflow._phase = original_phase
        window.paper_workflow.on_stream = original_on_stream  # type: ignore[assignment]

    assert len(received) == 1
    assert received[0].quotes[0].symbol == "SPY"


def test_a_snapshot_refreshes_an_active_shadow_book(window, quiet_minutes) -> None:
    """The Shadow fan-out survives the v2O-D move; only its owner changed."""

    received: list[object] = []

    class _Engine:
        active = True

        def on_stream(self, snapshot):
            received.append(snapshot)
            return window.shadow_orchestrator.snapshot

    window.shadow_orchestrator._engine = _Engine()  # type: ignore[assignment]
    try:
        window.market_orchestrator._on_snapshot(_snapshot())
    finally:
        window.shadow_orchestrator._engine = None  # type: ignore[assignment]

    assert len(received) == 1


def test_a_snapshot_does_not_wake_a_paused_session(window, quiet_minutes) -> None:
    """The gate is the phase, and it must still hold after the move."""

    received: list[object] = []
    original_on_stream = window.paper_workflow.on_stream
    window.paper_workflow.on_stream = (  # type: ignore[assignment]
        lambda snapshot: received.append(snapshot)
    )
    try:
        window.market_orchestrator._on_snapshot(_snapshot())
    finally:
        window.paper_workflow.on_stream = original_on_stream  # type: ignore[assignment]

    assert received == []


# -- the shell badges ----------------------------------------------------


def test_a_snapshot_paints_the_market_badge(window, quiet_minutes) -> None:
    window.market_orchestrator._on_snapshot(_snapshot())

    assert window.market_badge.text() == "行情 · IEX 实时"
    assert window.market_badge.property("state") == "ok"


def test_a_snapshot_promotes_the_handshake_badge(window, quiet_minutes) -> None:
    window.market_orchestrator._on_snapshot(_snapshot())

    assert window.handshake_badge.text() == "Alpaca · 已认证"


def test_a_stopped_feed_paints_a_warning_badge(window, quiet_minutes) -> None:
    window.market_orchestrator._on_snapshot(_snapshot())

    window.market_orchestrator._invalidate("行情流正在停止")

    assert window.market_badge.text() == "行情 · 已停止"
    assert window.market_badge.property("state") == "warn"


def test_a_failed_feed_is_recorded_in_the_event_log(
    window, monkeypatch, tmp_path
) -> None:
    """``Market -> System`` is a request, and the window must honour it.

    Read off the rendered table rather than the store: the page is capped at
    the most recent 500 rows, so the code column is what carries the fact.
    """

    from us_quant.paths import STATE_ROOT_ENV

    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))

    window.market_orchestrator._on_failed("socket exploded")
    _APP.processEvents()

    table = window.runtime_events_page.table
    codes = {
        table.item(row, 4).text() for row in range(table.rowCount())
    }
    assert "STREAM_FAILED" in codes


# -- controls ------------------------------------------------------------


def test_the_execution_stop_control_follows_the_orchestrators_controls(
    window,
) -> None:
    """The Execution v2 ordering, observed on the real window."""

    window.market_orchestrator._worker = _RunningWorker()
    try:
        window.market_orchestrator._render_controls()
        window.market_orchestrator.controls_changed.emit()
        _APP.processEvents()

        assert window.execution_page.controls.stop_stream_button.isEnabled()
        assert window.market_page.controls.stop_button.isEnabled()
    finally:
        window.market_orchestrator._worker = None
        window.market_orchestrator._stop_pending = False
        window.market_orchestrator._render_controls()
        _APP.processEvents()


def test_a_refused_request_is_surfaced_by_the_window(window, monkeypatch) -> None:
    """The orchestrator owns no widgets, so the window shows the dialog."""

    captured: list[tuple] = []
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.warning",
        lambda *args, **kwargs: captured.append(args),
    )

    window.market_orchestrator.refused.emit("无法启动", "至少填写一个行情代码")

    assert captured
    assert captured[0][1] == "无法启动"


def test_the_settings_provider_write_reaches_the_page_without_intent(
    window,
) -> None:
    """Restoring a saved provider is programmatic, and must not recurse."""

    seen: list[str] = []
    window.market_page.provider_selected.connect(seen.append)

    window._settings_provider_selected("alpaca_iex")

    assert window.market_page.selected_provider() == "alpaca_iex"
    assert seen == []


def test_the_market_scope_line_is_written_through_the_orchestrator(
    window,
) -> None:
    window._refresh_market_scope_summary()

    assert "范围分层" in window.market_page.scope_label.text()


def test_the_market_readiness_inputs_come_from_the_window(window) -> None:
    """The candidate shortlist is the execution route's, not the market's."""

    window._publish_market_readiness_inputs()

    inputs = window.market_orchestrator._render._inputs
    assert isinstance(inputs.candidate_symbols, tuple)
    assert isinstance(inputs.reference_symbols, tuple)


# -- shutdown ------------------------------------------------------------


class _StuckWorker:
    """A worker whose thread never confirms exit, then does.

    ``alive`` is the switch the recovery test flips: the same worker object
    stays installed while its thread finally ends, which is exactly the state
    the close path has to be able to leave.
    """

    def __init__(self, source_id: str = "ibkr") -> None:
        self.source_id = source_id
        self.market_exchange = "SMART"
        self.alive = True
        self.stop_requests = 0

    def isRunning(self) -> bool:  # noqa: N802 - Qt spelling
        return self.alive

    def request_stop(self) -> None:
        self.stop_requests += 1

    def wait(self, _timeout: int) -> bool:
        return False


def _silence_dialogs(monkeypatch) -> list[tuple]:
    """The close path reports to the operator; offscreen must not block on it."""

    calls: list[tuple] = []

    def recorder(kind: str):
        def message(*args, **kwargs):
            calls.append((kind, args, kwargs))
            return None

        return staticmethod(message)

    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.warning", recorder("warning")
    )
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.information",
        recorder("information"),
    )
    return calls


def _close_verdict(window) -> bool:
    """Run the real ``closeEvent`` and report whether it accepted the close."""

    from PySide6.QtGui import QCloseEvent

    event = QCloseEvent()
    window.closeEvent(event)
    return bool(event.isAccepted())


def test_a_stuck_market_worker_is_not_reported_as_a_clean_release(
    window, monkeypatch
) -> None:
    """The blocker this round repairs, pinned as behaviour.

    A stop that does not confirm leaves the feed unusable while the network
    thread is still alive.  Wiring the supervisor's probe to ``is_live`` makes
    it read that as "nothing to release", so shutdown reports success over a
    running worker.
    """

    _silence_dialogs(monkeypatch)
    worker = _StuckWorker()
    window.market_orchestrator._worker = worker
    try:
        assert window.market_orchestrator.is_live
        assert window.market_orchestrator.worker_running

        assert window._stop_market_data() is False

        # The two facts have now diverged: no usable feed, live thread.
        assert window.market_orchestrator.is_live is False
        assert window.market_orchestrator.worker_running is True

        snapshot = window.runtime_supervisor.shutdown()
        component = {
            item.name: item for item in snapshot.components
        }["market_data_stream"]

        assert component.exit_ok is False
        assert component.state == "failed"
        assert component.last_error is not None
        assert "did not exit" in component.last_error
        assert window.runtime_supervisor.errors()
    finally:
        window.market_orchestrator._worker = None
        window.market_orchestrator._stop_pending = False


def test_close_is_refused_while_the_market_thread_is_still_alive(
    window, monkeypatch
) -> None:
    """The close gate is thread liveness, not feed availability.

    Paper is already finalized and nothing is running in the background, so
    the only thing that can refuse this close is the market worker.  The
    dialog is asserted too: a refusal from some other branch would otherwise
    look identical from the outside.
    """

    calls = _silence_dialogs(monkeypatch)
    worker = _StuckWorker()
    window.market_orchestrator._worker = worker
    try:
        accepted = _close_verdict(window)

        assert accepted is False
        assert window.market_orchestrator.worker_running is True
        assert window.market_orchestrator.is_live is False
        titles = [
            args[1] for kind, args, _kwargs in calls if kind == "information"
        ]
        assert "行情线程正在停止" in titles, titles
    finally:
        window.market_orchestrator._worker = None
        window.market_orchestrator._stop_pending = False


def test_close_completes_once_the_market_thread_has_really_ended(
    window, monkeypatch
) -> None:
    """The repair must not wedge the exit behind a permanent stop-pending flag.

    Same worker object, same pending stop, thread finally gone: the next close
    has to go through.
    """

    _silence_dialogs(monkeypatch)
    worker = _StuckWorker()
    window.market_orchestrator._worker = worker
    try:
        assert _close_verdict(window) is False
        assert window.market_orchestrator._stop_pending is True

        worker.alive = False

        assert window.market_orchestrator.worker_running is False
        assert _close_verdict(window) is True
    finally:
        window.market_orchestrator._worker = None
        window.market_orchestrator._stop_pending = False


def test_closing_with_a_live_feed_asks_the_orchestrator(window) -> None:
    """The last check in ``closeEvent`` must not name a worker."""

    names = window.closeEvent.__func__.__code__.co_names
    assert "market_orchestrator" in names
    # The gate reads thread liveness, not the business "feed is usable" fact.
    assert "worker_running" in names
    assert "is_live" not in names
    assert "stream_worker" not in names


def test_the_connecting_view_reaches_the_real_page(window, monkeypatch) -> None:
    """The route must show "connecting" between start and first snapshot."""

    from PySide6.QtCore import QObject, Signal

    from us_quant.desktop_credentials import StreamCredentials

    class _Worker(QObject):
        snapshot_ready = Signal(object)
        failed = Signal(str)
        finished = Signal()

        source_id = "ibkr"
        market_exchange = "SMART"

        def __init__(self) -> None:
            super().__init__()
            self._running = False

        def isRunning(self) -> bool:  # noqa: N802 - Qt spelling
            return self._running

        def start(self) -> None:
            self._running = True

        def request_stop(self) -> None:
            self._running = False

        def wait(self, _timeout: int) -> bool:
            self._running = False
            return True

    worker = _Worker()
    monkeypatch.setattr(
        "us_quant.desktop_v2.orchestration.market.orchestrator.StreamWorker",
        lambda *_args, **_kwargs: worker,
    )
    monkeypatch.setattr(
        window.credential_service,
        "resolve_stream_credentials",
        lambda **_kwargs: StreamCredentials(
            finnhub_api_key="", alpaca_api_key="", alpaca_api_secret=""
        ),
    )
    window.market_page.set_selected_provider("ibkr")
    window.market_page.set_subscription_symbols(("SPY", "QQQ"))

    window.market_orchestrator.start()

    assert window.market_page.connection_card.value_label.text() == "连接中"
    assert window.market_page.empty_label.text() == presenter.CONNECTING_EMPTY
    assert window.market_page.watchlist_card.value_label.text() == "2"

    window.market_orchestrator._worker = None
    window.market_orchestrator.stop_polling()
    window.market_orchestrator._render_controls()
