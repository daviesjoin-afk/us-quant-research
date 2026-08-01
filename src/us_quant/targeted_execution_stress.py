from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4

from us_quant.minute_data import MinuteQuoteRecord
from us_quant.targeted_replay import run_targeted_replay
from us_quant.targeted_robustness import (
    TargetedRobustnessResult,
    group_regular_sessions,
)


MAXIMUM_P95_TOP_OF_BOOK_PARTICIPATION = Decimal("0.10")


@dataclass(frozen=True, slots=True)
class ExecutionStressScenario:
    scenario: str
    slippage_bps: Decimal
    commission_per_order: Decimal
    session_count: int
    compounded_return: Decimal
    maximum_drawdown: Decimal
    total_fills: int
    commission_cost: Decimal
    degradation_vs_configured: Decimal


@dataclass(frozen=True, slots=True)
class TargetedExecutionStressResult:
    run_id: str
    robustness_run_id: str
    symbol: str
    strategy_version_id: str
    strategy_semver: str
    base_parameter_hash: str
    data_hash: str
    provider: str
    scenarios: tuple[ExecutionStressScenario, ...]
    worst_stressed_return: Decimal
    worst_performance_degradation: Decimal
    size_observations: int
    p95_top_of_book_participation: Decimal | None
    maximum_top_of_book_participation: Decimal | None
    capacity_status: str
    stress_resilient: bool
    evidence_grade: str
    limitations: tuple[str, ...]
    status: str


def run_targeted_execution_stress(
    robustness: TargetedRobustnessResult,
    records: tuple[MinuteQuoteRecord, ...],
    *,
    parameters: dict[str, object],
    initial_equity: Decimal,
) -> TargetedExecutionStressResult:
    if not records:
        raise ValueError("执行压力测试需要可用分钟记录")
    if {row.symbol for row in records} != {robustness.symbol}:
        raise ValueError("执行压力记录与稳健性标的不一致")
    if {row.provider for row in records} != {robustness.provider}:
        raise ValueError("执行压力测试禁止跨行情源")
    usable_dates = {
        row.session_date
        for row in robustness.session_outcomes
        if row.scenario == "基准"
    }
    sessions = tuple(
        (session_date, rows)
        for session_date, rows in group_regular_sessions(records)
        if session_date in usable_dates
    )
    if len(sessions) != len(usable_dates):
        raise ValueError("执行压力测试无法覆盖全部稳健性会话")

    configured_slippage = Decimal(str(parameters["slippage_bps"]))
    configured_commission = Decimal(
        str(parameters["commission_per_order"])
    )
    variants = (
        (
            "配置成本",
            configured_slippage,
            configured_commission,
        ),
        (
            "滑点至少 5bps",
            max(configured_slippage, Decimal("5")),
            configured_commission,
        ),
        (
            "滑点至少 10bps + 双倍佣金",
            max(configured_slippage, Decimal("10")),
            configured_commission * Decimal("2"),
        ),
    )
    summaries: list[ExecutionStressScenario] = []
    baseline_fills: list[tuple[str, str, int]] = []
    baseline_return: Decimal | None = None
    for scenario, slippage, commission in variants:
        scenario_parameters = dict(parameters)
        scenario_parameters["slippage_bps"] = format(
            slippage.normalize(), "f"
        )
        scenario_parameters["commission_per_order"] = format(
            commission.normalize(), "f"
        )
        session_returns: list[Decimal] = []
        drawdowns: list[Decimal] = []
        total_fills = 0
        total_commission = Decimal("0")
        for session_date, session_rows in sessions:
            replay = run_targeted_replay(
                session_rows,
                strategy_version_id=robustness.strategy_version_id,
                strategy_semver=robustness.strategy_semver,
                parameter_hash=robustness.base_parameter_hash,
                parameters=scenario_parameters,
                initial_equity=initial_equity,
            )
            session_returns.append(replay.total_return)
            drawdowns.append(replay.maximum_drawdown)
            total_fills += len(replay.fills)
            total_commission += replay.commission_cost
            if scenario == "配置成本":
                baseline_fills.extend(
                    (
                        fill.occurred_at,
                        fill.side,
                        fill.quantity,
                    )
                    for fill in replay.fills
                )
        compounded = _compound(session_returns)
        if baseline_return is None:
            baseline_return = compounded
        summaries.append(
            ExecutionStressScenario(
                scenario=scenario,
                slippage_bps=slippage,
                commission_per_order=commission,
                session_count=len(session_returns),
                compounded_return=compounded,
                maximum_drawdown=max(
                    drawdowns, default=Decimal("0")
                ),
                total_fills=total_fills,
                commission_cost=total_commission,
                degradation_vs_configured=(
                    compounded - baseline_return
                ),
            )
        )
    participation = _participation_ratios(records, baseline_fills)
    p95_participation = (
        _percentile(participation, Decimal("0.95"))
        if participation
        else None
    )
    max_participation = max(participation) if participation else None
    if p95_participation is None:
        capacity_status = "盘口数量不足·不可估计"
    elif p95_participation > MAXIMUM_P95_TOP_OF_BOOK_PARTICIPATION:
        capacity_status = "盘口参与率过高"
    else:
        capacity_status = "Level-I盘口参与率门通过"
    worst_stress = min(
        summary.compounded_return for summary in summaries[1:]
    )
    worst_degradation = min(
        summary.degradation_vs_configured
        for summary in summaries[1:]
    )
    stress_resilient = (
        worst_stress > 0
        and p95_participation is not None
        and p95_participation
        <= MAXIMUM_P95_TOP_OF_BOOK_PARTICIPATION
    )
    limitations = (
        "Level-I 仅能比较订单与当时最优价一档数量，不能估计完整订单簿冲击。",
        "压力测试使用同一信号路径改变佣金和滑点，不代表未来真实成交保证。",
        "结果仅用于独立评审，不会自动晋级或提交订单。",
    )
    return TargetedExecutionStressResult(
        run_id=uuid4().hex,
        robustness_run_id=robustness.run_id,
        symbol=robustness.symbol,
        strategy_version_id=robustness.strategy_version_id,
        strategy_semver=robustness.strategy_semver,
        base_parameter_hash=robustness.base_parameter_hash,
        data_hash=robustness.data_hash,
        provider=robustness.provider,
        scenarios=tuple(summaries),
        worst_stressed_return=worst_stress,
        worst_performance_degradation=worst_degradation,
        size_observations=len(participation),
        p95_top_of_book_participation=p95_participation,
        maximum_top_of_book_participation=max_participation,
        capacity_status=capacity_status,
        stress_resilient=stress_resilient,
        evidence_grade=(
            "执行压力可进入独立评审"
            if stress_resilient
            else "执行压力未通过"
        ),
        limitations=limitations,
        status="research_execution_stress",
    )


def save_targeted_execution_stress(
    result: TargetedExecutionStressResult,
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


def load_targeted_execution_stress(
    output_root: str | Path,
    *,
    limit: int = 50,
) -> tuple[TargetedExecutionStressResult, ...]:
    root = Path(output_root)
    if not root.exists():
        return ()
    paths = sorted(
        root.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    results: list[TargetedExecutionStressResult] = []
    for path in paths:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            results.append(
                TargetedExecutionStressResult(
                    run_id=str(row["run_id"]),
                    robustness_run_id=str(
                        row["robustness_run_id"]
                    ),
                    symbol=str(row["symbol"]),
                    strategy_version_id=str(
                        row["strategy_version_id"]
                    ),
                    strategy_semver=str(row["strategy_semver"]),
                    base_parameter_hash=str(
                        row["base_parameter_hash"]
                    ),
                    data_hash=str(row["data_hash"]),
                    provider=str(row["provider"]),
                    scenarios=tuple(
                        ExecutionStressScenario(
                            scenario=str(item["scenario"]),
                            slippage_bps=Decimal(
                                str(item["slippage_bps"])
                            ),
                            commission_per_order=Decimal(
                                str(item["commission_per_order"])
                            ),
                            session_count=int(
                                item["session_count"]
                            ),
                            compounded_return=Decimal(
                                str(item["compounded_return"])
                            ),
                            maximum_drawdown=Decimal(
                                str(item["maximum_drawdown"])
                            ),
                            total_fills=int(item["total_fills"]),
                            commission_cost=Decimal(
                                str(item["commission_cost"])
                            ),
                            degradation_vs_configured=Decimal(
                                str(
                                    item[
                                        "degradation_vs_configured"
                                    ]
                                )
                            ),
                        )
                        for item in row["scenarios"]
                    ),
                    worst_stressed_return=Decimal(
                        str(row["worst_stressed_return"])
                    ),
                    worst_performance_degradation=Decimal(
                        str(row["worst_performance_degradation"])
                    ),
                    size_observations=int(row["size_observations"]),
                    p95_top_of_book_participation=_optional_decimal(
                        row.get("p95_top_of_book_participation")
                    ),
                    maximum_top_of_book_participation=_optional_decimal(
                        row.get(
                            "maximum_top_of_book_participation"
                        )
                    ),
                    capacity_status=str(row["capacity_status"]),
                    stress_resilient=bool(row["stress_resilient"]),
                    evidence_grade=str(row["evidence_grade"]),
                    limitations=tuple(row.get("limitations", ())),
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


def _participation_ratios(
    records: tuple[MinuteQuoteRecord, ...],
    fills: list[tuple[str, str, int]],
) -> list[Decimal]:
    by_minute = {
        _minute_key(row.minute): row for row in records
    }
    ratios: list[Decimal] = []
    for occurred_at, side, quantity in fills:
        record = by_minute.get(_minute_key(occurred_at))
        if record is None:
            continue
        size = (
            record.ask_size
            if side.upper() == "BUY"
            else record.bid_size
        )
        if size is None or size <= 0:
            continue
        ratios.append(Decimal(quantity) / size)
    return sorted(ratios)


def _minute_key(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(
        second=0, microsecond=0
    ).isoformat()


def _compound(returns: list[Decimal]) -> Decimal:
    equity = Decimal("1")
    for value in returns:
        equity *= Decimal("1") + value
    return equity - Decimal("1")


def _percentile(
    values: list[Decimal],
    quantile: Decimal,
) -> Decimal:
    if len(values) == 1:
        return values[0]
    position = quantile * Decimal(len(values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - Decimal(lower)
    return values[lower] + (values[upper] - values[lower]) * fraction


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
