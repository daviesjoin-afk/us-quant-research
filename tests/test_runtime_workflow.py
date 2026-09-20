from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.auto_launch import build_auto_launch_plan
from us_quant.trading.runtime.coordinator import PaperSessionCoordinator
from us_quant.trading.runtime.workflow import PaperWorkflowController
from us_quant.trading.runtime.workflow_state import ExecutionLease, PaperWorkflowPhase, WorkflowStateError


@dataclass(frozen=True)
class EngineSnapshot:
    session_id: str | None = "session"
    active: bool = True
    positions: tuple[object, ...] = ()
    pending_orders: tuple[object, ...] = ()
    entries_paused: bool = False
    stop_requested: bool = False


@dataclass(frozen=True)
class Health:
    safe_to_continue: bool = True
    status: str = "HEALTHY"


@dataclass(frozen=True)
class BrokerState:
    positions: tuple[object, ...] = ()


@dataclass(frozen=True)
class ReconciliationSummary:
    unreconciled: int = 0
    terminal_unreconciled: int = 0


@dataclass(frozen=True)
class ReconciliationSnapshot:
    account_fingerprint: str = "DU***17"
    connection_generation: int = 1
    reconciliation_generation: int = 1
    state_version: int = 1
    digest: str = "broker-digest"
    snapshot_complete: bool = True
    broker_positions: tuple[object, ...] = ()
    open_broker_orders: tuple[object, ...] = ()
    reconciliation_summary: ReconciliationSummary = ReconciliationSummary()


@dataclass(frozen=True)
class Connection:
    connected: bool = True
    open_broker_orders: int = 0


class Config:
    entry_order_timeout_seconds = 30


class Engine:
    config = Config()

    def __init__(self) -> None:
        self.current = EngineSnapshot()
        self.calls: list[str] = []

    def snapshot(self, *, observed_at=None):
        return self.current

    def on_execution(self, execution):
        self.calls.append("execution")
        return self.current

    def on_order_update(self, update):
        self.calls.append("update")
        return self.current

    def on_stream(self, snapshot, *, observed_at=None):
        self.calls.append("stream")
        return self.current

    def pause_entries(self):
        self.calls.append("pause")
        self.current = EngineSnapshot(**{**self.current.__dict__, "entries_paused": True})
        return self.current

    def resume_entries(self):
        self.calls.append("resume")
        self.current = EngineSnapshot(**{**self.current.__dict__, "entries_paused": False})
        return self.current

    def request_stop(self):
        self.calls.append("stop")
        self.current = EngineSnapshot(**{**self.current.__dict__, "active": False, "stop_requested": True})
        return self.current

    def halt_for_reconciliation(self, reason):
        self.calls.append("halt")
        self.current = EngineSnapshot(**{**self.current.__dict__, "active": False, "stop_requested": True})
        return self.current

    def resume_from_reconciliation(self, *, session_id, allow_force_flat_exit=True):
        self.calls.append("resume_from_reconciliation")
        self.current = EngineSnapshot(
            **{
                **self.current.__dict__,
                "active": True,
                "stop_requested": False,
                "entries_paused": False,
            }
        )
        return self.current


class Orders:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.broker = BrokerState()
        self.summary = ReconciliationSummary()
        self.reconciliation_snapshot = ReconciliationSnapshot()
        self.snapshot_current = True
        self.connected = True

    def fills(self):
        self.calls.append("fills")
        return ()

    def events(self):
        self.calls.append("events")
        return ()

    def cancel(self, order_id):
        self.calls.append("cancel")
        return True

    def connection_snapshot(self):
        return Connection(connected=self.connected)

    def broker_state(self):
        return self.broker

    def reconciliation_rows_with_latency(self, *, session_id, limit):
        return ()

    def reconciliation_summary(self, session_id):
        return self.summary

    def refresh_reconciliation_snapshot(self, session_id):
        self.calls.append("refresh_reconciliation")
        return self.reconciliation_snapshot

    def reconciliation_snapshot_is_current(self, snapshot):
        return self.snapshot_current and snapshot == self.reconciliation_snapshot


def plan(attempt: int = 1):
    return build_auto_launch_plan(
        attempt_id=attempt,
        strategy_version_id="strategy@1",
        parameter_hash="params",
        candidate_symbols=("AAA",),
        requested_capital_limit=Decimal("1000"),
    )


def coordinator(*, health: Health = Health()) -> PaperSessionCoordinator:
    return PaperSessionCoordinator(
        engine=Engine(),
        orders=Orders(),
        health_evaluator=lambda **kwargs: health,
        candidate_symbols=frozenset({"AAA"}),
        clock=lambda: datetime(2026, 8, 9, tzinfo=timezone.utc),
    )


def running_controller() -> PaperWorkflowController:
    controller = PaperWorkflowController()
    controller.begin_preparing()
    controller.mark_ready()
    approved = plan()
    controller.begin_connecting(approved)
    session = coordinator()
    controller.publish_armed(
        approved,
        engine=session._engine,  # type: ignore[attr-defined]
        orders=session._orders,  # type: ignore[attr-defined]
        health_evaluator=session._health_evaluator,  # type: ignore[attr-defined]
        candidate_symbols=frozenset({"AAA"}),
    )
    return controller


def test_stale_rejection_cannot_release_or_mutate_newer_attempt() -> None:
    controller = PaperWorkflowController()
    controller.begin_preparing()
    controller.mark_ready()
    newer = plan(2)
    controller.begin_connecting(newer)

    assert not controller.reject_connecting(plan(1))
    assert controller.phase is PaperWorkflowPhase.CONNECTING
    assert controller.active_plan == newer
    assert controller.lease is ExecutionLease.PAPER


def test_duplicate_begin_connecting_is_blocked() -> None:
    controller = PaperWorkflowController()
    controller.begin_preparing()
    controller.mark_ready()
    controller.begin_connecting(plan())

    with pytest.raises(WorkflowStateError):
        controller.begin_connecting(plan(2))


def test_cancel_preparing_returns_to_idle_without_acquiring_a_lease() -> None:
    controller = PaperWorkflowController()
    controller.begin_preparing()

    controller.cancel_preparing()

    assert controller.phase is PaperWorkflowPhase.IDLE
    assert controller.lease is ExecutionLease.NONE
    with pytest.raises(WorkflowStateError):
        controller.cancel_preparing()


def test_begin_preparing_can_restart_ready_preflight() -> None:
    controller = PaperWorkflowController()
    controller.begin_preparing()
    controller.mark_ready()

    controller.begin_preparing()

    assert controller.phase is PaperWorkflowPhase.PREPARING
    assert controller.lease is ExecutionLease.NONE


def test_runtime_ingress_delegates_once_and_snapshot_reflects_result() -> None:
    controller = running_controller()
    session = controller._coordinator  # type: ignore[attr-defined]
    assert session is not None
    orders = session._orders  # type: ignore[attr-defined]
    engine = session._engine  # type: ignore[attr-defined]

    controller.poll()
    controller.on_stream(object())
    controller.set_entries_paused(True)

    assert orders.calls.count("fills") == 2
    assert orders.calls.count("events") == 2
    assert engine.calls == ["stream", "pause"]
    assert controller.phase is PaperWorkflowPhase.PAUSED
    assert controller.snapshot(account_ready=True, market_ready=True).paper_phase is PaperWorkflowPhase.PAUSED


def test_unsafe_health_halts_stickily_and_requires_explicit_reconciliation() -> None:
    controller = PaperWorkflowController()
    controller.begin_preparing()
    controller.mark_ready()
    approved = plan()
    controller.begin_connecting(approved)
    bad = coordinator(health=Health(False, "HALT"))
    controller.publish_armed(
        approved,
        engine=bad._engine,  # type: ignore[attr-defined]
        orders=bad._orders,  # type: ignore[attr-defined]
        health_evaluator=bad._health_evaluator,  # type: ignore[attr-defined]
        candidate_symbols=frozenset({"AAA"}),
    )

    controller.poll()
    assert controller.phase is PaperWorkflowPhase.HALTED
    with pytest.raises(WorkflowStateError):
        controller.set_entries_paused(False)
    controller.begin_manual_reconciliation()
    assert controller.phase is PaperWorkflowPhase.RECONCILING


def test_failed_manual_reconciliation_returns_to_sticky_halt() -> None:
    controller = PaperWorkflowController()
    controller.begin_preparing()
    controller.mark_ready()
    approved = plan()
    controller.begin_connecting(approved)
    bad = coordinator(health=Health(False, "HALT"))
    controller.publish_armed(
        approved,
        engine=bad._engine,  # type: ignore[attr-defined]
        orders=bad._orders,  # type: ignore[attr-defined]
        health_evaluator=bad._health_evaluator,  # type: ignore[attr-defined]
        candidate_symbols=frozenset({"AAA"}),
    )
    controller.poll()
    attempt_id = controller.begin_manual_reconciliation()

    assert controller.fail_manual_reconciliation(attempt_id)

    assert controller.phase is PaperWorkflowPhase.HALTED
    assert controller.lease is ExecutionLease.PAPER


def test_manual_resume_requires_explicit_phase_and_invokes_coordinator_once() -> None:
    controller = PaperWorkflowController()
    with pytest.raises(WorkflowStateError):
        controller.confirm_manual_resume("missing")

    controller.begin_preparing()
    controller.mark_ready()
    approved = plan()
    controller.begin_connecting(approved)
    unsafe = coordinator(health=Health(False, "HALT"))
    controller.publish_armed(
        approved,
        engine=unsafe._engine,  # type: ignore[attr-defined]
        orders=unsafe._orders,  # type: ignore[attr-defined]
        health_evaluator=unsafe._health_evaluator,  # type: ignore[attr-defined]
        candidate_symbols=frozenset({"AAA"}),
    )
    controller.poll()
    attempt_id = controller.begin_manual_reconciliation()
    halted_session = controller._coordinator  # type: ignore[attr-defined]
    assert halted_session is not None
    engine = halted_session._engine  # type: ignore[attr-defined]
    orders = halted_session._orders  # type: ignore[attr-defined]
    candidates = halted_session._candidate_symbols  # type: ignore[attr-defined]

    # The reconciliation pass must be explicitly healthy before it produces
    # evidence; then a separate human confirmation performs a second refresh.
    halted_session._health_evaluator = lambda **_kwargs: Health(True, "HEALTHY")  # type: ignore[attr-defined]
    controller.complete_manual_reconciliation(attempt_id)
    evidence = controller.reconciliation_evidence
    assert evidence is not None
    assert controller.phase is PaperWorkflowPhase.RECONCILING_READY
    controller.confirm_manual_resume(evidence.evidence_id)

    assert engine.calls == ["halt", "resume_from_reconciliation"]
    resumed_session = controller._coordinator  # type: ignore[attr-defined]
    assert resumed_session is not None
    assert resumed_session._orders is orders  # type: ignore[attr-defined]
    assert getattr(resumed_session._health_evaluator(), "status") == "HEALTHY"  # type: ignore[attr-defined]
    assert resumed_session._candidate_symbols == candidates  # type: ignore[attr-defined]
    assert controller.phase is PaperWorkflowPhase.RUNNING


def test_stale_manual_reconciliation_attempt_is_ignored() -> None:
    controller = PaperWorkflowController()
    controller.begin_preparing()
    controller.mark_ready()
    approved = plan()
    controller.begin_connecting(approved)
    unsafe = coordinator(health=Health(False, "HALT"))
    controller.publish_armed(
        approved,
        engine=unsafe._engine,  # type: ignore[attr-defined]
        orders=unsafe._orders,  # type: ignore[attr-defined]
        health_evaluator=unsafe._health_evaluator,  # type: ignore[attr-defined]
        candidate_symbols=frozenset({"AAA"}),
    )
    controller.poll()
    attempt_id = controller.begin_manual_reconciliation()

    assert not controller.fail_manual_reconciliation("stale-attempt")
    assert controller.phase is PaperWorkflowPhase.RECONCILING
    with pytest.raises(WorkflowStateError, match="Stale or inactive"):
        controller.complete_manual_reconciliation("stale-attempt")
    assert controller.phase is PaperWorkflowPhase.RECONCILING
    assert controller.fail_manual_reconciliation(attempt_id)


def test_manual_confirmation_is_one_shot_and_bad_second_proof_halts() -> None:
    controller = PaperWorkflowController()
    controller.begin_preparing()
    controller.mark_ready()
    approved = plan()
    controller.begin_connecting(approved)
    unsafe = coordinator(health=Health(False, "HALT"))
    controller.publish_armed(
        approved,
        engine=unsafe._engine,  # type: ignore[attr-defined]
        orders=unsafe._orders,  # type: ignore[attr-defined]
        health_evaluator=unsafe._health_evaluator,  # type: ignore[attr-defined]
        candidate_symbols=frozenset({"AAA"}),
    )
    controller.poll()
    attempt_id = controller.begin_manual_reconciliation()
    halted_session = controller._coordinator  # type: ignore[attr-defined]
    assert halted_session is not None
    halted_session._health_evaluator = lambda **_kwargs: Health(True, "HEALTHY")  # type: ignore[attr-defined]
    controller.complete_manual_reconciliation(attempt_id)
    evidence = controller.reconciliation_evidence
    assert evidence is not None
    orders = halted_session._orders  # type: ignore[attr-defined]
    orders.reconciliation_snapshot = ReconciliationSnapshot(digest="changed")

    with pytest.raises(WorkflowStateError, match="changed"):
        controller.confirm_manual_resume(evidence.evidence_id)

    assert controller.phase is PaperWorkflowPhase.HALTED
    with pytest.raises(WorkflowStateError):
        controller.confirm_manual_resume(evidence.evidence_id)


def test_pause_stop_delegate_and_release_lease_only_after_finalization() -> None:
    controller = running_controller()
    controller.set_entries_paused(True)
    controller.set_entries_paused(False)
    controller.request_stop()

    assert controller.phase is PaperWorkflowPhase.STOPPING
    result, evidence_id = controller.capture_finalization_evidence()
    assert not result.state.finalized
    assert evidence_id is not None
    session = controller._coordinator  # type: ignore[attr-defined]
    session._orders.connected = False  # type: ignore[attr-defined]
    controller.confirm_finalization_after_disconnect(evidence_id)
    assert controller.phase is PaperWorkflowPhase.FINALIZED
    assert controller.lease is ExecutionLease.PAPER
    assert controller.finalize_if_safe()
    assert controller.lease is ExecutionLease.NONE


def test_failed_post_disconnect_finalization_keeps_paper_lease_halted() -> None:
    controller = running_controller()
    controller.request_stop()
    _, evidence_id = controller.capture_finalization_evidence()
    assert evidence_id is not None

    with pytest.raises(WorkflowStateError, match="disconnected"):
        controller.confirm_finalization_after_disconnect(evidence_id)

    assert controller.phase is PaperWorkflowPhase.HALTED
    assert controller.lease is ExecutionLease.PAPER
    assert not controller.finalize_if_safe()


def test_reject_matching_unarmed_connection_returns_ready_and_releases_lease() -> None:
    controller = PaperWorkflowController()
    controller.begin_preparing()
    controller.mark_ready()
    approved = plan()
    controller.begin_connecting(approved)

    assert controller.reject_connecting(approved)
    assert controller.phase is PaperWorkflowPhase.READY
    assert controller.active_plan is None
    assert controller.lease is ExecutionLease.NONE


def test_finalized_session_resets_for_a_new_launch_attempt() -> None:
    controller = running_controller()
    controller.request_stop()

    _, evidence_id = controller.capture_finalization_evidence()
    assert evidence_id is not None
    session = controller._coordinator  # type: ignore[attr-defined]
    session._orders.connected = False  # type: ignore[attr-defined]
    controller.confirm_finalization_after_disconnect(evidence_id)

    assert controller.finalize_if_safe()
    assert controller.phase is PaperWorkflowPhase.FINALIZED
    assert controller.active_plan is None
    assert controller.result is None

    controller.begin_preparing()
    controller.mark_ready()
    second = plan(2)
    controller.begin_connecting(second)

    assert controller.phase is PaperWorkflowPhase.CONNECTING
    assert controller.active_plan == second
    assert controller.lease is ExecutionLease.PAPER
