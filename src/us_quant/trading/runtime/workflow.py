"""UI-independent lifecycle controller for an already-safe Paper workflow.

The controller deliberately owns no broker connection or order API.  A caller
connects and arms the Paper service elsewhere, then publishes the resulting
ports here only after the immutable :class:`AutoLaunchPlan` still matches.

Its explicit recovery protocol -- manual reconciliation -- is mixed in from
``recovery``; it shares this class's state and primitives rather than keeping a
second copy of either.
"""

from __future__ import annotations

from us_quant.auto_launch import AutoLaunchPlan
from us_quant.trading.runtime.coordinator import PaperSessionCoordinator
from us_quant.trading.runtime.paper_contracts import (
    HealthEvaluator,
    PaperEngine,
    PaperOrderPort,
)
from us_quant.trading.runtime.paper_models import PaperSessionResult
from us_quant.trading.runtime.reconciliation import (
    CoordinatorFinalizationEvidence,
    CoordinatorReconciliationEvidence,
)
from us_quant.trading.runtime.recovery import ManualReconciliation
from us_quant.trading.runtime.workflow_state import (
    ExecutionLease,
    ExecutionLeaseManager,
    PaperWorkflowPhase,
    WorkflowSnapshot,
    WorkflowStateError,
    validate_paper_transition,
)


class PaperWorkflowController(ManualReconciliation):
    """Own one Paper lifecycle while keeping the desktop and broker decoupled.

    The controller acquires its execution lease before an asynchronous broker
    connection begins.  It never invokes connection, arming, submission,
    resubmission, or teardown APIs itself.  A halted session remains halted
    until a human performs reconciliation and explicitly confirms its resume.
    """

    def __init__(self, *, leases: ExecutionLeaseManager | None = None) -> None:
        self._leases = leases or ExecutionLeaseManager()
        self._phase = PaperWorkflowPhase.IDLE
        self._active_plan: AutoLaunchPlan | None = None
        self._coordinator: PaperSessionCoordinator | None = None
        self._result: PaperSessionResult | None = None
        self._lease_acquired = False
        self._reconciliation_attempt_id: str | None = None
        self._reconciliation_evidence: CoordinatorReconciliationEvidence | None = None
        self._finalization_evidence: CoordinatorFinalizationEvidence | None = None

    @property
    def phase(self) -> PaperWorkflowPhase:
        """Current immutable lifecycle phase."""

        return self._phase

    @property
    def active_plan(self) -> AutoLaunchPlan | None:
        """The currently bound immutable launch plan, if any."""

        return self._active_plan

    @property
    def result(self) -> PaperSessionResult | None:
        """Latest coordinator result, without exposing mutable engine state."""

        return self._result

    @property
    def lease(self) -> ExecutionLease:
        """Expose lease state for controllers that coordinate Shadow mode."""

        return self._leases.lease

    @property
    def reconciliation_evidence(self) -> CoordinatorReconciliationEvidence | None:
        """One-shot evidence currently awaiting explicit human confirmation."""

        return self._reconciliation_evidence

    @property
    def finalization_evidence(self) -> CoordinatorFinalizationEvidence | None:
        """Zero-state proof awaiting service disconnect and final drain."""

        return self._finalization_evidence

    def snapshot(
        self, *, account_ready: bool = False, market_ready: bool = False
    ) -> WorkflowSnapshot:
        """Return the only renderable Paper workflow state."""

        state = self._result.state if self._result else None
        return WorkflowSnapshot(
            lease=self._leases.lease,
            paper_phase=self._phase,
            shadow_active=False,
            account_ready=account_ready,
            market_ready=market_ready,
            local_position_count=state.local_position_count if state else 0,
            broker_position_count=state.broker_position_count if state else 0,
            pending_order_count=state.pending_order_count if state else 0,
            unreconciled_row_count=state.unreconciled_order_count if state else 0,
            status_message=self._status_message(),
            broker_open_order_count=state.broker_open_order_count if state else 0,
            reconciliation_ready=(
                self._phase is PaperWorkflowPhase.RECONCILING_READY
                and self._reconciliation_evidence is not None
            ),
        )

    def begin_preparing(self) -> None:
        """Enter the local candidate/market preflight phase."""

        if self._phase is PaperWorkflowPhase.FINALIZED:
            self._transition(PaperWorkflowPhase.IDLE)
        self._transition(PaperWorkflowPhase.PREPARING)

    def cancel_preparing(self) -> None:
        """Abandon an unfinished local preflight without taking a Paper lease."""

        if self._phase is not PaperWorkflowPhase.PREPARING:
            raise WorkflowStateError("Only PREPARING work can be cancelled.")
        if self._leases.lease is not ExecutionLease.NONE:
            raise WorkflowStateError("Preparing must not hold an execution lease.")
        self._transition(PaperWorkflowPhase.IDLE)

    def mark_ready(self) -> None:
        """Mark completed local preflight as ready for a Paper connection."""

        self._transition(PaperWorkflowPhase.READY)

    def begin_connecting(self, plan: AutoLaunchPlan) -> None:
        """Bind ``plan`` and acquire PAPER before the external connect starts."""

        if self._active_plan is not None:
            raise WorkflowStateError("A Paper launch attempt is already active.")
        self._transition(PaperWorkflowPhase.CONNECTING)
        try:
            self._leases.acquire_paper()
        except Exception:
            self._transition(PaperWorkflowPhase.READY)
            raise
        self._lease_acquired = True
        self._active_plan = plan

    def reject_connecting(self, plan: AutoLaunchPlan) -> bool:
        """Discard a matching unarmed connection result and release its lease.

        ``False`` means a stale callback arrived for a different attempt.  It
        must not change the newer plan, phase, or execution lease.
        """

        if self._phase is not PaperWorkflowPhase.CONNECTING or plan != self._active_plan:
            return False
        self._release_unarmed_lease()
        self._active_plan = None
        self._transition(PaperWorkflowPhase.READY)
        return True

    def publish_armed(
        self,
        plan: AutoLaunchPlan,
        *,
        engine: PaperEngine,
        orders: PaperOrderPort,
        health_evaluator: HealthEvaluator,
        candidate_symbols: frozenset[str],
    ) -> PaperSessionResult:
        """Publish the already-armed Paper ports only for the active plan."""

        if self._phase is not PaperWorkflowPhase.CONNECTING or plan != self._active_plan:
            raise WorkflowStateError("Stale or invalid Paper launch publication.")
        self._coordinator = PaperSessionCoordinator(
            engine=engine,
            orders=orders,
            health_evaluator=health_evaluator,
            candidate_symbols=frozenset(symbol.upper() for symbol in candidate_symbols),
        )
        self._result = self._coordinator.snapshot()
        self._transition(PaperWorkflowPhase.RUNNING)
        return self._result

    def poll(self) -> PaperSessionResult:
        """Delegate one broker poll to the coordinator."""

        return self._apply_runtime_result(self._require_runtime().poll())

    def on_stream(self, snapshot: object) -> PaperSessionResult:
        """Delegate one market-stream event to the coordinator."""

        return self._apply_runtime_result(self._require_runtime().on_stream(snapshot))

    def set_entries_paused(self, paused: bool) -> PaperSessionResult:
        """Delegate entry pause/resume; HALTED sessions cannot resume here."""

        if paused and self._phase not in {
            PaperWorkflowPhase.RUNNING,
            PaperWorkflowPhase.PAUSED,
        }:
            raise WorkflowStateError(
                "Entries can only be paused for an active Paper session."
            )
        if not paused and self._phase is not PaperWorkflowPhase.PAUSED:
            raise WorkflowStateError("Entries can only resume from PAUSED.")
        result = self._apply_runtime_result(
            self._require_runtime().set_entries_paused(paused)
        )
        if result.state.halted:
            return result
        self._transition(PaperWorkflowPhase.PAUSED if paused else PaperWorkflowPhase.RUNNING)
        return result

    def request_stop(self, stream_snapshot: object | None = None) -> PaperSessionResult:
        """Delegate an orderly stop; lease release remains explicit and final-only."""

        if self._phase not in {PaperWorkflowPhase.RUNNING, PaperWorkflowPhase.PAUSED}:
            raise WorkflowStateError(
                "A Paper stop requires a RUNNING or PAUSED session."
            )
        result = self._apply_runtime_result(
            self._require_runtime().request_stop(stream_snapshot)
        )
        if result.state.halted:
            return result
        if result.state.finalized:
            # A fast, clean stop may be finalized by the same coordinator call,
            # but the externally visible lifecycle still passes through
            # STOPPING before it can become FINALIZED.
            self._transition(PaperWorkflowPhase.STOPPING)
            self._transition(PaperWorkflowPhase.FINALIZED)
        else:
            self._transition(PaperWorkflowPhase.STOPPING)
        return result

    def capture_finalization_evidence(
        self,
    ) -> tuple[PaperSessionResult, str | None]:
        """Capture coherent broker zero-state while retaining the PAPER lease."""

        if self._phase is not PaperWorkflowPhase.STOPPING:
            raise WorkflowStateError("Finalization evidence requires STOPPING.")
        coordinator = self._coordinator
        if coordinator is None:
            raise WorkflowStateError("No stopping Paper runtime is available.")
        result, evidence = coordinator.capture_finalization_evidence()
        self._result = result
        if result.state.halted:
            self._finalization_evidence = None
            self._transition(PaperWorkflowPhase.HALTED)
            return result, None
        self._finalization_evidence = evidence
        return result, evidence.evidence_id if evidence is not None else None

    def confirm_finalization_after_disconnect(
        self, evidence_id: str
    ) -> PaperSessionResult:
        """Consume zero-state proof after the broker callback thread has joined."""

        evidence = self._finalization_evidence
        if (
            self._phase is not PaperWorkflowPhase.STOPPING
            or evidence is None
            or evidence.evidence_id != evidence_id
        ):
            raise WorkflowStateError("Current finalization evidence is required.")
        self._finalization_evidence = None
        coordinator = self._coordinator
        if coordinator is None:
            self._transition(PaperWorkflowPhase.HALTED)
            raise WorkflowStateError("No stopping Paper runtime is available.")
        try:
            result = coordinator.confirm_finalization_after_disconnect(evidence)
        except (RuntimeError, ValueError) as error:
            self._transition(PaperWorkflowPhase.HALTED)
            raise WorkflowStateError(str(error)) from error
        self._result = result
        if not result.state.finalized:
            self._transition(PaperWorkflowPhase.HALTED)
            return result
        self._transition(PaperWorkflowPhase.FINALIZED)
        return result

    def fail_finalization_refresh(self) -> bool:
        """Fail closed if the asynchronous finalization task cannot complete."""

        if self._phase is not PaperWorkflowPhase.STOPPING:
            return False
        self._finalization_evidence = None
        self._transition(PaperWorkflowPhase.HALTED)
        return True

    def finalize_if_safe(self) -> bool:
        """Release PAPER only after coordinator finalization is observable."""

        if self._result is None or not self._result.state.finalized:
            return False
        if self._phase not in {
            PaperWorkflowPhase.STOPPING,
            PaperWorkflowPhase.RECONCILING,
            PaperWorkflowPhase.RECONCILING_READY,
            PaperWorkflowPhase.FINALIZED,
        }:
            return False
        if self._phase is not PaperWorkflowPhase.FINALIZED:
            self._transition(PaperWorkflowPhase.FINALIZED)
        if self._lease_acquired:
            self._leases.release_paper(finalized=True)
            self._lease_acquired = False
        # The finalized WorkflowSnapshot has already been observable through
        # ``result`` while finalization completed.  Do not carry an old plan or
        # coordinator into the next PREPARING cycle.
        self._active_plan = None
        self._coordinator = None
        self._result = None
        return True

    def _require_runtime(self) -> PaperSessionCoordinator:
        if self._coordinator is None or self._phase not in {
            PaperWorkflowPhase.RUNNING,
            PaperWorkflowPhase.PAUSED,
            PaperWorkflowPhase.STOPPING,
        }:
            raise WorkflowStateError("No active Paper runtime is available.")
        return self._coordinator

    def _apply_runtime_result(self, result: PaperSessionResult) -> PaperSessionResult:
        self._result = result
        if result.state.halted:
            if self._phase is not PaperWorkflowPhase.HALTED:
                self._transition(PaperWorkflowPhase.HALTED)
        return result

    def _release_unarmed_lease(self) -> None:
        if self._lease_acquired:
            # No session was published, therefore there are no local positions,
            # pending orders, broker positions, or unreconciled journal rows.
            self._leases.release_paper(finalized=True)
            self._lease_acquired = False

    def _transition(
        self,
        target: PaperWorkflowPhase,
        *,
        explicit_reconciliation: bool = False,
    ) -> None:
        if self._phase is target:
            return
        validate_paper_transition(
            self._phase, target, explicit_reconciliation=explicit_reconciliation
        )
        self._phase = target

    def _status_message(self) -> str:
        if self._result and self._result.events:
            return self._result.events[-1].message
        if self._result and self._result.health is not None:
            return getattr(self._result.health, "status", self._phase.value)
        return self._phase.value