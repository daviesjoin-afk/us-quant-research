"""Wiring and characterization tests for the targeted validation page."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from us_quant import desktop
from us_quant.desktop import MainWindow
from us_quant.desktop_targeted_evidence_models import (
    TargetedRobustnessBundle,
)
from us_quant.desktop_v2.pages.research import ResearchWorkspace
from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedControlView,
    TargetedEvidenceWorkspace,
    TargetedWorkspace,
)
from us_quant.paths import STATE_ROOT_ENV
from us_quant.universe import UniverseRecord, UniverseSnapshot
from us_quant.trading.domain.strategy import StrategyStatus


_APP = QApplication.instance() or QApplication([])


@pytest.fixture()
def dialogs(monkeypatch) -> list[tuple[str, tuple]]:
    seen: list[tuple[str, tuple]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: seen.append(("warning", args)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: seen.append(("information", args)),
    )
    return seen


@pytest.fixture()
def window(monkeypatch, tmp_path, dialogs):
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    widget = MainWindow()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


def _valid_strategy() -> SimpleNamespace:
    return SimpleNamespace(
        strategy_id="intraday-targeted-t",
        status=StrategyStatus.RESEARCH,
        gate_passed=True,
        parameters={},
        version_id="targeted-v1",
        parameter_hash="hash-v1",
        semver="1.0.0",
    )


def _eligible_universe() -> UniverseSnapshot:
    """A real snapshot: adoption renders, so the presenter reads every field."""

    return UniverseSnapshot(
        generated_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
        source_timestamps={"test": "now"},
        records=(
            UniverseRecord(
                symbol="AAPL",
                name="Apple",
                exchange="NASDAQ",
                security_type="STK",
                eligible_for_research=True,
            ),
        ),
    )


def _ready_quote() -> SimpleNamespace:
    return SimpleNamespace(symbol="AAPL", realtime_ready=True)


def _ready_stream(quote: SimpleNamespace | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        realtime_ready=True,
        quotes=(quote or _ready_quote(),),
    )


class _RunningWorker:
    def isRunning(self) -> bool:
        return True


def _fake_live_market(window: MainWindow, stream: SimpleNamespace) -> None:
    """Make the market orchestrator report a live feed carrying ``stream``.

    The shadow gate reads the market truth through the orchestrator now, so the
    test supplies it there rather than by assigning a worker to the window.
    """

    orchestrator = window.market_orchestrator
    orchestrator._worker = _RunningWorker()
    orchestrator._snapshot = stream


class _Workflow:
    def __init__(self) -> None:
        self.active = False

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False


class _Engine:
    created: list["_Engine"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.active = False
        _Engine.created.append(self)

    def start(self):
        self.active = True
        return SimpleNamespace(active=True)

    def stop(self):
        self.active = False
        return SimpleNamespace(active=False)


def _bundle(run_id: str, symbol: str) -> TargetedRobustnessBundle:
    """One complete suite, shaped as the service returns it.

    Built here rather than shared with the completion file on purpose: the two
    files assert different things about the same shape, and a shared fixture
    would let a change that breaks one of them silently satisfy both.
    """

    from us_quant.targeted_data_quality import TargetedDataQualityResult
    from us_quant.targeted_execution_stress import (
        TargetedExecutionStressResult,
    )
    from us_quant.targeted_overfit import TargetedOverfitResult
    from us_quant.targeted_review import (
        DependenceDiagnostic,
        EvidenceGate,
        TargetedReviewResult,
    )
    from us_quant.targeted_robustness import TargetedRobustnessResult

    robustness = TargetedRobustnessResult(
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
    overfit = TargetedOverfitResult(
        run_id=f"overfit-{run_id}",
        robustness_run_id=run_id,
        symbol=symbol,
        strategy_version_id="version-1",
        strategy_semver="1.0.0",
        base_parameter_hash="base-hash",
        data_hash=f"data-{run_id}",
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
    quality = TargetedDataQualityResult(
        run_id=f"quality-{run_id}",
        robustness_run_id=run_id,
        symbol=symbol,
        strategy_version_id="version-1",
        strategy_semver="1.0.0",
        data_hash=f"data-{run_id}",
        raw_data_hash=f"raw-{run_id}",
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
    stress = TargetedExecutionStressResult(
        run_id=f"stress-{run_id}",
        robustness_run_id=run_id,
        symbol=symbol,
        strategy_version_id="version-1",
        strategy_semver="1.0.0",
        base_parameter_hash="base-hash",
        data_hash=f"data-{run_id}",
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
    review = TargetedReviewResult(
        run_id=f"review-{run_id}",
        robustness_run_id=run_id,
        validation_run_id=None,
        overfit_run_id=overfit.run_id,
        data_quality_run_id=quality.run_id,
        execution_stress_run_id=stress.run_id,
        symbol=symbol,
        strategy_version_id="version-1",
        strategy_semver="1.0.0",
        base_parameter_hash="base-hash",
        data_hash=f"data-{run_id}",
        provider="IBKR",
        evidence_origins=("captured_stream",),
        dependence=DependenceDiagnostic(
            oos_session_count=20,
            lag1_autocorrelation=None,
            effective_sample_size_ar1=Decimal("10"),
            newey_west_lags=1,
            hac_mean_return=Decimal("0.001"),
            hac_standard_error=Decimal("0.0005"),
            probability_mean_positive=Decimal("0.80"),
            status="estimated",
        ),
        gates=(
            EvidenceGate(
                code="complete_sessions",
                name="完整会话",
                passed=True,
                observed="20",
                required="20",
                evidence="20/20",
                severity="hard",
            ),
        ),
        passed_gates=1,
        blocking_failures=0,
        warnings=(),
        decision="人工评审",
        eligible_for_independent_review=True,
        status="research_review",
    )
    return TargetedRobustnessBundle(
        robustness=robustness,
        walk_forward=None,
        overfit=overfit,
        data_quality=quality,
        execution_stress=stress,
        review=review,
    )


# -- route and wiring ----------------------------------------------------


def test_research_route_is_the_native_aggregate(window: MainWindow) -> None:
    assert window.shell.page("research") is window.research_page
    assert (
        window.research_page.active_workspace()
        is ResearchWorkspace.TARGETED
    )
    window.research_page.set_active_workspace(ResearchWorkspace.BACKTEST)
    assert window.research_page._tabs.currentWidget() is window.backtest_page


def test_every_targeted_intent_reaches_its_owner(
    window: MainWindow, monkeypatch
) -> None:
    """The page's intents split by owner, and each reaches the right one.

    The session/Shadow intents are still the window's handlers.  The four
    evidence intents are *not*: they reach the capability that owns the evidence,
    which is the property v2O-C5A establishes.  Driving them through the real
    buttons is what makes this a wiring test rather than a signal test.
    """

    page = window.targeted_validation_page
    orchestrator = window.targeted_evidence_orchestrator
    seen: list[str] = []
    session_wiring = (
        ("strategy_selected", "_shadow_strategy_selection_changed"),
        ("target_apply_requested", "_target_symbol_requested"),
        ("target_subscribe_requested", "_target_subscribe_requested"),
        ("shadow_start_requested", "_start_shadow"),
        ("shadow_stop_requested", "_stop_shadow"),
    )
    for _signal, handler in session_wiring:
        monkeypatch.setattr(
            window,
            handler,
            lambda *_args, handler=handler: seen.append(handler),
        )
    evidence_wiring = (
        ("replay_requested", "request_replay"),
        ("robustness_requested", "request_robustness"),
        ("robustness_run_selected", "select_robustness_run"),
        ("review_run_selected", "select_review_run"),
    )
    for _signal, method in evidence_wiring:
        monkeypatch.setattr(
            orchestrator,
            method,
            lambda *_args, method=method: seen.append(method),
        )
    for signal, _handler in session_wiring + evidence_wiring:
        getattr(page, signal).disconnect()
    window._connect_targeted_validation_page()

    page.set_strategy_options(
        (
            SimpleNamespace(version_id="v1", label="Version 1"),
            SimpleNamespace(version_id="v2", label="Version 2"),
        ),
        "v1",
    )
    page.session_panel.controls.strategy_combo.setCurrentIndex(1)
    page.set_target_symbol("AAPL")
    page.session_panel.controls.target_symbol_apply_button.click()
    page.session_panel.controls.target_symbol_subscribe_button.click()
    page.session_panel.controls.render(
        TargetedControlView(
            strategy_enabled=True,
            target_enabled=True,
            subscribe_enabled=True,
            shadow_start_enabled=True,
            shadow_stop_enabled=True,
            replay_enabled=True,
            robustness_enabled=True,
        )
    )
    page.session_panel.controls.shadow_start_button.click()
    page.session_panel.controls.shadow_stop_button.click()
    page.session_panel.controls.replay_button.click()
    page.session_panel.controls.robustness_button.click()
    page.robustness_run_selected.emit("run-1")
    page.review_run_selected.emit("review-1")

    assert seen == [
        handler for _signal, handler in session_wiring
    ] + [method for _signal, method in evidence_wiring]


def test_targeted_theme_switch_notifies_the_page(
    window: MainWindow, monkeypatch
) -> None:
    seen: list[object] = []
    monkeypatch.setattr(
        window.targeted_validation_page,
        "set_palette",
        lambda palette: seen.append(palette),
    )
    window._apply_theme("light")
    assert len(seen) == 1


def test_targeted_controls_preserve_legacy_shadow_availability(
    window: MainWindow,
) -> None:
    window.shadow_snapshot = SimpleNamespace(active=True)
    controls = window._targeted_controls()
    assert controls.strategy_enabled is False
    assert controls.target_enabled is False
    assert controls.subscribe_enabled is False
    assert controls.shadow_start_enabled is False
    assert controls.shadow_stop_enabled is True
    assert controls.replay_enabled is True
    assert controls.robustness_enabled is True


def test_a_robustness_completion_asks_for_focus_and_navigates_semantically(
    window: MainWindow, monkeypatch
) -> None:
    """The one-shot tab fields are gone; focus is a pure navigation request.

    A completed suite used to stash two integers on the window so the *next*
    repaint would switch tabs.  That is state that exists only to be consumed
    once, and the page already names its panels semantically, so the capability
    now navigates its own page directly and asks the window for the route.
    """

    page = window.targeted_validation_page
    navigations: list[str] = []
    monkeypatch.setattr(
        page, "set_active_workspace", lambda ws: navigations.append(f"ws:{ws}")
    )
    monkeypatch.setattr(
        page,
        "set_active_evidence_workspace",
        lambda ws: navigations.append(f"evidence:{ws}"),
    )
    monkeypatch.setattr(window.shell, "navigate_to", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_record_runtime_event", lambda **kwargs: None)
    monkeypatch.setattr(window, "_log", lambda *args, **kwargs: None)

    window.targeted_evidence_orchestrator._robustness_finished(
        _bundle("run-1", "AAPL")
    )

    assert navigations == [
        f"ws:{TargetedWorkspace.EVIDENCE}",
        f"evidence:{TargetedEvidenceWorkspace.REVIEW}",
    ]
    assert (
        window.research_page.active_workspace() is ResearchWorkspace.TARGETED
    )
    # No integer reached the tab widgets: the vocabulary is the page's own.
    for name in (
        "_targeted_active_workspace",
        "_targeted_active_evidence_tab",
    ):
        assert not hasattr(window, name), name


# -- shadow characterization --------------------------------------------


def test_shadow_start_rejects_active_trading_runtime(
    window: MainWindow, dialogs
) -> None:
    window.trading_runtime = SimpleNamespace(
        session=SimpleNamespace(active=True)
    )
    window._start_shadow()
    assert dialogs[0][0] == "warning"
    assert dialogs[0][1][1] == "IBKR Paper 自动量化运行中"


def test_shadow_start_rejects_missing_paper_capital(
    window: MainWindow, monkeypatch, dialogs
) -> None:
    monkeypatch.setattr(window, "_selected_shadow_strategy_record", _valid_strategy)
    monkeypatch.setattr(window.account_orchestrator, "fresh_paper_net_liquidation", lambda: None)
    window._start_shadow()
    assert dialogs[0][1][1] == "缺少 IBKR Paper 资金真值"


def test_shadow_start_rejects_stale_market(
    window: MainWindow, monkeypatch, dialogs
) -> None:
    monkeypatch.setattr(window, "_selected_shadow_strategy_record", _valid_strategy)
    monkeypatch.setattr(window.account_orchestrator, "fresh_paper_net_liquidation", lambda: Decimal("10000"))
    monkeypatch.setattr(
        type(window.market_orchestrator),
        "is_live",
        property(lambda self: True),
    )
    monkeypatch.setattr(
        type(window.market_orchestrator),
        "snapshot",
        property(lambda self: SimpleNamespace(realtime_ready=False, quotes=())),
    )
    window._start_shadow()
    assert dialogs[0][1][1] == "行情门未通过"


def test_shadow_start_rejects_non_research_eligible_symbol(
    window: MainWindow, monkeypatch, dialogs
) -> None:
    monkeypatch.setattr(window, "_selected_shadow_strategy_record", _valid_strategy)
    monkeypatch.setattr(window.account_orchestrator, "fresh_paper_net_liquidation", lambda: Decimal("10000"))
    _fake_live_market(window, _ready_stream())
    window.universe_orchestrator.restore_snapshot(
        replace(_eligible_universe(), records=(
            UniverseRecord(
                symbol="AAPL",
                name="Apple",
                exchange="NASDAQ",
                security_type="STK",
                eligible_for_research=False,
            ),
        ))
    )
    window.targeted_validation_page.set_target_symbol("AAPL")
    window._start_shadow()
    assert dialogs[0][1][1] == "标的门未通过"


def test_shadow_start_rejects_missing_fresh_target_quote(
    window: MainWindow, monkeypatch, dialogs
) -> None:
    monkeypatch.setattr(window, "_selected_shadow_strategy_record", _valid_strategy)
    monkeypatch.setattr(window.account_orchestrator, "fresh_paper_net_liquidation", lambda: Decimal("10000"))
    _fake_live_market(
        window,
        _ready_stream(SimpleNamespace(symbol="AAPL", realtime_ready=False)),
    )
    window.universe_orchestrator.restore_snapshot(_eligible_universe())
    window.targeted_validation_page.set_target_symbol("AAPL")
    window._start_shadow()
    assert dialogs[0][1][1] == "目标行情未就绪"


def test_shadow_start_allowed_path_builds_and_starts_engine(
    window: MainWindow, monkeypatch, dialogs
) -> None:
    strategy = _valid_strategy()
    monkeypatch.setattr(window, "_selected_shadow_strategy_record", lambda: strategy)
    monkeypatch.setattr(window.account_orchestrator, "fresh_paper_net_liquidation", lambda: Decimal("10000"))
    monkeypatch.setattr(desktop, "build_targeted_shadow_config", lambda *args, **kwargs: object())
    monkeypatch.setattr(desktop, "ShadowPaperEngine", _Engine)
    monkeypatch.setattr(window, "_publish_targeted_session_view", lambda: None)
    monkeypatch.setattr(window, "_record_runtime_event", lambda **kwargs: None)
    monkeypatch.setattr(window, "_log", lambda *args, **kwargs: None)
    _fake_live_market(window, _ready_stream())
    window.universe_orchestrator.restore_snapshot(_eligible_universe())
    window.broker_account._portfolio = SimpleNamespace(
        account=SimpleNamespace(account_alias="Paper")
    )
    window.shadow_workflow = _Workflow()
    window.targeted_validation_page.set_target_symbol("AAPL")
    _Engine.created.clear()

    window._start_shadow()

    assert len(_Engine.created) == 1
    assert _Engine.created[0].active is True
    assert window.shadow_snapshot.active is True
    assert window.shadow_workflow.active is True


def test_shadow_stop_calls_engine_and_workflow(window: MainWindow, monkeypatch) -> None:
    stopped = SimpleNamespace(active=False)
    engine = SimpleNamespace(active=True, stop=lambda: stopped)
    workflow = _Workflow()
    workflow.active = True
    window.shadow_engine = engine
    window.shadow_workflow = workflow
    monkeypatch.setattr(window, "_publish_targeted_session_view", lambda: None)
    monkeypatch.setattr(window, "_record_runtime_event", lambda **kwargs: None)

    window._stop_shadow()

    assert window.shadow_snapshot is stopped
    assert workflow.active is False
