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
    """``record_failure`` notes the error; the caller still drives stop.

    Tearing the stream down from here would race the desktop's stop
    ordering, so the service only records.
    """

    created = _install(monkeypatch, "AlpacaIEXStream")
    service = _service()
    service.build_stream(
        MarketDataRequest(provider=PROVIDER_ALPACA_IEX, symbols=("A",))
    )

    service.record_failure("RuntimeError: socket exploded")

    snapshot = service.snapshot()
    assert snapshot.last_error == "RuntimeError: socket exploded"
    # Still reported as running: nobody asked it to stop yet.
    assert snapshot.running is True
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
