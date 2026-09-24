"""Completion and normal-refresh selection tests for targeted evidence."""

from __future__ import annotations

import os
from dataclasses import replace
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.desktop_targeted_evidence_models import (
    TargetedRobustnessBundle,
)
from us_quant.desktop_v2.orchestration.research.targeted.evidence import (
    models,
)
from us_quant.desktop_v2.pages.research import ResearchWorkspace
from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedEvidenceWorkspace,
    TargetedWorkspace,
)
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


def _bundle(run_id: str, symbol: str) -> TargetedRobustnessBundle:
    """The same six results, in the shape the service now returns them."""

    robustness, walk_forward, overfit, quality, stress, review = _pipeline(
        run_id, symbol
    )
    return TargetedRobustnessBundle(
        robustness=robustness,
        walk_forward=walk_forward,
        overfit=overfit,
        data_quality=quality,
        execution_stress=stress,
        review=review,
    )


def _capture_views(window: MainWindow, monkeypatch) -> list[object]:
    """Record every evidence paint, leaving the session paint alone.

    The page has two render entry points now, so this stubs exactly the one the
    evidence capability owns.  Stubbing the session paint too would hide the
    regression this file exists to catch: a normal session refresh must not
    repaint the evidence, and the way to see that is to keep counting.
    """

    seen: list[object] = []
    monkeypatch.setattr(
        window.targeted_validation_page,
        "render_evidence",
        lambda view: seen.append(view),
    )
    monkeypatch.setattr(window.shell, "navigate_to", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        window.runtime_events_orchestrator, "record", lambda **kwargs: None
    )
    monkeypatch.setattr(window, "_log", lambda *args, **kwargs: None)
    return seen


def _seed_history(
    window: MainWindow,
    bundle: TargetedRobustnessBundle,
    *,
    select: bool,
) -> None:
    """Put one completed suite into the canonical snapshot.

    The snapshot is the truth now, so a test seeds it through the capability
    rather than by assigning a window list -- there is no window list to assign.
    """

    orchestrator = window.targeted_evidence_orchestrator
    orchestrator._snapshot = models.commit_bundle(
        orchestrator.snapshot, bundle
    )
    if not select:
        # Startup semantics: evidence is present, nothing is selected yet.
        orchestrator._snapshot = replace(
            orchestrator.snapshot,
            selected_robustness_run_id=None,
            selected_review_run_id=None,
        )


def test_completed_robustness_pipeline_selects_the_new_evidence(
    window: MainWindow, monkeypatch
) -> None:
    old = _bundle("old-robustness", "AAPL")
    new = _bundle("new-robustness", "MSFT")
    _seed_history(window, old, select=True)
    seen = _capture_views(window, monkeypatch)

    window.targeted_evidence_orchestrator._robustness_finished(new)

    snapshot = window.targeted_evidence_orchestrator.snapshot
    assert snapshot.selected_robustness_run_id == new.robustness.run_id
    assert snapshot.selected_review_run_id == new.review.run_id
    assert seen
    view = seen[-1]
    assert view.selected_robustness_run_id == new.robustness.run_id
    assert view.selected_review_run_id == new.review.run_id
    assert "MSFT" in view.robustness_summary
    assert "AAPL" not in view.robustness_summary
    assert "MSFT" in view.review_summary
    assert "AAPL" not in view.review_summary
    assert view.review_gate_rows
    # The capability owns its own page's evidence workspace...
    assert (
        window.targeted_validation_page.workspace_tabs.currentIndex()
        == int(TargetedWorkspace.EVIDENCE)
    )
    assert (
        window.targeted_validation_page.evidence_panel.tabs.currentIndex()
        == int(TargetedEvidenceWorkspace.REVIEW)
    )
    # ...and the window owns the route, which it was asked for by signal.
    assert (
        window.research_page.active_workspace()
        is ResearchWorkspace.TARGETED
    )


def test_a_normal_session_refresh_preserves_the_evidence_and_its_selection(
    window: MainWindow, monkeypatch
) -> None:
    """A market tick, preflight refresh or minute update must not touch evidence.

    Before the render split, every one of those callers went through a single
    ``_publish_targeted_view`` that rebuilt all seven evidence tables.  This is
    the regression that split exists to prevent, so it asserts both halves: the
    evidence selection survives, and the evidence is not repainted at all.
    """

    old = _bundle("old-robustness", "AAPL")
    _seed_history(window, old, select=True)
    window.research_page.set_active_workspace(ResearchWorkspace.BACKTEST)
    evidence_paints: list[object] = []
    session_paints: list[object] = []
    monkeypatch.setattr(
        window.targeted_validation_page,
        "render_evidence",
        lambda view: evidence_paints.append(view),
    )
    monkeypatch.setattr(
        window.targeted_validation_page,
        "render_session",
        lambda view: session_paints.append(view),
    )

    window.targeted_session_orchestrator.render_current()

    assert session_paints, "the session half should have been painted"
    assert evidence_paints == [], "a session refresh must not repaint evidence"
    snapshot = window.targeted_evidence_orchestrator.snapshot
    assert snapshot.selected_robustness_run_id == old.robustness.run_id
    assert snapshot.selected_review_run_id == old.review.run_id
    assert (
        window.research_page.active_workspace()
        is ResearchWorkspace.BACKTEST
    )
