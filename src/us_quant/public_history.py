from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from time import sleep
from typing import Callable
from urllib.parse import quote
from urllib.request import Request, urlopen

from us_quant.history_queue import HistoryJobStore
from us_quant.market_data import (
    DailyBar,
    HistoricalRequest,
    HistoricalSeries,
    save_historical_series,
    validate_daily_series,
)


YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?period1={period1}&period2={period2}&interval=1d"
    "&events=history&includeAdjustedClose=true"
)


def collect_public_daily_history(
    symbol: str,
    *,
    years: int = 5,
    now: datetime | None = None,
) -> HistoricalSeries:
    if years <= 0:
        raise ValueError("years must be positive")
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=366 * years + 10)
    url = YAHOO_CHART_URL.format(
        symbol=quote(_yahoo_symbol(symbol)),
        period1=int(start.timestamp()),
        period2=int((end + timedelta(days=1)).timestamp()),
    )
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    return parse_yahoo_chart(
        payload,
        symbol=symbol,
        fetched_at=end,
        years=years,
    )


def parse_yahoo_chart(
    payload: dict,
    *,
    symbol: str,
    fetched_at: datetime,
    years: int,
) -> HistoricalSeries:
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(str(chart["error"]))
    results = chart.get("result") or []
    if not results:
        raise ValueError(f"secondary source returned no data for {symbol}")
    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote_rows = indicators.get("quote") or []
    if not quote_rows:
        raise ValueError(f"secondary source returned no OHLC for {symbol}")
    quote_row = quote_rows[0]
    adjusted_rows = indicators.get("adjclose") or []
    adjusted = (
        adjusted_rows[0].get("adjclose", [])
        if adjusted_rows
        else []
    )
    bars: list[DailyBar] = []
    for index, timestamp in enumerate(timestamps):
        values = {
            field: _at(quote_row.get(field, []), index)
            for field in ("open", "high", "low", "close", "volume")
        }
        if any(values[field] is None for field in ("open", "high", "low", "close")):
            continue
        raw_close = Decimal(str(values["close"]))
        adjusted_close = _at(adjusted, index)
        factor = (
            Decimal(str(adjusted_close)) / raw_close
            if adjusted_close is not None and raw_close > 0
            else Decimal("1")
        )
        adjusted_open = Decimal(str(values["open"])) * factor
        adjusted_high = Decimal(str(values["high"])) * factor
        adjusted_low = Decimal(str(values["low"])) * factor
        normalized_close = raw_close * factor
        adjusted_high = max(
            adjusted_high,
            adjusted_open,
            adjusted_low,
            normalized_close,
        )
        adjusted_low = min(
            adjusted_low,
            adjusted_open,
            adjusted_high,
            normalized_close,
        )
        bars.append(
            DailyBar(
                symbol=symbol,
                trading_date=datetime.fromtimestamp(
                    int(timestamp),
                    timezone.utc,
                ).date(),
                open=adjusted_open,
                high=adjusted_high,
                low=adjusted_low,
                close=normalized_close,
                volume=Decimal(str(values["volume"] or 0)),
                average=Decimal("0"),
                bar_count=0,
            )
        )
    if not bars:
        raise ValueError(f"secondary source returned no valid bars for {symbol}")
    bars.sort(key=lambda row: row.trading_date)
    return HistoricalSeries(
        source="yahoo_finance_chart_secondary",
        server_version=0,
        fetched_at=fetched_at,
        request=HistoricalRequest(
            symbol=symbol,
            end_datetime=fetched_at.isoformat(),
            duration=f"{years} Y",
            data_type="ADJUSTED_CLOSE_DERIVED_OHLC",
        ),
        returned_start=bars[0].trading_date.isoformat(),
        returned_end=bars[-1].trading_date.isoformat(),
        bars=tuple(bars),
    )


def run_public_history_queue(
    store: HistoryJobStore,
    *,
    data_root: str | Path = "data",
    maximum_jobs: int = 25,
    workers: int = 4,
    progress: Callable[[int, int, str, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, int]:
    if maximum_jobs <= 0:
        return store.counts()
    if workers <= 0 or workers > 6:
        raise ValueError("workers must be between 1 and 6")
    store.reset_stale_running()
    processed = 0
    while processed < maximum_jobs:
        if should_stop is not None and should_stop():
            break
        jobs = store.claim(min(workers, maximum_jobs - processed))
        if not jobs:
            break
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    collect_public_daily_history,
                    job.symbol,
                    years=_duration_years(job.duration),
                ): job
                for job in jobs
            }
            for future in as_completed(futures):
                job = futures[future]
                try:
                    series = future.result()
                    quality = validate_daily_series(series)
                    if not quality.passed:
                        codes = ",".join(
                            issue.code for issue in quality.issues
                        )
                        raise ValueError(
                            f"secondary data quality failed: {codes}"
                        )
                    save_historical_series(
                        series,
                        data_root=data_root,
                    )
                    store.complete(job.symbol, quality.row_count)
                    status = f"备用源完成 {quality.row_count} 根"
                except Exception as error:
                    store.fail(job.symbol, str(error))
                    status = "备用源失败"
                processed += 1
                if progress is not None:
                    progress(
                        processed,
                        maximum_jobs,
                        job.symbol,
                        status,
                    )
        if processed < maximum_jobs:
            sleep(0.5)
    return store.counts()


def _duration_years(duration: str) -> int:
    try:
        return max(1, int(duration.split()[0]))
    except (ValueError, IndexError):
        return 5


def _yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-")


def _at(values: list, index: int):
    return values[index] if index < len(values) else None
