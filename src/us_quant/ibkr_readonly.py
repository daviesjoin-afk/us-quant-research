from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from threading import Event, Thread
from time import monotonic
from typing import Any

from us_quant.ibkr import (
    IBKRClientConnectError,
    IBKRConnectionConfig,
    connect_ibkr_client,
)


ACCOUNT_SUMMARY_TAGS = (
    "NetLiquidation,TotalCashValue,BuyingPower,AvailableFunds,"
    "GrossPositionValue,ExcessLiquidity,MaintMarginReq,Cushion,"
    "Currency"
)
INFORMATIONAL_ERROR_CODES = {
    2104,
    2106,
    2107,
    2108,
    2119,
    2158,
}
IBKR_UNSET_DOUBLE = float("1.7976931348623157e308")


class IBKRAPIUnavailable(RuntimeError):
    pass


class IBKRReadOnlyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AccountMetric:
    account: str
    tag: str
    value: str
    currency: str


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    account: str
    con_id: int
    symbol: str
    local_symbol: str
    security_type: str
    exchange: str
    currency: str
    quantity: Decimal
    average_cost: Decimal


@dataclass(frozen=True, slots=True)
class ContractRecord:
    request_id: int
    con_id: int
    symbol: str
    local_symbol: str
    security_type: str
    primary_exchange: str
    currency: str
    trading_class: str
    long_name: str
    min_tick: Decimal


@dataclass(frozen=True, slots=True)
class MarketQuote:
    request_id: int
    symbol: str
    market_data_type: int | None
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    close: Decimal | None


@dataclass(frozen=True, slots=True)
class BrokerMessage:
    request_id: int
    code: int
    message: str


@dataclass(frozen=True, slots=True)
class AccountPnl:
    request_id: int
    account: str
    daily_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class PositionPnl:
    request_id: int
    account: str
    con_id: int
    quantity: Decimal | None
    daily_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None
    market_value: Decimal | None


@dataclass(frozen=True, slots=True)
class IBKRReadOnlySnapshot:
    server_version: int
    connection_time: str
    accounts: tuple[str, ...]
    metrics: tuple[AccountMetric, ...]
    positions: tuple[BrokerPosition, ...]
    contracts: tuple[ContractRecord, ...]
    quotes: tuple[MarketQuote, ...]
    messages: tuple[BrokerMessage, ...]
    account_pnl: tuple[AccountPnl, ...] = ()
    position_pnl: tuple[PositionPnl, ...] = ()


def mask_account_id(account: str) -> str:
    if len(account) <= 4:
        return "*" * len(account)
    return f"{account[:2]}***{account[-2:]}"


def ensure_readonly_paper_config(config: IBKRConnectionConfig) -> None:
    if config.port != 4002:
        raise IBKRReadOnlyError(
            "read-only integration is locked to IB Gateway Paper port 4002"
        )
    if not config.api_read_only:
        raise IBKRReadOnlyError(
            "IBKR API read-only mode must be enabled"
        )
    if config.paper_order_submission_enabled:
        raise IBKRReadOnlyError(
            "paper order submission must remain disabled"
        )


def snapshot_to_redacted_dict(
    snapshot: IBKRReadOnlySnapshot,
) -> dict[str, Any]:
    account_aliases = {
        account: mask_account_id(account) for account in snapshot.accounts
    }
    for metric in snapshot.metrics:
        account_aliases.setdefault(
            metric.account, mask_account_id(metric.account)
        )
    for position in snapshot.positions:
        account_aliases.setdefault(
            position.account, mask_account_id(position.account)
        )

    summaries: dict[str, dict[str, dict[str, str]]] = {}
    for metric in snapshot.metrics:
        account = account_aliases[metric.account]
        summaries.setdefault(account, {})[metric.tag] = {
            "value": metric.value,
            "currency": metric.currency,
        }

    intraday_reasons = intraday_market_data_reasons(snapshot)
    return {
        "connected": True,
        "server_version": snapshot.server_version,
        "connection_time": snapshot.connection_time,
        "accounts": sorted(set(account_aliases.values())),
        "account_summary": summaries,
        "positions": [
            {
                "account": account_aliases[position.account],
                "con_id": position.con_id,
                "symbol": position.symbol,
                "local_symbol": position.local_symbol,
                "security_type": position.security_type,
                "exchange": position.exchange,
                "currency": position.currency,
                "quantity": str(position.quantity),
                "average_cost": str(position.average_cost),
            }
            for position in snapshot.positions
        ],
        "contracts": [
            {
                "request_id": contract.request_id,
                "con_id": contract.con_id,
                "symbol": contract.symbol,
                "local_symbol": contract.local_symbol,
                "security_type": contract.security_type,
                "primary_exchange": contract.primary_exchange,
                "currency": contract.currency,
                "trading_class": contract.trading_class,
                "long_name": contract.long_name,
                "min_tick": str(contract.min_tick),
            }
            for contract in snapshot.contracts
        ],
        "quotes": [
            {
                "request_id": quote.request_id,
                "symbol": quote.symbol,
                "market_data_type": quote.market_data_type,
                "market_data_type_name": {
                    1: "live",
                    2: "frozen",
                    3: "delayed",
                    4: "delayed_frozen",
                }.get(quote.market_data_type, "unknown"),
                "bid": str(quote.bid) if quote.bid is not None else None,
                "ask": str(quote.ask) if quote.ask is not None else None,
                "last": str(quote.last) if quote.last is not None else None,
                "close": (
                    str(quote.close) if quote.close is not None else None
                ),
            }
            for quote in snapshot.quotes
        ],
        "account_pnl": [
            {
                "account": account_aliases.get(
                    row.account, mask_account_id(row.account)
                ),
                "daily_pnl": (
                    str(row.daily_pnl)
                    if row.daily_pnl is not None
                    else None
                ),
                "unrealized_pnl": (
                    str(row.unrealized_pnl)
                    if row.unrealized_pnl is not None
                    else None
                ),
                "realized_pnl": (
                    str(row.realized_pnl)
                    if row.realized_pnl is not None
                    else None
                ),
            }
            for row in snapshot.account_pnl
        ],
        "position_pnl": [
            {
                "account": account_aliases.get(
                    row.account, mask_account_id(row.account)
                ),
                "con_id": row.con_id,
                "quantity": (
                    str(row.quantity)
                    if row.quantity is not None
                    else None
                ),
                "daily_pnl": (
                    str(row.daily_pnl)
                    if row.daily_pnl is not None
                    else None
                ),
                "unrealized_pnl": (
                    str(row.unrealized_pnl)
                    if row.unrealized_pnl is not None
                    else None
                ),
                "realized_pnl": (
                    str(row.realized_pnl)
                    if row.realized_pnl is not None
                    else None
                ),
                "market_value": (
                    str(row.market_value)
                    if row.market_value is not None
                    else None
                ),
            }
            for row in snapshot.position_pnl
        ],
        "intraday_market_data_ready": not intraday_reasons,
        "intraday_market_data_reasons": list(intraday_reasons),
        "messages": [
            {
                "request_id": message.request_id,
                "code": message.code,
                "message": message.message,
                "informational": message.code in INFORMATIONAL_ERROR_CODES,
            }
            for message in snapshot.messages
        ],
    }


def intraday_market_data_reasons(
    snapshot: IBKRReadOnlySnapshot,
    *,
    required_symbols: tuple[str, ...] = ("SPY", "QQQ"),
) -> tuple[str, ...]:
    quotes = {quote.symbol: quote for quote in snapshot.quotes}
    reasons: list[str] = []
    for symbol in required_symbols:
        quote = quotes.get(symbol)
        if quote is None:
            reasons.append(f"missing quote for {symbol}")
            continue
        if quote.market_data_type != 1:
            reasons.append(
                f"{symbol} market data is not real-time type 1"
            )
        if quote.bid is None or quote.ask is None:
            reasons.append(f"{symbol} real-time bid/ask is incomplete")
    return tuple(reasons)


def collect_readonly_snapshot(
    config: IBKRConnectionConfig,
    *,
    symbols: tuple[str, ...] = ("SPY", "QQQ"),
    timeout_seconds: float = 20,
) -> IBKRReadOnlySnapshot:
    ensure_readonly_paper_config(config)
    if timeout_seconds <= 0:
        raise ValueError("snapshot timeout must be positive")

    try:
        from ibapi.client import EClient
        from ibapi.contract import Contract
        from ibapi.ticktype import TickTypeEnum
        from ibapi.wrapper import EWrapper
    except ModuleNotFoundError as error:
        raise IBKRAPIUnavailable(
            "official IBKR Python API is not installed"
        ) from error

    handshake_complete = Event()
    accounts_ready = Event()
    account_summary_complete = Event()
    positions_complete = Event()
    account_pnl_event = Event()
    contract_events: dict[int, Event] = {}
    quote_events: dict[int, Event] = {}
    quote_symbols: dict[int, str] = {}

    class ReadOnlyApp(EWrapper, EClient):
        def __init__(self) -> None:
            EWrapper.__init__(self)
            EClient.__init__(self, self)
            self.accounts: list[str] = []
            self.metrics: list[AccountMetric] = []
            self.positions: list[BrokerPosition] = []
            self.contracts: list[ContractRecord] = []
            self.messages: list[BrokerMessage] = []
            self.account_pnl: dict[int, AccountPnl] = {}
            self.position_pnl: dict[int, PositionPnl] = {}
            self.quote_types: dict[int, int] = {}
            self.quote_prices: dict[int, dict[str, Decimal]] = {}
            self.contract_objects: dict[int, list[Any]] = {}

        def nextValidId(self, orderId: int) -> None:
            del orderId
            handshake_complete.set()

        def managedAccounts(self, accountsList: str) -> None:
            self.accounts = [
                account.strip()
                for account in accountsList.split(",")
                if account.strip()
            ]
            accounts_ready.set()

        def accountSummary(
            self,
            reqId: int,
            account: str,
            tag: str,
            value: str,
            currency: str,
        ) -> None:
            del reqId
            self.metrics.append(
                AccountMetric(
                    account=account,
                    tag=tag,
                    value=value,
                    currency=currency,
                )
            )

        def accountSummaryEnd(self, reqId: int) -> None:
            del reqId
            account_summary_complete.set()

        def position(
            self,
            account: str,
            contract: Any,
            position: Any,
            avgCost: float,
        ) -> None:
            self.positions.append(
                BrokerPosition(
                    account=account,
                    con_id=int(contract.conId),
                    symbol=contract.symbol,
                    local_symbol=contract.localSymbol,
                    security_type=contract.secType,
                    exchange=contract.exchange,
                    currency=contract.currency,
                    quantity=Decimal(str(position)),
                    average_cost=Decimal(str(avgCost)),
                )
            )

        def positionEnd(self) -> None:
            positions_complete.set()

        def pnl(
            self,
            reqId: int,
            dailyPnL: float,
            unrealizedPnL: float,
            realizedPnL: float,
        ) -> None:
            account = self.accounts[0] if self.accounts else ""
            self.account_pnl[reqId] = AccountPnl(
                request_id=reqId,
                account=account,
                daily_pnl=_optional_decimal(dailyPnL),
                unrealized_pnl=_optional_decimal(unrealizedPnL),
                realized_pnl=_optional_decimal(realizedPnL),
            )
            account_pnl_event.set()

        def pnlSingle(
            self,
            reqId: int,
            pos: Any,
            dailyPnL: float,
            unrealizedPnL: float,
            realizedPnL: float,
            value: float,
        ) -> None:
            request = position_pnl_requests.get(reqId)
            if request is None:
                return
            account, con_id = request
            self.position_pnl[reqId] = PositionPnl(
                request_id=reqId,
                account=account,
                con_id=con_id,
                quantity=_optional_decimal(pos),
                daily_pnl=_optional_decimal(dailyPnL),
                unrealized_pnl=_optional_decimal(unrealizedPnL),
                realized_pnl=_optional_decimal(realizedPnL),
                market_value=_optional_decimal(value),
            )

        def contractDetails(
            self, reqId: int, contractDetails: Any
        ) -> None:
            contract = contractDetails.contract
            self.contract_objects.setdefault(reqId, []).append(contract)
            self.contracts.append(
                ContractRecord(
                    request_id=reqId,
                    con_id=int(contract.conId),
                    symbol=contract.symbol,
                    local_symbol=contract.localSymbol,
                    security_type=contract.secType,
                    primary_exchange=contract.primaryExchange,
                    currency=contract.currency,
                    trading_class=contract.tradingClass,
                    long_name=contractDetails.longName,
                    min_tick=Decimal(str(contractDetails.minTick)),
                )
            )

        def contractDetailsEnd(self, reqId: int) -> None:
            event = contract_events.get(reqId)
            if event is not None:
                event.set()

        def marketDataType(
            self, reqId: int, marketDataType: int
        ) -> None:
            self.quote_types[reqId] = int(marketDataType)

        def tickPrice(
            self,
            reqId: int,
            tickType: int,
            price: float,
            attrib: Any,
        ) -> None:
            del attrib
            if price <= 0:
                return
            field_name = TickTypeEnum.toStr(tickType)
            normalized = {
                "BID": "bid",
                "DELAYED_BID": "bid",
                "ASK": "ask",
                "DELAYED_ASK": "ask",
                "LAST": "last",
                "DELAYED_LAST": "last",
                "CLOSE": "close",
                "DELAYED_CLOSE": "close",
            }.get(field_name)
            if normalized is not None:
                self.quote_prices.setdefault(reqId, {})[normalized] = (
                    Decimal(str(price))
                )

        def tickSnapshotEnd(self, reqId: int) -> None:
            event = quote_events.get(reqId)
            if event is not None:
                event.set()

        def error(
            self,
            reqId: int,
            *args: Any,
        ) -> None:
            if len(args) >= 3:
                _, error_code, error_string, *_ = args
            elif len(args) == 2:
                error_code, error_string = args
            else:
                return
            self.messages.append(
                BrokerMessage(
                    request_id=int(reqId),
                    code=int(error_code),
                    message=str(error_string),
                )
            )
            if (
                int(reqId) in quote_events
                and int(error_code) not in INFORMATIONAL_ERROR_CODES
            ):
                quote_events[int(reqId)].set()

    app = ReadOnlyApp()
    network_thread: Thread | None = None
    account_request_id = 9001
    account_pnl_request_id = 9300
    position_pnl_requests: dict[int, tuple[str, int]] = {}
    try:
        try:
            connect_ibkr_client(app, config)
        except IBKRClientConnectError as error:
            raise IBKRReadOnlyError(str(error)) from error

        network_thread = Thread(
            target=app.run,
            name="ibkr-readonly-network",
            daemon=True,
        )
        network_thread.start()
        deadline = monotonic() + timeout_seconds

        _wait_for(
            handshake_complete,
            deadline,
            "IBKR protocol handshake",
        )
        _wait_for(accounts_ready, deadline, "managed accounts")

        app.reqAccountSummary(
            account_request_id,
            "All",
            ACCOUNT_SUMMARY_TAGS,
        )
        app.reqPositions()
        if app.accounts:
            app.reqPnL(
                account_pnl_request_id,
                app.accounts[0],
                "",
            )

        _wait_for(
            account_summary_complete,
            deadline,
            "account summary",
        )
        _wait_for(positions_complete, deadline, "positions")

        requested_symbols = list(dict.fromkeys(symbols))
        for position in app.positions:
            if (
                position.security_type == "STK"
                and position.symbol not in requested_symbols
            ):
                requested_symbols.append(position.symbol)
            if app.accounts and position.account == app.accounts[0]:
                request_id = 9400 + len(position_pnl_requests)
                position_pnl_requests[request_id] = (
                    position.account,
                    position.con_id,
                )
                app.reqPnLSingle(
                    request_id,
                    position.account,
                    "",
                    position.con_id,
                )

        for index, symbol in enumerate(requested_symbols):
            request_id = 9100 + index
            contract_events[request_id] = Event()
            contract = Contract()
            contract.symbol = symbol
            contract.secType = "STK"
            contract.exchange = "SMART"
            contract.currency = "USD"
            app.reqContractDetails(request_id, contract)

        for request_id, event in contract_events.items():
            _wait_for(
                event,
                deadline,
                f"contract details request {request_id}",
            )

        app.reqMarketDataType(3)
        for index, symbol in enumerate(requested_symbols):
            contract_request_id = 9100 + index
            contracts = app.contract_objects.get(
                contract_request_id, []
            )
            if len(contracts) != 1:
                app.messages.append(
                    BrokerMessage(
                        request_id=contract_request_id,
                        code=200,
                        message=(
                            f"{symbol} contract resolution returned "
                            f"{len(contracts)} matches"
                        ),
                    )
                )
                continue
            request_id = 9200 + index
            quote_events[request_id] = Event()
            quote_symbols[request_id] = symbol
            app.reqMktData(
                request_id,
                contracts[0],
                "",
                True,
                False,
                [],
            )

        for request_id, event in quote_events.items():
            _wait_for(
                event,
                deadline,
                f"market data snapshot request {request_id}",
            )
        remaining = deadline - monotonic()
        if app.accounts and remaining > 0:
            account_pnl_event.wait(min(2.0, remaining))

        return IBKRReadOnlySnapshot(
            server_version=int(app.serverVersion()),
            connection_time=_normalize_connection_time(
                app.twsConnectionTime()
            ),
            accounts=tuple(app.accounts),
            metrics=tuple(app.metrics),
            positions=tuple(app.positions),
            contracts=tuple(app.contracts),
            quotes=tuple(
                MarketQuote(
                    request_id=request_id,
                    symbol=symbol,
                    market_data_type=app.quote_types.get(request_id),
                    bid=app.quote_prices.get(request_id, {}).get("bid"),
                    ask=app.quote_prices.get(request_id, {}).get("ask"),
                    last=app.quote_prices.get(request_id, {}).get("last"),
                    close=app.quote_prices.get(request_id, {}).get("close"),
                )
                for request_id, symbol in quote_symbols.items()
            ),
            messages=tuple(app.messages),
            account_pnl=tuple(app.account_pnl.values()),
            position_pnl=tuple(app.position_pnl.values()),
        )
    finally:
        if app.isConnected():
            try:
                app.cancelAccountSummary(account_request_id)
                app.cancelPositions()
                if app.accounts:
                    app.cancelPnL(account_pnl_request_id)
                for request_id in position_pnl_requests:
                    app.cancelPnLSingle(request_id)
                for request_id in quote_events:
                    app.cancelMktData(request_id)
            finally:
                app.disconnect()
        if network_thread is not None:
            network_thread.join(timeout=2)


def _wait_for(event: Event, deadline: float, label: str) -> None:
    remaining = deadline - monotonic()
    if remaining <= 0 or not event.wait(remaining):
        raise IBKRReadOnlyError(f"timed out waiting for {label}")


def _normalize_connection_time(value: Any) -> str:
    if isinstance(value, bytes):
        for encoding in ("utf-8", "gb18030"):
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace")
    return str(value)


def _optional_decimal(value: Any) -> Decimal | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if abs(number) >= IBKR_UNSET_DOUBLE:
        return None
    return Decimal(str(value))
