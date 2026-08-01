from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable
from uuid import uuid4

from us_quant.minute_data import MinuteQuoteRecord
from us_quant.targeted_robustness import (
    EVALUATION_END,
    EVALUATION_START,
    NEW_YORK,
    SessionReplayOutcome,
    TargetedRobustnessResult,
    group_regular_sessions,
)


INITIAL_TRAIN_SESSIONS = 10
VALIDATION_SESSIONS = 5
TEST_SESSIONS = 5
FOLD_STEP = 5


@dataclass(frozen=True, slots=True)
class ValidationMetrics:
    session_count: int
    compounded_return: Decimal
    mean_session_return: Decimal
    median_session_return: Decimal
    worst_session_return: Decimal
    maximum_drawdown: Decimal
    profitable_session_fraction: Decimal
    total_fills: int
    commission_cost: Decimal


@dataclass(frozen=True, slots=True)
class BenchmarkSessionOutcome:
    session_date: str
    total_return: Decimal
    realized_pnl: Decimal
    fill_count: int
    commission_cost: Decimal


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold_number: int
    train_start: str
    train_end: str
    train_sessions: int
    validation_start: str
    validation_end: str
    validation_sessions: int
    test_start: str
    test_end: str
    test_sessions: int
    selected_scenario: str
    selected_parameter_hash: str
    training_metrics: ValidationMetrics
    validation_metrics: ValidationMetrics
    validation_benchmark: ValidationMetrics
    test_metrics: ValidationMetrics
    test_benchmark: ValidationMetrics
    validation_passed: bool
    test_excess_return: Decimal
    test_used_for_selection: bool


@dataclass(frozen=True, slots=True)
class TargetedWalkForwardResult:
    run_id: str
    robustness_run_id: str
    symbol: str
    strategy_version_id: str
    strategy_semver: str
    base_parameter_hash: str
    data_hash: str
    provider: str
    candidate_count: int
    selection_rule: str
    initial_train_sessions: int
    validation_sessions: int
    test_sessions: int
    folds: tuple[WalkForwardFold, ...]
    out_of_sample_metrics: ValidationMetrics
    out_of_sample_benchmark: ValidationMetrics
    out_of_sample_excess_return: Decimal
    validation_passed_folds: int
    evidence_grade: str
    review_ready: bool
    status: str


def run_targeted_walk_forward(
    robustness: TargetedRobustnessResult,
    records: tuple[MinuteQuoteRecord, ...],
    *,
    parameters: dict[str, object],
    initial_equity: Decimal,
) -> TargetedWalkForwardResult:
    if robustness.usable_sessions < (
        INITIAL_TRAIN_SESSIONS
        + VALIDATION_SESSIONS
        + TEST_SESSIONS
    ):
        raise ValueError("时间隔离验证至少需要 20 个完整有效会话")
    providers = {row.provider for row in records}
    if providers != {robustness.provider}:
        raise ValueError("时间隔离验证必须使用稳健性结果的同一行情源")
    usable_dates = tuple(
        sorted(
            {
                outcome.session_date
                for outcome in robustness.session_outcomes
                if outcome.scenario == "基准"
            }
        )
    )
    outcomes = {
        (row.scenario, row.session_date): row
        for row in robustness.session_outcomes
    }
    scenarios = tuple(
        summary.scenario
        for summary in robustness.scenario_summaries
    )
    benchmark = build_intraday_benchmark_outcomes(
        records,
        usable_dates=usable_dates,
        parameters=parameters,
        initial_equity=initial_equity,
    )
    benchmark_by_date = {
        row.session_date: row for row in benchmark
    }
    if set(benchmark_by_date) != set(usable_dates):
        raise ValueError("基准无法覆盖全部有效会话")

    folds: list[WalkForwardFold] = []
    selected_test_outcomes: list[SessionReplayOutcome] = []
    benchmark_test_outcomes: list[BenchmarkSessionOutcome] = []
    train_end = INITIAL_TRAIN_SESSIONS
    fold_number = 1
    while (
        train_end + VALIDATION_SESSIONS + TEST_SESSIONS
        <= len(usable_dates)
    ):
        train_dates = usable_dates[:train_end]
        validation_dates = usable_dates[
            train_end : train_end + VALIDATION_SESSIONS
        ]
        test_dates = usable_dates[
            train_end
            + VALIDATION_SESSIONS : train_end
            + VALIDATION_SESSIONS
            + TEST_SESSIONS
        ]
        selected = _select_training_scenario(
            scenarios, train_dates, outcomes
        )
        training_rows = tuple(
            outcomes[(selected, session_date)]
            for session_date in train_dates
        )
        validation_rows = tuple(
            outcomes[(selected, session_date)]
            for session_date in validation_dates
        )
        test_rows = tuple(
            outcomes[(selected, session_date)]
            for session_date in test_dates
        )
        validation_benchmark_rows = tuple(
            benchmark_by_date[session_date]
            for session_date in validation_dates
        )
        test_benchmark_rows = tuple(
            benchmark_by_date[session_date]
            for session_date in test_dates
        )
        training_metrics = _strategy_metrics(training_rows)
        validation_metrics = _strategy_metrics(validation_rows)
        validation_benchmark_metrics = _benchmark_metrics(
            validation_benchmark_rows
        )
        test_metrics = _strategy_metrics(test_rows)
        test_benchmark_metrics = _benchmark_metrics(
            test_benchmark_rows
        )
        validation_passed = (
            validation_metrics.compounded_return > 0
            and validation_metrics.compounded_return
            > validation_benchmark_metrics.compounded_return
        )
        folds.append(
            WalkForwardFold(
                fold_number=fold_number,
                train_start=train_dates[0],
                train_end=train_dates[-1],
                train_sessions=len(train_dates),
                validation_start=validation_dates[0],
                validation_end=validation_dates[-1],
                validation_sessions=len(validation_dates),
                test_start=test_dates[0],
                test_end=test_dates[-1],
                test_sessions=len(test_dates),
                selected_scenario=selected,
                selected_parameter_hash=outcomes[
                    (selected, train_dates[0])
                ].parameter_hash,
                training_metrics=training_metrics,
                validation_metrics=validation_metrics,
                validation_benchmark=validation_benchmark_metrics,
                test_metrics=test_metrics,
                test_benchmark=test_benchmark_metrics,
                validation_passed=validation_passed,
                test_excess_return=(
                    test_metrics.compounded_return
                    - test_benchmark_metrics.compounded_return
                ),
                test_used_for_selection=False,
            )
        )
        selected_test_outcomes.extend(test_rows)
        benchmark_test_outcomes.extend(test_benchmark_rows)
        train_end += FOLD_STEP
        fold_number += 1

    oos = _strategy_metrics(tuple(selected_test_outcomes))
    oos_benchmark = _benchmark_metrics(
        tuple(benchmark_test_outcomes)
    )
    excess = (
        oos.compounded_return - oos_benchmark.compounded_return
    )
    passed_folds = sum(fold.validation_passed for fold in folds)
    review_ready = (
        len(folds) >= 2
        and passed_folds == len(folds)
        and oos.compounded_return > 0
        and excess > 0
    )
    if len(folds) < 2:
        grade = "单折时间隔离·仅探索"
    elif review_ready:
        grade = "多折时间隔离·可人工复核"
    else:
        grade = "多折时间隔离·未通过"
    return TargetedWalkForwardResult(
        run_id=uuid4().hex,
        robustness_run_id=robustness.run_id,
        symbol=robustness.symbol,
        strategy_version_id=robustness.strategy_version_id,
        strategy_semver=robustness.strategy_semver,
        base_parameter_hash=robustness.base_parameter_hash,
        data_hash=robustness.data_hash,
        provider=robustness.provider,
        candidate_count=len(scenarios),
        selection_rule=(
            "仅训练集：中位会话收益、复合收益、低回撤、低费用"
            "依次择优；验证集只做门控；测试集不参与选择"
        ),
        initial_train_sessions=INITIAL_TRAIN_SESSIONS,
        validation_sessions=VALIDATION_SESSIONS,
        test_sessions=TEST_SESSIONS,
        folds=tuple(folds),
        out_of_sample_metrics=oos,
        out_of_sample_benchmark=oos_benchmark,
        out_of_sample_excess_return=excess,
        validation_passed_folds=passed_folds,
        evidence_grade=grade,
        review_ready=review_ready,
        status="research_walk_forward",
    )


def build_intraday_benchmark_outcomes(
    records: tuple[MinuteQuoteRecord, ...],
    *,
    usable_dates: tuple[str, ...],
    parameters: dict[str, object],
    initial_equity: Decimal,
) -> tuple[BenchmarkSessionOutcome, ...]:
    by_date = dict(group_regular_sessions(records))
    max_fraction = Decimal(str(parameters["max_position_fraction"]))
    minimum_notional = Decimal(str(parameters["min_order_notional"]))
    commission = Decimal(str(parameters["commission_per_order"]))
    slippage = Decimal(str(parameters["slippage_bps"])) / Decimal(
        "10000"
    )
    results: list[BenchmarkSessionOutcome] = []
    for session_date in usable_dates:
        rows = by_date.get(session_date)
        if not rows:
            continue
        entry_rows = [
            row
            for row in rows
            if _eastern_time(row.minute) >= EVALUATION_START
            and row.ask is not None
        ]
        exit_rows = [
            row
            for row in rows
            if _eastern_time(row.minute) <= EVALUATION_END
            and row.bid is not None
        ]
        if not entry_rows or not exit_rows:
            continue
        entry = entry_rows[0]
        exit_row = exit_rows[-1]
        assert entry.ask is not None and exit_row.bid is not None
        buy_price = entry.ask * (Decimal("1") + slippage)
        sell_price = exit_row.bid * (Decimal("1") - slippage)
        quantity = int(
            (
                initial_equity
                * max_fraction
                / buy_price
            ).to_integral_value(rounding=ROUND_DOWN)
        )
        gross = buy_price * quantity
        if quantity < 1 or gross < minimum_notional:
            pnl = Decimal("0")
            fill_count = 0
            cost = Decimal("0")
        else:
            cost = commission * 2
            pnl = (sell_price - buy_price) * quantity - cost
            fill_count = 2
        results.append(
            BenchmarkSessionOutcome(
                session_date=session_date,
                total_return=pnl / initial_equity,
                realized_pnl=pnl,
                fill_count=fill_count,
                commission_cost=cost,
            )
        )
    return tuple(results)


def save_targeted_walk_forward(
    result: TargetedWalkForwardResult,
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
            "test_used_for_selection": False,
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


def load_targeted_walk_forwards(
    output_root: str | Path,
    *,
    limit: int = 50,
) -> tuple[TargetedWalkForwardResult, ...]:
    root = Path(output_root)
    if not root.exists():
        return ()
    paths = sorted(
        root.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    results: list[TargetedWalkForwardResult] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            folds = tuple(_fold_from_dict(row) for row in payload["folds"])
            results.append(
                TargetedWalkForwardResult(
                    run_id=str(payload["run_id"]),
                    robustness_run_id=str(
                        payload["robustness_run_id"]
                    ),
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
                    candidate_count=int(payload["candidate_count"]),
                    selection_rule=str(payload["selection_rule"]),
                    initial_train_sessions=int(
                        payload["initial_train_sessions"]
                    ),
                    validation_sessions=int(
                        payload["validation_sessions"]
                    ),
                    test_sessions=int(payload["test_sessions"]),
                    folds=folds,
                    out_of_sample_metrics=_metrics_from_dict(
                        payload["out_of_sample_metrics"]
                    ),
                    out_of_sample_benchmark=_metrics_from_dict(
                        payload["out_of_sample_benchmark"]
                    ),
                    out_of_sample_excess_return=Decimal(
                        str(payload["out_of_sample_excess_return"])
                    ),
                    validation_passed_folds=int(
                        payload["validation_passed_folds"]
                    ),
                    evidence_grade=str(payload["evidence_grade"]),
                    review_ready=bool(payload["review_ready"]),
                    status=str(payload["status"]),
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


def _select_training_scenario(
    scenarios: tuple[str, ...],
    dates: tuple[str, ...],
    outcomes: dict[tuple[str, str], SessionReplayOutcome],
) -> str:
    scored = []
    for scenario in scenarios:
        metrics = _strategy_metrics(
            tuple(outcomes[(scenario, date)] for date in dates)
        )
        scored.append(
            (
                metrics.median_session_return,
                metrics.compounded_return,
                -metrics.maximum_drawdown,
                -metrics.commission_cost,
                scenario,
            )
        )
    return max(scored)[-1]


def _strategy_metrics(
    outcomes: tuple[SessionReplayOutcome, ...],
) -> ValidationMetrics:
    return _metrics(
        returns=tuple(row.total_return for row in outcomes),
        intraday_drawdowns=tuple(
            row.maximum_drawdown for row in outcomes
        ),
        total_fills=sum(row.fill_count for row in outcomes),
        commission=sum(
            (row.commission_cost for row in outcomes),
            Decimal("0"),
        ),
    )


def _benchmark_metrics(
    outcomes: tuple[BenchmarkSessionOutcome, ...],
) -> ValidationMetrics:
    return _metrics(
        returns=tuple(row.total_return for row in outcomes),
        intraday_drawdowns=(),
        total_fills=sum(row.fill_count for row in outcomes),
        commission=sum(
            (row.commission_cost for row in outcomes),
            Decimal("0"),
        ),
    )


def _metrics(
    *,
    returns: tuple[Decimal, ...],
    intraday_drawdowns: tuple[Decimal, ...],
    total_fills: int,
    commission: Decimal,
) -> ValidationMetrics:
    if not returns:
        raise ValueError("validation metrics require at least one session")
    compounded = _compound(returns)
    maximum_drawdown = _compounded_drawdown(returns)
    if intraday_drawdowns:
        maximum_drawdown = max(
            maximum_drawdown, *intraday_drawdowns
        )
    ordered = sorted(returns)
    middle = len(ordered) // 2
    median_value = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / Decimal("2")
    )
    return ValidationMetrics(
        session_count=len(returns),
        compounded_return=compounded,
        mean_session_return=sum(
            returns, Decimal("0")
        ) / Decimal(len(returns)),
        median_session_return=median_value,
        worst_session_return=min(returns),
        maximum_drawdown=maximum_drawdown,
        profitable_session_fraction=(
            Decimal(sum(value > 0 for value in returns))
            / Decimal(len(returns))
        ),
        total_fills=total_fills,
        commission_cost=commission,
    )


def _fold_from_dict(row: dict[str, Any]) -> WalkForwardFold:
    return WalkForwardFold(
        fold_number=int(row["fold_number"]),
        train_start=str(row["train_start"]),
        train_end=str(row["train_end"]),
        train_sessions=int(row["train_sessions"]),
        validation_start=str(row["validation_start"]),
        validation_end=str(row["validation_end"]),
        validation_sessions=int(row["validation_sessions"]),
        test_start=str(row["test_start"]),
        test_end=str(row["test_end"]),
        test_sessions=int(row["test_sessions"]),
        selected_scenario=str(row["selected_scenario"]),
        selected_parameter_hash=str(row["selected_parameter_hash"]),
        training_metrics=_metrics_from_dict(row["training_metrics"]),
        validation_metrics=_metrics_from_dict(
            row["validation_metrics"]
        ),
        validation_benchmark=_metrics_from_dict(
            row["validation_benchmark"]
        ),
        test_metrics=_metrics_from_dict(row["test_metrics"]),
        test_benchmark=_metrics_from_dict(row["test_benchmark"]),
        validation_passed=bool(row["validation_passed"]),
        test_excess_return=Decimal(str(row["test_excess_return"])),
        test_used_for_selection=bool(
            row["test_used_for_selection"]
        ),
    )


def _metrics_from_dict(row: dict[str, Any]) -> ValidationMetrics:
    return ValidationMetrics(
        session_count=int(row["session_count"]),
        compounded_return=Decimal(str(row["compounded_return"])),
        mean_session_return=Decimal(
            str(row["mean_session_return"])
        ),
        median_session_return=Decimal(
            str(row["median_session_return"])
        ),
        worst_session_return=Decimal(
            str(row["worst_session_return"])
        ),
        maximum_drawdown=Decimal(str(row["maximum_drawdown"])),
        profitable_session_fraction=Decimal(
            str(row["profitable_session_fraction"])
        ),
        total_fills=int(row["total_fills"]),
        commission_cost=Decimal(str(row["commission_cost"])),
    )


def _eastern_time(value: str):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(NEW_YORK).time().replace(tzinfo=None)


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


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value
