"""Table row projections for the execution page.

One responsibility: turn already-fetched facts into the display strings the
tables and cards draw.  Split out of ``presenter`` because the two halves have
different readers -- the cards and the control state are read as a whole, while
the rows are consumed one table at a time -- and because a single module holding
both would have to be read through the half you did not want.

The direction is one-way: this module knows only the models, and ``presenter``
imports the row builders from here.  The display formatters live here rather than
in the presenter because a row and the amount inside it are the same job, and
because putting them in the presenter is what made the two modules import each
other.

Like the presenter, this module is Qt-free and service-free: it computes strings
and tones, and the tables decide which cell the tone colours.

Two operator-visible conventions are carried over unchanged from the legacy
builder: the broker's position list is the answer and the local book is only a
fallback, and a candidate's realtime column says *fresh*, *recent* or *waiting*
using the window's own 30-second recency fact rather than the quote's age.
"""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from typing import Callable, Mapping, Sequence

from us_quant.desktop_v2.pages.execution.models import (
    CandidateRealtime,
    CandidateRow,
    FillRow,
    LatencyRow,
    OrderRow,
    PositionRow,
    ShadowRow,
    Tone,
)


def money(value: Decimal | float | int | None, *, signed: bool = False) -> str:
    """Render an amount the way the account page does."""

    if value is None:
        return "不可用"
    number = float(value)
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}${number:,.2f}"


def price(value: Decimal | None) -> str:
    """Render a limit or mark without trailing zeros."""

    if value is None:
        return "—"
    return f"{float(value):,.4f}".rstrip("0").rstrip(".")


def display_values(rows: Sequence[object]) -> tuple[tuple[str, ...], ...]:
    """The display columns of each row, in field-declaration order.

    ``tone`` is a presentation instruction rather than a column, so it is
    excluded by name rather than by position -- a column that moved would
    otherwise silently change which cell is coloured.
    """

    return tuple(
        tuple(
            str(getattr(row, field.name))
            for field in fields(row)
            if field.name != "tone"
        )
        for row in rows
    )


def row_tones(rows: Sequence[object]) -> tuple[Tone, ...]:
    """The tone of each row, defaulting to neutral for rows without one."""

    return tuple(Tone(getattr(row, "tone", Tone.NEUTRAL)) for row in rows)


#: The research shadow band the page displays: the same +/-5 bps convention the
#: shadow engine applies to its own fills, shown here as a limit band.
SHADOW_BAND_BPS = Decimal("5")

#: Latency thresholds in milliseconds: comfortable, noticeable, alarming.
LATENCY_FAST_MS = 120
LATENCY_SLOW_MS = 500

#: How many executions the fill table shows, most recent first.
RECENT_FILL_LIMIT = 20


def shadow_price(value: Decimal, *, side: str) -> Decimal:
    """Apply the displayed band to a quote, on the side a fill would cross."""

    direction = Decimal("1") if side == "BUY" else Decimal("-1")
    return value * (Decimal("1") + direction * SHADOW_BAND_BPS / Decimal("10000"))


def mark_for(quote: object | None, fallback: Decimal) -> Decimal:
    """The mid of a two-sided quote, or the cost basis when there is none."""

    bid = getattr(quote, "bid", None)
    ask = getattr(quote, "ask", None)
    if bid is None or ask is None:
        return fallback
    return (bid + ask) / Decimal("2")


def position_rows(
    snapshot: object,
    broker_positions: Sequence[object],
    quotes: Mapping[str, object],
) -> tuple[PositionRow, ...]:
    """Value every holding at the freshest mark the page has.

    A broker holding has no session history, so its hold time and source say so
    rather than borrowing the session's; a fractional broker quantity is skipped
    instead of being rounded into a whole-share display.
    """

    if broker_positions:
        displayed = [
            (row.symbol, int(row.quantity), row.average_cost, "—", "IBKR Paper position")
            for row in broker_positions
            if row.quantity > 0 and row.quantity == int(row.quantity)
        ]
    else:
        displayed = [
            (row.symbol, row.quantity, row.average_price, row.opened_at, row.provider)
            for row in tuple(getattr(snapshot, "positions", ()) or ())
        ]
    rows: list[PositionRow] = []
    for symbol, quantity, average_price, held_for, source in displayed:
        mark = mark_for(quotes.get(symbol), average_price)
        rows.append(
            PositionRow(
                symbol=symbol,
                quantity=str(quantity),
                average_price=price(average_price),
                mark=price(mark),
                unrealized=money((mark - average_price) * quantity, signed=True),
                held_for=held_for,
                source=source,
            )
        )
    return tuple(rows)


def fill_rows(snapshot: object) -> tuple[FillRow, ...]:
    """The most recent executions, newest first."""

    fills = tuple(getattr(snapshot, "fills", ()) or ())
    return tuple(
        FillRow(
            occurred_at=fill.occurred_at,
            symbol=fill.symbol,
            side=fill.side,
            quantity=str(fill.quantity),
            price=price(fill.price),
            commission=money(fill.estimated_commission),
            realized=money(fill.realized_pnl, signed=True),
        )
        for fill in reversed(fills[-RECENT_FILL_LIMIT:])
    )


def shadow_rows(
    candidates: Sequence[object],
    quotes: Mapping[str, object],
    pending_by_symbol: Mapping[str, object],
) -> tuple[ShadowRow, ...]:
    """The research-only shadow band for every candidate."""

    rows: list[ShadowRow] = []
    for candidate in candidates:
        quote = quotes.get(candidate.symbol)
        bid = getattr(quote, "bid", None)
        ask = getattr(quote, "ask", None)
        intent = pending_by_symbol.get(candidate.symbol)
        if bid is None or ask is None:
            status, tone = "等待行情", Tone.WARNING
        elif intent is None:
            status, tone = "无待挂单", Tone.NEUTRAL
        else:
            status, tone = "策略限价已挂", Tone.SUCCESS
        rows.append(
            ShadowRow(
                symbol=candidate.symbol,
                bid=price(bid),
                ask=price(ask),
                shadow_buy=(
                    price(shadow_price(ask, side="BUY")) if ask is not None else "—"
                ),
                shadow_sell=(
                    price(shadow_price(bid, side="SELL")) if bid is not None else "—"
                ),
                limit_price=price(intent.limit_price) if intent is not None else "—",
                status=status,
                tone=tone,
            )
        )
    return tuple(rows)


def latency_rows(rows: Sequence[Mapping[str, object]]) -> tuple[LatencyRow, ...]:
    """The submission latencies, with the thresholds the operator watches."""

    rendered: list[LatencyRow] = []
    for row in rows:
        latency_ms = row.get("submit_latency_ms")
        if latency_ms is None:
            continue
        value = int(latency_ms)
        rendered.append(
            LatencyRow(
                intent_id=str(row.get("intent_id", "")),
                symbol=str(row.get("symbol", "")),
                side=str(row.get("side", "")),
                latency=f"{value} ms",
                generated_at=str(row.get("observed_at", ""))[:19].replace("T", " "),
                tone=latency_tone(value),
            )
        )
    return tuple(rendered)


def latency_tone(latency_ms: int) -> Tone:
    """How a submission latency should read."""

    if latency_ms <= LATENCY_FAST_MS:
        return Tone.SUCCESS
    if latency_ms <= LATENCY_SLOW_MS:
        return Tone.WARNING
    return Tone.ERROR


def tier_label(leader_tier: int) -> str:
    """The operator's word for a leader tier."""

    if leader_tier == 1:
        return "龙头"
    if leader_tier == 2:
        return "优质二线"
    return f"层级{leader_tier}"


def candidate_static_key(candidates: Sequence[object]) -> tuple[tuple[str, ...], ...]:
    """The identity of the candidate set, used to skip a static rebuild."""

    return tuple(
        (
            candidate.symbol,
            candidate.name,
            candidate.sector,
            str(candidate.leader_tier),
            str(candidate.scan_score),
            candidate.signal,
        )
        for candidate in candidates
    )


def candidate_rows(candidates: Sequence[object]) -> tuple[CandidateRow, ...]:
    """The scan columns for every candidate."""

    return tuple(
        CandidateRow(
            symbol=candidate.symbol,
            name=candidate.name,
            sector=candidate.sector,
            tier=tier_label(candidate.leader_tier),
            scan_score=f"{candidate.scan_score:.1f}",
            signal=candidate.signal,
        )
        for candidate in candidates
    )


def candidate_realtime(
    candidates: Sequence[object],
    quotes: Mapping[str, object],
    recently_ready: Callable[[str], bool],
) -> tuple[CandidateRealtime, ...]:
    """The live column: is this candidate's quote fresh, recent or missing."""

    rendered: list[CandidateRealtime] = []
    for candidate in candidates:
        quote = quotes.get(candidate.symbol)
        if quote is not None and getattr(quote, "realtime_ready", False):
            rendered.append(CandidateRealtime("当前 fresh"))
        elif recently_ready(candidate.symbol):
            rendered.append(CandidateRealtime("近30秒有实时成交"))
        else:
            rendered.append(CandidateRealtime("等待", Tone.WARNING))
    return tuple(rendered)


def order_rows(
    reconciliations: Sequence[object],
    audit_by_intent: Mapping[str, Mapping[str, object]],
) -> tuple[OrderRow, ...]:
    """The reconciled session orders, with the store's own explanation."""

    rows: list[OrderRow] = []
    for row in reconciliations:
        audit = audit_by_intent.get(row.intent_id, {})
        status = "已核对" if row.reconciled else row.latest_status or "等待首次状态"
        tone = (
            Tone.SUCCESS
            if row.reconciled
            else Tone.ERROR
            if row.terminal
            else Tone.WARNING
        )
        explanation = f"{row.reason} · {str(audit.get('reason') or '')}".strip(" ·")
        rows.append(
            OrderRow(
                status=status,
                symbol=row.symbol,
                side=row.side,
                quantities=f"{row.intended_quantity}/{row.executed_quantity}",
                limit_price=price(Decimal(str(audit.get("limit_price", "0")))),
                explanation=explanation,
                broker_order_id=str(row.broker_order_id),
                tone=tone,
            )
        )
    return tuple(rows)