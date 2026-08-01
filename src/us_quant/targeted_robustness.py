from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
from statistics import median
import tempfile
from typing import Any, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

from us_quant.minute_data import MinuteQuoteRecord, MinuteQuoteStore
from us_quant.targeted_replay import run_targeted_replay


NEW_YORK = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EVALUATION_START = time(10, 0)
EVALUATION_END = time(15, 45)
MINIMUM_SESSION_ROWS = 300


@dataclass(frozen=True, slots=True)
class SessionReplayOutcome:
    scenario: str
    session_date: str
    parameter_hash: str
    data_hash: str
    row_count: int
    gap_count: int
    total_return: Decimal
    maximum_drawdown: Decimal
    realized_pnl: Decimal
    commission_cost: Decimal
    fill_count: int


@dataclass(frozen=True, slots=True)
class PerturbationSummary:
    scenario: str
    parameter_hash: str
    changed_parameters: dict[str, object]
    session_count: int
    compounded_return: Decimal
    mean_session_return: Decimal
    median_session_return: Decimal
    worst_session_return: Decimal
    best_session_return: Decimal
    maximum_drawdown: Decimal
    profitable_session_fraction: Decimal
    total_fills: int
    commission_cost: Decimal


@dataclass(frozen=True, slots=True)
class TargetedRobustnessResult:
    run_id: str
    symbol: str
    strategy_version_id: str
    strategy_semver: str
    base_parameter_hash: str
    data_hash: str
    provider: str
    coverages: tuple[str, ...]
    first_session: str
    last_session: str
    total_sessions: int
    usable_sessions: int
    skipped_sessions: tuple[str, ...]
    minimum_required_minutes: int
    scenario_summaries: tuple[PerturbationSummary, ...]
    session_outcomes: tuple[SessionReplayOutcome, ...]
    sign_stability_fraction: Decimal
    evidence_grade: str
    review_ready: bool
    status: str
    evidence_origins: tuple[str, ...] = ("captured_stream",)


def run_targeted_robustness(
    records: tuple[MinuteQuoteRecord, ...],
    *,
    strategy_version_id: str,
    strategy_semver: str,
    parameter_hash: str,
    parameters: dict[str, object],
    initial_equity: Decimal,
) -> TargetedRobustnessResult:
    if not records:
        raise ValueError("没有可用于多日稳健性评估的分钟数据")
    symbols = {row.symbol for row in records}
    if len(symbols) != 1:
        raise ValueError("多日稳健性评估只能包含一个代码")
    providers = {row.provider for row in records}
    if len(providers) != 1:
        raise ValueError("多日稳健性评估禁止跨行情源拼接")
    sessions = group_regular_sessions(records)
    if not sessions:
        raise ValueError("纽约常规交易时段内没有可用分钟数据")
    signal_required = max(
        int(parameters["warmup_minutes"]),
        int(parameters["momentum_lookback_minutes"]) + 1,
    )
    required = max(MINIMUM_SESSION_ROWS, signal_required)
    usable: list[tuple[str, tuple[MinuteQuoteRecord, ...]]] = []
    skipped: list[str] = []
    for session_date, rows in sessions:
        first = _parse_minute(rows[0].minute).astimezone(NEW_YORK)
        last = _parse_minute(rows[-1].minute).astimezone(NEW_YORK)
        if (
            len(rows) < required
            or _longest_contiguous_run(rows) < signal_required
            or first.time().replace(tzinfo=None) > EVALUATION_START
            or last.time().replace(tzinfo=None) < EVALUATION_END
        ):
            skipped.append(session_date)
        else:
            usable.append((session_date, rows))
    if not usable:
        raise ValueError(
            "独立交易日均未覆盖完整评估窗口："
            f"纽约 {EVALUATION_START:%H:%M}–{EVALUATION_END:%H:%M}，"
            f"至少 {required} 行且需 {signal_required} 个连续分钟"
        )

    scenarios = _parameter_scenarios(parameters, parameter_hash)
    outcomes: list[SessionReplayOutcome] = []
    summaries: list[PerturbationSummary] = []
    for scenario, scenario_hash, changed, scenario_parameters in scenarios:
        scenario_results = []
        for session_date, rows in usable:
            replay = run_targeted_replay(
                rows,
                strategy_version_id=strategy_version_id,
                strategy_semver=strategy_semver,
                parameter_hash=scenario_hash,
                parameters=scenario_parameters,
                initial_equity=initial_equity,
            )
            scenario_results.append(replay)
            outcomes.append(
                SessionReplayOutcome(
                    scenario=scenario,
                    session_date=session_date,
                    parameter_hash=scenario_hash,
                    data_hash=replay.data_hash,
                    row_count=replay.row_count,
                    gap_count=replay.gap_count,
                    total_return=replay.total_return,
                    maximum_drawdown=replay.maximum_drawdown,
                    realized_pnl=replay.realized_pnl,
                    commission_cost=replay.commission_cost,
                    fill_count=len(replay.fills),
                )
            )
        returns = [row.total_return for row in scenario_results]
        summaries.append(
            PerturbationSummary(
                scenario=scenario,
                parameter_hash=scenario_hash,
                changed_parameters=changed,
                session_count=len(scenario_results),
                compounded_return=_compound(returns),
                mean_session_return=sum(
                    returns, Decimal("0")
                ) / Decimal(len(returns)),
                median_session_return=median(returns),
                worst_session_return=min(returns),
                best_session_return=max(returns),
                maximum_drawdown=max(
                    _compounded_drawdown(returns),
                    *(row.maximum_drawdown for row in scenario_results),
                ),
                profitable_session_fraction=(
                    Decimal(sum(value > 0 for value in returns))
                    / Decimal(len(returns))
                ),
                total_fills=sum(
                    len(row.fills) for row in scenario_results
                ),
                commission_cost=sum(
                    (row.commission_cost for row in scenario_results),
                    Decimal("0"),
                ),
            )
        )
    baseline_return = summaries[0].compounded_return
    same_sign = sum(
        _same_sign(summary.compounded_return, baseline_return)
        for summary in summaries
    )
    stability = Decimal(same_sign) / Decimal(len(summaries))
    session_count = len(usable)
    if session_count < 5:
        grade = "样本不足"
    elif session_count < 20:
        grade = "初步多会话"
    elif stability < Decimal("0.8"):
        grade = "扩展样本·参数不稳"
    elif baseline_return <= 0:
        grade = "扩展样本·基准参数非正"
    else:
        grade = "扩展样本·可进入时间隔离验证"
    regular_records = tuple(
        row for _, session_rows in sessions for row in session_rows
    )
    return TargetedRobustnessResult(
        run_id=uuid4().hex,
        symbol=next(iter(symbols)),
        strategy_version_id=strategy_version_id,
        strategy_semver=strategy_semver,
        base_parameter_hash=parameter_hash,
        data_hash=MinuteQuoteStore.fingerprint(regular_records),
        provider=next(iter(providers)),
        coverages=tuple(
            sorted({row.coverage for row in regular_records})
        ),
        first_session=sessions[0][0],
        last_session=sessions[-1][0],
        total_sessions=len(sessions),
        usable_sessions=session_count,
        skipped_sessions=tuple(skipped),
        minimum_required_minutes=required,
        scenario_summaries=tuple(summaries),
        session_outcomes=tuple(outcomes),
        sign_stability_fraction=stability,
        evidence_grade=grade,
        review_ready=(
            session_count >= 20
            and stability >= Decimal("0.8")
            and baseline_return > 0
        ),
        status="research_robustness",
        evidence_origins=tuple(
            sorted(
                {
                    row.evidence_origin
                    for row in regular_records
                }
            )
        ),
    )


def group_regular_sessions(
    records: Iterable[MinuteQuoteRecord],
) -> tuple[tuple[str, tuple[MinuteQuoteRecord, ...]], ...]:
    grouped: dict[str, list[MinuteQuoteRecord]] = {}
    for record in sorted(records, key=lambda row: row.minute):
        observed = _parse_minute(record.minute)
        eastern = observed.astimezone(NEW_YORK)
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


def save_targeted_robustness(
    result: TargetedRobustnessResult,
    output_root: str | Path,
) -> Path:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{result.run_id}.json"
    payload = _json_ready(asdict(result))
    payload.update(
        {
            "orders_submitted": False,
            "automatic_strategy_promotion": False,
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


def load_targeted_robustness(
    output_root: str | Path,
    *,
    limit: int = 50,
) -> tuple[TargetedRobustnessResult, ...]:
    root = Path(output_root)
    if not root.exists():
        return ()
    paths = sorted(
        root.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    results: list[TargetedRobustnessResult] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            summaries = tuple(
                PerturbationSummary(
                    scenario=str(row["scenario"]),
                    parameter_hash=str(row["parameter_hash"]),
                    changed_parameters=dict(
                        row.get("changed_parameters", {})
                    ),
                    session_count=int(row["session_count"]),
                    compounded_return=Decimal(
                        str(row["compounded_return"])
                    ),
                    mean_session_return=Decimal(
                        str(row["mean_session_return"])
                    ),
                    median_session_return=Decimal(
                        str(row["median_session_return"])
                    ),
                    worst_session_return=Decimal(
                        str(row["worst_session_return"])
                    ),
                    best_session_return=Decimal(
                        str(row["best_session_return"])
                    ),
                    maximum_drawdown=Decimal(
                        str(row["maximum_drawdown"])
                    ),
                    profitable_session_fraction=Decimal(
                        str(row["profitable_session_fraction"])
                    ),
                    total_fills=int(row["total_fills"]),
                    commission_cost=Decimal(
                        str(row["commission_cost"])
                    ),
                )
                for row in payload["scenario_summaries"]
            )
            outcomes = tuple(
                SessionReplayOutcome(
                    scenario=str(row["scenario"]),
                    session_date=str(row["session_date"]),
                    parameter_hash=str(row["parameter_hash"]),
                    data_hash=str(row["data_hash"]),
                    row_count=int(row["row_count"]),
                    gap_count=int(row["gap_count"]),
                    total_return=Decimal(str(row["total_return"])),
                    maximum_drawdown=Decimal(
                        str(row["maximum_drawdown"])
                    ),
                    realized_pnl=Decimal(str(row["realized_pnl"])),
                    commission_cost=Decimal(
                        str(row["commission_cost"])
                    ),
                    fill_count=int(row["fill_count"]),
                )
                for row in payload["session_outcomes"]
            )
            results.append(
                TargetedRobustnessResult(
                    run_id=str(payload["run_id"]),
                    symbol=str(payload["symbol"]),
                    strategy_version_id=str(
                        payload["strategy_version_id"]
                    ),
                    strategy_semver=str(payload["strategy_semver"]),
                    base_parameter_hash=str(
                        payload["base_parameter_hash"]
                    ),
                    data_hash=str(payload["data_hash"]),
                    provider=str(payload["provider"]),
                    coverages=tuple(payload.get("coverages", ())),
                    first_session=str(payload["first_session"]),
                    last_session=str(payload["last_session"]),
                    total_sessions=int(payload["total_sessions"]),
                    usable_sessions=int(payload["usable_sessions"]),
                    skipped_sessions=tuple(
                        payload.get("skipped_sessions", ())
                    ),
                    minimum_required_minutes=int(
                        payload["minimum_required_minutes"]
                    ),
                    scenario_summaries=summaries,
                    session_outcomes=outcomes,
                    sign_stability_fraction=Decimal(
                        str(payload["sign_stability_fraction"])
                    ),
                    evidence_grade=str(payload["evidence_grade"]),
                    review_ready=bool(payload["review_ready"]),
                    status=str(payload["status"]),
                    evidence_origins=tuple(
                        payload.get(
                            "evidence_origins",
                            ("captured_stream",),
                        )
                    ),
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


def _parameter_scenarios(
    parameters: dict[str, object],
    baseline_hash: str,
) -> tuple[
    tuple[str, str, dict[str, object], dict[str, object]], ...
]:
    scenarios: list[
        tuple[str, str, dict[str, object], dict[str, object]]
    ] = [("基准", baseline_hash, {}, dict(parameters))]
    for name, factor, fields in (
        ("入场阈值 -20%", Decimal("0.8"), ("minimum_momentum",)),
        ("入场阈值 +20%", Decimal("1.2"), ("minimum_momentum",)),
        (
            "退出距离 -20%",
            Decimal("0.8"),
            ("profit_target", "stop_loss", "trailing_stop"),
        ),
        (
            "退出距离 +20%",
            Decimal("1.2"),
            ("profit_target", "stop_loss", "trailing_stop"),
        ),
    ):
        variant = dict(parameters)
        changed: dict[str, object] = {}
        for field in fields:
            value = Decimal(str(parameters[field])) * factor
            value = max(Decimal("0.001"), min(Decimal("0.20"), value))
            formatted = format(value.normalize(), "f")
            variant[field] = formatted
            changed[field] = formatted
        scenario_hash = _parameter_hash(variant)
        scenarios.append((name, scenario_hash, changed, variant))
    return tuple(scenarios)


def _parameter_hash(parameters: dict[str, object]) -> str:
    canonical = json.dumps(
        parameters,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _parse_minute(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _longest_contiguous_run(
    records: tuple[MinuteQuoteRecord, ...],
) -> int:
    longest = 0
    current = 0
    previous: datetime | None = None
    for row in records:
        observed = _parse_minute(row.minute)
        if previous is None or (observed - previous).total_seconds() == 60:
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        previous = observed
    return longest


def _compound(returns: Iterable[Decimal]) -> Decimal:
    equity = Decimal("1")
    for value in returns:
        equity *= Decimal("1") + value
    return equity - Decimal("1")


def _compounded_drawdown(returns: Iterable[Decimal]) -> Decimal:
    equity = Decimal("1")
    peak = equity
    drawdown = Decimal("0")
    for value in returns:
        equity *= Decimal("1") + value
        peak = max(peak, equity)
        if peak > 0:
            drawdown = max(drawdown, (peak - equity) / peak)
    return drawdown


def _same_sign(value: Decimal, baseline: Decimal) -> bool:
    tolerance = Decimal("0.000001")
    if abs(baseline) <= tolerance:
        return abs(value) <= tolerance
    return value * baseline > 0


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value
