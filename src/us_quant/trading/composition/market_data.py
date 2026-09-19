"""Market data composition root.

This is the one place allowed to know both the application service and the
concrete provider adapters.  It maps each stable source id onto a factory and
hands the mapping to :class:`MarketDataApplication`, which never imports an
adapter itself.

Keeping the wiring here is what lets the application stay provider-blind: the
policy values (stale thresholds, requested market-data type, venue, label,
coverage) are decided in the application, and only the *constructor call* is
assembled here.

Each factory takes the application's current ``IBKRConnectionConfig`` as an
argument rather than closing over the one passed to this function.  A closure
would keep rebuilding with the start-up config after ``update_config`` had
been accepted -- the settings transaction would report success while the next
feed still used the old endpoint.
"""

from __future__ import annotations

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
    config: IBKRConnectionConfig,
) -> MarketDataApplication:
    """Assemble the market data application with every provider wired."""

    def alpaca_factory(
        request: MarketDataStartRequest,
        listener: SnapshotListener | None,
        config: IBKRConnectionConfig,
    ) -> AlpacaIEXStream:
        del config  # Alpaca does not use the IBKR endpoint
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
        config: IBKRConnectionConfig,
    ) -> FinnhubTradeStream:
        del config  # Finnhub does not use the IBKR endpoint
        return FinnhubTradeStream(
            symbols=request.symbols,
            api_key=request.credentials.finnhub_api_key,
            stale_after_seconds=FINNHUB_STALE_AFTER_SECONDS,
            listener=listener,
        )

    def ibkr_factory(
        request: MarketDataStartRequest,
        listener: SnapshotListener | None,
        config: IBKRConnectionConfig,
    ) -> IBKRReadOnlyStream:
        extended = request.source_id == SOURCE_IBKR_EXTENDED
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
        config,
        factories=factories,
        exchange_resolver=ibkr_market_data_exchange,
    )


__all__ = [
    "ALPACA_SOURCE_LABEL",
    "FINNHUB_SOURCE_LABEL",
    "build_market_data_application",
]
