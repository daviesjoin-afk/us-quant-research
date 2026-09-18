"""Adapter-internal market data transport state.

This module holds everything the three provider adapters share:

* the **transport DTOs** ``StreamQuote`` / ``StreamSnapshot``.  These are
  provider-shaped (IBKR market-data type numbers, ISO timestamp strings) and
  are deliberately *not* the domain types.  They must not escape
  ``trading/adapters``;
* ``StreamStateReducer``, the tick reducer that turns raw callbacks into a
  transport snapshot;
* ``ReadOnlyEClientGuard``, the hard-disable of every trading mutation
  reachable on IBKR's ``EClient``.  This is a safety boundary, not a
  convenience: it is what makes the market-data connection structurally
  incapable of placing an order;
* the **conversion** from transport to domain.  Every field the upper layers
  consume is mapped explicitly here, and nowhere else.

The reducer logic was moved verbatim from the former ``us_quant.ibkr_stream``.
This change is a relocation, not a rewrite: reconnect handling, generation
guards, hard errors, the stale calculation and the connectivity messages are
all unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from threading import RLock
from time import monotonic
from typing import Any

from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)


MARKET_DATA_TYPE_NAMES = {
    1: "实时",
    2: "冻结",
    3: "延迟",
    4: "延迟冻结",
}
PRICE_TICK_FIELDS = {
    1: "bid",
    2: "ask",
    4: "last",
    9: "close",
    66: "bid",
    67: "ask",
    68: "last",
    75: "close",
}
SIZE_TICK_FIELDS = {
    0: "bid_size",
    3: "ask_size",
    69: "bid_size",
    70: "ask_size",
}
HARD_MARKET_DATA_ERRORS = {
    10089,
    10090,
    10091,
    10186,
    10197,
}
CONNECTIVITY_MESSAGES = {
    1100: "IBKR 与市场数据服务器连接丢失",
    1101: "连接恢复，但行情订阅已丢失",
    1102: "连接恢复，行情订阅保持",
    1300: "API 端口变化，连接已断开",
}

#: Vendor market-data type -> domain mode.  This mapping is IBKR knowledge and
#: lives on the adapter side of the boundary; the domain never sees 1/2/3/4.
MARKET_DATA_TYPE_MODES = {
    1: MarketDataMode.REALTIME,
    2: MarketDataMode.FROZEN,
    3: MarketDataMode.DELAYED,
    4: MarketDataMode.DELAYED_FROZEN,
}


def market_data_mode(value: int | None) -> MarketDataMode:
    """Map a vendor market-data type onto the domain mode."""
    return MARKET_DATA_TYPE_MODES.get(value, MarketDataMode.UNKNOWN)


class ReadOnlyViolation(RuntimeError):
    pass


class ReadOnlyEClientGuard:
    """Hard-disable every trading mutation reachable on EClient."""

    @staticmethod
    def _deny(name: str) -> None:
        raise ReadOnlyViolation(f"{name} is disabled by read-only guard")

    def placeOrder(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self._deny("placeOrder")

    def placeOrderProtoBuf(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self._deny("placeOrderProtoBuf")

    def cancelOrder(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self._deny("cancelOrder")

    def cancelOrderProtoBuf(
        self, *args: Any, **kwargs: Any
    ) -> None:
        del args, kwargs
        self._deny("cancelOrderProtoBuf")

    def reqGlobalCancel(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self._deny("reqGlobalCancel")

    def exerciseOptions(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self._deny("exerciseOptions")

    def exerciseOptionsProtoBuf(
        self, *args: Any, **kwargs: Any
    ) -> None:
        del args, kwargs
        self._deny("exerciseOptionsProtoBuf")

    def replaceFA(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self._deny("replaceFA")

    def updateDisplayGroup(
        self, *args: Any, **kwargs: Any
    ) -> None:
        del args, kwargs
        self._deny("updateDisplayGroup")


@dataclass(frozen=True, slots=True)
class StreamQuote:
    """Adapter-internal quote: provider-shaped, ISO timestamps."""

    symbol: str
    request_id: int
    generation: int
    requested_market_data_type: int
    effective_market_data_type: int | None
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    close: Decimal | None
    updated_at: str | None
    age_seconds: float | None
    stale: bool
    stale_reason: str | None
    provider: str = "IBKR"
    coverage: str = "IBKR TWS market data"
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None

    @property
    def spread(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def realtime_ready(self) -> bool:
        return (
            not self.stale
            and self.effective_market_data_type == 1
            and self.bid is not None
            and self.ask is not None
            and self.bid > 0
            and self.ask >= self.bid
        )


@dataclass(frozen=True, slots=True)
class StreamSnapshot:
    """Adapter-internal snapshot: provider-shaped, ISO timestamps."""

    generation: int
    socket_connected: bool
    handshake_complete: bool
    reconnect_attempt: int
    quotes: tuple[StreamQuote, ...]
    last_error_code: int | None
    last_message: str
    observed_at: str
    provider: str = "IBKR"
    coverage: str = "由 IBKR 订阅权限决定"

    @property
    def realtime_ready(self) -> bool:
        return any(
            quote.realtime_ready for quote in self.quotes
        )


@dataclass(slots=True)
class _MutableQuote:
    symbol: str
    request_id: int
    generation: int
    requested_market_data_type: int
    effective_market_data_type: int | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    close: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    updated_at: str | None = None
    updated_monotonic: float | None = None
    field_updated_monotonic: dict[str, float] = field(
        default_factory=dict
    )
    field_event_time: dict[str, datetime] = field(
        default_factory=dict
    )
    hard_block_reason: str | None = None
    required_refresh_fields: set[str] = field(default_factory=set)
    refresh_fields_seen: set[str] = field(default_factory=set)
    stale_reason: str | None = "等待首个行情回调"


class StreamStateReducer:
    def __init__(self, *, stale_after_seconds: float = 8.0) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale threshold must be positive")
        self.stale_after_seconds = stale_after_seconds
        self._lock = RLock()
        self._generation = 0
        self._socket_connected = False
        self._handshake_complete = False
        self._reconnect_attempt = 0
        self._quotes: dict[int, _MutableQuote] = {}
        self._last_error_code: int | None = None
        self._last_message = "尚未启动"

    def set_stale_after_seconds(self, value: float) -> None:
        """Atomically adjust freshness for the current market session."""

        if value <= 0:
            raise ValueError("stale threshold must be positive")
        with self._lock:
            self.stale_after_seconds = value

    def start_generation(self, generation: int, attempt: int) -> None:
        with self._lock:
            self._generation = generation
            self._socket_connected = True
            self._handshake_complete = False
            self._reconnect_attempt = attempt
            self._quotes.clear()
            self._last_error_code = None
            self._last_message = "Gateway socket 已连接，等待协议握手"

    def handshake(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._handshake_complete = True
            self._last_message = "IBKR 协议握手完成"

    def register_quote(
        self,
        *,
        generation: int,
        request_id: int,
        symbol: str,
        requested_market_data_type: int,
    ) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._quotes[request_id] = _MutableQuote(
                symbol=symbol,
                request_id=request_id,
                generation=generation,
                requested_market_data_type=requested_market_data_type,
            )

    def market_data_type(
        self,
        generation: int,
        request_id: int,
        market_data_type: int,
    ) -> None:
        with self._lock:
            quote = self._current_quote(generation, request_id)
            if quote is None:
                return
            quote.effective_market_data_type = market_data_type
            if quote.stale_reason == "等待首个行情回调":
                quote.stale_reason = "等待价格"
            self._last_message = (
                f"{quote.symbol} 行情类型："
                f"{MARKET_DATA_TYPE_NAMES.get(market_data_type, market_data_type)}"
            )

    def tick_price(
        self,
        generation: int,
        request_id: int,
        tick_type: int,
        price: float,
        *,
        now_monotonic: float | None = None,
        now_iso: str | None = None,
    ) -> None:
        field = PRICE_TICK_FIELDS.get(tick_type)
        if field is None or price <= 0:
            return
        with self._lock:
            quote = self._current_quote(generation, request_id)
            if quote is None:
                return
            event_time = _parse_event_time(now_iso)
            previous_event_time = quote.field_event_time.get(field)
            if (
                event_time is not None
                and previous_event_time is not None
                and event_time < previous_event_time
            ):
                return
            observed_monotonic = (
                monotonic()
                if now_monotonic is None
                else now_monotonic
            )
            setattr(quote, field, Decimal(str(price)))
            quote.updated_monotonic = observed_monotonic
            quote.field_updated_monotonic[field] = observed_monotonic
            if event_time is not None:
                quote.field_event_time[field] = event_time
            quote.updated_at = now_iso or datetime.now(
                timezone.utc
            ).isoformat()
            quote.stale_reason = None
            if field in quote.required_refresh_fields:
                quote.refresh_fields_seen.add(field)
                if (
                    quote.refresh_fields_seen
                    >= quote.required_refresh_fields
                ):
                    quote.hard_block_reason = None
                    quote.required_refresh_fields.clear()
                    quote.refresh_fields_seen.clear()

    def tick_size(
        self,
        generation: int,
        request_id: int,
        tick_type: int,
        size: Decimal | float | int,
    ) -> None:
        field_name = SIZE_TICK_FIELDS.get(tick_type)
        if field_name is None:
            return
        value = Decimal(str(size))
        if value < 0:
            return
        with self._lock:
            quote = self._current_quote(generation, request_id)
            if quote is None:
                return
            setattr(quote, field_name, value)

    def error(
        self,
        generation: int,
        request_id: int,
        code: int,
        message: str,
    ) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._last_error_code = code
            self._last_message = message
            if code in HARD_MARKET_DATA_ERRORS:
                quote = self._quotes.get(request_id)
                if quote is not None:
                    quote.hard_block_reason = (
                        f"IBKR {code}: {message}"
                    )
            if code in {1100, 1300}:
                self._socket_connected = False
                for quote in self._quotes.values():
                    quote.hard_block_reason = (
                        CONNECTIVITY_MESSAGES.get(code, message)
                    )
            elif code == 1101:
                self._socket_connected = True
                self._handshake_complete = True
                for quote in self._quotes.values():
                    quote.hard_block_reason = (
                        CONNECTIVITY_MESSAGES[1101]
                    )
            elif code == 1102:
                self._socket_connected = True
                self._handshake_complete = True
                for quote in self._quotes.values():
                    quote.hard_block_reason = (
                        "连接已恢复，等待新回调确认新鲜度"
                    )
                    quote.required_refresh_fields = {"bid", "ask"}
                    quote.refresh_fields_seen.clear()

    def disconnected(self, generation: int, message: str) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._socket_connected = False
            self._handshake_complete = False
            self._last_message = message
            for quote in self._quotes.values():
                quote.hard_block_reason = message

    def retire_quote(
        self, generation: int, request_id: int
    ) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._quotes.pop(request_id, None)

    def snapshot(
        self, *, now_monotonic: float | None = None
    ) -> StreamSnapshot:
        current = monotonic() if now_monotonic is None else now_monotonic
        with self._lock:
            quotes: list[StreamQuote] = []
            for row in self._quotes.values():
                bid_time = row.field_updated_monotonic.get("bid")
                ask_time = row.field_updated_monotonic.get("ask")
                if bid_time is not None and ask_time is not None:
                    age = max(current - bid_time, current - ask_time)
                else:
                    age = (
                        current - row.updated_monotonic
                        if row.updated_monotonic is not None
                        else None
                    )
                stale_reason = row.hard_block_reason or row.stale_reason
                if stale_reason is None and not self._socket_connected:
                    stale_reason = "行情 socket 已断开"
                if stale_reason is None and not self._handshake_complete:
                    stale_reason = "行情协议尚未握手"
                if (
                    stale_reason is None
                    and row.effective_market_data_type != 1
                ):
                    stale_reason = (
                        "非实时 Type 1，仅可观察，不能用于日内信号"
                    )
                if (
                    stale_reason is None
                    and (row.bid is None or row.ask is None)
                ):
                    stale_reason = "缺少同步有效的 bid/ask"
                if (
                    stale_reason is None
                    and row.bid is not None
                    and row.ask is not None
                    and row.ask < row.bid
                ):
                    stale_reason = "盘口倒挂：ask 低于 bid"
                if (
                    stale_reason is None
                    and age is not None
                    and age > self.stale_after_seconds
                ):
                    stale_reason = (
                        f"bid/ask 最旧分量已 {age:.1f} 秒无更新，超过 "
                        f"{self.stale_after_seconds:.1f} 秒阈值"
                    )
                quotes.append(
                    StreamQuote(
                        symbol=row.symbol,
                        request_id=row.request_id,
                        generation=row.generation,
                        requested_market_data_type=(
                            row.requested_market_data_type
                        ),
                        effective_market_data_type=(
                            row.effective_market_data_type
                        ),
                        bid=row.bid,
                        ask=row.ask,
                        last=row.last,
                        close=row.close,
                        updated_at=row.updated_at,
                        age_seconds=age,
                        stale=stale_reason is not None,
                        stale_reason=stale_reason,
                        bid_size=row.bid_size,
                        ask_size=row.ask_size,
                    )
                )
            return StreamSnapshot(
                generation=self._generation,
                socket_connected=self._socket_connected,
                handshake_complete=self._handshake_complete,
                reconnect_attempt=self._reconnect_attempt,
                quotes=tuple(sorted(quotes, key=lambda row: row.symbol)),
                last_error_code=self._last_error_code,
                last_message=self._last_message,
                observed_at=datetime.now(timezone.utc).isoformat(),
            )

    def _current_quote(
        self, generation: int, request_id: int
    ) -> _MutableQuote | None:
        if generation != self._generation:
            return None
        row = self._quotes.get(request_id)
        if row is None or row.generation != generation:
            return None
        return row


def _parse_event_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# -- transport -> domain conversion ---------------------------------------


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse an ISO timestamp at the adapter boundary.

    Returns ``None`` for a missing or unparseable value.  It deliberately does
    *not* substitute the current time: a quote with no usable timestamp must
    stay visibly untimestamped rather than look freshly updated.
    """

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_market_quote(
    row: StreamQuote,
    *,
    source_id: str,
    source_label: str,
    coverage: str | None = None,
) -> MarketQuote:
    """Convert one transport quote into the domain type, field by field."""

    return MarketQuote(
        symbol=row.symbol,
        bid=row.bid,
        ask=row.ask,
        last=row.last,
        close=row.close,
        bid_size=row.bid_size,
        ask_size=row.ask_size,
        mode=market_data_mode(row.effective_market_data_type),
        updated_at=parse_iso_datetime(row.updated_at),
        age_seconds=row.age_seconds,
        stale=row.stale,
        stale_reason=row.stale_reason,
        generation=row.generation,
        source_id=source_id,
        source_label=source_label,
        coverage=row.coverage if coverage is None else coverage,
    )


def to_market_snapshot(
    snapshot: StreamSnapshot,
    *,
    source_id: str,
    source_label: str,
    coverage: str | None = None,
) -> MarketSnapshot:
    """Convert a transport snapshot into the domain type, field by field."""

    resolved_coverage = (
        snapshot.coverage if coverage is None else coverage
    )
    return MarketSnapshot(
        generation=snapshot.generation,
        connected=snapshot.socket_connected,
        ready=snapshot.handshake_complete,
        reconnect_attempt=snapshot.reconnect_attempt,
        quotes=tuple(
            to_market_quote(
                row,
                source_id=source_id,
                source_label=source_label,
                coverage=resolved_coverage,
            )
            for row in snapshot.quotes
        ),
        error_code=snapshot.last_error_code,
        message=snapshot.last_message,
        observed_at=parse_iso_datetime(snapshot.observed_at)
        or datetime.now(timezone.utc),
        source_id=source_id,
        source_label=source_label,
        coverage=resolved_coverage,
    )


__all__ = [
    "CONNECTIVITY_MESSAGES",
    "HARD_MARKET_DATA_ERRORS",
    "MARKET_DATA_TYPE_MODES",
    "MARKET_DATA_TYPE_NAMES",
    "PRICE_TICK_FIELDS",
    "SIZE_TICK_FIELDS",
    "ReadOnlyEClientGuard",
    "ReadOnlyViolation",
    "StreamQuote",
    "StreamSnapshot",
    "StreamStateReducer",
    "market_data_mode",
    "parse_iso_datetime",
    "to_market_quote",
    "to_market_snapshot",
]
