"""Market orchestration: the one owner of the market runtime.

Before this module the market route's runtime truth was spread across
``MainWindow`` -- the worker, the snapshot, the poll timer, the pending switch,
the readiness cache, the scope line and the whole page render all lived on the
window and were touched from a dozen handlers.  This class now owns them.

The boundary it draws is the point of the extraction:

* it owns the **market runtime**: the ``StreamWorker``, the snapshot, the poll
  timer, the pending provider switch, the recently-ready cache and the render of
  the market page (the last two through :class:`MarketRenderer`);
* it does **not** own any cross-workflow decision.  Whether a stop or a switch
  is *allowed* depends on a Paper session holding positions and on an active
  Shadow book -- neither of which this class may name.  Those gates stay on the
  window and call in here once they have decided;
* it does **not** own the shell.  Global badges are published as a
  ``MarketShellHealthView`` fact and painted by the window, and runtime events
  are *requested* rather than written, so ``Market -> System`` never becomes a
  dependency.

The window injects exactly three things (see ``__init__``) and reads back only
finished facts: ``snapshot``, ``is_live``, ``worker_running``,
``active_source_id``, ``active_market_exchange``, ``stop_reason`` and
``was_recently_ready``.  The worker, the timer, the pending switch and the
readiness dict stay private -- a caller that could reach the worker would be
a second owner of the feed.
"""

from __future__ import annotations

from time import monotonic

from PySide6.QtCore import QObject, QTimer, Signal

from us_quant.desktop_credentials import DesktopCredentialService
from us_quant.desktop_v2.orchestration.market.health import ShellHealthPublisher
from us_quant.desktop_v2.orchestration.market.models import (
    MarketReadinessInputs,
    MarketRuntimeEvent,
)
from us_quant.desktop_v2.orchestration.market.renderer import MarketRenderer
from us_quant.desktop_v2.pages.market.controls import VALID_MARKET_SOURCES
from us_quant.desktop_workers import StreamWorker
from us_quant.trading.application.market_data import (
    SOURCE_IBKR_EXTENDED,
    MarketDataApplication,
    MarketDataCredentials,
    MarketDataStartRequest,
)
from us_quant.trading.domain.market import MarketSnapshot
from us_quant.trading.ports.market_data import (
    MarketDataActiveError,
    MarketDataCredentialsError,
)


#: A push inside this window keeps the poll timer idle.
PUSH_IDLE_SECONDS = 1.0

#: The subscription ceiling; the orchestrator enforces it because it is the
#: market layer that owns the connection.
MAX_SUBSCRIPTION_SYMBOLS = 30


class MarketOrchestrator(QObject):
    """Owns the market runtime and renders the market page from it."""

    #: A valid snapshot was stored and drawn.  The window fans this out to the
    #: workflows that still consume market facts (dashboard, minute evidence,
    #: auto-quant candidates, Paper, Shadow, targeted preflight).
    snapshot_changed = Signal(object)

    #: The stored snapshot was invalidated (a stop, or a finished worker).
    snapshot_invalidated = Signal()

    #: The global header facts changed.  The shell badges are not ours.
    shell_health_changed = Signal(object)

    #: The market page's control state was republished, so anything that
    #: mirrors it (the execution route's stop-stream control) must follow.
    controls_changed = Signal()

    #: The settings page is closed while a feed holds the connection.
    connection_settings_enabled_changed = Signal(bool)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    #: One runtime event the window should record.
    runtime_event_requested = Signal(object)

    #: A start failed and the operator must be told.
    task_failure_requested = Signal(str)

    #: A market request was refused before it reached the feed; the window
    #: shows the dialog because the orchestrator owns no widgets of its own.
    refused = Signal(str, str)

    #: The US equity session moved and the IBKR 5x24 venue must be rebuilt.
    #: Emitted as a *request* so the window's Paper interlock still applies.
    automatic_switch_requested = Signal(str)

    def __init__(
        self,
        *,
        market_data: MarketDataApplication,
        credential_service: DesktopCredentialService,
        page: object,
        poll_interval_ms: int = 500,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._market_data = market_data
        self._credentials = credential_service
        # The market runtime.  None of these is a public attribute.
        self._worker: StreamWorker | None = None
        self._snapshot: MarketSnapshot | None = None
        self._pending: tuple[str, tuple[str, ...]] | None = None
        self._stop_pending = False
        self._last_push_at = 0.0
        self._last_event_key: tuple[str, int] | None = None
        self._health = ShellHealthPublisher()
        self._stop_reason: str | None = None
        # Rendering is a separate job: the page projection, the scope line, the
        # watchlist note and the quote-recency cache live in the renderer.
        self._render = MarketRenderer(page=page)
        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self._poll)

    # -- read-only facts the rest of the desktop may consume -------------

    @property
    def snapshot(self) -> MarketSnapshot | None:
        """The single market truth, or ``None`` before anything arrived."""

        return self._snapshot

    @property
    def is_live(self) -> bool:
        """Whether a usable feed is owned: running, and not stuck stopping."""

        return not self._stop_pending and self.worker_running

    @property
    def worker_running(self) -> bool:
        """Whether the ``StreamWorker`` thread is actually still running.

        Thread liveness, a different question from feed availability
        (:attr:`is_live`): a stop that timed out leaves ``is_live`` false
        while this stays true, and shutdown reads this one.  A boolean,
        never a worker handle.
        """

        worker = self._worker
        return worker is not None and worker.isRunning()

    @property
    def active_source_id(self) -> str | None:
        """The source id of the live stream, or ``None`` when idle."""

        worker = self._worker
        if worker is None or not worker.isRunning():
            return None
        return worker.source_id

    @property
    def active_market_exchange(self) -> str | None:
        """The venue the live adapter was built for, or ``None`` when idle."""

        worker = self._worker
        if worker is None or not worker.isRunning():
            return None
        return worker.market_exchange

    @property
    def stop_reason(self) -> str | None:
        """Why the last snapshot was invalidated, for the dashboard card."""

        return self._stop_reason

    @property
    def polling_active(self) -> bool:
        """Whether the fallback poll timer is running."""

        return self._timer.isActive()

    def was_recently_ready(self, symbol: str) -> bool:
        """Whether this symbol had a fresh quote inside the recency window."""

        return self._render.was_recently_ready(symbol)

    def recently_ready_symbols(self) -> tuple[str, ...]:
        """The symbols inside the recency window, as finished data."""

        return self._render.recently_ready_symbols()

    # -- inputs the composition root pushes in ---------------------------

    def set_readiness_inputs(self, inputs: MarketReadinessInputs) -> None:
        """Hand over the cross-domain symbols the readiness breakdown needs.

        These come from the auto-quant shortlist and the selected strategy's
        parameters.  The orchestrator must not go looking for them itself: that
        would make it depend on the execution page and the strategy layer.
        """

        self._render.set_readiness_inputs(inputs)

    def set_scope(self, text: str) -> None:
        """Set the scope line written outside a full render."""

        self._render.set_scope(text)

    def set_subscription_symbols(
        self, symbols: tuple[str, ...], *, note: str | None = None
    ) -> None:
        """Write the subscription subset; not an operator intent.

        Research and the targeted workflow both change what is subscribed, and
        neither may touch the page directly.
        """

        self._render.set_subscription_symbols(symbols, note=note)
        self._render_controls()

    def set_selected_provider(self, source_id: str) -> None:
        """Point the page's provider combo without emitting an intent."""

        self._render.set_selected_provider(source_id)

    def selected_provider(self) -> str:
        """The provider the route currently points at, as finished input."""

        return self._render.selected_provider()

    def subscription_symbols(self) -> tuple[str, ...]:
        """The subscription subset the route currently holds.

        A *query*, not the truth: what is actually subscribed is what the live
        worker was built with.  This is the operator's typed draft, which the
        switch and settings paths need before they decide anything.
        """

        return self._render.subscription_symbols()

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        """Build the feed, draw the connecting state and start the worker."""

        if self.worker_running:
            # The start control doubles as "switch / reconnect" once a feed is
            # live, so a second start is a switch to the selected provider.
            self.request_switch(
                self._render.selected_provider() or "finnhub_trades"
            )
            return
        symbols = self._render.subscription_symbols()
        if not symbols:
            self.refused.emit("无法启动", "至少填写一个行情代码")
            return
        if len(symbols) > MAX_SUBSCRIPTION_SYMBOLS:
            self.refused.emit("无法启动", "首期最多订阅 30 个代码")
            return
        provider = self._render.selected_provider() or "ibkr"
        try:
            credentials = self._credentials.resolve_stream_credentials()
            # The page knows *what the operator selected*; everything else --
            # constructor, timeouts, venue, labels, coverage -- is the
            # application's business.
            request = MarketDataStartRequest(
                source_id=provider,
                symbols=symbols,
                credentials=MarketDataCredentials(
                    alpaca_api_key=credentials.alpaca_api_key,
                    alpaca_api_secret=credentials.alpaca_api_secret,
                    finnhub_api_key=credentials.finnhub_api_key,
                ),
            )
            worker = StreamWorker(self._market_data, request)
            market_exchange = worker.market_exchange
        except (
            MarketDataCredentialsError,
            MarketDataActiveError,
            ValueError,
        ) as error:
            # ``MarketDataActiveError`` is a ``RuntimeError``, so it has to be
            # named here: the application refuses to prepare while it still
            # holds a live feed, and an uncaught exception escaping a Qt slot
            # would abort the process instead of telling the operator.
            self.task_failure_requested.emit(str(error))
            return
        self._worker = worker
        self._render.reset()
        self._health.reset()
        worker.snapshot_ready.connect(self._on_pushed)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self.connection_settings_enabled_changed.emit(False)
        self._render.render_connecting(
            source_id=provider,
            symbol_count=len(symbols),
            controls=self._control_view(),
        )
        worker.start()
        self._timer.start()
        # Published *after* the thread starts: the route's stop-stream control
        # reads "is a worker running", so publishing before ``start()`` would
        # leave it disabled for every direct start.
        self._render_controls()
        self.controls_changed.emit()
        self._emit_event(
            "STREAM_START",
            f"{provider} 只读流启动；{len(symbols)} 个代码；"
            f"IBKR 路由 {market_exchange}",
        )
        route_note = (
            f" · 当前 IBKR 行情路由 {market_exchange}"
            if provider == SOURCE_IBKR_EXTENDED
            else ""
        )
        self.log_requested.emit(f"{provider} 只读流行情正在连接…{route_note}")

    def stop(self, *, preserve_pending: bool = False) -> bool:
        """Stop the feed; returns False when the thread did not confirm exit.

        The cross-workflow interlock is *not* here: whether a stop is allowed
        depends on the Paper session and the Shadow book, so the window decides
        that and only then calls this.
        """

        if not preserve_pending:
            self._pending = None
        worker = self._worker
        if worker is None:
            return True
        self._invalidate("行情流正在停止")
        worker.request_stop()
        if not worker.wait(3000):
            self.log_requested.emit("流服务正在退出；等待网络线程关闭…")
            self._timer.stop()
            self._stop_pending = True
            self._render_controls()
            self.controls_changed.emit()
            self._render.render_stop_pending()
            self._emit_event(
                "STREAM_STOP_PENDING",
                "流行情停止超过3秒，保持启动门关闭",
                severity="warning",
            )
            return False
        self._timer.stop()
        if self._worker is worker:
            self._worker = None
        self._stop_pending = False
        self._render_controls()
        self.controls_changed.emit()
        self._render.render_stopped("最后行情保留为 stale")
        self.connection_settings_enabled_changed.emit(True)
        self._emit_event("STREAM_STOP", "流行情已请求停止")
        return True

    def request_switch(self, provider: str) -> None:
        """Move the feed to another provider: stop, then start on the pending one."""

        symbols = self._render.subscription_symbols()
        if not symbols:
            self.refused.emit(
                "无法切换行情",
                "请先在“监控台 → 实时行情”填写至少一个代码，"
                "或从广域扫描载入候选。",
            )
            return
        if len(symbols) > MAX_SUBSCRIPTION_SYMBOLS:
            self.refused.emit("无法切换行情", "首期最多订阅 30 个代码")
            return
        if provider not in VALID_MARKET_SOURCES:
            self.refused.emit("无法切换行情", f"不支持的数据源：{provider}")
            return
        self._render.set_selected_provider(provider)
        self._pending = (provider, symbols)
        worker = self._worker
        if worker is not None and worker.isRunning():
            self.log_requested.emit(
                f"正在停止 {worker.source_id}，随后切换到 {provider}…"
            )
            if not self.stop(preserve_pending=True):
                return
        self._activate_pending()

    def maybe_request_extended_session_rotation(self) -> None:
        """Ask for a rebuild when the US equity session moved the IBKR venue.

        Emitted as a request rather than performed here: the switch still has
        to pass the window's Paper interlock, which this class may not know
        about.
        """

        worker = self._worker
        if (
            worker is None
            or not worker.isRunning()
            or worker.source_id != SOURCE_IBKR_EXTENDED
            or self._pending is not None
        ):
            return
        desired_exchange = self._market_data.desired_market_exchange(
            worker.source_id
        )
        if desired_exchange == worker.market_exchange:
            return
        self.log_requested.emit(
            "美东时段切换：正在把 IBKR 5×24 行情从 "
            f"{worker.market_exchange} 切换到 {desired_exchange}…"
        )
        self.automatic_switch_requested.emit(SOURCE_IBKR_EXTENDED)

    def stop_polling(self) -> None:
        """Release the fallback poll timer."""

        self._timer.stop()

    # -- ingress --------------------------------------------------------

    def _on_pushed(self, result: object) -> None:
        """Event-driven ingress: a service push delivered a fresh snapshot.

        Recording its arrival keeps the timer poll idle, so latency is bounded
        by the feed rather than by the poll interval.
        """

        if not isinstance(result, MarketSnapshot):
            return
        self._last_push_at = monotonic()
        self._on_snapshot(result)

    def _poll(self) -> None:
        """Timer fallback for feeds that have no push listener (IBKR)."""

        if not self.worker_running:
            return
        if monotonic() - self._last_push_at < PUSH_IDLE_SECONDS:
            return
        self._on_snapshot(self._market_data.snapshot())

    def _on_snapshot(self, snapshot: MarketSnapshot) -> None:
        if not isinstance(snapshot, MarketSnapshot):
            return
        self._snapshot = snapshot
        self._stop_reason = None
        if snapshot.error_code is not None:
            # Keyed by provider+error code only: reconnect generations must not
            # flood the event log with one entry per retry attempt.
            event_key = (snapshot.source_id, snapshot.error_code)
            if event_key != self._last_event_key:
                self._last_event_key = event_key
                self._emit_event(
                    str(snapshot.error_code),
                    snapshot.message,
                    severity="error",
                )
        else:
            # Recovered: allow the next outage of the same kind to be recorded
            # again instead of being swallowed by the old key.
            self._last_event_key = None
        readiness = self._render.render_snapshot(
            snapshot=snapshot,
            controls=self._control_view(),
        )
        self.shell_health_changed.emit(
            self._health.publish(snapshot, readiness)
        )
        self.snapshot_changed.emit(snapshot)

    def _on_failed(self, message: str) -> None:
        self._render.render_failure(message)
        self.shell_health_changed.emit(self._health.failed(message))
        self._emit_event("STREAM_FAILED", message, severity="error")

    def _emit_event(
        self, code: str, message: str, *, severity: str = "info"
    ) -> None:
        """Ask the window to record one market runtime event."""

        self.runtime_event_requested.emit(
            MarketRuntimeEvent(
                severity=severity,
                component="market_data",
                code=code,
                message=message,
            )
        )

    def _on_finished(self) -> None:
        """A worker's thread ended.  A stale worker must not clear its successor."""

        sender = self.sender()
        if self._worker is not None and sender is not self._worker:
            return
        self._worker = None
        self._invalidate("行情流已停止")
        self._timer.stop()
        self._stop_pending = False
        self._render_controls()
        self.controls_changed.emit()
        self.connection_settings_enabled_changed.emit(True)
        self._activate_pending()

    def _activate_pending(self) -> None:
        pending = self._pending
        if pending is None:
            return
        if self.worker_running:
            return
        provider, symbols = pending
        self._pending = None
        self._render.set_selected_provider(provider)
        self._render.set_subscription_symbols(symbols)
        self.start()

    # -- rendering ------------------------------------------------------

    def _invalidate(self, reason: str) -> None:
        """Mark the stored snapshot stale and publish the stopped state."""

        self._stop_reason = reason
        snapshot = self._snapshot
        if snapshot is not None:
            self._snapshot, readiness = self._render.invalidate(
                snapshot, reason, controls=self._control_view()
            )
            self.shell_health_changed.emit(
                self._health.publish(self._snapshot, readiness)
            )
        self.shell_health_changed.emit(self._health.stopped(reason))
        self.snapshot_invalidated.emit()

    def _control_view(self):
        return self._render.control_view(
            live=self.is_live, stop_pending=self._stop_pending
        )

    def _render_controls(self) -> None:
        self._render.render_controls(self._control_view())


__all__ = ["MarketOrchestrator"]
