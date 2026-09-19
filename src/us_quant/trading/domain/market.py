"""Market data domain: historical bars, live quotes, and the upper-layer truth.

``Bar`` and ``MarketSlice`` are the historical research types and keep their
existing validation exactly.

``MarketQuote`` and ``MarketSnapshot`` are the **only** market-data truth the
layers above the adapters may consume.  Desktop, AutoQuant, Shadow and the
minute store read these; the provider transport DTOs
(``StreamQuote``/``StreamSnapshot``) are adapter-internal and must not appear
above ``trading/adapters``.

Two deliberate design points:

* ``mode`` is a semantic enum, not a vendor number.  IBKR's ``1/2/3/4``
  market-data types are interpreted in the IBKR adapter; nothing here knows
  that ``1`` means realtime, and no upper layer compares
  ``effective_market_data_type == 1``.
* ``source_id`` and ``source_label`` are separate.  Logic keys off the stable
  ``source_id`` (``alpaca_iex``, ``finnhub_trades``, ``ibkr``,
  ``ibkr_extended``); only presentation uses ``source_label``.  Reusing a
  display name such as ``"Alpaca"`` as a logic key is what this split ends.

Timestamps are timezone-aware ``datetime``.  ``updated_at`` is ``None`` when
the provider gave no usable timestamp -- the domain does not invent "now" to
stand in for a quote's update time, because that would make a stale quote look
fresh.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from us_quant.trading.domain.common import ZERO


class MarketDataMode(StrEnum):
    """How current the provider says a quote is.

    Vendor-neutral on purpose.  The IBKR adapter maps market-data type
    ``1/2/3/4`` onto these; the other providers set ``REALTIME`` because their
    feeds are live by construction.
    """

    REALTIME = "realtime"
    FROZEN = "frozen"
    DELAYED = "delayed"
    DELAYED_FROZEN = "delayed_frozen"
    UNKNOWN = "unknown"


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

    ``coverage`` carries the honesty label: Finnhub's bid/ask is a synthetic
    +/-5bps execution band around trade prints, not an NBBO, and that must
    survive the move into the domain rather than being smoothed away.
    """

    symbol: str

    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    close: Decimal | None

    bid_size: Decimal | None
    ask_size: Decimal | None

    mode: MarketDataMode

    updated_at: datetime | None
    age_seconds: float | None

    stale: bool
    stale_reason: str | None

    generation: int

    source_id: str
    source_label: str
    coverage: str

    @property
    def spread(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def realtime(self) -> bool:
        """Whether the provider claims this quote is live."""
        return self.mode is MarketDataMode.REALTIME

    @property
    def realtime_ready(self) -> bool:
        """Whether this quote may drive an intraday signal.

        Semantics are unchanged from the transport type it replaces::

            not stale
            AND realtime
            AND bid is not None
            AND ask is not None
            AND bid > 0
            AND ask >= bid
        """

        return (
            not self.stale
            and self.mode is MarketDataMode.REALTIME
            and self.bid is not None
            and self.ask is not None
            and self.bid > ZERO
            and self.ask >= self.bid
        )


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """One provider-neutral view of the feed.

    ``connected`` is the socket state and ``ready`` is the protocol handshake;
    they are separate because a socket can be up before the handshake and
    after it has been lost.  ``error_code``/``message`` carry the last provider
    error verbatim for the event log.
    """

    generation: int

    connected: bool
    ready: bool
    reconnect_attempt: int

    quotes: tuple[MarketQuote, ...]

    error_code: int | None
    message: str

    observed_at: datetime

    source_id: str
    source_label: str
    coverage: str

    @property
    def realtime_ready(self) -> bool:
        return any(quote.realtime_ready for quote in self.quotes)

    def quote_for(self, symbol: str) -> MarketQuote | None:
        """Return the quote for ``symbol``, or ``None``."""
        normalized = symbol.strip().upper()
        for quote in self.quotes:
            if quote.symbol == normalized:
                return quote
        return None


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
