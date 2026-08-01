from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from math import floor, sqrt
import os
from pathlib import Path
from statistics import NormalDist, mean
import tempfile
from typing import Any
from uuid import uuid4

from us_quant.targeted_overfit import TargetedOverfitResult
from us_quant.targeted_data_quality import TargetedDataQualityResult
from us_quant.targeted_execution_stress import (
    TargetedExecutionStressResult,
)
from us_quant.targeted_robustness import TargetedRobustnessResult
from us_quant.targeted_validation import TargetedWalkForwardResult


MINIMUM_COMPLETE_SESSIONS = 25
MINIMUM_OOS_SESSIONS = 10
MAXIMUM_PBO = Decimal("0.50")
MINIMUM_DSR_PROBABILITY = Decimal("0.95")
MINIMUM_HAC_POSITIVE_PROBABILITY = Decimal("0.95")


@dataclass(frozen=True, slots=True)
class EvidenceGate:
    code: str
    name: str
    passed: bool
    observed: str
    required: str
    evidence: str
    severity: str = "blocking"


@dataclass(frozen=True, slots=True)
class DependenceDiagnostic:
    oos_session_count: int
    lag1_autocorrelation: Decimal | None
    effective_sample_size_ar1: Decimal | None
    newey_west_lags: int
    hac_mean_return: Decimal | None
    hac_standard_error: Decimal | None
    probability_mean_positive: Decimal | None
    status: str


@dataclass(frozen=True, slots=True)
class TargetedReviewResult:
    run_id: str
    robustness_run_id: str
    validation_run_id: str | None
    overfit_run_id: str
    data_quality_run_id: str | None
    execution_stress_run_id: str | None
    symbol: str
    strategy_version_id: str
    strategy_semver: str
    base_parameter_hash: str
    data_hash: str
    provider: str
    evidence_origins: tuple[str, ...]
    dependence: DependenceDiagnostic
    gates: tuple[EvidenceGate, ...]
    passed_gates: int
    blocking_failures: int
    warnings: tuple[str, ...]
    decision: str
    eligible_for_independent_review: bool
    status: str


def run_targeted_review(
    robustness: TargetedRobustnessResult,
    validation: TargetedWalkForwardResult | None,
    overfit: TargetedOverfitResult,
    *,
    parameters: dict[str, object],
    data_quality: TargetedDataQualityResult | None = None,
    execution_stress: TargetedExecutionStressResult | None = None,
) -> TargetedReviewResult:
    identity_match = (
        overfit.robustness_run_id == robustness.run_id
        and overfit.symbol == robustness.symbol
        and overfit.strategy_version_id
        == robustness.strategy_version_id
        and overfit.base_parameter_hash
        == robustness.base_parameter_hash
        and overfit.data_hash == robustness.data_hash
        and overfit.provider == robustness.provider
        and (
            validation is None
            or (
                validation.robustness_run_id == robustness.run_id
                and validation.symbol == robustness.symbol
                and validation.strategy_version_id
                == robustness.strategy_version_id
                and validation.base_parameter_hash
                == robustness.base_parameter_hash
                and validation.data_hash == robustness.data_hash
                and validation.provider == robustness.provider
            )
        )
    )
    quality_identity = bool(
        data_quality
        and data_quality.robustness_run_id == robustness.run_id
        and data_quality.symbol == robustness.symbol
        and data_quality.strategy_version_id
        == robustness.strategy_version_id
        and data_quality.data_hash == robustness.data_hash
        and data_quality.provider == robustness.provider
    )
    stress_identity = bool(
        execution_stress
        and execution_stress.robustness_run_id == robustness.run_id
        and execution_stress.symbol == robustness.symbol
        and execution_stress.strategy_version_id
        == robustness.strategy_version_id
        and execution_stress.base_parameter_hash
        == robustness.base_parameter_hash
        and execution_stress.data_hash == robustness.data_hash
        and execution_stress.provider == robustness.provider
    )
    oos_returns = _selected_oos_returns(robustness, validation)
    dependence = _dependence_diagnostic(oos_returns)
    validation_folds = len(validation.folds) if validation else 0
    validation_passed = (
        validation.validation_passed_folds if validation else 0
    )
    oos_return = (
        validation.out_of_sample_metrics.compounded_return
        if validation
        else None
    )
    oos_excess = (
        validation.out_of_sample_excess_return
        if validation
        else None
    )
    oos_fills = (
        validation.out_of_sample_metrics.total_fills
        if validation
        else 0
    )
    test_isolation = bool(
        validation
        and validation.folds
        and all(
            not fold.test_used_for_selection
            for fold in validation.folds
        )
    )
    whole_shares = parameters.get("whole_shares") is True
    commission = Decimal(
        str(parameters.get("commission_per_order", "0"))
    )
    slippage = Decimal(str(parameters.get("slippage_bps", "0")))

    gates = (
        _gate(
            "identity",
            "证据身份一致",
            identity_match,
            "一致" if identity_match else "不一致",
            "Run、标的、版本、参数、数据哈希与行情源完全一致",
            "稳健性、时间隔离和过拟合结果交叉核验",
        ),
        _gate(
            "captured_origin",
            "真实流采集来源",
            robustness.evidence_origins == ("captured_stream",),
            " / ".join(robustness.evidence_origins) or "缺失",
            "仅 captured_stream",
            "分钟证据来源字段，不凭行情源名称猜测",
        ),
        _gate(
            "complete_sessions",
            "完整独立会话",
            robustness.usable_sessions >= MINIMUM_COMPLETE_SESSIONS,
            str(robustness.usable_sessions),
            f"≥ {MINIMUM_COMPLETE_SESSIONS}",
            "纽约 10:00–15:45、≥300 行且连续预热",
        ),
        _gate(
            "quality_identity",
            "数据质量报告身份",
            quality_identity,
            "一致" if quality_identity else "缺失/不一致",
            "与稳健性 Run、标的、版本、数据哈希和行情源一致",
            "原始分钟质量报告交叉核验",
        ),
        _gate(
            "high_quality_sessions",
            "高质量完整会话",
            data_quality is not None
            and data_quality.high_quality_sessions
            >= MINIMUM_COMPLETE_SESSIONS,
            (
                str(data_quality.high_quality_sessions)
                if data_quality
                else "缺失"
            ),
            f"≥ {MINIMUM_COMPLETE_SESSIONS}",
            "完整率、连续缺口、报价合法性和行情年龄联合门",
        ),
        _gate(
            "minimum_completeness",
            "最差会话完整率",
            data_quality is not None
            and data_quality.minimum_completeness
            >= Decimal("0.98"),
            (
                _percent(data_quality.minimum_completeness)
                if data_quality
                else "缺失"
            ),
            "≥ 98%",
            "纽约 10:00–15:45 共 346 个预期分钟",
        ),
        _gate(
            "source_age",
            "行情年龄 P95",
            data_quality is not None
            and data_quality.p95_source_age_seconds is not None
            and data_quality.p95_source_age_seconds
            <= Decimal("5"),
            (
                f"{data_quality.p95_source_age_seconds:.2f}s"
                if data_quality
                and data_quality.p95_source_age_seconds is not None
                else "不可估计"
            ),
            "≤ 5 秒",
            "原始流快照记录的 bid/ask 最旧分量年龄",
        ),
        _gate(
            "walk_forward_folds",
            "时间隔离折数",
            validation_folds >= 2,
            str(validation_folds),
            "≥ 2",
            "锚定训练/验证/未触碰测试折",
        ),
        _gate(
            "test_isolation",
            "测试集未参与选择",
            test_isolation,
            "是" if test_isolation else "否/缺失",
            "全部折为否",
            "test_used_for_selection",
        ),
        _gate(
            "validation_gates",
            "验证门全部通过",
            validation_folds >= 2
            and validation_passed == validation_folds,
            f"{validation_passed}/{validation_folds}",
            "全部通过且至少 2 折",
            "验证策略收益同时为正且高于等风险基准",
        ),
        _gate(
            "oos_return",
            "未触碰测试收益",
            oos_return is not None and oos_return > 0,
            _percent(oos_return),
            "> 0%",
            "只汇总各折未参与选择的测试会话",
        ),
        _gate(
            "oos_excess",
            "未触碰测试超额",
            oos_excess is not None and oos_excess > 0,
            _percent(oos_excess),
            "> 0%",
            "相对同风险、整股、同成本日内基准",
        ),
        _gate(
            "pbo",
            "回测过拟合概率",
            overfit.pbo is not None and overfit.pbo < MAXIMUM_PBO,
            _percent(overfit.pbo),
            "< 50%",
            "10 分区 CSCV 固定候选集",
        ),
        _gate(
            "dsr",
            "多重检验修正",
            overfit.dsr_probability is not None
            and overfit.dsr_probability >= MINIMUM_DSR_PROBABILITY,
            _percent(overfit.dsr_probability),
            "≥ 95%",
            "DSR；不可估计按未通过处理",
        ),
        _gate(
            "dependence",
            "序列相关性可估计",
            dependence.lag1_autocorrelation is not None,
            _decimal_text(dependence.lag1_autocorrelation),
            "非零方差且可估计",
            "未触碰测试会话收益 lag-1 自相关",
        ),
        _gate(
            "effective_oos",
            "相关性折算样本量",
            dependence.effective_sample_size_ar1 is not None
            and dependence.effective_sample_size_ar1
            >= Decimal(MINIMUM_OOS_SESSIONS),
            _decimal_text(
                dependence.effective_sample_size_ar1, places=1
            ),
            f"≥ {MINIMUM_OOS_SESSIONS}",
            "AR(1) 近似；正相关会降低有效样本量",
        ),
        _gate(
            "hac_positive",
            "HAC均值为正置信度",
            dependence.probability_mean_positive is not None
            and dependence.probability_mean_positive
            >= MINIMUM_HAC_POSITIVE_PROBABILITY,
            _percent(dependence.probability_mean_positive),
            "≥ 95%",
            "Newey-West/Bartlett 长期方差下的均值为正概率",
        ),
        _gate(
            "execution_constraints",
            "整股与成本建模",
            whole_shares and commission > 0 and slippage > 0,
            (
                f"整股={whole_shares}，佣金={commission}，"
                f"滑点={slippage}bps"
            ),
            "整股=true，佣金>0，滑点>0",
            "不可变策略版本参数",
        ),
        _gate(
            "oos_execution",
            "样本外实际成交证据",
            validation_folds >= 2
            and oos_fills >= validation_folds * 2,
            str(oos_fills),
            "每折至少 1 次完整往返",
            "未触碰测试会话成交汇总",
        ),
        _gate(
            "stress_identity",
            "执行压力报告身份",
            stress_identity,
            "一致" if stress_identity else "缺失/不一致",
            "与稳健性 Run、标的、版本、参数、数据哈希和行情源一致",
            "执行成本压力结果交叉核验",
        ),
        _gate(
            "cost_stress",
            "高成本压力收益",
            execution_stress is not None
            and execution_stress.worst_stressed_return > 0,
            (
                _percent(execution_stress.worst_stressed_return)
                if execution_stress
                else "缺失"
            ),
            "> 0%",
            "至少 10bps 滑点并使用双倍佣金",
        ),
        _gate(
            "top_book_capacity",
            "最优价一档参与率 P95",
            execution_stress is not None
            and execution_stress.p95_top_of_book_participation
            is not None
            and execution_stress.p95_top_of_book_participation
            <= Decimal("0.10"),
            (
                _percent(
                    execution_stress.p95_top_of_book_participation
                )
                if execution_stress
                else "缺失"
            ),
            "≤ 10%",
            "整股订单量 / 当时对应买卖一档数量",
        ),
    )
    blocking_failures = sum(
        not gate.passed and gate.severity == "blocking"
        for gate in gates
    )
    warnings: list[str] = []
    rho = dependence.lag1_autocorrelation
    if rho is not None and abs(rho) >= Decimal("0.30"):
        warnings.append(
            f"未触碰测试收益 lag-1 自相关为 {rho:.3f}，"
            "IID 近似偏弱。"
        )
    if robustness.skipped_sessions:
        warnings.append(
            f"有 {len(robustness.skipped_sessions)} 个交易日因数据门被跳过。"
        )
    eligible = blocking_failures == 0
    return TargetedReviewResult(
        run_id=uuid4().hex,
        robustness_run_id=robustness.run_id,
        validation_run_id=validation.run_id if validation else None,
        overfit_run_id=overfit.run_id,
        data_quality_run_id=(
            data_quality.run_id if data_quality else None
        ),
        execution_stress_run_id=(
            execution_stress.run_id if execution_stress else None
        ),
        symbol=robustness.symbol,
        strategy_version_id=robustness.strategy_version_id,
        strategy_semver=robustness.strategy_semver,
        base_parameter_hash=robustness.base_parameter_hash,
        data_hash=robustness.data_hash,
        provider=robustness.provider,
        evidence_origins=robustness.evidence_origins,
        dependence=dependence,
        gates=gates,
        passed_gates=sum(gate.passed for gate in gates),
        blocking_failures=blocking_failures,
        warnings=tuple(warnings),
        decision=(
            "ELIGIBLE_FOR_INDEPENDENT_REVIEW"
            if eligible
            else "BLOCKED"
        ),
        eligible_for_independent_review=eligible,
        status="research_evidence_review",
    )


def save_targeted_review(
    result: TargetedReviewResult,
    output_root: str | Path,
) -> Path:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{result.run_id}.json"
    payload = _json_ready(asdict(result))
    payload.update(
        {
            "manual_approval_recorded": False,
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


def load_targeted_reviews(
    output_root: str | Path,
    *,
    limit: int = 50,
) -> tuple[TargetedReviewResult, ...]:
    root = Path(output_root)
    if not root.exists():
        return ()
    paths = sorted(
        root.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    results: list[TargetedReviewResult] = []
    for path in paths:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            dependence = row["dependence"]
            results.append(
                TargetedReviewResult(
                    run_id=str(row["run_id"]),
                    robustness_run_id=str(row["robustness_run_id"]),
                    validation_run_id=(
                        str(row["validation_run_id"])
                        if row.get("validation_run_id") is not None
                        else None
                    ),
                    overfit_run_id=str(row["overfit_run_id"]),
                    data_quality_run_id=(
                        str(row["data_quality_run_id"])
                        if row.get("data_quality_run_id") is not None
                        else None
                    ),
                    execution_stress_run_id=(
                        str(row["execution_stress_run_id"])
                        if row.get("execution_stress_run_id")
                        is not None
                        else None
                    ),
                    symbol=str(row["symbol"]),
                    strategy_version_id=str(row["strategy_version_id"]),
                    strategy_semver=str(row["strategy_semver"]),
                    base_parameter_hash=str(row["base_parameter_hash"]),
                    data_hash=str(row["data_hash"]),
                    provider=str(row["provider"]),
                    evidence_origins=tuple(
                        row.get("evidence_origins", ())
                    ),
                    dependence=DependenceDiagnostic(
                        oos_session_count=int(
                            dependence["oos_session_count"]
                        ),
                        lag1_autocorrelation=_optional_decimal(
                            dependence.get("lag1_autocorrelation")
                        ),
                        effective_sample_size_ar1=_optional_decimal(
                            dependence.get(
                                "effective_sample_size_ar1"
                            )
                        ),
                        newey_west_lags=int(
                            dependence["newey_west_lags"]
                        ),
                        hac_mean_return=_optional_decimal(
                            dependence.get("hac_mean_return")
                        ),
                        hac_standard_error=_optional_decimal(
                            dependence.get("hac_standard_error")
                        ),
                        probability_mean_positive=_optional_decimal(
                            dependence.get(
                                "probability_mean_positive"
                            )
                        ),
                        status=str(dependence["status"]),
                    ),
                    gates=tuple(
                        EvidenceGate(
                            code=str(gate["code"]),
                            name=str(gate["name"]),
                            passed=bool(gate["passed"]),
                            observed=str(gate["observed"]),
                            required=str(gate["required"]),
                            evidence=str(gate["evidence"]),
                            severity=str(
                                gate.get("severity", "blocking")
                            ),
                        )
                        for gate in row["gates"]
                    ),
                    passed_gates=int(row["passed_gates"]),
                    blocking_failures=int(row["blocking_failures"]),
                    warnings=tuple(row.get("warnings", ())),
                    decision=str(row["decision"]),
                    eligible_for_independent_review=bool(
                        row["eligible_for_independent_review"]
                    ),
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


def _selected_oos_returns(
    robustness: TargetedRobustnessResult,
    validation: TargetedWalkForwardResult | None,
) -> tuple[float, ...]:
    if validation is None:
        return ()
    outcomes = {
        (row.scenario, row.session_date): row
        for row in robustness.session_outcomes
    }
    values: list[tuple[str, float]] = []
    for fold in validation.folds:
        dates = sorted(
            {
                row.session_date
                for row in robustness.session_outcomes
                if fold.test_start
                <= row.session_date
                <= fold.test_end
            }
        )
        values.extend(
            (
                date,
                float(
                    outcomes[
                        (fold.selected_scenario, date)
                    ].total_return
                ),
            )
            for date in dates
            if (fold.selected_scenario, date) in outcomes
        )
    deduplicated = dict(values)
    return tuple(
        deduplicated[date] for date in sorted(deduplicated)
    )


def _dependence_diagnostic(
    returns: tuple[float, ...],
) -> DependenceDiagnostic:
    count = len(returns)
    if count < 3:
        return DependenceDiagnostic(
            oos_session_count=count,
            lag1_autocorrelation=None,
            effective_sample_size_ar1=None,
            newey_west_lags=0,
            hac_mean_return=None,
            hac_standard_error=None,
            probability_mean_positive=None,
            status="样本不足·不可估计",
        )
    average = mean(returns)
    centered = tuple(value - average for value in returns)
    denominator = sum(value * value for value in centered)
    if denominator <= 0:
        return DependenceDiagnostic(
            oos_session_count=count,
            lag1_autocorrelation=None,
            effective_sample_size_ar1=None,
            newey_west_lags=0,
            hac_mean_return=Decimal(str(average)),
            hac_standard_error=None,
            probability_mean_positive=None,
            status="零方差·不可估计",
        )
    rho = sum(
        centered[index] * centered[index - 1]
        for index in range(1, count)
    ) / denominator
    rho = max(-0.999, min(0.999, rho))
    effective = count * (1 - rho) / (1 + rho)
    effective = max(1.0, min(float(count), effective))
    lags = max(
        1,
        min(
            count - 1,
            floor(4 * (count / 100) ** (2 / 9)),
        ),
    )
    gamma_zero = denominator / count
    long_run_variance = gamma_zero
    for lag in range(1, lags + 1):
        covariance = sum(
            centered[index] * centered[index - lag]
            for index in range(lag, count)
        ) / count
        weight = 1 - lag / (lags + 1)
        long_run_variance += 2 * weight * covariance
    if long_run_variance <= 0:
        standard_error = None
        probability = None
        status = "HAC方差非正·不可估计"
    else:
        standard_error = sqrt(long_run_variance / count)
        if standard_error <= 0:
            probability = None
            status = "HAC标准误为零·不可估计"
        else:
            probability = NormalDist().cdf(average / standard_error)
            status = "可估计"
    return DependenceDiagnostic(
        oos_session_count=count,
        lag1_autocorrelation=Decimal(str(rho)),
        effective_sample_size_ar1=Decimal(str(effective)),
        newey_west_lags=lags,
        hac_mean_return=Decimal(str(average)),
        hac_standard_error=(
            Decimal(str(standard_error))
            if standard_error is not None
            else None
        ),
        probability_mean_positive=(
            Decimal(str(probability))
            if probability is not None
            else None
        ),
        status=status,
    )


def _gate(
    code: str,
    name: str,
    passed: bool,
    observed: str,
    required: str,
    evidence: str,
) -> EvidenceGate:
    return EvidenceGate(
        code=code,
        name=name,
        passed=passed,
        observed=observed,
        required=required,
        evidence=evidence,
    )


def _percent(value: Decimal | None) -> str:
    return f"{value:.2%}" if value is not None else "不可估计"


def _decimal_text(
    value: Decimal | None,
    *,
    places: int = 3,
) -> str:
    return f"{value:.{places}f}" if value is not None else "不可估计"


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
