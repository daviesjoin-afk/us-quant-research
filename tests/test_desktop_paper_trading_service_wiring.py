"""Desktop wiring coverage for the Paper trading service boundary.

These tests pin the *wiring*: that ``MainWindow`` builds the service, that the
migrated reads really go through it, that order-service **ownership** now lives
in the service rather than on the window, and that the close path still refuses
to tear Paper down before the session is finalized.  No broker is started; the
window is constructed offscreen and its collaborators are replaced.
"""

from __future__ import annotations

import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.paper_trading_service import PaperTradingService
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
    window.paper_trading.promote_candidate("wiring-test")
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
    """

    for name in (
        "_apply_paper_workflow_result",
        "_publish_execution_controls",
        "_paper_needs_manual_recovery",
        "_poll_auto_quant_orders",
        "_stream_snapshot_received",
        "_auto_candidate_preparation_failed",
    ):
        source = _source(name)
        assert "self.paper_workflow.phase" not in source, name
        assert "self.paper_trading.phase()" in source, name

    # And the render path still reaches the controls through the publisher
    # rather than by writing them itself.
    assert "self._publish_execution_controls()" in _source(
        "_apply_paper_workflow_button_state"
    )


# -- ownership wiring ----------------------------------------------------


def test_starting_a_launch_connects_a_candidate_not_the_active_slot() -> None:
    """A connected broker is not yet the session: it must stay a candidate."""

    source = _source("_start_auto_quant")

    assert "self.paper_trading.connect_candidate(" in source
    assert "IBKRPaperOrderService(" not in source
    assert "self.paper_trading.promote_candidate(" not in source


def test_promotion_happens_only_after_the_launch_is_published() -> None:
    """Order of the irreversible steps: check, publish, then promote.

    A promotion that happened first would leave the session owned by the window
    while the workflow still believes it is only connecting.
    """

    source = _source("_auto_order_service_connected")
    check = source.index("self.paper_trading.ensure_candidate_can_promote(")
    publish = source.index("self.paper_workflow.publish_armed(")
    promote = source.index("self.paper_trading.promote_candidate(")

    assert check < publish < promote


def test_a_late_callback_disposes_only_its_own_candidate() -> None:
    """The stale path must never touch the active slot."""

    source = _source("_reject_unpublished_auto_candidate")

    assert "self.paper_trading.has_candidate(" in source
    assert "self.paper_trading.discard_candidate(" in source
    assert "self.paper_trading.disconnect()" not in source
    assert "self.paper_trading.clear_active(" not in source
    # The rejection must run even when the disconnect fails.
    assert source.index("finally:") < source.index(
        "self.paper_workflow.reject_connecting(plan)"
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
    assert "promote_candidate" not in source


def test_manual_reconciliation_reconnects_the_owned_service_only() -> None:
    source = _source("_reconnect_auto_order_service")

    assert "self.paper_trading.has_order_service()" in source
    assert "self.paper_trading.connect_active()" in source
    assert "IBKRPaperOrderService(" not in source


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

    It exists only so the current arm/publish wiring can hand the workflow an
    order port.  Every other call site would be a new direct dependency on the
    broker adapter, so the allowlist is pinned structurally.
    """

    allowed = {"_auto_order_service_connected"}
    used_in = set()
    for name, member in inspect.getmembers(MainWindow, inspect.isfunction):
        if "candidate_service(" in inspect.getsource(member):
            used_in.add(name)

    assert used_in == allowed

    # And the window must never store the borrowed reference.
    source = _source("_auto_order_service_connected")
    assert "self.paper_order_service" not in source
    for line in source.splitlines():
        if "candidate_service(" in line:
            assert line.lstrip().startswith("service = "), line


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
