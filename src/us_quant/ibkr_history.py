from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Event, Thread
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.ibkr_readonly import (
    IBKRAPIUnavailable,
    IBKRReadOnlyError,
    INFORMATIONAL_ERROR_CODES,
    ensure_readonly_paper_config,
)
from us_quant.market_data import (
    DailyBar,
    HistoricalRequest,
    HistoricalSeries,
)


def default_completed_session_end(
    now: datetime | None = None,
) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("current time must be timezone-aware")
    try:
        eastern = current.astimezone(ZoneInfo("America/New_York"))
        completed_date = eastern.date() - timedelta(days=1)
    except ZoneInfoNotFoundError:
        completed_date = (
            current.astimezone(timezone.utc).date() - timedelta(days=1)
        )
    return f"{completed_date:%Y%m%d} 23:59:59 US/Eastern"


def collect_daily_history(
    config: IBKRConnectionConfig,
    *,
    symbols: tuple[str, ...] = ("SPY", "QQQ"),
    duration: str = "5 Y",
    end_datetime: str | None = None,
    timeout_seconds: float = 45,
) -> tuple[HistoricalSeries, ...]:
    ensure_readonly_paper_config(config)
    if not symbols:
        raise ValueError("at least one symbol is required")
    if timeout_seconds <= 0:
        raise ValueError("history timeout must be positive")

    try:
        from ibapi.client import EClient
        from ibapi.contract import Contract
        from ibapi.wrapper import EWrapper
    except ModuleNotFoundError as error:
        raise IBKRAPIUnavailable(
            "official IBKR Python API is not installed"
        ) from error

    requested_end = end_datetime or default_completed_session_end()
    handshake_complete = Event()
    request_events: dict[int, Event] = {}
    request_symbols: dict[int, str] = {}

    class HistoricalApp(EWrapper, EClient):
        def __init__(self) -> None:
            EWrapper.__init__(self)
            EClient.__init__(self, self)
            self.bars: dict[int, list[DailyBar]] = {}
            self.ranges: dict[int, tuple[str, str]] = {}
            self.errors: list[tuple[int, int, str]] = []

        def nextValidId(self, orderId: int) -> None:
            del orderId
            handshake_complete.set()

        def historicalData(self, reqId: int, bar: Any) -> None:
            symbol = request_symbols[reqId]
            self.bars.setdefault(reqId, []).append(
                DailyBar(
                    symbol=symbol,
                    trading_date=_parse_daily_date(str(bar.date)),
                    open=Decimal(str(bar.open)),
                    high=Decimal(str(bar.high)),
                    low=Decimal(str(bar.low)),
                    close=Decimal(str(bar.close)),
                    volume=Decimal(str(bar.volume)),
                    average=Decimal(str(bar.wap)),
                    bar_count=int(bar.barCount),
                )
            )

        def historicalDataEnd(
            self, reqId: int, start: str, end: str
        ) -> None:
            self.ranges[reqId] = (str(start), str(end))
            event = request_events.get(reqId)
            if event is not None:
                event.set()

        def error(self, reqId: int, *args: Any) -> None:
            if len(args) >= 3:
                _, error_code, error_string, *_ = args
            elif len(args) == 2:
                error_code, error_string = args
            else:
                return
            request_id = int(reqId)
            code = int(error_code)
            self.errors.append(
                (request_id, code, str(error_string))
            )
            if (
                request_id in request_events
                and code not in INFORMATIONAL_ERROR_CODES
            ):
                request_events[request_id].set()

    app = HistoricalApp()
    network_thread: Thread | None = None
    try:
        app.connect(
            config.host,
            config.port,
            clientId=config.client_id,
        )
        if not app.isConnected():
            raise IBKRReadOnlyError("IBKR API socket connection failed")

        network_thread = Thread(
            target=app.run,
            name="ibkr-history-network",
            daemon=True,
        )
        network_thread.start()
        deadline = monotonic() + timeout_seconds
        _wait_for(
            handshake_complete,
            deadline,
            "IBKR protocol handshake",
        )

        for index, symbol in enumerate(symbols):
            request_id = 9300 + index
            request_events[request_id] = Event()
            request_symbols[request_id] = symbol
            contract = Contract()
            contract.symbol = symbol
            contract.secType = "STK"
            contract.exchange = "SMART"
            contract.currency = "USD"
            app.reqHistoricalData(
                request_id,
                contract,
                requested_end,
                duration,
                "1 day",
                "TRADES",
                1,
                1,
                False,
                [],
            )

        for request_id, event in request_events.items():
            _wait_for(
                event,
                deadline,
                f"historical data request {request_id}",
            )

        failed_requests = [
            (request_id, code, message)
            for request_id, code, message in app.errors
            if request_id in request_events
            and code not in INFORMATIONAL_ERROR_CODES
        ]
        if failed_requests:
            safe_errors = "; ".join(
                f"{request_id}/{code}: {message}"
                for request_id, code, message in failed_requests
            )
            raise IBKRReadOnlyError(
                f"IBKR historical request failed: {safe_errors}"
            )

        fetched_at = datetime.now(timezone.utc)
        series: list[HistoricalSeries] = []
        for request_id, symbol in request_symbols.items():
            returned_start, returned_end = app.ranges.get(
                request_id, ("", "")
            )
            bars = tuple(
                sorted(
                    app.bars.get(request_id, []),
                    key=lambda bar: bar.trading_date,
                )
            )
            series.append(
                HistoricalSeries(
                    source="ibkr_tws_api",
                    server_version=int(app.serverVersion()),
                    fetched_at=fetched_at,
                    request=HistoricalRequest(
                        symbol=symbol,
                        end_datetime=requested_end,
                        duration=duration,
                    ),
                    returned_start=returned_start,
                    returned_end=returned_end,
                    bars=bars,
                )
            )
        return tuple(series)
    finally:
        if app.isConnected():
            for request_id in request_events:
                app.cancelHistoricalData(request_id)
            app.disconnect()
        if network_thread is not None:
            network_thread.join(timeout=2)


def _parse_daily_date(value: str):
    normalized = value.strip()
    for pattern in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized, pattern).date()
        except ValueError:
            continue
    raise ValueError(f"unsupported IBKR daily bar date: {value}")


def _wait_for(event: Event, deadline: float, label: str) -> None:
    remaining = deadline - monotonic()
    if remaining <= 0 or not event.wait(remaining):
        raise IBKRReadOnlyError(f"timed out waiting for {label}")
