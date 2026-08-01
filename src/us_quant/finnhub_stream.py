from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import os
import random
from threading import Event
from time import monotonic, sleep
from typing import Callable
from urllib.parse import quote

from us_quant.ibkr_stream import StreamSnapshot, StreamStateReducer


FINNHUB_KEY_ENV = "FINNHUB_API_KEY"
FINNHUB_WEBSOCKET_URL = "wss://ws.finnhub.io"


class FinnhubCredentialsMissing(RuntimeError):
    pass


class FinnhubTradeStream:
    """Finnhub real-time trade prints with an explicit synthetic fill band.

    Finnhub's free WebSocket payload is a trade stream rather than NBBO
    quote data. To keep the engineering shadow session usable, this adapter
    creates a conservative +/-5 bps execution band around each trade. The
    band is always labeled synthetic and must never be represented as a
    venue bid/ask.
    """

    def __init__(
        self,
        *,
        symbols: tuple[str, ...],
        api_key: str | None = None,
        stale_after_seconds: float = 20.0,
        synthetic_half_spread_bps: float = 5.0,
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
                "this client limits Finnhub to 30 symbols"
            )
        self.symbols = normalized
        self.api_key = (
            api_key
            if api_key is not None
            else os.environ.get(FINNHUB_KEY_ENV, "")
        )
        if not self.api_key:
            raise FinnhubCredentialsMissing(
                "缺少 Finnhub API Key；请在客户端中填写并安全保存"
            )
        if synthetic_half_spread_bps <= 0:
            raise ValueError("synthetic spread must be positive")
        self.synthetic_half_spread_bps = synthetic_half_spread_bps
        self.reducer = StreamStateReducer(
            stale_after_seconds=stale_after_seconds
        )
        self.listener = listener
        self._stop = Event()
        self._connection = None
        self._request_ids = {
            symbol: 60_000 + index
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
        while not self._stop.is_set():
            generation += 1
            attempt += 1
            self.reducer.start_generation(generation, attempt)
            self._emit()
            try:
                url = (
                    f"{FINNHUB_WEBSOCKET_URL}?token="
                    f"{quote(self.api_key, safe='')}"
                )
                with connect(
                    url,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=3,
                ) as connection:
                    self._connection = connection
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
                                    "type": "subscribe",
                                    "symbol": symbol,
                                }
                            )
                        )
                    self._emit()
                    while not self._stop.is_set():
                        try:
                            payload = connection.recv(timeout=1)
                        except TimeoutError:
                            self._emit()
                            continue
                        self.process_message(
                            payload,
                            generation=generation,
                        )
            except Exception as error:
                if not self._stop.is_set():
                    self.reducer.error(
                        generation,
                        -1,
                        9101,
                        f"Finnhub: {type(error).__name__}",
                    )
                    self.reducer.disconnected(
                        generation,
                        "Finnhub 连接中断，准备重连",
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
        message = json.loads(payload)
        if not isinstance(message, dict):
            return
        message_type = message.get("type")
        if message_type == "error":
            self.reducer.error(
                generation,
                -1,
                9102,
                "Finnhub WebSocket returned an error",
            )
            self._emit()
            return
        if message_type != "trade":
            return
        for trade in message.get("data") or []:
            if not isinstance(trade, dict):
                continue
            symbol = str(trade.get("s") or "").upper()
            request_id = self._request_ids.get(symbol)
            price_value = float(trade.get("p") or 0)
            if request_id is None or price_value <= 0:
                continue
            timestamp_ms = trade.get("t")
            timestamp = (
                datetime.fromtimestamp(
                    float(timestamp_ms) / 1000,
                    timezone.utc,
                ).isoformat()
                if timestamp_ms
                else None
            )
            half_spread = self.synthetic_half_spread_bps / 10_000
            self.reducer.tick_price(
                generation,
                request_id,
                1,
                price_value * (1 - half_spread),
                now_iso=timestamp,
            )
            self.reducer.tick_price(
                generation,
                request_id,
                2,
                price_value * (1 + half_spread),
                now_iso=timestamp,
            )
            self.reducer.tick_price(
                generation,
                request_id,
                4,
                price_value,
                now_iso=timestamp,
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
        coverage = (
            "实时成交（20秒 fresh 窗）；bid/ask 为 ±5bps 影子带，"
            "非市场盘口/NBBO"
        )
        quotes = tuple(
            replace(
                row,
                provider="Finnhub",
                coverage=coverage,
            )
            for row in base.quotes
        )
        return replace(
            base,
            quotes=quotes,
            provider="Finnhub",
            coverage=coverage,
        )

    def _emit(self) -> None:
        if self.listener is not None:
            self.listener(self.snapshot())
