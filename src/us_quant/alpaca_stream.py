from __future__ import annotations

from dataclasses import replace
import json
import os
import random
from threading import Event
from time import monotonic, sleep
from typing import Callable

from us_quant.ibkr_stream import (
    StreamSnapshot,
    StreamStateReducer,
)
from us_quant.proxy_support import (
    DEFAULT_PROXY,
    proxy_unreachable,
    resolve_proxy,
)


ALPACA_KEY_ENV = "APCA_API_KEY_ID"
ALPACA_SECRET_ENV = "APCA_API_SECRET_KEY"
ALPACA_IEX_URL = "wss://stream.data.alpaca.markets/v2/iex"


class AlpacaCredentialsMissing(RuntimeError):
    pass


class AlpacaIEXStream:
    """Free real-time IEX feed; explicitly not consolidated SIP/NBBO."""

    def __init__(
        self,
        *,
        symbols: tuple[str, ...],
        api_key: str | None = None,
        api_secret: str | None = None,
        stale_after_seconds: float = 8.0,
        open_timeout_seconds: float = 15.0,
        listener: Callable[[StreamSnapshot], None] | None = None,
    ) -> None:
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
            raise ValueError(
                "Alpaca Basic IEX is limited to 30 symbols"
            )
        self.symbols = normalized
        self.api_key = (
            api_key
            if api_key is not None
            else os.environ.get(ALPACA_KEY_ENV, "")
        )
        self.api_secret = (
            api_secret
            if api_secret is not None
            else os.environ.get(ALPACA_SECRET_ENV, "")
        )
        if not self.api_key or not self.api_secret:
            raise AlpacaCredentialsMissing(
                "缺少 Alpaca 免费行情密钥；请设置 "
                "APCA_API_KEY_ID 和 APCA_API_SECRET_KEY"
            )
        if open_timeout_seconds <= 0:
            raise ValueError("open timeout must be positive")
        self.open_timeout_seconds = open_timeout_seconds
        self.reducer = StreamStateReducer(
            stale_after_seconds=stale_after_seconds
        )
        self.listener = listener
        self._stop = Event()
        self._connection = None
        self._active_proxy: str | None = None
        self._request_ids = {
            symbol: 50_000 + index
            for index, symbol in enumerate(self.symbols)
        }

    def run(self) -> None:
        try:
            from websockets.sync.client import connect
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "websockets dependency is not installed"
            ) from error
        generation = 0
        attempt = 0
        # Route through the local Clash proxy by default; fall back to a
        # direct connection for this lifetime when the proxy is unreachable.
        self._active_proxy = resolve_proxy()
        while not self._stop.is_set():
            generation += 1
            attempt += 1
            self.reducer.start_generation(generation, attempt)
            self._emit()
            try:
                with connect(
                    ALPACA_IEX_URL,
                    open_timeout=self.open_timeout_seconds,
                    proxy=self._active_proxy,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=3,
                ) as connection:
                    self._connection = connection
                    self._authenticate(connection)
                    self.reducer.handshake(generation)
                    for symbol, request_id in self._request_ids.items():
                        self.reducer.register_quote(
                            generation=generation,
                            request_id=request_id,
                            symbol=symbol,
                            requested_market_data_type=1,
                        )
                        self.reducer.market_data_type(
                            generation, request_id, 1
                        )
                    connection.send(
                        json.dumps(
                            {
                                "action": "subscribe",
                                "trades": list(self.symbols),
                                "quotes": list(self.symbols),
                                "bars": list(self.symbols),
                            }
                        )
                    )
                    self._emit()
                    while not self._stop.is_set():
                        try:
                            payload = connection.recv(timeout=0.5)
                        except TimeoutError:
                            self._emit()
                            continue
                        self.process_message(
                            payload,
                            generation=generation,
                        )
            except Exception as error:
                if not self._stop.is_set():
                    if (
                        self._active_proxy is not None
                        and proxy_unreachable(error)
                    ):
                        # Clash is not running: fall back to a direct route
                        # for this stream lifetime and tell the user why.
                        self._active_proxy = None
                        message = (
                            f"未检测到本地 Clash 代理（{DEFAULT_PROXY}），"
                            "已自动回退直连重试；如需代理请确认 Clash 已启动"
                        )
                        self.reducer.error(
                            generation, -1, 9104, message
                        )
                        self.reducer.disconnected(
                            generation, message
                        )
                        self._emit()
                    else:
                        detail = " ".join(
                            str(error).split()
                        )[:200] or type(error).__name__
                        self.reducer.error(
                            generation,
                            -1,
                            9001,
                            f"Alpaca IEX: {detail}",
                        )
                        self.reducer.disconnected(
                            generation,
                            "Alpaca IEX 连接中断，准备重连",
                        )
                        self._emit()
            finally:
                self._connection = None
            if self._stop.is_set():
                break
            delay = min(30.0, 2 ** min(attempt - 1, 4))
            delay += random.uniform(0, min(1.0, delay * 0.2))
            deadline = monotonic() + delay
            while not self._stop.is_set() and monotonic() < deadline:
                sleep(min(0.2, deadline - monotonic()))

    def process_message(
        self,
        payload: str | bytes,
        *,
        generation: int,
    ) -> None:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        messages = json.loads(payload)
        if not isinstance(messages, list):
            return
        for message in messages:
            if not isinstance(message, dict):
                continue
            message_type = message.get("T")
            if message_type == "error":
                self.reducer.error(
                    generation,
                    -1,
                    int(message.get("code", 9002)),
                    str(message.get("msg", "Alpaca error")),
                )
                continue
            symbol = str(message.get("S", "")).upper()
            request_id = self._request_ids.get(symbol)
            if request_id is None:
                continue
            timestamp = str(message.get("t") or "")
            if message_type == "q":
                self.reducer.tick_price(
                    generation,
                    request_id,
                    1,
                    float(message.get("bp") or 0),
                    now_iso=timestamp or None,
                )
                self.reducer.tick_size(
                    generation,
                    request_id,
                    0,
                    float(message.get("bs") or 0),
                )
                self.reducer.tick_size(
                    generation,
                    request_id,
                    3,
                    float(message.get("as") or 0),
                )
                self.reducer.tick_price(
                    generation,
                    request_id,
                    2,
                    float(message.get("ap") or 0),
                    now_iso=timestamp or None,
                )
            elif message_type == "t":
                self.reducer.tick_price(
                    generation,
                    request_id,
                    4,
                    float(message.get("p") or 0),
                    now_iso=timestamp or None,
                )
            elif message_type in {"b", "u"}:
                self.reducer.tick_price(
                    generation,
                    request_id,
                    4,
                    float(message.get("c") or 0),
                    now_iso=timestamp or None,
                )
        self._emit()

    def stop(self) -> None:
        self._stop.set()
        connection = self._connection
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def snapshot(self) -> StreamSnapshot:
        base = self.reducer.snapshot()
        quotes = tuple(
            replace(
                quote,
                provider="Alpaca",
                coverage="IEX 单交易所实时；非 SIP/NBBO",
            )
            for quote in base.quotes
        )
        return replace(
            base,
            quotes=quotes,
            provider="Alpaca",
            coverage="IEX 单交易所实时；非全市场 SIP/NBBO",
        )

    def _authenticate(self, connection) -> None:
        connection.recv(timeout=10)
        connection.send(
            json.dumps(
                {
                    "action": "auth",
                    "key": self.api_key,
                    "secret": self.api_secret,
                }
            )
        )
        response = json.loads(connection.recv(timeout=10))
        if not any(
            isinstance(item, dict)
            and item.get("T") == "success"
            and item.get("msg") == "authenticated"
            for item in response
        ):
            raise PermissionError("Alpaca IEX authentication failed")

    def _emit(self) -> None:
        if self.listener is not None:
            self.listener(self.snapshot())
