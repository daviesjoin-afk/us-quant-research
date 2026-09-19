"""Market data composition root.

This is the one place allowed to know both the application service and the
concrete provider adapters.  It maps each stable source id onto a factory and
hands the mapping to :class:`MarketDataApplication`, which never imports an
adapter itself.

Keeping the wiring here is what lets the application stay provider-blind: the
policy values (stale thresholds, requested market-data type, venue, label,
coverage) are decided in the application, and only the *constructor call* is
assembled here.

The IBKR endpoint comes from a **getter**, not from a captured config.  The
connection settings are owned at runtime by
:class:`~us_quant.trading.application.accounts.BrokerAccountApplication`, and
the IBKR factory calls the getter on every prepare, so a settings change is
picked up by the next stream.  Closing over a start-up config here would
rebuild with the old endpoint after the settings transaction had reported
success -- the stale-config bug this arrangement exists to prevent.
"""

from __future__ import annotations

from collections.abc import Callable

from us_quant.extended_hours import ibkr_market_data_exchange
from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.adapters.alpaca.market_data import (
    ALPACA_SOURCE_LABEL,
    AlpacaIEXStream,
)
from us_quant.trading.adapters.finnhub.market_data import (
    FINNHUB_SOURCE_LABEL,
    FinnhubTradeStream,
)
from us_quant.trading.adapters.ibkr.market_data import (
    IBKR_COVERAGE,
    IBKR_EXTENDED_COVERAGE,
    IBKR_EXTENDED_SOURCE_LABEL,
    IBKR_SOURCE_LABEL,
    IBKRReadOnlyStream,
)
from us_quant.trading.application.market_data import (
    ALPACA_STALE_AFTER_SECONDS,
    FINNHUB_STALE_AFTER_SECONDS,
    IBKR_REQUESTED_MARKET_DATA_TYPE,
    IBKR_STALE_AFTER_SECONDS,
    SOURCE_ALPACA_IEX,
    SOURCE_FINNHUB_TRADES,
    SOURCE_IBKR,
    SOURCE_IBKR_EXTENDED,
    MarketDataApplication,
    MarketDataStartRequest,
    ProviderFactory,
)
from us_quant.trading.ports.market_data import SnapshotListener


def build_market_data_application(
    config_getter: Callable[[], IBKRConnectionConfig],
) -> MarketDataApplication:
    """Assemble the market data application with every provider wired.

    ``config_getter`` returns the *current* IBKR connection config.  It is
    called at prepare time, never at build time, so the endpoint the IBKR
    adapter is built with is the one in force when the stream starts.
    """

    def alpaca_factory(
        request: MarketDataStartRequest,
        listener: SnapshotListener | None,
    ) -> AlpacaIEXStream:
        return AlpacaIEXStream(
            symbols=request.symbols,
            api_key=request.credentials.alpaca_api_key,
            api_secret=request.credentials.alpaca_api_secret,
            stale_after_seconds=ALPACA_STALE_AFTER_SECONDS,
            listener=listener,
        )

    def finnhub_factory(
        request: MarketDataStartRequest,
        listener: SnapshotListener | None,
    ) -> FinnhubTradeStream:
        return FinnhubTradeStream(
            symbols=request.symbols,
            api_key=request.credentials.finnhub_api_key,
            stale_after_seconds=FINNHUB_STALE_AFTER_SECONDS,
            listener=listener,
        )

    def ibkr_factory(
        request: MarketDataStartRequest,
        listener: SnapshotListener | None,
    ) -> IBKRReadOnlyStream:
        extended = request.source_id == SOURCE_IBKR_EXTENDED
        # Resolved here, at prepare time, from the account application's
        # current config -- never captured when this composition ran.
        config = config_getter()
        # The application already resolved the venue onto the request, so the
        # adapter and the lifecycle report the same value.  IBKR is built
        # without a push listener: the desktop polls it through its snapshot
        # timer, and a listener as well would publish every quote twice.
        # ``listener`` is already ``None`` here because the application
        # filters it, and passing it through anyway keeps the adapter honest
        # about who decides.
        return IBKRReadOnlyStream(
            config,
            symbols=request.symbols,
            requested_market_data_type=IBKR_REQUESTED_MARKET_DATA_TYPE,
            stale_after_seconds=IBKR_STALE_AFTER_SECONDS,
            market_exchange=request.market_exchange or "SMART",
            provider_label=(
                IBKR_EXTENDED_SOURCE_LABEL
                if extended
                else IBKR_SOURCE_LABEL
            ),
            coverage=(
                IBKR_EXTENDED_COVERAGE if extended else IBKR_COVERAGE
            ),
            source_id=(
                SOURCE_IBKR_EXTENDED if extended else SOURCE_IBKR
            ),
            listener=listener,
        )

    factories: dict[str, ProviderFactory] = {
        SOURCE_ALPACA_IEX: alpaca_factory,
        SOURCE_FINNHUB_TRADES: finnhub_factory,
        SOURCE_IBKR: ibkr_factory,
        SOURCE_IBKR_EXTENDED: ibkr_factory,
    }
    return MarketDataApplication(
        factories=factories,
        exchange_resolver=ibkr_market_data_exchange,
    )


__all__ = [
    "ALPACA_SOURCE_LABEL",
    "FINNHUB_SOURCE_LABEL",
    "build_market_data_application",
]
