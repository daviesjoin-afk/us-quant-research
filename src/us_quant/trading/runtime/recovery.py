"""The human-driven recovery half of the Paper runtime.

A Paper session has two kinds of work.  Most of the time it sequences a running
tick: drain broker facts, enforce the order-age rules, ask whether it may keep
going, and only then let the trading runtime see the market stream.  The rest of
the time it is *proved* -- a halt has to be resolved by a human presenting
one-shot evidence against fresh broker truth, and a stop has to be finalized
only after the service disconnects and the last local callback has drained.

This module owns that second kind, at both levels of the Paper runtime:

* ``SessionRecovery`` is the session's proof handshake, mixed into
  ``PaperSessionCoordinator``;
* ``ManualReconciliation`` is the workflow's explicit recovery protocol, mixed
  into ``PaperWorkflowController``.

Neither is a standalone object, because in both cases the two halves are one
object with one state, and the split exists so that neither half has to be read
through the other.  The state and the shared primitives are declared by the
class that mixes these in; the flows below use them and never reimplement them,
and nothing here connects, disconnects, submits or cancels anything.

The only engine call any of these flows can make is the explicit resume that a
human confirmation has already validated.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from us_quant.trading.runtime.paper_contracts import (
    Clock,
    EngineSnapshot,
    HealthEvaluator,
    PaperEngine,
    PaperHealth,
    PaperOrderPort,
)
from us_quant.trading.runtime.paper_models import (
    PaperSessionEvent,
    PaperSessionResult,
)
from us_quant.trading.runtime.reconciliation import (
    CoordinatorFinalizationEvidence,
    CoordinatorReconciliationEvidence,
    OneShotEvidence,
    build_finalization_evidence,
    engine_snapshot_digest,
    require_finalization_evidence,
    require_reconciliation_evidence,
    require_unchanged_reconciliation_proof,
    utc,
)
from us_quant.trading.runtime.workflow_state import (
    PaperWorkflowPhase,
    WorkflowStateError,
)

if TYPE_CHECKING:
    from us_quant.trading.runtime.coordinator import PaperSessionCoordinator


class SessionRecovery:
    """Reconciliation and finalization flows for one Paper session.

    Declared here so the contract these flows rely on is readable in one place;
    every attribute below is set by ``PaperSessionCoordinator.__init__`` and the
    methods are provided by that class.
    """

    _engine: PaperEngine
    _orders: PaperOrderPort
    _clock: Clock
    _health_evaluator: HealthEvaluator
    _candidate_symbols: frozenset[str]
    _engine_snapshot: EngineSnapshot
    _health: PaperHealth | None
    _halted: bool
    _finalization_proven: bool
    _consumed: OneShotEvidence

    def capture_reconciliation_evidence(
        self, *, now: datetime | None = None
    ) -> tuple[PaperSessionResult, CoordinatorReconciliationEvidence | None]:
        """Refresh complete broker truth and publish evidence only if healthy."""

        if not self._halted:
            raise RuntimeError("Reconciliation evidence requires a halted session.")
        session_id = self._engine_snapshot.session_id
        if not session_id:
            raise RuntimeError("A halted Paper session requires a session ID.")
        if not self._arming_is_valid():
            return self._store(()), None
        observed_at = utc(now or self._clock())
        broker_snapshot = self._orders.refresh_reconciliation_snapshot(session_id)
        self._drain_broker_events()
        events: list[PaperSessionEvent] = []
        self._evaluate_health(observed_at, events)
        result = self._store(events)
        if not self._proof_is_safe_and_current(broker_snapshot):
            return result, None
        return result, self._evidence(broker_snapshot, observed_at)

    def confirm_reconciliation(
        self,
        evidence: CoordinatorReconciliationEvidence,
        *,
        now: datetime | None = None,
    ) -> "PaperSessionCoordinator":
        """Revalidate and resume once; never refresh evidence implicitly."""

        observed_at = utc(now or self._clock())
        if not self._halted:
            raise RuntimeError("Only a halted session can consume reconciliation evidence.")
        require_reconciliation_evidence(
            evidence, binding=self._binding(), observed_at=observed_at
        )
        # Consume before the second broker refresh.  A failed or ambiguous
        # confirmation must require a new human reconciliation attempt.
        self._consumed.consume(evidence.evidence_id, label="Reconciliation")
        if not self._arming_is_valid():
            raise RuntimeError(
                "Paper session account changed before reconciliation confirmation."
            )
        if self._armed_account_fingerprint() != evidence.armed_account_fingerprint:
            raise RuntimeError(
                "Paper session armed-account identity changed before confirmation."
            )
        session_id = self._engine_snapshot.session_id
        assert session_id is not None
        broker_snapshot = self._orders.refresh_reconciliation_snapshot(session_id)
        self._drain_broker_events()
        events: list[PaperSessionEvent] = []
        self._evaluate_health(observed_at, events)
        self._store(events)
        if not self._proof_is_safe_and_current(broker_snapshot):
            raise RuntimeError("Fresh reconciliation evidence is no longer safe.")
        require_unchanged_reconciliation_proof(
            evidence, self._evidence(broker_snapshot, observed_at)
        )
        self._engine.resume_from_reconciliation(
            session_id=session_id, allow_force_flat_exit=True
        )
        # A resumed session is a new session of the same kind: a fresh halt flag
        # and fresh proof state, over the same engine and order port.
        return type(self)(
            engine=self._engine,
            orders=self._orders,
            health_evaluator=self._health_evaluator,
            candidate_symbols=self._candidate_symbols,
            clock=self._clock,
        )

    def capture_finalization_evidence(
        self, *, now: datetime | None = None
    ) -> tuple[PaperSessionResult, CoordinatorFinalizationEvidence | None]:
        """Capture coherent zero-state proof before the service disconnects."""

        observed_at = utc(now or self._clock())
        if (
            self._engine_snapshot.active
            or self._engine_snapshot.positions
            or self._engine_snapshot.pending_orders
        ):
            return self._store(()), None
        session_id = self._engine_snapshot.session_id
        if not session_id:
            raise RuntimeError("Paper finalization requires a session ID.")
        events: list[PaperSessionEvent] = []
        try:
            broker_snapshot = self._orders.refresh_reconciliation_snapshot(session_id)
        except Exception:
            self._halt("PAPER_FINALIZATION_REFRESH_FAILED", events)
            return self._store(events), None
        self._drain_broker_events()
        self._evaluate_health(observed_at, events)
        result = self._store(events)
        summary = getattr(broker_snapshot, "reconciliation_summary", None)
        clear = bool(
            self._proof_is_safe_and_current(broker_snapshot)
            and not self._engine_snapshot.active
            and not self._engine_snapshot.positions
            and not self._engine_snapshot.pending_orders
            and not getattr(broker_snapshot, "broker_positions", ())
            and not getattr(broker_snapshot, "open_broker_orders", ())
            and int(getattr(summary, "unreconciled", 1)) == 0
        )
        if not clear:
            return result, None
        return result, build_finalization_evidence(
            binding=self._binding(),
            broker_snapshot=broker_snapshot,
            engine_digest=engine_snapshot_digest(self._engine_snapshot),
            observed_at=observed_at,
        )

    def confirm_finalization_after_disconnect(
        self, evidence: CoordinatorFinalizationEvidence
    ) -> PaperSessionResult:
        """Finalize only after callback shutdown and a final local/journal drain."""

        self._consumed.consume(evidence.evidence_id, label="Finalization")
        require_finalization_evidence(
            evidence,
            binding=self._binding(),
            connected=bool(
                getattr(self._orders.connection_snapshot(), "connected", True)
            ),
        )
        self._drain_broker_events()
        summary = self._orders.reconciliation_summary(
            self._engine_snapshot.session_id or ""
        )
        clear = bool(
            not self._engine_snapshot.active
            and not self._engine_snapshot.positions
            and not self._engine_snapshot.pending_orders
            and int(getattr(summary, "unreconciled", 1)) == 0
            and engine_snapshot_digest(self._engine_snapshot) == evidence.engine_digest
        )
        if not clear:
            events: list[PaperSessionEvent] = []
            self._halt("PAPER_FINALIZATION_CHANGED", events)
            return self._store(events)
        self._finalization_proven = True
        return self._store(
            (PaperSessionEvent("PAPER_FINALIZED", "info", "PAPER_FINALIZED"),)
        )


class ManualReconciliation:
    """The explicit recovery protocol of a halted Paper workflow.

    A halt is sticky: the only way out is a human asking for reconciliation,
    which produces one-shot evidence, and then a separate explicit confirmation
    that revalidates it against fresh broker truth.  Stale attempts and stale
    confirmations -- a second click, an asynchronous result from an abandoned
    attempt -- are refused rather than ignored.

    Every attribute below is set by ``PaperWorkflowController.__init__``, and the
    primitives (``_require_runtime``, ``_apply_runtime_result``, ``_transition``)
    are provided by that class.
    """

    _phase: PaperWorkflowPhase
    _coordinator: PaperSessionCoordinator | None
    _result: PaperSessionResult | None
    _reconciliation_attempt_id: str | None
    _reconciliation_evidence: CoordinatorReconciliationEvidence | None

    def begin_manual_reconciliation(self) -> str:
        """Enter manual reconciliation only after a sticky safety halt."""

        if self._phase is not PaperWorkflowPhase.HALTED:
            raise WorkflowStateError(
                "Manual reconciliation requires a HALTED Paper session."
            )
        self._reconciliation_evidence = None
        self._reconciliation_attempt_id = uuid4().hex
        self._transition(PaperWorkflowPhase.RECONCILING, explicit_reconciliation=True)
        return self._reconciliation_attempt_id

    def complete_manual_reconciliation(
        self, attempt_id: str
    ) -> PaperSessionResult:
        """Publish one-shot evidence for the matching async attempt only."""

        if (
            self._phase is not PaperWorkflowPhase.RECONCILING
            or attempt_id != self._reconciliation_attempt_id
        ):
            raise WorkflowStateError(
                "Stale or inactive manual reconciliation result."
            )
        coordinator = self._coordinator
        if coordinator is None:
            self._reconciliation_attempt_id = None
            self._transition(PaperWorkflowPhase.HALTED)
            raise WorkflowStateError("No halted Paper runtime is available.")
        try:
            result, evidence = coordinator.capture_reconciliation_evidence()
        except (RuntimeError, ValueError) as error:
            self._reconciliation_attempt_id = None
            self._transition(PaperWorkflowPhase.HALTED)
            raise WorkflowStateError(str(error)) from error
        self._result = result
        self._reconciliation_attempt_id = None
        if evidence is None:
            self._reconciliation_evidence = None
            self._transition(PaperWorkflowPhase.HALTED)
            return result
        self._reconciliation_evidence = evidence
        self._transition(PaperWorkflowPhase.RECONCILING_READY)
        return result

    def fail_manual_reconciliation(self, attempt_id: str | None = None) -> bool:
        """Fail only the matching attempt; stale callbacks are ignored."""

        if self._phase is not PaperWorkflowPhase.RECONCILING:
            return False
        if attempt_id is not None and attempt_id != self._reconciliation_attempt_id:
            return False
        self._reconciliation_attempt_id = None
        self._reconciliation_evidence = None
        self._transition(PaperWorkflowPhase.HALTED)
        return True

    def confirm_manual_resume(self, evidence_id: str) -> PaperSessionResult:
        """Resume the existing coordinator after explicit reconciliation.

        The coordinator invokes the engine recovery exactly once while keeping
        the existing order port, health evaluator, and candidate context.  No
        connect, arm, submit, resubmit, or disconnect action occurs here.
        """

        evidence = self._reconciliation_evidence
        if (
            self._phase is not PaperWorkflowPhase.RECONCILING_READY
            or evidence is None
            or evidence.evidence_id != evidence_id
        ):
            raise WorkflowStateError(
                "Manual resume requires current reconciliation evidence."
            )
        coordinator = self._coordinator
        if coordinator is None:
            raise WorkflowStateError(
                "No halted Paper runtime is available for manual resume."
            )
        # Atomically consume before invoking a second broker refresh so a
        # duplicate click cannot recover the engine twice.
        self._reconciliation_evidence = None
        try:
            coordinator = coordinator.confirm_reconciliation(evidence)
        except (RuntimeError, ValueError) as error:
            self._transition(PaperWorkflowPhase.HALTED)
            raise WorkflowStateError(str(error)) from error
        result = coordinator.snapshot()
        self._coordinator = coordinator
        self._result = result
        if result.state.halted:
            self._transition(PaperWorkflowPhase.HALTED)
            return result
        self._transition(PaperWorkflowPhase.RUNNING, explicit_reconciliation=True)
        return result