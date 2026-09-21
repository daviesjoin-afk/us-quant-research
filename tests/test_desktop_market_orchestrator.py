"""Behavioural tests for ``MarketOrchestrator`` itself.

The window's wiring tests prove the *signals are connected*; these prove the
orchestrator is the thing that decides.  Everything here drives the
orchestrator directly with fakes, so a failure points at the runtime rather
than at the composition around it.

Two properties get the most attention because they are the ones a careless
extraction would break:

* **one feed at a time.**  A switch stops the old worker before starting the
  new one, and a stale worker's ``finished`` must not clear its successor.
* **the poll is a fallback.**  A push keeps the timer idle; the timer only
  reads the application when nothing was pushed recently.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.orchestration.market import orchestrator as module
from us_quant.desktop_v2.orchestration.market.models import (
    MarketReadinessInputs,
    MarketRuntimeEvent,
    MarketShellHealthView,
)
from us_quant.desktop_v2.orchestration.market.orchestrator import (
    MAX_SUBSCRIPTION_SYMBOLS,
    MarketOrchestrator,
)
from us_quant.desktop_v2.pages.market import presenter
from us_quant.desktop_v2.pages.market.models import (
    MarketControlView,
    MarketPageView,
    MarketSubscriptionDraft,
)
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)


_APP = QApplication.instance() or QApplication([])
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


# -- fakes ---------------------------------------------------------------


class _Signal:
    """Stands in for a Qt signal's ``connect``; nothing is emitted from here."""

    def __init__(self) -> None:
        self.slots: list = []

    def connect(self, slot) -> None:
        self.slots.append(slot)

    def fire(self, *args: object) -> None:
        for slot in self.slots:
            slot(*args)


class _FakeWorker(QObject):
    """A worker whose ``isRunning`` follows ``start``/``wait`` exactly."""

    snapshot_ready = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self, *, source_id: str = "ibkr", market_exchange: str = "SMART"
    ) -> None:
        super().__init__()
        self.source_id = source_id
        self.market_exchange = market_exchange
        self._running = False
        self.started = False
        self.requested = False
        self.stop_confirms = True
        self.deleted = False

    def isRunning(self) -> bool:  # noqa: N802 - Qt spelling
        return self._running

    def start(self) -> None:
        self.started = True
        self._running = True

    def request_stop(self) -> None:
        self.requested = True

    def wait(self, _timeout: int) -> bool:
        if self.stop_confirms:
            self._running = False
        return self.stop_confirms

    def deleteLater(self) -> None:  # noqa: N802 - Qt spelling
        self.deleted = True


class _FakePage(QObject):
    """A recorder for the page's render entry points."""

    def __init__(self) -> None:
        super().__init__()
        self.views: list[MarketPageView] = []
        self.controls: list[MarketControlView] = []
        self.health: list[str] = []
        self.failures: list[str] = []
        self.scopes: list[str] = []
        self.symbols: tuple[str, ...] = ()
        self.provider = "ibkr"
        self.palette = None

    def render(self, view: MarketPageView) -> None:
        self.views.append(view)

    def render_controls(
        self, controls: MarketControlView, *, watchlist: str | None = None
    ) -> None:
        self.controls.append(controls)

    def render_health(self, text: str) -> None:
        self.health.append(text)

    def render_failure(self, message: str) -> None:
        self.failures.append(message)

    def render_scope(self, scope: str) -> None:
        self.scopes.append(scope)

    def set_subscription_symbols(self, symbols: tuple[str, ...]) -> None:
        self.symbols = tuple(symbols)

    def set_selected_provider(self, source_id: str) -> None:
        self.provider = source_id

    def subscription_symbols(self) -> tuple[str, ...]:
        return self.symbols

    def selected_provider(self) -> str:
        return self.provider

    def subscription_draft(self) -> MarketSubscriptionDraft:
        return MarketSubscriptionDraft(self.provider, self.symbols)


class _FakeCredentials:
    def __init__(self) -> None:
        self.resolved = 0

    def resolve_stream_credentials(self):
        from us_quant.desktop_credentials import StreamCredentials

        self.resolved += 1
        return StreamCredentials(
            finnhub_api_key="f", alpaca_api_key="a", alpaca_api_secret="s"
        )


class _FakeMarketData:
    """Stands in for ``MarketDataApplication`` with the three calls we make."""

    def __init__(self, *, desired: str = "SMART") -> None:
        self.desired = desired
        self.snapshots: list[MarketSnapshot] = []
        self.polls = 0

    def snapshot(self) -> MarketSnapshot:
        self.polls += 1
        if self.snapshots:
            return self.snapshots[-1]
        return _snapshot()

    def desired_market_exchange(self, _source_id: str) -> str:
        return self.desired


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


def _snapshot(*, quotes=None, error_code=None, message="") -> MarketSnapshot:
    return MarketSnapshot(
        generation=1,
        connected=True,
        ready=True,
        reconnect_attempt=0,
        quotes=tuple(quotes) if quotes is not None else (_quote(),),
        error_code=error_code,
        message=message,
        observed_at=NOW,
        source_id="alpaca_iex",
        source_label="Alpaca",
        coverage="IEX",
    )


class _Harness:
    """One orchestrator plus the recorders it published to."""

    def __init__(self, monkeypatch, *, symbols=("SPY",), provider="ibkr"):
        self.page = _FakePage()
        self.credentials = _FakeCredentials()
        self.market_data = _FakeMarketData()
        self.workers: list[_FakeWorker] = []

        def build(_application, request) -> _FakeWorker:
            worker = _FakeWorker(source_id=request.source_id)
            self.workers.append(worker)
            return worker

        monkeypatch.setattr(module, "StreamWorker", build)
        self.orchestrator = MarketOrchestrator(
            market_data=self.market_data,
            credential_service=self.credentials,
            page=self.page,
        )
        self.page.set_subscription_symbols(tuple(symbols))
        self.page.set_selected_provider(provider)

        self.snapshots: list[object] = []
        self.invalidated: list[int] = []
        self.health: list[MarketShellHealthView] = []
        self.events: list[MarketRuntimeEvent] = []
        self.logs: list[str] = []
        self.failures: list[str] = []
        self.refusals: list[tuple[str, str]] = []
        self.rotations: list[str] = []
        self.controls: list[int] = []
        self.settings: list[bool] = []
        self.orchestrator.snapshot_changed.connect(self.snapshots.append)
        self.orchestrator.snapshot_invalidated.connect(
            lambda: self.invalidated.append(1)
        )
        self.orchestrator.shell_health_changed.connect(self.health.append)
        self.orchestrator.runtime_event_requested.connect(self.events.append)
        self.orchestrator.log_requested.connect(self.logs.append)
        self.orchestrator.task_failure_requested.connect(self.failures.append)
        self.orchestrator.refused.connect(
            lambda title, message: self.refusals.append((title, message))
        )
        self.orchestrator.automatic_switch_requested.connect(
            self.rotations.append
        )
        self.orchestrator.controls_changed.connect(
            lambda: self.controls.append(1)
        )
        self.orchestrator.connection_settings_enabled_changed.connect(
            self.settings.append
        )

    @property
    def worker(self) -> _FakeWorker:
        return self.workers[-1]

    def codes(self) -> list[str]:
        return [event.code for event in self.events]


@pytest.fixture()
def harness(monkeypatch):
    built = _Harness(monkeypatch)
    yield built
    built.orchestrator.stop_polling()
    built.orchestrator.deleteLater()


# -- start ---------------------------------------------------------------


def test_a_start_builds_a_worker_from_the_selected_provider(harness) -> None:
    harness.page.set_selected_provider("alpaca_iex")
    harness.page.set_subscription_symbols(("SPY", "QQQ"))

    harness.orchestrator.start()

    worker = harness.worker
    assert worker.started
    assert worker.source_id == "alpaca_iex"
    assert harness.credentials.resolved == 1
    assert harness.orchestrator.is_live
    assert harness.orchestrator.active_source_id == "alpaca_iex"


def test_a_start_draws_the_connecting_state_before_the_worker_runs(
    harness,
) -> None:
    """The route must not keep claiming it is idle once a worker exists."""

    harness.orchestrator.start()

    assert harness.page.views
    view = harness.page.views[0]
    assert view.connection.value == "连接中"
    assert view.empty_message == presenter.CONNECTING_EMPTY


def test_a_start_publishes_the_controls_after_the_thread_starts(
    harness,
) -> None:
    """The ordering Execution v2 fixed, asserted on the orchestrator's side.

    The route's stop-stream control reads "is a worker running", so a control
    published before ``start()`` would leave it disabled for every start.
    """

    observed: list[bool] = []
    real = harness.orchestrator._render_controls

    def record() -> None:
        observed.append(harness.orchestrator.is_live)
        real()

    harness.orchestrator._render_controls = record  # type: ignore[assignment]
    harness.orchestrator.start()

    assert observed and all(observed), observed
    assert harness.orchestrator.polling_active


def test_a_start_without_symbols_is_refused(harness) -> None:
    harness.page.set_subscription_symbols(())

    harness.orchestrator.start()

    assert harness.refusals == [("无法启动", "至少填写一个行情代码")]
    assert harness.workers == []
    assert not harness.orchestrator.is_live


def test_a_start_over_the_subscription_ceiling_is_refused(harness) -> None:
    harness.page.set_subscription_symbols(
        tuple(f"S{i}" for i in range(MAX_SUBSCRIPTION_SYMBOLS + 1))
    )

    harness.orchestrator.start()

    assert harness.refusals == [("无法启动", "首期最多订阅 30 个代码")]
    assert harness.workers == []


def test_a_credential_failure_is_reported_and_starts_nothing(
    harness, monkeypatch
) -> None:
    from us_quant.trading.ports.market_data import MarketDataCredentialsError

    def explode(**_kwargs):
        raise MarketDataCredentialsError("缺少凭据")

    monkeypatch.setattr(
        harness.credentials, "resolve_stream_credentials", explode
    )

    harness.orchestrator.start()

    assert harness.failures == ["缺少凭据"]
    assert harness.workers == []
    assert not harness.orchestrator.is_live


def test_a_second_start_while_live_switches_instead(harness) -> None:
    """The start control doubles as switch/reconnect once a feed is live."""

    harness.orchestrator.start()
    harness.page.set_selected_provider("alpaca_iex")

    harness.orchestrator.start()

    assert len(harness.workers) == 2
    assert harness.worker.source_id == "alpaca_iex"


def test_a_start_emits_one_runtime_event_and_one_log(harness) -> None:
    harness.orchestrator.start()

    assert harness.codes() == ["STREAM_START"]
    assert harness.logs == ["ibkr 只读流行情正在连接…"]


def test_the_settings_page_closes_while_a_feed_is_held(harness) -> None:
    harness.orchestrator.start()

    assert harness.settings[0] is False


# -- push and poll -------------------------------------------------------


def test_a_pushed_snapshot_is_stored_rendered_and_fanned_out(harness) -> None:
    harness.orchestrator.start()
    snapshot = _snapshot()

    harness.worker.snapshot_ready.emit(snapshot)
    _APP.processEvents()

    assert harness.orchestrator.snapshot is snapshot
    assert harness.snapshots == [snapshot]
    assert harness.health
    assert harness.page.views[-1].connection.value == "已握手"
    assert harness.page.views[-1].readiness.value == "订阅合计 1/1"


def test_a_non_snapshot_push_is_ignored(harness) -> None:
    """A stray payload must not become the market truth."""

    harness.orchestrator.start()

    harness.worker.snapshot_ready.emit(object())
    _APP.processEvents()

    assert harness.orchestrator.snapshot is None
    assert harness.snapshots == []


def test_the_poll_is_idle_right_after_a_push(harness) -> None:
    """Latency must be bounded by the feed, not by the poll interval."""

    harness.orchestrator.start()
    harness.worker.snapshot_ready.emit(_snapshot())
    _APP.processEvents()
    before = harness.market_data.polls

    harness.orchestrator._poll()

    assert harness.market_data.polls == before


def test_the_poll_reads_the_application_when_nothing_was_pushed(
    harness,
) -> None:
    harness.orchestrator.start()

    harness.orchestrator._poll()

    assert harness.market_data.polls == 1
    assert harness.snapshots


def test_the_poll_does_nothing_without_a_running_worker(harness) -> None:
    harness.orchestrator._poll()

    assert harness.market_data.polls == 0
    assert harness.snapshots == []


def test_an_error_snapshot_is_recorded_once_per_key(harness) -> None:
    """Reconnect generations must not flood the log with one entry per retry."""

    harness.orchestrator.start()
    failing = _snapshot(error_code=10197, message="无实时订阅")

    harness.orchestrator._on_snapshot(failing)
    harness.orchestrator._on_snapshot(failing)

    assert harness.codes() == ["STREAM_START", "10197"]
    assert len(harness.events) == 2


def test_a_recovered_feed_may_report_the_same_error_again(harness) -> None:
    harness.orchestrator.start()
    failing = _snapshot(error_code=10197, message="无实时订阅")

    harness.orchestrator._on_snapshot(failing)
    harness.orchestrator._on_snapshot(_snapshot())
    harness.orchestrator._on_snapshot(failing)

    assert harness.codes() == ["STREAM_START", "10197", "10197"]


def test_a_failed_feed_is_drawn_and_reported(harness) -> None:
    harness.orchestrator.start()

    harness.worker.failed.emit("socket exploded")
    _APP.processEvents()

    assert harness.page.failures == ["socket exploded"]
    assert harness.health[-1].market_text == "行情 · 流服务失败"
    assert harness.codes()[-1] == "STREAM_FAILED"


# -- stop ----------------------------------------------------------------


def test_a_stop_invalidates_the_snapshot_and_releases_the_worker(
    harness,
) -> None:
    harness.orchestrator.start()
    harness.worker.snapshot_ready.emit(_snapshot())
    _APP.processEvents()

    assert harness.orchestrator.stop() is True

    assert harness.worker.requested
    assert not harness.orchestrator.is_live
    assert harness.orchestrator.active_source_id is None
    assert harness.orchestrator.stop_reason == "行情流正在停止"
    assert harness.invalidated
    assert harness.settings[-1] is True


def test_a_stop_marks_the_last_quotes_stale(harness) -> None:
    """The grid must not keep showing live quotes under a stopped header."""

    harness.orchestrator.start()
    harness.worker.snapshot_ready.emit(_snapshot())
    _APP.processEvents()

    harness.orchestrator.stop()

    snapshot = harness.orchestrator.snapshot
    assert snapshot is not None
    assert snapshot.connected is False
    assert snapshot.ready is False
    assert all(quote.stale for quote in snapshot.quotes)
    assert all(
        quote.stale_reason == "行情流正在停止" for quote in snapshot.quotes
    )


def test_a_stop_that_does_not_confirm_exit_keeps_the_gate_closed(
    harness,
) -> None:
    harness.orchestrator.start()
    harness.worker.stop_confirms = False

    assert harness.orchestrator.stop() is False

    assert not harness.orchestrator.is_live
    assert not harness.orchestrator.polling_active
    assert harness.codes()[-1] == "STREAM_STOP_PENDING"
    assert harness.page.health[-1].startswith("停止中")
    assert harness.worker in harness.workers


def test_a_timed_out_stop_separates_the_feed_from_the_thread(harness) -> None:
    """Two liveness facts, and the timeout is exactly where they diverge.

    A stop that did not confirm leaves the feed unusable while the network
    thread is still alive.  If ``is_live`` were also the thread's answer, the
    supervisor's liveness probe and ``closeEvent``'s last check would both
    report a clean shutdown over a running worker.
    """

    harness.orchestrator.start()
    harness.worker.stop_confirms = False

    assert harness.orchestrator.stop() is False

    assert not harness.orchestrator.is_live
    assert harness.orchestrator.worker_running


def test_the_worker_fact_starts_false_and_follows_the_thread(harness) -> None:
    """The worker fact is about the thread, and only about the thread."""

    assert not harness.orchestrator.worker_running

    harness.orchestrator.start()

    assert harness.orchestrator.worker_running
    assert harness.orchestrator.is_live


def test_the_worker_fact_clears_when_the_thread_really_ends(harness) -> None:
    """A thread that exits on its own clears the fact with no stop involved."""

    harness.orchestrator.start()
    harness.worker._running = False

    harness.worker.finished.emit()
    _APP.processEvents()

    assert not harness.orchestrator.worker_running
    assert not harness.orchestrator.is_live


def test_a_stuck_stop_leaves_the_worker_installed(harness) -> None:
    """A stop that did not confirm must not pretend the worker is gone.

    Clearing it would let the next start build a second feed while the first
    thread is still winding down.
    """

    harness.orchestrator.start()
    harness.worker.stop_confirms = False

    harness.orchestrator.stop()

    assert harness.orchestrator.active_source_id == "ibkr"


def test_a_stop_with_no_worker_is_a_no_op(harness) -> None:
    assert harness.orchestrator.stop() is True
    assert harness.invalidated == []


def test_a_stop_emits_the_stop_event_and_log(harness) -> None:
    harness.orchestrator.start()

    harness.orchestrator.stop()

    assert harness.codes()[-1] == "STREAM_STOP"
    assert harness.orchestrator.stop_reason == "行情流正在停止"


# -- provider switch -----------------------------------------------------


def test_a_switch_stops_the_current_feed_then_starts_the_pending_one(
    harness,
) -> None:
    """The order matters: two live workers would mean two feeds."""

    harness.orchestrator.start()
    first = harness.worker

    harness.orchestrator.request_switch("alpaca_iex")

    assert first.requested
    assert not first.isRunning()
    assert len(harness.workers) == 2
    assert harness.worker.started
    assert harness.worker.source_id == "alpaca_iex"
    assert harness.orchestrator.active_source_id == "alpaca_iex"


def test_a_switch_without_symbols_is_refused(harness) -> None:
    harness.orchestrator.start()
    harness.page.set_subscription_symbols(())

    harness.orchestrator.request_switch("alpaca_iex")

    assert harness.refusals
    assert harness.refusals[-1][0] == "无法切换行情"


def test_a_switch_to_an_unknown_provider_is_refused(harness) -> None:
    harness.orchestrator.start()

    harness.orchestrator.request_switch("bogus")

    assert harness.refusals[-1] == ("无法切换行情", "不支持的数据源：bogus")
    assert harness.orchestrator.active_source_id == "ibkr"


def test_a_switch_that_cannot_stop_does_not_start_the_new_provider(
    harness,
) -> None:
    """The pending switch must stay pending, not run against a dying worker."""

    harness.orchestrator.start()
    harness.worker.stop_confirms = False

    harness.orchestrator.request_switch("alpaca_iex")

    assert len(harness.workers) == 1
    assert harness.orchestrator.active_source_id == "ibkr"


def test_a_stale_worker_finish_does_not_clear_its_successor(harness) -> None:
    """The classic double-feed bug: an old thread's ``finished``.

    The old worker's thread ends *after* the new one is live, and its signal
    must be dropped rather than clearing the current worker.
    """

    harness.orchestrator.start()
    stale = harness.worker
    harness.orchestrator.request_switch("alpaca_iex")
    current = harness.worker
    assert current is not stale

    stale.finished.emit()
    _APP.processEvents()

    assert harness.orchestrator._worker is current
    assert harness.orchestrator.is_live


def test_a_finish_with_no_worker_installed_still_releases_the_route(
    harness,
) -> None:
    harness.orchestrator.start()

    harness.orchestrator._worker.finished.emit()
    _APP.processEvents()

    assert harness.orchestrator.snapshot is None
    assert not harness.orchestrator.polling_active
    assert harness.settings[-1] is True


# -- readiness inputs and the recency cache ------------------------------


def test_readiness_inputs_are_counted_into_the_breakdown(harness) -> None:
    harness.orchestrator.set_readiness_inputs(
        MarketReadinessInputs(
            candidate_symbols=("SPY",), reference_symbols=("QQQ",)
        )
    )
    harness.orchestrator.start()
    harness.worker.snapshot_ready.emit(
        _snapshot(quotes=(_quote(symbol="SPY"), _quote(symbol="QQQ")))
    )
    _APP.processEvents()

    # The readiness card reads "1/1" for the candidate when its quote is fresh.
    assert harness.page.views[-1].readiness.value != "—"


def test_a_fresh_symbol_is_recently_ready(harness) -> None:
    harness.orchestrator.start()
    harness.orchestrator._on_snapshot(_snapshot())

    assert harness.orchestrator.was_recently_ready("SPY")
    assert harness.orchestrator.recently_ready_symbols() == ("SPY",)


def test_a_symbol_leaves_the_cache_when_it_is_unsubscribed(harness) -> None:
    """Otherwise a removed symbol would keep answering "recently ready"."""

    harness.orchestrator.start()
    harness.orchestrator._on_snapshot(_snapshot(quotes=(_quote(symbol="SPY"),)))
    assert harness.orchestrator.was_recently_ready("SPY")

    harness.orchestrator._on_snapshot(_snapshot(quotes=(_quote(symbol="QQQ"),)))

    assert not harness.orchestrator.was_recently_ready("SPY")
    assert harness.orchestrator.was_recently_ready("QQQ")


def test_a_stale_quote_does_not_refresh_the_cache(harness) -> None:
    harness.orchestrator.start()
    harness.orchestrator._on_snapshot(
        _snapshot(quotes=(_quote(stale=True, stale_reason="超时"),))
    )

    assert not harness.orchestrator.was_recently_ready("SPY")


def test_a_new_feed_forgets_the_previous_cache(harness) -> None:
    """A symbol ready under the old feed is not ready under the new one."""

    harness.orchestrator.start()
    harness.orchestrator._on_snapshot(_snapshot())
    assert harness.orchestrator.was_recently_ready("SPY")

    harness.orchestrator.request_switch("alpaca_iex")

    assert not harness.orchestrator.was_recently_ready("SPY")


# -- extended session rotation -------------------------------------------


def test_a_venue_move_requests_an_automatic_switch(harness) -> None:
    """The rotation asks; it must not perform the switch itself.

    Performing it here would bypass the window's Paper interlock, which this
    class is not allowed to know about.
    """

    harness.orchestrator.start()
    harness.worker.source_id = "ibkr_extended"
    harness.worker.market_exchange = "SMART"
    harness.market_data.desired = "OVERNIGHT"

    harness.orchestrator.maybe_request_extended_session_rotation()

    assert harness.rotations == ["ibkr_extended"]
    assert harness.orchestrator._pending is None
    assert harness.orchestrator._worker is harness.worker


def test_a_stable_venue_requests_nothing(harness) -> None:
    harness.orchestrator.start()
    harness.worker.source_id = "ibkr_extended"
    harness.worker.market_exchange = "SMART"
    harness.market_data.desired = "SMART"

    harness.orchestrator.maybe_request_extended_session_rotation()

    assert harness.rotations == []


def test_a_pending_switch_suppresses_the_rotation(harness) -> None:
    """A switch already in flight must not be restarted by the session check."""

    harness.orchestrator.start()
    harness.worker.source_id = "ibkr_extended"
    harness.worker.market_exchange = "SMART"
    harness.market_data.desired = "OVERNIGHT"
    harness.orchestrator._pending = ("ibkr_extended", ("SPY",))

    harness.orchestrator.maybe_request_extended_session_rotation()

    assert harness.rotations == []


def test_an_ordinary_source_is_never_rotated(harness) -> None:
    harness.orchestrator.start()
    harness.worker.source_id = "alpaca_iex"
    harness.market_data.desired = "OVERNIGHT"

    harness.orchestrator.maybe_request_extended_session_rotation()

    assert harness.rotations == []


# -- inputs and lifecycle commands ---------------------------------------


def test_the_scope_line_reaches_the_page(harness) -> None:
    harness.orchestrator.set_scope("范围分层 · 官方美股/ETF 6,000")

    assert harness.page.scopes == ["范围分层 · 官方美股/ETF 6,000"]


def test_writing_the_subscription_republishes_the_controls(harness) -> None:
    """The route's own control strip follows a subscription write.

    It is the *page* that is republished, not the execution route: the legacy
    path did not republish execution controls on a subscription change, and
    this round preserves that.
    """

    before = len(harness.page.controls)

    harness.orchestrator.set_subscription_symbols(("AAPL",), note="测试")

    assert harness.page.symbols == ("AAPL",)
    assert len(harness.page.controls) > before
    assert harness.controls == []


def test_writing_the_provider_emits_no_intent(harness) -> None:
    """A programmatic write is not the operator choosing."""

    seen: list[str] = []
    harness.orchestrator.snapshot_changed.connect(seen.append)

    harness.orchestrator.set_selected_provider("alpaca_iex")

    assert harness.page.provider == "alpaca_iex"
    assert seen == []


def test_stop_polling_releases_the_timer(harness) -> None:
    harness.orchestrator.start()
    assert harness.orchestrator.polling_active

    harness.orchestrator.stop_polling()

    assert not harness.orchestrator.polling_active


def test_the_control_view_closes_the_stop_while_one_is_pending(harness) -> None:
    """A second stop against a dying worker must not be admitted."""

    harness.orchestrator.start()
    harness.worker.stop_confirms = False

    harness.orchestrator.stop()

    assert harness.page.controls[-1].stop_enabled is False
