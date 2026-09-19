"""IBKR market data adapter (read-only).

Moved from ``us_quant.ibkr_stream``.  The transport behaviour is unchanged:
the reconnect loop, generation guards, hard-error handling, contract
resolution, ``reqMarketDataType`` and the extended-hours venue routing are all
the same code.

What changed is only the boundary: this adapter now returns the **domain**
``MarketSnapshot`` from ``snapshot()``, and the IBKR market-data type numbers
are interpreted here (``1/2/3/4`` -> ``MarketDataMode``) instead of leaking to
the UI.  The transport reducer still produces ``StreamSnapshot`` internally.

``ReadOnlyEClientGuard`` is mixed into the ``EClient`` subclass, so the
market-data connection is structurally unable to place, cancel or exercise
anything.  That guard is a safety boundary and is covered by its own tests.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import random
from threading import Event
from time import monotonic, sleep
from typing import Any

from us_quant.ibkr import (
    IBKRConnectionConfig,
    connect_ibkr_client,
)
from us_quant.trading.adapters.ibkr.support import (
    IBKRAPIUnavailable,
    INFORMATIONAL_ERROR_CODES,
    ensure_readonly_paper_config,
)
from us_quant.trading.adapters.market_data_state import (
    ReadOnlyEClientGuard,
    StreamSnapshot,
    StreamStateReducer,
    to_market_snapshot,
)
from us_quant.trading.domain.market import (
    MarketDataHealth,
    MarketSnapshot,
)
from us_quant.trading.ports.market_data import SnapshotListener


#: Stable logic key for the plain IBKR feed.
SOURCE_IBKR = "ibkr"
#: Stable logic key for the IBKR 5x24 (pre/after/overnight) feed.
SOURCE_IBKR_EXTENDED = "ibkr_extended"

IBKR_SOURCE_LABEL = "IBKR"
IBKR_EXTENDED_SOURCE_LABEL = "IBKR 5×24"

IBKR_COVERAGE = "由 IBKR 订阅权限决定"
IBKR_EXTENDED_COVERAGE = (
    "IBKR 5×24：盘前/盘后 SMART；隔夜直接 OVERNIGHT；"
    "实际权限与标的资格以券商回调为准"
)


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
        source_id: str = SOURCE_IBKR,
        listener: SnapshotListener | None = None,
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
        self.source_id = source_id
        self.coverage = coverage or (
            IBKR_COVERAGE
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

    def transport_snapshot(self) -> StreamSnapshot:
        """The raw adapter-internal snapshot, before domain conversion."""

        return replace(
            self.reducer.snapshot(),
            provider=self.provider_label,
            coverage=self.coverage,
        )

    def snapshot(self) -> MarketSnapshot:
        return to_market_snapshot(
            self.transport_snapshot(),
            source_id=self.source_id,
            source_label=self.provider_label,
            coverage=self.coverage,
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
