"""Contract coverage for the market data application service.

These tests never touch the network.  Every provider adapter is replaced by a
recorder that captures the exact constructor arguments, so the assertions are
about *what the application decided*, not about WebSocket behaviour (which
``test_ibkr_stream``/``test_alpaca_stream``/``test_finnhub_stream`` own).

The behaviour pinned here is preservation, not preference: the stale
thresholds, the requested market data type, the IBKR venue routing, the source
labels and the coverage copy are the values the desktop used to pass inline
before any service existed.  A regression in any of them would silently change
what the feed reports about freshness.

Migrated from ``tests/test_market_data_service.py``.  Every behaviour in that
file is reproduced here against ``MarketDataApplication``; the additions are
the v2 requirements -- provider-blind construction through injected factories,
the provider-neutral credential error, and the resolved venue.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import threading
from pathlib import Path

import pytest

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.adapters.ibkr import market_data as ibkr_module
from us_quant.trading.application import market_data as module
from us_quant.trading.application.market_data import (
    ALPACA_STALE_AFTER_SECONDS,
    DEFAULT_MARKET_EXCHANGE,
    FINNHUB_STALE_AFTER_SECONDS,
    IBKR_REQUESTED_MARKET_DATA_TYPE,
    IBKR_STALE_AFTER_SECONDS,
    PUSH_LISTENER_SOURCES,
    SOURCE_ALPACA_IEX,
    SOURCE_FINNHUB_TRADES,
    SOURCE_IBKR,
    SOURCE_IBKR_EXTENDED,
    SUPPORTED_SOURCES,
    MarketDataApplication,
    MarketDataCredentials,
    MarketDataLifecycle,
    MarketDataStartRequest,
)
from us_quant.trading.composition import market_data as composition
from us_quant.trading.composition.market_data import (
    build_market_data_application,
)
from us_quant.trading.ports.market_data import (
    MarketDataActiveError,
    MarketDataCredentialsError,
)

# The labels and coverage copy live with the adapters that own them.
from us_quant.trading.adapters.ibkr.market_data import (
    IBKR_COVERAGE,
    IBKR_EXTENDED_COVERAGE,
    IBKR_EXTENDED_SOURCE_LABEL,
    IBKR_SOURCE_LABEL,
)
from us_quant.trading.adapters.alpaca.market_data import (
    AlpacaCredentialsMissing,
)
from us_quant.trading.adapters.finnhub.market_data import (
    FinnhubCredentialsMissing,
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

    def health(self) -> object:
        return object()


def _install(monkeypatch, name: str) -> list[_Recorder]:
    """Replace one adapter class in the composition root with a recorder.

    The adapters are built by `trading.composition.market_data`, so that is
    where the patch goes.  Patching the application module is impossible by
    design -- it does not import any adapter.
    """

    created: list[_Recorder] = []

    def recorder(*args, **kwargs) -> _Recorder:
        record = _Recorder(
            kwargs=kwargs, symbols=kwargs.get("symbols", ())
        )
        record.args = args
        created.append(record)
        return record

    monkeypatch.setattr(composition, name, recorder)
    return created


def _application(
    *,
    factories: dict | None = None,
    resolver=None,
) -> MarketDataApplication:
    return MarketDataApplication(
        factories=factories or {},
        exchange_resolver=resolver,
    )


def _app_with_recorder(
    monkeypatch,
    *,
    resolver=None,
    source: str = SOURCE_IBKR,
) -> tuple[MarketDataApplication, list[_Recorder]]:
    """An application whose one source builds a recorder."""

    created: list[_Recorder] = []

    def factory(request, listener):
        recorder = _Recorder(
            kwargs={
                "symbols": request.symbols,
                "credentials": request.credentials,
                "market_exchange": request.market_exchange,
                "listener": listener,
            },
            symbols=request.symbols,
        )
        recorder.request = request
        created.append(recorder)
        return recorder

    return (
        _application(factories={source: factory}, resolver=resolver),
        created,
    )


def replace_config(**changes) -> IBKRConnectionConfig:
    """A copy of the baseline config with ``changes`` applied."""

    return dataclasses.replace(_config(), **changes)


# -- provider selection ------------------------------------------------


def test_ibkr_request_builds_the_ibkr_read_only_stream(monkeypatch) -> None:
    created = _install(monkeypatch, "IBKRReadOnlyStream")
    app = build_market_data_application(_config)

    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY", "QQQ"))
    )

    assert len(created) == 1
    stream = created[0]
    assert stream.kwargs["symbols"] == ("SPY", "QQQ")
    assert stream.kwargs["requested_market_data_type"] == 1
    assert stream.kwargs["stale_after_seconds"] == IBKR_STALE_AFTER_SECONDS
    assert stream.kwargs["provider_label"] == IBKR_SOURCE_LABEL
    assert stream.kwargs["coverage"] == IBKR_COVERAGE
    assert stream.kwargs["market_exchange"] == DEFAULT_MARKET_EXCHANGE
    assert stream.kwargs["source_id"] == SOURCE_IBKR
    # The IBKR config is positional in the adapter's signature, and it comes
    # from the getter -- resolved at prepare time, not captured at build time.
    assert stream.args == (_config(),)


def test_extended_ibkr_keeps_label_coverage_and_venue_routing(
    monkeypatch,
) -> None:
    created = _install(monkeypatch, "IBKRReadOnlyStream")
    app = build_market_data_application(_config)
    monkeypatch.setattr(
        ibkr_module,
        "ibkr_market_data_exchange",
        lambda: "ARCA",
        raising=False,
    )

    app.prepare(
        MarketDataStartRequest(
            source_id=SOURCE_IBKR_EXTENDED, symbols=("SPY",)
        )
    )

    kwargs = created[0].kwargs
    assert kwargs["provider_label"] == IBKR_EXTENDED_SOURCE_LABEL
    assert kwargs["coverage"] == IBKR_EXTENDED_COVERAGE
    assert kwargs["requested_market_data_type"] == 1
    assert kwargs["source_id"] == SOURCE_IBKR_EXTENDED


def test_explicit_market_exchange_overrides_session_routing() -> None:
    """An explicit venue must win over whatever the session resolver says."""

    created: list[_Recorder] = []

    def factory(request, listener):
        recorder = _Recorder(
            kwargs={"market_exchange": request.market_exchange}
        )
        created.append(recorder)
        return recorder

    app = _application(
        factories={SOURCE_IBKR_EXTENDED: factory},
        resolver=lambda: "ARCA",
    )
    app.prepare(
        MarketDataStartRequest(
            source_id=SOURCE_IBKR_EXTENDED,
            symbols=("SPY",),
            market_exchange="SMART",
        )
    )

    assert created[0].kwargs["market_exchange"] == "SMART"


def test_desired_market_exchange_only_routes_the_extended_source() -> None:
    app = _application(resolver=lambda: "ARCA")

    assert app.desired_market_exchange(SOURCE_IBKR_EXTENDED) == "ARCA"
    for source in SUPPORTED_SOURCES:
        if source == SOURCE_IBKR_EXTENDED:
            continue
        assert app.desired_market_exchange(source) == "SMART"


# -- venue: resolved exactly once per prepare ---------------------------


def test_prepare_resolves_the_venue_once_and_commits_it() -> None:
    """The prepared venue is the one the adapter was actually given.

    The resolver follows the US equity session, so it can legitimately return
    a different answer on a second call.  Resolving twice would let the
    application report a venue the live adapter was never built for, and the
    desktop's session rotation would then skip a reconnect it owes.
    """

    created: list[_Recorder] = []
    calls: list[int] = []

    def resolver() -> str:
        calls.append(len(calls))
        return "SMART"

    def factory(request, listener):
        created.append(_Recorder(kwargs={"request": request}))
        return created[-1]

    app = _application(
        factories={SOURCE_IBKR_EXTENDED: factory}, resolver=resolver
    )

    app.prepare(
        MarketDataStartRequest(
            source_id=SOURCE_IBKR_EXTENDED, symbols=("SPY",)
        )
    )

    assert len(calls) == 1, "the resolver must run exactly once per prepare"
    assert created[0].kwargs["request"].market_exchange == "SMART"
    assert app.prepared_market_exchange == "SMART"


def test_a_changing_resolver_cannot_desynchronise_the_prepared_venue() -> None:
    """A resolver that flips mid-transaction must not produce a mismatch.

    This is the regression the single resolution exists for: with two calls the
    adapter would be built for ``SMART`` while ``prepared_market_exchange``
    reported ``OVERNIGHT``.
    """

    created: list[_Recorder] = []
    values = iter(["SMART", "OVERNIGHT"])
    calls: list[str] = []

    def resolver() -> str:
        value = next(values)
        calls.append(value)
        return value

    def factory(request, listener):
        created.append(_Recorder(kwargs={"request": request}))
        return created[-1]

    app = _application(
        factories={SOURCE_IBKR_EXTENDED: factory}, resolver=resolver
    )

    app.prepare(
        MarketDataStartRequest(
            source_id=SOURCE_IBKR_EXTENDED, symbols=("SPY",)
        )
    )

    assert calls == ["SMART"], "the resolver must not be consulted twice"
    assert created[0].kwargs["request"].market_exchange == "SMART"
    assert app.prepared_market_exchange == "SMART"
    assert app.prepared_market_exchange != "OVERNIGHT"


def test_an_explicit_venue_bypasses_the_resolver_entirely() -> None:
    created: list[_Recorder] = []
    calls: list[int] = []

    def resolver() -> str:
        calls.append(len(calls))
        return "OVERNIGHT"

    def factory(request, listener):
        created.append(_Recorder(kwargs={"request": request}))
        return created[-1]

    app = _application(
        factories={SOURCE_IBKR_EXTENDED: factory}, resolver=resolver
    )

    app.prepare(
        MarketDataStartRequest(
            source_id=SOURCE_IBKR_EXTENDED,
            symbols=("SPY",),
            market_exchange="SMART",
        )
    )

    assert calls == [], "an explicit venue must not consult the resolver"
    assert created[0].kwargs["request"].market_exchange == "SMART"
    assert app.prepared_market_exchange == "SMART"


def test_prepared_venue_defaults_before_any_prepare() -> None:
    app = _application(resolver=lambda: "OVERNIGHT")

    assert app.prepared_market_exchange == DEFAULT_MARKET_EXCHANGE


def test_a_failed_factory_does_not_half_update_the_prepared_venue() -> None:
    """A failed prepare must not leave a venue no adapter was built for.

    The previous successful prepare's venue stays in place, matching the
    existing semantics where a failed prepare also leaves the previous source
    id and symbols untouched.
    """

    created: list[_Recorder] = []
    values = iter(["SMART", "OVERNIGHT"])

    def resolver() -> str:
        return next(values)

    def good_factory(request, listener):
        created.append(_Recorder(kwargs={"request": request}))
        return created[-1]

    app = _application(
        factories={SOURCE_IBKR_EXTENDED: good_factory}, resolver=resolver
    )
    app.prepare(
        MarketDataStartRequest(
            source_id=SOURCE_IBKR_EXTENDED, symbols=("SPY",)
        )
    )
    assert app.prepared_market_exchange == "SMART"

    # Free the slot, then make the next factory fail after the resolver has
    # already produced a different venue.
    app.stop()

    def exploding_factory(request, listener):
        raise RuntimeError("socket exploded")

    app._factories[SOURCE_IBKR_EXTENDED] = exploding_factory

    with pytest.raises(RuntimeError):
        app.prepare(
            MarketDataStartRequest(
                source_id=SOURCE_IBKR_EXTENDED, symbols=("QQQ",)
            )
        )

    # Still the venue of the last *successful* prepare, not "OVERNIGHT".
    # This is the whole point: a failed attempt must not leave a venue that no
    # adapter was built for.
    assert app.prepared_market_exchange == "SMART"
    assert app.prepared_market_exchange != "OVERNIGHT"


def test_the_prepared_venue_tracks_a_successful_reprepare() -> None:
    """A later successful prepare does move the prepared venue."""

    created: list[_Recorder] = []
    values = iter(["SMART", "OVERNIGHT"])

    def resolver() -> str:
        return next(values)

    def factory(request, listener):
        created.append(_Recorder(kwargs={"request": request}))
        return created[-1]

    app = _application(
        factories={SOURCE_IBKR_EXTENDED: factory}, resolver=resolver
    )
    app.prepare(
        MarketDataStartRequest(
            source_id=SOURCE_IBKR_EXTENDED, symbols=("SPY",)
        )
    )
    assert app.prepared_market_exchange == "SMART"

    app.stop()
    app.prepare(
        MarketDataStartRequest(
            source_id=SOURCE_IBKR_EXTENDED, symbols=("SPY",)
        )
    )

    assert app.prepared_market_exchange == "OVERNIGHT"
    assert created[1].kwargs["request"].market_exchange == "OVERNIGHT"


def test_rotation_is_visible_when_the_session_moves_on() -> None:
    """The adapter's venue and the desired venue must be comparable.

    ``_maybe_rotate_extended_ibkr_session`` only reconnects when the live
    adapter's venue differs from the desired one, so the two values have to be
    independently obtainable -- and must actually differ once the session has
    moved on.
    """

    created: list[_Recorder] = []
    values = iter(["SMART", "OVERNIGHT"])

    def resolver() -> str:
        return next(values)

    def factory(request, listener):
        created.append(_Recorder(kwargs={"request": request}))
        return created[-1]

    app = _application(
        factories={SOURCE_IBKR_EXTENDED: factory}, resolver=resolver
    )
    app.prepare(
        MarketDataStartRequest(
            source_id=SOURCE_IBKR_EXTENDED, symbols=("SPY",)
        )
    )

    built_with = app.prepared_market_exchange
    desired = app.desired_market_exchange(SOURCE_IBKR_EXTENDED)

    assert built_with == "SMART"
    assert desired == "OVERNIGHT"
    assert built_with != desired, "rotation must be able to detect the change"


def test_the_adapter_is_not_exposed_publicly() -> None:
    """The application owns the adapter; callers read queries instead."""

    app = _application()
    assert not hasattr(app, "adapter"), (
        "the concrete adapter must stay private; callers use "
        "prepared_market_exchange / lifecycle / snapshot"
    )


def test_credentials_are_passed_through_for_alpaca_and_finnhub(
    monkeypatch,
) -> None:
    alpaca = _install(monkeypatch, "AlpacaIEXStream")
    finnhub = _install(monkeypatch, "FinnhubTradeStream")
    app = build_market_data_application(_config)

    app.prepare(
        MarketDataStartRequest(
            source_id=SOURCE_ALPACA_IEX,
            symbols=("AAPL",),
            credentials=MarketDataCredentials(
                alpaca_api_key="key-id",
                alpaca_api_secret="secret-value",
            ),
        )
    )
    assert alpaca[0].kwargs["api_key"] == "key-id"
    assert alpaca[0].kwargs["api_secret"] == "secret-value"
    assert alpaca[0].kwargs["stale_after_seconds"] == ALPACA_STALE_AFTER_SECONDS
    assert alpaca[0].kwargs["symbols"] == ("AAPL",)

    app.stop()
    app.prepare(
        MarketDataStartRequest(
            source_id=SOURCE_FINNHUB_TRADES,
            symbols=("MSFT",),
            credentials=MarketDataCredentials(finnhub_api_key="fh-key"),
        )
    )
    assert finnhub[0].kwargs["api_key"] == "fh-key"
    assert finnhub[0].kwargs["stale_after_seconds"] == FINNHUB_STALE_AFTER_SECONDS


def test_unknown_source_is_rejected_without_building_anything(
    monkeypatch,
) -> None:
    """A typo must fail closed, never fall back to a broker connection."""

    created = _install(monkeypatch, "IBKRReadOnlyStream")
    app = build_market_data_application(_config)

    with pytest.raises(ValueError) as error:
        app.prepare(
            MarketDataStartRequest(
                source_id="ibkr_extened", symbols=("SPY",)
            )
        )

    assert "ibkr_extened" in str(error.value)
    assert created == []
    # Fail closed also means "not running, and the reason is recorded".
    lifecycle = app.lifecycle()
    assert lifecycle.running is False
    assert lifecycle.source_id == ""
    assert "ibkr_extened" in (lifecycle.last_error or "")


def test_empty_source_is_rejected(monkeypatch) -> None:
    created = _install(monkeypatch, "IBKRReadOnlyStream")
    app = build_market_data_application(_config)

    with pytest.raises(ValueError):
        app.prepare(MarketDataStartRequest(source_id="", symbols=("SPY",)))

    assert created == []


def test_every_supported_source_builds(monkeypatch) -> None:
    """The whitelist and the dispatch must agree for all four sources."""

    _install(monkeypatch, "IBKRReadOnlyStream")
    _install(monkeypatch, "AlpacaIEXStream")
    _install(monkeypatch, "FinnhubTradeStream")
    app = build_market_data_application(_config)

    for source in SUPPORTED_SOURCES:
        app.prepare(
            MarketDataStartRequest(
                source_id=source,
                symbols=("SPY",),
                credentials=MarketDataCredentials(
                    alpaca_api_key="k",
                    alpaca_api_secret="s",
                    finnhub_api_key="f",
                ),
            )
        )
        # One live feed at a time: release it before preparing the next.
        app.stop()


def test_a_supported_source_without_a_factory_fails_closed() -> None:
    """A composition gap must not degrade into a silent no-op."""

    app = _application(factories={})
    with pytest.raises(ValueError, match="no market data factory"):
        app.prepare(
            MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",))
        )
    assert app.lifecycle().running is False


# -- listener routing --------------------------------------------------


def test_listener_reaches_the_push_sources(monkeypatch) -> None:
    alpaca = _install(monkeypatch, "AlpacaIEXStream")
    finnhub = _install(monkeypatch, "FinnhubTradeStream")
    listener = object()
    app = build_market_data_application(_config)

    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_ALPACA_IEX, symbols=("A",)),
        listener=listener,
    )
    app.stop()
    app.prepare(
        MarketDataStartRequest(
            source_id=SOURCE_FINNHUB_TRADES, symbols=("B",)
        ),
        listener=listener,
    )

    assert alpaca[0].kwargs["listener"] is listener
    assert finnhub[0].kwargs["listener"] is listener


def test_ibkr_is_not_given_a_push_listener(monkeypatch) -> None:
    """Preserved behaviour: IBKR is polled, so a listener would double-publish.

    The desktop's snapshot timer is the IBKR ingress path.  Handing the
    adapter a listener as well would emit every quote twice -- once queued
    through the signal and once from the poll.
    """

    for source in (SOURCE_IBKR, SOURCE_IBKR_EXTENDED):
        created = _install(monkeypatch, "IBKRReadOnlyStream")
        app = build_market_data_application(_config)

        app.prepare(
            MarketDataStartRequest(source_id=source, symbols=("SPY",)),
            listener=object(),
        )

        assert created[0].kwargs["listener"] is None


def test_listener_policy_is_reported_for_each_source() -> None:
    listener = object()

    assert (
        MarketDataApplication.listener_for(SOURCE_ALPACA_IEX, listener)
        is listener
    )
    assert (
        MarketDataApplication.listener_for(SOURCE_FINNHUB_TRADES, listener)
        is listener
    )
    assert MarketDataApplication.listener_for(SOURCE_IBKR, listener) is None
    assert (
        MarketDataApplication.listener_for(SOURCE_IBKR_EXTENDED, listener)
        is None
    )
    assert PUSH_LISTENER_SOURCES == {
        SOURCE_ALPACA_IEX,
        SOURCE_FINNHUB_TRADES,
    }


# -- lifecycle and snapshot -------------------------------------------


def test_lifecycle_reports_state_and_normalises_symbols(
    monkeypatch,
) -> None:
    created: list[_Recorder] = []

    def factory(request, listener):
        recorder = _Recorder(symbols=("AAPL", "MSFT"))
        created.append(recorder)
        return recorder

    app = _application(factories={SOURCE_ALPACA_IEX: factory})

    assert app.lifecycle() == MarketDataLifecycle(
        source_id="", symbols=(), running=False, last_error=None
    )
    app.prepare(
        MarketDataStartRequest(
            source_id=SOURCE_ALPACA_IEX, symbols=("aapl ", "msft")
        )
    )

    lifecycle = app.lifecycle()
    assert lifecycle.source_id == SOURCE_ALPACA_IEX
    # The adapter's own normalised watchlist is reported, not the raw request,
    # so the lifecycle cannot disagree with the subscription.
    assert lifecycle.symbols == ("AAPL", "MSFT")
    assert lifecycle.running is True
    assert lifecycle.last_error is None


def test_stop_is_forwarded_and_safe_before_any_prepare(monkeypatch) -> None:
    created = _install(monkeypatch, "AlpacaIEXStream")
    app = build_market_data_application(_config)

    app.stop()  # nothing prepared yet: must not raise
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_ALPACA_IEX, symbols=("A",))
    )
    app.stop()

    assert created[0].stopped is True
    assert app.lifecycle().running is False


def test_a_runtime_failure_is_recorded_without_tearing_down(
    monkeypatch,
) -> None:
    """A feed that raises is recorded, and never left reading as running.

    ``run`` owns the lifecycle half, so the failure is recorded there and
    ``finally`` clears ``running``.  The adapter is deliberately *not* torn
    down from inside the application -- whoever caught the failure drives the
    shutdown, so that this cannot race the desktop's stop ordering.
    """

    app, created = _app_with_recorder(monkeypatch, source=SOURCE_ALPACA_IEX)
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_ALPACA_IEX, symbols=("A",))
    )

    def explode() -> None:
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(created[0], "run", explode)

    with pytest.raises(RuntimeError):
        app.run()

    lifecycle = app.lifecycle()
    assert lifecycle.last_error == "RuntimeError: socket exploded"
    # The failure must not leave a fake running state behind.
    assert lifecycle.running is False
    assert created[0].stopped is False


def test_failed_construction_never_reports_running(monkeypatch) -> None:
    """Credentials missing is the realistic fail-closed case."""

    def factory(request, listener):
        raise AlpacaCredentialsMissing("missing")

    app = _application(factories={SOURCE_ALPACA_IEX: factory})

    with pytest.raises(MarketDataCredentialsError):
        app.prepare(
            MarketDataStartRequest(
                source_id=SOURCE_ALPACA_IEX, symbols=("A",)
            )
        )

    lifecycle = app.lifecycle()
    assert lifecycle.running is False
    assert lifecycle.source_id == ""
    assert lifecycle.last_error is not None


def test_a_successful_reprepare_clears_a_previous_error(monkeypatch) -> None:
    """The lifecycle must not keep reporting a stale failure forever."""

    app, _created = _app_with_recorder(
        monkeypatch, source=SOURCE_ALPACA_IEX
    )

    with pytest.raises(ValueError):
        app.prepare(
            MarketDataStartRequest(source_id="nope", symbols=("A",))
        )
    assert app.lifecycle().last_error is not None

    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_ALPACA_IEX, symbols=("A",))
    )

    assert app.lifecycle().last_error is None


# -- lifecycle: one feed at a time --------------------------------------


def test_a_second_prepare_is_refused_while_a_feed_is_live(
    monkeypatch,
) -> None:
    """Overwriting the adapter would drop the first feed out of management:
    it would keep running while the application reported the new one.  The
    application must refuse instead of stopping or replacing it.
    """

    app, created = _app_with_recorder(monkeypatch, source=SOURCE_ALPACA_IEX)
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_ALPACA_IEX, symbols=("A",))
    )
    first = created[0]

    with pytest.raises(MarketDataActiveError):
        app.prepare(
            MarketDataStartRequest(
                source_id=SOURCE_ALPACA_IEX, symbols=("B",)
            )
        )

    # The first feed is still held, untouched, and nothing new was built.
    assert len(created) == 1
    assert app.lifecycle().symbols == ("A",)
    assert app.lifecycle().running is True
    # Refusing is not a feed failure: the old one is not stopped for us.
    assert first.stopped is False


def test_a_finished_feed_can_be_replaced(monkeypatch) -> None:
    """``prepare`` -> ``run`` -> finished -> the next ``prepare`` is legal."""

    app, created = _app_with_recorder(monkeypatch, source=SOURCE_ALPACA_IEX)
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_ALPACA_IEX, symbols=("A",))
    )
    app.run()  # returns immediately: the recorder's run() is inert

    assert app.lifecycle().running is False

    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_ALPACA_IEX, symbols=("B",))
    )

    assert len(created) == 2
    assert app.lifecycle().symbols == ("B",)
    assert app.lifecycle().running is True


def test_a_feed_that_returns_normally_stops_reporting_running(
    monkeypatch,
) -> None:
    """``run`` returning on its own must clear ``running``, not just stop()."""

    app, _created = _app_with_recorder(monkeypatch)
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",))
    )
    assert app.lifecycle().running is True

    app.run()

    lifecycle = app.lifecycle()
    assert lifecycle.running is False
    # Ending on its own is not an error.
    assert lifecycle.last_error is None


def test_run_without_a_prepared_adapter_is_refused() -> None:
    with pytest.raises(RuntimeError):
        _application().run()


def test_run_records_an_exception_and_still_clears_running(
    monkeypatch,
) -> None:
    """The fake-running regression: an exception must not leave ``True``."""

    app, created = _app_with_recorder(monkeypatch)
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",))
    )

    def explode() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(created[0], "run", explode)

    with pytest.raises(RuntimeError):
        app.run()

    lifecycle = app.lifecycle()
    assert lifecycle.running is False
    assert lifecycle.last_error == "RuntimeError: boom"


# -- lifecycle: a stop request is not a finished feed --------------------


def _run_in_a_thread(app: MarketDataApplication, adapter: object) -> tuple:
    """Start ``app.run()`` in its own thread and return its handles.

    ``adapter`` is the recorder the test's factory produced, so the test drives
    the object the application built without the application having to expose
    it.

    Returns ``(thread, entered, release, outcome)``: the caller waits on
    ``entered`` so the test only proceeds once ``run`` is genuinely inside the
    adapter, then sets ``release`` to let it return.  Waiting on an ``Event``
    rather than sleeping is what makes this deterministic instead of
    timing-based.
    """

    entered = threading.Event()
    release = threading.Event()
    outcome: list[BaseException | None] = []

    original_run = adapter.run

    def blocking_run() -> None:
        entered.set()
        release.wait(5)
        return original_run()

    adapter.run = blocking_run

    def target() -> None:
        try:
            app.run()
        except BaseException as error:  # noqa: BLE001 - reported to the test
            outcome.append(error)
        else:
            outcome.append(None)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    assert entered.wait(5), "run() never reached the adapter"
    return thread, release, outcome


def test_a_stop_request_does_not_release_the_live_feed(
    monkeypatch,
) -> None:
    """The regression: ``stop()`` asked for a wind-down, not a completion.

    ``run()`` is still executing inside the adapter -- an IBKR socket loop
    takes real time to unwind.  Treating the stop request as "finished" would
    let a second feed be prepared, and a new connection config be applied,
    while the first one is still running.
    """

    app, created = _app_with_recorder(monkeypatch)
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("A",))
    )
    thread, release, outcome = _run_in_a_thread(app, created[0])
    try:
        app.stop()
        assert created[0].stopped is True

        # Still executing: nothing may be replaced or reconfigured yet.
        with pytest.raises(MarketDataActiveError):
            app.prepare(
                MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("B",))
            )
        with pytest.raises(MarketDataActiveError):
            app.ensure_reconfiguration_allowed()

        assert len(created) == 1
        assert app.lifecycle().running is True
    finally:
        release.set()
        thread.join(5)

    assert not thread.is_alive()
    assert outcome == [None]

    # Only now that run() has returned is the slot free.
    app.ensure_reconfiguration_allowed()
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("B",))
    )

    assert len(created) == 2
    assert app.lifecycle().symbols == ("B",)
    # The replacement resolved a venue rather than inheriting None.
    assert created[1].request.market_exchange is not None


def test_a_stop_request_before_run_does_release_the_feed(
    monkeypatch,
) -> None:
    """Prepared but never run, then stopped: no execution to protect.

    This is the aborted-before-run case.  Refusing here would wedge the
    application permanently, because a feed that never ran can never finish.
    """

    app, created = _app_with_recorder(monkeypatch)
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("A",))
    )

    app.stop()

    # The adapter really was asked to wind down before being dropped.
    assert created[0].stopped is True
    app.ensure_reconfiguration_allowed()
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("B",))
    )

    assert len(created) == 2
    assert app.lifecycle().symbols == ("B",)


def test_a_failed_run_still_releases_the_feed(monkeypatch) -> None:
    """``run`` raising must free the slot: the exception is reported, but the
    application must not stay wedged behind a feed that no longer runs.
    """

    app, created = _app_with_recorder(monkeypatch)
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("A",))
    )

    def explode() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(created[0], "run", explode)

    with pytest.raises(RuntimeError):
        app.run()

    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("B",))
    )

    assert len(created) == 2


def test_ensure_reconfiguration_allowed_changes_nothing(
    monkeypatch,
) -> None:
    """It is a check, not an application: state must be untouched.

    The settings save path calls it *before* writing the file, so an accepted
    check that had already mutated runtime state would defeat the ordering it
    exists to establish.
    """

    app, _created = _app_with_recorder(monkeypatch)
    before = app.lifecycle()

    app.ensure_reconfiguration_allowed()

    assert app.lifecycle() == before


def test_ensure_reconfiguration_allowed_allows_an_idle_application(
    monkeypatch,
) -> None:
    """An application with no feed may always be reconfigured."""

    app, _created = _app_with_recorder(monkeypatch)

    app.ensure_reconfiguration_allowed()  # must not raise


def test_ensure_reconfiguration_allowed_is_refused_while_a_feed_is_live(
    monkeypatch,
) -> None:
    """The open connection is the one the feed was built with."""

    app, _created = _app_with_recorder(monkeypatch)
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",))
    )

    with pytest.raises(MarketDataActiveError):
        app.ensure_reconfiguration_allowed()


def test_ensure_reconfiguration_allowed_after_a_stop_request_before_run(
    monkeypatch,
) -> None:
    """The one releasable case: prepared, stopped, never run."""

    app, _created = _app_with_recorder(monkeypatch)
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",))
    )
    app.stop()

    app.ensure_reconfiguration_allowed()  # must not raise


def test_ensure_reconfiguration_allowed_after_the_feed_finished(
    monkeypatch,
) -> None:
    """A feed that ran and returned no longer holds the connection."""

    app, _created = _app_with_recorder(monkeypatch)
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",))
    )
    app.run()

    app.ensure_reconfiguration_allowed()  # must not raise


def test_the_application_no_longer_owns_a_connection_config(
    monkeypatch,
) -> None:
    """The transitional config dependency is gone, not merely unused.

    Broker/Account v2 moved connection ownership to
    ``BrokerAccountApplication``.  If a ``config`` attribute or an
    ``update_config`` method came back here, the two runtimes would again
    disagree about which endpoint is in force -- the stale-config bug.
    """

    app, _created = _app_with_recorder(monkeypatch)

    assert not hasattr(app, "config")
    assert not hasattr(app, "update_config")
    assert not hasattr(app, "ensure_config_update_allowed")


def test_the_factory_is_never_handed_a_connection_config(
    monkeypatch,
) -> None:
    """The factory receives the request and the listener, nothing else.

    A config parameter was what forced the application to import
    ``us_quant.ibkr``.  The IBKR composition reads the endpoint from the
    account application's getter at prepare time instead.
    """

    app, created = _app_with_recorder(monkeypatch)
    app.prepare(
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",))
    )

    assert len(created) == 1
    assert not hasattr(created[0], "config")


# -- immutability ------------------------------------------------------


def test_lifecycle_is_immutable() -> None:
    lifecycle = MarketDataLifecycle(
        source_id=SOURCE_IBKR, symbols=("SPY",), running=True, last_error=None
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        lifecycle.running = False  # type: ignore[misc]


def test_request_is_immutable() -> None:
    request = MarketDataStartRequest(
        source_id=SOURCE_IBKR, symbols=("SPY",)
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        request.source_id = SOURCE_ALPACA_IEX  # type: ignore[misc]


def test_lifecycle_carries_no_quote_data() -> None:
    """Quote truth stays with the domain ``MarketSnapshot``."""

    field_names = {
        field.name for field in dataclasses.fields(MarketDataLifecycle)
    }

    assert field_names == {"source_id", "symbols", "running", "last_error"}


def test_credentials_never_appear_in_repr() -> None:
    """A secret must not leak through a log line or an exception repr."""

    credentials = MarketDataCredentials(
        alpaca_api_key="alpaca-key",
        alpaca_api_secret="alpaca-secret",
        finnhub_api_key="finnhub-key",
    )
    rendered = repr(credentials)

    assert "alpaca-key" not in rendered
    assert "alpaca-secret" not in rendered
    assert "finnhub-key" not in rendered

    request = MarketDataStartRequest(
        source_id=SOURCE_ALPACA_IEX,
        symbols=("AAPL",),
        credentials=credentials,
    )
    rendered_request = repr(request)
    assert "alpaca-key" not in rendered_request
    assert "alpaca-secret" not in rendered_request
    assert "finnhub-key" not in rendered_request


# -- boundary ---------------------------------------------------------


def _module_source() -> str:
    return inspect.getsource(module)


def test_module_does_not_import_any_concrete_adapter() -> None:
    """The application must stay provider-blind.

    This is the load-bearing structural rule of the migration: if the
    application could import an adapter, the dependency arrow would point
    outward and the port would be decorative.
    """

    tree = ast.parse(_module_source())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    for name in imported:
        assert "adapters" not in name, f"{name} reaches into adapters"
        assert not name.startswith("us_quant.ibkr_stream")
        assert not name.startswith("us_quant.alpaca_stream")
        assert not name.startswith("us_quant.finnhub_stream")
        assert name != "ibapi"
        assert not name.startswith("ibapi.")


def test_module_does_not_import_any_gui_toolkit() -> None:
    """The application must stay UI-free, so no Qt import may sneak in."""

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

    Scanned from the AST's *names*, not the raw text: the docstring mentions
    these words on purpose (to say what is deliberately absent), and a
    substring search would flag its own explanation.
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
    """Importing the application must not drag the UI module in."""

    tree = ast.parse(_module_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("us_quant.desktop")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("us_quant.desktop")


def test_only_composition_names_the_concrete_adapters() -> None:
    """The composition root is the one place allowed to know both sides."""

    composition = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "us_quant"
        / "trading"
        / "composition"
        / "market_data.py"
    ).read_text(encoding="utf-8")

    for name in (
        "AlpacaIEXStream",
        "FinnhubTradeStream",
        "IBKRReadOnlyStream",
    ):
        assert name in composition, f"composition should wire {name}"


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


def test_desktop_no_longer_imports_a_concrete_adapter() -> None:
    """Desktop must know only the application and the domain."""

    desktop = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "us_quant"
        / "desktop.py"
    ).read_text(encoding="utf-8")

    for name in (
        "us_quant.ibkr_stream",
        "us_quant.alpaca_stream",
        "us_quant.finnhub_stream",
        "us_quant.market_data_service",
        "trading.adapters",
    ):
        assert name not in desktop, f"desktop.py still imports {name}"


def test_desktop_delegates_venue_routing_to_the_application() -> None:
    """The venue choice is the application's; the market orchestrator asks it.

    v2O-A moved the IBKR 5x24 session check out of ``MainWindow`` into
    ``MarketOrchestrator``, so the call site moved with it.  The property under
    test is unchanged: nobody computes the exchange locally, the application is
    asked.
    """

    root = Path(__file__).resolve().parents[1] / "src" / "us_quant"
    desktop = (root / "desktop.py").read_text(encoding="utf-8")
    orchestrator = (
        root
        / "desktop_v2"
        / "orchestration"
        / "market"
        / "orchestrator.py"
    ).read_text(encoding="utf-8")

    assert "ibkr_market_data_exchange" not in desktop
    assert "ibkr_market_data_exchange" not in orchestrator
    assert "desired_market_exchange" in orchestrator
