"""Runtime regressions that must survive the Desktop UI v2 rewrite.

These tests were originally filed under ``test_desktop_unified_workflow.py``
and were mostly about the legacy/unified navigation shell.  The navigation
tests went away with the shells they described; what remains are the runtime
regressions that have nothing to do with which container is on screen:

* the offscreen self-test path must exit before it shows or finishes the
  splash, so ``scripts/verify.ps1`` can run the client headlessly;
* the Paper order watchdog heartbeat must not depend on market-stream ticks
  (H-1);
* a busy broker resource must *defer* finalization proof instead of halting
  the session (H-3).

All three are business/runtime behaviour, so they are kept and re-pointed at
the v2 shell.
"""

from __future__ import annotations

import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from us_quant import desktop
from us_quant.desktop import MainWindow
from us_quant.desktop_v2.navigation import DEFAULT_ROUTE, ROUTES
from us_quant.desktop_v2.orchestration.paper.orchestrator import PaperOrchestrator


_APP = QApplication.instance() or QApplication([])


def test_self_test_exits_before_showing_or_finishing_splash() -> None:
    source = inspect.getsource(desktop.main)
    self_test_branch = source.index("if self_test:")
    early_return = source.index("return 0", self_test_branch)
    show_window = source.index("window.showMaximized()")
    finish_splash = source.index("splash.finish(window)")
    assert self_test_branch < early_return < show_window < finish_splash


def _window() -> MainWindow:
    window = MainWindow()
    _APP.processEvents()
    return window


def test_default_desktop_builds_every_v2_route() -> None:
    window = _window()
    try:
        assert window.shell.routes == ROUTES
        assert window.shell.current_route == DEFAULT_ROUTE
        # The execution route is a native v2 page now: the window holds the page
        # and the page holds its own controls and detail sections.
        assert window.execution_page.details.tabs.count() == 5
        assert window.targeted_validation_page.workspace_tabs.count() == 5
        assert window.execution_page.controls.prepare_button is not None
        assert window.execution_page.controls.start_button is not None
        assert window.targeted_validation_page.session_panel.controls.shadow_start_button is not None
        assert window.targeted_validation_page.evidence_panel.review_history_table is not None
    finally:
        window.close()
        window.deleteLater()


def test_paper_order_watchdog_heartbeat_is_wired() -> None:
    """H-1 regression: the order watchdog must not depend on stream ticks.

    The heartbeat, its phase gate and the suppression window are active-Paper
    orchestration since v2O-E2, so the source assertions moved with them; what the
    window still owns is the timer, and that is asserted here on the real one.
    """

    window = _window()
    try:
        timer = window.paper_order_timer
        assert timer is not None
        assert timer.interval() == 1_000
        assert timer.isActive()
        # The heartbeat callback must be safe to invoke in any phase.
        window.paper_orchestrator.poll()
        # Stream ingress must refresh the watchdog liveness stamp...
        ingress = inspect.getsource(PaperOrchestrator.on_market_snapshot)
        assert "_last_stream_ingress_monotonic = self._clock()" in ingress
        # ...and the heartbeat skips when stream ticks drove the watchdog recently.
        poll_source = inspect.getsource(PaperOrchestrator.poll)
        assert "STREAM_INGRESS_SUPPRESSION_SECONDS" in poll_source
    finally:
        window.close()
        window.deleteLater()


def test_finalization_deferral_replaces_halt_on_busy_resource() -> None:
    """H-3 regression: a busy broker group defers the proof, never halts.

    v2O-E3 moved the proof into the capability, so the assertions read there.  The claim
    is unchanged and is now checkable in three pieces: the refused-submission branch
    clears its own flag and touches the workflow not at all (``fail_finalization_refresh``
    appears *once*, on the genuinely-failed path, not twice); the proof is only ever asked
    for on a ``STOPPING`` result; and the backoff is what stops a stream tick from
    re-reading the whole broker while the exits are still flattening.
    """

    window = _window()
    try:
        start = inspect.getsource(PaperOrchestrator._start_finalization)
        # fail_finalization_refresh remains only for the no-service branch; the
        # busy-resource branch must defer instead of halting the session.
        assert start.count("fail_finalization_refresh") == 1
        assert "suppress_busy_message=True" in start
        assert "if not started:" in start
        assert "self._finalization_inflight = False" in start[start.index("if not started:") :]

        schedule = inspect.getsource(PaperOrchestrator._maybe_schedule_finalization)
        assert "FINALIZATION_REFRESH_BACKOFF_SECONDS" in schedule
        assert "PaperWorkflowPhase.STOPPING" in schedule

        # And the one post-result hook is what reaches it, from the one result path.
        publish = inspect.getsource(PaperOrchestrator._publish_result)
        assert "self._after_result(result)" in publish
        after = inspect.getsource(PaperOrchestrator._after_result)
        assert "_maybe_schedule_finalization(result)" in after
        assert "_maybe_finish_finalized_session(result)" in after
    finally:
        window.close()
        window.deleteLater()
