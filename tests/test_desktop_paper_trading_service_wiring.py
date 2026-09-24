"""Desktop wiring coverage for the Paper trading service boundary.

These tests pin the *wiring*: that ``MainWindow`` builds the service, that the
migrated reads really go through it, that order-service **ownership** now lives
in the service rather than on the window, and that the close path still refuses
to tear Paper down before the session is finalized.  No broker is started; the
window is constructed offscreen and its collaborators are replaced.

v2O-E1 moved the *launch* sequence -- connect, stale-callback decision, arm,
reserve, publish, commit -- out of ``MainWindow`` and into ``PaperOrchestrator``.
The structural assertions below therefore read the capability rather than the
window.  Their safety meaning is unchanged and deliberately not weakened: the same
call must exist, in the same order, on the new owner.  Only the object being
inspected moved, which is exactly what the extraction claims and what these guards
exist to verify.
"""

from __future__ import annotations

import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.desktop_v2.orchestration.paper import PaperOrchestrator
from us_quant.trading.application.paper import PaperTradingService
from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase


_APP = QApplication.instance() or QApplication([])


class _Connection:
    def __init__(self, connected: bool) -> None:
        self.connected = connected


class _FakeOrderService:
    """Stands in for ``IBKRPaperOrderService`` without a broker."""

    def __init__(self, *, connected: bool = False) -> None:
        self.connected = connected
        self.connect_calls = 0
        self.disconnect_calls = 0

    def connect(self) -> object:
        self.connect_calls += 1
        self.connected = True
        return self

    def connection_snapshot(self) -> _Connection:
        return _Connection(self.connected)

    def broker_state(self) -> object:
        return object()

    def reconciliation_rows_with_latency(
        self, *, session_id: str | None = None, limit: int = 1000
    ) -> tuple:
        return ()

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False


def _window() -> MainWindow:
    window = MainWindow()
    _APP.processEvents()
    return window


def _source(name: str) -> str:
    return inspect.getsource(getattr(MainWindow, name))


def _orchestrator_source(name: str) -> str:
    """The source of one method on the launch capability, its owner since v2O-E1.

    The assertions that read this used to read ``MainWindow``.  The move is the
    point of the round, so the guards follow the owner instead of being dropped --
    a deleted assertion would be a silently weakened safety check.
    """

    return inspect.getsource(getattr(PaperOrchestrator, name))


def _install_fake_service(window: MainWindow, *, connected: bool = False):
    """Own a fake order service through the real service, exactly as production does.

    The window's service is rebuilt with an injected factory (the same seam the
    unit tests use), then a candidate is connected and promoted -- the real code
    path, so ownership state is genuine rather than poked in.
    """

    fake = _FakeOrderService(connected=connected)
    window.paper_trading = PaperTradingService(
        workflow_getter=lambda: window.paper_workflow,
        order_service_factory=lambda config, *, repository, extended_hours_enabled: fake,
    )
    window.paper_trading.connect_candidate(
        "wiring-test",
        config=object(),
        repository=object(),
        extended_hours_enabled=False,
    )
    window.paper_trading.commit_candidate_promotion(
        window.paper_trading.reserve_candidate_promotion("wiring-test")
    )
    if connected:
        window.paper_trading.connect_active()
    return fake


def test_main_window_builds_the_paper_trading_service() -> None:
    window = _window()
    try:
        assert isinstance(window.paper_trading, PaperTradingService)
    finally:
        window.deleteLater()


def test_the_window_no_longer_owns_an_order_service_attribute() -> None:
    """Ownership moved into the service; the old slot must be gone, not shadowed."""

    window = _window()
    try:
        assert not hasattr(window, "paper_order_service")
        assert window.paper_trading.has_order_service() is False
    finally:
        window.deleteLater()


def test_the_desktop_no_longer_constructs_the_order_service() -> None:
    """The point of the step: the UI stops building the broker adapter."""

    source = inspect.getsource(MainWindow)

    assert "IBKRPaperOrderService(" not in source
    assert "self.paper_order_service" not in source


def test_the_service_reads_the_live_workflow_not_a_snapshot() -> None:
    """Replacing the controller must be visible through the service.

    The safety tests in ``test_desktop_runtime_teardown.py`` swap
    ``window.paper_workflow`` to drive the halted and refused-close paths; if the
    service captured the controller at construction those tests would pass while
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


def test_the_service_reads_the_owned_order_service() -> None:
    window = _window()
    try:
        assert window.paper_trading.has_order_service() is False
        assert window.paper_trading.is_connected() is False

        _install_fake_service(window, connected=True)

        assert window.paper_trading.has_order_service() is True
        assert window.paper_trading.is_connected() is True
    finally:
        window.deleteLater()


def test_a_status_read_goes_through_the_service() -> None:
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


def test_disconnect_goes_through_the_service_exactly_once() -> None:
    window = _window()
    try:
        fake = _install_fake_service(window, connected=True)

        window.paper_trading.disconnect()

        assert fake.disconnect_calls == 1
        # Disconnecting is not releasing: the session is still owned.
        assert window.paper_trading.has_order_service() is True
    finally:
        window.deleteLater()


def test_migrated_reads_no_longer_touch_the_order_service_directly() -> None:
    """The UI stops reading the order service for status."""

    for name in (
        "_render_auto_quant_snapshot",
        "_check_auto_order_channel",
        "_finish_auto_quant_session_if_safe",
    ):
        assert "self.paper_order_service" not in _source(name), name


def test_migrated_reads_no_longer_touch_the_workflow_directly() -> None:
    """Phase reads go through the service, never the controller.

    The one place that turns the phase into control state is the publisher; the
    handlers that used to read it themselves now call that instead, so the
    assertion moved with the read rather than being dropped.

    Two entries changed shape in v2O-E2.  The result handler and the market fan-out
    no longer read a Paper phase *at all* -- whether a live session wants this fact is
    the capability's question now -- so for those the claim is the stronger one: they
    read neither the workflow's phase nor the service's.
    """

    for name in (
        "_publish_execution_controls",
        "_paper_needs_manual_recovery",
        "_handle_paper_e3_result_bridge",
        "_auto_candidate_preparation_failed",
    ):
        source = _source(name)
        assert "self.paper_workflow.phase" not in source, name
        assert "self.paper_trading.phase()" in source, name

    for name in ("_on_paper_result_changed", "_on_market_snapshot_changed"):
        source = _source(name)
        assert "self.paper_workflow.phase" not in source, name
        assert "self.paper_trading.phase()" not in source, name

    # And the render path still reaches the controls through the publisher
    # rather than by writing them itself.
    assert "self._publish_execution_controls()" in _source(
        "_apply_paper_workflow_button_state"
    )


# -- ownership wiring ----------------------------------------------------


def test_starting_a_launch_connects_a_candidate_not_the_active_slot() -> None:
    """A connected broker is not yet the session: it must stay a candidate.

    Read on ``PaperOrchestrator.start`` since v2O-E1 -- the launch sequence is the
    capability's now.  The claim is unchanged: the connect creates a *candidate*,
    and never promotes it in the same breath.
    """

    source = _orchestrator_source("start")

    assert "self._paper_trading.connect_candidate(" in source
    assert "IBKRPaperOrderService(" not in source
    assert "self._paper_trading.reserve_candidate_promotion(" not in source
    assert "self._paper_trading.commit_candidate_promotion(" not in source


def test_the_promotion_is_taken_before_publication_and_ended_after_it() -> None:
    """Order of the irreversible steps: take the slot, publish, then end the claim.

    The retired order checked promotability, published, and only *then* promoted --
    so the slot was still empty while a session that expects an owner came into
    being, and a refusal in that last step stranded a running session with an armed
    broker channel and no owner.  Taking the promotion first is what makes
    ``RUNNING`` imply an owner.  Pinned on
    ``PaperOrchestrator._arm_and_publish`` since v2O-E1.
    """

    source = _orchestrator_source("_arm_and_publish")
    reserve = source.index("self._paper_trading.reserve_candidate_promotion(")
    publish = source.index("self._workflow.publish_armed(")
    commit = source.index("self._paper_trading.commit_candidate_promotion(")

    assert reserve < publish < commit


def test_a_late_callback_disposes_only_its_own_candidate() -> None:
    """The stale path must never touch the active slot."""

    source = _orchestrator_source("_discard_candidate")

    assert "self._paper_trading.has_candidate(" in source
    assert "self._paper_trading.discard_candidate(" in source
    assert "self._paper_trading.disconnect()" not in source
    assert "self._paper_trading.clear_active(" not in source
    # The rejection must run even when the disconnect fails.
    assert source.index("finally:") < source.index(
        "self._workflow.reject_connecting(request.plan)"
    )


def test_finalization_releases_ownership_only_after_the_workflow_agrees() -> None:
    source = _source("_finish_auto_quant_session_if_safe")
    disconnect = source.index("self.paper_trading.disconnect()")
    finalize = source.index("self.paper_workflow.finalize_if_safe()")
    clear = source.index("self.paper_trading.clear_active()")

    assert disconnect < finalize < clear


def test_close_still_blocks_an_unfinalized_session_before_any_disconnect() -> None:
    """The safety ordering survives the move of ownership into the service."""

    source = _source("closeEvent")

    assert "self.paper_trading.is_finalized()" in source
    assert source.index("self.paper_trading.is_finalized()") < source.index(
        "self.paper_trading.disconnect()"
    )


def test_close_releases_ownership_only_after_a_successful_disconnect() -> None:
    source = _source("closeEvent")

    assert source.index("self.paper_trading.disconnect()") < source.index(
        "self.paper_trading.clear_active()"
    )


def test_the_order_channel_check_never_owns_the_channel_it_probes() -> None:
    source = _source("_check_auto_order_channel")

    assert "self.paper_trading.probe_order_channel(" in source
    assert "connect_candidate" not in source
    assert "reserve_candidate_promotion" not in source
    assert "commit_candidate_promotion" not in source


def test_manual_reconciliation_reconnects_the_owned_service_only() -> None:
    source = _source("_reconnect_auto_order_service")

    assert "self.paper_trading.has_order_service()" in source
    assert "self.paper_trading.connect_active()" in source
    assert "IBKRPaperOrderService(" not in source


def test_high_risk_calls_stay_in_the_desktop_on_purpose() -> None:
    """submit/cancel/lease and manual recovery are NOT migrated in this step.

    v2O-E1 moved the *launch* only.  ``begin_connecting``, ``publish_armed`` and
    ``reject_connecting`` now live on the orchestrator, but every manual-recovery
    and finalization call is still the window's and must stay so until v2O-E3 --
    which is why the assertions below split by owner rather than by feature.
    """

    assert "self._workflow.begin_connecting(" in _orchestrator_source("start")
    assert "self._paper_trading.commit_candidate_promotion(" in _orchestrator_source(
        "_arm_and_publish"
    )
    assert "self._workflow.publish_armed(" in _orchestrator_source(
        "_arm_and_publish"
    )
    assert "paper_workflow.confirm_manual_resume" in _source(
        "_resume_auto_quant_from_reconciliation"
    )
    assert "paper_workflow.begin_manual_reconciliation()" in _source(
        "_reconnect_auto_order_service"
    )
    assert "paper_workflow.finalize_if_safe()" in _source(
        "_finish_auto_quant_session_if_safe"
    )


def test_the_service_is_not_rebuilt_on_every_read() -> None:
    """The window builds the service once, in the initializer."""

    window = _window()
    try:
        first = window.paper_trading
        _APP.processEvents()
        assert window.paper_trading is first
    finally:
        window.deleteLater()


@pytest.mark.parametrize("name", ["closeEvent", "_finish_auto_quant_session_if_safe"])
def test_no_path_hands_the_order_service_back_to_the_window(name: str) -> None:
    """Ownership must never be reassigned onto the window again."""

    assert "paper_order_service =" not in _source(name)


def test_the_raw_candidate_bridge_is_confined_to_one_critical_wiring_point() -> None:
    """``candidate_service()`` is a borrowed reference, not a second owner.

    It exists only so the arm/publish wiring can hand the workflow an order port.
    Every other call site would be a new direct dependency on the broker adapter, so
    the allowlist is pinned structurally.

    v2O-E1 moved that one call from ``MainWindow._auto_order_service_connected`` to
    ``PaperOrchestrator._arm_and_publish``.  Both halves of the guard survive the
    move: the window may no longer call it *at all*, and the capability may call it
    in exactly one place, into a local.
    """

    # The window lost the call with the launch sequence.
    window_used_in = set()
    for name, member in inspect.getmembers(MainWindow, inspect.isfunction):
        if "candidate_service(" in inspect.getsource(member):
            window_used_in.add(name)
    assert window_used_in == set(), window_used_in

    # And the capability has exactly one call site.
    allowed = {"_arm_and_publish"}
    used_in = set()
    for name, member in inspect.getmembers(
        PaperOrchestrator, inspect.isfunction
    ):
        if "candidate_service(" in inspect.getsource(member):
            used_in.add(name)

    assert used_in == allowed

    # And the borrowed reference is never stored.
    source = _orchestrator_source("_arm_and_publish")
    for line in source.splitlines():
        if "candidate_service(" in line:
            assert line.lstrip().startswith("service = "), line
    for name, member in inspect.getmembers(
        PaperOrchestrator, inspect.isfunction
    ):
        assert "self._candidate_service" not in inspect.getsource(member), name


def test_a_failed_finalization_keeps_the_active_ownership() -> None:
    """HALTED still needs the same order/journal session context.

    The §21 scenario: the session reached STOPPING, the zero-state proof ran and
    the connection was closed, but confirmation failed.  Ownership must survive
    an already-disconnected service -- which is exactly the state where a stray
    ``clear_active()`` would succeed silently and drop the session context that
    manual reconciliation still needs.
    """

    window = _window()
    try:
        _install_fake_service(window, connected=False)
        # The connection is already gone, as it is when confirmation fails.
        window.paper_trading.disconnect()
        assert window.paper_trading.is_connected() is False
        assert window.paper_trading.has_order_service() is True

        class _HaltedWorkflow:
            phase = PaperWorkflowPhase.HALTED
            reconciliation_evidence = None
            fail_calls = 0

            def fail_finalization_refresh(self) -> None:
                type(self).fail_calls += 1

        workflow = _HaltedWorkflow()
        window.paper_workflow = workflow  # type: ignore[assignment]

        window._paper_finalization_failed("zero-state proof failed")

        assert workflow.fail_calls == 1
        # Still owned: the disconnect was not a finalization.
        assert window.paper_trading.has_order_service() is True
    finally:
        window.deleteLater()
