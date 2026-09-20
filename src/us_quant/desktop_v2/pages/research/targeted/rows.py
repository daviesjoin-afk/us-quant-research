"""Pure evidence-row projections for the targeted validation workspace.

Only immutable research results become presentation strings here.  Shadow and
preflight session facts are projected by ``session_presenter``; this module is
kept under the evidence line cap and imports no Qt.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from us_quant.desktop_v2.pages.research.targeted.models import (
    DataQualityRow,
    ExecutionStressRow,
    OverfitRow,
    ReplayRow,
    ReviewGateRow,
    ReviewHistoryRow,
    RobustnessRunRow,
    RobustnessScenarioRow,
    TargetedRowTone,
    WalkForwardRow,
)
from us_quant.targeted_data_quality import TargetedDataQualityResult
from us_quant.targeted_execution_stress import TargetedExecutionStressResult
from us_quant.targeted_overfit import TargetedOverfitResult
from us_quant.targeted_replay import TargetedReplayResult
from us_quant.targeted_review import TargetedReviewResult
from us_quant.targeted_robustness import TargetedRobustnessResult
from us_quant.targeted_validation import TargetedWalkForwardResult


def money(value: Decimal | None, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    return f"{'+' if signed and value > 0 else ''}${value:,.2f}"


def price(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def ratio(value: Decimal | None, *, signed: bool = False, places: int = 1) -> str:
    if value is None:
        return "不可估计"
    return f"{value:+.{places}%}" if signed else f"{value:.{places}%}"


def replay_rows(results: Sequence[TargetedReplayResult]) -> tuple[ReplayRow, ...]:
    return tuple(
        ReplayRow(
            key=result.run_id,
            values=(
                result.run_id[:8], result.symbol, result.strategy_semver,
                " / ".join(result.providers),
                f"{result.first_minute[:16]} → {result.last_minute[:16]}",
                str(result.row_count), str(result.gap_count),
                f"{result.total_return:+.2%}",
                f"{result.maximum_drawdown:.2%}",
                money(result.realized_pnl, signed=True),
                str(len(result.fills)), money(result.commission_cost),
            ),
        )
        for result in results
    )


def robustness_rows(
    results: Sequence[TargetedRobustnessResult],
) -> tuple[RobustnessRunRow, ...]:
    return tuple(
        RobustnessRunRow(
            key=result.run_id,
            values=(
                result.run_id[:8], result.symbol, result.strategy_semver,
                result.provider,
                f"{result.first_session} → {result.last_session}",
                f"{result.usable_sessions}/{result.total_sessions}",
                f"{result.sign_stability_fraction:.0%}", result.evidence_grade,
            ),
        )
        for result in results
    )


def robustness_scenario_rows(
    result: TargetedRobustnessResult | None,
) -> tuple[RobustnessScenarioRow, ...]:
    summaries = result.scenario_summaries if result else ()
    return tuple(
        RobustnessScenarioRow(
            key=summary.scenario,
            values=(
                summary.scenario, str(summary.session_count),
                f"{summary.compounded_return:+.2%}",
                f"{summary.mean_session_return:+.2%}",
                f"{summary.median_session_return:+.2%}",
                f"{summary.worst_session_return:+.2%}",
                f"{summary.profitable_session_fraction:.0%}",
                f"{summary.maximum_drawdown:.2%}", str(summary.total_fills),
                money(summary.commission_cost),
            ),
        )
        for summary in summaries
    )


def walk_forward_rows(
    results: Sequence[TargetedWalkForwardResult],
) -> tuple[WalkForwardRow, ...]:
    rows: list[WalkForwardRow] = []
    for result in results:
        for fold in result.folds:
            rows.append(
                WalkForwardRow(
                    key=f"{result.run_id}:{fold.fold_number}",
                    values=(
                        result.run_id[:8], str(fold.fold_number),
                        fold.selected_scenario,
                        f"{fold.train_start} → {fold.train_end}",
                        f"{fold.validation_start} → {fold.validation_end}",
                        f"{fold.validation_metrics.compounded_return:+.2%}",
                        f"{fold.validation_benchmark.compounded_return:+.2%}",
                        "通过" if fold.validation_passed else "未通过",
                        f"{fold.test_start} → {fold.test_end}",
                        f"{fold.test_metrics.compounded_return:+.2%}",
                        f"{fold.test_benchmark.compounded_return:+.2%}",
                        f"{fold.test_excess_return:+.2%}",
                    ),
                    tone=(
                        TargetedRowTone.NEUTRAL if fold.validation_passed
                        else TargetedRowTone.WARNING
                    ),
                )
            )
    return tuple(rows)


def overfit_rows(
    results: Sequence[TargetedOverfitResult],
) -> tuple[OverfitRow, ...]:
    rows: list[OverfitRow] = []
    for result in results:
        values = (
            result.run_id[:8], result.symbol,
            f"{result.observations_used}/{result.observations_total}",
            str(result.candidate_count), str(result.cscv_partitions),
            str(result.cscv_combinations), ratio(result.pbo),
            ratio(result.probability_oos_loss),
            ratio(result.average_performance_degradation, signed=True, places=3),
            ratio(result.dsr_probability),
            result.dsr_selected_scenario or "—", result.evidence_grade,
        )
        rows.append(
            OverfitRow(
                key=result.run_id,
                values=values,
                tone=(
                    TargetedRowTone.WARNING
                    if any(any(marker in values[column] for marker in ("不可", "风险", "未通过")) for column in (6, 7, 9, 11))
                    else TargetedRowTone.NEUTRAL
                ),
            )
        )
    return tuple(rows)


def data_quality_rows(
    results: Sequence[TargetedDataQualityResult],
) -> tuple[DataQualityRow, ...]:
    latest = results[0] if results else None
    sessions = latest.sessions if latest else ()
    return tuple(
        DataQualityRow(
            key=session.session_date,
            values=(
                session.session_date, str(session.raw_rows), str(session.usable_rows),
                f"{session.completeness:.1%}", str(session.missing_minutes),
                str(session.maximum_consecutive_missing), str(session.stale_rows),
                str(session.invalid_quote_rows),
                f"{session.p95_source_age_seconds:.2f}s"
                if session.p95_source_age_seconds is not None else "不可估计",
                f"{session.size_coverage_fraction:.1%}",
                "通过" if session.high_quality else "阻断",
            ),
            tone=(
                TargetedRowTone.NEUTRAL if session.high_quality
                else TargetedRowTone.WARNING
            ),
            tooltip="；".join(session.failure_reasons) or None,
        )
        for session in sessions
    )


def execution_stress_rows(
    results: Sequence[TargetedExecutionStressResult],
) -> tuple[ExecutionStressRow, ...]:
    latest = results[0] if results else None
    scenarios = latest.scenarios if latest else ()
    return tuple(
        ExecutionStressRow(
            key=scenario.scenario,
            values=(
                scenario.scenario, f"{scenario.slippage_bps}bps",
                money(scenario.commission_per_order), str(scenario.session_count),
                f"{scenario.compounded_return:+.2%}",
                f"{scenario.degradation_vs_configured:+.2%}",
                f"{scenario.maximum_drawdown:.2%}", str(scenario.total_fills),
                money(scenario.commission_cost),
            ),
            tone=(
                TargetedRowTone.WARNING if scenario.compounded_return <= 0
                else TargetedRowTone.NEUTRAL
            ),
        )
        for scenario in scenarios
    )


def review_history_rows(
    results: Sequence[TargetedReviewResult],
) -> tuple[ReviewHistoryRow, ...]:
    rows: list[ReviewHistoryRow] = []
    for result in results:
        dependence = result.dependence
        values = (
            result.run_id[:8], result.symbol, result.provider,
            " / ".join(result.evidence_origins) or "缺失",
            next(
                (gate.observed for gate in result.gates
                 if gate.code == "complete_sessions"),
                "—",
            ),
            str(dependence.oos_session_count),
            f"{dependence.effective_sample_size_ar1:.1f}"
            if dependence.effective_sample_size_ar1 is not None else "不可估计",
            ratio(dependence.probability_mean_positive),
            f"{result.passed_gates}/{len(result.gates)}",
            "可进入人工独立评审" if result.eligible_for_independent_review
            else f"阻断 {result.blocking_failures}",
        )
        rows.append(
            ReviewHistoryRow(
                key=result.run_id,
                values=values,
                tone=(
                    TargetedRowTone.WARNING
                    if "不可" in values[7] or "阻断" in values[9]
                    else TargetedRowTone.NEUTRAL
                ),
            )
        )
    return tuple(rows)


def review_gate_rows(
    result: TargetedReviewResult | None,
) -> tuple[ReviewGateRow, ...]:
    gates = result.gates if result else ()
    return tuple(
        ReviewGateRow(
            key=gate.code,
            values=(
                gate.name, "通过" if gate.passed else "阻断", gate.observed,
                gate.required, gate.evidence, gate.severity, gate.code,
            ),
            tone=(
                TargetedRowTone.NEUTRAL if gate.passed
                else TargetedRowTone.WARNING
            ),
        )
        for gate in gates
    )


__all__ = [
    "data_quality_rows", "execution_stress_rows", "money", "overfit_rows",
    "price", "ratio", "replay_rows", "review_gate_rows",
    "review_history_rows", "robustness_rows", "robustness_scenario_rows",
    "walk_forward_rows",
]
