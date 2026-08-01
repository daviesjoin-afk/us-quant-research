from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from itertools import combinations
import json
from math import e, log, sqrt
import os
from pathlib import Path
from statistics import NormalDist, mean, median, stdev
import tempfile
from typing import Any
from uuid import uuid4

from us_quant.targeted_robustness import TargetedRobustnessResult


MINIMUM_OBSERVATIONS = 20
CSCV_PARTITIONS = 10
EULER_MASCHERONI = 0.5772156649015329
SOURCE_REFERENCES = (
    "Bailey et al., The Probability of Backtest Overfitting (2015)",
    "Bailey & Lopez de Prado, The Deflated Sharpe Ratio (2014)",
)


@dataclass(frozen=True, slots=True)
class TargetedOverfitResult:
    run_id: str
    robustness_run_id: str
    symbol: str
    strategy_version_id: str
    strategy_semver: str
    base_parameter_hash: str
    data_hash: str
    provider: str
    candidate_count: int
    observations_total: int
    observations_used: int
    excluded_tail: int
    cscv_partitions: int
    cscv_combinations: int
    pbo: Decimal | None
    probability_oos_loss: Decimal | None
    mean_is_selected_return: Decimal | None
    mean_oos_selected_return: Decimal | None
    average_performance_degradation: Decimal | None
    median_oos_rank: Decimal | None
    dsr_probability: Decimal | None
    dsr_selected_scenario: str | None
    observed_sharpe: Decimal | None
    deflated_sharpe_threshold: Decimal | None
    sample_skewness: Decimal | None
    sample_kurtosis: Decimal | None
    evidence_grade: str
    status: str
    limitations: tuple[str, ...]
    source_references: tuple[str, ...]


def run_targeted_overfit_diagnostics(
    robustness: TargetedRobustnessResult,
) -> TargetedOverfitResult:
    scenarios, dates, matrix = _candidate_matrix(robustness)
    total = len(dates)
    limitations = [
        "PBO 使用会话平均收益作为绩效度量，诊断固定候选集的选择偏差。",
        "DSR 使用独立同分布的会话收益近似，未做自相关修正。",
        "统计诊断只支持人工复核，不会晋级策略或提交订单。",
    ]
    used = total - total % CSCV_PARTITIONS
    excluded = total - used
    if excluded:
        limitations.append(
            f"为构造 {CSCV_PARTITIONS} 个等长 CSCV 分区，"
            f"末尾 {excluded} 个会话未参与 PBO。"
        )

    pbo = None
    probability_loss = None
    mean_is = None
    mean_oos = None
    degradation = None
    median_rank = None
    combination_count = 0
    if (
        total >= MINIMUM_OBSERVATIONS
        and len(scenarios) >= 2
        and used >= MINIMUM_OBSERVATIONS
    ):
        (
            pbo,
            probability_loss,
            mean_is,
            mean_oos,
            degradation,
            median_rank,
            combination_count,
        ) = _cscv_diagnostics(matrix[:used])
    else:
        limitations.append(
            "PBO 不可估计：至少需要 20 个同步完整会话和 2 个候选。"
        )

    (
        dsr,
        selected,
        observed_sharpe,
        threshold,
        skewness,
        kurtosis,
        dsr_limitation,
    ) = _deflated_sharpe(scenarios, matrix)
    if dsr_limitation:
        limitations.append(dsr_limitation)

    if pbo is None:
        grade = "样本不足·不可估计"
    elif pbo >= Decimal("0.5"):
        grade = "PBO高·过拟合风险"
    elif dsr is None:
        grade = "PBO可估·DSR不可估"
    elif dsr < Decimal("0.95"):
        grade = "多重检验未通过"
    else:
        grade = "统计诊断可人工复核"

    return TargetedOverfitResult(
        run_id=uuid4().hex,
        robustness_run_id=robustness.run_id,
        symbol=robustness.symbol,
        strategy_version_id=robustness.strategy_version_id,
        strategy_semver=robustness.strategy_semver,
        base_parameter_hash=robustness.base_parameter_hash,
        data_hash=robustness.data_hash,
        provider=robustness.provider,
        candidate_count=len(scenarios),
        observations_total=total,
        observations_used=used if pbo is not None else 0,
        excluded_tail=excluded if pbo is not None else total,
        cscv_partitions=CSCV_PARTITIONS,
        cscv_combinations=combination_count,
        pbo=pbo,
        probability_oos_loss=probability_loss,
        mean_is_selected_return=mean_is,
        mean_oos_selected_return=mean_oos,
        average_performance_degradation=degradation,
        median_oos_rank=median_rank,
        dsr_probability=dsr,
        dsr_selected_scenario=selected,
        observed_sharpe=observed_sharpe,
        deflated_sharpe_threshold=threshold,
        sample_skewness=skewness,
        sample_kurtosis=kurtosis,
        evidence_grade=grade,
        status="research_overfit_diagnostic",
        limitations=tuple(limitations),
        source_references=SOURCE_REFERENCES,
    )


def save_targeted_overfit(
    result: TargetedOverfitResult,
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


def load_targeted_overfits(
    output_root: str | Path,
    *,
    limit: int = 50,
) -> tuple[TargetedOverfitResult, ...]:
    root = Path(output_root)
    if not root.exists():
        return ()
    paths = sorted(
        root.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    results: list[TargetedOverfitResult] = []
    for path in paths:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            results.append(
                TargetedOverfitResult(
                    run_id=str(row["run_id"]),
                    robustness_run_id=str(row["robustness_run_id"]),
                    symbol=str(row["symbol"]),
                    strategy_version_id=str(row["strategy_version_id"]),
                    strategy_semver=str(row["strategy_semver"]),
                    base_parameter_hash=str(row["base_parameter_hash"]),
                    data_hash=str(row["data_hash"]),
                    provider=str(row["provider"]),
                    candidate_count=int(row["candidate_count"]),
                    observations_total=int(row["observations_total"]),
                    observations_used=int(row["observations_used"]),
                    excluded_tail=int(row["excluded_tail"]),
                    cscv_partitions=int(row["cscv_partitions"]),
                    cscv_combinations=int(row["cscv_combinations"]),
                    pbo=_optional_decimal(row.get("pbo")),
                    probability_oos_loss=_optional_decimal(
                        row.get("probability_oos_loss")
                    ),
                    mean_is_selected_return=_optional_decimal(
                        row.get("mean_is_selected_return")
                    ),
                    mean_oos_selected_return=_optional_decimal(
                        row.get("mean_oos_selected_return")
                    ),
                    average_performance_degradation=_optional_decimal(
                        row.get("average_performance_degradation")
                    ),
                    median_oos_rank=_optional_decimal(
                        row.get("median_oos_rank")
                    ),
                    dsr_probability=_optional_decimal(
                        row.get("dsr_probability")
                    ),
                    dsr_selected_scenario=row.get(
                        "dsr_selected_scenario"
                    ),
                    observed_sharpe=_optional_decimal(
                        row.get("observed_sharpe")
                    ),
                    deflated_sharpe_threshold=_optional_decimal(
                        row.get("deflated_sharpe_threshold")
                    ),
                    sample_skewness=_optional_decimal(
                        row.get("sample_skewness")
                    ),
                    sample_kurtosis=_optional_decimal(
                        row.get("sample_kurtosis")
                    ),
                    evidence_grade=str(row["evidence_grade"]),
                    status=str(row["status"]),
                    limitations=tuple(row.get("limitations", ())),
                    source_references=tuple(
                        row.get("source_references", ())
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


def _candidate_matrix(
    robustness: TargetedRobustnessResult,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[float, ...], ...]]:
    scenarios = tuple(
        summary.scenario for summary in robustness.scenario_summaries
    )
    outcomes = {
        (row.session_date, row.scenario): float(row.total_return)
        for row in robustness.session_outcomes
    }
    dates = tuple(
        sorted(
            date
            for date in {row.session_date for row in robustness.session_outcomes}
            if all((date, scenario) in outcomes for scenario in scenarios)
        )
    )
    matrix = tuple(
        tuple(outcomes[(date, scenario)] for scenario in scenarios)
        for date in dates
    )
    return scenarios, dates, matrix


def _cscv_diagnostics(
    matrix: tuple[tuple[float, ...], ...],
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal, int]:
    block_size = len(matrix) // CSCV_PARTITIONS
    blocks = tuple(
        tuple(range(index * block_size, (index + 1) * block_size))
        for index in range(CSCV_PARTITIONS)
    )
    logits: list[float] = []
    losses: list[bool] = []
    selected_is: list[float] = []
    selected_oos: list[float] = []
    oos_ranks: list[float] = []
    all_indices = set(range(CSCV_PARTITIONS))
    for selected_blocks in combinations(
        range(CSCV_PARTITIONS), CSCV_PARTITIONS // 2
    ):
        train_rows = tuple(
            row
            for block in selected_blocks
            for row in blocks[block]
        )
        test_rows = tuple(
            row
            for block in sorted(all_indices - set(selected_blocks))
            for row in blocks[block]
        )
        train_scores = _column_means(matrix, train_rows)
        test_scores = _column_means(matrix, test_rows)
        winner = max(
            range(len(train_scores)),
            key=lambda index: (train_scores[index], -index),
        )
        rank = _average_rank(test_scores, winner)
        omega = rank / (len(test_scores) + 1)
        logits.append(log(omega / (1 - omega)))
        losses.append(test_scores[winner] < 0)
        selected_is.append(train_scores[winner])
        selected_oos.append(test_scores[winner])
        oos_ranks.append(rank)
    count = len(logits)
    return (
        _decimal(sum(value <= 0 for value in logits) / count),
        _decimal(sum(losses) / count),
        _decimal(mean(selected_is)),
        _decimal(mean(selected_oos)),
        _decimal(mean(oos - ins for ins, oos in zip(selected_is, selected_oos))),
        _decimal(median(oos_ranks)),
        count,
    )


def _deflated_sharpe(
    scenarios: tuple[str, ...],
    matrix: tuple[tuple[float, ...], ...],
) -> tuple[
    Decimal | None,
    str | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    str | None,
]:
    if len(matrix) < MINIMUM_OBSERVATIONS or len(scenarios) < 2:
        return (None, None, None, None, None, None,
                "DSR 不可估计：至少需要 20 个同步会话和 2 个候选。")
    series = tuple(
        tuple(row[index] for row in matrix)
        for index in range(len(scenarios))
    )
    sharpe_values: list[tuple[int, float]] = []
    for index, values in enumerate(series):
        sigma = stdev(values)
        if sigma > 0:
            sharpe_values.append((index, mean(values) / sigma))
    if len(sharpe_values) < 2:
        return (None, None, None, None, None, None,
                "DSR 不可估计：候选会话收益方差不足。")
    sharpe_dispersion = stdev(value for _, value in sharpe_values)
    if sharpe_dispersion <= 0:
        return (None, None, None, None, None, None,
                "DSR 不可估计：候选 Sharpe 估计没有横截面方差。")
    selected_index, observed = max(
        sharpe_values, key=lambda item: (item[1], -item[0])
    )
    values = series[selected_index]
    sample_mean = mean(values)
    sigma = stdev(values)
    standardized = tuple(
        (value - sample_mean) / sigma for value in values
    )
    skewness = mean(value**3 for value in standardized)
    kurtosis = mean(value**4 for value in standardized)
    candidate_mean = mean(value for _, value in sharpe_values)
    candidate_count = len(sharpe_values)
    normal = NormalDist()
    expected_max_z = (
        (1 - EULER_MASCHERONI)
        * normal.inv_cdf(1 - 1 / candidate_count)
        + EULER_MASCHERONI
        * normal.inv_cdf(1 - 1 / (candidate_count * e))
    )
    threshold = candidate_mean + sharpe_dispersion * expected_max_z
    variance_adjustment = (
        1
        - skewness * observed
        + ((kurtosis - 1) / 4) * observed * observed
    )
    if variance_adjustment <= 0:
        return (
            None,
            scenarios[selected_index],
            _decimal(observed),
            _decimal(threshold),
            _decimal(skewness),
            _decimal(kurtosis),
            "DSR 不可估计：偏度/峰度调整后的方差项非正。",
        )
    probability = normal.cdf(
        (observed - threshold)
        * sqrt(len(values) - 1)
        / sqrt(variance_adjustment)
    )
    return (
        _decimal(probability),
        scenarios[selected_index],
        _decimal(observed),
        _decimal(threshold),
        _decimal(skewness),
        _decimal(kurtosis),
        None,
    )


def _column_means(
    matrix: tuple[tuple[float, ...], ...],
    rows: tuple[int, ...],
) -> tuple[float, ...]:
    return tuple(
        mean(matrix[row][column] for row in rows)
        for column in range(len(matrix[0]))
    )


def _average_rank(values: tuple[float, ...], target: int) -> float:
    value = values[target]
    lower = sum(candidate < value for candidate in values)
    equal = sum(candidate == value for candidate in values)
    return lower + (equal + 1) / 2


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


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
