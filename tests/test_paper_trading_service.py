"""Unit coverage for the Paper trading service: ownership, reads, lifecycle.

Nothing here touches IBKR: the order service is a local fake built by a fake
factory, so the ownership contract -- candidate registration, promotion,
stale-candidate disposal, and the fail-closed clearing rules -- is pinned
without a broker, a Qt event loop, or a real order service.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from us_quant import paper_trading_service as module
from us_quant.trading.composition.execution import build_execution_candidate
from us_quant.paper_trading_service import (
    PaperReconciliationStatus,
    PaperTradingLifecycleError,
    PaperTradingService,
    PaperTradingSnapshot,
)
from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase


# -- fakes ---------------------------------------------------------------


class _FakeConnection:
    def __init__(self, connected: bool) -> None:
        self.connected = connected


class _FakeState:
    def __init__(self, finalized: bool) -> None:
        self.finalized = finalized


class _FakeResult:
    def __init__(self, finalized: bool) -> None:
        self.state = _FakeState(finalized)


class _FakeService:
    """Records every call so 'exactly once' can be asserted, not assumed."""

    def __init__(
        self,
        *,
        connected: bool = True,
        error: Exception | None = None,
        connect_error: Exception | None = None,
    ) -> None:
        self.connected = connected
        self.error = error
        self.connect_error = connect_error
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.broker_state_calls = 0
        self.row_calls: list[dict] = []
        self.broker_state_value: object = object()
        self.rows: tuple[dict, ...] = ()
        self.connection = object()

    def connect(self) -> object:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True
        return self.connection

    def connection_snapshot(self) -> _FakeConnection:
        return _FakeConnection(self.connected)

    def broker_state(self) -> object:
        self.broker_state_calls += 1
        return self.broker_state_value

    def reconciliation_rows_with_latency(
        self, *, session_id: str | None = None, limit: int = 1000
    ) -> tuple[dict, ...]:
        self.row_calls.append({"session_id": session_id, "limit": limit})
        return self.rows

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        if self.error is not None:
            raise self.error
        self.connected = False


class _FakeFactory:
    """Builds fakes on demand and records exactly how it was called."""

    def __init__(self, *services: object) -> None:
        self._queued = list(services)
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def __call__(
        self,
        config: object,
        *,
        repository: object,
        extended_hours_enabled: bool,
    ) -> object:
        self.calls.append(
            {
                "config": config,
                "repository": repository,
                "extended_hours_enabled": extended_hours_enabled,
            }
        )
        if self.error is not None:
            raise self.error
        if not self._queued:
            raise AssertionError("the factory was called more often than expected")
        return self._queued.pop(0)


class _FakeWorkflow:
    def __init__(
        self,
        *,
        phase: PaperWorkflowPhase = PaperWorkflowPhase.IDLE,
        result: object | None = None,
        evidence: object | None = None,
    ) -> None:
        self.phase = phase
        self.result = result
        self.reconciliation_evidence = evidence


class _Holder:
    """A getter that counts resolutions and can be re-pointed mid-test.

    Counting is what makes "read per call, never cached" falsifiable: a service
    that captured the workflow at construction would resolve exactly once.
    """

    def __init__(self, workflow: _FakeWorkflow | None = None) -> None:
        self.workflow = workflow if workflow is not None else _FakeWorkflow()
        self.calls = 0

    def __call__(self) -> _FakeWorkflow:
        self.calls += 1
        return self.workflow


def _service(
    *,
    factory: object | None = None,
    holder: _Holder | None = None,
) -> PaperTradingService:
    return PaperTradingService(
        workflow_getter=holder or _Holder(),
        order_service_factory=factory or _FakeFactory(),  # type: ignore[arg-type]
    )


def _owned(
    service: _FakeService,
    *,
    factory: _FakeFactory | None = None,
    candidate_id: str = "attempt-1",
    holder: _Holder | None = None,
) -> PaperTradingService:
    """A service whose active slot already holds ``service`` via a real promote."""

    factory = factory if factory is not None else _FakeFactory(service)
    boundary = _service(factory=factory, holder=holder)
    boundary.connect_candidate(
        candidate_id,
        config=object(),
        repository=object(),
        extended_hours_enabled=False,
    )
    boundary.promote_candidate(candidate_id)
    return boundary


def _owned_disconnected(
    service: _FakeService,
    *,
    candidate_id: str = "attempt-1",
) -> PaperTradingService:
    """An owned service whose broker connection has been closed.

    ``connect_candidate`` connects the adapter, so a promoted-but-closed session
    has to be produced by running the real ``disconnect`` -- the same way the
    finalization path leaves it.
    """

    boundary = _owned(service, candidate_id=candidate_id)
    boundary.disconnect()
    return boundary


# -- no order service ----------------------------------------------------


def test_without_a_service_there_is_nothing_connected() -> None:
    boundary = _service()

    assert boundary.has_order_service() is False
    assert boundary.is_connected() is False


def test_disconnect_without_a_service_is_safe_and_does_nothing() -> None:
    boundary = _service()

    boundary.disconnect()

    assert boundary.snapshot().last_error is None


def test_read_only_order_queries_degrade_to_empty_without_a_service() -> None:
    boundary = _service()

    assert boundary.broker_state() is None
    assert boundary.reconciliation_rows_with_latency(session_id="s", limit=10) == ()


def test_connect_active_without_a_service_is_refused_not_invented() -> None:
    """Manual reconciliation must never create a service of its own."""

    boundary = _service()

    with pytest.raises(PaperTradingLifecycleError, match="no active Paper"):
        boundary.connect_active()


# -- with a service ------------------------------------------------------


def test_connected_state_follows_the_active_service() -> None:
    assert _owned(_FakeService(connected=True)).is_connected() is True
    assert _owned_disconnected(_FakeService()).is_connected() is False


def test_connection_state_is_read_from_the_live_service_not_a_copy() -> None:
    service = _FakeService()
    boundary = _owned(service)

    assert boundary.is_connected() is True
    service.connected = False

    assert boundary.is_connected() is False


def test_read_only_order_queries_forward_to_the_active_service() -> None:
    service = _FakeService()
    service.rows = ({"intent_id": "abc"},)
    boundary = _owned(service)

    assert boundary.broker_state() is service.broker_state_value
    assert boundary.reconciliation_rows_with_latency(
        session_id="session-1", limit=100
    ) == ({"intent_id": "abc"},)
    assert service.row_calls == [{"session_id": "session-1", "limit": 100}]


def test_reads_ignore_candidates_until_they_are_promoted() -> None:
    """An unarmed candidate is not the session, even while it is connected."""

    service = _FakeService(connected=True)
    boundary = _service(factory=_FakeFactory(service))

    boundary.connect_candidate(
        "attempt-1", config=object(), repository=object(), extended_hours_enabled=False
    )

    assert boundary.has_candidate("attempt-1") is True
    assert boundary.has_order_service() is False
    assert boundary.is_connected() is False
    assert boundary.broker_state() is None


# -- snapshot ------------------------------------------------------------


def test_snapshot_is_frozen() -> None:
    boundary = _owned(_FakeService(connected=True))

    snapshot = boundary.snapshot()

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.phase = "HALTED"  # type: ignore[misc]


def test_snapshot_carries_only_the_lifecycle_fields() -> None:
    field_names = {field.name for field in dataclasses.fields(PaperTradingSnapshot)}

    assert field_names == {"phase", "connected", "finalized", "last_error"}


def test_snapshot_reports_lifecycle_truth() -> None:
    workflow = _FakeWorkflow(
        phase=PaperWorkflowPhase.RUNNING,
        result=_FakeResult(finalized=False),
    )
    boundary = _owned(_FakeService(connected=True), holder=_Holder(workflow))

    snapshot = boundary.snapshot()

    assert snapshot.phase == "RUNNING"
    assert snapshot.connected is True
    assert snapshot.finalized is False
    assert snapshot.last_error is None


def test_snapshot_does_not_carry_orders_positions_or_fills() -> None:
    """Order truth stays in the existing service and journal."""

    field_names = {field.name for field in dataclasses.fields(PaperTradingSnapshot)}

    for forbidden in ("positions", "orders", "fills", "reconciliation", "pending"):
        assert not any(forbidden in name for name in field_names), forbidden


# -- phase / finalized / reconciliation ----------------------------------


def test_phase_comes_from_the_workflow_controller() -> None:
    holder = _Holder(_FakeWorkflow(phase=PaperWorkflowPhase.RECONCILING_READY))
    boundary = _service(holder=holder)

    assert boundary.phase() is PaperWorkflowPhase.RECONCILING_READY
    assert holder.calls == 1


def test_the_workflow_is_resolved_on_every_call_not_captured() -> None:
    """Replacing the controller must be visible, exactly as it is for the window."""

    holder = _Holder(_FakeWorkflow(phase=PaperWorkflowPhase.IDLE))
    boundary = _service(holder=holder)

    assert boundary.phase() is PaperWorkflowPhase.IDLE

    holder.workflow = _FakeWorkflow(phase=PaperWorkflowPhase.HALTED)

    assert boundary.phase() is PaperWorkflowPhase.HALTED
    assert holder.calls == 2


def test_finalized_requires_the_controllers_own_result() -> None:
    assert (
        _service(holder=_Holder(_FakeWorkflow(result=_FakeResult(True)))).is_finalized()
        is True
    )
    assert (
        _service(holder=_Holder(_FakeWorkflow(result=_FakeResult(False)))).is_finalized()
        is False
    )


def test_a_window_that_never_started_a_session_counts_as_finalized() -> None:
    """Nothing is outstanding, so the close gate must not refuse."""

    assert _service(holder=_Holder(_FakeWorkflow(result=None))).is_finalized() is True


def test_reconciliation_status_reports_awaiting_confirmation() -> None:
    waiting = _service(holder=_Holder(_FakeWorkflow(evidence=object())))
    idle = _service(holder=_Holder(_FakeWorkflow(evidence=None)))

    assert waiting.reconciliation_status() == PaperReconciliationStatus(True)
    assert waiting.reconciliation_status().awaiting_confirmation is True
    assert idle.reconciliation_status().awaiting_confirmation is False


# -- candidate connect ---------------------------------------------------


def test_connect_candidate_connects_once_and_tracks_without_promoting() -> None:
    service = _FakeService()
    factory = _FakeFactory(service)
    boundary = _service(factory=factory)
    config, repository = object(), object()

    connection = boundary.connect_candidate(
        "attempt-7",
        config=config,
        repository=repository,
        extended_hours_enabled=True,
    )

    assert connection is service.connection
    assert service.connect_calls == 1
    assert factory.calls == [
        {"config": config, "repository": repository, "extended_hours_enabled": True}
    ]
    assert boundary.has_candidate("attempt-7") is True
    # Connecting is not permission to own the session.
    assert boundary.has_order_service() is False
    assert boundary.candidate_service("attempt-7") is service


def test_connect_candidate_failure_disconnects_and_registers_nothing() -> None:
    service = _FakeService(connect_error=RuntimeError("no gateway"))
    boundary = _service(factory=_FakeFactory(service))

    with pytest.raises(RuntimeError, match="no gateway"):
        boundary.connect_candidate(
            "attempt-1",
            config=object(),
            repository=object(),
            extended_hours_enabled=False,
        )

    assert service.disconnect_calls == 1
    assert boundary.has_candidate("attempt-1") is False
    assert boundary.has_order_service() is False
    assert "no gateway" in (boundary.snapshot().last_error or "")


def test_constructor_failure_registers_nothing_and_leaves_no_connection() -> None:
    factory = _FakeFactory()
    factory.error = RuntimeError("cannot build")
    boundary = _service(factory=factory)

    with pytest.raises(RuntimeError, match="cannot build"):
        boundary.connect_candidate(
            "attempt-1",
            config=object(),
            repository=object(),
            extended_hours_enabled=False,
        )

    assert boundary.has_candidate("attempt-1") is False
    assert boundary.has_order_service() is False


def test_a_candidate_whose_cleanup_fails_is_still_tracked() -> None:
    """Dropping the reference would abandon a possibly-live broker socket."""

    service = _FakeService(
        connect_error=RuntimeError("connect blew up"),
        error=RuntimeError("disconnect also failed"),
    )
    boundary = _service(factory=_FakeFactory(service))

    with pytest.raises(RuntimeError, match="connect blew up"):
        boundary.connect_candidate(
            "attempt-1",
            config=object(),
            repository=object(),
            extended_hours_enabled=False,
        )

    assert service.disconnect_calls == 1
    assert boundary.has_candidate("attempt-1") is True
    assert "disconnect also failed" in (boundary.snapshot().last_error or "")


def test_a_candidate_id_cannot_be_reused() -> None:
    first, second = _FakeService(), _FakeService()
    boundary = _service(factory=_FakeFactory(first, second))
    boundary.connect_candidate(
        "attempt-1", config=object(), repository=object(), extended_hours_enabled=False
    )

    with pytest.raises(PaperTradingLifecycleError, match="already registered"):
        boundary.connect_candidate(
            "attempt-1",
            config=object(),
            repository=object(),
            extended_hours_enabled=False,
        )

    assert second.connect_calls == 0
    assert boundary.candidate_service("attempt-1") is first


def test_a_candidate_id_must_be_a_non_empty_string() -> None:
    boundary = _service()

    for bad in ("", "   ", None, 7):
        with pytest.raises(PaperTradingLifecycleError, match="non-empty string"):
            boundary.connect_candidate(
                bad,  # type: ignore[arg-type]
                config=object(),
                repository=object(),
                extended_hours_enabled=False,
            )


def test_candidate_service_refuses_an_unknown_id() -> None:
    boundary = _service()

    with pytest.raises(PaperTradingLifecycleError, match="unknown Paper candidate"):
        boundary.candidate_service("nope")


# -- discard -------------------------------------------------------------


def test_discard_disconnects_only_the_named_candidate() -> None:
    active, stale, other = _FakeService(), _FakeService(), _FakeService()
    boundary = _owned(active, factory=_FakeFactory(active, stale, other))
    boundary.connect_candidate(
        "stale", config=object(), repository=object(), extended_hours_enabled=False
    )
    boundary.connect_candidate(
        "other", config=object(), repository=object(), extended_hours_enabled=False
    )

    boundary.discard_candidate("stale")

    assert stale.disconnect_calls == 1
    assert other.disconnect_calls == 0
    assert active.disconnect_calls == 0
    assert boundary.has_candidate("stale") is False
    assert boundary.has_candidate("other") is True
    assert boundary.has_order_service() is True
    assert boundary.is_connected() is True


def test_discard_failure_keeps_the_candidate_tracked_and_raises() -> None:
    stale = _FakeService(error=RuntimeError("socket stuck"))
    boundary = _service(factory=_FakeFactory(stale))
    boundary.connect_candidate(
        "stale", config=object(), repository=object(), extended_hours_enabled=False
    )

    with pytest.raises(RuntimeError, match="socket stuck"):
        boundary.discard_candidate("stale")

    assert boundary.has_candidate("stale") is True
    assert "socket stuck" in (boundary.snapshot().last_error or "")


def test_discard_refuses_an_unknown_id() -> None:
    boundary = _service()

    with pytest.raises(PaperTradingLifecycleError, match="unknown Paper candidate"):
        boundary.discard_candidate("nope")


# -- promote -------------------------------------------------------------


def test_promote_moves_the_candidate_into_the_active_slot() -> None:
    service = _FakeService()
    boundary = _service(factory=_FakeFactory(service))
    boundary.connect_candidate(
        "attempt-1", config=object(), repository=object(), extended_hours_enabled=False
    )

    boundary.promote_candidate("attempt-1")

    assert boundary.has_order_service() is True
    assert boundary.has_candidate("attempt-1") is False
    assert boundary.is_connected() is True


def test_promote_refuses_to_replace_a_live_active_service() -> None:
    old, new = _FakeService(), _FakeService()
    boundary = _owned(old, factory=_FakeFactory(old, new))
    boundary.connect_candidate(
        "attempt-2", config=object(), repository=object(), extended_hours_enabled=False
    )

    with pytest.raises(PaperTradingLifecycleError, match="already active"):
        boundary.promote_candidate("attempt-2")

    # The old owner is untouched, and the rejected candidate is still disposable.
    assert old.disconnect_calls == 0
    assert boundary.broker_state() is old.broker_state_value
    boundary.discard_candidate("attempt-2")
    assert new.disconnect_calls == 1
    assert boundary.has_order_service() is True


def test_ensure_can_promote_is_a_pure_check() -> None:
    service = _FakeService()
    boundary = _service(factory=_FakeFactory(service))
    boundary.connect_candidate(
        "attempt-1", config=object(), repository=object(), extended_hours_enabled=False
    )

    boundary.ensure_candidate_can_promote("attempt-1")

    # Nothing moved: the check must not promote, discard, or connect.
    assert boundary.has_candidate("attempt-1") is True
    assert boundary.has_order_service() is False
    assert service.disconnect_calls == 0
    assert service.connect_calls == 1


def test_ensure_can_promote_reports_an_occupied_slot_before_it_is_too_late() -> None:
    old, new = _FakeService(), _FakeService()
    boundary = _owned(old, factory=_FakeFactory(old, new))
    boundary.connect_candidate(
        "attempt-2", config=object(), repository=object(), extended_hours_enabled=False
    )

    with pytest.raises(PaperTradingLifecycleError, match="already active"):
        boundary.ensure_candidate_can_promote("attempt-2")


def test_ensure_can_promote_refuses_an_unknown_candidate() -> None:
    boundary = _service()

    with pytest.raises(PaperTradingLifecycleError, match="unknown Paper candidate"):
        boundary.ensure_candidate_can_promote("nope")


# -- active lifecycle ----------------------------------------------------


def test_disconnect_does_not_clear_ownership() -> None:
    """A dead socket is not a released session; reconciliation still needs it."""

    service = _FakeService()
    boundary = _owned(service)

    boundary.disconnect()

    assert service.disconnect_calls == 1
    assert boundary.has_order_service() is True
    assert boundary.is_connected() is False


def test_disconnect_failure_is_recorded_keeps_ownership_and_still_raises() -> None:
    service = _FakeService(error=RuntimeError("socket refused"))
    boundary = _owned(service)

    with pytest.raises(RuntimeError, match="socket refused"):
        boundary.disconnect()

    assert service.disconnect_calls == 1
    assert boundary.has_order_service() is True
    assert "socket refused" in (boundary.snapshot().last_error or "")


def test_disconnect_never_reports_silent_success_on_failure() -> None:
    boundary = _owned(_FakeService(error=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        boundary.disconnect()

    assert boundary.snapshot().last_error is not None


def test_a_later_successful_disconnect_clears_the_recorded_error() -> None:
    service = _FakeService(error=RuntimeError("boom"))
    boundary = _owned(service)

    with pytest.raises(RuntimeError):
        boundary.disconnect()

    service.error = None
    boundary.disconnect()

    assert boundary.snapshot().last_error is None


def test_connect_active_reconnects_the_owned_service_once() -> None:
    service = _FakeService()
    boundary = _owned(service)
    assert service.connect_calls == 1  # the candidate connect

    result = boundary.connect_active()

    assert result is service.connection
    assert service.connect_calls == 2
    assert boundary.has_order_service() is True


# -- clear_active --------------------------------------------------------


def test_clear_active_is_refused_while_the_connection_is_alive() -> None:
    service = _FakeService(connected=True)
    boundary = _owned(service)

    with pytest.raises(PaperTradingLifecycleError, match="live connection"):
        boundary.clear_active()

    assert boundary.has_order_service() is True


def test_clear_active_succeeds_once_the_connection_is_gone() -> None:
    boundary = _owned_disconnected(_FakeService())

    boundary.clear_active()

    assert boundary.has_order_service() is False
    assert boundary.is_connected() is False


def test_clear_active_refuses_when_nothing_is_owned() -> None:
    boundary = _service()

    with pytest.raises(PaperTradingLifecycleError, match="no active Paper"):
        boundary.clear_active()


def test_clear_active_refuses_a_different_service_than_the_one_owned() -> None:
    """A late callback must not clear an owner that replaced it."""

    old, new = _FakeService(connected=False), _FakeService(connected=False)
    boundary = _owned(old, factory=_FakeFactory(old, new))

    with pytest.raises(PaperTradingLifecycleError, match="different active"):
        boundary.clear_active(expected_service=new)

    assert boundary.has_order_service() is True
    assert boundary.broker_state() is old.broker_state_value


def test_clear_active_accepts_the_service_it_actually_owns() -> None:
    service = _FakeService()
    boundary = _owned_disconnected(service)

    boundary.clear_active(expected_service=service)

    assert boundary.has_order_service() is False


# -- probe ---------------------------------------------------------------


def test_probe_connects_reads_and_disconnects_without_owning_anything() -> None:
    service = _FakeService()
    boundary = _service(factory=_FakeFactory(service))

    connection, broker_state = boundary.probe_order_channel(
        config=object(), repository=object(), extended_hours_enabled=False
    )

    assert connection is service.connection
    assert broker_state is service.broker_state_value
    assert service.connect_calls == 1
    assert service.disconnect_calls == 1
    assert boundary.has_order_service() is False


def test_probe_disconnects_even_when_the_read_fails() -> None:
    service = _FakeService()

    def _explode() -> object:
        raise RuntimeError("no state")

    service.broker_state = _explode  # type: ignore[method-assign]
    boundary = _service(factory=_FakeFactory(service))

    with pytest.raises(RuntimeError, match="no state"):
        boundary.probe_order_channel(
            config=object(), repository=object(), extended_hours_enabled=False
        )

    assert service.disconnect_calls == 1
    assert boundary.has_order_service() is False


def test_probe_does_not_disturb_an_active_session() -> None:
    active, probe = _FakeService(), _FakeService()
    boundary = _owned(active, factory=_FakeFactory(active, probe))

    boundary.probe_order_channel(
        config=object(), repository=object(), extended_hours_enabled=False
    )

    assert active.disconnect_calls == 0
    assert boundary.has_order_service() is True


# -- construction --------------------------------------------------------


def test_the_default_factory_is_the_execution_composition_root() -> None:
    """Production must build the real stack; tests inject their own.

    The default is the execution composition root rather than the concrete
    adapter, so this module never names the IBKR channel or the SQLite store.
    """

    signature = inspect.signature(PaperTradingService.__init__)
    default = signature.parameters["order_service_factory"].default

    assert default is build_execution_candidate


# -- structural boundary -------------------------------------------------


def _module_source() -> str:
    return inspect.getsource(module)


def _imported_modules() -> list[str]:
    tree = ast.parse(_module_source())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return imported


def test_module_does_not_import_any_gui_toolkit() -> None:
    forbidden = {"PySide6", "PyQt5", "PyQt6", "PySide2"}
    for name in _imported_modules():
        assert name.split(".")[0] not in forbidden, f"{name} pulls a GUI toolkit in"


def test_module_does_not_reference_widgets_or_the_desktop() -> None:
    tree = ast.parse(_module_source())
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            referenced.update(alias.name for alias in node.names)

    for symbol in ("QThread", "QWidget", "MainWindow", "QObject"):
        assert symbol not in referenced, f"{symbol} must not be used here"


def test_module_does_not_import_the_desktop_module() -> None:
    for name in _imported_modules():
        assert not name.startswith("us_quant.desktop")


def test_module_does_not_import_the_launch_plan_or_the_workflow() -> None:
    """The service must not learn the shape of a strategy launch."""

    for name in _imported_modules():
        assert "AutoLaunchPlan" not in name, f"{name} leaks the launch plan in"
        assert not name.startswith("us_quant.paper_workflow"), name


def test_module_exposes_no_real_order_entry_point() -> None:
    """This step must not add or wrap any trading entry point."""

    tree = ast.parse(_module_source())
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for forbidden in (
        "submit",
        "cancel",
        "replace",
        "place",
        "submit_order",
        "cancel_order",
        "replace_order",
        "place_order",
        "reqGlobalCancel",
        "resubmit_pending_intent",
        "arm",
    ):
        assert forbidden not in defined, f"{forbidden} must not exist here"

    source = _module_source()
    for forbidden in ("reqGlobalCancel", "placeOrder", "cancelOrder"):
        assert forbidden not in source, f"{forbidden} must not appear here"


def test_module_never_holds_the_lock_across_a_network_call() -> None:
    """``with self._lock`` must not wrap connect/disconnect."""

    tree = ast.parse(_module_source())
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
        for forbidden in ("connect", "disconnect"):
            assert forbidden not in body, f"{forbidden} runs inside the lock"


def test_no_method_grows_into_a_god_method() -> None:
    tree = ast.parse(_module_source())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        span = (node.end_lineno or node.lineno) - node.lineno + 1
        assert span < 60, f"{node.name} spans {span} lines"


def test_module_stays_a_thin_boundary() -> None:
    """A god service would mean this step moved too much.

    The module owns one lifecycle; the implementation it wraps (the broker
    adapter plus the journal and model modules behind it) is several times
    larger and still holds every trading rule.
    """

    lines = [
        line
        for line in _module_source().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert len(lines) < 500, f"service grew to {len(lines)} lines"


def test_the_wrapped_implementation_is_still_far_larger_than_this_boundary() -> None:
    """Guards the claim above instead of trusting it.

    The wrapped implementation is spread over the execution adapter, the order
    store and the models; the boundary must stay a fraction of all three
    together.  The threshold is 2.5x rather than 3x because the sum sits at
    roughly 4.0x, and a guard that close to the measured value would fail for
    no real reason.
    """

    package = Path(module.__file__ or "").parent
    trading = package / "trading"
    implementation = sum(
        path.stat().st_size
        for path in (
            trading / "adapters" / "ibkr" / "execution.py",
            trading / "adapters" / "sqlite" / "order_repository.py",
            package / "paper_order_models.py",
        )
    )

    boundary = Path(module.__file__ or "").stat().st_size

    assert implementation > 2.5 * boundary


def test_service_is_reachable_from_the_package_layout() -> None:
    path = Path(module.__file__ or "")

    assert path.name == "paper_trading_service.py"
    assert path.parent.name == "us_quant"
