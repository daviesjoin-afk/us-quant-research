from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from us_quant.domain import Bar, MarketSlice


@dataclass(frozen=True, slots=True)
class DailyBar:
    symbol: str
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    average: Decimal
    bar_count: int


@dataclass(frozen=True, slots=True)
class HistoricalRequest:
    symbol: str
    end_datetime: str
    duration: str
    bar_size: str = "1 day"
    data_type: str = "TRADES"
    regular_trading_hours_only: bool = True


@dataclass(frozen=True, slots=True)
class HistoricalSeries:
    source: str
    server_version: int
    fetched_at: datetime
    request: HistoricalRequest
    returned_start: str
    returned_end: str
    bars: tuple[DailyBar, ...]


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    severity: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    symbol: str
    row_count: int
    first_date: date | None
    last_date: date | None
    issues: tuple[DataQualityIssue, ...]

    @property
    def passed(self) -> bool:
        return not any(
            issue.severity == "error" for issue in self.issues
        )


@dataclass(frozen=True, slots=True)
class SavedHistoricalArtifact:
    symbol: str
    content_sha256: str
    raw_path: Path
    normalized_path: Path | None


@dataclass(frozen=True, slots=True)
class LoadedDailySeries:
    symbol: str
    source_sha256: str
    path: Path
    bars: tuple[DailyBar, ...]
    source: str = "legacy_unknown"
    price_basis: str = "unspecified_legacy"


def validate_daily_series(series: HistoricalSeries) -> DataQualityReport:
    issues: list[DataQualityIssue] = []
    bars = series.bars
    dates = [bar.trading_date for bar in bars]

    if len(bars) < 20:
        issues.append(
            DataQualityIssue(
                severity="error",
                code="too_few_rows",
                message="fewer than 20 daily bars were returned",
            )
        )
    if dates != sorted(dates):
        issues.append(
            DataQualityIssue(
                severity="error",
                code="not_sorted",
                message="daily bars are not sorted by trading date",
            )
        )
    if len(dates) != len(set(dates)):
        issues.append(
            DataQualityIssue(
                severity="error",
                code="duplicate_date",
                message="duplicate trading dates were returned",
            )
        )

    for bar in bars:
        if bar.symbol != series.request.symbol:
            issues.append(
                DataQualityIssue(
                    severity="error",
                    code="symbol_mismatch",
                    message=(
                        f"bar symbol {bar.symbol} differs from "
                        f"{series.request.symbol}"
                    ),
                )
            )
        prices = (bar.open, bar.high, bar.low, bar.close)
        if any(price <= 0 for price in prices):
            issues.append(
                DataQualityIssue(
                    severity="error",
                    code="non_positive_price",
                    message=f"non-positive price on {bar.trading_date}",
                )
            )
        if bar.high < max(bar.open, bar.close, bar.low):
            issues.append(
                DataQualityIssue(
                    severity="error",
                    code="invalid_high",
                    message=f"inconsistent high on {bar.trading_date}",
                )
            )
        if bar.low > min(bar.open, bar.close, bar.high):
            issues.append(
                DataQualityIssue(
                    severity="error",
                    code="invalid_low",
                    message=f"inconsistent low on {bar.trading_date}",
                )
            )
        if bar.volume < 0:
            issues.append(
                DataQualityIssue(
                    severity="error",
                    code="negative_volume",
                    message=f"negative volume on {bar.trading_date}",
                )
            )

    for previous, current in zip(bars, bars[1:]):
        if previous.close > 0:
            move = abs(current.close / previous.close - Decimal("1"))
            if move > Decimal("0.50"):
                issues.append(
                    DataQualityIssue(
                        severity="warning",
                        code="extreme_close_move",
                        message=(
                            f"close changed {move:.2%} on "
                            f"{current.trading_date}; check splits or bad data"
                        ),
                    )
                )

    unique_issues = tuple(
        DataQualityIssue(*values)
        for values in dict.fromkeys(
            (issue.severity, issue.code, issue.message)
            for issue in issues
        )
    )
    return DataQualityReport(
        symbol=series.request.symbol,
        row_count=len(bars),
        first_date=bars[0].trading_date if bars else None,
        last_date=bars[-1].trading_date if bars else None,
        issues=unique_issues,
    )


def save_historical_series(
    series: HistoricalSeries,
    *,
    data_root: str | Path = "data",
    include_normalized: bool = True,
) -> SavedHistoricalArtifact:
    root = Path(data_root)
    payload = _series_payload(series)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = sha256(canonical).hexdigest()
    timestamp = series.fetched_at.astimezone(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    stem = f"{timestamp}_{digest[:12]}"

    raw_path = (
        root
        / "raw"
        / "ibkr"
        / "historical"
        / series.request.symbol
        / f"{stem}.json"
    )
    normalized_path = (
        root
        / "normalized"
        / "ibkr"
        / "daily"
        / series.request.symbol
        / f"{stem}.json"
    )
    _write_once(raw_path, canonical)

    normalized_payload = {
        "source_sha256": digest,
        "source": series.source,
        "request_data_type": series.request.data_type,
        "price_basis": (
            "adjusted_research_proxy"
            if "ADJUSTED" in series.request.data_type.upper()
            else "raw_trade_ohlc"
        ),
        "symbol": series.request.symbol,
        "bars": [
            {
                "trading_date": bar.trading_date.isoformat(),
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": str(bar.volume),
                "average": str(bar.average),
                "bar_count": bar.bar_count,
            }
            for bar in series.bars
        ],
    }
    if include_normalized:
        _write_once(
            normalized_path,
            json.dumps(
                normalized_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
    return SavedHistoricalArtifact(
        symbol=series.request.symbol,
        content_sha256=digest,
        raw_path=raw_path,
        normalized_path=(
            normalized_path if include_normalized else None
        ),
    )


def load_latest_normalized_series(
    symbol: str,
    *,
    data_root: str | Path = "data",
    fallback_data_root: str | Path | None = None,
) -> LoadedDailySeries:
    roots = [Path(data_root)]
    if fallback_data_root is not None:
        fallback = Path(fallback_data_root)
        if fallback not in roots:
            roots.append(fallback)
    candidates: list[Path] = []
    for root in roots:
        directory = (
            root / "normalized" / "ibkr" / "daily" / symbol
        )
        root_candidates = sorted(directory.glob("*.json"))
        if root_candidates:
            candidates = root_candidates
            break
    if not candidates:
        raise FileNotFoundError(
            f"no normalized IBKR daily data found for {symbol}"
        )
    path = candidates[-1]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("symbol") != symbol:
        raise ValueError(
            f"normalized file symbol does not match {symbol}: {path}"
        )

    bars = tuple(
        DailyBar(
            symbol=symbol,
            trading_date=date.fromisoformat(row["trading_date"]),
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=Decimal(row["volume"]),
            average=Decimal(row["average"]),
            bar_count=int(row["bar_count"]),
        )
        for row in payload["bars"]
    )
    completed_before = _current_us_trading_date()
    bars = tuple(
        bar for bar in bars if bar.trading_date < completed_before
    )
    return LoadedDailySeries(
        symbol=symbol,
        source_sha256=payload["source_sha256"],
        path=path,
        bars=bars,
        source=str(payload.get("source", "legacy_unknown")),
        price_basis=str(
            payload.get("price_basis", "unspecified_legacy")
        ),
    )


def _current_us_trading_date(
    now: datetime | None = None,
) -> date:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("current time must be timezone-aware")
    try:
        return current.astimezone(
            ZoneInfo("America/New_York")
        ).date()
    except ZoneInfoNotFoundError:
        return current.astimezone(timezone.utc).date()


def build_aligned_market_slices(
    series_collection: tuple[LoadedDailySeries, ...],
) -> tuple[MarketSlice, ...]:
    if not series_collection:
        raise ValueError("at least one daily series is required")

    by_symbol = {
        series.symbol: {
            bar.trading_date: bar for bar in series.bars
        }
        for series in series_collection
    }
    common_dates = set.intersection(
        *(set(bars) for bars in by_symbol.values())
    )
    if not common_dates:
        raise ValueError("daily series have no common trading dates")

    slices: list[MarketSlice] = []
    for trading_date in sorted(common_dates):
        timestamp = datetime.combine(
            trading_date,
            time.min,
            tzinfo=timezone.utc,
        )
        bars = {
            symbol: Bar(
                symbol=symbol,
                timestamp=timestamp,
                open=daily[trading_date].open,
                high=daily[trading_date].high,
                low=daily[trading_date].low,
                close=daily[trading_date].close,
                volume=int(daily[trading_date].volume),
            )
            for symbol, daily in by_symbol.items()
        }
        slices.append(MarketSlice(timestamp=timestamp, bars=bars))
    return tuple(slices)


def _series_payload(series: HistoricalSeries) -> dict[str, Any]:
    return {
        "source": series.source,
        "server_version": series.server_version,
        "fetched_at": series.fetched_at.astimezone(timezone.utc).isoformat(),
        "request": asdict(series.request),
        "returned_start": series.returned_start,
        "returned_end": series.returned_end,
        "bars": [
            {
                "symbol": bar.symbol,
                "trading_date": bar.trading_date.isoformat(),
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": str(bar.volume),
                "average": str(bar.average),
                "bar_count": bar.bar_count,
            }
            for bar in series.bars
        ],
    }


def _write_once(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_bytes()
        if existing != content:
            raise FileExistsError(
                f"immutable data path already exists with other content: {path}"
            )
        return

    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary.flush()
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)
