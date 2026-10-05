"""Canonical New York minute-session grouping and quality facts.

This module is pure: it has no database, broker, UI, or strategy authority.
Research and readiness consumers use the same calculations so captured rows
cannot be counted under subtly different session rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Protocol
from zoneinfo import ZoneInfo



NEW_YORK = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EVALUATION_START = time(10, 0)
EVALUATION_END = time(15, 45)
EXPECTED_MINUTES = 346
MINIMUM_SESSION_ROWS = 300
MINIMUM_COMPLETENESS = Decimal("0.98")
MAXIMUM_CONSECUTIVE_MISSING = 2
MAXIMUM_P95_SOURCE_AGE_SECONDS = Decimal("5")


class MinuteEvidenceRecord(Protocol):
    """Storage-neutral shape consumed by the pure session evaluator."""

    minute: str
    realtime_ready: bool
    stale: bool
    bid: Decimal | None
    ask: Decimal | None
    source_age_seconds: float | None
    bid_size: Decimal | None
    ask_size: Decimal | None


@dataclass(frozen=True, slots=True)
class MinuteEvidenceSessionQuality:
    session_date: str
    row_count: int
    evaluation_window_covered: bool
    longest_contiguous_run: int
    robustness_usable: bool
    expected_minutes: int
    raw_rows: int
    usable_rows: int
    completeness: Decimal
    missing_minutes: int
    maximum_consecutive_missing: int
    stale_rows: int
    invalid_quote_rows: int
    age_sample_count: int
    median_source_age_seconds: Decimal | None
    p95_source_age_seconds: Decimal | None
    maximum_source_age_seconds: Decimal | None
    size_sample_count: int
    size_coverage_fraction: Decimal
    high_quality: bool
    robustness_failure_reasons: tuple[str, ...]
    failure_reasons: tuple[str, ...]


def group_regular_sessions(
    records: Iterable[MinuteEvidenceRecord],
) -> tuple[tuple[str, tuple[MinuteEvidenceRecord, ...]], ...]:
    """Group weekday regular-session rows by their New York trading date."""

    grouped: dict[str, list[MinuteEvidenceRecord]] = {}
    for record in sorted(records, key=lambda row: row.minute):
        eastern = parse_minute(record.minute).astimezone(NEW_YORK)
        if eastern.weekday() >= 5:
            continue
        wall_time = eastern.time().replace(tzinfo=None)
        if not REGULAR_OPEN <= wall_time < REGULAR_CLOSE:
            continue
        grouped.setdefault(eastern.date().isoformat(), []).append(record)
    return tuple(
        (session_date, tuple(rows))
        for session_date, rows in sorted(grouped.items())
    )


def evaluate_minute_evidence_session(
    session_date: str,
    records: Iterable[MinuteEvidenceRecord],
    *,
    minimum_rows: int = MINIMUM_SESSION_ROWS,
    minimum_contiguous_run: int = 1,
) -> MinuteEvidenceSessionQuality:
    """Calculate the existing robustness and data-quality facts once.

    ``records`` may contain the full regular session. Quality metrics are
    calculated only over 10:00–15:45 New York time; robustness continuity and
    row counts retain the prior behavior over all regular-session usable rows.
    """

    if minimum_rows < 1 or minimum_contiguous_run < 1:
        raise ValueError("session minimums must be positive")
    ordered = tuple(
        sorted(records, key=lambda row: parse_minute(row.minute))
    )
    same_session = tuple(
        row
        for row in ordered
        if parse_minute(row.minute).astimezone(NEW_YORK).date().isoformat()
        == session_date
        and REGULAR_OPEN
        <= parse_minute(row.minute).astimezone(NEW_YORK).time().replace(
            tzinfo=None
        )
        < REGULAR_CLOSE
    )

    robustness_rows = tuple(row for row in same_session if _robustness_usable(row))
    longest_contiguous = longest_contiguous_minutes(robustness_rows)
    if robustness_rows:
        first = parse_minute(robustness_rows[0].minute).astimezone(NEW_YORK)
        last = parse_minute(robustness_rows[-1].minute).astimezone(NEW_YORK)
        window_covered = (
            first.time().replace(tzinfo=None) <= EVALUATION_START
            and last.time().replace(tzinfo=None) >= EVALUATION_END
        )
    else:
        window_covered = False
    robustness_failures: list[str] = []
    if len(robustness_rows) < minimum_rows:
        robustness_failures.append("分钟行数不足")
    if longest_contiguous < minimum_contiguous_run:
        robustness_failures.append("连续预热分钟不足")
    if not window_covered:
        robustness_failures.append("未覆盖完整评估窗口")

    rows_by_minute: dict[str, MinuteQuoteRecord] = {}
    for row in same_session:
        eastern = parse_minute(row.minute).astimezone(NEW_YORK)
        wall_time = eastern.time().replace(tzinfo=None)
        if EVALUATION_START <= wall_time <= EVALUATION_END:
            rows_by_minute[eastern.strftime("%H:%M")] = row

    expected = expected_minute_keys()
    usable_keys = {
        key for key, row in rows_by_minute.items() if _quality_usable(row)
    }
    missing_flags = tuple(key not in usable_keys for key in expected)
    missing = sum(missing_flags)
    maximum_missing = longest_true_run(missing_flags)
    raw_rows = len(rows_by_minute)
    stale = sum(row.stale for row in rows_by_minute.values())
    invalid = sum(
        row.bid is not None
        and row.ask is not None
        and (row.bid <= 0 or row.ask <= 0 or row.ask < row.bid)
        for row in rows_by_minute.values()
    )
    ages = sorted(
        Decimal(str(row.source_age_seconds))
        for row in rows_by_minute.values()
        if row.source_age_seconds is not None
        and row.source_age_seconds >= 0
    )
    usable_count = len(usable_keys)
    size_samples = sum(
        key in usable_keys
        and row.bid_size is not None
        and row.ask_size is not None
        and row.bid_size > 0
        and row.ask_size > 0
        for key, row in rows_by_minute.items()
    )
    completeness = Decimal(usable_count) / Decimal(EXPECTED_MINUTES)
    p95_age = percentile(ages, Decimal("0.95")) if ages else None
    quality_failures: list[str] = []
    if completeness < MINIMUM_COMPLETENESS:
        quality_failures.append("完整率低于 98%")
    if maximum_missing > MAXIMUM_CONSECUTIVE_MISSING:
        quality_failures.append("连续缺口超过 2 分钟")
    if invalid:
        quality_failures.append("存在非正或倒挂报价")
    if p95_age is None:
        quality_failures.append("行情年龄不可估计")
    elif p95_age > MAXIMUM_P95_SOURCE_AGE_SECONDS:
        quality_failures.append("行情年龄 P95 超过 5 秒")

    return MinuteEvidenceSessionQuality(
        session_date=session_date,
        row_count=len(robustness_rows),
        evaluation_window_covered=window_covered,
        longest_contiguous_run=longest_contiguous,
        robustness_usable=not robustness_failures,
        expected_minutes=EXPECTED_MINUTES,
        raw_rows=raw_rows,
        usable_rows=usable_count,
        completeness=completeness,
        missing_minutes=missing,
        maximum_consecutive_missing=maximum_missing,
        stale_rows=stale,
        invalid_quote_rows=invalid,
        age_sample_count=len(ages),
        median_source_age_seconds=percentile(ages, Decimal("0.5")) if ages else None,
        p95_source_age_seconds=p95_age,
        maximum_source_age_seconds=max(ages) if ages else None,
        size_sample_count=size_samples,
        size_coverage_fraction=(
            Decimal(size_samples) / Decimal(usable_count)
            if usable_count
            else Decimal("0")
        ),
        high_quality=not quality_failures,
        robustness_failure_reasons=tuple(robustness_failures),
        failure_reasons=tuple(quality_failures),
    )


def parse_minute(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def minute_is_in_evaluation_window(
    value: str,
    session_dates: tuple[str, ...] | None = None,
) -> bool:
    eastern = parse_minute(value).astimezone(NEW_YORK)
    return (
        (session_dates is None or eastern.date().isoformat() in session_dates)
        and EVALUATION_START
        <= eastern.time().replace(tzinfo=None)
        <= EVALUATION_END
    )


def expected_minute_keys() -> tuple[str, ...]:
    start = datetime.combine(date(2000, 1, 1), EVALUATION_START)
    return tuple(
        (start + timedelta(minutes=index)).strftime("%H:%M")
        for index in range(EXPECTED_MINUTES)
    )


def longest_contiguous_minutes(
    records: Iterable[MinuteEvidenceRecord],
) -> int:
    longest = 0
    current = 0
    previous: datetime | None = None
    for row in records:
        observed = parse_minute(row.minute)
        if previous is None or (observed - previous).total_seconds() == 60:
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        previous = observed
    return longest


def longest_true_run(values: Iterable[bool]) -> int:
    longest = 0
    current = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def percentile(values: list[Decimal], quantile: Decimal) -> Decimal:
    if not values:
        raise ValueError("percentile requires values")
    if len(values) == 1:
        return values[0]
    position = quantile * Decimal(len(values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - Decimal(lower)
    return values[lower] + (values[upper] - values[lower]) * fraction


def _robustness_usable(row: MinuteEvidenceRecord) -> bool:
    # This matches MinuteQuoteStore.load(usable_only=True), which is the
    # existing robustness input contract (including its exact SQL filters).
    return (
        row.realtime_ready
        and not row.stale
        and row.bid is not None
        and row.ask is not None
    )


def _quality_usable(row: MinuteEvidenceRecord) -> bool:
    return (
        row.realtime_ready
        and not row.stale
        and row.bid is not None
        and row.ask is not None
        and row.bid > 0
        and row.ask >= row.bid
    )


__all__ = [
    "EVALUATION_END",
    "EVALUATION_START",
    "EXPECTED_MINUTES",
    "MAXIMUM_CONSECUTIVE_MISSING",
    "MAXIMUM_P95_SOURCE_AGE_SECONDS",
    "MINIMUM_COMPLETENESS",
    "MINIMUM_SESSION_ROWS",
    "NEW_YORK",
    "MinuteEvidenceRecord",
    "REGULAR_CLOSE",
    "REGULAR_OPEN",
    "MinuteEvidenceSessionQuality",
    "evaluate_minute_evidence_session",
    "expected_minute_keys",
    "group_regular_sessions",
    "longest_contiguous_minutes",
    "longest_true_run",
    "minute_is_in_evaluation_window",
    "parse_minute",
    "percentile",
]
