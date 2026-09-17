"""Unit coverage for the transitional Paper trading facade.

Nothing here touches IBKR: every collaborator is a local fake, so the facade's
contract -- reads, immutable snapshots, and the fail-safe ``disconnect`` -- is
pinned without a broker, a Qt event loop, or a real order service.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from us_quant import paper_trading_facade as module
from us_quant.paper_trading_facade import (
    PaperReconciliationStatus,
    PaperTradingFacade,
    PaperTradingSnapshot,
)
from us_quant.workflow_state import PaperWorkflowPhase


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

    def __init__(self, *, connected: bool = True, error: Exception | None = None) -> None:
        self.connected = connected
        self.error = error
        self.disconnect_calls = 0
        self.connection_snapshot_calls = 0
        self.broker_state_calls = 0
        self.row_calls: list[dict] = []
        self.broker_state_value: object = object()
        self.rows: tuple[dict, ...] = ()

    def connection_snapshot(self) -> _FakeConnection:
        self.connection_snapshot_calls += 1
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
        self.phase_reads = 0


def _facade(
    *, service: object | None = None, workflow: _FakeWorkflow | None = None
) -> PaperTradingFacade:
    workflow = workflow if workflow is not None else _FakeWorkflow()
    return PaperTradingFacade(
        workflow_getter=lambda: workflow,
        order_service_getter=lambda: service,
    )


# -- no order service ----------------------------------------------------


def test_without_a_service_there_is_nothing_connected() -> None:
    facade = _facade(service=None)

    assert facade.has_order_service() is False
    assert facade.is_connected() is False


def test_disconnect_without_a_service_is_safe_and_does_nothing() -> None:
    facade = _facade(service=None)

    facade.disconnect()

    assert facade.snapshot().last_error is None


def test_read_only_order_queries_degrade_to_empty_without_a_service() -> None:
    facade = _facade(service=None)

    assert facade.broker_state() is None
    assert facade.reconciliation_rows_with_latency(session_id="s", limit=10) == ()


# -- with a service ------------------------------------------------------


def test_connected_state_follows_the_service() -> None:
    connected = _facade(service=_FakeService(connected=True))
    disconnected = _facade(service=_FakeService(connected=False))

    assert connected.is_connected() is True
    assert disconnected.is_connected() is False


def test_connection_state_is_read_from_the_live_service_not_a_copy() -> None:
    """A replaced service must be seen: the getter is resolved per call."""

    service = _FakeService(connected=False)
    facade = _facade(service=service)

    assert facade.is_connected() is False
    service.connected = True

    assert facade.is_connected() is True


def test_read_only_order_queries_forward_to_the_service() -> None:
    service = _FakeService()
    service.rows = ({"intent_id": "abc"},)
    facade = _facade(service=service)

    assert facade.broker_state() is service.broker_state_value
    assert facade.reconciliation_rows_with_latency(
        session_id="session-1", limit=100
    ) == ({"intent_id": "abc"},)
    assert service.row_calls == [{"session_id": "session-1", "limit": 100}]


# -- snapshot ------------------------------------------------------------


def test_snapshot_is_frozen() -> None:
    facade = _facade(service=_FakeService(connected=True))

    snapshot = facade.snapshot()

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
    facade = _facade(service=_FakeService(connected=True), workflow=workflow)

    snapshot = facade.snapshot()

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
    workflow = _FakeWorkflow(phase=PaperWorkflowPhase.RECONCILING_READY)
    facade = _facade(workflow=workflow)

    assert facade.phase() is PaperWorkflowPhase.RECONCILING_READY
    assert workflow.phase_reads == 0  # read through the getter, not cached


def test_finalized_requires_the_controllers_own_result() -> None:
    assert _facade(workflow=_FakeWorkflow(result=_FakeResult(True))).is_finalized() is True
    assert (
        _facade(workflow=_FakeWorkflow(result=_FakeResult(False))).is_finalized() is False
    )


def test_a_window_that_never_started_a_session_counts_as_finalized() -> None:
    """Nothing is outstanding, so the close gate must not refuse."""

    assert _facade(workflow=_FakeWorkflow(result=None)).is_finalized() is True


def test_reconciliation_status_reports_awaiting_confirmation() -> None:
    waiting = _facade(workflow=_FakeWorkflow(evidence=object()))
    idle = _facade(workflow=_FakeWorkflow(evidence=None))

    assert waiting.reconciliation_status() == PaperReconciliationStatus(True)
    assert waiting.reconciliation_status().awaiting_confirmation is True
    assert idle.reconciliation_status().awaiting_confirmation is False


# -- disconnect ----------------------------------------------------------


def test_disconnect_calls_the_existing_service_exactly_once() -> None:
    service = _FakeService()
    facade = _facade(service=service)

    facade.disconnect()

    assert service.disconnect_calls == 1


def test_disconnect_failure_is_recorded_and_still_raised() -> None:
    failure = RuntimeError("socket refused")
    service = _FakeService(error=failure)
    facade = _facade(service=service)

    with pytest.raises(RuntimeError, match="socket refused"):
        facade.disconnect()

    assert service.disconnect_calls == 1
    assert facade.snapshot().last_error is not None
    assert "socket refused" in (facade.snapshot().last_error or "")


def test_disconnect_never_reports_silent_success_on_failure() -> None:
    facade = _facade(service=_FakeService(error=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        facade.disconnect()

    assert facade.snapshot().last_error is not None


def test_a_later_successful_disconnect_clears_the_recorded_error() -> None:
    service = _FakeService(error=RuntimeError("boom"))
    facade = _facade(service=service)

    with pytest.raises(RuntimeError):
        facade.disconnect()

    service.error = None
    facade.disconnect()

    assert facade.snapshot().last_error is None


# -- structural boundary -------------------------------------------------


def _module_source() -> str:
    return inspect.getsource(module)


def test_module_does_not_import_any_gui_toolkit() -> None:
    tree = ast.parse(_module_source())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    forbidden = {"PySide6", "PyQt5", "PyQt6", "PySide2"}
    for name in imported:
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
    tree = ast.parse(_module_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("us_quant.desktop")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("us_quant.desktop")


def test_module_exposes_no_real_order_entry_point() -> None:
    """This step must not add or wrap any trading entry point."""

    tree = ast.parse(_module_source())
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for forbidden in (
        "submit_order",
        "cancel_order",
        "replace_order",
        "place_order",
        "submit",
        "cancel",
        "reqGlobalCancel",
        "cancel_intent",
        "resubmit_pending_intent",
    ):
        assert forbidden not in defined, f"{forbidden} must not exist here"

    source = _module_source()
    for forbidden in ("reqGlobalCancel", "placeOrder", "cancelOrder", "submit("):
        assert forbidden not in source, f"{forbidden} must not appear here"


def test_facade_stays_a_thin_boundary() -> None:
    """A god facade would mean this step moved too much."""

    lines = [
        line
        for line in _module_source().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert len(lines) < 300, f"facade grew to {len(lines)} lines"


def test_facade_is_reachable_from_the_package_layout() -> None:
    path = Path(module.__file__ or "")

    assert path.name == "paper_trading_facade.py"
    assert path.parent.name == "us_quant"
