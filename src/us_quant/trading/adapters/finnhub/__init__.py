"""Finnhub market data adapter."""

from __future__ import annotations

from us_quant.trading.adapters.finnhub.market_data import (
    FINNHUB_KEY_ENV,
    FINNHUB_SOURCE_LABEL,
    FINNHUB_WEBSOCKET_URL,
    SOURCE_FINNHUB_TRADES,
    FinnhubCredentialsMissing,
    FinnhubRejectedError,
    FinnhubTradeStream,
    classify_connect_error,
    proxy_from_environment,
)

__all__ = [
    "FINNHUB_KEY_ENV",
    "FINNHUB_SOURCE_LABEL",
    "FINNHUB_WEBSOCKET_URL",
    "SOURCE_FINNHUB_TRADES",
    "FinnhubCredentialsMissing",
    "FinnhubRejectedError",
    "FinnhubTradeStream",
    "classify_connect_error",
    "proxy_from_environment",
]
