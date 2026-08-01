from __future__ import annotations

import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTabWidget

from us_quant import desktop
from us_quant.desktop import MainWindow
from us_quant.unified_workflow_ui import UnifiedWorkflowPage


_APP = QApplication.instance() or QApplication([])


def test_self_test_exits_before_showing_or_finishing_splash() -> None:
    source = inspect.getsource(desktop.main)
    self_test_branch = source.index("if self_test:")
    early_return = source.index("return 0", self_test_branch)
    show_window = source.index("window.showMaximized()")
    finish_splash = source.index("splash.finish(window)")
    assert self_test_branch < early_return < show_window < finish_splash


def _window(monkeypatch, *, legacy: bool) -> MainWindow:
    if legacy:
        monkeypatch.setenv("US_QUANT_LEGACY_UI", "1")
    else:
        monkeypatch.delenv("US_QUANT_LEGACY_UI", raising=False)
    window = MainWindow()
    _APP.processEvents()
    return window


def test_default_desktop_uses_multilevel_workflow_navigation(monkeypatch) -> None:
    window = _window(monkeypatch, legacy=False)
    try:
        workflow = window.unified_workflow_page
        assert isinstance(workflow, UnifiedWorkflowPage)
        assert set(workflow._sections) == {"today", "shadow", "research", "audit"}
        assert workflow.section("today").is_expanded()
        assert not workflow.section("shadow").is_expanded()
        assert not workflow.section("research").is_expanded()
        assert not workflow.section("audit").is_expanded()
        assert workflow.active_key == "today"
        assert not hasattr(workflow, "scroll_area")
        assert workflow.section("shadow").boundary_label.text() == (
            "仅内部模拟，不发送 IBKR 或任何券商订单。"
        )
        secondary_tabs = workflow.section("today").findChild(
            QTabWidget, "workflowSecondaryTabs"
        )
        assert secondary_tabs is not None
        assert secondary_tabs.count() == 4
        assert window.auto_detail_tabs.count() == 5
        assert window.targeted_workspace_tabs.count() == 5
        assert window.auto_prepare_button is not None
        assert window.auto_start_button is not None
        assert window.shadow_start_button is not None
        assert window.targeted_review_history_table is not None
    finally:
        window.close()
        window.deleteLater()


def test_first_level_navigation_switches_modules_exclusively(monkeypatch) -> None:
    window = _window(monkeypatch, legacy=False)
    try:
        workflow = window.unified_workflow_page
        assert workflow is not None
        workflow._anchors["shadow"].click()
        _APP.processEvents()
        assert not workflow.section("today").is_expanded()
        assert workflow.section("shadow").is_expanded()
        assert workflow.module_stack.currentWidget() is workflow.section("shadow")
        assert not workflow.section("shadow").boundary_label.isHidden()
    finally:
        window.close()
        window.deleteLater()


def test_paper_order_watchdog_heartbeat_is_wired(monkeypatch) -> None:
    """H-1 regression: the order watchdog must not depend on stream ticks."""
    window = _window(monkeypatch, legacy=False)
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


def test_finalization_deferral_replaces_halt_on_busy_resource(
    monkeypatch,
) -> None:
    """H-3 regression: a busy broker group defers the proof, never halts."""
    window = _window(monkeypatch, legacy=False)
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


def test_legacy_environment_keeps_original_tab_tree(monkeypatch) -> None:
    window = _window(monkeypatch, legacy=True)
    try:
        assert window.unified_workflow_page is None
        assert isinstance(window.tabs, QTabWidget)
        assert window.tabs.count() == 5
        assert isinstance(window.monitor_tabs, QTabWidget)
        assert isinstance(window.targeted_workspace_tabs, QTabWidget)
        assert isinstance(window.auto_detail_tabs, QTabWidget)
    finally:
        window.close()
        window.deleteLater()
