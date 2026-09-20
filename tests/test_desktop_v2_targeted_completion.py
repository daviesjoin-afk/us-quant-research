"""Completion and normal-refresh selection tests for targeted evidence."""

from __future__ import annotations

import os
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.paths import STATE_ROOT_ENV
from us_quant.targeted_data_quality import TargetedDataQualityResult
from us_quant.targeted_execution_stress import TargetedExecutionStressResult
from us_quant.targeted_overfit import TargetedOverfitResult
from us_quant.targeted_review import (
    DependenceDiagnostic,
    EvidenceGate,
    TargetedReviewResult,
)
from us_quant.targeted_robustness import TargetedRobustnessResult


_APP = QApplication.instance() or QApplication([])


@pytest.fixture()
def window(monkeypatch, tmp_path):
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    widget = MainWindow()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


def _robustness(run_id: str, symbol: str) -> TargetedRobustnessResult:
    return TargetedRobustnessResult(
        run_id=run_id,
        symbol=symbol,
        strategy_version_id="version-1",
        strategy_semver="1.0.0",
        base_parameter_hash="base-hash",
        data_hash=f"data-{run_id}",
        provider="IBKR",
        coverages=("Type 1",),
        first_session="2026-09-01",
        last_session="2026-09-20",
        total_sessions=20,
        usable_sessions=20,
        skipped_sessions=(),
        minimum_required_minutes=300,
        scenario_summaries=(),
        session_outcomes=(),
        sign_stability_fraction=Decimal("1"),
        evidence_grade="B",
        review_ready=True,
        status="research_robustness",
    )


def _overfit(run_id: str, robustness_run_id: str, symbol: str) -> TargetedOverfitResult:
    return TargetedOverfitResult(
        run_id=run_id,
        robustness_run_id=robustness_run_id,
        symbol=symbol,
        strategy_version_id="version-1",
        strategy_semver="1.0.0",
        base_parameter_hash="base-hash",
        data_hash=f"data-{robustness_run_id}",
        provider="IBKR",
        candidate_count=5,
        observations_total=20,
        observations_used=20,
        excluded_tail=0,
        cscv_partitions=8,
        cscv_combinations=70,
        pbo=Decimal("0.25"),
        probability_oos_loss=Decimal("0.30"),
        mean_is_selected_return=None,
        mean_oos_selected_return=None,
        average_performance_degradation=Decimal("-0.005"),
        median_oos_rank=None,
        dsr_probability=Decimal("0.80"),
        dsr_selected_scenario="base",
        observed_sharpe=None,
        deflated_sharpe_threshold=None,
        sample_skewness=None,
        sample_kurtosis=None,
        evidence_grade="B",
        status="research_overfit",
        limitations=(),
        source_references=(),
    )


def _quality(run_id: str, robustness_run_id: str, symbol: str) -> TargetedDataQualityResult:
    return TargetedDataQualityResult(
        run_id=run_id,
        robustness_run_id=robustness_run_id,
        symbol=symbol,
        strategy_version_id="version-1",
        strategy_semver="1.0.0",
        data_hash=f"data-{robustness_run_id}",
        raw_data_hash=f"raw-{robustness_run_id}",
        provider="IBKR",
        evidence_origins=("captured_stream",),
        session_count=20,
        high_quality_sessions=20,
        minimum_completeness=Decimal("0.99"),
        median_completeness=Decimal("1"),
        maximum_consecutive_missing=0,
        stale_fraction=Decimal("0"),
        invalid_quote_rows=0,
        p95_source_age_seconds=Decimal("1"),
        size_coverage_fraction=Decimal("0.95"),
        sessions=(),
        evidence_grade="A",
        status="research_data_quality",
    )


def _stress(run_id: str, robustness_run_id: str, symbol: str) -> TargetedExecutionStressResult:
    return TargetedExecutionStressResult(
        run_id=run_id,
        robustness_run_id=robustness_run_id,
        symbol=symbol,
        strategy_version_id="version-1",
        strategy_semver="1.0.0",
        base_parameter_hash="base-hash",
        data_hash=f"data-{robustness_run_id}",
        provider="IBKR",
        scenarios=(),
        worst_stressed_return=Decimal("-0.02"),
        worst_performance_degradation=Decimal("-0.07"),
        size_observations=20,
        p95_top_of_book_participation=Decimal("0.10"),
        maximum_top_of_book_participation=Decimal("0.20"),
        capacity_status="可接受",
        stress_resilient=True,
        evidence_grade="B",
        limitations=(),
        status="research_execution_stress",
    )


def _review(
    run_id: str,
    robustness_run_id: str,
    symbol: str,
    overfit_run_id: str,
    quality_run_id: str,
    stress_run_id: str,
) -> TargetedReviewResult:
    gate = EvidenceGate(
        code="complete_sessions",
        name="完整会话",
        passed=True,
        observed="20",
        required="20",
        evidence="20/20",
        severity="hard",
    )
    dependence = DependenceDiagnostic(
        oos_session_count=20,
        lag1_autocorrelation=None,
        effective_sample_size_ar1=Decimal("10"),
        newey_west_lags=1,
        hac_mean_return=Decimal("0.001"),
        hac_standard_error=Decimal("0.0005"),
        probability_mean_positive=Decimal("0.80"),
        status="estimated",
    )
    return TargetedReviewResult(
        run_id=run_id,
        robustness_run_id=robustness_run_id,
        validation_run_id=None,
        overfit_run_id=overfit_run_id,
        data_quality_run_id=quality_run_id,
        execution_stress_run_id=stress_run_id,
        symbol=symbol,
        strategy_version_id="version-1",
        strategy_semver="1.0.0",
        base_parameter_hash="base-hash",
        data_hash=f"data-{robustness_run_id}",
        provider="IBKR",
        evidence_origins=("captured_stream",),
        dependence=dependence,
        gates=(gate,),
        passed_gates=1,
        blocking_failures=0,
        warnings=(),
        decision="人工评审",
        eligible_for_independent_review=True,
        status="research_review",
    )


def _pipeline(run_id: str, symbol: str):
    robustness = _robustness(run_id, symbol)
    overfit = _overfit(f"overfit-{run_id}", run_id, symbol)
    quality = _quality(f"quality-{run_id}", run_id, symbol)
    stress = _stress(f"stress-{run_id}", run_id, symbol)
    review = _review(
        f"review-{run_id}",
        run_id,
        symbol,
        overfit.run_id,
        quality.run_id,
        stress.run_id,
    )
    return robustness, None, overfit, quality, stress, review


def _capture_views(window: MainWindow, monkeypatch) -> list[object]:
    seen: list[object] = []
    monkeypatch.setattr(
        window.targeted_validation_page,
        "render",
        lambda view: seen.append(view),
    )
    monkeypatch.setattr(window.shell, "navigate_to", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_record_runtime_event", lambda **kwargs: None)
    monkeypatch.setattr(window, "_log", lambda *args, **kwargs: None)
    return seen


def test_completed_robustness_pipeline_selects_the_new_evidence(
    window: MainWindow, monkeypatch
) -> None:
    old_robustness, _, old_overfit, old_quality, old_stress, old_review = _pipeline(
        "old-robustness", "AAPL"
    )
    new_robustness, _, new_overfit, new_quality, new_stress, new_review = _pipeline(
        "new-robustness", "MSFT"
    )
    window.targeted_robustness_results = [old_robustness]
    window.targeted_overfit_results = [old_overfit]
    window.targeted_data_quality_results = [old_quality]
    window.targeted_execution_stress_results = [old_stress]
    window.targeted_review_results = [old_review]
    window._selected_robustness_run_id = old_robustness.run_id
    window._selected_review_run_id = old_review.run_id
    seen = _capture_views(window, monkeypatch)

    window._targeted_robustness_finished(
        (
            new_robustness,
            None,
            new_overfit,
            new_quality,
            new_stress,
            new_review,
        )
    )

    assert window._selected_robustness_run_id == new_robustness.run_id
    assert window._selected_review_run_id == new_review.run_id
    assert seen
    view = seen[-1]
    assert view.evidence.selected_robustness_run_id == new_robustness.run_id
    assert view.evidence.selected_review_run_id == new_review.run_id
    assert "MSFT" in view.evidence.robustness_summary
    assert "AAPL" not in view.evidence.robustness_summary
    assert "MSFT" in view.evidence.review_summary
    assert "AAPL" not in view.evidence.review_summary
    assert view.evidence.review_gate_rows
    assert view.active_workspace == 3
    assert view.active_evidence_tab == 6


def test_normal_targeted_refresh_preserves_a_historical_run_selection(
    window: MainWindow, monkeypatch
) -> None:
    old_robustness, _, old_overfit, old_quality, old_stress, old_review = _pipeline(
        "old-robustness", "AAPL"
    )
    new_robustness, _, new_overfit, new_quality, new_stress, new_review = _pipeline(
        "new-robustness", "MSFT"
    )
    window.targeted_robustness_results = [new_robustness, old_robustness]
    window.targeted_overfit_results = [new_overfit, old_overfit]
    window.targeted_data_quality_results = [new_quality, old_quality]
    window.targeted_execution_stress_results = [new_stress, old_stress]
    window.targeted_review_results = [new_review, old_review]
    window._selected_robustness_run_id = old_robustness.run_id
    window._selected_review_run_id = old_review.run_id
    seen = _capture_views(window, monkeypatch)

    window._publish_targeted_view()

    assert window._selected_robustness_run_id == old_robustness.run_id
    assert window._selected_review_run_id == old_review.run_id
    assert seen
    view = seen[-1]
    assert view.evidence.selected_robustness_run_id == old_robustness.run_id
    assert view.evidence.selected_review_run_id == old_review.run_id
    assert "AAPL" in view.evidence.robustness_summary
    assert "MSFT" not in view.evidence.robustness_summary
    assert view.active_workspace is None
    assert view.active_evidence_tab is None
