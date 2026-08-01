from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable

from us_quant.ibkr_stream import StreamSnapshot
from us_quant.portfolio_view import PortfolioView
from us_quant.runtime_events import RuntimeEvent
from us_quant.redaction import sanitize_value
from us_quant.shadow_paper import ShadowFill
from us_quant.strategy_registry import StrategyRecord
from us_quant.targeted_replay import TargetedReplayResult
from us_quant.targeted_robustness import TargetedRobustnessResult
from us_quant.targeted_validation import TargetedWalkForwardResult
from us_quant.targeted_overfit import TargetedOverfitResult
from us_quant.targeted_review import TargetedReviewResult
from us_quant.targeted_data_quality import TargetedDataQualityResult
from us_quant.targeted_execution_stress import (
    TargetedExecutionStressResult,
)


def export_terminal_bundle(
    export_root: Path,
    *,
    portfolio: PortfolioView | None,
    stream: StreamSnapshot | None,
    strategies: tuple[StrategyRecord, ...],
    events: tuple[RuntimeEvent, ...],
    shadow_fills: tuple[ShadowFill, ...] = (),
    targeted_replays: tuple[TargetedReplayResult, ...] = (),
    targeted_robustness: tuple[TargetedRobustnessResult, ...] = (),
    targeted_walk_forward: tuple[
        TargetedWalkForwardResult, ...
    ] = (),
    targeted_overfit: tuple[TargetedOverfitResult, ...] = (),
    targeted_data_quality: tuple[
        TargetedDataQualityResult, ...
    ] = (),
    targeted_execution_stress: tuple[
        TargetedExecutionStressResult, ...
    ] = (),
    targeted_review: tuple[TargetedReviewResult, ...] = (),
    paper_order_audit: tuple[dict, ...] = (),
    paper_execution_audit: tuple[dict, ...] = (),
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = export_root / f"terminal-export-{stamp}"
    target.mkdir(parents=True, exist_ok=False)

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "privacy": (
            "account identifiers are masked; no credentials or raw "
            "account IDs are exported"
        ),
        "environment_separation": [
            "paper account",
            "streaming market data",
            "research strategy registry",
        ],
        "files": [
            "account.json",
            "positions.csv",
            "quotes.csv",
            "strategies.csv",
            "runtime_events.csv",
            "shadow_fills.csv",
            "targeted_replays.csv",
            "targeted_robustness.csv",
            "targeted_walk_forward.csv",
            "targeted_overfit.csv",
            "targeted_data_quality.csv",
            "targeted_execution_stress.csv",
            "targeted_review.csv",
            "paper_orders.csv",
            "paper_executions.csv",
        ],
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    account_payload = _sanitize(
        asdict(portfolio.account) if portfolio is not None else None
    )
    (target / "account.json").write_text(
        json.dumps(
            account_payload,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    _write_csv(
        target / "positions.csv",
        [_sanitize(asdict(row)) for row in portfolio.positions]
        if portfolio is not None
        else [],
    )
    _write_csv(
        target / "quotes.csv",
        [_sanitize(asdict(row)) for row in stream.quotes]
        if stream is not None
        else [],
    )
    _write_csv(
        target / "strategies.csv",
        [
            _sanitize({
                **asdict(row),
                "parameters": json.dumps(
                    row.parameters,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            })
            for row in strategies
        ],
    )
    _write_csv(
        target / "runtime_events.csv",
        [_sanitize(asdict(row)) for row in events],
    )
    _write_csv(
        target / "shadow_fills.csv",
        [_sanitize(asdict(row)) for row in shadow_fills],
    )
    _write_csv(
        target / "targeted_replays.csv",
        [
            _sanitize(
                {
                    **{
                        key: value
                        for key, value in asdict(row).items()
                        if key != "fills"
                    },
                    "fill_count": len(row.fills),
                }
            )
            for row in targeted_replays
        ],
    )
    _write_csv(
        target / "targeted_robustness.csv",
        [
            _sanitize(
                {
                    "run_id": result.run_id,
                    "symbol": result.symbol,
                    "strategy_version_id": result.strategy_version_id,
                    "strategy_semver": result.strategy_semver,
                    "base_parameter_hash": (
                        result.base_parameter_hash
                    ),
                    "data_hash": result.data_hash,
                    "provider": result.provider,
                    "evidence_origins": list(
                        result.evidence_origins
                    ),
                    "first_session": result.first_session,
                    "last_session": result.last_session,
                    "total_sessions": result.total_sessions,
                    "usable_sessions": result.usable_sessions,
                    "skipped_session_count": len(
                        result.skipped_sessions
                    ),
                    "sign_stability_fraction": (
                        result.sign_stability_fraction
                    ),
                    "evidence_grade": result.evidence_grade,
                    "review_ready": result.review_ready,
                    "status": result.status,
                    "scenario": summary.scenario,
                    "scenario_parameter_hash": (
                        summary.parameter_hash
                    ),
                    "session_count": summary.session_count,
                    "compounded_return": summary.compounded_return,
                    "mean_session_return": (
                        summary.mean_session_return
                    ),
                    "median_session_return": (
                        summary.median_session_return
                    ),
                    "worst_session_return": (
                        summary.worst_session_return
                    ),
                    "profitable_session_fraction": (
                        summary.profitable_session_fraction
                    ),
                    "maximum_drawdown": summary.maximum_drawdown,
                    "total_fills": summary.total_fills,
                    "commission_cost": summary.commission_cost,
                    "orders_submitted": False,
                    "automatic_strategy_promotion": False,
                }
            )
            for result in targeted_robustness
            for summary in result.scenario_summaries
        ],
    )
    _write_csv(
        target / "targeted_walk_forward.csv",
        [
            _sanitize(
                {
                    "run_id": result.run_id,
                    "robustness_run_id": result.robustness_run_id,
                    "symbol": result.symbol,
                    "strategy_version_id": result.strategy_version_id,
                    "strategy_semver": result.strategy_semver,
                    "data_hash": result.data_hash,
                    "provider": result.provider,
                    "candidate_count": result.candidate_count,
                    "selection_rule": result.selection_rule,
                    "fold_number": fold.fold_number,
                    "train_start": fold.train_start,
                    "train_end": fold.train_end,
                    "train_sessions": fold.train_sessions,
                    "validation_start": fold.validation_start,
                    "validation_end": fold.validation_end,
                    "validation_sessions": (
                        fold.validation_sessions
                    ),
                    "test_start": fold.test_start,
                    "test_end": fold.test_end,
                    "test_sessions": fold.test_sessions,
                    "selected_scenario": fold.selected_scenario,
                    "selected_parameter_hash": (
                        fold.selected_parameter_hash
                    ),
                    "validation_return": (
                        fold.validation_metrics.compounded_return
                    ),
                    "validation_benchmark_return": (
                        fold.validation_benchmark.compounded_return
                    ),
                    "validation_passed": fold.validation_passed,
                    "test_return": (
                        fold.test_metrics.compounded_return
                    ),
                    "test_benchmark_return": (
                        fold.test_benchmark.compounded_return
                    ),
                    "test_excess_return": fold.test_excess_return,
                    "test_used_for_selection": (
                        fold.test_used_for_selection
                    ),
                    "evidence_grade": result.evidence_grade,
                    "review_ready": result.review_ready,
                    "orders_submitted": False,
                    "automatic_strategy_promotion": False,
                }
            )
            for result in targeted_walk_forward
            for fold in result.folds
        ],
    )
    _write_csv(
        target / "targeted_overfit.csv",
        [
            _sanitize(
                {
                    **asdict(result),
                    "limitations": list(result.limitations),
                    "source_references": list(
                        result.source_references
                    ),
                    "orders_submitted": False,
                    "automatic_strategy_promotion": False,
                }
            )
            for result in targeted_overfit
        ],
    )
    _write_csv(
        target / "targeted_data_quality.csv",
        [
            _sanitize(
                {
                    "run_id": result.run_id,
                    "robustness_run_id": result.robustness_run_id,
                    "symbol": result.symbol,
                    "strategy_version_id": (
                        result.strategy_version_id
                    ),
                    "strategy_semver": result.strategy_semver,
                    "data_hash": result.data_hash,
                    "raw_data_hash": result.raw_data_hash,
                    "provider": result.provider,
                    "evidence_origins": list(
                        result.evidence_origins
                    ),
                    "evidence_grade": result.evidence_grade,
                    "status": result.status,
                    **asdict(session),
                    "orders_submitted": False,
                    "automatic_strategy_promotion": False,
                }
            )
            for result in targeted_data_quality
            for session in result.sessions
        ],
    )
    _write_csv(
        target / "targeted_execution_stress.csv",
        [
            _sanitize(
                {
                    "run_id": result.run_id,
                    "robustness_run_id": result.robustness_run_id,
                    "symbol": result.symbol,
                    "strategy_version_id": (
                        result.strategy_version_id
                    ),
                    "strategy_semver": result.strategy_semver,
                    "base_parameter_hash": (
                        result.base_parameter_hash
                    ),
                    "data_hash": result.data_hash,
                    "provider": result.provider,
                    "worst_stressed_return": (
                        result.worst_stressed_return
                    ),
                    "worst_performance_degradation": (
                        result.worst_performance_degradation
                    ),
                    "size_observations": result.size_observations,
                    "p95_top_of_book_participation": (
                        result.p95_top_of_book_participation
                    ),
                    "maximum_top_of_book_participation": (
                        result.maximum_top_of_book_participation
                    ),
                    "capacity_status": result.capacity_status,
                    "stress_resilient": result.stress_resilient,
                    "evidence_grade": result.evidence_grade,
                    "limitations": list(result.limitations),
                    "status": result.status,
                    **asdict(scenario),
                    "orders_submitted": False,
                    "automatic_strategy_promotion": False,
                }
            )
            for result in targeted_execution_stress
            for scenario in result.scenarios
        ],
    )
    _write_csv(
        target / "targeted_review.csv",
        [
            _sanitize(
                {
                    "run_id": result.run_id,
                    "robustness_run_id": result.robustness_run_id,
                    "validation_run_id": result.validation_run_id,
                    "overfit_run_id": result.overfit_run_id,
                    "data_quality_run_id": (
                        result.data_quality_run_id
                    ),
                    "execution_stress_run_id": (
                        result.execution_stress_run_id
                    ),
                    "symbol": result.symbol,
                    "strategy_version_id": (
                        result.strategy_version_id
                    ),
                    "strategy_semver": result.strategy_semver,
                    "data_hash": result.data_hash,
                    "provider": result.provider,
                    "evidence_origins": list(
                        result.evidence_origins
                    ),
                    "oos_session_count": (
                        result.dependence.oos_session_count
                    ),
                    "lag1_autocorrelation": (
                        result.dependence.lag1_autocorrelation
                    ),
                    "effective_sample_size_ar1": (
                        result.dependence.effective_sample_size_ar1
                    ),
                    "newey_west_lags": (
                        result.dependence.newey_west_lags
                    ),
                    "probability_mean_positive": (
                        result.dependence.probability_mean_positive
                    ),
                    "gate_code": gate.code,
                    "gate_name": gate.name,
                    "gate_passed": gate.passed,
                    "gate_observed": gate.observed,
                    "gate_required": gate.required,
                    "gate_evidence": gate.evidence,
                    "decision": result.decision,
                    "eligible_for_independent_review": (
                        result.eligible_for_independent_review
                    ),
                    "manual_approval_recorded": False,
                    "automatic_strategy_promotion": False,
                    "orders_submitted": False,
                }
            )
            for result in targeted_review
            for gate in result.gates
        ],
    )
    _write_csv(
        target / "paper_orders.csv",
        [_sanitize(row) for row in paper_order_audit],
    )
    _write_csv(
        target / "paper_executions.csv",
        [_sanitize(row) for row in paper_execution_audit],
    )
    return target


def _write_csv(path: Path, rows: Iterable[dict]) -> None:
    materialized = list(rows)
    fields = sorted(
        {
            key
            for row in materialized
            for key in row
        }
    )
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        if not fields:
            file.write("")
            return
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in materialized:
            writer.writerow(
                {
                    key: (
                        json.dumps(
                            value,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        if isinstance(value, (dict, list, tuple))
                        else _csv_safe(value)
                    )
                    for key, value in row.items()
                }
            )


def _sanitize(value):  # type: ignore[no-untyped-def]
    return sanitize_value(value)


def _csv_safe(value):  # type: ignore[no-untyped-def]
    value = _sanitize(value)
    if isinstance(value, str) and value.startswith(
        ("=", "+", "-", "@")
    ):
        return "'" + value
    return value
