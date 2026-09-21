"""Wiring tests: the real window's market route, end to end.

The page's own tests prove it emits; these prove the *window* is listening.  A
signal nobody connected is the classic way a UI migration ships a dead button,
and it is invisible to a test that only checks the page.

The clicks are driven on a real ``MainWindow`` with the handlers replaced by
recorders.  Replacing them is not a shortcut around the wiring -- the wiring is
exactly what is being checked -- it is what keeps the test from reaching a feed:
the handlers under test are the ones that would *start* a stream, and the page
cannot run them itself.

Two further properties live here because they are neither page nor presenter
facts.  One is the ordering the Execution v2 round fixed: the execution controls
must be published *after* ``worker.start()``, or the route's stop-stream control
reads "no worker" and disables itself on the very click that started one.  The
other is the set of safety gates around ``_stop_stream``, which the migration
must not weaken.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import date, datetime, timezone

from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.desktop_v2.pages.market import presenter
from us_quant.scanner import MarketScan, ScanResult


_APP = QApplication.instance() or QApplication([])

#: Each page signal and the window method that must receive it.  The start and
#: stop entries are the window's cross-workflow bridges, not the orchestrator's
#: methods: the gates that decide whether a stop or switch is allowed live on
#: the window, so the page signals land there first.
EXPECTED_WIRING = (
    ("provider_selected", "_stream_provider_selected"),
    ("start_requested", "_request_market_start"),
    ("stop_requested", "_request_market_stop"),
    ("load_scan_watchlist_requested", "_apply_intraday_watchlist"),
)


def _window() -> MainWindow:
    window = MainWindow()
    _APP.processEvents()
    return window


def test_the_route_table_points_at_the_market_page() -> None:
    """The shell must hold the page, not a builder's return value."""

    window = _window()
    try:
        assert window.market_page is not None
        assert window.market_page.__class__.__name__ == "MarketPage"
    finally:
        window.close()
        window.deleteLater()


def test_every_market_intent_has_a_handler_on_both_sides() -> None:
    window = _window()
    try:
        for signal, handler in EXPECTED_WIRING:
            assert hasattr(MainWindow, handler), handler
            assert hasattr(window.market_page, signal), signal
    finally:
        window.close()
        window.deleteLater()


def test_a_real_click_reaches_the_window_handler(monkeypatch) -> None:
    """Every market control must land on its handler, once, in order.

    The connections are rebuilt against recorders after disconnecting the real
    ones, so this asserts the page's signals and the window's table agree --
    which is the property a dead button violates.
    """

    window = _window()
    try:
        page = window.market_page
        seen: list[str] = []
        for _signal, handler in EXPECTED_WIRING:
            # ``provider_selected`` carries an id, so the recorder has to
            # accept the signal's argument and still record the handler name.
            monkeypatch.setattr(
                window,
                handler,
                lambda *_args, handler=handler: seen.append(handler),
            )
        for signal, _handler in EXPECTED_WIRING:
            getattr(page, signal).disconnect()
        window._connect_market_page()

        # The idle state: start and the watchlist load are open, stop is not.
        page.controls.start_button.click()
        page.controls.load_watchlist_button.click()
        # A running worker: stop opens and the watchlist load closes, which is
        # why the two clicks cannot share one control state.
        page.render_controls(_running_controls())
        page.controls.stop_button.click()
        index = page.controls.provider_combo.findData("alpaca_iex")
        page.controls.provider_combo.setCurrentIndex(index)

        assert seen == [
            "_request_market_start",
            "_apply_intraday_watchlist",
            "_request_market_stop",
            "_stream_provider_selected",
        ]
    finally:
        window.close()
        window.deleteLater()


def _running_controls():
    """The control state that opens the stop, as the window would publish it."""

    from us_quant.desktop_v2.pages.market.presenter import control_view

    return control_view(
        worker_running=True,
        stop_pending=False,
        symbols_enabled=False,
        provider_enabled=True,
    )


# -- the provider sync ----------------------------------------------------


def test_a_page_provider_choice_syncs_the_settings_combo() -> None:
    """Choosing on the market route must not leave settings showing another."""

    window = _window()
    try:
        combo = window.market_page.controls.provider_combo
        combo.setCurrentIndex(combo.findData("alpaca_iex"))
        _APP.processEvents()

        assert (
            window.settings_page.current_draft().market_provider
            == "alpaca_iex"
        )
    finally:
        window.close()
        window.deleteLater()


def test_a_settings_provider_choice_syncs_the_page_without_recursing() -> None:
    """The two combos must not drive each other in a loop.

    The settings handler writes the page through the *silent* setter, so the
    page must end up on the new provider while reporting no operator intent.
    """

    window = _window()
    try:
        seen: list[str] = []
        window.market_page.provider_selected.connect(seen.append)

        combo = window.settings_page.appearance.provider_combo
        index = combo.findData("ibkr_extended")
        assert index >= 0
        combo.setCurrentIndex(index)
        _APP.processEvents()

        assert window.market_page.selected_provider() == "ibkr_extended"
        assert seen == []
    finally:
        window.close()
        window.deleteLater()


def test_a_saved_settings_preference_lands_on_the_page(
    monkeypatch, tmp_path
) -> None:
    """Saving settings is a programmatic write, not an operator choice.

    The window must not reach into the market route's combo to restore the
    saved provider; it hands the page the id and the page's silent setter
    points the combo, so the two combos cannot drive each other.
    """

    from us_quant.paths import STATE_ROOT_ENV

    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    window = _window()
    try:
        seen: list[str] = []
        window.market_page.provider_selected.connect(seen.append)

        combo = window.settings_page.appearance.provider_combo
        index = combo.findData("alpaca_iex")
        assert index >= 0
        combo.setCurrentIndex(index)
        window._save_user_preferences(
            window.settings_page.current_draft()
        )

        assert window.market_page.selected_provider() == "alpaca_iex"
        assert seen == []
    finally:
        window.close()
        window.deleteLater()


# -- subscription writes --------------------------------------------------


def test_loading_the_scan_watchlist_writes_the_page_field() -> None:
    """``_apply_intraday_watchlist`` writes through the market orchestrator."""

    window = _window()
    try:
        window.scan = _scan_result()
        window.market_page.set_subscription_symbols(())

        window._apply_intraday_watchlist()

        assert window.market_page.subscription_symbols() == ("S00", "S01")
        # The note is asserted on the rendered card rather than through the
        # orchestrator's private state: what the operator reads is the fact.
        assert window.market_page.watchlist_card.note_label.text() == (
            "实时订阅子集；不限制研究或交易范围"
        )
        # And the watchlist card follows, through the page's own render.
        assert window.market_page.watchlist_card.value_label.text() == "2"
    finally:
        window.close()
        window.deleteLater()


def test_load_watchlist_is_refused_while_a_stream_runs() -> None:
    window = _window()
    try:
        window.scan = _scan_result()
        window.market_page.set_subscription_symbols(())
        window.market_orchestrator._worker = _FakeWorker(running=True)

        window._apply_intraday_watchlist()

        assert window.market_page.subscription_symbols() == ()
    finally:
        window.market_orchestrator._worker = None
        window.close()
        window.deleteLater()


def test_the_targeted_symbol_path_writes_the_page_field(monkeypatch) -> None:
    """A targeted session subscribes one symbol, through the orchestrator.

    ``_sync_targeted_symbol_to_stream`` finishes by starting the stream, which
    fails closed without a credential; the message box is recorded so the test
    observes the write rather than blocking on a dialog.
    """

    window = _window()
    try:
        _capture_messages(monkeypatch)
        window.market_page.set_subscription_symbols(("AAPL", "MSFT"))
        window.targeted_validation_page.set_target_symbol("NVDA")

        window._sync_targeted_symbol_to_stream()

        assert window.market_page.subscription_symbols() == ("NVDA",)
        assert window.market_page.watchlist_card.note_label.text() == (
            "针对性日内 T：NVDA"
        )
    finally:
        window.market_orchestrator._worker = None
        window.close()
        window.deleteLater()


# -- start publish ordering (non-vacuity) --------------------------------


class _FakeWorker:
    """A worker whose ``isRunning`` flips exactly when ``start`` is called."""

    def __init__(self, *, running: bool) -> None:
        self._running = running
        self.started = False
        self.requested = False
        self.source_id = "ibkr"
        self.market_exchange = "SMART"
        self.snapshot_ready = _Signal()
        self.failed = _Signal()
        self.finished = _Signal()

    def isRunning(self) -> bool:  # noqa: N802 - Qt spelling
        return self._running

    def start(self) -> None:
        self.started = True
        self._running = True

    def request_stop(self) -> None:
        self.requested = True

    def wait(self, _timeout: int) -> bool:
        self._running = False
        return True

    def deleteLater(self) -> None:  # noqa: N802 - Qt spelling
        return None


class _Signal:
    """Stands in for a Qt signal's ``connect``; nothing is ever emitted."""

    def connect(self, *_args: object) -> None:
        return None


def test_the_execution_stop_stream_control_follows_the_started_worker(
    monkeypatch,
) -> None:
    """The ordering Execution v2 fixed, asserted from the market route's side.

    The orchestrator hands the worker over and then calls ``start()``.
    Publishing the execution controls *before* ``start()`` would leave the
    route's stop-stream control disabled for every direct start -- the control
    reads "is a worker running", and at that moment none is.  Swapping the two
    lines must fail this test.
    """

    window = _window()
    try:
        worker = _FakeWorker(running=False)
        monkeypatch.setattr(
            "us_quant.desktop_v2.orchestration.market.orchestrator.StreamWorker",
            lambda *a, **k: worker,
        )
        monkeypatch.setattr(
            window.credential_service,
            "resolve_stream_credentials",
            lambda **kwargs: _credentials(),
        )

        observed: list[bool] = []
        real_publish = window._publish_execution_controls

        def record() -> None:
            observed.append(window.market_orchestrator.is_live)
            real_publish()

        monkeypatch.setattr(window, "_publish_execution_controls", record)

        window.market_page.set_selected_provider("ibkr")
        window.market_page.set_subscription_symbols(("SPY",))
        window.market_orchestrator.start()

        assert worker.started, "the worker was never started"
        assert observed, "the controls were never published"
        # Every publish happened while a worker was running.
        assert all(observed), observed
        assert window.market_orchestrator.is_live
    finally:
        window.market_orchestrator._worker = None
        window.market_orchestrator.stop_polling()
        window.close()
        window.deleteLater()


def test_the_market_stop_control_follows_the_started_worker(
    monkeypatch,
) -> None:
    """The market controls must be published after the worker is live."""

    window = _window()
    try:
        worker = _FakeWorker(running=False)
        monkeypatch.setattr(
            "us_quant.desktop_v2.orchestration.market.orchestrator.StreamWorker",
            lambda *a, **k: worker,
        )
        monkeypatch.setattr(
            window.credential_service,
            "resolve_stream_credentials",
            lambda **kwargs: _credentials(),
        )

        observed: list[bool] = []
        real_publish = window.market_orchestrator._render_controls

        def record() -> None:
            observed.append(window.market_orchestrator.is_live)
            real_publish()

        monkeypatch.setattr(
            window.market_orchestrator, "_render_controls", record
        )

        window.market_page.set_selected_provider("ibkr")
        window.market_page.set_subscription_symbols(("SPY",))
        window.market_orchestrator.start()

        assert worker.started, "the worker was never started"
        assert observed, "the market controls were never published"
        assert all(observed), observed
        assert window.market_page.controls.stop_button.isEnabled()
    finally:
        window.market_orchestrator._worker = None
        window.market_orchestrator.stop_polling()
        window.close()
        window.deleteLater()


def test_the_market_start_publishes_the_connecting_state(
    monkeypatch,
) -> None:
    """A started worker with no snapshot yet must not render as idle."""

    window = _window()
    try:
        worker = _FakeWorker(running=False)
        monkeypatch.setattr(
            "us_quant.desktop_v2.orchestration.market.orchestrator.StreamWorker",
            lambda *a, **k: worker,
        )
        monkeypatch.setattr(
            window.credential_service,
            "resolve_stream_credentials",
            lambda **kwargs: _credentials(),
        )

        window.market_page.set_selected_provider("ibkr")
        window.market_page.set_subscription_symbols(("SPY", "QQQ"))
        window.market_orchestrator.start()

        assert worker.started
        assert window.market_page.connection_card.value_label.text() == "连接中"
        assert (
            window.market_page.empty_label.text()
            == presenter.CONNECTING_EMPTY
        )
        assert window.market_page.watchlist_card.value_label.text() == "2"
    finally:
        window.market_orchestrator._worker = None
        window.market_orchestrator.stop_polling()
        window.close()
        window.deleteLater()


def _credentials():
    from us_quant.desktop_credentials import StreamCredentials

    return StreamCredentials(
        finnhub_api_key="",
        alpaca_api_key="",
        alpaca_api_secret="",
    )


# -- the stop safety gates (characterization) ----------------------------
#
# The gates moved *shape* but not meaning.  They now live on the window's
# ``_stop_market_data`` / ``_request_market_switch`` bridges rather than inside
# ``_stop_stream``, because the market orchestrator may not name the Paper
# session or the Shadow book.  The behaviour is unchanged: a Paper session
# holding anything refuses an ordinary stop, an active Shadow book is stopped
# first, and the automatic session rotation is the one path allowed past the
# interlock.


class _Snapshot:
    """The only facts the stop gate reads off the session snapshot."""

    def __init__(
        self, *, active: bool = False, positions=(), pending=()
    ) -> None:
        self.active = active
        self.positions = positions
        self.pending_orders = pending


def test_an_active_paper_session_refuses_a_normal_stop(monkeypatch) -> None:
    """Stopping the feed under a live session would starve its exit gates."""

    window = _window()
    try:
        messages = _capture_messages(monkeypatch)
        worker = _FakeWorker(running=True)
        window.market_orchestrator._worker = worker
        window.auto_quant_snapshot = _Snapshot(active=True)

        refused = window._stop_market_data()

        assert refused is False
        assert len(messages) == 1
        assert messages[0][0] == "warning"
        # And the feed is still running: a refusal must not half-stop it.
        assert worker.requested is False
    finally:
        window.auto_quant_snapshot = None
        window.market_orchestrator._worker = None
        window.close()
        window.deleteLater()


def test_open_positions_refuse_a_normal_stop(monkeypatch) -> None:
    window = _window()
    try:
        messages = _capture_messages(monkeypatch)
        window.market_orchestrator._worker = _FakeWorker(running=True)
        window.auto_quant_snapshot = _Snapshot(
            positions=(object(),), pending=()
        )

        assert window._stop_market_data() is False
        assert messages
    finally:
        window.auto_quant_snapshot = None
        window.market_orchestrator._worker = None
        window.close()
        window.deleteLater()


def test_a_pending_order_refuses_a_normal_stop(monkeypatch) -> None:
    window = _window()
    try:
        _capture_messages(monkeypatch)
        window.market_orchestrator._worker = _FakeWorker(running=True)
        window.auto_quant_snapshot = _Snapshot(pending=(object(),))

        assert window._stop_market_data() is False
    finally:
        window.auto_quant_snapshot = None
        window.market_orchestrator._worker = None
        window.close()
        window.deleteLater()


def test_the_declared_auto_session_switch_path_is_still_allowed(
    monkeypatch,
) -> None:
    """The one exception the earlier round established must survive."""

    window = _window()
    try:
        _capture_messages(monkeypatch)
        worker = _FakeWorker(running=True)
        window.market_orchestrator._worker = worker
        window.auto_quant_snapshot = _Snapshot(active=True)
        window.market_page.set_subscription_symbols(("SPY",))
        # The automatic path runs the whole switch, which would build a real
        # feed once the fake worker reports itself stopped; the activation is
        # recorded instead so this test observes the interlock rather than
        # reaching a provider.
        activated: list[str] = []
        monkeypatch.setattr(
            window.market_orchestrator, "start", lambda: activated.append("start")
        )

        window._request_automatic_market_switch("ibkr")

        assert worker.requested is True
        assert activated == ["start"]
    finally:
        window.auto_quant_snapshot = None
        window.market_orchestrator._worker = None
        window.close()
        window.deleteLater()


def test_a_normal_switch_is_refused_while_a_paper_session_holds(
    monkeypatch,
) -> None:
    """The operator's switch obeys the interlock the automatic one bypasses."""

    window = _window()
    try:
        messages = _capture_messages(monkeypatch)
        worker = _FakeWorker(running=True)
        window.market_orchestrator._worker = worker
        window.auto_quant_snapshot = _Snapshot(active=True)
        window.market_page.set_subscription_symbols(("SPY",))

        window._request_market_switch("ibkr")

        assert worker.requested is False
        assert len(messages) == 1
    finally:
        window.auto_quant_snapshot = None
        window.market_orchestrator._worker = None
        window.close()
        window.deleteLater()


def test_an_active_shadow_session_is_stopped_first(monkeypatch) -> None:
    """A market stop must take the internal shadow book down with it."""

    window = _window()
    try:
        _capture_messages(monkeypatch)
        stopped: list[str] = []

        class _Engine:
            active = True

        window.shadow_engine = _Engine()
        monkeypatch.setattr(
            window, "_stop_shadow", lambda: stopped.append("shadow")
        )

        window.market_orchestrator._worker = _FakeWorker(running=True)

        assert window._stop_market_data() is True
        assert stopped == ["shadow"]
    finally:
        window.shadow_engine = None
        window.market_orchestrator._worker = None
        window.close()
        window.deleteLater()


def test_a_stuck_stop_keeps_the_start_gate_closed(monkeypatch) -> None:
    """A stop that does not confirm exit must not admit a second start."""

    window = _window()
    try:
        _capture_messages(monkeypatch)

        class _Stuck(_FakeWorker):
            def request_stop(self) -> None:
                return None

            def wait(self, _timeout: int) -> bool:
                return False

        window.market_orchestrator._worker = _Stuck(running=True)

        assert window._stop_market_data() is False
        assert not window.market_orchestrator.is_live
        # And the page is told, so its stop control closes.
        assert not window.market_page.controls.stop_button.isEnabled()
    finally:
        window.market_orchestrator._worker = None
        window.market_orchestrator._stop_pending = False
        window.close()
        window.deleteLater()


def _capture_messages(monkeypatch) -> list[tuple]:
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


def _scan_result() -> MarketScan:
    """A minimal scan the watchlist selector accepts.

    Two affordable tier-1 rows, both under the small-account position cap, so
    the selector returns them without needing reference symbols.
    """

    return MarketScan(
        generated_at=datetime(2026, 9, 20, 14, 30, tzinfo=timezone.utc),
        capital=1500.0,
        data_date=date(2026, 9, 19),
        results=(
            _scan_row("S00"),
            _scan_row("S01"),
        ),
        skipped={},
    )


def _scan_row(symbol: str) -> ScanResult:
    return ScanResult(
        symbol=symbol,
        execution_symbol=symbol,
        name=f"{symbol} Inc",
        sector="科技",
        leader_tier=1,
        security_type="STK",
        trading_date=date(2026, 9, 19),
        close=50.0,
        execution_price=50.0,
        whole_share_capacity=30,
        average_dollar_volume_20d=100_000_000.0,
        return_20d=0.02,
        return_63d=0.05,
        volatility_20d=0.2,
        drawdown_252d=-0.1,
        rsi_14d=55,
        atr_pct_14d=0.02,
        above_sma_50=True,
        above_sma_200=True,
        score=90.0,
        signal="趋势候选",
        research_eligible=True,
        trade_eligible=True,
        reason="test",
    )
