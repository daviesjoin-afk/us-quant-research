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

from us_quant.desktop import MainWindow
from us_quant.desktop_credentials import (
    CredentialStatus,
    DesktopCredentialService,
    StreamCredentials,
)
from us_quant.desktop_workers import StreamWorker
from us_quant.desktop_v2.pages.market.controls import VALID_MARKET_SOURCES
from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.application.market_data import (
    SOURCE_ALPACA_IEX,
    SOURCE_IBKR,
    SOURCE_IBKR_EXTENDED,
    MarketDataApplication,
    MarketDataCredentials,
    MarketDataStartRequest,
)
from us_quant.trading.composition import market_data as module
from us_quant.trading.composition.market_data import (
    build_market_data_application,
)
from us_quant.paths import STATE_ROOT_ENV
from us_quant.user_settings import UserSettingsError


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

    def recorder(*args, **kwargs) -> _Recorder:
        record = _Recorder(args=args, kwargs=kwargs)
        created.append(record)
        return record

    monkeypatch.setattr(module, name, recorder)
    return created


def _service() -> MarketDataApplication:
    return build_market_data_application(_config)


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
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",)),
    )

    assert worker.source_id == SOURCE_IBKR
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
        MarketDataStartRequest(
            source_id=SOURCE_ALPACA_IEX,
            symbols=("AAPL",),
            credentials=MarketDataCredentials(
                alpaca_api_key="k",
                alpaca_api_secret="s",
            ),
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
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",)),
    )
    worker.request_stop()

    # The stop goes through the service, not straight to the adapter, so
    # the service's own lifecycle bookkeeping stays truthful.
    assert created[0].stopped is True
    assert service.lifecycle().running is False
    worker.deleteLater()


def test_stream_worker_reports_a_runtime_failure_to_the_service(
    monkeypatch,
) -> None:
    """A stream that raises out of its loop must not leave the service
    claiming it is still healthy."""

    created = _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()
    worker = StreamWorker(
        service,
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",)),
    )
    captured: list[str] = []
    worker.failed.connect(captured.append)

    def explode() -> None:
        raise RuntimeError("boom")

    # The recorder is the object the application built, so the test drives it
    # without the application exposing its private adapter.
    monkeypatch.setattr(created[0], "run", explode)
    worker.run()

    assert captured and "boom" in captured[0]
    assert service.lifecycle().last_error == captured[0]
    worker.deleteLater()


def test_stream_worker_reports_a_stream_failure(monkeypatch) -> None:
    def recorder(*args, **kwargs) -> _Recorder:
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(module, "IBKRReadOnlyStream", recorder)

    with pytest.raises(RuntimeError):
        StreamWorker(
            _service(),
            MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",)),
        )


def test_stream_worker_venue_comes_from_the_service(monkeypatch) -> None:
    _install(monkeypatch, "IBKRReadOnlyStream")

    worker = StreamWorker(
        _service(),
        MarketDataStartRequest(
            source_id=SOURCE_IBKR_EXTENDED, symbols=("SPY",)
        ),
    )

    assert worker.market_exchange == _service().desired_market_exchange(
        SOURCE_IBKR_EXTENDED
    )
    worker.deleteLater()


def test_stream_worker_reads_the_prepared_venue_without_re_resolving() -> None:
    """The worker must not resolve the venue a second time.

    The resolver follows the US equity session, so a second call around a
    SMART/OVERNIGHT boundary can legitimately return a different venue.  The
    worker would then report a venue the live adapter was never built for, and
    ``_maybe_rotate_extended_ibkr_session`` would skip the reconnect it owes.

    The resolver here flips on every call, so a second resolution cannot pass
    by coincidence.
    """

    created: list = []
    values = iter(["SMART", "OVERNIGHT"])
    calls: list[str] = []

    def resolver() -> str:
        value = next(values)
        calls.append(value)
        return value

    def factory(request, listener):
        record = _Recorder(args=(), kwargs={"request": request})
        created.append(record)
        return record

    service = MarketDataApplication(
        factories={
            SOURCE_IBKR: factory,
            SOURCE_IBKR_EXTENDED: factory,
        },
        exchange_resolver=resolver,
    )

    worker = StreamWorker(
        service,
        MarketDataStartRequest(
            source_id=SOURCE_IBKR_EXTENDED, symbols=("SPY",)
        ),
    )

    assert calls == ["SMART"], (
        "the worker re-resolved the venue; it must read the prepared value"
    )
    assert created[0].kwargs["request"].market_exchange == "SMART"
    assert worker.market_exchange == "SMART"
    assert worker.market_exchange != "OVERNIGHT"
    # And the worker holds no adapter.
    assert not hasattr(worker, "service")
    assert not hasattr(worker, "adapter")
    worker.deleteLater()


def test_stream_worker_carries_an_explicit_venue_verbatim() -> None:
    """An explicit venue must reach both the adapter and the worker."""

    created: list = []
    calls: list[int] = []

    def resolver() -> str:
        calls.append(len(calls))
        return "OVERNIGHT"

    def factory(request, listener):
        record = _Recorder(args=(), kwargs={"request": request})
        created.append(record)
        return record

    service = MarketDataApplication(
        factories={SOURCE_IBKR_EXTENDED: factory},
        exchange_resolver=resolver,
    )

    worker = StreamWorker(
        service,
        MarketDataStartRequest(
            source_id=SOURCE_IBKR_EXTENDED,
            symbols=("SPY",),
            market_exchange="SMART",
        ),
    )

    assert calls == []
    assert created[0].kwargs["request"].market_exchange == "SMART"
    assert worker.market_exchange == "SMART"
    worker.deleteLater()


def test_the_stream_worker_source_never_re_resolves_the_venue() -> None:
    """Structural guard: the worker may only read prepared truth.

    A behavioural test can miss a redundant call whose result happens to be
    discarded, so the source is checked directly.  This is an AST check, not a
    text search: the docstring and comments deliberately *name* the forbidden
    methods to explain why they are absent, and a substring search would flag
    its own explanation.
    """

    import ast

    tree = ast.parse(inspect.getsource(StreamWorker))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }

    for forbidden in ("market_exchange_for", "desired_market_exchange"):
        assert forbidden not in called, (
            f"StreamWorker must not call {forbidden}(); it reads "
            "prepared_market_exchange instead"
        )

    read = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    assert "prepared_market_exchange" in read


def test_session_rotation_fires_when_the_venue_moves_on(monkeypatch) -> None:
    """End to end: the desktop detects the session change and rotates.

    The worker must report the venue its adapter was built with, so that when
    the desired venue later differs the rotation guard can see the difference.
    If the worker had re-resolved, it would report the *new* venue and the
    rotation would be skipped -- leaving the live feed on the stale venue.
    """

    created: list = []
    calls: list[str] = []

    def resolver() -> str:
        # SMART for the prepare, then the session moves on for good.
        value = "SMART" if not calls else "OVERNIGHT"
        calls.append(value)
        return value

    def factory(request, listener):
        record = _Recorder(args=(), kwargs={"request": request})
        created.append(record)
        return record

    window = _window()
    try:
        # Replace the composed application with one whose resolver flips.  The
        # orchestrator holds the application it was injected with, so the swap
        # has to reach both -- otherwise the test would exercise the original.
        window.market_data = MarketDataApplication(
            factories={SOURCE_IBKR_EXTENDED: factory},
            exchange_resolver=resolver,
        )
        window.market_orchestrator._market_data = window.market_data
        assert SOURCE_IBKR_EXTENDED in VALID_MARKET_SOURCES
        window.market_page.set_selected_provider(SOURCE_IBKR_EXTENDED)
        window.market_page.set_subscription_symbols(("SPY",))

        window.market_orchestrator.start()

        worker = window.market_orchestrator._worker
        assert worker is not None
        # The adapter was built for SMART and the worker reports SMART, even
        # though the resolver now returns OVERNIGHT.
        assert created[0].kwargs["request"].market_exchange == "SMART"
        assert worker.market_exchange == "SMART"
        assert (
            window.market_data.desired_market_exchange(
                SOURCE_IBKR_EXTENDED
            )
            == "OVERNIGHT"
        )

        # The rotation guard must therefore fire.
        switches: list[tuple] = []
        monkeypatch.setattr(
            window,
            "_request_market_switch",
            lambda provider, **kwargs: switches.append(
                (provider, kwargs)
            ),
        )
        monkeypatch.setattr(
            window.market_orchestrator._worker, "isRunning", lambda: True
        )

        window.market_orchestrator.maybe_request_extended_session_rotation()

        assert len(switches) == 1, "the session rotation did not fire"
        provider, kwargs = switches[0]
        assert provider == SOURCE_IBKR_EXTENDED
        assert kwargs.get("allow_auto_session_switch") is True
    finally:
        window._stop_market_data()
        window.deleteLater()


def test_no_rotation_when_the_venue_is_unchanged(monkeypatch) -> None:
    """Belt and braces: a stable session must not trigger a reconnect."""

    created: list = []

    def factory(request, listener):
        record = _Recorder(args=(), kwargs={"request": request})
        created.append(record)
        return record

    window = _window()
    try:
        window.market_data = MarketDataApplication(
            factories={SOURCE_IBKR_EXTENDED: factory},
            exchange_resolver=lambda: "SMART",
        )
        window.market_orchestrator._market_data = window.market_data
        window.market_page.set_selected_provider(SOURCE_IBKR_EXTENDED)
        window.market_page.set_subscription_symbols(("SPY",))
        window.market_orchestrator.start()

        switches: list[tuple] = []
        monkeypatch.setattr(
            window,
            "_request_market_switch",
            lambda provider, **kwargs: switches.append(
                (provider, kwargs)
            ),
        )
        monkeypatch.setattr(
            window.market_orchestrator._worker, "isRunning", lambda: True
        )

        window.market_orchestrator.maybe_request_extended_session_rotation()

        assert switches == []
    finally:
        window._stop_market_data()
        window.deleteLater()


def test_stream_worker_rejects_an_unknown_provider(monkeypatch) -> None:
    created = _install(monkeypatch, "IBKRReadOnlyStream")

    with pytest.raises(ValueError):
        StreamWorker(
            _service(),
            MarketDataStartRequest(source_id="ibkr_extened", symbols=("SPY",)),
        )

    assert created == []


# -- MainWindow wiring -------------------------------------------------


def test_main_window_owns_a_market_data_service() -> None:
    window = _window()
    try:
        assert isinstance(window.market_data, MarketDataApplication)
        # It holds no connection config any more: the account application
        # owns that, and the market data composition reads it through a
        # getter at prepare time.
        assert not hasattr(window.market_data, "config")
    finally:
        window.deleteLater()


def test_main_window_owns_a_broker_account_application() -> None:
    """The account application is the single config owner."""

    from us_quant.trading.application.accounts import (
        BrokerAccountApplication,
    )

    window = _window()
    try:
        assert isinstance(
            window.broker_account, BrokerAccountApplication
        )
        # It is built from the live IBKR config, not a copy.
        assert window.broker_account.config == window.config.ibkr
        # And it starts with no account truth at all -- an unread account
        # must never look like an empty one.
        assert window.broker_account.portfolio is None
        assert window.broker_account.last_error is None
    finally:
        window.deleteLater()


def test_the_market_data_application_reads_the_account_config_getter(
) -> None:
    """The getter is the account application, not a captured config.

    This is the structural half of the stale-config regression: if the
    composition closed over a config value instead of calling the getter,
    the next IBKR stream would still use the start-up endpoint.
    """

    window = _window()
    try:
        # Change the owner's config and confirm the composition observes it
        # rather than a snapshot taken at construction time.
        import dataclasses

        before = window.broker_account.config
        updated = dataclasses.replace(before, client_id=before.client_id + 1)
        window.broker_account.update_config(updated)
        try:
            assert window.broker_account.config.client_id == (
                before.client_id + 1
            )
        finally:
            window.broker_account.update_config(before)
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

    current = window.settings_page.current_draft().ibkr_client_id
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

        draft = dataclasses.replace(
            window.settings_page.current_draft(),
            ibkr_client_id=new_client_id,
        )
        window.settings_orchestrator.save_preferences(draft)

        # The account application -- not just the window -- carries the
        # new config.  It is the single runtime owner.
        assert window.config.ibkr.client_id == new_client_id
        assert window.broker_account.config == window.config.ibkr

        window.market_page.set_selected_provider(SOURCE_IBKR)
        window.market_page.set_subscription_symbols(("SPY",))
        window.market_orchestrator.start()

        assert len(created) == 1
        # The adapter is constructed with the *saved* config, and it is a
        # new object rather than the start-up one.
        built_with = created[0].args[0]
        assert isinstance(built_with, IBKRConnectionConfig)
        assert built_with is not startup_config
        assert built_with.client_id == new_client_id
    finally:
        window._stop_market_data()
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
        window.market_data.prepare(
            MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",))
        )
        before = window.broker_account.config

        draft = dataclasses.replace(
            window.settings_page.current_draft(),
            ibkr_client_id=_next_client_id(window),
        )
        window.settings_orchestrator.save_preferences(draft)

        assert len(warnings) == 1, "the refusal must be reported"
        # Refused means *nothing* moved: the account application kept its
        # config, the window kept its config, and the settings file was not
        # rewritten behind the refusal.
        assert window.broker_account.config == before
        assert window.config.ibkr == before
        assert not (tmp_path / "settings" / "preferences.json").exists()
    finally:
        window._stop_market_data()
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
        window.market_data.prepare(
            MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",))
        )

        window.settings_orchestrator.save_preferences(
            window.settings_page.current_draft()
        )  # unchanged values: must not refuse

        assert warnings == []
        assert (tmp_path / "settings" / "preferences.json").exists()
    finally:
        window._stop_market_data()
        window.deleteLater()


def test_a_failed_settings_write_leaves_the_runtime_untouched(
    monkeypatch,
    tmp_path,
) -> None:
    """The write is the commit point: if it fails, nothing may have moved.

    Applying the config before persisting would leave the operator told
    "not saved" while the runtime had already adopted the new client id --
    the next stream would then connect with values the settings file does
    not contain.  ``ensure_config_update_allowed`` exists so the *check*
    can happen before the write without the *change* happening with it.
    """

    _install(monkeypatch, "IBKRReadOnlyStream")
    warnings = _capture_warnings(monkeypatch)
    window = _window_with_tmp_state(monkeypatch, tmp_path)
    try:
        # Establish a known on-disk baseline first.
        window.settings_orchestrator.save_preferences(
            window.settings_page.current_draft()
        )
        assert warnings == []
        saved_file = tmp_path / "settings" / "preferences.json"
        assert saved_file.exists()
        on_disk = saved_file.read_text(encoding="utf-8")

        config_before = window.config.ibkr
        service_before = window.broker_account.config
        preferences_before = window.preferences
        new_client_id = _next_client_id(window)
        assert new_client_id != config_before.client_id

        def failing_save(preferences):
            raise UserSettingsError("disk is full")

        monkeypatch.setattr(
            window.preferences_store, "save", failing_save
        )
        draft = dataclasses.replace(
            window.settings_page.current_draft(),
            ibkr_client_id=new_client_id,
        )
        window.settings_orchestrator.save_preferences(draft)

        # The operator is told, and told the truth.
        assert len(warnings) == 1
        assert "设置未保存" in warnings[0][0][1]
        # Nothing moved: not the window, not the service, not the file.
        assert window.config.ibkr == config_before
        assert window.broker_account.config == service_before
        assert window.preferences == preferences_before
        assert saved_file.read_text(encoding="utf-8") == on_disk
    finally:
        window._stop_market_data()
        window.deleteLater()


def test_a_failed_settings_write_does_not_need_a_live_stream(
    monkeypatch,
    tmp_path,
) -> None:
    """The check passes with no stream, so the failure is purely the write.

    This separates the two failure modes: the previous test would also pass
    if the refusal came from the live-stream guard rather than from the
    write, so this one proves the write is what rejected it.
    """

    _install(monkeypatch, "IBKRReadOnlyStream")
    warnings = _capture_warnings(monkeypatch)
    window = _window_with_tmp_state(monkeypatch, tmp_path)
    try:
        assert window.market_orchestrator._worker is None

        def failing_save(preferences):
            raise UserSettingsError("disk is full")

        monkeypatch.setattr(
            window.preferences_store, "save", failing_save
        )
        draft = dataclasses.replace(
            window.settings_page.current_draft(),
            ibkr_client_id=_next_client_id(window),
        )
        window.settings_orchestrator.save_preferences(draft)

        assert len(warnings) == 1
        assert not (tmp_path / "settings" / "preferences.json").exists()
    finally:
        window.deleteLater()


def test_starting_the_stream_uses_the_service_built_adapter(
    monkeypatch,
) -> None:
    created = _install(monkeypatch, "IBKRReadOnlyStream")
    window = _window()
    try:
        window.market_page.set_selected_provider(SOURCE_IBKR)
        window.market_page.set_subscription_symbols(("SPY", "QQQ"))

        window.market_orchestrator.start()

        assert len(created) == 1
        worker = window.market_orchestrator._worker
        assert worker is not None
        # The worker drives the application, which owns the adapter; the
        # UI never holds the adapter itself.  This is the boundary under test.
        assert worker.market_data is window.market_data
        assert worker.source_id == SOURCE_IBKR
        # The factory sentinel is the object the application built, and the
        # worker's venue is the one that adapter was actually given.
        assert created[0].kwargs["market_exchange"] == worker.market_exchange
        assert created[0].kwargs["symbols"] == ("SPY", "QQQ")
        assert created[0].kwargs["provider_label"] == "IBKR"
    finally:
        window._stop_market_data()
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
        window.market_data.prepare(
            MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",))
        )
        window.market_page.set_selected_provider(SOURCE_IBKR)
        window.market_page.set_subscription_symbols(("SPY",))

        window.market_orchestrator.start()  # must not raise

        # The operator is told, rather than the process dying.
        assert len(warnings) == 1
        # No second stream was built, and nothing was adopted as a worker.
        assert window.market_orchestrator._worker is None
        assert window.market_data.lifecycle().running is True
    finally:
        window._stop_market_data()
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
        window.market_page.set_selected_provider(SOURCE_ALPACA_IEX)
        window.market_page.set_subscription_symbols(("AAPL",))
        monkeypatch.setattr(
            window.credential_service,
            "resolve_stream_credentials",
            lambda **kwargs: StreamCredentials(
                finnhub_api_key="",
                alpaca_api_key="placeholder",
                alpaca_api_secret="placeholder",
            ),
        )

        window.market_orchestrator.start()

        worker = window.market_orchestrator._worker
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
        window._stop_market_data()
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
            window.market_page,
            "selected_provider",
            lambda: "ibkr_extened",
        )
        window.market_page.set_subscription_symbols(("SPY",))

        window.market_orchestrator.start()

        assert created == []
        assert window.market_orchestrator._worker is None
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


# -- the desktop hands credentials over, it does not store them --------


def test_main_window_owns_the_credential_service() -> None:
    """The window keeps store *ownership*; the service keeps the rules."""

    window = _window()
    try:
        assert isinstance(
            window.credential_service, DesktopCredentialService
        )
        # Not a second store: the same object, so a blob written through
        # one path is visible through the other.
        assert (
            window.credential_service.store is window.credential_store
        )
    finally:
        window.deleteLater()


def test_the_desktop_performs_no_credential_store_operations() -> None:
    """``desktop.py`` must not name the store's operations or layout.

    Constructing ``WindowsCredentialStore`` stays -- the window still owns
    the persistence object -- but every read, write and delete goes
    through the service, and the ``.dpapi`` filename never appears.
    """

    source = inspect.getsource(
        __import__("us_quant.desktop", fromlist=["desktop"])
    )

    for forbidden in (
        "credential_store.save_secret(",
        "credential_store.load_secret(",
        "credential_store.delete_secret(",
        "credential_store.has_secret(",
        "credential_store.root",
        ".dpapi",
    ):
        assert forbidden not in source, f"desktop.py still uses {forbidden}"


def test_the_desktop_no_longer_resolves_credentials_itself() -> None:
    """The per-credential env-then-DPAPI fallback has one home now."""

    source = inspect.getsource(
        __import__("us_quant.desktop", fromlist=["desktop"])
    )

    assert "_load_stream_credential" not in source
    assert "FINNHUB_API_KEY" not in source
    assert "APCA_API_KEY_ID" not in source
    assert "APCA_API_SECRET_KEY" not in source


def test_the_stream_request_carries_the_services_credentials(
    monkeypatch,
) -> None:
    """Three sentinels in, three sentinels out.

    Asserting on the request the service is asked to build is the point:
    a window that resolved credentials correctly but then passed them to
    the wrong field would still look like it worked.
    """

    requests: list[MarketDataStartRequest] = []

    def prepare(request, *, listener=None):
        requests.append(request)
        return _Recorder()

    window = _window()
    try:
        window.market_page.set_selected_provider(SOURCE_ALPACA_IEX)
        window.market_page.set_subscription_symbols(("AAPL",))
        monkeypatch.setattr(
            window.credential_service,
            "resolve_stream_credentials",
            lambda **kwargs: StreamCredentials(
                finnhub_api_key="SENTINEL-FINNHUB",
                alpaca_api_key="SENTINEL-KEY",
                alpaca_api_secret="SENTINEL-SECRET",
            ),
        )
        monkeypatch.setattr(window.market_data, "prepare", prepare)

        window.market_orchestrator.start()

        assert len(requests) == 1
        request = requests[0]
        assert request.credentials.alpaca_api_key == "SENTINEL-KEY"
        assert request.credentials.alpaca_api_secret == "SENTINEL-SECRET"
        assert request.credentials.finnhub_api_key == "SENTINEL-FINNHUB"
    finally:
        window._stop_market_data()
        window.deleteLater()


def _fake_running_worker(provider: str):
    """A stand-in for a live ``StreamWorker``: running, on ``provider``."""

    class _Worker:
        def __init__(self) -> None:
            self.source_id = provider

        def isRunning(self) -> bool:  # noqa: N802 - Qt spelling
            return True

    return _Worker()


def _capture_messages(monkeypatch) -> list[tuple]:
    """Record every ``QMessageBox`` call: warning and information."""

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


def test_clearing_the_active_providers_credentials_is_refused(
    monkeypatch,
) -> None:
    """The guard is UI/runtime coordination, so it stays in the window.

    A live stream is holding those credentials; deleting the blob under it
    would leave a running stream whose key no longer exists on disk.
    """

    cleared: list[str] = []
    window = _window()
    try:
        monkeypatch.setattr(
            window.credential_service,
            "clear_provider",
            cleared.append,
        )
        window.market_orchestrator._worker = _fake_running_worker(
            "finnhub_trades"
        )
        window.settings_page.set_api_provider("finnhub_trades", emit_change=True)
        messages = _capture_messages(monkeypatch)

        window.settings_orchestrator.clear_credentials(
            window.settings_page.current_credentials_draft().provider
        )

        assert cleared == []
        assert len(messages) == 1
        kind, args, _ = messages[0]
        assert kind == "warning"
        assert "行情运行中" in args[1]
    finally:
        window.market_orchestrator._worker = None
        window.deleteLater()


def test_clearing_an_inactive_providers_credentials_is_allowed(
    monkeypatch,
) -> None:
    """Finnhub running must not block clearing Alpaca.

    The over-broad version of the guard -- "any stream running refuses
    every clear" -- is the mistake this pins down.
    """

    cleared: list[str] = []
    window = _window()
    try:
        monkeypatch.setattr(
            window.credential_service,
            "clear_provider",
            cleared.append,
        )
        window.market_orchestrator._worker = _fake_running_worker(
            "finnhub_trades"
        )
        window.settings_page.set_api_provider("alpaca_iex", emit_change=True)
        messages = _capture_messages(monkeypatch)

        window.settings_orchestrator.clear_credentials(
            window.settings_page.current_credentials_draft().provider
        )

        assert cleared == ["alpaca_iex"]
        assert messages == []
    finally:
        window.market_orchestrator._worker = None
        window.deleteLater()


def test_clearing_ibkr_credentials_asks_the_service_for_nothing(
    monkeypatch,
) -> None:
    """IBKR stores no API key here, so there is nothing to delete."""

    cleared: list[str] = []
    window = _window()
    try:
        monkeypatch.setattr(
            window.credential_service,
            "clear_provider",
            cleared.append,
        )
        window.settings_page.set_api_provider("ibkr", emit_change=True)
        messages = _capture_messages(monkeypatch)

        window.settings_orchestrator.clear_credentials(
            window.settings_page.current_credentials_draft().provider
        )

        assert cleared == []
        assert len(messages) == 1
        kind, args, _ = messages[0]
        assert kind == "information"
        assert "无需清除" in args[1]
    finally:
        window.deleteLater()


def test_saving_ibkr_credentials_asks_the_service_for_nothing(
    monkeypatch,
) -> None:
    saved: list[tuple] = []
    window = _window()
    try:
        monkeypatch.setattr(
            window.credential_service,
            "save_provider",
            lambda *args, **kwargs: saved.append((args, kwargs)),
        )
        window.settings_page.set_api_provider("ibkr", emit_change=True)
        messages = _capture_messages(monkeypatch)

        window.settings_orchestrator.save_credentials(
            window.settings_page.current_credentials_draft()
        )

        assert saved == []
        assert len(messages) == 1
        kind, args, _ = messages[0]
        assert kind == "information"
        assert "无需 API Key" in args[1]
    finally:
        window.deleteLater()


def test_the_credential_status_line_comes_from_the_service(
    monkeypatch,
) -> None:
    """The window renders the copy; the service answers the question."""

    window = _window()
    try:
        window.settings_page.set_api_provider("finnhub_trades", emit_change=True)

        monkeypatch.setattr(
            window.credential_service,
            "status",
            lambda provider: CredentialStatus(
                provider=provider,
                requires_api_key=True,
                api_key_saved=True,
                api_secret_saved=False,
            ),
        )
        window.settings_orchestrator.render_current()
        assert (
            window.settings_page.credentials.status_label.text()
            == "Finnhub：已加密保存"
        )

        monkeypatch.setattr(
            window.credential_service,
            "status",
            lambda provider: CredentialStatus(
                provider=provider,
                requires_api_key=True,
                api_key_saved=False,
                api_secret_saved=False,
            ),
        )
        window.settings_orchestrator.render_current()
        assert (
            window.settings_page.credentials.status_label.text()
            == "Finnhub：未保存"
        )
    finally:
        window.deleteLater()


def test_the_alpaca_status_line_reports_each_half(
    monkeypatch,
) -> None:
    window = _window()
    try:
        window.settings_page.set_api_provider("alpaca_iex", emit_change=True)

        monkeypatch.setattr(
            window.credential_service,
            "status",
            lambda provider: CredentialStatus(
                provider=provider,
                requires_api_key=True,
                api_key_saved=True,
                api_secret_saved=False,
            ),
        )
        window.settings_orchestrator.render_current()

        text = window.settings_page.credentials.status_label.text()
        assert "Alpaca Key：已加密保存" in text
        assert "Alpaca Secret：未保存" in text
    finally:
        window.deleteLater()


def test_the_ibkr_status_line_says_no_key_is_needed(
    monkeypatch,
) -> None:
    window = _window()
    try:
        window.settings_page.set_api_provider("ibkr", emit_change=True)
        messages = _capture_messages(monkeypatch)

        window.settings_orchestrator.render_current()

        assert (
            window.settings_page.credentials.status_label.text()
            == "IBKR Gateway：使用本机 Host / 端口 / Client ID，"
            "无需 API Key"
        )
        assert messages == []
    finally:
        window.deleteLater()
