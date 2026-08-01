from dataclasses import FrozenInstanceError

import pytest

from us_quant.workflow_state import (
    ExecutionLease,
    ExecutionLeaseManager,
    PaperWorkflowPhase,
    WorkflowSnapshot,
    WorkflowStateError,
    validate_paper_transition,
)


def test_execution_leases_are_mutually_exclusive() -> None:
    leases = ExecutionLeaseManager()

    leases.acquire_shadow()
    with pytest.raises(WorkflowStateError):
        leases.acquire_paper()
    leases.release_shadow()
    leases.acquire_paper()
    with pytest.raises(WorkflowStateError):
        leases.acquire_shadow()


def test_paper_lease_requires_finalized_session_before_release() -> None:
    leases = ExecutionLeaseManager()
    leases.acquire_paper()

    with pytest.raises(WorkflowStateError):
        leases.release_paper(finalized=False)
    assert leases.lease is ExecutionLease.PAPER

    leases.release_paper(finalized=True)
    assert leases.lease is ExecutionLease.NONE


def test_invalid_lease_releases_fail_closed() -> None:
    leases = ExecutionLeaseManager()

    with pytest.raises(WorkflowStateError):
        leases.release_shadow()
    with pytest.raises(WorkflowStateError):
        leases.release_paper(finalized=True)


def test_halted_paper_cannot_automatically_recover() -> None:
    with pytest.raises(WorkflowStateError):
        validate_paper_transition(
            PaperWorkflowPhase.HALTED, PaperWorkflowPhase.RUNNING
        )
    with pytest.raises(WorkflowStateError):
        validate_paper_transition(
            PaperWorkflowPhase.HALTED, PaperWorkflowPhase.RECONCILING
        )


def test_halted_paper_enters_reconciliation_only_after_explicit_action() -> None:
    validate_paper_transition(
        PaperWorkflowPhase.HALTED,
        PaperWorkflowPhase.RECONCILING,
        explicit_reconciliation=True,
    )
    validate_paper_transition(
        PaperWorkflowPhase.RECONCILING,
        PaperWorkflowPhase.RECONCILING_READY,
    )
    validate_paper_transition(
        PaperWorkflowPhase.RECONCILING_READY,
        PaperWorkflowPhase.FINALIZED,
    )


def test_connecting_failure_can_return_to_ready_for_a_new_attempt() -> None:
    validate_paper_transition(PaperWorkflowPhase.CONNECTING, PaperWorkflowPhase.READY)


def test_ready_session_can_deliberately_prepare_fresh_candidates() -> None:
    validate_paper_transition(PaperWorkflowPhase.READY, PaperWorkflowPhase.PREPARING)

    for phase in (
        PaperWorkflowPhase.CONNECTING,
        PaperWorkflowPhase.RUNNING,
        PaperWorkflowPhase.HALTED,
    ):
        with pytest.raises(WorkflowStateError):
            validate_paper_transition(phase, PaperWorkflowPhase.PREPARING)


def test_reconciled_paper_requires_explicit_confirmation_to_resume() -> None:
    with pytest.raises(WorkflowStateError):
        validate_paper_transition(
            PaperWorkflowPhase.RECONCILING_READY,
            PaperWorkflowPhase.RUNNING,
        )

    validate_paper_transition(
        PaperWorkflowPhase.RECONCILING_READY,
        PaperWorkflowPhase.RUNNING,
        explicit_reconciliation=True,
    )


def test_reconciling_cannot_resume_before_safe_evidence_is_ready() -> None:
    with pytest.raises(WorkflowStateError):
        validate_paper_transition(
            PaperWorkflowPhase.RECONCILING,
            PaperWorkflowPhase.RUNNING,
            explicit_reconciliation=True,
        )


def test_snapshot_is_frozen_and_slotted() -> None:
    snapshot = WorkflowSnapshot(
        lease=ExecutionLease.NONE,
        paper_phase=PaperWorkflowPhase.IDLE,
        shadow_active=False,
        account_ready=False,
        market_ready=False,
        local_position_count=0,
        broker_position_count=0,
        pending_order_count=0,
        unreconciled_row_count=0,
        status_message="Idle",
    )

    with pytest.raises(FrozenInstanceError):
        snapshot.status_message = "Changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        snapshot.extra = "not allowed"  # type: ignore[attr-defined]
