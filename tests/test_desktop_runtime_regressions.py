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
        assert window.auto_detail_tabs.count() == 5
        assert window.targeted_workspace_tabs.count() == 5
        assert window.auto_prepare_button is not None
        assert window.auto_start_button is not None
        assert window.shadow_start_button is not None
        assert window.targeted_review_history_table is not None
    finally:
        window.close()
        window.deleteLater()


def test_paper_order_watchdog_heartbeat_is_wired() -> None:
    """H-1 regression: the order watchdog must not depend on stream ticks."""
    window = _window()
    try:
        timer = window.paper_order_timer
        assert timer is not None
        assert timer.interval() == 1_000
        assert timer.isActive()
        # The heartbeat callback must be safe to invoke in any phase.
        window._poll_auto_quant_orders()
        # Stream ingress must refresh the watchdog liveness stamp.
        source = inspect.getsource(MainWindow._stream_snapshot_received)
        assert "_last_stream_ingress_monotonic = monotonic()" in source
        # The heartbeat skips when stream ticks drove the watchdog recently.
        poll_source = inspect.getsource(MainWindow._poll_auto_quant_orders)
        assert (
            "monotonic() - self._last_stream_ingress_monotonic < 1.2"
            in poll_source
        )
    finally:
        window.close()
        window.deleteLater()


def test_finalization_deferral_replaces_halt_on_busy_resource() -> None:
    """H-3 regression: a busy broker group defers the proof, never halts."""
    window = _window()
    try:
        refresh = inspect.getsource(
            MainWindow._start_paper_finalization_refresh
        )
        # fail_finalization_refresh remains only for the service-None branch;
        # the busy-resource branch must defer instead of halting the session.
        assert refresh.count("fail_finalization_refresh") == 1
        assert "suppress_busy_message=True" in refresh
        apply = inspect.getsource(MainWindow._apply_paper_workflow_result)
        assert "_schedule_paper_finalization_refresh(result)" in apply
        schedule = inspect.getsource(
            MainWindow._schedule_paper_finalization_refresh
        )
        assert "monotonic() - last < 5.0" in schedule
    finally:
        window.close()
        window.deleteLater()
