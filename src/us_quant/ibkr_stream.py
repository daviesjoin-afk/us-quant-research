from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
import random
from threading import Event, RLock
from time import monotonic, sleep
from typing import Any, Callable

from us_quant.ibkr import (
    IBKRConnectionConfig,
    connect_ibkr_client,
)
from us_quant.ibkr_readonly import (
    IBKRAPIUnavailable,
    INFORMATIONAL_ERROR_CODES,
    ensure_readonly_paper_config,
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


class IBKRReadOnlyStream:
    """Persistent read-only IBKR watchlist stream with reconnects."""

    def __init__(
        self,
        config: IBKRConnectionConfig,
        *,
        symbols: tuple[str, ...],
        requested_market_data_type: int = 3,
        stale_after_seconds: float = 8.0,
        market_exchange: str = "SMART",
        provider_label: str = "IBKR",
        coverage: str | None = None,
        listener: Callable[[StreamSnapshot], None] | None = None,
    ) -> None:
        ensure_readonly_paper_config(config)
        if requested_market_data_type not in {1, 2, 3, 4}:
            raise ValueError("market data type must be 1, 2, 3, or 4")
        normalized_exchange = market_exchange.strip().upper()
        if normalized_exchange not in {"SMART", "OVERNIGHT"}:
            raise ValueError(
                "market exchange must be SMART or OVERNIGHT"
            )
        if not provider_label.strip():
            raise ValueError("provider label cannot be empty")
        normalized = tuple(
            dict.fromkeys(
                symbol.strip().upper()
                for symbol in symbols
                if symbol.strip()
            )
        )
        if not normalized:
            raise ValueError("at least one symbol is required")
        if len(normalized) > 30:
            raise ValueError("stream watchlist is limited to 30 symbols")
        self.config = config
        self.symbols = normalized
        self.requested_market_data_type = requested_market_data_type
        self.market_exchange = normalized_exchange
        self.provider_label = provider_label.strip()
        self.coverage = coverage or (
            "由 IBKR 订阅权限决定"
            if normalized_exchange == "SMART"
            else "IBKR OVERNIGHT 直接路由报价；资格由券商决定"
        )
        self.reducer = StreamStateReducer(
            stale_after_seconds=stale_after_seconds
        )
        self.listener = listener
        self._stop = Event()
        self._app: Any | None = None

    def run(self) -> None:
        try:
            from ibapi.client import EClient
            from ibapi.contract import Contract
            from ibapi.wrapper import EWrapper
        except ModuleNotFoundError as error:
            raise IBKRAPIUnavailable(
                "official IBKR Python API is not installed"
            ) from error

        owner = self

        class StreamApp(ReadOnlyEClientGuard, EWrapper, EClient):
            def __init__(self, generation: int) -> None:
                EWrapper.__init__(self)
                EClient.__init__(self, self)
                self.generation = generation
                self.contracts: dict[int, list[Any]] = {}
                self.market_requests: dict[int, str] = {}
                self.resubscribe_generation = 0

            def nextValidId(self, orderId: int) -> None:
                del orderId
                owner.reducer.handshake(self.generation)
                owner._emit()
                for index, symbol in enumerate(owner.symbols):
                    request_id = self.generation * 10_000 + 1_000 + index
                    self.contracts[request_id] = []
                    contract = Contract()
                    contract.symbol = symbol
                    contract.secType = "STK"
                    contract.exchange = "SMART"
                    contract.currency = "USD"
                    self.reqContractDetails(request_id, contract)

            def contractDetails(
                self, reqId: int, contractDetails: Any
            ) -> None:
                self.contracts.setdefault(reqId, []).append(
                    contractDetails.contract
                )

            def contractDetailsEnd(self, reqId: int) -> None:
                contracts = self.contracts.get(reqId, [])
                index = reqId - self.generation * 10_000 - 1_000
                if not 0 <= index < len(owner.symbols):
                    return
                symbol = owner.symbols[index]
                if len(contracts) != 1:
                    owner.reducer.error(
                        self.generation,
                        reqId,
                        200,
                        (
                            f"{symbol} 合约解析返回 "
                            f"{len(contracts)} 个结果"
                        ),
                    )
                    owner._emit()
                    return
                self._subscribe_exact(symbol, contracts[0], index)

            def _subscribe_exact(
                self, symbol: str, contract: Any, index: int
            ) -> None:
                self.reqMarketDataType(
                    owner.requested_market_data_type
                )
                request_id = (
                    self.generation * 10_000
                    + 2_000
                    + self.resubscribe_generation * 100
                    + index
                )
                self.market_requests[request_id] = symbol
                if owner.market_exchange != "SMART":
                    contract.exchange = owner.market_exchange
                owner.reducer.register_quote(
                    generation=self.generation,
                    request_id=request_id,
                    symbol=symbol,
                    requested_market_data_type=(
                        owner.requested_market_data_type
                    ),
                )
                self.reqMktData(
                    request_id,
                    contract,
                    "",
                    False,
                    False,
                    [],
                )
                owner._emit()

            def marketDataType(
                self, reqId: int, marketDataType: int
            ) -> None:
                owner.reducer.market_data_type(
                    self.generation,
                    reqId,
                    int(marketDataType),
                )
                owner._emit()

            def tickPrice(
                self,
                reqId: int,
                tickType: int,
                price: float,
                attrib: Any,
            ) -> None:
                del attrib
                owner.reducer.tick_price(
                    self.generation,
                    reqId,
                    int(tickType),
                    float(price),
                )
                owner._emit()

            def tickSize(
                self,
                reqId: int,
                tickType: int,
                size: Any,
            ) -> None:
                owner.reducer.tick_size(
                    self.generation,
                    reqId,
                    int(tickType),
                    Decimal(str(size)),
                )
                owner._emit()

            def error(self, reqId: int, *args: Any) -> None:
                if len(args) >= 3:
                    _, code, message, *_ = args
                elif len(args) == 2:
                    code, message = args
                else:
                    return
                code = int(code)
                if code in INFORMATIONAL_ERROR_CODES:
                    owner._emit()
                    return
                owner.reducer.error(
                    self.generation,
                    int(reqId),
                    code,
                    str(message),
                )
                owner._emit()
                if code == 1101:
                    self.resubscribe_generation += 1
                    existing = list(self.market_requests)
                    for request_id in existing:
                        try:
                            self.cancelMktData(request_id)
                        except Exception:
                            pass
                        owner.reducer.retire_quote(
                            self.generation,
                            request_id,
                        )
                    self.market_requests.clear()
                    for request_id, contracts in list(
                        self.contracts.items()
                    ):
                        if len(contracts) != 1:
                            continue
                        index = (
                            request_id
                            - self.generation * 10_000
                            - 1_000
                        )
                        if 0 <= index < len(owner.symbols):
                            self._subscribe_exact(
                                owner.symbols[index],
                                contracts[0],
                                index,
                            )

            def connectionClosed(self) -> None:
                owner.reducer.disconnected(
                    self.generation,
                    "IBKR API 连接已关闭",
                )
                owner._emit()

        generation = 0
        attempt = 0
        while not self._stop.is_set():
            generation += 1
            attempt += 1
            self.reducer.start_generation(generation, attempt)
            self._emit()
            app = StreamApp(generation)
            self._app = app
            try:
                connect_ibkr_client(
                    app,
                    self.config,
                    client_id=self.config.client_id + 1,
                    stop_event=self._stop,
                )
                app.run()
            except Exception as error:
                self.reducer.disconnected(
                    generation,
                    f"{type(error).__name__}: {error}",
                )
                self._emit()
            finally:
                if app.isConnected():
                    for request_id in tuple(app.market_requests):
                        try:
                            app.cancelMktData(request_id)
                        except Exception:
                            pass
                    app.disconnect()
                self._app = None
            if self._stop.is_set():
                break
            delay = min(30.0, 2.0 ** min(attempt - 1, 4))
            delay += random.uniform(0, min(1.0, delay * 0.2))
            deadline = monotonic() + delay
            while not self._stop.is_set() and monotonic() < deadline:
                sleep(min(0.2, deadline - monotonic()))

    def stop(self) -> None:
        self._stop.set()
        app = self._app
        if app is not None and app.isConnected():
            app.disconnect()

    def snapshot(self) -> StreamSnapshot:
        return replace(
            self.reducer.snapshot(),
            provider=self.provider_label,
            coverage=self.coverage,
        )

    def _emit(self) -> None:
        if self.listener is not None:
            self.listener(self.snapshot())
