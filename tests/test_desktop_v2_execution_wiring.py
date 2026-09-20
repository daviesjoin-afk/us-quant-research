"""Wiring tests: the real window's execution route, end to end.

The page's own tests prove it emits; these prove the *window* is listening.  A
signal that nobody connected is the classic way a UI migration ships a dead
button, and it is invisible to a test that only checks the page.

The clicks are driven on a real ``MainWindow`` with the handlers replaced by
recorders.  Replacing them is not a shortcut around the wiring -- the wiring is
exactly what is being checked -- it is what keeps the test from reaching a
broker: the handlers under test are the ones that would *start* a session, and
the page cannot run them itself.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.desktop_v2.pages.execution.models import ExecutionControlState
from us_quant.trading.application.strategy_selection import StrategySelectionPurpose


_APP = QApplication.instance() or QApplication([])

#: Each page signal and the window method that must receive it, in the order the
#: window's wiring table declares them.
EXPECTED_WIRING = (
    ("strategy_selected", "_auto_strategy_selected"),
    ("preflight_inputs_changed", "_refresh_auto_quant_preflight"),
    ("prepare_requested", "_prepare_auto_quant_candidates"),
    ("channel_check_requested", "_check_auto_order_channel"),
    ("start_requested", "_confirm_and_start_auto_quant"),
    ("stop_stream_requested", "_stop_auto_market_data"),
    ("pause_requested", "_pause_auto_quant_entries"),
    ("resume_requested", "_resume_auto_quant_entries"),
    ("stop_requested", "_stop_auto_quant"),
    ("reconcile_requested", "_reconnect_auto_order_service"),
    ("resume_reconciliation_requested", "_resume_auto_quant_from_reconciliation"),
)

#: The buttons that must reach a handler, in click order.
CLICK_ORDER = (
    ("prepare_button", "_prepare_auto_quant_candidates"),
    ("channel_check_button", "_check_auto_order_channel"),
    ("start_button", "_confirm_and_start_auto_quant"),
    ("stop_stream_button", "_stop_auto_market_data"),
    ("pause_button", "_pause_auto_quant_entries"),
    ("resume_button", "_resume_auto_quant_entries"),
    ("stop_button", "_stop_auto_quant"),
    ("resume_reconciliation_button", "_resume_auto_quant_from_reconciliation"),
)


def _window() -> MainWindow:
    window = MainWindow()
    _APP.processEvents()
    return window


def _all_enabled() -> ExecutionControlState:
    return ExecutionControlState(
        prepare_enabled=True,
        start_enabled=True,
        channel_check_enabled=True,
        strategy_combo_enabled=True,
        candidate_limit_enabled=True,
        capital_limit_enabled=True,
        arm_confirm_enabled=True,
        pause_enabled=True,
        resume_enabled=True,
        stop_enabled=True,
        stop_stream_enabled=True,
        reconcile_enabled=True,
        resume_reconciliation_enabled=True,
    )


def test_every_execution_intent_has_a_handler_on_both_sides() -> None:
    window = _window()
    try:
        for signal, handler in EXPECTED_WIRING:
            assert hasattr(MainWindow, handler), handler
            assert hasattr(window.execution_page, signal), signal
    finally:
        window.close()
        window.deleteLater()


def test_a_real_click_reaches_the_window_handler(monkeypatch) -> None:
    """Every session button must land on its handler, once, in order.

    The handlers are recorded rather than invoked, and the connections are then
    rebuilt against the recorders -- so this asserts the page's signals and the
    window's table agree, which is the property a dead button violates.
    """

    window = _window()
    try:
        page = window.execution_page
        seen: list[str] = []
        for _signal, handler in EXPECTED_WIRING:
            monkeypatch.setattr(
                window, handler, lambda handler=handler: seen.append(handler)
            )
        for signal, _handler in EXPECTED_WIRING:
            getattr(page, signal).disconnect()
        window._connect_execution_page()

        page.set_control_state(_all_enabled())
        for attribute, _handler in CLICK_ORDER:
            getattr(page.controls, attribute).click()
        page.details.reconcile_button.click()

        assert seen == [handler for _attribute, handler in CLICK_ORDER] + [
            "_reconnect_auto_order_service"
        ]
    finally:
        window.close()
        window.deleteLater()


def test_the_route_serves_the_page_and_not_a_builder() -> None:
    """The shell's execution route is the page object itself."""

    window = _window()
    try:
        assert window.shell.page("execution") is window.execution_page
    finally:
        window.close()
        window.deleteLater()


def test_the_combo_choice_reaches_the_selection_service() -> None:
    """The page's choice is recorded where the runtime reads it, not on screen."""

    window = _window()
    try:
        combo = window.execution_page.controls.strategy_combo
        assert combo.count() >= 2

        combo.setCurrentIndex(combo.count() - 1)
        _APP.processEvents()

        selected = window.strategy_selection.selected(
            StrategySelectionPurpose.AUTO_ROTATION
        )
        assert selected is not None
        assert selected.version_id == combo.itemData(combo.count() - 1)
    finally:
        window.close()
        window.deleteLater()


def test_the_preflight_repaints_when_an_input_moves() -> None:
    """The preflight is a view of the inputs, so moving one must repaint it."""

    window = _window()
    try:
        page = window.execution_page
        page.controls.candidate_limit_input.setValue(12)
        _APP.processEvents()

        # The line carries the tally the preflight produced, which only happens
        # if the handler ran.
        assert "准备检查" in page.controls.preflight_label.text()
    finally:
        window.close()
        window.deleteLater()


def test_the_stop_stream_control_opens_once_the_thread_is_running() -> None:
    """A direct start from the market page must enable the route's stop control.

    The control reads "is a stream worker running", so the publisher has to run
    *after* ``worker.start()``: publishing before it would leave the button
    disabled for every start that did not happen to be followed by another
    publish.
    """

    import inspect

    source = inspect.getsource(MainWindow._start_stream)
    assert source.index("worker.start()") < source.index(
        "self._publish_execution_controls()"
    )


def test_the_window_repaints_the_page_from_the_workflow_truth() -> None:
    """The publisher is the one writer, and it reads the phase through the service."""

    from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase

    window = _window()
    try:
        class _Phase:
            phase = PaperWorkflowPhase.HALTED
            result = None
            reconciliation_evidence = None

        window.paper_workflow = _Phase()  # type: ignore[assignment]
        window._apply_paper_workflow_button_state()

        assert window.execution_page.details.reconcile_button.isEnabled()
        assert not window.execution_page.controls.pause_button.isEnabled()
    finally:
        window.close()
        window.deleteLater()
