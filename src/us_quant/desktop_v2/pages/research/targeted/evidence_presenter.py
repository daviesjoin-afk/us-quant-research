"""Evidence-side projection for the targeted validation page.

The result caches stay on the window.  This module only turns the already-loaded
immutable results into summaries and table rows; it does not run a study, select
a strategy or touch a widget.
"""

from __future__ import annotations

from typing import Sequence

from us_quant.desktop_v2.pages.research.targeted import rows
from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedEvidenceView,
)
from us_quant.targeted_data_quality import TargetedDataQualityResult
from us_quant.targeted_execution_stress import TargetedExecutionStressResult
from us_quant.targeted_overfit import TargetedOverfitResult
from us_quant.targeted_replay import TargetedReplayResult
from us_quant.targeted_review import TargetedReviewResult
from us_quant.targeted_robustness import TargetedRobustnessResult
from us_quant.targeted_validation import TargetedWalkForwardResult


def _robustness_summary(results: Sequence[TargetedRobustnessResult]) -> str:
    if not results:
        return "尚未运行多日稳健性评估；结果不会自动晋级策略。"
    result = results[0]
    readiness = (
        "达到参数稳健性门；仍需时间隔离验证"
        if result.review_ready
        else "未达到参数稳健性门"
    )
    return (
        f"{result.symbol} · {result.strategy_semver} · {result.provider} · "
        f"有效会话 {result.usable_sessions}/{result.total_sessions}，"
        f"跳过 {len(result.skipped_sessions)} · 参数收益方向一致 "
        f"{result.sign_stability_fraction:.0%}（不代表盈利）· "
        f"{result.evidence_grade} · {readiness}"
    )


def _walk_forward_summary(results: Sequence[TargetedWalkForwardResult]) -> str:
    if not results:
        return (
            "时间隔离验证至少需要 20 个完整有效会话；测试集永不参与参数选择。"
        )
    latest = results[0]
    return (
        f"{latest.symbol} · {latest.strategy_semver} · {len(latest.folds)} 折 · "
        f"验证通过 {latest.validation_passed_folds}/{len(latest.folds)} · "
        f"未触碰测试集策略 "
        f"{latest.out_of_sample_metrics.compounded_return:+.2%}，"
        f"等风险基准 {latest.out_of_sample_benchmark.compounded_return:+.2%}，"
        f"超额 {latest.out_of_sample_excess_return:+.2%} · "
        f"{latest.evidence_grade} · 不自动晋级"
    )


def _overfit_summary(results: Sequence[TargetedOverfitResult]) -> str:
    if not results:
        return (
            "PBO/CSCV 与 DSR 至少需要 20 个同步完整会话；"
            "统计条件不足时明确显示不可估计。"
        )
    latest = results[0]
    pbo = rows.ratio(latest.pbo)
    dsr = rows.ratio(latest.dsr_probability)
    return (
        f"{latest.symbol} · 固定候选 {latest.candidate_count} · "
        f"同步会话 {latest.observations_used}/{latest.observations_total} · "
        f"CSCV {latest.cscv_partitions} 分区/{latest.cscv_combinations} 组合 · "
        f"PBO {pbo} · DSR {dsr} · {latest.evidence_grade}。"
        "该页仅诊断研究选择偏差，不构成策略批准。"
    )


def _quality_summary(results: Sequence[TargetedDataQualityResult]) -> str:
    if not results:
        return (
            "数据质量报告检查每个会话的 346 个预期分钟、连续缺口、"
            "异常报价、行情年龄和一档数量覆盖。"
        )
    latest = results[0]
    age = (
        f"{latest.p95_source_age_seconds:.2f}s"
        if latest.p95_source_age_seconds is not None
        else "不可估计"
    )
    return (
        f"{latest.symbol} · 高质量会话 "
        f"{latest.high_quality_sessions}/{latest.session_count} · "
        f"最差完整率 {latest.minimum_completeness:.1%} · "
        f"最长连续缺口 {latest.maximum_consecutive_missing} 分钟 · "
        f"行情年龄 P95 {age} · "
        f"一档数量覆盖 {latest.size_coverage_fraction:.1%} · "
        f"{latest.evidence_grade}"
    )


def _stress_summary(results: Sequence[TargetedExecutionStressResult]) -> str:
    if not results:
        return (
            "执行压力测试将配置成本与 5bps、10bps+双倍佣金场景对比，"
            "并检查最优价一档参与率。"
        )
    latest = results[0]
    participation = (
        f"{latest.p95_top_of_book_participation:.1%}"
        if latest.p95_top_of_book_participation is not None
        else "不可估计"
    )
    return (
        f"{latest.symbol} · 最差压力收益 {latest.worst_stressed_return:+.2%} · "
        f"最差相对退化 {latest.worst_performance_degradation:+.2%} · "
        f"一档参与率 P95 {participation} · "
        f"{latest.capacity_status} · {latest.evidence_grade}"
    )


def _review_summary(result: TargetedReviewResult | None) -> str:
    if result is None:
        return (
            "独立评审汇总真实流来源、时间隔离、过拟合、序列相关性、"
            "成本与成交硬门；不会自动批准策略。"
        )
    dependence = result.dependence
    hac = rows.ratio(dependence.probability_mean_positive)
    effective = (
        f"{dependence.effective_sample_size_ar1:.1f}"
        if dependence.effective_sample_size_ar1 is not None
        else "不可估计"
    )
    return (
        f"{result.symbol} · 通过 {result.passed_gates}/{len(result.gates)} · "
        f"阻断 {result.blocking_failures} · 测试会话 "
        f"{dependence.oos_session_count} · 相关性折算样本 {effective} · "
        f"HAC均值为正 {hac} · "
        + (
            "仅可进入人工独立评审；尚未批准。"
            if result.eligible_for_independent_review
            else "证据门未满足，不得晋级。"
        )
    )


def evidence_view(
    *,
    replay_results: Sequence[TargetedReplayResult] = (),
    robustness_results: Sequence[TargetedRobustnessResult] = (),
    walk_forward_results: Sequence[TargetedWalkForwardResult] = (),
    overfit_results: Sequence[TargetedOverfitResult] = (),
    data_quality_results: Sequence[TargetedDataQualityResult] = (),
    execution_stress_results: Sequence[TargetedExecutionStressResult] = (),
    review_results: Sequence[TargetedReviewResult] = (),
    selected_robustness_run_id: str | None = None,
    selected_review_run_id: str | None = None,
) -> TargetedEvidenceView:
    selected_robustness = next(
        (
            result for result in robustness_results
            if result.run_id == selected_robustness_run_id
        ),
        robustness_results[0] if robustness_results else None,
    )
    selected_review = next(
        (
            result for result in review_results
            if result.run_id == selected_review_run_id
        ),
        review_results[0] if review_results else None,
    )
    return TargetedEvidenceView(
        replay_rows=rows.replay_rows(replay_results),
        robustness_summary=_robustness_summary(robustness_results),
        robustness_rows=rows.robustness_rows(robustness_results),
        robustness_scenario_rows=rows.robustness_scenario_rows(
            selected_robustness
        ),
        walk_forward_summary=_walk_forward_summary(walk_forward_results),
        walk_forward_rows=rows.walk_forward_rows(walk_forward_results),
        overfit_summary=_overfit_summary(overfit_results),
        overfit_rows=rows.overfit_rows(overfit_results),
        quality_summary=_quality_summary(data_quality_results),
        quality_rows=rows.data_quality_rows(data_quality_results),
        stress_summary=_stress_summary(execution_stress_results),
        stress_rows=rows.execution_stress_rows(execution_stress_results),
        review_summary=_review_summary(selected_review),
        review_history_rows=rows.review_history_rows(review_results),
        review_gate_rows=rows.review_gate_rows(selected_review),
        selected_robustness_run_id=(
            selected_robustness.run_id if selected_robustness else None
        ),
        selected_review_run_id=selected_review.run_id if selected_review else None,
    )


__all__ = ["evidence_view"]
