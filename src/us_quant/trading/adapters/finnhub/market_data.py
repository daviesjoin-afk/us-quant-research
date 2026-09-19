"""Finnhub market data adapter.

Moved from ``us_quant.finnhub_stream``.  The transport behaviour is unchanged:
proxy resolution and Clash fallback, the REST bootstrap that seeds quotes so
premarket is not blank, the 90-second REST refresh, the extended-hours
freshness window, the error classification and the reconnect backoff are the
same code.

Two things are preserved deliberately and must not be "simplified" later:

* the **synthetic +/-5bps execution band**.  Finnhub's free feed is trade
  prints, not NBBO.  This adapter builds a conservative band around each print
  and labels it as synthetic in ``coverage``; the domain quote carries that
  label so no upper layer can mistake the band for a venue bid/ask.
* the **extended-hours freshness window**.  Regular hours require a print
  within 20 seconds; pre/after/overnight allow 120 seconds because prints are
  sparse.  This is not unified with the other providers' thresholds on
  purpose -- the feeds are genuinely different.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
import random
from threading import Event
from time import monotonic, sleep
from urllib.parse import quote

from us_quant.extended_hours import USEquitySession, us_equity_session
from us_quant.proxy_support import (
    DEFAULT_PROXY,
    proxy_unreachable,
    resolve_proxy,
)
from us_quant.trading.adapters.market_data_state import (
    StreamSnapshot,
    StreamStateReducer,
    to_market_snapshot,
)
from us_quant.trading.domain.market import (
    MarketDataHealth,
    MarketSnapshot,
)
from us_quant.trading.ports.market_data import (
    MarketDataCredentialsError,
    SnapshotListener,
)


FINNHUB_KEY_ENV = "FINNHUB_API_KEY"
FINNHUB_WEBSOCKET_URL = "wss://ws.finnhub.io"

#: Stable logic key for this feed.
SOURCE_FINNHUB_TRADES = "finnhub_trades"
FINNHUB_SOURCE_LABEL = "Finnhub"


class FinnhubCredentialsMissing(MarketDataCredentialsError):
    """Kept as a named subclass of the provider-neutral error."""


class FinnhubRejectedError(RuntimeError):
    """The Finnhub endpoint rejected this connection (invalid/unauthorized
    API key).  Reconnecting cannot help, so the stream surfaces the error."""


def classify_connect_error(error: Exception) -> tuple[bool, str]:
    """Classify a Finnhub connect failure into (fatal, message).

    ``fatal`` means retrying cannot help (rejected API key): the stream
    should stop and surface the error instead of reconnecting forever.
    """
    if isinstance(error, FinnhubRejectedError):
        return True, str(error)
    status = _http_status_code(error)
    if status in {401, 403}:
        return True, (
            f"Finnhub 拒绝连接（HTTP {status}）：API Key 无效或未授权，"
            "请在 系统设置 → API 数据源 中检查 Finnhub Key"
        )
    if isinstance(error, TimeoutError):
        return False, (
            "Finnhub 连接超时：15 秒内未完成握手；"
            "请检查本机网络能否访问 ws.finnhub.io（部分网络需要代理）"
        )
    if isinstance(error, (ConnectionError, EOFError)) or "eof" in type(
        error
    ).__name__.casefold():
        # TLS/transport resets (e.g. SSLEOFError, ConnectionResetError)
        # usually mean an intermediate network device is interfering;
        # retrying may help but the user must fix connectivity/proxy.
        return False, (
            "Finnhub 网络连接被中断（TLS/传输层重置）；"
            "常见于需要代理的网络环境。请检查网络，"
            "或设置 HTTPS_PROXY 环境变量后重连，"
            "或改用 Alpaca/IBKR 行情源"
        )
    detail = " ".join(str(error).split())[:200] or type(error).__name__
    return False, f"Finnhub 连接失败：{detail}"


def proxy_from_environment() -> str | None:
    """Deprecated alias kept for compatibility; prefer :func:`resolve_proxy`."""
    return resolve_proxy()


def _http_status_code(error: Exception) -> int | None:
    """Extract the HTTP status from a websockets rejection if present."""
    response = getattr(error, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if status is not None:
            return int(status)
    status = getattr(error, "status_code", None)
    if status is not None:
        return int(status)
    return None


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
        extended_stale_after_seconds: float = 120.0,
        synthetic_half_spread_bps: float = 5.0,
        open_timeout_seconds: float = 15.0,
        listener: SnapshotListener | None = None,
        session_provider=None,
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
        if open_timeout_seconds <= 0:
            raise ValueError("open timeout must be positive")
        self.synthetic_half_spread_bps = synthetic_half_spread_bps
        self.open_timeout_seconds = open_timeout_seconds
        if extended_stale_after_seconds < stale_after_seconds:
            raise ValueError(
                "extended-hours freshness must not be shorter than regular-hours freshness"
            )
        self.regular_stale_after_seconds = stale_after_seconds
        self.extended_stale_after_seconds = extended_stale_after_seconds
        self.session_provider = session_provider or us_equity_session
        self.reducer = StreamStateReducer(
            stale_after_seconds=stale_after_seconds
        )
        self.listener = listener
        self._stop = Event()
        self._connection = None
        self._active_proxy: str | None = None
        self._rest_opener = None  # lazily built, reused for TLS session
        self._rest_proxy: str | None = None
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
        # Route through the local Clash proxy by default; fall back to a
        # direct connection for this lifetime when the proxy is unreachable.
        self._active_proxy = resolve_proxy()
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
                # websockets>=15 supports an HTTP CONNECT proxy argument, so
                # the feed can reach ws.finnhub.io through Clash.
                with connect(
                    url,
                    open_timeout=self.open_timeout_seconds,
                    proxy=self._active_proxy,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=3,
                ) as connection:
                    self._connection = connection
                    self.reducer.handshake(generation)
                    attempt = 0  # reset backoff on successful connection
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
                    # Seed initial prices via REST so the UI shows ready
                    # quotes immediately instead of waiting for the first
                    # sparse trade print (critical for premarket/low-activity
                    # periods where WS trade prints are minutes apart).
                    # Run in a daemon thread so the WebSocket recv loop is
                    # not blocked during the ~4-5s parallel fetch.
                    from threading import Thread as _Thread
                    _Thread(
                        target=self._bootstrap_quotes_from_rest,
                        args=(generation,),
                        daemon=True,
                    ).start()
                    last_rest_refresh = monotonic()
                    while not self._stop.is_set():
                        try:
                            payload = connection.recv(timeout=0.5)
                        except TimeoutError:
                            self._emit()
                            # Periodically re-seed quotes that have gone
                            # stale (no WS trade print within the freshness
                            # window).  90s keeps all symbols fresh for the
                            # 120s premarket window without hitting the
                            # Finnhub 60 calls/min REST rate limit.
                            if monotonic() - last_rest_refresh > 90:
                                _Thread(
                                    target=self._bootstrap_quotes_from_rest,
                                    args=(generation,),
                                    daemon=True,
                                ).start()
                                last_rest_refresh = monotonic()
                            continue
                        self.process_message(
                            payload,
                            generation=generation,
                        )
            except FinnhubRejectedError:
                # Auth rejection inside process_message: reconnect cannot
                # help; propagate so the worker's failed signal surfaces it.
                raise
            except Exception as error:
                if not self._stop.is_set():
                    if (
                        self._active_proxy is not None
                        and proxy_unreachable(error)
                    ):
                        # Clash is not running: retrying through a dead proxy
                        # can never succeed.  Fall back to a direct route for
                        # this stream lifetime and tell the user why.
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
                        fatal, message = classify_connect_error(error)
                        self.reducer.error(
                            generation,
                            -1,
                            9103 if fatal else 9101,
                            message,
                        )
                        self.reducer.disconnected(
                            generation,
                            message,
                        )
                        self._emit()
                        if fatal:
                            # A rejected key cannot be fixed by retrying; stop
                            # and surface the error through the worker's failed
                            # signal instead of reconnecting forever.
                            raise FinnhubRejectedError(message) from error
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
            detail = str(
                message.get("msg") or message.get("message") or ""
            )
            lowered = detail.casefold()
            if any(
                marker in lowered
                for marker in ("token", "unauthorized", "api key", "apikey")
            ):
                raise FinnhubRejectedError(
                    f"Finnhub 拒绝连接：{detail[:200]}"
                )
            self.reducer.error(
                generation,
                -1,
                9102,
                (
                    f"Finnhub WebSocket 返回错误：{detail[:200]}"
                    if detail
                    else "Finnhub WebSocket returned an error"
                ),
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

    def transport_snapshot(self) -> StreamSnapshot:
        session = self.session_provider()
        extended = session in {
            USEquitySession.OVERNIGHT,
            USEquitySession.PREMARKET,
            USEquitySession.AFTER_HOURS,
        }
        freshness = (
            self.extended_stale_after_seconds
            if extended
            else self.regular_stale_after_seconds
        )
        self.reducer.set_stale_after_seconds(freshness)
        base = self.reducer.snapshot()
        coverage = (
            f"实时成交（{freshness:.0f}秒 fresh 窗）；bid/ask 为 ±5bps 影子带，"
            "非市场盘口/NBBO"
        )
        quotes = tuple(
            replace(
                row,
                provider=FINNHUB_SOURCE_LABEL,
                coverage=coverage,
            )
            for row in base.quotes
        )
        last_message = base.last_message
        if (
            base.handshake_complete
            and base.last_error_code is None
            and not any(row.realtime_ready for row in quotes)
        ):
            last_message = (
                f"Finnhub 已连接；当前 {session.value} 时段等待新的成交打印，"
                f"收到后 {freshness:.0f} 秒内计为 fresh"
            )
        return replace(
            base,
            quotes=quotes,
            provider=FINNHUB_SOURCE_LABEL,
            coverage=coverage,
            last_message=last_message,
        )

    def snapshot(self) -> MarketSnapshot:
        return to_market_snapshot(
            self.transport_snapshot(),
            source_id=SOURCE_FINNHUB_TRADES,
            source_label=FINNHUB_SOURCE_LABEL,
        )

    def health(self) -> MarketDataHealth:
        snapshot = self.snapshot()
        return MarketDataHealth(
            connected=snapshot.connected,
            source=snapshot.source_id,
            stale_symbols=tuple(
                quote.symbol
                for quote in snapshot.quotes
                if quote.stale
            ),
            message=snapshot.message,
        )

    def _emit(self) -> None:
        if self.listener is not None:
            self.listener(self.snapshot())

    def _get_rest_opener(self):
        """Build or reuse a urllib opener with TLS session pooling."""
        proxy = self._active_proxy or resolve_proxy()
        if self._rest_opener is not None and self._rest_proxy == proxy:
            return self._rest_opener
        import urllib.request
        handler = urllib.request.ProxyHandler(
            {"https": proxy, "http": proxy} if proxy else {}
        )
        self._rest_opener = urllib.request.build_opener(handler)
        self._rest_proxy = proxy
        return self._rest_opener

    def _fetch_single_quote(
        self, symbol: str, request_id: int, generation: int
    ) -> bool:
        """Fetch one symbol via REST; return True if price was seeded."""
        opener = self._get_rest_opener()
        half_spread = self.synthetic_half_spread_bps / 10_000
        try:
            url = (
                f"https://finnhub.io/api/v1/quote"
                f"?symbol={quote(symbol, safe='')}"
                f"&token={quote(self.api_key, safe='')}"
            )
            with opener.open(url, timeout=5) as resp:
                data = json.loads(resp.read())
            price = float(data.get("c") or data.get("pc") or 0)
            if price <= 0:
                return False
            self.reducer.tick_price(
                generation, request_id, 1,
                price * (1 - half_spread),
            )
            self.reducer.tick_price(
                generation, request_id, 2,
                price * (1 + half_spread),
            )
            self.reducer.tick_price(
                generation, request_id, 4,
                price,
            )
            return True
        except Exception:
            return False

    def _bootstrap_quotes_from_rest(
        self, generation: int
    ) -> None:
        """Seed initial prices via Finnhub REST so the UI shows ready
        quotes immediately instead of waiting for the first sparse trade
        print.  Critical for premarket/low-activity periods where WS
        trade prints can be minutes apart.

        Fetches symbols in small batches (4 concurrent, 0.3s gap between
        batches) to avoid Finnhub rate-limiting and proxy connection pool
        saturation.  Best-effort: individual failures are silently skipped.
        """
        if self._stop.is_set():
            return
        symbols = list(self._request_ids.items())
        self._get_rest_opener()
        changed = False
        batch_size = 4
        for i in range(0, len(symbols), batch_size):
            if self._stop.is_set():
                break
            batch = symbols[i:i + batch_size]
            with ThreadPoolExecutor(max_workers=batch_size) as pool:
                futures = {
                    pool.submit(
                        self._fetch_single_quote,
                        symbol, request_id, generation,
                    ): symbol
                    for symbol, request_id in batch
                }
                for future in as_completed(futures):
                    if self._stop.is_set():
                        pool.shutdown(wait=False, cancel_futures=True)
                        break
                    try:
                        if future.result():
                            changed = True
                    except Exception:
                        continue
            # Brief pause between batches to let the proxy connection pool
            # and Finnhub rate limiter breathe.
            if i + batch_size < len(symbols):
                sleep(0.3)
        if changed:
            self._emit()
