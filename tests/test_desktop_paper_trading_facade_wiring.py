"""Desktop wiring coverage for the Paper trading facade boundary.

These tests pin the *wiring*: that ``MainWindow`` builds the facade, that the
migrated reads really go through it, and that the close path still refuses to
tear Paper down before the session is finalized.  No broker is started; the
window is constructed offscreen and its collaborators are replaced.
"""

from __future__ import annotations

import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.paper_trading_facade import PaperTradingFacade
from us_quant.workflow_state import PaperWorkflowPhase


_APP = QApplication.instance() or QApplication([])


def _window() -> MainWindow:
    window = MainWindow()
    _APP.processEvents()
    return window


def _source(name: str) -> str:
    return inspect.getsource(getattr(MainWindow, name))


def test_main_window_builds_the_paper_trading_facade() -> None:
    window = _window()
    try:
        assert isinstance(window.paper_trading, PaperTradingFacade)
    finally:
        window.deleteLater()


def test_the_facade_reads_the_live_attributes_not_snapshots() -> None:
    """Replacing the controller must be visible through the facade.

    The safety tests in ``test_desktop_runtime_teardown.py`` swap
    ``window.paper_workflow`` to drive the halted and refused-close paths; if the
    facade captured the controller at construction those tests would pass while
    exercising a dead object.
    """

    window = _window()
    try:
        real = window.paper_workflow
        assert window.paper_trading.phase() is real.phase

        class _Replacement:
            phase = PaperWorkflowPhase.HALTED
            result = None
            reconciliation_evidence = None

        window.paper_workflow = _Replacement()  # type: ignore[assignment]
        assert window.paper_trading.phase() is PaperWorkflowPhase.HALTED
    finally:
        window.deleteLater()


def test_the_facade_reads_the_live_order_service() -> None:
    window = _window()
    try:
        assert window.paper_trading.has_order_service() is False
        assert window.paper_trading.is_connected() is False

        class _Service:
            def connection_snapshot(self):
                class _Connection:
                    connected = True

                return _Connection()

        window.paper_order_service = _Service()  # type: ignore[assignment]
        assert window.paper_trading.has_order_service() is True
        assert window.paper_trading.is_connected() is True
    finally:
        window.paper_order_service = None
        window.deleteLater()


def test_a_status_read_goes_through_the_facade() -> None:
    """One real read on a real window, without touching a broker."""

    window = _window()
    try:
        snapshot = window.paper_trading.snapshot()

        assert snapshot.phase == PaperWorkflowPhase.IDLE.value
        assert snapshot.connected is False
        assert snapshot.finalized is True
        assert window.paper_trading.is_finalized() is True
    finally:
        window.deleteLater()


def test_disconnect_goes_through_the_facade_exactly_once() -> None:
    window = _window()
    try:
        calls: list[str] = []

        class _Service:
            def connection_snapshot(self):
                class _Connection:
                    connected = True

                return _Connection()

            def disconnect(self) -> None:
                calls.append("disconnect")

        window.paper_order_service = _Service()  # type: ignore[assignment]

        window.paper_trading.disconnect()

        assert calls == ["disconnect"]
    finally:
        window.paper_order_service = None
        window.deleteLater()


def test_migrated_reads_no_longer_touch_the_service_directly() -> None:
    """The point of the step: the UI stops reading the service for status."""

    assert "self.paper_order_service.broker_state()" not in _source(
        "_populate_auto_quant_snapshot"
    )
    assert "self.paper_order_service.reconciliation_rows_with_latency" not in _source(
        "_populate_auto_latency_table"
    )
    assert "self.paper_order_service is not None" not in _source(
        "_check_auto_order_channel"
    )


def test_migrated_reads_no_longer_touch_the_workflow_directly() -> None:
    for name in (
        "_apply_paper_workflow_result",
        "_apply_paper_workflow_button_state",
        "_paper_needs_manual_recovery",
        "_poll_auto_quant_orders",
        "_stream_snapshot_received",
        "_auto_candidate_preparation_failed",
    ):
        source = _source(name)
        assert "self.paper_workflow.phase" not in source, name
        assert "self.paper_trading.phase()" in source, name


def test_close_still_blocks_an_unfinalized_session_before_any_disconnect() -> None:
    """The safety ordering survives the move onto the facade."""

    source = _source("closeEvent")

    assert "self.paper_trading.is_finalized()" in source
    assert source.index("self.paper_trading.is_finalized()") < source.index(
        "self.paper_trading.disconnect()"
    )


def test_high_risk_calls_stay_in_the_desktop_on_purpose() -> None:
    """submit/cancel/lease and manual recovery are NOT migrated in this step."""

    assert "paper_workflow.begin_connecting(plan)" in _source("_start_auto_quant")
    assert "paper_workflow.publish_armed(" in _source("_auto_order_service_connected")
    assert "paper_workflow.confirm_manual_resume" in _source(
        "_resume_auto_quant_from_reconciliation"
    )
    assert "paper_workflow.begin_manual_reconciliation()" in _source(
        "_reconnect_auto_order_service"
    )
    assert "paper_workflow.finalize_if_safe()" in _source(
        "_finish_auto_quant_session_if_safe"
    )
