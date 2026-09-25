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

from us_quant.desktop import MainWindow
from us_quant.desktop_v2.orchestration.shadow import orchestrator as shadow_orchestrator_module
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


def _shadow_snapshot(*, active: bool):
    """A real Shadow snapshot, because the presenter reads every field.

    A ``SimpleNamespace(active=True)`` would satisfy a rule that only reads
    ``active``, and then pass here while the actual panel rendered nothing -- the
    presenter is the one projection this round must not change.
    """

    from us_quant.shadow.models import ShadowSnapshot

    return ShadowSnapshot(
        session_id="session-1",
        strategy_version_id="targeted-v1",
        parameter_hash="hash-v1",
        target_symbol="AAPL",
        active=active,
        initial_cash=Decimal("10000"),
        capital_source="IBKR Paper DU***67 NetLiquidation",
        cash=Decimal("10000"),
        equity=Decimal("10000"),
        realized_pnl=Decimal("0"),
        daily_realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        positions=(),
        fills=(),
        trades_today=0,
        trading_day="2026-09-22",
        status="运行中" if active else "已停止",
        observed_at="2026-09-22T14:00:00+00:00",
    )


def render_session_view(window: MainWindow):
    """The session view the capability just painted, captured from the page.

    The capability is the only session painter now and it fetches the Shadow
    snapshot itself, so a test that wants to read the *projection* -- rather than
    the widgets -- records the render it drives.  That keeps the assertion on the
    projection rule instead of on "some call happened".
    """

    painted: list[object] = []
    original = window.targeted_validation_page.render_session
    window.targeted_validation_page.render_session = painted.append
    try:
        window.targeted_session_orchestrator.render_current()
    finally:
        window.targeted_validation_page.render_session = original
    assert painted, "the session capability must paint"
    return painted[-1]


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
    """The page's intents split three ways, and each reaches the right owner.

    Session intents go to ``targeted_session_orchestrator`` (v2O-C5B), evidence
    intents to ``targeted_evidence_orchestrator`` (v2O-C5A), and the two Shadow
    intents to ``shadow_orchestrator`` (v2O-D) -- starting and stopping the
    internal simulation is the Shadow runtime, and it has its own capability.
    Driving them through the real buttons is what makes this a wiring test rather
    than a signal test.
    """

    page = window.targeted_validation_page
    session = window.targeted_session_orchestrator
    evidence = window.targeted_evidence_orchestrator
    shadow = window.shadow_orchestrator
    seen: list[str] = []
    session_wiring = (
        ("target_draft_changed", "adopt_target_draft"),
        ("strategy_selected", "request_strategy_selection"),
        ("target_apply_requested", "request_target_apply"),
        ("target_subscribe_requested", "request_target_subscribe"),
    )
    shadow_wiring = (
        ("shadow_start_requested", "start"),
        ("shadow_stop_requested", "stop"),
    )
    evidence_wiring = (
        ("replay_requested", "request_replay"),
        ("robustness_requested", "request_robustness"),
        ("robustness_run_selected", "select_robustness_run"),
        ("review_run_selected", "select_review_run"),
    )
    for _signal, method in session_wiring:
        monkeypatch.setattr(
            session,
            method,
            lambda *_args, method=method: seen.append(method),
        )
    for _signal, method in shadow_wiring:
        monkeypatch.setattr(
            shadow,
            method,
            lambda *_args, method=method: seen.append(method),
        )
    for _signal, method in evidence_wiring:
        monkeypatch.setattr(
            evidence,
            method,
            lambda *_args, method=method: seen.append(method),
        )
    for signal, _ in session_wiring + shadow_wiring + evidence_wiring:
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
    page.session_panel.controls.target_symbol_input.setText("AAPL")
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
        # The clicked order: the combo, then typing, then the two target buttons,
        # then the Shadow buttons, then the evidence ones.
        "request_strategy_selection",
        "adopt_target_draft",
        "request_target_apply",
        "request_target_subscribe",
        "start",
        "stop",
        "request_replay",
        "request_robustness",
        "select_robustness_run",
        "select_review_run",
    ]


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
    """The enabled-state rule is the same one; its owner moved with the session.

    A running internal simulation owns the target, the strategy and the
    subscription, so those close and only 停止内部仿真 stays live.  Replay and
    robustness stay available.  The rule is unchanged -- only who projects it.
    """

    window.shadow_orchestrator._snapshot = _shadow_snapshot(active=True)
    controls = render_session_view(window).controls
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
    monkeypatch.setattr(
        window.runtime_events_orchestrator, "record", lambda **kwargs: None
    )
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


# -- cross-workflow wiring ----------------------------------------------
#
# Three external changes reach the session capability, and one does not.  Each of
# these drives the real bridge -- the window's own slot or the capability's own
# signal -- so a rewiring that dropped a consumer fails here rather than in
# production.
#
# The execution route is a *fourth* consumer of the market fan-out.  Since G2-B
# it is repainted by ``execution_orchestrator.refresh_all`` -- the retired window
# seam was ``_populate_auto_quant_candidates`` -- so the tests below stub that
# call at its new owner and assert what *their* own route did.


def test_a_market_snapshot_refreshes_the_targeted_preflight(
    window: MainWindow, monkeypatch
) -> None:
    """Market snapshot -> session preflight refresh, through the real bridge."""

    calls: list[str] = []
    monkeypatch.setattr(
        window.targeted_session_orchestrator,
        "refresh_preflight",
        lambda: calls.append("preflight"),
    )
    monkeypatch.setattr(window, "_record_minute_snapshot", lambda _s: None)
    monkeypatch.setattr(
            window.dashboard_orchestrator, "render_current", lambda: None
        )
    monkeypatch.setattr(
        window.execution_orchestrator, "refresh_all", lambda: None
    )

    window._on_market_snapshot_changed(
        SimpleNamespace(
            realtime_ready=True,
            message="ok",
            quotes=(),
            source_id="test",
            source_label="TestFeed",
        )
    )

    assert calls == ["preflight"], calls


def test_a_market_snapshot_repaints_the_session_when_shadow_runs(
    window: MainWindow, monkeypatch
) -> None:
    """A Shadow snapshot change repaints the session -- and only the session.

    The Shadow capability owns the snapshot and asks the session capability to
    repaint; the evidence tables must not be touched, which is the C5A property
    this round preserves.  The engine is injected into the orchestrator, because
    the window no longer holds one.
    """

    paints: list[str] = []
    monkeypatch.setattr(
        window.targeted_session_orchestrator,
        "render_current",
        lambda: paints.append("session"),
    )
    evidence: list[object] = []
    monkeypatch.setattr(
        window.targeted_validation_page,
        "render_evidence",
        lambda view: evidence.append(view),
    )
    monkeypatch.setattr(window, "_record_minute_snapshot", lambda _s: None)
    monkeypatch.setattr(
            window.dashboard_orchestrator, "render_current", lambda: None
        )
    monkeypatch.setattr(
        window.execution_orchestrator, "refresh_all", lambda: None
    )

    class _Engine:
        active = True

        def on_stream(self, _snapshot):
            return _shadow_snapshot(active=True)

        def stop(self):
            self.active = False
            return _shadow_snapshot(active=False)

    window.shadow_orchestrator._engine = _Engine()

    window._on_market_snapshot_changed(
        SimpleNamespace(
            realtime_ready=True,
            message="ok",
            quotes=(),
            source_id="test",
            source_label="TestFeed",
        )
    )

    # Exactly two session paints, both intended: the preflight refresh paints, and
    # then the Shadow snapshot bridge paints the new positions.  Asserting the
    # *count* rather than "at least one" is what makes the Shadow repaint
    # load-bearing -- with the bridge removed the preflight refresh alone would
    # still produce one paint and a loose assertion would pass.
    assert paints == ["session", "session"], paints
    assert evidence == [], "a market tick must not rebuild the evidence tables"


def test_a_market_snapshot_without_shadow_refreshes_without_the_shadow_repaint(
    window: MainWindow, monkeypatch
) -> None:
    """The Shadow repaint is the *bridge's*, not the preflight's.

    The mirror of the test above: with no running engine there is exactly one
    paint, so the second one really is the Shadow bridge reacting to a new
    snapshot rather than a second preflight refresh.
    """

    paints: list[str] = []
    monkeypatch.setattr(
        window.targeted_session_orchestrator,
        "render_current",
        lambda: paints.append("session"),
    )
    monkeypatch.setattr(window, "_record_minute_snapshot", lambda _s: None)
    monkeypatch.setattr(
            window.dashboard_orchestrator, "render_current", lambda: None
        )
    monkeypatch.setattr(
        window.execution_orchestrator, "refresh_all", lambda: None
    )

    window.shadow_orchestrator._engine = None

    window._on_market_snapshot_changed(
        SimpleNamespace(
            realtime_ready=True,
            message="ok",
            quotes=(),
            source_id="test",
            source_label="TestFeed",
        )
    )

    assert paints == ["session"], paints


def test_an_account_portfolio_change_refreshes_the_targeted_preflight(
    window: MainWindow, monkeypatch
) -> None:
    """Account portfolio change -> session preflight refresh.

    The two execution halves the window used to repaint here are the route's own
    now (G2-B): ``refresh_current`` draws the session and ``refresh_preflight``
    the readiness line.  Both are stubbed because this test is about the
    *targeted* preflight -- but they are still driven, so the fan-out has to
    reach the route for the test to pass at all.
    """

    calls: list[str] = []
    monkeypatch.setattr(
        window.targeted_session_orchestrator,
        "refresh_preflight",
        lambda: calls.append("preflight"),
    )
    monkeypatch.setattr(
            window.dashboard_orchestrator, "render_current", lambda: None
        )
    execution: list[str] = []
    monkeypatch.setattr(
        window.execution_orchestrator,
        "refresh_current",
        lambda: execution.append("current"),
    )
    monkeypatch.setattr(
        window.execution_orchestrator,
        "refresh_preflight",
        lambda: execution.append("preflight"),
    )

    window._on_account_portfolio_changed(SimpleNamespace(account=None))

    assert calls == ["preflight"], calls
    # The route's readiness line is repainted too: an account fact is one of
    # its preflight inputs.
    assert execution == ["preflight"], execution


def test_an_evidence_replay_completion_refreshes_the_minute_status(
    window: MainWindow, monkeypatch
) -> None:
    """Evidence replay completion -> session minute refresh, and nothing else.

    The capability's published signal is connected to the session capability's own
    command: minute status is not an evidence fact, so it is not the evidence
    capability's to write.
    """

    calls: list[str] = []
    monkeypatch.setattr(
        window.targeted_session_orchestrator,
        "refresh_minute_status",
        lambda symbol=None: calls.append(symbol),
    )

    window.targeted_evidence_orchestrator.minute_status_refresh_requested.emit(
        "AAPL"
    )

    assert calls == ["AAPL"], calls


def test_a_targeted_strategy_choice_reaches_the_selection_service(
    window: MainWindow,
) -> None:
    """The page -> capability -> StrategySelectionService path, end to end.

    The canonical selection stays in the service; the capability relays the id it
    was handed and caches nothing.
    """

    from us_quant.trading.application.strategy_selection import (
        StrategySelectionPurpose,
    )

    seen: list[tuple] = []
    original = window.strategy_selection.select

    def select(purpose, version_id):
        seen.append((purpose, version_id))
        return original(purpose, version_id)

    window.strategy_selection.select = select  # type: ignore[method-assign]
    try:
        window.targeted_session_orchestrator.request_strategy_selection(
            "does-not-exist"
        )
    finally:
        window.strategy_selection.select = original  # type: ignore[method-assign]

    assert seen == [
        (StrategySelectionPurpose.TARGETED_SHADOW, "does-not-exist")
    ], seen


def test_the_targeted_draft_feeds_the_evidence_target_provider(
    window: MainWindow,
) -> None:
    """Typing a symbol without applying it still lets Replay read it.

    The evidence capability reads the session snapshot's draft through its
    provider, so the two halves share exactly one fact and neither imports the
    other.
    """

    window.targeted_validation_page.session_panel.controls.target_symbol_input.setText(
        "aapl"
    )
    assert (
        window.targeted_evidence_orchestrator._target_symbol_provider() == "AAPL"
    )


def test_a_normal_session_refresh_leaves_the_evidence_tables_alone(
    window: MainWindow, monkeypatch
) -> None:
    """The C5A property, re-asserted against the new owner.

    A session refresh is a session refresh: it must not repaint seven research
    tables, which is why the two halves have separate entry points.
    """

    evidence: list[object] = []
    monkeypatch.setattr(
        window.targeted_validation_page,
        "render_evidence",
        lambda view: evidence.append(view),
    )

    window.targeted_session_orchestrator.render_current()

    assert evidence == []


# -- shadow characterization --------------------------------------------
#
# The intents now reach ``shadow_orchestrator`` instead of a window handler, so
# these drive the capability and assert the same operator-visible outcome.  The
# gates themselves read the window's composed facts through the injected
# providers, so a test still sets up the world the way it always did.


def test_shadow_start_rejects_active_trading_runtime(
    window: MainWindow, dialogs
) -> None:
    """The capital-truth gate reads the Paper capability's canonical result.

    v2O-E2 removed the window's runtime handle, so this installs the fact where the
    workflow keeps it -- which is where the gate reads it.  The result carries the whole
    shape the facade reads, not just the one field under test, so the window that is
    torn down afterwards still sees a well-formed session.
    """

    window.paper_workflow._result = SimpleNamespace(
        engine_snapshot=SimpleNamespace(active=True),
        state=SimpleNamespace(active=True, halted=False, finalized=False),
        events=(),
        health=None,
    )
    try:
        window.shadow_orchestrator.start()
        assert dialogs[0][0] == "warning"
        assert dialogs[0][1][1] == "IBKR Paper 自动量化运行中"
    finally:
        window.paper_workflow._result = None


def test_shadow_start_rejects_missing_paper_capital(
    window: MainWindow, monkeypatch, dialogs
) -> None:
    monkeypatch.setattr(window, "_selected_shadow_strategy_record", _valid_strategy)
    monkeypatch.setattr(window.account_orchestrator, "fresh_paper_net_liquidation", lambda: None)
    window.shadow_orchestrator.start()
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
    window.shadow_orchestrator.start()
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
    window.targeted_session_orchestrator.adopt_target_draft("AAPL")
    window.shadow_orchestrator.start()
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
    window.targeted_session_orchestrator.adopt_target_draft("AAPL")
    window.shadow_orchestrator.start()
    assert dialogs[0][1][1] == "目标行情未就绪"


def test_shadow_start_allowed_path_builds_and_starts_engine(
    window: MainWindow, monkeypatch, dialogs
) -> None:
    """The engine is built and started through the capability, and the lease held.

    The constructor symbols are patched where the orchestrator imports them, not
    in ``us_quant.desktop``: the window no longer names either one, which is the
    ownership this round moved.
    """

    strategy = _valid_strategy()
    monkeypatch.setattr(window, "_selected_shadow_strategy_record", lambda: strategy)
    monkeypatch.setattr(window.account_orchestrator, "fresh_paper_net_liquidation", lambda: Decimal("10000"))
    monkeypatch.setattr(
        shadow_orchestrator_module,
        "build_targeted_shadow_config",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(shadow_orchestrator_module, "ShadowPaperEngine", _Engine)
    monkeypatch.setattr(
        window.targeted_session_orchestrator, "render_current", lambda: None
    )
    monkeypatch.setattr(
        window.runtime_events_orchestrator, "record", lambda **kwargs: None
    )
    monkeypatch.setattr(window, "_log", lambda *args, **kwargs: None)
    _fake_live_market(window, _ready_stream())
    window.universe_orchestrator.restore_snapshot(_eligible_universe())
    window.broker_account._portfolio = SimpleNamespace(
        account=SimpleNamespace(account_alias="Paper")
    )
    workflow = _Workflow()
    window.shadow_orchestrator._lease = workflow  # type: ignore[assignment]
    window.targeted_session_orchestrator.adopt_target_draft("AAPL")
    _Engine.created.clear()

    window.shadow_orchestrator.start()

    assert len(_Engine.created) == 1
    assert _Engine.created[0].active is True
    assert window.shadow_orchestrator.snapshot.active is True
    assert workflow.active is True


def test_shadow_stop_calls_engine_and_workflow(window: MainWindow, monkeypatch) -> None:
    stopped = SimpleNamespace(active=False)
    engine = SimpleNamespace(active=True, stop=lambda: stopped)
    workflow = _Workflow()
    workflow.active = True
    window.shadow_orchestrator._engine = engine  # type: ignore[assignment]
    window.shadow_orchestrator._lease = workflow  # type: ignore[assignment]
    # Shadow is the holder of the shared lease in this scenario, which is what
    # entitles the stop to hand it back.  The flag is set explicitly because the
    # release is gated on *this capability* holding the lease, not on the lease
    # being active -- Paper may be the holder instead.
    window.shadow_orchestrator._holds_lease = True
    monkeypatch.setattr(
        window.targeted_session_orchestrator, "render_current", lambda: None
    )
    monkeypatch.setattr(
        window.runtime_events_orchestrator, "record", lambda **kwargs: None
    )

    window.shadow_orchestrator.stop()

    assert window.shadow_orchestrator.snapshot is stopped
    assert workflow.active is False


def _arm_shadow_start(window: MainWindow, monkeypatch) -> None:
    """Make a real start succeed through the wiring: live market, capital, strategy."""

    monkeypatch.setattr(
        window, "_selected_shadow_strategy_record", lambda: _valid_strategy()
    )
    monkeypatch.setattr(
        window.account_orchestrator,
        "fresh_paper_net_liquidation",
        lambda: Decimal("10000"),
    )
    monkeypatch.setattr(
        shadow_orchestrator_module,
        "build_targeted_shadow_config",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(shadow_orchestrator_module, "ShadowPaperEngine", _Engine)
    monkeypatch.setattr(
        window.targeted_session_orchestrator, "render_current", lambda: None
    )
    monkeypatch.setattr(
        window.runtime_events_orchestrator, "record", lambda **kwargs: None
    )
    monkeypatch.setattr(window, "_log", lambda *args, **kwargs: None)
    _fake_live_market(window, _ready_stream())
    window.universe_orchestrator.restore_snapshot(_eligible_universe())
    window.broker_account._portfolio = SimpleNamespace(
        account=SimpleNamespace(account_alias="Paper")
    )
    window.targeted_session_orchestrator.adopt_target_draft("AAPL")
    _Engine.created.clear()


def test_two_shadow_start_requests_leave_one_session_holding_one_lease(
    window: MainWindow, monkeypatch
) -> None:
    """The page signal twice must not produce a second session or a free lease.

    This is the safety property the capability extraction had to get right: the
    shared ``ExecutionLeaseManager`` *is* the Shadow XOR Paper invariant, so a
    duplicate start that released it would leave the simulation running while
    Paper could take execution.  Driven through the real signal, not by calling
    ``start()`` directly, so the wiring is covered too.
    """

    _arm_shadow_start(window, monkeypatch)
    lease = window.shadow_orchestrator._lease

    page = window.targeted_validation_page
    page.shadow_start_requested.emit()
    first_engine = _Engine.created[0]
    first_snapshot = window.shadow_orchestrator.snapshot

    page.shadow_start_requested.emit()

    assert len(_Engine.created) == 1, "a second engine must not be built"
    assert window.shadow_orchestrator.is_active is True
    assert window.shadow_orchestrator.snapshot is first_snapshot
    assert first_engine.active is True
    # The shared lease is still held, so Paper still cannot take execution.
    assert lease.active is True
    assert window.shadow_workflow.active is True
