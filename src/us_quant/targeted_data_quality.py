from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from statistics import median
import tempfile
from typing import Any
from uuid import uuid4

from us_quant.trading.domain.market_evidence_quality import (
    EXPECTED_MINUTES,
    MinuteEvidenceSessionQuality,
    evaluate_minute_evidence_session,
    group_regular_sessions,
    minute_is_in_evaluation_window,
    percentile as _percentile,
)
from us_quant.minute_data import MinuteQuoteRecord, MinuteQuoteStore
from us_quant.targeted_robustness import TargetedRobustnessResult


@dataclass(frozen=True, slots=True)
class SessionDataQuality:
    session_date: str
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
    failure_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TargetedDataQualityResult:
    run_id: str
    robustness_run_id: str
    symbol: str
    strategy_version_id: str
    strategy_semver: str
    data_hash: str
    raw_data_hash: str
    provider: str
    evidence_origins: tuple[str, ...]
    session_count: int
    high_quality_sessions: int
    minimum_completeness: Decimal
    median_completeness: Decimal
    maximum_consecutive_missing: int
    stale_fraction: Decimal
    invalid_quote_rows: int
    p95_source_age_seconds: Decimal | None
    size_coverage_fraction: Decimal
    sessions: tuple[SessionDataQuality, ...]
    evidence_grade: str
    status: str


def run_targeted_data_quality(
    robustness: TargetedRobustnessResult,
    raw_records: tuple[MinuteQuoteRecord, ...],
) -> TargetedDataQualityResult:
    if not raw_records:
        raise ValueError("数据质量评估需要原始分钟记录")
    if {row.symbol for row in raw_records} != {robustness.symbol}:
        raise ValueError("数据质量记录与稳健性标的不一致")
    if {row.provider for row in raw_records} != {robustness.provider}:
        raise ValueError("数据质量评估禁止跨行情源")
    usable_dates = tuple(
        sorted(
            {
                row.session_date
                for row in robustness.session_outcomes
                if row.scenario == "基准"
            }
        )
    )
    by_date = dict(group_regular_sessions(raw_records))
    sessions = tuple(
        _quality_result(
            evaluate_minute_evidence_session(
                session_date,
                by_date.get(session_date, ()),
            )
        )
        for session_date in usable_dates
    )
    completeness = tuple(row.completeness for row in sessions)
    total_raw = sum(row.raw_rows for row in sessions)
    stale_rows = sum(row.stale_rows for row in sessions)
    all_ages = sorted(
        Decimal(str(record.source_age_seconds))
        for record in raw_records
        if record.source_age_seconds is not None
        and record.source_age_seconds >= 0
        and minute_is_in_evaluation_window(record.minute, usable_dates)
    )
    total_usable = sum(row.usable_rows for row in sessions)
    total_size = sum(row.size_sample_count for row in sessions)
    high_quality = sum(row.high_quality for row in sessions)
    if not sessions:
        grade = "无对齐会话"
    elif high_quality < 25:
        grade = "高质量会话不足"
    elif total_size < total_usable:
        grade = "会话质量通过·深度覆盖不足"
    else:
        grade = "数据质量可进入独立评审"
    return TargetedDataQualityResult(
        run_id=uuid4().hex,
        robustness_run_id=robustness.run_id,
        symbol=robustness.symbol,
        strategy_version_id=robustness.strategy_version_id,
        strategy_semver=robustness.strategy_semver,
        data_hash=robustness.data_hash,
        raw_data_hash=MinuteQuoteStore.fingerprint(raw_records),
        provider=robustness.provider,
        evidence_origins=tuple(
            sorted({row.evidence_origin for row in raw_records})
        ),
        session_count=len(sessions),
        high_quality_sessions=high_quality,
        minimum_completeness=(
            min(completeness) if completeness else Decimal("0")
        ),
        median_completeness=(
            median(completeness) if completeness else Decimal("0")
        ),
        maximum_consecutive_missing=max(
            (
                row.maximum_consecutive_missing
                for row in sessions
            ),
            default=EXPECTED_MINUTES,
        ),
        stale_fraction=(
            Decimal(stale_rows) / Decimal(total_raw)
            if total_raw
            else Decimal("1")
        ),
        invalid_quote_rows=sum(
            row.invalid_quote_rows for row in sessions
        ),
        p95_source_age_seconds=(
            _percentile(all_ages, Decimal("0.95"))
            if all_ages
            else None
        ),
        size_coverage_fraction=(
            Decimal(total_size) / Decimal(total_usable)
            if total_usable
            else Decimal("0")
        ),
        sessions=sessions,
        evidence_grade=grade,
        status="research_data_quality",
    )


def save_targeted_data_quality(
    result: TargetedDataQualityResult,
    output_root: str | Path,
) -> Path:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{result.run_id}.json"
    payload = _json_ready(asdict(result))
    payload.update(
        {
            "automatic_strategy_promotion": False,
            "orders_submitted": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=root,
            prefix=f".{result.run_id}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return path


def load_targeted_data_quality(
    output_root: str | Path,
    *,
    limit: int = 50,
) -> tuple[TargetedDataQualityResult, ...]:
    root = Path(output_root)
    if not root.exists():
        return ()
    paths = sorted(
        root.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    results: list[TargetedDataQualityResult] = []
    for path in paths:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            sessions = tuple(
                SessionDataQuality(
                    session_date=str(item["session_date"]),
                    expected_minutes=int(item["expected_minutes"]),
                    raw_rows=int(item["raw_rows"]),
                    usable_rows=int(item["usable_rows"]),
                    completeness=Decimal(
                        str(item["completeness"])
                    ),
                    missing_minutes=int(item["missing_minutes"]),
                    maximum_consecutive_missing=int(
                        item["maximum_consecutive_missing"]
                    ),
                    stale_rows=int(item["stale_rows"]),
                    invalid_quote_rows=int(
                        item["invalid_quote_rows"]
                    ),
                    age_sample_count=int(item["age_sample_count"]),
                    median_source_age_seconds=_optional_decimal(
                        item.get("median_source_age_seconds")
                    ),
                    p95_source_age_seconds=_optional_decimal(
                        item.get("p95_source_age_seconds")
                    ),
                    maximum_source_age_seconds=_optional_decimal(
                        item.get("maximum_source_age_seconds")
                    ),
                    size_sample_count=int(item["size_sample_count"]),
                    size_coverage_fraction=Decimal(
                        str(item["size_coverage_fraction"])
                    ),
                    high_quality=bool(item["high_quality"]),
                    failure_reasons=tuple(
                        item.get("failure_reasons", ())
                    ),
                )
                for item in row["sessions"]
            )
            results.append(
                TargetedDataQualityResult(
                    run_id=str(row["run_id"]),
                    robustness_run_id=str(
                        row["robustness_run_id"]
                    ),
                    symbol=str(row["symbol"]),
                    strategy_version_id=str(
                        row["strategy_version_id"]
                    ),
                    strategy_semver=str(row["strategy_semver"]),
                    data_hash=str(row["data_hash"]),
                    raw_data_hash=str(row["raw_data_hash"]),
                    provider=str(row["provider"]),
                    evidence_origins=tuple(
                        row.get("evidence_origins", ())
                    ),
                    session_count=int(row["session_count"]),
                    high_quality_sessions=int(
                        row["high_quality_sessions"]
                    ),
                    minimum_completeness=Decimal(
                        str(row["minimum_completeness"])
                    ),
                    median_completeness=Decimal(
                        str(row["median_completeness"])
                    ),
                    maximum_consecutive_missing=int(
                        row["maximum_consecutive_missing"]
                    ),
                    stale_fraction=Decimal(
                        str(row["stale_fraction"])
                    ),
                    invalid_quote_rows=int(
                        row["invalid_quote_rows"]
                    ),
                    p95_source_age_seconds=_optional_decimal(
                        row.get("p95_source_age_seconds")
                    ),
                    size_coverage_fraction=Decimal(
                        str(row["size_coverage_fraction"])
                    ),
                    sessions=sessions,
                    evidence_grade=str(row["evidence_grade"]),
                    status=str(row["status"]),
                )
            )
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            continue
    return tuple(results)


def _quality_result(
    quality: MinuteEvidenceSessionQuality,
) -> SessionDataQuality:
    return SessionDataQuality(
        session_date=quality.session_date,
        expected_minutes=quality.expected_minutes,
        raw_rows=quality.raw_rows,
        usable_rows=quality.usable_rows,
        completeness=quality.completeness,
        missing_minutes=quality.missing_minutes,
        maximum_consecutive_missing=quality.maximum_consecutive_missing,
        stale_rows=quality.stale_rows,
        invalid_quote_rows=quality.invalid_quote_rows,
        age_sample_count=quality.age_sample_count,
        median_source_age_seconds=quality.median_source_age_seconds,
        p95_source_age_seconds=quality.p95_source_age_seconds,
        maximum_source_age_seconds=quality.maximum_source_age_seconds,
        size_sample_count=quality.size_sample_count,
        size_coverage_fraction=quality.size_coverage_fraction,
        high_quality=quality.high_quality,
        failure_reasons=quality.failure_reasons,
    )


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value
