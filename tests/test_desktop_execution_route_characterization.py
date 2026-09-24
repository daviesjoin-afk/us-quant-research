"""Characterization of the execution route's visible state.

These assertions were written against the legacy builder, before the route moved
to ``ExecutionPage``, and every one of them still holds afterwards.  That is the
point: the migration moved the widgets, and the operator-visible behaviour -- the
default control state, the two numeric defaults, the five detail sections, and
the phase -> control mapping -- had to survive the move unchanged.

What changed is only *where* the window keeps them: the window now knows one
object, ``self.execution_page``, and the controls inside it are the page's
business.  The tests read them through the page for the same reason.

The phase-driven cases go through the window's one result handler rather than
poking the controls, because the mapping is produced by the render path and a
test that set the controls directly would pass with that path broken.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase


_APP = QApplication.instance() or QApplication([])


def _window() -> MainWindow:
    window = MainWindow()
    _APP.processEvents()
    return window


class _Phase:
    """The controller surface the render path reads, and nothing else."""

    def __init__(
        self, phase: PaperWorkflowPhase, *, awaiting: bool = False
    ) -> None:
        self.phase = phase
        self.result = None
        self.reconciliation_evidence = object() if awaiting else None


class _State:
    def __init__(self, *, halted: bool, finalized: bool) -> None:
        self.halted = halted
        self.finalized = finalized


class _Result:
    """One controller result, with no engine snapshot to render."""

    def __init__(self, *, halted: bool = False, finalized: bool = False) -> None:
        self.engine_snapshot = None
        self.health = None
        self.events: tuple[object, ...] = ()
        self.state = _State(halted=halted, finalized=finalized)


def _apply(window: MainWindow, phase: PaperWorkflowPhase, *, awaiting=False):
    """Drive one result through the real render path for ``phase``."""

    window.paper_workflow = _Phase(phase, awaiting=awaiting)  # type: ignore[assignment]
    window._on_paper_result_changed(_Result())  # type: ignore[arg-type]


def test_the_execution_route_starts_ready_to_prepare_only() -> None:
    """Default: you may prepare and start; you may not pause, stop or resume."""

    window = _window()
    try:
        controls = window.execution_page.controls
        assert controls.prepare_button.isEnabled()
        assert controls.start_button.isEnabled()
        assert not controls.pause_button.isEnabled()
        assert not controls.resume_button.isEnabled()
        assert not controls.stop_button.isEnabled()
        assert not window.execution_page.details.reconcile_button.isEnabled()
        assert not controls.resume_reconciliation_button.isEnabled()
    finally:
        window.close()
        window.deleteLater()


def test_the_launch_confirmation_and_channel_probe_are_hidden_by_default() -> None:
    """Neither is an operator control: one is written by the confirm dialog."""

    window = _window()
    try:
        controls = window.execution_page.controls
        assert not controls.arm_confirm.isVisible()
        assert not controls.arm_confirm.isChecked()
        assert not controls.channel_check_button.isVisible()
    finally:
        window.close()
        window.deleteLater()


def test_the_two_numeric_inputs_keep_their_documented_defaults() -> None:
    window = _window()
    try:
        controls = window.execution_page.controls
        assert controls.candidate_limit_input.value() == 30
        assert controls.candidate_limit_input.minimum() == 3
        assert controls.candidate_limit_input.maximum() == 30
        assert controls.capital_limit_input.value() == 0
        # And the page's own read API agrees with what it displays.
        assert window.execution_page.candidate_limit() == 30
        assert window.execution_page.capital_limit() == 0
    finally:
        window.close()
        window.deleteLater()


def test_the_detail_tabs_keep_their_five_sections() -> None:
    window = _window()
    try:
        tabs = window.execution_page.details.tabs
        titles = [tabs.tabText(index) for index in range(tabs.count())]
    finally:
        window.close()
        window.deleteLater()
    assert titles == [
        "组合与盈亏",
        "影子执行带",
        "提交延迟",
        "候选与信号",
        "Paper订单",
    ]


def test_a_running_session_offers_pause_and_stop() -> None:
    window = _window()
    try:
        _apply(window, PaperWorkflowPhase.RUNNING)
        controls = window.execution_page.controls
        assert controls.pause_button.isEnabled()
        assert not controls.resume_button.isEnabled()
        assert controls.stop_button.isEnabled()
        assert not window.execution_page.details.reconcile_button.isEnabled()
    finally:
        window.close()
        window.deleteLater()


def test_a_paused_session_offers_resume_and_stop() -> None:
    window = _window()
    try:
        _apply(window, PaperWorkflowPhase.PAUSED)
        controls = window.execution_page.controls
        assert controls.resume_button.isEnabled()
        assert not controls.pause_button.isEnabled()
        assert controls.stop_button.isEnabled()
    finally:
        window.close()
        window.deleteLater()


def test_a_halted_session_offers_only_reconciliation() -> None:
    """HALTED is sticky: the recovery route opens, nothing else does."""

    window = _window()
    try:
        _apply(window, PaperWorkflowPhase.HALTED)
        controls = window.execution_page.controls
        assert window.execution_page.details.reconcile_button.isEnabled()
        assert not controls.resume_reconciliation_button.isEnabled()
        assert not controls.pause_button.isEnabled()
        assert not controls.resume_button.isEnabled()
        assert not controls.stop_button.isEnabled()
    finally:
        window.close()
        window.deleteLater()


def test_manual_resume_opens_only_with_evidence_awaiting_confirmation() -> None:
    """Phase alone is not enough: the one-shot proof must be present."""

    window = _window()
    try:
        controls = window.execution_page.controls
        _apply(window, PaperWorkflowPhase.RECONCILING_READY, awaiting=False)
        assert not controls.resume_reconciliation_button.isEnabled()

        _apply(window, PaperWorkflowPhase.RECONCILING_READY, awaiting=True)
        assert controls.resume_reconciliation_button.isEnabled()
        assert not window.execution_page.details.reconcile_button.isEnabled()
    finally:
        window.close()
        window.deleteLater()


def test_the_candidates_are_visible_before_a_session_is_armed() -> None:
    """The shortlist is inspectable at the moment it is being approved.

    The legacy route filled its candidate table straight from the prepared
    candidates, with no session behind them.  A route that waited for a runtime
    snapshot -- which only exists once a session is armed -- would show an empty
    table while asking the operator to approve exactly that content.
    """

    window = _window()
    try:
        assert window._paper_render_snapshot is None

        window.auto_quant_candidates = (
            _candidate("AAA"),
            _candidate("BBB"),
        )
        window._populate_auto_quant_candidates()

        table = window.execution_page.details.candidate_table
        assert table.rowCount() == 2
        # The table sorts itself once refilled, so the row order is not the
        # contract -- the content is.
        assert {
            table.item(row, 0).text() for row in range(table.rowCount())
        } == {"AAA", "BBB"}
    finally:
        window.close()
        window.deleteLater()


def _candidate(symbol: str):
    from decimal import Decimal

    from us_quant.trading.runtime.models import AutoQuantCandidate

    return AutoQuantCandidate(
        symbol=symbol,
        name=f"{symbol} Inc",
        sector="Tech",
        leader_tier=1,
        scan_score=Decimal("80"),
        signal="buy",
    )


def test_the_window_no_longer_owns_the_execution_widgets() -> None:
    """The route is one attribute: the page.  Nothing inside it is the window's.

    This is the guard the round exists for.  Every name below was a widget the
    window reached into by attribute; a page that could be reached into that way
    would put the presentation back under the window's control.
    """

    window = _window()
    try:
        for retired in (
            "auto_prepare_button",
            "auto_start_button",
            "auto_pause_button",
            "auto_resume_button",
            "auto_stop_button",
            "auto_stop_stream_button",
            "auto_reconcile_button",
            "auto_resume_from_reconciliation_button",
            "auto_channel_check_button",
            "auto_arm_confirm",
            "auto_strategy_combo",
            "auto_candidate_limit",
            "auto_capital_limit",
            "auto_summary_label",
            "auto_scope_label",
            "auto_session_label",
            "auto_preflight_label",
            "auto_pipeline_label",
            "auto_status_card",
            "auto_equity_card",
            "auto_realized_card",
            "auto_unrealized_card",
            "auto_position_card",
            "auto_detail_tabs",
            "auto_position_table",
            "auto_position_model",
            "auto_recent_fill_table",
            "auto_recent_fill_model",
            "auto_shadow_table",
            "auto_latency_table",
            "auto_candidate_table",
            "auto_order_table",
            "auto_execution_health_label",
        ):
            assert not hasattr(window, retired), retired
        # The two pieces of state the window legitimately keeps: the prepared
        # shortlist, and the presentation-only snapshot the route draws.  The second
        # one is named for what it is since v2O-E2 -- the session's own truth lives in
        # the Paper capability, and this may be read by render paths and nothing else.
        assert hasattr(window, "auto_quant_candidates")
        assert hasattr(window, "_paper_render_snapshot")
        assert not hasattr(window, "trading_runtime")
        assert window.execution_page is not None
    finally:
        window.close()
        window.deleteLater()
