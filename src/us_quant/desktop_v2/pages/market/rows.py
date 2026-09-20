"""Quote facts projected into display rows, without a widget toolkit.

The table must not interpret ``MarketDataMode``, ``realtime_ready`` or
``stale``; this module does that interpretation once, here, and the Qt table
receives finished strings plus a tone.  That is why these functions are
Qt-free and why the price and age formatting lives here rather than in the
model: a formatting rule that the table owned could not be tested without a
widget, and would drift from the shadow and position tables that show the same
kind of numbers.

The parsing helper exists for the same reason in the other direction: turning
the operator's comma-separated text into a symbol tuple is presentation, but
*how many* symbols are acceptable is not -- the caller still decides that.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from us_quant.desktop_v2.pages.market.models import (
    MarketQuoteRow,
    MarketRowTone,
)
from us_quant.trading.domain.market import MarketDataMode


#: Presentation labels for the market data mode.  The domain carries the
#: semantic mode; the UI decides how to spell it.
MODE_LABELS = {
    MarketDataMode.REALTIME: "实时",
    MarketDataMode.FROZEN: "冻结",
    MarketDataMode.DELAYED: "延迟",
    MarketDataMode.DELAYED_FROZEN: "延迟冻结",
    MarketDataMode.UNKNOWN: "未知",
}

#: Sources whose feed is a stream of prints rather than a book.  Kept here as a
#: spelling, not a capability: the page only uses it to choose a label.
TRADE_FEED_SOURCES = ("finnhub_trades",)
PUSH_LISTENER_SOURCES = ("alpaca_iex", "finnhub_trades")
ALPACA_IEX_SOURCE = "alpaca_iex"

#: The columns whose cells are tinted.  The status column and the ones an
#: operator scans for a stale symbol.
STALE_TONE_COLUMNS = frozenset({0, 6, 10, 12, 13})
READY_TONE_COLUMNS = frozenset({0, 6, 10, 12})


def price(value: Decimal | None) -> str:
    """A price as the tables spell it, or an em dash when there is none."""

    if value is None:
        return "—"
    return f"{float(value):,.4f}".rstrip("0").rstrip(".")


def symbol_text(raw: str) -> str:
    """The operator's comma-separated text as an ordered, deduped tuple.

    Splitting, stripping, upper-casing and de-duplicating are presentation: they
    describe what the operator typed.  Whether the result is *acceptable* -- at
    least one symbol, at most thirty, whether it may change mid-session -- is
    decided by the orchestration layer, which is why this returns a tuple and
    passes no judgement on its size.
    """

    return tuple(
        dict.fromkeys(
            item.strip().upper()
            for item in raw.split(",")
            if item.strip()
        )
    )


def joined_symbols(symbols: Sequence[str]) -> str:
    """The inverse: the text the page puts back into its subscription field."""

    return ",".join(symbols)


def mode_label(mode: object) -> str:
    """The display name of a market data mode; unknown modes read as unknown."""

    return MODE_LABELS.get(mode, "未知")


def feed_label(
    *, source_id: str, modes: Sequence[object]
) -> str:
    """The "行情类型" card's value for a snapshot.

    The two push listeners are named by what they actually are -- an IEX book
    and a stream of trades -- because a generic "实时" would hide the coverage
    difference the operator has to know about.  Everything else is described by
    the modes the provider reported.
    """

    if source_id == ALPACA_IEX_SOURCE:
        return "IEX 实时"
    if source_id in TRADE_FEED_SOURCES:
        return "实时成交"
    if modes:
        return " / ".join(
            mode_label(mode)
            for mode in sorted(modes, key=lambda item: getattr(item, "value", ""))
        )
    return "等待回调"


def connection_label(snapshot: object) -> str:
    """Handshaken, merely connected, or gone."""

    if getattr(snapshot, "ready", False):
        return "已握手"
    if getattr(snapshot, "connected", False):
        return "端口已连"
    return "已断开"


def age_text(age_seconds: float | None) -> str:
    return f"{age_seconds:.1f}" if age_seconds is not None else "—"


def updated_text(updated_at: datetime | None) -> str:
    return updated_at.isoformat() if updated_at is not None else "未收到"


def row_tone(*, realtime_ready: bool, stale: bool) -> MarketRowTone:
    """The tone a quote row carries; stale outranks ready."""

    if stale:
        return MarketRowTone.ERROR
    if realtime_ready:
        return MarketRowTone.SUCCESS
    return MarketRowTone.NEUTRAL


def quote_rows(snapshot: object) -> tuple[MarketQuoteRow, ...]:
    """Project every quote of a snapshot into one display row."""

    rendered: list[MarketQuoteRow] = []
    for quote in getattr(snapshot, "quotes", ()) or ():
        rendered.append(
            MarketQuoteRow(
                symbol=quote.symbol,
                bid=price(quote.bid),
                ask=price(quote.ask),
                last=price(quote.last),
                close=price(quote.close),
                spread=price(quote.spread),
                mode=mode_label(quote.mode),
                updated_at=updated_text(quote.updated_at),
                age=age_text(quote.age_seconds),
                generation=str(quote.generation),
                source=quote.source_label,
                coverage=quote.coverage,
                status="READY" if quote.realtime_ready else "STALE",
                reason=quote.stale_reason or "可用于日内观察",
                tone=row_tone(
                    realtime_ready=bool(quote.realtime_ready),
                    stale=bool(quote.stale),
                ),
            )
        )
    return tuple(rendered)


def display_values(rows: Sequence[MarketQuoteRow]) -> tuple[tuple[str, ...], ...]:
    """A row's cells in the table's own column order.

    The dataclass declares its fields in column order, so there is one place to
    change when a column moves.
    """

    return tuple(
        tuple(
            str(getattr(row, field.name))
            for field in fields(row)
            if field.name != "tone"
        )
        for row in rows
    )


__all__ = [
    "ALPACA_IEX_SOURCE",
    "MODE_LABELS",
    "PUSH_LISTENER_SOURCES",
    "READY_TONE_COLUMNS",
    "STALE_TONE_COLUMNS",
    "TRADE_FEED_SOURCES",
    "age_text",
    "connection_label",
    "display_values",
    "feed_label",
    "joined_symbols",
    "mode_label",
    "price",
    "quote_rows",
    "row_tone",
    "symbol_text",
    "updated_text",
]
