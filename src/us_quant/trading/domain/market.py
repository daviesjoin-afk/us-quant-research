"""Market data domain: historical bars and live Level-I quotes.

``Bar`` and ``MarketSlice`` are the historical research types and keep their
existing validation exactly.  The remaining types describe the live market
view the future Market Data v2 runtime will publish.

These models are provider-neutral on purpose.  They must never carry an IBKR
``marketDataType``, an Alpaca websocket object, a Finnhub trade id, a
``QThread`` or a Qt ``Signal`` -- those belong to adapters, and letting one in
here would tie the domain to a vendor and to a UI toolkit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from us_quant.trading.domain.common import ZERO


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol is required")
        if self.timestamp.tzinfo is None:
            raise ValueError("bar timestamp must be timezone-aware")
        prices = (self.open, self.high, self.low, self.close)
        if any(price <= ZERO for price in prices):
            raise ValueError("bar prices must be positive")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high price is inconsistent")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low price is inconsistent")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")


@dataclass(frozen=True, slots=True)
class MarketSlice:
    timestamp: datetime
    bars: dict[str, Bar]

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("market slice timestamp must be timezone-aware")
        if not self.bars:
            raise ValueError("market slice requires at least one bar")
        for symbol, bar in self.bars.items():
            if symbol != bar.symbol or bar.timestamp != self.timestamp:
                raise ValueError("bar key/timestamp does not match market slice")


@dataclass(frozen=True, slots=True)
class MarketQuote:
    """A single provider-neutral Level-I quote.

    ``source`` is the human-facing provider name (``IBKR``, ``Finnhub``, ...).
    ``realtime`` and ``stale`` describe the data's standing, not the transport:
    a quote can arrive over a healthy socket and still be stale, which is the
    distinction the existing freshness gates depend on.
    """

    symbol: str
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    close: Decimal | None
    observed_at: datetime
    source: str
    realtime: bool
    stale: bool


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    quotes: tuple[MarketQuote, ...]
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class MarketSubscription:
    symbols: tuple[str, ...]
    source: str | None = None


@dataclass(frozen=True, slots=True)
class MarketDataHealth:
    connected: bool
    source: str | None
    stale_symbols: tuple[str, ...]
    message: str
