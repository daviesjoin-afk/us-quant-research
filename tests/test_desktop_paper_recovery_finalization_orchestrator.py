"""Behavioural tests for v2O-E3: recovery, finalization and the shutdown verdict.

The E3 counterpart of the launch and active-session files.  No Qt application, no broker
and no real workflow: what is asserted is the *sequencing* this round moved off
``MainWindow`` -- when a halted session may be reconciled, which proof may resume it,
when the zero-state proof is due, and what a close must do about all of it.

Four properties carry the round, and each has tests that would fail on a plausible wrong
implementation rather than only on a missing one:

* **a halt is left by a human, twice.**  Reconciliation *collects evidence*; a separate
  explicit confirmation revalidates it.  Nothing on either path resumes a session,
  rebuilds an intent or resubmits an order, and a proof that is missing, consumed or
  superseded cannot resume anything -- which is why the confirmation re-reads the
  evidence *after* the operator answers rather than before the question;
* **the proof has a fixed order, and it releases nothing.**  Evidence is captured before
  the disconnect, the disconnect precedes the confirmation, and PAPER is released by no
  path except the workflow's own ``finalize_if_safe``.  A successful disconnect is not a
  finalization, and a broker that still reports a position or an unreconciled row means
  the ownership stays exactly where it is;
* **a refusal to schedule is not a failure.**  ``TaskSubmitter`` answering ``False``
  means the broker resource group was busy and nothing ran, so the session is left as it
  was; only a task that started and then failed halts it;
* **every result still has one path.**  Each E3 operation publishes through
  ``_publish_result`` exactly once, requests each event exactly once, and lets
  ``_after_result`` -- not the caller -- decide what the result implies.

A "refused" operation here is modelled as the real controller refuses it: by raising
``WorkflowStateError``.  The fakes record the *attempt* before raising, so "the
orchestrator never reached the workflow" and "the workflow refused it" stay
distinguishable -- a distinction the logs alone cannot make.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from us_quant.auto_launch import build_auto_launch_plan
from us_quant.desktop_v2.orchestration.paper.models import (
    PAPER_RELEASE_INVARIANT_CODE,
    RECONCILIATION_NO_SERVICE_MESSAGE,
    RESUME_EVIDENCE_MISSING_MESSAGE,
    RESUME_NOT_READY_MESSAGE,
    SHUTDOWN_FINALIZATION_PENDING_MESSAGE,
    SHUTDOWN_LAUNCH_IN_FLIGHT_REASON,
    SHUTDOWN_MANUAL_RECOVERY_MESSAGE,
    SHUTDOWN_OWNERSHIP_BLOCKED_MESSAGE,
    SHUTDOWN_STOP_REFUSED_REASON,
    SHUTDOWN_STOP_REQUESTED_MESSAGE,
    SHUTDOWN_UNPROVABLE_SESSION_REASON,
    PaperShutdownDisposition,
)
from us_quant.desktop_v2.orchestration.paper.orchestrator import (
    FINALIZATION_REFRESH_BACKOFF_SECONDS,
    PaperOrchestrator,
)
from us_quant.trading.application.paper.models import PaperTradingLifecycleError
from us_quant.trading.runtime.workflow import PaperWorkflowController
from us_quant.trading.runtime.workflow_state import (
    ExecutionLease,
    PaperWorkflowPhase,
    WorkflowStateError,
)

#: The three phases whose only exit is the operator.  Spelled out here rather than
#: imported: an assertion that reused the implementation's own rule would pass on a
#: wrong rule.
MANUAL_RECOVERY_PHASES = (
    PaperWorkflowPhase.HALTED,
    PaperWorkflowPhase.RECONCILING,
    PaperWorkflowPhase.RECONCILING_READY,
)

#: The finalized-session event the real coordinator produces.  Named so the
#: one-emission-per-operation tests can tell the two results apart by their events.
PAPER_FINALIZED_EVENT = "PAPER_FINALIZED"

#: "No result at all", as distinct from "a result that is not finalized".  The two are
#: different states -- one is a client whose session never started, the other is a
#: running session -- and several shutdown assertions turn on telling them apart.
_NO_RESULT = object()


# -- fakes ---------------------------------------------------------------


class _State:
    def __init__(self, *, halted: bool = False, finalized: bool = False) -> None:
        self.halted = halted
        self.finalized = finalized


class _Snapshot:
    def __init__(
        self,
        *,
        active: bool = False,
        positions: tuple[str, ...] = (),
        pending_orders: tuple[str, ...] = (),
        session_id: str = "session-1",
    ) -> None:
        self.active = active
        self.positions = positions
        self.pending_orders = pending_orders
        self.session_id = session_id


class _Event:
    def __init__(self, code: str, *, severity: str = "info") -> None:
        self.code = code
        self.severity = severity
        self.message = f"{code} happened"


class _Result:
    """A stand-in ``PaperSessionResult``."""

    def __init__(
        self,
        *,
        active: bool = False,
        halted: bool = False,
        finalized: bool = False,
        positions: tuple[str, ...] = (),
        pending_orders: tuple[str, ...] = (),
        events: tuple[_Event, ...] = (),
        session_id: str = "session-1",
    ) -> None:
        self.state = _State(halted=halted, finalized=finalized)
        self.engine_snapshot = _Snapshot(
            active=active,
            positions=positions,
            pending_orders=pending_orders,
            session_id=session_id,
        )
        self.events = events
        self.health = None


class _Evidence:
    def __init__(self, evidence_id: str) -> None:
        self.evidence_id = evidence_id


class _BrokerState:
    def __init__(self, positions: tuple[object, ...] = ()) -> None:
        self.positions = positions


class _Row:
    def __init__(self, reconciled: bool) -> None:
        self.reconciled = reconciled


class _Workflow:
    """A faithful ``PaperWorkflowController`` for the recovery surface.

    It mirrors the real controller where the orchestrator can observe it: the phase each
    operation performs, the one-shot evidence, the refusals -- including the three that
    matter most for this round, which are all "a *current* proof is required": a stale
    attempt, a consumed proof and a superseded one.  Every entry point records the
    attempt first, so a test can tell "the capability never called" from "the capability
    called and was refused".
    """

    def __init__(
        self,
        *,
        phase: PaperWorkflowPhase = PaperWorkflowPhase.HALTED,
        result: object = _NO_RESULT,
        evidence: str | None = None,
        reconcile_outcome: str = "ready",
        resume_outcome: str = "running",
        finalization_outcome: str = "finalized",
        finalize_returns: bool = True,
        stop_outcome: str = "stopping",
    ) -> None:
        self._phase = phase
        self._result: _Result | None = (
            _Result() if result is _NO_RESULT else result  # type: ignore[assignment]
        )
        self._evidence = _Evidence(evidence) if evidence is not None else None
        self._attempt: str | None = None
        self._finalization_evidence: str | None = None
        self.reconcile_outcome = reconcile_outcome
        self.resume_outcome = resume_outcome
        self.finalization_outcome = finalization_outcome
        self.finalize_returns = finalize_returns
        self.stop_outcome = stop_outcome
        self.calls: list[object] = []
        self.stop_requests = 0
        self.lease_released = False
        # Shared with the trading fake by ``_build``, so a test can assert the *interleaving*
        # of the two -- which is the only way "the evidence is captured before the
        # disconnect" is observable: each fake's own call list is internally in order
        # whichever of the two runs first.
        self.trace: list[str] = []

    # -- reads ---------------------------------------------------------

    @property
    def phase(self) -> PaperWorkflowPhase:
        return self._phase

    @property
    def result(self) -> _Result | None:
        return self._result

    @property
    def reconciliation_evidence(self) -> _Evidence | None:
        return self._evidence

    # -- the ordinary stop ---------------------------------------------

    def request_stop(self, stream_snapshot: object | None = None) -> _Result:
        """Model the real automatic route, including its two non-``STOPPING`` endings.

        A stop is not a phase edit: the same call can halt the session (a stale BUY that
        cannot be cancelled, an aged protective SELL, unsafe health) or finalize it
        outright.  An operator-only phase has no automatic stop at all, so asking for one
        there is a test bug and must be loud rather than silently accepted -- that is
        exactly the failure ``prepare_shutdown`` is there to prevent.
        """

        self.calls.append("request_stop")
        if self._phase not in {
            PaperWorkflowPhase.RUNNING,
            PaperWorkflowPhase.PAUSED,
        }:
            raise WorkflowStateError("A Paper stop requires a RUNNING or PAUSED session.")
        self.stop_requests += 1
        if self.stop_outcome == "refused":
            raise WorkflowStateError("No active Paper runtime is available.")
        if self.stop_outcome == "halt":
            self._phase = PaperWorkflowPhase.HALTED
            self._result = _Result(halted=True)
            return self._result
        if self.stop_outcome == "finalized":
            self._phase = PaperWorkflowPhase.FINALIZED
            self._result = _Result(
                finalized=True, events=(_Event(PAPER_FINALIZED_EVENT),)
            )
            return self._result
        self._phase = PaperWorkflowPhase.STOPPING
        return self._result  # type: ignore[return-value]

    # -- the recovery protocol -----------------------------------------

    def begin_manual_reconciliation(self) -> str:
        self.calls.append("begin_manual_reconciliation")
        if self._phase is not PaperWorkflowPhase.HALTED:
            raise WorkflowStateError(
                "Manual reconciliation requires a HALTED Paper session."
            )
        self._evidence = None
        self._attempt = "attempt-1"
        self._phase = PaperWorkflowPhase.RECONCILING
        return self._attempt

    def complete_manual_reconciliation(self, attempt_id: str) -> _Result:
        self.calls.append(("complete_manual_reconciliation", attempt_id))
        if self._phase is not PaperWorkflowPhase.RECONCILING or (
            attempt_id != self._attempt
        ):
            raise WorkflowStateError("Stale or inactive manual reconciliation result.")
        self._attempt = None
        if self.reconcile_outcome == "raise":
            self._phase = PaperWorkflowPhase.HALTED
            raise WorkflowStateError("the broker reading was not coherent")
        if self.reconcile_outcome == "unsafe":
            # The refresh ran and produced no proof: the session stays halted.
            self._evidence = None
            self._phase = PaperWorkflowPhase.HALTED
            return self._result  # type: ignore[return-value]
        self._evidence = _Evidence("reconciliation-1")
        self._phase = PaperWorkflowPhase.RECONCILING_READY
        return self._result  # type: ignore[return-value]

    def fail_manual_reconciliation(self, attempt_id: str | None = None) -> bool:
        self.calls.append(("fail_manual_reconciliation", attempt_id))
        if self._phase is not PaperWorkflowPhase.RECONCILING:
            return False
        if attempt_id is not None and attempt_id != self._attempt:
            return False
        self._attempt = None
        self._evidence = None
        self._phase = PaperWorkflowPhase.HALTED
        return True

    def confirm_manual_resume(self, evidence_id: str) -> _Result:
        self.calls.append(("confirm_manual_resume", evidence_id))
        evidence = self._evidence
        if (
            self._phase is not PaperWorkflowPhase.RECONCILING_READY
            or evidence is None
            or evidence.evidence_id != evidence_id
        ):
            raise WorkflowStateError(
                "Manual resume requires current reconciliation evidence."
            )
        # Consumed before the second refresh, exactly as the real controller does, so a
        # duplicate click cannot recover the engine twice.
        self._evidence = None
        if self.resume_outcome == "raise":
            self._phase = PaperWorkflowPhase.HALTED
            raise WorkflowStateError("fresh reconciliation evidence is no longer safe")
        if self.resume_outcome == "halt":
            self._phase = PaperWorkflowPhase.HALTED
            return self._result  # type: ignore[return-value]
        self._phase = PaperWorkflowPhase.RUNNING
        return self._result  # type: ignore[return-value]

    # -- finalization ---------------------------------------------------

    def capture_finalization_evidence(self) -> tuple[_Result, str | None]:
        self.calls.append("capture_finalization_evidence")
        self.trace.append("capture_finalization_evidence")
        if self._phase is not PaperWorkflowPhase.STOPPING:
            raise WorkflowStateError("Finalization evidence requires STOPPING.")
        if self.finalization_outcome == "halt":
            self._finalization_evidence = None
            self._phase = PaperWorkflowPhase.HALTED
            return self._result, None  # type: ignore[return-value]
        if self.finalization_outcome == "not_clear":
            return self._result, None  # type: ignore[return-value]
        self._finalization_evidence = "finalization-1"
        return self._result, "finalization-1"  # type: ignore[return-value]

    def confirm_finalization_after_disconnect(self, evidence_id: str) -> _Result:
        self.calls.append(("confirm_finalization_after_disconnect", evidence_id))
        self.trace.append("confirm_finalization_after_disconnect")
        if (
            self._phase is not PaperWorkflowPhase.STOPPING
            or self._finalization_evidence != evidence_id
        ):
            raise WorkflowStateError("Current finalization evidence is required.")
        self._finalization_evidence = None
        if self.finalization_outcome == "changed":
            self._phase = PaperWorkflowPhase.HALTED
            return _Result(halted=True)  # type: ignore[return-value]
        self._phase = PaperWorkflowPhase.FINALIZED
        self._result = _Result(
            finalized=True, events=(_Event(PAPER_FINALIZED_EVENT),)
        )
        return self._result  # type: ignore[return-value]

    def fail_finalization_refresh(self) -> bool:
        self.calls.append("fail_finalization_refresh")
        if self._phase is not PaperWorkflowPhase.STOPPING:
            return False
        self._finalization_evidence = None
        self._phase = PaperWorkflowPhase.HALTED
        return True

    def finalize_if_safe(self) -> bool:
        self.calls.append("finalize_if_safe")
        if self._result is None or not self._result.state.finalized:
            return False
        if self._phase not in {
            PaperWorkflowPhase.STOPPING,
            PaperWorkflowPhase.RECONCILING,
            PaperWorkflowPhase.RECONCILING_READY,
            PaperWorkflowPhase.FINALIZED,
        }:
            return False
        if not self.finalize_returns:
            return False
        self._phase = PaperWorkflowPhase.FINALIZED
        # The real controller's own semantics: the finalized snapshot has already been
        # observable, and the plan/coordinator/result are dropped here so a later read
        # cannot resurrect the session it just finished.
        self._result = None
        self.lease_released = True
        return True


class _Trading:
    """A recording ``PaperTradingService`` over one owned slot.

    The release is two-phase here exactly as it is in the service: ``reserve_active_release``
    proves and locks, ``commit_active_release`` drops, ``cancel_active_release`` gives the
    lock back.  A fake that offered a single ``clear_active`` would make the transaction
    boundary invisible, which is the one thing the tests below are about.
    """

    def __init__(
        self,
        *,
        owned: bool = True,
        connected: bool = True,
        broker_positions: tuple[object, ...] = (),
        disconnect_fails: bool = False,
        release_refusal: str | None = None,
        commit_fails: bool = False,
        cancel_refused: bool = False,
    ) -> None:
        self.owned = owned
        self.connected = connected
        self.broker_positions = broker_positions
        self.disconnect_fails = disconnect_fails
        self.release_refusal = release_refusal
        self.commit_fails = commit_fails
        self.cancel_refused = cancel_refused
        self.calls: list[str] = []
        self.trace: list[str] = []
        self._release_reservation: object | None = None
        self._workflow = None

    def has_order_service(self) -> bool:
        return self.owned

    def is_connected(self) -> bool:
        return self.connected

    def is_finalized(self) -> bool:
        workflow = self._workflow
        if workflow is None:
            return True
        result = workflow.result
        return result is None or bool(result.state.finalized)

    def phase(self) -> PaperWorkflowPhase:
        return self._workflow.phase if self._workflow is not None else (
            PaperWorkflowPhase.IDLE
        )

    def connect_active(self) -> object:
        self.calls.append("connect_active")
        self.connected = True
        return self

    def disconnect(self) -> None:
        self.calls.append("disconnect")
        self.trace.append("disconnect")
        if self.disconnect_fails:
            raise RuntimeError("the broker socket refused to close")
        self.connected = False

    def reserve_active_release(self) -> object:
        self.calls.append("reserve_active_release")
        if self.release_refusal is not None:
            raise PaperTradingLifecycleError(self.release_refusal)
        self._release_reservation = object()
        return self._release_reservation

    def commit_active_release(self, reservation: object) -> None:
        self.calls.append("commit_active_release")
        if self.commit_fails:
            raise PaperTradingLifecycleError(
                "stale or foreign Paper active-release reservation;"
                " refusing to commit it"
            )
        self._release_reservation = None
        self.owned = False

    def cancel_active_release(self, reservation: object) -> bool:
        self.calls.append("cancel_active_release")
        if self.cancel_refused or self._release_reservation is not reservation:
            return False
        self._release_reservation = None
        return True

    def broker_state(self) -> _BrokerState:
        self.calls.append("broker_state")
        return _BrokerState(self.broker_positions)


class _Submitted:
    def __init__(self, task, on_success, on_failure, **kwargs) -> None:
        self.task = task
        self.on_success = on_success
        self.on_failure = on_failure
        self.kwargs = kwargs
        self.ran = False

    def run(self) -> None:
        self.ran = True
        try:
            result = self.task(lambda _message: None)
        except Exception as error:  # noqa: BLE001 - the task boundary reports it
            if self.on_failure is not None:
                self.on_failure(str(error))
            return
        self.on_success(result)


class _Submitter:
    """Records submissions and runs them on demand.

    ``admit`` models the ``TaskSubmitter`` protocol honestly: ``False`` means the task was
    *refused and never started*, which is a different event from a task that ran and
    failed.  Several tests in this file turn on that distinction.
    """

    def __init__(self, *, admit: bool = True) -> None:
        self.admit = admit
        self.submissions: list[_Submitted] = []
        self.refusals = 0

    def __call__(
        self,
        task,
        *,
        on_success,
        on_failure=None,
        start_message,
        resource_group="research",
        suppress_busy_message=False,
        shutdown_essential=False,
        on_finished=None,
    ) -> bool:
        if not self.admit:
            self.refusals += 1
            return False
        self.submissions.append(
            _Submitted(
                task,
                on_success,
                on_failure,
                start_message=start_message,
                resource_group=resource_group,
                suppress_busy_message=suppress_busy_message,
                shutdown_essential=shutdown_essential,
            )
        )
        return True

    def run_next(self) -> None:
        """Run the oldest submission that has not run yet."""

        for submission in self.submissions:
            if not submission.ran:
                submission.run()
                return
        raise AssertionError("no submission left to run")

    def run_all(self) -> None:
        for submission in list(self.submissions):
            if not submission.ran:
                submission.run()


class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Events:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.results: list[object] = []
        self.runtime_events: list[object] = []
        self.refreshes = 0
        self.finalized = 0
        self.recovery_required = 0


class _Harness:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


def _build(
    *,
    workflow: _Workflow | None = None,
    trading: _Trading | None = None,
    submitter: _Submitter | None = None,
    rows: tuple[object, ...] = (),
    clock: _Clock | None = None,
) -> _Harness:
    """Assemble an orchestrator over the recovery fakes."""

    workflow = workflow or _Workflow()
    trading = trading if trading is not None else _Trading()
    trading._workflow = workflow
    # One trace, two owners: the finalization order is a claim about how the workflow
    # calls and the broker calls interleave, which neither list alone can show.  A real
    # controller has no trace, so a test driving one simply gets its own list back.
    trace = getattr(workflow, "trace", [])
    trading.trace = trace
    submitter = submitter or _Submitter()
    clock = clock or _Clock()
    events = _Events()
    seen_sessions: list[str] = []

    def rows_for(session_id: str):
        seen_sessions.append(session_id)
        return rows

    orchestrator = PaperOrchestrator(
        workflow_getter=lambda: workflow,
        paper_trading_getter=lambda: trading,
        build_session=lambda *args, **kwargs: None,
        submit_task=submitter,
        health_evaluator="health",
        preflight_provider=lambda: None,
        strategy_provider=lambda: None,
        candidates_provider=lambda: (),
        capital_limit_provider=lambda: 0,
        order_channel_provider=lambda: None,
        shadow_is_active=lambda: False,
        market_snapshot_provider=lambda: None,
        reconciliation_rows_provider=rows_for,
        clear_arm_confirmation=lambda: None,
        render_launch_state=lambda: None,
        render_launch_context=lambda summary: None,
        clock=clock,
    )
    orchestrator.log_requested.connect(events.logs.append)
    orchestrator.result_changed.connect(events.results.append)
    orchestrator.runtime_event_requested.connect(events.runtime_events.append)
    orchestrator.presentation_refresh_requested.connect(
        lambda: setattr(events, "refreshes", events.refreshes + 1)
    )
    orchestrator.session_finalized.connect(
        lambda: setattr(events, "finalized", events.finalized + 1)
    )
    orchestrator.manual_recovery_required.connect(
        lambda: setattr(events, "recovery_required", events.recovery_required + 1)
    )
    return _Harness(
        orchestrator=orchestrator,
        workflow=workflow,
        trading=trading,
        submitter=submitter,
        events=events,
        clock=clock,
        rows_seen=seen_sessions,
        trace=trace,
    )


def _halted(
    *,
    result: object = _NO_RESULT,
    reconcile_outcome: str = "ready",
    **kwargs: object,
) -> _Harness:
    """A halted, still-owned session -- the state every recovery path starts from."""

    return _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.HALTED,
            result=_Result(active=False) if result is _NO_RESULT else result,
            reconcile_outcome=reconcile_outcome,
        ),
        **kwargs,  # type: ignore[arg-type]
    )


def _stopping(
    *,
    result: object = _NO_RESULT,
    finalization_outcome: str = "finalized",
    **kwargs: object,
) -> _Harness:
    """A session on its way out: ``STOPPING``, owned, engine already dormant."""

    return _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.STOPPING,
            result=_Result(active=False) if result is _NO_RESULT else result,
            finalization_outcome=finalization_outcome,
        ),
        **kwargs,  # type: ignore[arg-type]
    )


def _finalized(*, finalize_returns: bool = True, **kwargs: object) -> _Harness:
    """A finished session whose result is still published and whose owner is still held.

    This is the state a finalized session is left in when the release sequencing could
    not prove it safe, and it is the state ``prepare_shutdown``'s ownership branch (and a
    later result) has to decide about.
    """

    return _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.FINALIZED,
            result=_Result(finalized=True),
            finalize_returns=finalize_returns,
        ),
        **kwargs,  # type: ignore[arg-type]
    )


# -- reconciliation: HALTED -> RECONCILING --------------------------------


def test_reconciliation_moves_a_halted_session_to_reconciling() -> None:
    harness = _halted()

    harness.orchestrator.reconcile()

    assert harness.workflow.phase is PaperWorkflowPhase.RECONCILING
    assert "begin_manual_reconciliation" in harness.workflow.calls
    # The task exists, and it is the broker group's -- reconciliation is broker work.
    assert len(harness.submitter.submissions) == 1
    assert harness.submitter.submissions[0].kwargs["resource_group"] == "broker"
    # And the controls were repainted, because the phase moved without a result.
    assert harness.events.refreshes == 1


def test_reconciliation_refuses_a_halted_session_with_no_order_service() -> None:
    """The precondition is checked before the phase moves, so nothing is left half-open."""

    harness = _halted(trading=_Trading(owned=False))

    harness.orchestrator.reconcile()

    assert harness.workflow.calls == []
    assert harness.workflow.phase is PaperWorkflowPhase.HALTED
    assert harness.submitter.submissions == []
    assert harness.events.logs == [RECONCILIATION_NO_SERVICE_MESSAGE]


def test_reconciliation_reconnects_the_disconnected_service_and_collects_evidence() -> None:
    """Reconnect is collection only: no resume, no replacement intent, no resubmit."""

    harness = _halted(trading=_Trading(owned=True, connected=False))

    harness.orchestrator.reconcile()
    harness.submitter.run_next()

    assert "connect_active" in harness.trading.calls
    assert harness.workflow.phase is PaperWorkflowPhase.RECONCILING_READY
    assert harness.workflow.reconciliation_evidence is not None
    # The engine was never asked to resume, and no broker write happened at all.
    assert not any(
        isinstance(call, tuple) and call[0] == "confirm_manual_resume"
        for call in harness.workflow.calls
    )
    assert harness.trading.calls == ["connect_active"]


def test_reconciliation_does_not_reconnect_an_already_live_service() -> None:
    """A connected service is left alone: a second connect would be a second socket."""

    harness = _halted(trading=_Trading(owned=True, connected=True))

    harness.orchestrator.reconcile()
    harness.submitter.run_next()

    assert "connect_active" not in harness.trading.calls
    assert harness.workflow.phase is PaperWorkflowPhase.RECONCILING_READY


def test_a_successful_reconciliation_publishes_its_result_once() -> None:
    harness = _halted(result=_Result(active=False, events=(_Event("EVIDENCE"),)))

    harness.orchestrator.reconcile()
    harness.submitter.run_next()

    assert len(harness.events.results) == 1
    assert [event.code for event in harness.events.runtime_events] == ["EVIDENCE"]


def test_reconciliation_that_cannot_take_a_proof_stays_halted() -> None:
    """No evidence means no ``RECONCILING_READY``: the halt is sticky."""

    harness = _halted(reconcile_outcome="unsafe")

    harness.orchestrator.reconcile()
    harness.submitter.run_next()

    assert harness.workflow.phase is PaperWorkflowPhase.HALTED
    assert harness.workflow.reconciliation_evidence is None
    assert harness.events.results == [harness.workflow.result]
    assert harness.events.recovery_required == 1


def test_a_reconciliation_task_that_fails_returns_to_sticky_halted() -> None:
    harness = _halted(reconcile_outcome="raise")

    harness.orchestrator.reconcile()
    harness.submitter.run_next()

    assert harness.workflow.phase is PaperWorkflowPhase.HALTED
    # The failed attempt is failed explicitly, so no zombie RECONCILING is left behind.
    assert any(
        isinstance(call, tuple) and call[0] == "fail_manual_reconciliation"
        for call in harness.workflow.calls
    )
    assert harness.events.recovery_required == 1


def test_a_reconciliation_that_is_never_admitted_rolls_its_attempt_back() -> None:
    """A refused task must not leave the workflow in ``RECONCILING``.

    Nothing was scheduled, so there is no proof coming and the phase would be a zombie
    whose only exit is a confirmation of evidence nobody took.
    """

    harness = _halted(submitter=_Submitter(admit=False))

    harness.orchestrator.reconcile()

    assert harness.workflow.phase is PaperWorkflowPhase.HALTED
    assert harness.submitter.refusals == 1
    assert harness.workflow.calls == [
        "begin_manual_reconciliation",
        ("fail_manual_reconciliation", "attempt-1"),
    ]
    assert harness.events.refreshes == 2


def test_reconciliation_is_refused_outside_a_halted_session() -> None:
    """The refusal is the workflow's, and it is logged rather than repaired."""

    harness = _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.RUNNING, result=_Result(active=True)
        )
    )

    harness.orchestrator.reconcile()

    assert harness.submitter.submissions == []
    assert harness.events.logs == [
        "Manual reconciliation requires a HALTED Paper session."
    ]
    assert harness.workflow.phase is PaperWorkflowPhase.RUNNING


# -- confirmation: RECONCILING_READY -> RUNNING ---------------------------


def test_resume_without_a_fresh_proof_is_refused_with_no_task_and_no_result() -> None:
    """Nothing is confirmed, nothing is submitted, and no result is fabricated."""

    harness = _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.HALTED, result=_Result(active=False)
        )
    )

    harness.orchestrator.confirm_reconciliation_resume()

    assert harness.workflow.calls == []
    assert harness.submitter.submissions == []
    assert harness.events.results == []
    assert harness.events.logs == [RESUME_NOT_READY_MESSAGE]


def test_resume_from_the_ready_phase_without_a_proof_is_also_refused() -> None:
    """``RECONCILING_READY`` with no evidence is the consumed-proof state."""

    harness = _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.RECONCILING_READY,
            result=_Result(active=False),
            evidence=None,
        )
    )

    harness.orchestrator.confirm_reconciliation_resume()

    assert harness.submitter.submissions == []
    assert harness.events.results == []
    assert harness.events.logs == [RESUME_EVIDENCE_MISSING_MESSAGE]


def test_a_confirmed_resume_calls_the_workflow_exactly_once() -> None:
    workflow = _Workflow(
        phase=PaperWorkflowPhase.RECONCILING_READY,
        result=_Result(active=False),
        evidence="reconciliation-1",
    )
    harness = _build(workflow=workflow)

    harness.orchestrator.confirm_reconciliation_resume()
    harness.submitter.run_next()

    confirmations = [
        call
        for call in workflow.calls
        if isinstance(call, tuple) and call[0] == "confirm_manual_resume"
    ]
    assert confirmations == [("confirm_manual_resume", "reconciliation-1")]
    assert workflow.phase is PaperWorkflowPhase.RUNNING
    assert len(harness.events.results) == 1


def test_a_consumed_proof_cannot_resume_a_session() -> None:
    """The proof is read *after* the confirmation, so a proof consumed meanwhile fails.

    This is the property the ordering exists for: reading it before the dialog would let
    a proof that has since been consumed -- by a duplicate click, by another window -- be
    revalidated as if it were still current.
    """

    workflow = _Workflow(
        phase=PaperWorkflowPhase.RECONCILING_READY,
        result=_Result(active=False),
        evidence="reconciliation-1",
    )
    harness = _build(workflow=workflow)

    harness.orchestrator.confirm_reconciliation_resume()
    # Consumed between the read and the task running.
    workflow._evidence = None
    harness.submitter.run_next()

    # The workflow refuses without moving the phase: the session is still one only the
    # operator can leave, which is why the refusal is announced rather than repaired.
    assert workflow.phase is PaperWorkflowPhase.RECONCILING_READY
    assert harness.events.results == []
    assert harness.events.recovery_required == 1


def test_a_superseded_proof_cannot_resume_a_session() -> None:
    """A different id is a different proof, and the stale one resumes nothing."""

    workflow = _Workflow(
        phase=PaperWorkflowPhase.RECONCILING_READY,
        result=_Result(active=False),
        evidence="reconciliation-1",
    )
    harness = _build(workflow=workflow)

    harness.orchestrator.confirm_reconciliation_resume()
    workflow._evidence = _Evidence("reconciliation-2")
    harness.submitter.run_next()

    assert workflow.phase is PaperWorkflowPhase.RECONCILING_READY
    assert harness.events.results == []
    assert harness.events.recovery_required == 1


def test_a_resume_that_halts_the_engine_ends_halted() -> None:
    harness = _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.RECONCILING_READY,
            result=_Result(active=False),
            evidence="reconciliation-1",
            resume_outcome="halt",
        )
    )

    harness.orchestrator.confirm_reconciliation_resume()
    harness.submitter.run_next()

    assert harness.workflow.phase is PaperWorkflowPhase.HALTED
    assert len(harness.events.results) == 1
    assert harness.events.recovery_required == 1


def test_a_resume_that_is_never_admitted_keeps_the_proof_available() -> None:
    """The workflow was never reached, so the proof survives for another attempt."""

    workflow = _Workflow(
        phase=PaperWorkflowPhase.RECONCILING_READY,
        result=_Result(active=False),
        evidence="reconciliation-1",
    )
    harness = _build(workflow=workflow, submitter=_Submitter(admit=False))

    harness.orchestrator.confirm_reconciliation_resume()

    assert workflow.phase is PaperWorkflowPhase.RECONCILING_READY
    assert workflow.reconciliation_evidence is not None
    assert ("confirm_manual_resume", "reconciliation-1") not in workflow.calls
    assert harness.events.refreshes == 1


def test_resume_never_reconnects_or_resubmits() -> None:
    """The resumed session keeps its own order port: nothing here touches the broker."""

    harness = _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.RECONCILING_READY,
            result=_Result(active=False),
            evidence="reconciliation-1",
        )
    )

    harness.orchestrator.confirm_reconciliation_resume()
    harness.submitter.run_next()

    assert harness.trading.calls == []


# -- the finalization gate ------------------------------------------------


def test_a_stopping_result_schedules_the_proof_once() -> None:
    harness = _stopping()

    harness.orchestrator._publish_result(_Result(active=False))

    assert len(harness.submitter.submissions) == 1
    assert harness.orchestrator._finalization_inflight is True
    assert harness.submitter.submissions[0].kwargs["resource_group"] == "broker"
    assert harness.submitter.submissions[0].kwargs["shutdown_essential"] is True
    assert harness.submitter.submissions[0].kwargs["suppress_busy_message"] is True


def test_a_proof_already_in_flight_is_never_submitted_again() -> None:
    harness = _stopping()
    harness.orchestrator._publish_result(_Result(active=False))

    harness.orchestrator._publish_result(_Result(active=False))

    assert len(harness.submitter.submissions) == 1


def test_an_active_engine_holds_the_proof_back_for_the_backoff_window() -> None:
    """While the exits are still working, a tick must not re-read the whole broker."""

    clock = _Clock()
    harness = _stopping(clock=clock)
    harness.orchestrator._publish_result(_Result(active=True))

    clock.advance(FINALIZATION_REFRESH_BACKOFF_SECONDS - 0.001)
    harness.orchestrator._finalization_inflight = False
    harness.orchestrator._publish_result(_Result(active=True))

    assert len(harness.submitter.submissions) == 1


def test_the_backoff_expires_and_the_next_attempt_is_allowed() -> None:
    clock = _Clock()
    harness = _stopping(clock=clock)
    harness.orchestrator._publish_result(_Result(active=True))

    clock.advance(FINALIZATION_REFRESH_BACKOFF_SECONDS)
    harness.orchestrator._finalization_inflight = False
    harness.orchestrator._publish_result(_Result(active=True))

    assert len(harness.submitter.submissions) == 2


def test_a_dormant_engine_is_not_held_back_at_all() -> None:
    """The backoff exists for a flattening session; a dormant one has nothing to wait for."""

    clock = _Clock()
    harness = _stopping(clock=clock)
    harness.orchestrator._publish_result(_Result(active=False))

    harness.orchestrator._finalization_inflight = False
    harness.orchestrator._publish_result(_Result(active=False))

    assert len(harness.submitter.submissions) == 2


def test_a_finalized_result_schedules_nothing() -> None:
    harness = _stopping(result=_Result(finalized=True))

    harness.orchestrator._publish_result(_Result(finalized=True))

    assert harness.submitter.submissions == []


@pytest.mark.parametrize(
    "phase",
    [row for row in PaperWorkflowPhase if row is not PaperWorkflowPhase.STOPPING],
)
def test_only_stopping_schedules_the_proof(phase: PaperWorkflowPhase) -> None:
    """Every other phase is a no-op, and the workflow is not even asked."""

    harness = _build(
        workflow=_Workflow(phase=phase, result=_Result(active=True)),
        trading=_Trading(),
    )

    harness.orchestrator._publish_result(_Result(active=True))

    assert harness.submitter.submissions == []
    assert harness.workflow.calls == []
    assert harness.trading.calls == []


def test_a_session_with_no_order_service_fails_the_refresh_and_requires_the_operator() -> None:
    """There is nothing to prove against, so the workflow itself has to fail the refresh."""

    harness = _stopping(trading=_Trading(owned=False))

    harness.orchestrator._publish_result(_Result(active=False))

    assert harness.workflow.phase is PaperWorkflowPhase.HALTED
    assert "fail_finalization_refresh" in harness.workflow.calls
    assert harness.submitter.submissions == []
    assert harness.events.recovery_required == 1


def test_a_busy_broker_group_defers_the_proof_without_halting() -> None:
    """``False`` means nothing ran, so nothing failed -- the session is left as it was."""

    harness = _stopping(submitter=_Submitter(admit=False))

    harness.orchestrator._publish_result(_Result(active=False))

    assert harness.workflow.phase is PaperWorkflowPhase.STOPPING
    assert "fail_finalization_refresh" not in harness.workflow.calls
    assert harness.orchestrator._finalization_inflight is False
    assert harness.events.refreshes == 1
    assert harness.events.recovery_required == 0


def test_the_proof_keeps_its_flag_up_until_its_result_has_been_acted_on() -> None:
    """Clearing it first would re-enter the scheduler from the result being published."""

    harness = _stopping()
    harness.orchestrator._publish_result(_Result(active=False))
    assert harness.orchestrator._finalization_inflight is True

    harness.submitter.run_next()

    assert harness.orchestrator._finalization_inflight is False
    # Exactly one proof was asked for, start to finish.
    assert len(harness.submitter.submissions) == 1


def test_a_proof_that_really_fails_halts_the_session() -> None:
    harness = _stopping(finalization_outcome="changed")

    harness.orchestrator._publish_result(_Result(active=False))
    harness.submitter.run_all()

    assert harness.workflow.phase is PaperWorkflowPhase.HALTED
    assert harness.events.recovery_required == 1
    # The disconnect ran, as the order requires, but no ownership was given up: a halt
    # needs the session it halted.
    assert harness.trading.calls == ["disconnect"]
    assert harness.trading.owned is True
    assert "clear_active" not in harness.trading.calls


def test_a_proof_task_that_raises_halts_the_session_and_keeps_ownership() -> None:
    harness = _stopping(trading=_Trading(disconnect_fails=True))

    harness.orchestrator._publish_result(_Result(active=False))
    harness.submitter.run_all()

    assert harness.workflow.phase is PaperWorkflowPhase.HALTED
    assert harness.orchestrator._finalization_inflight is False
    assert harness.trading.calls == ["disconnect"]
    assert harness.trading.owned is True
    assert harness.events.recovery_required == 1


def test_a_halted_session_never_resumes_the_proof_automatically() -> None:
    """Once halted, further results must not start another proof."""

    harness = _stopping(finalization_outcome="changed")

    harness.orchestrator._publish_result(_Result(active=False))
    harness.submitter.run_all()
    assert harness.workflow.phase is PaperWorkflowPhase.HALTED

    harness.orchestrator._publish_result(_Result(active=False, halted=True))

    assert len(harness.submitter.submissions) == 1


# -- the proof's fixed order ---------------------------------------------


def test_the_proof_captures_evidence_then_disconnects_then_confirms() -> None:
    """The interleaving is the claim, so it is asserted across both owners at once."""

    harness = _stopping()

    harness.orchestrator._publish_result(_Result(active=False))
    harness.submitter.run_next()

    assert harness.trace[:3] == [
        "capture_finalization_evidence",
        "disconnect",
        "confirm_finalization_after_disconnect",
    ]


def test_a_proof_that_is_not_clear_disconnects_nothing() -> None:
    """No evidence means no disconnect: the exits are still working."""

    harness = _stopping(finalization_outcome="not_clear")

    harness.orchestrator._publish_result(_Result(active=False))
    harness.submitter.run_next()

    assert harness.workflow.calls == ["capture_finalization_evidence"]
    assert harness.trading.calls == []
    assert harness.workflow.phase is PaperWorkflowPhase.STOPPING


def test_a_proof_that_halts_on_capture_disconnects_nothing() -> None:
    harness = _stopping(finalization_outcome="halt")

    harness.orchestrator._publish_result(_Result(active=False))
    harness.submitter.run_next()

    assert harness.trading.calls == []
    assert harness.workflow.phase is PaperWorkflowPhase.HALTED


def test_a_disconnect_failure_releases_nothing() -> None:
    """The proof raises before the confirmation, so neither PAPER nor the slot is freed."""

    harness = _stopping(trading=_Trading(disconnect_fails=True))

    harness.orchestrator._publish_result(_Result(active=False))
    harness.submitter.run_all()

    assert not any(
        isinstance(call, tuple)
        and call[0] == "confirm_finalization_after_disconnect"
        for call in harness.workflow.calls
    )
    assert harness.workflow.lease_released is False
    assert "clear_active" not in harness.trading.calls
    assert harness.trading.owned is True


# -- releasing the session ------------------------------------------------


def test_every_proof_passing_releases_the_slot_after_the_workflow_agrees() -> None:
    harness = _finalized()

    harness.orchestrator._publish_result(_Result(finalized=True))

    assert "disconnect" in harness.trading.calls
    assert "finalize_if_safe" in harness.workflow.calls
    assert "commit_active_release" in harness.trading.calls
    assert harness.workflow.lease_released is True
    assert harness.trading.owned is False
    assert harness.events.finalized == 1
    # The release is ordered, not merely all present: disconnect, then the reservation
    # that proves and locks the slot, then the workflow's own gate, then the commit.
    assert harness.trading.calls == [
        "broker_state",
        "disconnect",
        "reserve_active_release",
        "commit_active_release",
    ]
    assert harness.workflow.calls == ["finalize_if_safe"]


def test_a_broker_position_stops_the_release() -> None:
    harness = _finalized(trading=_Trading(broker_positions=("AAPL",)))

    harness.orchestrator._publish_result(_Result(finalized=True))

    assert harness.trading.calls == ["broker_state"]
    assert harness.workflow.calls == []
    assert harness.trading.owned is True
    assert harness.workflow.lease_released is False
    assert harness.events.finalized == 0


def test_an_unreconciled_row_stops_the_release() -> None:
    harness = _finalized(rows=(_Row(reconciled=True), _Row(reconciled=False)))

    harness.orchestrator._publish_result(_Result(finalized=True))

    assert harness.trading.calls == ["broker_state"]
    assert harness.workflow.lease_released is False
    assert harness.events.finalized == 0


def test_the_journal_is_read_for_the_finished_session_only() -> None:
    harness = _finalized(rows=(_Row(reconciled=True),))

    harness.orchestrator._publish_result(_Result(finalized=True))

    assert harness.rows_seen == ["session-1"]


def test_a_workflow_refusal_gives_the_reservation_back_and_keeps_the_slot() -> None:
    """``finalize_if_safe`` is the gate, and a refusal costs only the reservation.

    Nothing was dropped: the slot is held and *unlocked* exactly as it was found, so an
    operator who fixes the cause can try again.
    """

    harness = _finalized(finalize_returns=False)

    harness.orchestrator._publish_result(_Result(finalized=True))

    assert "disconnect" in harness.trading.calls
    assert "reserve_active_release" in harness.trading.calls
    assert "finalize_if_safe" in harness.workflow.calls
    assert "cancel_active_release" in harness.trading.calls
    assert "commit_active_release" not in harness.trading.calls
    assert harness.trading.owned is True
    assert harness.workflow.lease_released is False
    assert harness.events.finalized == 0


def test_a_release_the_slot_refuses_never_reaches_the_workflow() -> None:
    """The transaction boundary, asserted as the thing that cannot happen.

    This is the failure the two-phase release exists for.  ``finalize_if_safe`` is a
    check-and-commit call on a canonical owner: once it answers ``True`` the PAPER lease
    is gone.  So the slot's releasability is *proved and locked first* -- and when it
    cannot be (a promotion claim still holds it, the service still reports connected,
    the slot changed) the workflow is never asked at all.

    Asserting "``finalize_if_safe`` was not called" is the strongest available evidence,
    and it is stronger than asserting a flag: the lease, the workflow's result, its
    coordinator and both evidence records are all released or dropped *inside* that call,
    so a workflow the capability never reached has been left completely untouched.  An
    earlier shape asked the workflow first and cleaned up afterwards whose only cleanup
    was a return value, which left PAPER released with the ownership still held.
    """

    harness = _finalized(
        trading=_Trading(
            release_refusal="Paper candidate '1' holds the promotion reservation;"
            " refusing to reserve the slot it is reserved to"
        )
    )

    harness.orchestrator._publish_result(_Result(finalized=True))

    assert harness.workflow.calls == []
    assert harness.workflow.lease_released is False
    assert harness.workflow.result is not None
    assert harness.trading.calls == ["broker_state", "disconnect", "reserve_active_release"]
    assert harness.trading.owned is True
    assert harness.events.finalized == 0
    # And the failure is a plain refusal, not an invariant report: nothing was corrupted.
    assert harness.events.runtime_events == []


def test_a_release_that_cannot_be_committed_is_reported_as_an_invariant() -> None:
    """Unreachable while the reservation locks the slot -- and not described as ordinary.

    What it would leave behind is PAPER released with the ownership still held, which is
    a different situation for the operator from a claim that never finished, so it is
    filed under its own code and never reported as a clean ``READY``.
    """

    harness = _finalized(trading=_Trading(commit_fails=True))

    harness.orchestrator._publish_result(_Result(finalized=True))

    assert harness.workflow.lease_released is True
    assert harness.trading.owned is True
    assert harness.events.finalized == 0
    assert [event.code for event in harness.events.runtime_events] == [
        PAPER_RELEASE_INVARIANT_CODE
    ]


def test_a_reservation_that_cannot_be_given_back_stops_the_release() -> None:
    """A lock that cannot be released is reported rather than treated as a clean refusal.

    Nothing was dropped in this branch -- the workflow refused, so the lease is intact --
    so the outcome is still fail-closed rather than corrupted; what it must not do is
    proceed as if the slot had been given back.
    """

    harness = _finalized(
        finalize_returns=False, trading=_Trading(cancel_refused=True)
    )

    harness.orchestrator._publish_result(_Result(finalized=True))

    assert "commit_active_release" not in harness.trading.calls
    assert harness.trading.owned is True
    assert harness.workflow.lease_released is False
    assert harness.events.finalized == 0


def test_a_finalized_result_is_released_exactly_once() -> None:
    """The workflow drops the result on release, so a second result releases nothing."""

    harness = _finalized()

    harness.orchestrator._publish_result(_Result(finalized=True))
    harness.orchestrator._publish_result(_Result(finalized=True))

    assert harness.trading.calls.count("commit_active_release") == 1
    assert harness.events.finalized == 1


def test_an_unfinished_session_is_never_released() -> None:
    """A result that does not report itself finalized releases nothing at all."""

    harness = _stopping(submitter=_Submitter(admit=False))

    harness.orchestrator._publish_result(_Result(active=False))

    assert harness.trading.calls == []
    assert harness.workflow.calls == []
    assert harness.events.finalized == 0


def test_the_release_helper_refuses_an_unfinalized_result_of_its_own_accord() -> None:
    """The helper does not trust its caller: the finalized fact is its own precondition.

    Both callers check it first, so removing the check *here* is invisible through them
    -- which is exactly why it is asserted directly.  A "refuse or release" helper that
    released on an unfinalized result would be one caller away from dropping a live
    session's ownership.
    """

    harness = _finalized()

    reason = harness.orchestrator._release_paper_ownership_if_proven(
        _Result(finalized=False)
    )

    assert reason == "the session does not report itself finalized"
    assert harness.trading.calls == []
    assert harness.workflow.calls == []
    assert harness.trading.owned is True


# -- the operator-recovery announcement -----------------------------------


@pytest.mark.parametrize("phase", MANUAL_RECOVERY_PHASES)
def test_a_manual_recovery_phase_is_announced(phase: PaperWorkflowPhase) -> None:
    harness = _build(workflow=_Workflow(phase=phase, result=_Result(active=False)))

    harness.orchestrator._publish_result(_Result(active=False))

    assert harness.events.recovery_required == 1


@pytest.mark.parametrize(
    "phase",
    [row for row in PaperWorkflowPhase if row not in MANUAL_RECOVERY_PHASES],
)
def test_an_automatic_phase_is_not_announced(phase: PaperWorkflowPhase) -> None:
    """A drain that still has an automatic route must keep the admission gate down."""

    harness = _build(
        workflow=_Workflow(phase=phase, result=_Result(active=True)),
        submitter=_Submitter(admit=False),
    )

    harness.orchestrator._publish_result(_Result(active=True))

    assert harness.events.recovery_required == 0


# -- every E3 result still has one path ----------------------------------


def test_each_recovery_result_is_published_once_with_its_events_once() -> None:
    """One emission per operation, and one runtime-event request per event."""

    events = (_Event("A"), _Event("B", severity="warning"), _Event("C"))
    workflow = _Workflow(
        phase=PaperWorkflowPhase.RECONCILING_READY,
        result=_Result(active=False, events=events),
        evidence="reconciliation-1",
    )
    harness = _build(workflow=workflow)

    harness.orchestrator.confirm_reconciliation_resume()
    harness.submitter.run_next()

    assert len(harness.events.results) == 1
    assert [event.code for event in harness.events.runtime_events] == ["A", "B", "C"]


def test_each_finalization_result_is_published_once_with_its_events_once() -> None:
    """The scheduled result and the proof's own result are two operations, two emissions."""

    events = (_Event("F1"), _Event("F2", severity="error"))
    harness = _stopping(result=_Result(active=False, events=events))

    harness.orchestrator._publish_result(_Result(active=False, events=events))
    harness.submitter.run_next()

    assert len(harness.events.results) == 2
    assert [event.code for event in harness.events.runtime_events] == [
        "F1",
        "F2",
        PAPER_FINALIZED_EVENT,
    ]


# -- prepare_shutdown -----------------------------------------------------


def test_shutdown_of_a_running_session_reuses_the_capabilitys_own_stop() -> None:
    harness = _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.RUNNING, result=_Result(active=True)
        )
    )

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.WAITING_FOR_FINALIZATION
    assert verdict.message == SHUTDOWN_STOP_REQUESTED_MESSAGE
    assert harness.workflow.stop_requests == 1
    assert harness.workflow.phase is PaperWorkflowPhase.STOPPING


def test_shutdown_of_a_paused_session_reuses_the_same_stop() -> None:
    harness = _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.PAUSED, result=_Result(active=True)
        )
    )

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.WAITING_FOR_FINALIZATION
    assert harness.workflow.stop_requests == 1


def test_shutdown_stops_a_running_session_exactly_once() -> None:
    """No second stop request, and no direct ``request_stop`` from here."""

    harness = _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.RUNNING, result=_Result(active=True)
        )
    )

    harness.orchestrator.prepare_shutdown()

    assert harness.workflow.calls.count("request_stop") == 1


def test_shutdown_of_a_stopping_session_does_not_disconnect_it() -> None:
    """The exits are still working and the proof is what observes them."""

    harness = _stopping(submitter=_Submitter(admit=False))

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.WAITING_FOR_FINALIZATION
    assert verdict.message == SHUTDOWN_FINALIZATION_PENDING_MESSAGE
    assert harness.trading.calls == []
    assert harness.workflow.calls == []


@pytest.mark.parametrize("phase", MANUAL_RECOVERY_PHASES)
def test_shutdown_of_an_operator_only_phase_requires_the_operator(
    phase: PaperWorkflowPhase,
) -> None:
    """No confirmation is made on the operator's behalf, and nothing is released."""

    harness = _build(
        workflow=_Workflow(phase=phase, result=_Result(active=False)),
        trading=_Trading(),
    )

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.MANUAL_RECOVERY_REQUIRED
    assert verdict.message == SHUTDOWN_MANUAL_RECOVERY_MESSAGE
    assert harness.trading.calls == []
    assert harness.workflow.calls == []


def test_shutdown_of_a_finalized_session_with_no_ownership_is_ready() -> None:
    harness = _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.FINALIZED, result=_Result(finalized=True)
        ),
        trading=_Trading(owned=False),
    )

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.READY
    assert verdict.message == ""
    assert harness.trading.calls == []
    assert harness.workflow.calls == []


def test_shutdown_of_a_never_launched_client_is_ready() -> None:
    harness = _build(
        workflow=_Workflow(phase=PaperWorkflowPhase.READY, result=None),
        trading=_Trading(owned=False),
    )

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.READY


def test_shutdown_releases_a_finalized_session_that_still_owns_its_slot() -> None:
    harness = _finalized()

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.READY
    assert harness.trading.owned is False
    assert harness.workflow.lease_released is True
    assert harness.events.finalized == 1


def test_shutdown_blocks_when_an_ownership_cannot_be_released() -> None:
    """Fail closed: the refusal is reported and nothing is forced to let the process exit.

    And crucially the *diagnosis* survives into the dialog, so the operator is told which
    of the blocked situations they are in rather than one catch-all sentence.
    """

    harness = _finalized(
        trading=_Trading(
            release_refusal="Paper candidate '1' holds the promotion reservation"
        )
    )

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.OWNERSHIP_BLOCKED
    assert SHUTDOWN_OWNERSHIP_BLOCKED_MESSAGE in verdict.message
    assert "holds the promotion reservation" in verdict.message
    assert harness.trading.owned is True
    assert harness.events.finalized == 0
    # The workflow was never asked, so the lease is untouched.
    assert harness.workflow.calls == []
    assert harness.workflow.lease_released is False


def test_shutdown_of_a_connecting_attempt_is_never_ready() -> None:
    """The canonical phase decides, not "the workflow holds no result yet".

    Driven on the **real** controller, because the shape being guarded is one this service
    and the workflow disagree about: ``CONNECTING`` legitimately has no result, so
    ``is_finalized()`` -- which reads ``result is None`` as "nothing awaits finalization" --
    answers ``True`` while the launch is holding the PAPER lease and may already have a
    connected candidate.  Classifying from that answer would let a close walk straight
    past an owned session into the generic teardown.
    """

    workflow = PaperWorkflowController()
    # Driven through the controller's own API rather than by poking the phase, so the
    # state is one production can really reach: IDLE -> PREPARING -> READY -> CONNECTING.
    workflow.begin_preparing()
    workflow.mark_ready()
    workflow.begin_connecting(
        build_auto_launch_plan(
            attempt_id=1,
            strategy_version_id="version-1",
            parameter_hash="hash-1",
            candidate_symbols=("AAPL",),
            requested_capital_limit=Decimal("1000"),
        )
    )
    assert workflow.phase is PaperWorkflowPhase.CONNECTING
    assert workflow.result is None
    assert workflow.lease is ExecutionLease.PAPER

    harness = _build(workflow=workflow, trading=_Trading(owned=False))

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is not PaperShutdownDisposition.READY
    assert verdict.disposition is PaperShutdownDisposition.OWNERSHIP_BLOCKED
    assert SHUTDOWN_OWNERSHIP_BLOCKED_MESSAGE in verdict.message
    # Nothing was released on the way to that verdict.
    assert workflow.lease is ExecutionLease.PAPER
    assert harness.trading.calls == []


def test_shutdown_blocks_a_promotion_claim_with_no_result_at_all() -> None:
    """E1's invariant path: an owned slot and no session to prove anything about."""

    harness = _build(
        workflow=_Workflow(phase=PaperWorkflowPhase.CONNECTING, result=None),
        trading=_Trading(owned=True, connected=False),
    )

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.OWNERSHIP_BLOCKED
    assert SHUTDOWN_OWNERSHIP_BLOCKED_MESSAGE in verdict.message
    assert SHUTDOWN_LAUNCH_IN_FLIGHT_REASON in verdict.message
    # Nothing was disconnected, released or reserved on the way to that verdict.
    assert harness.trading.calls == []
    assert harness.workflow.calls == []
    assert harness.trading.owned is True


@pytest.mark.parametrize("owned", [True, False])
def test_shutdown_of_a_connecting_attempt_blocks_either_way(owned: bool) -> None:
    """Whether or not a slot is held, a launch in flight is not closable.

    A ``CONNECTING`` workflow owns the PAPER lease and a candidate that may be connected
    without being promoted, so "no active service" is not the same as "nothing owned" --
    and the disposition must not depend on which of the two the service happens to report.
    """

    harness = _build(
        workflow=_Workflow(phase=PaperWorkflowPhase.CONNECTING, result=None),
        trading=_Trading(owned=owned, connected=False),
    )

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.OWNERSHIP_BLOCKED
    assert harness.trading.calls == []


def test_shutdown_reports_the_halt_an_orderly_stop_produced() -> None:
    """A stop that halts is not "waiting for finalization".

    The same ``request_stop`` call can land the session in ``HALTED`` -- a stale BUY that
    cannot be cancelled, an aged protective SELL, unsafe health -- and after that there is
    no automatic route left.  Telling the operator to wait would leave them waiting for a
    proof nothing will run.
    """

    harness = _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.RUNNING,
            result=_Result(active=True),
            stop_outcome="halt",
        )
    )

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.MANUAL_RECOVERY_REQUIRED
    assert verdict.message == SHUTDOWN_MANUAL_RECOVERY_MESSAGE
    assert harness.workflow.stop_requests == 1
    assert harness.workflow.phase is PaperWorkflowPhase.HALTED


def test_shutdown_of_a_fast_finalizing_stop_is_ready() -> None:
    """A clean stop can finalize in the same call, and then the close may proceed.

    The stop publishes its finalized result, the ordinary result path releases the
    ownership, and the re-classification finds nothing left to settle -- so the verdict is
    ``READY`` rather than a "wait" for a proof that already ran.
    """

    harness = _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.RUNNING,
            result=_Result(active=True),
            stop_outcome="finalized",
        )
    )

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.READY
    assert harness.workflow.phase is PaperWorkflowPhase.FINALIZED
    assert harness.trading.owned is False
    assert harness.workflow.lease_released is True
    assert harness.events.finalized == 1


def test_shutdown_blocks_when_the_orderly_stop_was_refused() -> None:
    """A stop that could not even be requested leaves a live session: nothing is released."""

    harness = _build(
        workflow=_Workflow(
            phase=PaperWorkflowPhase.RUNNING,
            result=_Result(active=True),
            stop_outcome="refused",
        )
    )

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.OWNERSHIP_BLOCKED
    assert SHUTDOWN_STOP_REFUSED_REASON in verdict.message
    assert harness.workflow.phase is PaperWorkflowPhase.RUNNING
    assert harness.trading.calls == []
    assert harness.trading.owned is True


def test_shutdown_blocks_an_owned_slot_with_no_session_at_all() -> None:
    """An owned slot and no result: nothing can be proved, so nothing is released.

    ``CONNECTING`` is refused before this branch is reached, so getting here means a slot
    came to exist with no session behind it -- a launch fault.  Defence in depth, and the
    assertion is the same one every other blocked outcome makes: no ``READY`` while
    something is still held, and nothing released on the way to saying so.
    """

    harness = _build(
        workflow=_Workflow(phase=PaperWorkflowPhase.READY, result=None),
        trading=_Trading(owned=True, connected=False),
    )

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.OWNERSHIP_BLOCKED
    assert SHUTDOWN_UNPROVABLE_SESSION_REASON in verdict.message
    assert harness.trading.calls == []
    assert harness.workflow.calls == []


def test_shutdown_blocks_when_the_broker_still_reports_positions() -> None:
    harness = _finalized(trading=_Trading(broker_positions=("AAPL",)))

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.OWNERSHIP_BLOCKED
    assert harness.workflow.lease_released is False
    assert harness.trading.owned is True


def test_shutdown_blocks_when_the_journal_still_has_unreconciled_rows() -> None:
    harness = _finalized(rows=(_Row(reconciled=False),))

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.OWNERSHIP_BLOCKED
    assert harness.trading.owned is True


def test_shutdown_is_idempotent_after_a_release() -> None:
    harness = _finalized()

    first = harness.orchestrator.prepare_shutdown()
    second = harness.orchestrator.prepare_shutdown()

    assert first.disposition is PaperShutdownDisposition.READY
    assert second.disposition is PaperShutdownDisposition.READY
    assert harness.trading.calls.count("commit_active_release") == 1
    # The second call found nothing to release, and said READY without asking anything.
    assert harness.workflow.calls == ["finalize_if_safe"]


def test_shutdown_after_a_failed_proof_requires_the_operator() -> None:
    """The automatic failure route, seen from the close's side."""

    harness = _stopping(finalization_outcome="changed")
    harness.orchestrator._publish_result(_Result(active=False))
    harness.submitter.run_all()
    assert harness.workflow.phase is PaperWorkflowPhase.HALTED

    verdict = harness.orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.MANUAL_RECOVERY_REQUIRED
