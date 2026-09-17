"""Desktop regression coverage for the market data service boundary.

The unit tests in ``tests/test_market_data_service.py`` pin what the
service builds.  These tests pin the *wiring*: that ``StreamWorker`` is a
Qt thread adapter around a stream the service built, and that it no longer
chooses or constructs a provider itself.

The regression this exists for is concrete: the worker used to take a
``config`` plus every provider's credentials and branch on ``provider`` to
build one of three adapters.  Asserting the worker holds the *service's*
stream object is what makes "the UI no longer knows providers"
meaningful -- otherwise a worker that quietly built its own stream would
also read as working.

The adapters are replaced with recorders; nothing here opens a socket.
"""

from __future__ import annotations

import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dataclasses

import pytest
from PySide6.QtWidgets import QApplication

from us_quant import market_data_service as module
from us_quant.desktop import MainWindow, StreamWorker
from us_quant.ibkr import IBKRConnectionConfig
from us_quant.market_data_service import (
    PROVIDER_IBKR,
    PROVIDER_IBKR_EXTENDED,
    MarketDataRequest,
    MarketDataService,
)
from us_quant.paths import STATE_ROOT_ENV


_APP = QApplication.instance() or QApplication([])


@dataclasses.dataclass
class _Recorder:
    args: tuple = ()
    kwargs: dict = dataclasses.field(default_factory=dict)
    stopped: bool = False
    ran: bool = False

    def run(self) -> None:
        self.ran = True

    def stop(self) -> None:
        self.stopped = True

    def snapshot(self) -> object:
        return object()


def _config() -> IBKRConnectionConfig:
    return IBKRConnectionConfig(
        host="127.0.0.1",
        port=4002,
        client_id=17,
        api_read_only=True,
        paper_order_submission_enabled=False,
        connection_timeout_seconds=2,
    )


def _install(monkeypatch, name: str) -> list[_Recorder]:
    created: list[_Recorder] = []

    def factory(*args, **kwargs) -> _Recorder:
        recorder = _Recorder(args=args, kwargs=kwargs)
        created.append(recorder)
        return recorder

    monkeypatch.setattr(module, name, factory)
    return created


def _service() -> MarketDataService:
    return MarketDataService(_config())


def _window() -> MainWindow:
    window = MainWindow()
    _APP.processEvents()
    return window


# -- the worker is an adapter, not a factory ---------------------------


def test_stream_worker_runs_the_stream_the_service_built(
    monkeypatch,
) -> None:
    created = _install(monkeypatch, "IBKRReadOnlyStream")

    worker = StreamWorker(
        _service(),
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",)),
    )

    assert worker.service is created[0]
    assert worker.provider == PROVIDER_IBKR
    assert created[0].kwargs["symbols"] == ("SPY",)
    worker.deleteLater()


def test_stream_worker_does_not_accept_provider_configuration() -> None:
    """The construction parameters must be gone, not merely unused.

    A leftover ``api_key``/``finnhub_key``/``config`` parameter would let
    the UI keep passing provider knowledge into the worker, which is the
    responsibility this change removes.
    """

    parameters = set(
        inspect.signature(StreamWorker.__init__).parameters
    )

    assert parameters == {"self", "market_data", "request"}


def test_stream_worker_listener_is_the_queued_signal(monkeypatch) -> None:
    """The push listener must be the worker's own signal.

    The service decides *whether* a provider gets a listener; the worker
    decides *what* it is.  It has to be ``snapshot_ready.emit`` so the
    snapshot crosses into the GUI thread queued, instead of a callback
    touching widgets from the stream thread.  The check is behavioural:
    invoking the listener the worker handed the adapter must deliver
    through ``snapshot_ready``.
    """

    created = _install(monkeypatch, "AlpacaIEXStream")

    worker = StreamWorker(
        _service(),
        MarketDataRequest(
            provider=module.PROVIDER_ALPACA_IEX,
            symbols=("AAPL",),
            alpaca_api_key="k",
            alpaca_api_secret="s",
        ),
    )

    delivered: list[object] = []
    worker.snapshot_ready.connect(delivered.append)

    listener = created[0].kwargs["listener"]
    assert listener is not None, "a push provider must get a listener"
    payload = object()
    listener(payload)
    _APP.processEvents()

    assert delivered == [payload]
    worker.deleteLater()


def test_stream_worker_forwards_the_stop_request(monkeypatch) -> None:
    created = _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()

    worker = StreamWorker(
        service,
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",)),
    )
    worker.request_stop()

    # The stop goes through the service, not straight to the adapter, so
    # the service's own lifecycle bookkeeping stays truthful.
    assert created[0].stopped is True
    assert service.snapshot().running is False
    worker.deleteLater()


def test_stream_worker_reports_a_runtime_failure_to_the_service(
    monkeypatch,
) -> None:
    """A stream that raises out of its loop must not leave the service
    claiming it is still healthy."""

    _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()
    worker = StreamWorker(
        service,
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",)),
    )
    captured: list[str] = []
    worker.failed.connect(captured.append)

    def explode() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker.service, "run", explode)
    worker.run()

    assert captured and "boom" in captured[0]
    assert service.snapshot().last_error == captured[0]
    worker.deleteLater()


def test_stream_worker_reports_a_stream_failure(monkeypatch) -> None:
    def factory(*args, **kwargs) -> _Recorder:
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(module, "IBKRReadOnlyStream", factory)

    with pytest.raises(RuntimeError):
        StreamWorker(
            _service(),
            MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",)),
        )


def test_stream_worker_venue_comes_from_the_service(monkeypatch) -> None:
    _install(monkeypatch, "IBKRReadOnlyStream")

    worker = StreamWorker(
        _service(),
        MarketDataRequest(
            provider=PROVIDER_IBKR_EXTENDED, symbols=("SPY",)
        ),
    )

    assert worker.market_exchange == module.ibkr_market_data_exchange()
    worker.deleteLater()


def test_stream_worker_rejects_an_unknown_provider(monkeypatch) -> None:
    created = _install(monkeypatch, "IBKRReadOnlyStream")

    with pytest.raises(ValueError):
        StreamWorker(
            _service(),
            MarketDataRequest(provider="ibkr_extened", symbols=("SPY",)),
        )

    assert created == []


# -- MainWindow wiring -------------------------------------------------


def test_main_window_owns_a_market_data_service() -> None:
    window = _window()
    try:
        assert isinstance(window.market_data_service, MarketDataService)
        # It must be built from the live IBKR config, not a copy.
        assert window.market_data_service.config == window.config.ibkr
    finally:
        window.deleteLater()


def _window_with_tmp_state(monkeypatch, tmp_path) -> MainWindow:
    """A window whose writable state root is ``tmp_path``.

    Saving preferences writes ``settings/preferences.json``, and the state
    root is read in ``MainWindow.__init__``, so it has to be redirected
    before construction -- otherwise these tests would overwrite the
    operator's real settings file, and would also inherit whatever client
    id it happens to hold.
    """

    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    return _window()


def _capture_warnings(monkeypatch) -> list[tuple]:
    """Record ``QMessageBox.warning`` calls instead of blocking on them."""

    calls: list[tuple] = []

    def warning(*args, **kwargs):
        calls.append((args, kwargs))
        return None

    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.warning", staticmethod(warning)
    )
    return calls


def _next_client_id(window: MainWindow) -> int:
    """A client id that differs from the one the window is showing.

    Derived rather than hard-coded so the test does not depend on the
    baseline config, and clamped to the spin box range so ``setValue``
    cannot silently ignore it.
    """

    current = window.settings_ibkr_client_id.value()
    return current + 1 if current < 999_999 else current - 1


def test_saving_settings_reaches_the_service_before_the_next_stream(
    monkeypatch,
    tmp_path,
) -> None:
    """The regression this whole change exists for.

    ``MarketDataService`` holds its own ``IBKRConnectionConfig`` and builds
    every stream from it.  Saving settings used to update only
    ``self.config``, so the next stream silently reconnected with the
    values captured at start-up.
    """

    created = _install(monkeypatch, "IBKRReadOnlyStream")
    window = _window_with_tmp_state(monkeypatch, tmp_path)
    try:
        startup_config = window.config.ibkr
        new_client_id = _next_client_id(window)
        assert new_client_id != startup_config.client_id

        window.settings_ibkr_client_id.setValue(new_client_id)
        window._save_user_preferences()

        # The service -- not just the window -- carries the new config.
        assert window.config.ibkr.client_id == new_client_id
        assert window.market_data_service.config == window.config.ibkr

        index = window.stream_mode.findData(PROVIDER_IBKR)
        assert index >= 0
        window.stream_mode.setCurrentIndex(index)
        window.stream_symbols.setText("SPY")
        window._start_stream()

        assert len(created) == 1
        # The adapter is constructed with the *saved* config, and it is a
        # new object rather than the start-up one.
        built_with = created[0].args[0]
        assert isinstance(built_with, IBKRConnectionConfig)
        assert built_with is not startup_config
        assert built_with.client_id == new_client_id
    finally:
        window._stop_stream()
        window.deleteLater()


def test_saving_settings_is_refused_while_a_stream_is_live(
    monkeypatch,
    tmp_path,
) -> None:
    """A live stream must not have its connection config swapped underneath.

    The stream is built directly on the service so the assertion is
    deterministic: no thread has to start for the service to hold a live
    stream.  The refusal surfaces as an operator warning, not an escaping
    exception, so that is what the test pins.
    """

    _install(monkeypatch, "IBKRReadOnlyStream")
    warnings = _capture_warnings(monkeypatch)
    window = _window_with_tmp_state(monkeypatch, tmp_path)
    try:
        window.market_data_service.build_stream(
            MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",))
        )
        before = window.market_data_service.config

        window.settings_ibkr_client_id.setValue(_next_client_id(window))
        window._save_user_preferences()

        assert len(warnings) == 1, "the refusal must be reported"
        # Refused means *nothing* moved: the service kept its config, the
        # window kept its config, and the settings file was not rewritten
        # behind the refusal.
        assert window.market_data_service.config == before
        assert window.config.ibkr == before
        assert not (tmp_path / "settings" / "preferences.json").exists()
    finally:
        window._stop_stream()
        window.deleteLater()


def test_saving_identical_settings_while_streaming_is_not_a_refusal(
    monkeypatch,
    tmp_path,
) -> None:
    """Only a *change* is refused, so re-saving the same values still works.

    Otherwise the operator would be blocked from saving an unrelated
    setting (the theme, say) purely because a stream happened to be
    running.
    """

    _install(monkeypatch, "IBKRReadOnlyStream")
    warnings = _capture_warnings(monkeypatch)
    window = _window_with_tmp_state(monkeypatch, tmp_path)
    try:
        window.market_data_service.build_stream(
            MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",))
        )

        window._save_user_preferences()  # unchanged values: must not refuse

        assert warnings == []
        assert (tmp_path / "settings" / "preferences.json").exists()
    finally:
        window._stop_stream()
        window.deleteLater()


def test_starting_the_stream_uses_the_service_built_adapter(
    monkeypatch,
) -> None:
    created = _install(monkeypatch, "IBKRReadOnlyStream")
    window = _window()
    try:
        index = window.stream_mode.findData(PROVIDER_IBKR)
        assert index >= 0
        window.stream_mode.setCurrentIndex(index)
        window.stream_symbols.setText("SPY,QQQ")

        window._start_stream()

        assert len(created) == 1
        worker = window.stream_worker
        assert worker is not None
        # The worker holds the object the service built, not one of its
        # own: this is the whole boundary under test.
        assert worker.service is created[0]
        assert worker.provider == PROVIDER_IBKR
        assert created[0].kwargs["symbols"] == ("SPY", "QQQ")
        assert created[0].kwargs["provider_label"] == "IBKR"
    finally:
        window._stop_stream()
        window.deleteLater()


def test_a_refused_build_is_reported_instead_of_escaping(
    monkeypatch,
) -> None:
    """The service's live-stream refusal must not escape the Qt slot.

    ``MarketDataStreamActive`` is a ``RuntimeError``; an exception leaving
    ``_start_stream`` would abort the process rather than tell the
    operator.  The window is left with no worker while the service still
    holds a live stream, which is exactly the state the guard exists for.
    """

    _install(monkeypatch, "IBKRReadOnlyStream")
    warnings = _capture_warnings(monkeypatch)
    window = _window()
    try:
        window.market_data_service.build_stream(
            MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",))
        )
        index = window.stream_mode.findData(PROVIDER_IBKR)
        assert index >= 0
        window.stream_mode.setCurrentIndex(index)
        window.stream_symbols.setText("SPY")

        window._start_stream()  # must not raise

        # The operator is told, rather than the process dying.
        assert len(warnings) == 1
        # No second stream was built, and nothing was adopted as a worker.
        assert window.stream_worker is None
        assert window.market_data_service.snapshot().running is True
    finally:
        window._stop_stream()
        window.deleteLater()


def test_the_desktop_hands_the_adapter_the_queued_signal(
    monkeypatch,
) -> None:
    """End to end: the listener the adapter holds is the worker's signal.

    A push provider reached through ``MainWindow`` must deliver snapshots
    through ``StreamWorker.snapshot_ready``, which is what makes the hop
    into the GUI thread a queued signal instead of a direct call from the
    stream thread.  Asserting on the service directly would miss a
    desktop that substituted its own callback afterwards.
    """

    created = _install(monkeypatch, "AlpacaIEXStream")
    window = _window()
    try:
        index = window.stream_mode.findData(
            module.PROVIDER_ALPACA_IEX
        )
        assert index >= 0
        window.stream_mode.setCurrentIndex(index)
        window.stream_symbols.setText("AAPL")
        monkeypatch.setattr(
            window,
            "_load_stream_credential",
            lambda *args, **kwargs: "placeholder",
        )

        window._start_stream()

        worker = window.stream_worker
        assert worker is not None
        listener = created[0].kwargs["listener"]
        assert listener is not None

        delivered: list[object] = []
        worker.snapshot_ready.connect(delivered.append)
        payload = object()
        listener(payload)
        _APP.processEvents()

        assert delivered == [payload]
    finally:
        window._stop_stream()
        window.deleteLater()


def test_starting_an_unknown_provider_fails_closed(
    monkeypatch,
) -> None:
    """A bad provider id must surface as a failure, never as a stream."""

    created = _install(monkeypatch, "IBKRReadOnlyStream")
    window = _window()
    try:
        failures: list[str] = []
        monkeypatch.setattr(window, "_task_failed", failures.append)
        monkeypatch.setattr(
            window.stream_mode, "currentData", lambda: "ibkr_extened"
        )
        window.stream_symbols.setText("SPY")

        window._start_stream()

        assert created == []
        assert window.stream_worker is None
        assert failures and "ibkr_extened" in failures[0]
    finally:
        window.deleteLater()


def test_the_desktop_no_longer_names_a_provider_adapter() -> None:
    """Belt and braces with the service-side test: the UI is provider-free."""

    source = inspect.getsource(
        __import__("us_quant.desktop", fromlist=["desktop"])
    )

    for name in (
        "AlpacaIEXStream",
        "FinnhubTradeStream",
        "IBKRReadOnlyStream",
        "ibkr_market_data_exchange",
    ):
        assert name not in source, f"desktop.py still references {name}"
