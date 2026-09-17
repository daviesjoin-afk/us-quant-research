"""Contract coverage for the market data application service.

These tests never touch the network.  Every provider adapter is replaced
by a recorder that captures the exact constructor arguments, so the
assertions are about *what the service decided*, not about WebSocket
behaviour (which ``test_ibkr_stream``/``test_alpaca_stream``/
``test_finnhub_stream`` already own).

The behaviour pinned here is preservation, not preference: the stale
thresholds, the requested market data type, the IBKR venue routing, the
provider labels and the coverage copy are the values the desktop used to
pass inline before ``MarketDataService`` existed.  A regression in any of
them would silently change what the feed reports about freshness.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import threading
from pathlib import Path

import pytest

from us_quant import market_data_service as module
from us_quant.ibkr import IBKRConnectionConfig
from us_quant.market_data_service import (
    ALPACA_STALE_AFTER_SECONDS,
    DEFAULT_MARKET_EXCHANGE,
    FINNHUB_STALE_AFTER_SECONDS,
    IBKR_COVERAGE,
    IBKR_EXTENDED_COVERAGE,
    IBKR_EXTENDED_PROVIDER_LABEL,
    IBKR_PROVIDER_LABEL,
    IBKR_REQUESTED_MARKET_DATA_TYPE,
    IBKR_STALE_AFTER_SECONDS,
    PROVIDER_ALPACA_IEX,
    PROVIDER_FINNHUB_TRADES,
    PROVIDER_IBKR,
    PROVIDER_IBKR_EXTENDED,
    SUPPORTED_PROVIDERS,
    MarketDataRequest,
    MarketDataService,
    MarketDataServiceSnapshot,
)


def _config() -> IBKRConnectionConfig:
    return IBKRConnectionConfig(
        host="127.0.0.1",
        port=4002,
        client_id=17,
        api_read_only=True,
        paper_order_submission_enabled=False,
        connection_timeout_seconds=2,
    )


@dataclasses.dataclass
class _Recorder:
    """Stands in for a provider adapter, capturing ctor arguments."""

    kwargs: dict = dataclasses.field(default_factory=dict)
    symbols: tuple[str, ...] = ()
    stopped: bool = False
    ran: bool = False

    def run(self) -> None:
        self.ran = True

    def stop(self) -> None:
        self.stopped = True

    def snapshot(self) -> object:
        return object()


def _install(monkeypatch, name: str) -> list[_Recorder]:
    """Replace one adapter class with a recorder factory.

    Returns the list the recorders append themselves to, so a test can
    inspect what was built without holding a reference to the stream.
    """

    created: list[_Recorder] = []

    def factory(*args, **kwargs) -> _Recorder:
        recorder = _Recorder(kwargs=kwargs, symbols=kwargs.get("symbols", ()))
        recorder.args = args
        created.append(recorder)
        return recorder

    monkeypatch.setattr(module, name, factory)
    return created


def _service() -> MarketDataService:
    return MarketDataService(_config())


def replace_config(**changes) -> IBKRConnectionConfig:
    """A copy of the baseline config with ``changes`` applied.

    Named after ``dataclasses.replace`` because that is what it does; the
    point of the tests using it is that the *value* differs from the
    baseline, so an ignored ``update_config`` cannot pass by accident.
    """

    return dataclasses.replace(_config(), **changes)


# -- provider selection ------------------------------------------------


def test_ibkr_request_builds_the_ibkr_read_only_stream(monkeypatch) -> None:
    created = _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()

    stream = service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY", "QQQ"))
    )

    assert created == [stream]
    assert stream.kwargs["symbols"] == ("SPY", "QQQ")
    assert stream.kwargs["requested_market_data_type"] == 1
    assert stream.kwargs["stale_after_seconds"] == IBKR_STALE_AFTER_SECONDS
    assert stream.kwargs["provider_label"] == IBKR_PROVIDER_LABEL
    assert stream.kwargs["coverage"] == IBKR_COVERAGE
    assert stream.kwargs["market_exchange"] == DEFAULT_MARKET_EXCHANGE
    # The IBKR config is positional in the adapter's signature.
    assert stream.args == (_config(),)


def test_extended_ibkr_keeps_label_coverage_and_venue_routing(
    monkeypatch,
) -> None:
    created = _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()

    # A sentinel venue, deliberately different from the default: the real
    # helper returns "SMART" during the regular session, so asserting
    # against it would pass even if the routing were deleted entirely.
    monkeypatch.setattr(
        module, "ibkr_market_data_exchange", lambda: "ARCA"
    )

    service.build_stream(
        MarketDataRequest(
            provider=PROVIDER_IBKR_EXTENDED, symbols=("SPY",)
        )
    )

    kwargs = created[0].kwargs
    assert kwargs["provider_label"] == IBKR_EXTENDED_PROVIDER_LABEL
    assert kwargs["coverage"] == IBKR_EXTENDED_COVERAGE
    assert kwargs["requested_market_data_type"] == 1
    # The venue follows the US equity session, which is provider
    # knowledge: whatever the session helper says is what must be routed.
    assert kwargs["market_exchange"] == "ARCA"


def test_explicit_market_exchange_overrides_session_routing(
    monkeypatch,
) -> None:
    created = _install(monkeypatch, "IBKRReadOnlyStream")

    # The session helper must be *ignored* here, so give it a value that
    # could not be mistaken for the explicit one.
    monkeypatch.setattr(
        module, "ibkr_market_data_exchange", lambda: "ARCA"
    )

    _service().build_stream(
        MarketDataRequest(
            provider=PROVIDER_IBKR_EXTENDED,
            symbols=("SPY",),
            market_exchange="SMART",
        )
    )

    assert created[0].kwargs["market_exchange"] == "SMART"


def test_desired_market_exchange_only_routes_the_extended_provider(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        module, "ibkr_market_data_exchange", lambda: "ARCA"
    )
    service = _service()

    assert service.desired_market_exchange(PROVIDER_IBKR_EXTENDED) == "ARCA"
    for provider in SUPPORTED_PROVIDERS:
        if provider == PROVIDER_IBKR_EXTENDED:
            continue
        assert service.desired_market_exchange(provider) == "SMART"


def test_alpaca_credentials_are_passed_through(monkeypatch) -> None:
    created = _install(monkeypatch, "AlpacaIEXStream")

    _service().build_stream(
        MarketDataRequest(
            provider=PROVIDER_ALPACA_IEX,
            symbols=("AAPL",),
            alpaca_api_key="key-id",
            alpaca_api_secret="secret-value",
        )
    )

    kwargs = created[0].kwargs
    assert kwargs["api_key"] == "key-id"
    assert kwargs["api_secret"] == "secret-value"
    assert kwargs["stale_after_seconds"] == ALPACA_STALE_AFTER_SECONDS
    assert kwargs["symbols"] == ("AAPL",)


def test_finnhub_key_is_passed_through(monkeypatch) -> None:
    created = _install(monkeypatch, "FinnhubTradeStream")

    _service().build_stream(
        MarketDataRequest(
            provider=PROVIDER_FINNHUB_TRADES,
            symbols=("MSFT",),
            finnhub_api_key="fh-key",
        )
    )

    kwargs = created[0].kwargs
    assert kwargs["api_key"] == "fh-key"
    assert kwargs["stale_after_seconds"] == FINNHUB_STALE_AFTER_SECONDS


def test_unknown_provider_is_rejected_without_building_anything(
    monkeypatch,
) -> None:
    """A typo must fail closed, never fall back to a broker connection."""

    created = _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()

    with pytest.raises(ValueError) as error:
        service.build_stream(
            MarketDataRequest(provider="ibkr_extened", symbols=("SPY",))
        )

    assert "ibkr_extened" in str(error.value)
    assert created == []
    # Fail closed also means "not running, and the reason is recorded".
    snapshot = service.snapshot()
    assert snapshot.running is False
    assert snapshot.provider == ""
    assert "ibkr_extened" in (snapshot.last_error or "")


def test_empty_provider_is_rejected(monkeypatch) -> None:
    created = _install(monkeypatch, "IBKRReadOnlyStream")

    with pytest.raises(ValueError):
        _service().build_stream(
            MarketDataRequest(provider="", symbols=("SPY",))
        )

    assert created == []


def test_every_supported_provider_builds(monkeypatch) -> None:
    """The whitelist and the dispatch must agree for all four providers."""

    _install(monkeypatch, "IBKRReadOnlyStream")
    _install(monkeypatch, "AlpacaIEXStream")
    _install(monkeypatch, "FinnhubTradeStream")
    service = _service()

    for provider in SUPPORTED_PROVIDERS:
        request = MarketDataRequest(
            provider=provider,
            symbols=("SPY",),
            alpaca_api_key="k",
            alpaca_api_secret="s",
            finnhub_api_key="f",
        )
        assert service.build_stream(request) is not None
        # One live stream at a time: release it before building the next.
        service.stop()


# -- listener routing --------------------------------------------------


def test_listener_reaches_the_push_providers(monkeypatch) -> None:
    alpaca = _install(monkeypatch, "AlpacaIEXStream")
    finnhub = _install(monkeypatch, "FinnhubTradeStream")
    listener = object()

    service = _service()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_ALPACA_IEX, symbols=("A",)),
        listener=listener,
    )
    service.stop()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_FINNHUB_TRADES, symbols=("B",)),
        listener=listener,
    )

    assert alpaca[0].kwargs["listener"] is listener
    assert finnhub[0].kwargs["listener"] is listener


def test_ibkr_is_not_given_a_push_listener(monkeypatch) -> None:
    """Preserved behaviour: IBKR is polled, so a listener would double-publish.

    The desktop's snapshot timer is the IBKR ingress path.  Handing the
    adapter a listener as well would emit every quote twice -- once
    queued through the signal and once from the poll.
    """

    for provider in (PROVIDER_IBKR, PROVIDER_IBKR_EXTENDED):
        created = _install(monkeypatch, "IBKRReadOnlyStream")

        _service().build_stream(
            MarketDataRequest(provider=provider, symbols=("SPY",)),
            listener=object(),
        )

        assert "listener" not in created[0].kwargs


def test_listener_policy_is_reported_for_each_provider() -> None:
    service = _service()
    listener = object()

    assert service.listener_for(PROVIDER_ALPACA_IEX, listener) is listener
    assert service.listener_for(PROVIDER_FINNHUB_TRADES, listener) is listener
    assert service.listener_for(PROVIDER_IBKR, listener) is None
    assert service.listener_for(PROVIDER_IBKR_EXTENDED, listener) is None


# -- lifecycle and snapshot -------------------------------------------


def test_snapshot_reports_lifecycle_only_and_normalises_symbols(
    monkeypatch,
) -> None:
    created = _install(monkeypatch, "AlpacaIEXStream")

    def factory(*args, **kwargs) -> _Recorder:
        recorder = _Recorder(kwargs=kwargs, symbols=("AAPL", "MSFT"))
        created.append(recorder)
        return recorder

    monkeypatch.setattr(module, "AlpacaIEXStream", factory)
    service = _service()

    assert service.snapshot() == MarketDataServiceSnapshot(
        provider="", symbols=(), running=False, last_error=None
    )
    service.build_stream(
        MarketDataRequest(
            provider=PROVIDER_ALPACA_IEX, symbols=("aapl ", "msft")
        )
    )

    snapshot = service.snapshot()
    assert snapshot.provider == PROVIDER_ALPACA_IEX
    # The adapter's own normalised watchlist is reported, not the raw
    # request, so the snapshot cannot disagree with the subscription.
    assert snapshot.symbols == ("AAPL", "MSFT")
    assert snapshot.running is True
    assert snapshot.last_error is None


def test_stop_is_forwarded_and_safe_before_any_build(monkeypatch) -> None:
    created = _install(monkeypatch, "AlpacaIEXStream")
    service = _service()

    service.stop()  # no stream yet: must not raise
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_ALPACA_IEX, symbols=("A",))
    )
    service.stop()

    assert created[0].stopped is True
    assert service.snapshot().running is False


def test_a_runtime_failure_is_recorded_without_tearing_down(
    monkeypatch,
) -> None:
    """A stream that raises is recorded, and never left reading as running.

    ``run`` owns the lifecycle half, so the failure is recorded there and
    ``finally`` clears ``running``.  The stream is deliberately *not* torn
    down from inside the service -- whoever caught the failure drives the
    shutdown, so that this cannot race the desktop's stop ordering.
    """

    created = _install(monkeypatch, "AlpacaIEXStream")
    service = _service()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_ALPACA_IEX, symbols=("A",))
    )

    def explode() -> None:
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(created[0], "run", explode)

    with pytest.raises(RuntimeError):
        service.run()

    snapshot = service.snapshot()
    assert snapshot.last_error == "RuntimeError: socket exploded"
    # The failure must not leave a fake running state behind.
    assert snapshot.running is False
    assert created[0].stopped is False


def test_failed_construction_never_reports_running(monkeypatch) -> None:
    """Credentials missing is the realistic fail-closed case."""

    def factory(*args, **kwargs) -> _Recorder:
        raise module.AlpacaCredentialsMissing("missing")

    monkeypatch.setattr(module, "AlpacaIEXStream", factory)
    service = _service()

    with pytest.raises(module.AlpacaCredentialsMissing):
        service.build_stream(
            MarketDataRequest(provider=PROVIDER_ALPACA_IEX, symbols=("A",))
        )

    snapshot = service.snapshot()
    assert snapshot.running is False
    assert snapshot.provider == ""
    assert snapshot.last_error is not None


def test_a_successful_rebuild_clears_a_previous_error(monkeypatch) -> None:
    """The snapshot must not keep reporting a stale failure forever."""

    service = _service()
    _install(monkeypatch, "AlpacaIEXStream")

    with pytest.raises(ValueError):
        service.build_stream(
            MarketDataRequest(provider="nope", symbols=("A",))
        )
    assert service.snapshot().last_error is not None

    service.build_stream(
        MarketDataRequest(provider=PROVIDER_ALPACA_IEX, symbols=("A",))
    )

    assert service.snapshot().last_error is None


# -- lifecycle: one stream at a time ------------------------------------


def test_a_second_build_is_refused_while_a_stream_is_live(
    monkeypatch,
) -> None:
    """Overwriting ``self._stream`` would drop the first stream out of
    management: it would keep running while the service reported the new
    one.  The service must refuse instead of stopping or replacing it.
    """

    created = _install(monkeypatch, "AlpacaIEXStream")
    service = _service()
    first = service.build_stream(
        MarketDataRequest(provider=PROVIDER_ALPACA_IEX, symbols=("A",))
    )

    with pytest.raises(module.MarketDataStreamActive):
        service.build_stream(
            MarketDataRequest(provider=PROVIDER_ALPACA_IEX, symbols=("B",))
        )

    # The first stream is still held, untouched, and nothing new was built.
    assert len(created) == 1
    assert service.snapshot().symbols == ("A",)
    assert service.snapshot().running is True
    # Refusing is not a stream failure: the old one is not stopped for us.
    assert first.stopped is False


def test_a_finished_stream_can_be_replaced(monkeypatch) -> None:
    """``build`` -> ``run`` -> finished -> the next ``build`` is legal."""

    created = _install(monkeypatch, "AlpacaIEXStream")
    service = _service()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_ALPACA_IEX, symbols=("A",))
    )
    service.run()  # returns immediately: the recorder's run() is inert

    assert service.snapshot().running is False

    service.build_stream(
        MarketDataRequest(provider=PROVIDER_ALPACA_IEX, symbols=("B",))
    )

    assert len(created) == 2
    assert service.snapshot().symbols == ("B",)
    assert service.snapshot().running is True


def test_a_stream_that_returns_normally_stops_reporting_running(
    monkeypatch,
) -> None:
    """``run`` returning on its own must clear ``running``, not just stop()."""

    _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",))
    )
    assert service.snapshot().running is True

    service.run()

    snapshot = service.snapshot()
    assert snapshot.running is False
    # Ending on its own is not an error.
    assert snapshot.last_error is None


def test_run_without_a_stream_is_refused() -> None:
    with pytest.raises(RuntimeError):
        _service().run()


def test_run_records_an_exception_and_still_clears_running(
    monkeypatch,
) -> None:
    """The fake-running regression: an exception must not leave ``True``."""

    created = _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",))
    )

    def explode() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(created[0], "run", explode)

    with pytest.raises(RuntimeError):
        service.run()

    snapshot = service.snapshot()
    assert snapshot.running is False
    assert snapshot.last_error == "RuntimeError: boom"


# -- lifecycle: a stop request is not a finished stream ------------------


def _run_in_a_thread(service: MarketDataService) -> tuple:
    """Start ``service.run()`` in its own thread and return its handles.

    Returns ``(thread, entered, release)``: the caller waits on ``entered``
    so the test only proceeds once ``run`` is genuinely inside the adapter,
    then sets ``release`` to let it return.  Waiting on an ``Event`` rather
    than sleeping is what makes this deterministic instead of timing-based.
    """

    entered = threading.Event()
    release = threading.Event()
    outcome: list[BaseException | None] = []

    original_run = service._stream.run

    def blocking_run() -> None:
        entered.set()
        release.wait(5)
        return original_run()

    service._stream.run = blocking_run

    def target() -> None:
        try:
            service.run()
        except BaseException as error:  # noqa: BLE001 - reported to the test
            outcome.append(error)
        else:
            outcome.append(None)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    assert entered.wait(5), "run() never reached the adapter"
    return thread, release, outcome


def test_a_stop_request_does_not_release_the_live_stream(
    monkeypatch,
) -> None:
    """The regression: ``stop()`` asked for a wind-down, not a completion.

    ``run()`` is still executing inside the adapter -- an IBKR socket loop
    takes real time to unwind.  Treating the stop request as "finished"
    would let a second stream be built, and a new connection config be
    applied, while the first one is still running.
    """

    created = _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("A",))
    )
    thread, release, outcome = _run_in_a_thread(service)
    try:
        service.stop()
        assert created[0].stopped is True

        # Still executing: nothing may be replaced or reconfigured yet.
        with pytest.raises(module.MarketDataStreamActive):
            service.build_stream(
                MarketDataRequest(provider=PROVIDER_IBKR, symbols=("B",))
            )
        with pytest.raises(module.MarketDataStreamActive):
            service.update_config(replace_config(client_id=99))
        with pytest.raises(module.MarketDataStreamActive):
            service.ensure_config_update_allowed(
                replace_config(client_id=99)
            )

        assert len(created) == 1
        assert service.snapshot().running is True
    finally:
        release.set()
        thread.join(5)

    assert not thread.is_alive()
    assert outcome == [None]

    # Only now that run() has returned is the slot free.  The config
    # change goes first because building B would make B live again, and a
    # live stream is exactly what the guard protects.
    service.update_config(replace_config(client_id=99))
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("B",))
    )

    assert len(created) == 2
    assert service.snapshot().symbols == ("B",)
    assert service.config.client_id == 99
    # The replacement really did see the updated config.
    assert created[1].args[0].client_id == 99


def test_a_stop_request_before_run_does_release_the_stream(
    monkeypatch,
) -> None:
    """Built but never run, then stopped: there is no execution to protect.

    This is the aborted-before-run case.  Refusing here would wedge the
    service permanently, because a stream that never ran can never finish.
    """

    created = _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("A",))
    )

    service.stop()

    # The stream really was asked to wind down before being dropped.
    assert created[0].stopped is True
    service.update_config(replace_config(client_id=99))
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("B",))
    )

    assert len(created) == 2
    assert service.config.client_id == 99
    assert created[1].args[0].client_id == 99


def test_a_failed_run_still_releases_the_stream(monkeypatch) -> None:
    """``run`` raising must free the slot: the exception is reported, but
    the service must not stay wedged behind a stream that no longer runs.
    """

    created = _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("A",))
    )

    def explode() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(created[0], "run", explode)

    with pytest.raises(RuntimeError):
        service.run()

    service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("B",))
    )

    assert len(created) == 2


def test_ensure_config_update_allowed_changes_nothing(monkeypatch) -> None:
    """It is a check, not an application: state must be untouched.

    The settings save path calls it *before* writing the file, so an
    accepted check that had already mutated the config would defeat the
    ordering it exists to establish.
    """

    _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()
    before = service.config

    service.ensure_config_update_allowed(replace_config(client_id=99))

    assert service.config == before


def test_ensure_config_update_allowed_allows_an_unchanged_config(
    monkeypatch,
) -> None:
    """Re-saving identical settings must not be blocked by a live stream."""

    _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",))
    )

    service.ensure_config_update_allowed(_config())  # must not raise


def test_update_config_is_refused_while_a_stream_is_live(
    monkeypatch,
) -> None:
    """The open connection is the one the stream was built with.

    The refusal is about a *change*: an identical config is a no-op, so
    re-saving unchanged settings is not blocked (see
    ``ensure_config_update_allowed``).
    """

    _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",))
    )
    before = service.config

    with pytest.raises(module.MarketDataStreamActive):
        service.update_config(replace_config(client_id=99))

    # Refused means unchanged, not half-applied.
    assert service.config == before


def test_update_config_is_allowed_once_the_stream_is_stopped(
    monkeypatch,
) -> None:
    _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",))
    )
    service.stop()

    updated = replace_config(client_id=99)

    service.update_config(updated)

    assert service.config == updated


def test_update_config_is_allowed_after_the_stream_finished(
    monkeypatch,
) -> None:
    _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",))
    )
    service.run()

    updated = replace_config(client_id=99)

    service.update_config(updated)

    assert service.config == updated


def test_update_config_only_affects_future_builds(monkeypatch) -> None:
    """No network work, no reconnect, no stream: the *next* build sees it."""

    created = _install(monkeypatch, "IBKRReadOnlyStream")
    service = _service()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",))
    )
    assert created[0].args == (_config(),)
    service.stop()

    updated = replace_config(client_id=99)
    service.update_config(updated)

    # Updating built nothing and ran nothing.
    assert len(created) == 1

    service.build_stream(
        MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",))
    )

    assert created[1].args == (updated,)


def test_snapshot_is_immutable() -> None:
    snapshot = MarketDataServiceSnapshot(
        provider=PROVIDER_IBKR, symbols=("SPY",), running=True, last_error=None
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.running = False  # type: ignore[misc]


def test_request_is_immutable() -> None:
    request = MarketDataRequest(provider=PROVIDER_IBKR, symbols=("SPY",))

    with pytest.raises(dataclasses.FrozenInstanceError):
        request.provider = PROVIDER_ALPACA_IEX  # type: ignore[misc]


def test_snapshot_carries_no_quote_data() -> None:
    """Quote truth stays with ``StreamSnapshot``."""

    field_names = {
        field.name
        for field in dataclasses.fields(MarketDataServiceSnapshot)
    }

    assert field_names == {"provider", "symbols", "running", "last_error"}


# -- boundary ---------------------------------------------------------


def _module_source() -> str:
    return inspect.getsource(module)


def test_module_does_not_import_any_gui_toolkit() -> None:
    """The service must stay UI-free, so no Qt import may sneak in."""

    tree = ast.parse(_module_source())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    forbidden = {"PySide6", "PyQt5", "PyQt6", "PySide2"}
    for name in imported:
        root = name.split(".")[0]
        assert root not in forbidden, f"{name} pulls a GUI toolkit in"


def test_module_does_not_import_qthread_or_widgets() -> None:
    """No Qt threading or widget class may be imported or referenced.

    Scanned from the AST's *names*, not the raw text: the docstring
    mentions these words on purpose (to say what is deliberately absent),
    and a substring search would flag its own explanation.
    """

    tree = ast.parse(_module_source())
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            referenced.update(alias.name for alias in node.names)

    for symbol in ("QThread", "QWidget", "MainWindow", "QObject"):
        assert symbol not in referenced, f"{symbol} must not be used here"


def test_module_has_no_desktop_dependency() -> None:
    """Importing the service must not drag the UI module in."""

    tree = ast.parse(_module_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("us_quant.desktop")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("us_quant.desktop")


def test_desktop_no_longer_constructs_providers_itself() -> None:
    """The whole point of the extraction: no provider branch in the UI."""

    desktop = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "us_quant"
        / "desktop.py"
    ).read_text(encoding="utf-8")

    for name in (
        "AlpacaIEXStream",
        "FinnhubTradeStream",
        "IBKRReadOnlyStream",
    ):
        assert name not in desktop, f"desktop.py still references {name}"


def test_desktop_delegates_venue_routing_to_the_service() -> None:
    desktop = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "us_quant"
        / "desktop.py"
    ).read_text(encoding="utf-8")

    assert "ibkr_market_data_exchange" not in desktop
    assert "desired_market_exchange" in desktop
