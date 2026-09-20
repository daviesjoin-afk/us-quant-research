"""UI- and broker-independent workflow state guards.

This module intentionally contains no I/O.  Controllers can use it to make
execution-mode ownership and Paper lifecycle transitions explicit and
fail-closed before invoking any external service.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WorkflowStateError(RuntimeError):
    """Raised when a requested workflow state change is unsafe or invalid."""


class ExecutionLease(str, Enum):
    """The sole workflow allowed to own execution resources."""

    NONE = "NONE"
    SHADOW = "SHADOW"
    PAPER = "PAPER"


class PaperWorkflowPhase(str, Enum):
    """Explicit Paper session lifecycle, including manual reconciliation."""

    IDLE = "IDLE"
    PREPARING = "PREPARING"
    READY = "READY"
    CONNECTING = "CONNECTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    HALTED = "HALTED"
    RECONCILING = "RECONCILING"
    RECONCILING_READY = "RECONCILING_READY"
    FINALIZED = "FINALIZED"


@dataclass(frozen=True, slots=True)
class WorkflowSnapshot:
    """Read-only state rendered by the desktop without owning services."""

    lease: ExecutionLease
    paper_phase: PaperWorkflowPhase
    shadow_active: bool
    account_ready: bool
    market_ready: bool
    local_position_count: int
    broker_position_count: int
    pending_order_count: int
    unreconciled_row_count: int
    status_message: str
    broker_open_order_count: int = 0
    reconciliation_ready: bool = False


class ExecutionLeaseManager:
    """Enforces mutually exclusive Shadow and Paper execution ownership."""

    def __init__(self) -> None:
        self._lease = ExecutionLease.NONE

    @property
    def lease(self) -> ExecutionLease:
        """The currently held execution lease."""

        return self._lease

    def acquire_shadow(self) -> None:
        if self._lease is not ExecutionLease.NONE:
            raise WorkflowStateError(
                f"Cannot acquire SHADOW lease while {self._lease.value} is active."
            )
        self._lease = ExecutionLease.SHADOW

    def release_shadow(self) -> None:
        if self._lease is not ExecutionLease.SHADOW:
            raise WorkflowStateError("Cannot release SHADOW without a SHADOW lease.")
        self._lease = ExecutionLease.NONE

    def acquire_paper(self) -> None:
        if self._lease is not ExecutionLease.NONE:
            raise WorkflowStateError(
                f"Cannot acquire PAPER lease while {self._lease.value} is active."
            )
        self._lease = ExecutionLease.PAPER

    def release_paper(self, finalized: bool) -> None:
        if self._lease is not ExecutionLease.PAPER:
            raise WorkflowStateError("Cannot release PAPER without a PAPER lease.")
        if not finalized:
            raise WorkflowStateError("Cannot release PAPER until the session is finalized.")
        self._lease = ExecutionLease.NONE


_PAPER_TRANSITIONS: dict[PaperWorkflowPhase, frozenset[PaperWorkflowPhase]] = {
    PaperWorkflowPhase.IDLE: frozenset({PaperWorkflowPhase.PREPARING}),
    PaperWorkflowPhase.PREPARING: frozenset(
        {PaperWorkflowPhase.READY, PaperWorkflowPhase.IDLE, PaperWorkflowPhase.HALTED}
    ),
    PaperWorkflowPhase.READY: frozenset(
        {
            PaperWorkflowPhase.PREPARING,
            PaperWorkflowPhase.CONNECTING,
            PaperWorkflowPhase.IDLE,
            PaperWorkflowPhase.HALTED,
        }
    ),
    PaperWorkflowPhase.CONNECTING: frozenset(
        {
            PaperWorkflowPhase.READY,
            PaperWorkflowPhase.RUNNING,
            PaperWorkflowPhase.HALTED,
        }
    ),
    PaperWorkflowPhase.RUNNING: frozenset(
        {PaperWorkflowPhase.PAUSED, PaperWorkflowPhase.STOPPING, PaperWorkflowPhase.HALTED}
    ),
    PaperWorkflowPhase.PAUSED: frozenset(
        {PaperWorkflowPhase.RUNNING, PaperWorkflowPhase.STOPPING, PaperWorkflowPhase.HALTED}
    ),
    PaperWorkflowPhase.STOPPING: frozenset(
        {PaperWorkflowPhase.FINALIZED, PaperWorkflowPhase.HALTED}
    ),
    PaperWorkflowPhase.HALTED: frozenset(),
    PaperWorkflowPhase.RECONCILING: frozenset(
        {
            PaperWorkflowPhase.RECONCILING_READY,
            PaperWorkflowPhase.FINALIZED,
            PaperWorkflowPhase.HALTED,
        }
    ),
    PaperWorkflowPhase.RECONCILING_READY: frozenset(
        {PaperWorkflowPhase.FINALIZED, PaperWorkflowPhase.HALTED}
    ),
    PaperWorkflowPhase.FINALIZED: frozenset({PaperWorkflowPhase.IDLE}),
}


def validate_paper_transition(
    current: PaperWorkflowPhase,
    target: PaperWorkflowPhase,
    *,
    explicit_reconciliation: bool = False,
) -> None:
    """Validate a Paper phase transition without changing any state.

    HALTED can enter RECONCILING only after an explicit human action.  A
    session must then publish safe evidence as RECONCILING_READY before an
    explicit confirmation can return it to RUNNING; no automatic route back
    to RUNNING exists from HALTED or RECONCILING.
    """

    if explicit_reconciliation and (
        (current is PaperWorkflowPhase.HALTED and target is PaperWorkflowPhase.RECONCILING)
        or (
            current is PaperWorkflowPhase.RECONCILING_READY
            and target is PaperWorkflowPhase.RUNNING
        )
    ):
        return
    if target in _PAPER_TRANSITIONS[current]:
        return
    reconciliation_hint = (
        " Explicit manual reconciliation is required."
        if current is PaperWorkflowPhase.HALTED
        else ""
    )
    raise WorkflowStateError(
        f"Invalid Paper transition: {current.value} -> {target.value}.{reconciliation_hint}"
    )