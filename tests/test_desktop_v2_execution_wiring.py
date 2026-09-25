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

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QMessageBox

from us_quant.desktop import MainWindow
from us_quant.desktop_v2.pages.execution.models import ExecutionControlState
from us_quant.trading.application.strategy_selection import StrategySelectionPurpose


_APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _silence_dialogs(monkeypatch):
    """A refused close is a modal dialog, and a modal dialog blocks a headless run.

    One case below leaves the workflow in ``HALTED`` and then closes the window.  Since
    v2O-E3 ``closeEvent`` asks the capability what to do about the session and shows the
    verdict, so that session is refused with ``QMessageBox.information`` -- correctly, and
    for ever if nothing answers it.
    """

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

#: Each page signal and the handler that must receive it, in the order the window's
#: wiring table declares them.  A target is a *path*, not a name, because the session
#: controls belong to the capability: ``self.paper_orchestrator.pause`` is where the
#: click lands, and a test that patched the window instead would observe nothing while
#: the button was dead.
#:
#: v2O-E3 moved ``reconcile_requested`` onto the same rule -- the reconciliation is the
#: capability's -- and left the resume *confirmation* on the window, because it is a
#: ``QMessageBox`` and the capability may not import one.
#:
#: G2-B moved the six route intents onto ``execution_orchestrator`` for the same reason:
#: the runtime strategy selection, the preflight, the candidate preparation, the channel
#: probe, the launch request and the stream stop are the AutoQuant route's own decisions,
#: so the click now lands on the object that owns them, rather than on a window method
#: that forwarded.  The stream stop's cross-capability interlocks are still composition's,
#: but they are reached *through* the orchestrator's provider now, not by the click.
EXPECTED_WIRING = (
    ("strategy_selected", "self.execution_orchestrator.select_strategy"),
    ("preflight_inputs_changed", "self.execution_orchestrator.refresh_preflight"),
    ("prepare_requested", "self.execution_orchestrator.request_prepare"),
    ("channel_check_requested", "self.execution_orchestrator.request_channel_check"),
    ("start_requested", "self.execution_orchestrator.request_start"),
    ("stop_stream_requested", "self.execution_orchestrator.request_stop_stream"),
    ("pause_requested", "self.paper_orchestrator.pause"),
    ("resume_requested", "self.paper_orchestrator.resume"),
    ("stop_requested", "self.paper_orchestrator.stop"),
    ("reconcile_requested", "self.paper_orchestrator.reconcile"),
    ("resume_reconciliation_requested", "self._confirm_paper_reconciliation_resume"),
)

#: The buttons that must reach a handler, in click order.
CLICK_ORDER = (
    ("prepare_button", "self.execution_orchestrator.request_prepare"),
    ("channel_check_button", "self.execution_orchestrator.request_channel_check"),
    ("start_button", "self.execution_orchestrator.request_start"),
    ("stop_stream_button", "self.execution_orchestrator.request_stop_stream"),
    ("pause_button", "self.paper_orchestrator.pause"),
    ("resume_button", "self.paper_orchestrator.resume"),
    ("stop_button", "self.paper_orchestrator.stop"),
    ("resume_reconciliation_button", "self._confirm_paper_reconciliation_resume"),
)


def _window() -> MainWindow:
    window = MainWindow()
    _APP.processEvents()
    return window


def _handler_owner(window: MainWindow, target: str) -> tuple[object, str]:
    """Resolve ``self.paper_orchestrator.pause`` to ``(orchestrator, "pause")``.

    The capability's own attributes are addressed from the window, so a recorder has to
    be installed on the object the click actually lands on.
    """

    owner, _, name = target.rpartition(".")
    resolved: object = window
    for part in owner.split("."):
        if part != "self":
            resolved = getattr(resolved, part)
    return resolved, name


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
        for signal, target in EXPECTED_WIRING:
            owner, name = _handler_owner(window, target)
            assert hasattr(owner, name), target
            assert hasattr(window.execution_page, signal), signal
    finally:
        window.close()
        window.deleteLater()


def test_a_real_click_reaches_the_window_handler(monkeypatch) -> None:
    """Every session button must land on its handler, once, in order.

    The handlers are recorded rather than invoked, and the connections are then
    rebuilt against the recorders -- so this asserts the page's signals and the
    window's table agree, which is the property a dead button violates.  The
    recorders are installed on the *owner* of each target, which for the ten
    capability controls is a capability: ``paper_orchestrator`` for the four
    Paper ones and ``execution_orchestrator`` for the six route-owned ones.
    """

    window = _window()
    try:
        page = window.execution_page
        seen: list[str] = []
        for _signal, target in EXPECTED_WIRING:
            owner, name = _handler_owner(window, target)
            monkeypatch.setattr(owner, name, lambda name=name: seen.append(name))
        for signal, _target in EXPECTED_WIRING:
            getattr(page, signal).disconnect()
        window._connect_execution_page()

        page.set_control_state(_all_enabled())
        for attribute, _target in CLICK_ORDER:
            getattr(page.controls, attribute).click()
        page.details.reconcile_button.click()

        assert seen == [target.rpartition(".")[2] for _attribute, target in CLICK_ORDER] + [
            "reconcile"
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

    The control reads "is a stream worker running", so the market orchestrator
    has to publish its control state *after* ``worker.start()``: publishing
    before it would leave the button disabled for every start that did not
    happen to be followed by another publish.  The route's
    ``execution_orchestrator.refresh_controls`` is connected to the orchestrator's
    ``controls_changed`` (in ``_connect_execution_page``), which is emitted right
    after that publish.
    """

    import inspect

    from us_quant.desktop_v2.orchestration.market.orchestrator import (
        MarketOrchestrator,
    )

    source = inspect.getsource(MarketOrchestrator.start)
    assert source.index("worker.start()") < source.index(
        "self._render_controls()"
    )
    assert source.index("self._render_controls()") < source.index(
        "self.controls_changed.emit()"
    )


def test_the_window_repaints_the_page_from_the_workflow_truth() -> None:
    """The publisher is the one writer, and it reads the phase through the service.

    G2-B moved the publisher: the route's control state is
    ``ExecutionOrchestrator.refresh_controls``, and it reads
    ``paper_orchestrator.session_control_facts`` -- the capability's own
    interpretation of its phase -- instead of comparing a phase on the window.
    """

    from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase

    window = _window()
    try:
        class _Phase:
            phase = PaperWorkflowPhase.HALTED
            result = None
            reconciliation_evidence = None

        window.paper_workflow = _Phase()  # type: ignore[assignment]
        window.execution_orchestrator.refresh_controls()

        assert window.execution_page.details.reconcile_button.isEnabled()
        assert not window.execution_page.controls.pause_button.isEnabled()
    finally:
        window.close()
        window.deleteLater()


# -- the palette reaches the page -----------------------------------------


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


def _tone_cell(window: MainWindow, row: int = 0):
    """The one candidate cell whose colour comes from the palette.

    A candidate with no quote is the ``warning`` row, so this cell is a direct
    read of which palette the page coloured it with.
    """

    return window.execution_page.details.candidate_table.item(
        row, 6
    ).foreground().color().name()


def test_switching_the_theme_recolours_the_execution_page() -> None:
    """A theme switch must reach the page *and* redraw its toned cells.

    The page colours a status cell from the palette it was handed, and the table
    keeps the rows it was given -- so telling the page about a new palette
    without redrawing would leave every already-drawn row on the old colours.
    This drives the real window, because the bug it guards against was a missing
    call in ``MainWindow._apply_theme``, which no page-level test can see.  Since
    G2-B that call is ``execution_orchestrator.refresh_current`` -- the window no
    longer renders the route -- so the shortlist is planted on the orchestrator
    that retains it.
    """

    window = _window()
    try:
        window.execution_orchestrator._candidates = (_candidate("AAA"),)
        window.execution_orchestrator.refresh_current()

        dark_warning = window.theme.warning
        assert _tone_cell(window) == QColor(dark_warning).name()

        window._apply_theme("light")

        assert window.theme.name == "light"
        light_warning = window.theme.warning
        assert light_warning != dark_warning
        assert _tone_cell(window) == QColor(light_warning).name()
    finally:
        window.close()
        window.deleteLater()


# -- the channel probe owns the route while it runs -----------------------


def _launch_controls_locked(window: MainWindow) -> bool:
    controls = window.execution_page.controls
    return not (
        controls.prepare_button.isEnabled()
        and controls.start_button.isEnabled()
        and controls.channel_check_button.isEnabled()
    )


def test_the_probe_keeps_the_launch_controls_locked_while_it_runs(
    monkeypatch,
) -> None:
    """The channel probe is a launch step: the route stays locked until it ends.

    The probe runs on its own worker, so its lock is its own flag rather than the
    shared one -- and the flag has to be *read*, or the route reopens under a
    probe that is still talking to the broker.  Since G2-B both the flag and the
    read are ``execution_orchestrator``'s: ``_channel_probe_inflight`` feeds
    ``launch_locked``, which is what disables the controls.
    """

    window = _window()
    try:
        monkeypatch.setattr(
            window.execution_orchestrator, "_submit_task", lambda *a, **k: True
        )
        assert not _launch_controls_locked(window)

        window.execution_orchestrator.request_channel_check()

        assert window.execution_orchestrator._channel_probe_inflight
        assert _launch_controls_locked(window)

        window.execution_orchestrator._channel_probe_failed("broker said no")
        assert not window.execution_orchestrator._channel_probe_inflight
        assert not _launch_controls_locked(window)
    finally:
        window.close()
        window.deleteLater()


def test_an_unrelated_task_cannot_release_the_probe(monkeypatch) -> None:
    """Only the probe's own lifecycle may clear the probe's lock.

    A research task finishing or failing used to publish the control state too,
    and it cleared the shared launch flag.  If the probe were riding on that
    flag, the failure of an unrelated scan would silently reopen the route
    mid-probe.  Since G2-B the generic lifecycle reaches no execution state at
    all -- ``_task_failed`` logs and shows a dialog, ``_worker_finished``
    releases the worker -- so the probe's own flag is the only thing that can
    move, and this pins that.
    """

    from PySide6.QtWidgets import QMessageBox

    window = _window()
    try:
        monkeypatch.setattr(
            window.execution_orchestrator, "_submit_task", lambda *a, **k: True
        )
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
        window.execution_orchestrator.request_channel_check()
        assert _launch_controls_locked(window)

        # What an unrelated worker's failure does.
        window._task_failed("scan failed")
        assert window.execution_orchestrator._channel_probe_inflight
        assert _launch_controls_locked(window)

        # And what an unrelated worker finishing does.
        class _Worker:
            resource_group = "scan"

            def isRunning(self) -> bool:
                return False

        window.task_controller._workers.append(_Worker())
        window._worker_finished(window.task_controller._workers[-1])
        assert window.execution_orchestrator._channel_probe_inflight
        assert _launch_controls_locked(window)

        # Only the probe itself, finishing, releases the route.
        window.execution_orchestrator._channel_probe_failed("done")
        assert not _launch_controls_locked(window)
    finally:
        window.close()
        window.deleteLater()


def test_a_second_probe_request_does_not_release_the_first(monkeypatch) -> None:
    """A refused second worker must not clear the running probe's lock.

    The broker resource group serializes probes, so a second request cannot be
    admitted -- the retry path is turned away before it submits anything, and
    that refusal belongs to the attempt that never started, not to the probe that
    is still running.
    """

    window = _window()
    try:
        monkeypatch.setattr(
            window.execution_orchestrator, "_submit_task", lambda *a, **k: True
        )
        window.execution_orchestrator.request_channel_check()
        assert window.execution_orchestrator._channel_probe_inflight

        # The retry path: the group is busy, so no worker is admitted.
        monkeypatch.setattr(
            window.execution_orchestrator, "_submit_task", lambda *a, **k: False
        )
        window.execution_orchestrator.request_channel_check()

        assert window.execution_orchestrator._channel_probe_inflight
        assert _launch_controls_locked(window)
    finally:
        window.close()
        window.deleteLater()
