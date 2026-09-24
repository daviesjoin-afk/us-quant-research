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
    """The UI stops reading the order service for status.

    v2O-E3 deleted ``_finish_auto_quant_session_if_safe`` with the rest of the
    finalization sequencing, so the list follows the window methods that remain -- and
    the capability's own release path is checked to read through the service facade
    rather than through a raw order-service handle.
    """

    for name in (
        "_render_auto_quant_snapshot",
        "_check_auto_order_channel",
        "_publish_execution_controls",
        "_on_paper_session_finalized",
        "closeEvent",
    ):
        assert "self.paper_order_service" not in _source(name), name

    release = _orchestrator_source("_release_paper_ownership_if_proven")
    for forbidden in (
        "self._order_service",
        "self._active_service",
        "candidate_service(",
        "connection_snapshot",
    ):
        assert forbidden not in release, forbidden


def test_migrated_reads_no_longer_touch_the_workflow_directly() -> None:
    """Phase reads go through the service, never the controller.

    The one place that turns the phase into control state is the publisher; the
    handlers that used to read it themselves now call that instead, so the
    assertion moved with the read rather than being dropped.

    Two entries changed shape in v2O-E2 and three more in v2O-E3.  The result handler
    and the market fan-out no longer read a Paper phase *at all* -- whether a live
    session wants this fact is the capability's question now -- and v2O-E3 added three
    more such handlers: the close, the recovery announcement and the finished-session
    render.  For all of them the claim is the stronger one: they read neither the
    workflow's phase nor the service's.
    """

    for name in (
        "_publish_execution_controls",
        "_auto_candidate_preparation_failed",
    ):
        source = _source(name)
        assert "self.paper_workflow.phase" not in source, name
        assert "self.paper_trading.phase()" in source, name

    for name in (
        "_on_paper_result_changed",
        "_on_market_snapshot_changed",
        "closeEvent",
        "_on_paper_manual_recovery_required",
        "_on_paper_session_finalized",
        "_confirm_paper_reconciliation_resume",
        "_apply_paper_workflow_button_state",
    ):
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
    """Disconnect, reserve the slot, ask the workflow, then commit -- on the new owner.

    v2O-E3 moved the sequencing into ``PaperOrchestrator``, so the claim reads there.  It
    was never about *where* the calls were written: it is that a successful disconnect
    proves nothing, and ``finalize_if_safe`` is the only thing allowed to release PAPER
    and, therefore, the only thing that may precede dropping the slot.

    The order is now *disconnect, reserve, ask, commit* rather than
    disconnect, ask, clear.  ``finalize_if_safe`` is a check-and-commit call on a
    canonical owner whose ``True`` has already released the execution lease, so a slot
    that could still refuse to be dropped after it would leave PAPER released with the
    ownership held -- E1's ownerless-session state, reached from the other end.
    """

    source = _orchestrator_source("_release_paper_ownership_if_proven")
    disconnect = source.index("self._paper_trading.disconnect()")
    reserve = source.index("self._paper_trading.reserve_active_release()")
    finalize = source.index("if not self._workflow.finalize_if_safe():", reserve)
    commit = source.index("self._paper_trading.commit_active_release(reservation)")

    assert disconnect < reserve < finalize < commit
    # And a refusal from the gate gives the lock back instead of dropping the slot.
    assert (
        source.index("self._paper_trading.cancel_active_release(reservation)")
        < commit
    )


def test_close_still_blocks_an_unfinalized_session_before_any_disconnect() -> None:
    """The safety ordering survives the move of ownership into the capability."""

    source = _source("closeEvent")

    assert "self.paper_orchestrator.prepare_shutdown()" in source
    assert source.index("self.paper_orchestrator.prepare_shutdown()") < source.index(
        "self.runtime_supervisor.shutdown()"
    )
    # The window no longer disconnects Paper at all: the only disconnect is the
    # capability's, and it sits behind the same gate.
    assert "self.paper_trading.disconnect()" not in source


def test_close_releases_ownership_only_after_a_successful_disconnect() -> None:
    source = _orchestrator_source("_release_paper_ownership_if_proven")

    assert source.index("self._paper_trading.disconnect()") < source.index(
        "self._paper_trading.commit_active_release(reservation)"
    )


def test_the_order_channel_check_never_owns_the_channel_it_probes() -> None:
    source = _source("_check_auto_order_channel")

    assert "self.paper_trading.probe_order_channel(" in source
    assert "connect_candidate" not in source
    assert "reserve_candidate_promotion" not in source
    assert "commit_candidate_promotion" not in source


def test_manual_reconciliation_reconnects_the_owned_service_only() -> None:
    source = _orchestrator_source("reconcile")

    assert "self._paper_trading.has_order_service()" in source
    assert "self._paper_trading.connect_active()" in source
    assert "IBKRPaperOrderService(" not in source
    # The reconnect is conditional on the *active* service being down, and it is the only
    # thing here that touches the connection -- nothing creates, promotes or disconnects.
    assert "if not self._paper_trading.is_connected():" in source
    for forbidden in (
        "connect_candidate",
        "reserve_candidate_promotion",
        "commit_candidate_promotion",
        "discard_candidate",
        "disconnect",
    ):
        assert forbidden not in source, forbidden


def test_high_risk_calls_now_belong_to_the_capability() -> None:
    """v2O-E3 moved the recovery and finalization calls off the window.

    This guard read the other way round while E1 and E2 were the only rounds landed: it
    asserted the window still owned every manual-recovery and finalization call, so a
    later round could not claim the move had happened early.  It has happened now, so the
    assertion inverts -- each call must be *absent* from the window and *present* on the
    capability.  Inverting rather than deleting keeps the same protection in both
    directions, and the calls are enumerated so none can quietly stay behind.
    """

    workflow_calls = (
        "begin_manual_reconciliation",
        "complete_manual_reconciliation",
        "fail_manual_reconciliation",
        "confirm_manual_resume",
        "capture_finalization_evidence",
        "confirm_finalization_after_disconnect",
        "fail_finalization_refresh",
        "finalize_if_safe",
    )
    window = inspect.getsource(MainWindow)
    for call in workflow_calls:
        assert f"paper_workflow.{call}" not in window, call
    for call in ("prepare_shutdown", "reconcile", "confirm_reconciliation_resume"):
        assert call in inspect.getsource(PaperOrchestrator), call

    assert "self._workflow.begin_connecting(" in _orchestrator_source("start")
    assert "self._paper_trading.commit_candidate_promotion(" in _orchestrator_source(
        "_arm_and_publish"
    )
    assert "self._workflow.publish_armed(" in _orchestrator_source(
        "_arm_and_publish"
    )
    assert "confirm_manual_resume(" in _orchestrator_source(
        "confirm_reconciliation_resume"
    )
    assert "self._workflow.begin_manual_reconciliation()" in _orchestrator_source(
        "reconcile"
    )
    assert "self._workflow.finalize_if_safe()" in _orchestrator_source(
        "_release_paper_ownership_if_proven"
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


@pytest.mark.parametrize(
    "name",
    [
        "closeEvent",
        "_on_paper_session_finalized",
        "_on_paper_manual_recovery_required",
        "_confirm_paper_reconciliation_resume",
        "_release_paper_ownership_if_proven",
        "_start_finalization",
        "prepare_shutdown",
        "reconcile",
    ],
)
def test_no_path_hands_the_order_service_back_to_the_window(name: str) -> None:
    """Ownership must never be reassigned onto the window again.

    Read on the window for the window's methods and on the capability for the ones
    v2O-E3 moved, so the claim follows each owner rather than being dropped for the
    half that changed.
    """

    owner = (
        MainWindow
        if name in {"closeEvent", "_on_paper_session_finalized",
                    "_on_paper_manual_recovery_required",
                    "_confirm_paper_reconciliation_resume"}
        else PaperOrchestrator
    )
    assert "paper_order_service =" not in inspect.getsource(getattr(owner, name))


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

        window.paper_orchestrator._finalization_failed("zero-state proof failed")

        assert workflow.fail_calls == 1
        # Still owned: the disconnect was not a finalization.
        assert window.paper_trading.has_order_service() is True
    finally:
        window.deleteLater()
