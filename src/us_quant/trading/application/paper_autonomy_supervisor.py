"""The Paper autonomy supervisor: the tick, as a sequence of steps.

This is the Qt-free core that turns the vocabulary into behaviour.  It holds no
timer, no widget and no broker; the host decides *when* to call
:meth:`PaperAutonomySupervisor.tick`, and everything about *what* a tick does is
here so it can be tested without one.

A tick is a fixed sequence, and the sequence is the design:

    read the operator intent          -- fresh, every tick
    read the runtime facts            -- fresh, every tick
    read the schedule verdict         -- fresh, every tick
    read the action state             -- fresh, every tick
    decide                            -- pure (see ``decide_paper_autonomy``)
    claim the action                  -- atomically, before asking anybody
    ask the owner                     -- one narrow request
    record how far the request got    -- CLAIMED -> REQUESTED, or terminal
    emit one event                    -- for the audit surface

Nothing is cached between ticks.  A scheduler that reused a previous tick's facts
would be deciding on the strength of a session that may since have stopped, and
it is the tick *after* a session ends that matters most.

Two things are deliberately not here:

* **shutdown admission.**  ``RuntimeSupervisor.shutting_down`` stays the single
  admission truth, projected into the runtime facts.  A second flag on this
  object would be a second place to forget.
* **completion.**  Asking an owner to start a session is not starting one, so a
  tick ends at ``REQUESTED`` and the canonical publication moves it further.  A
  success is never recorded on the strength of a call returning.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from us_quant.trading.domain.paper_autonomy import (
    PaperAutonomyError,
    PaperAutonomyMode,
)
from us_quant.trading.domain.paper_autonomy_supervisor import (
    EVENT_SEVERITIES,
    REQUESTED_EVENT_CODES,
    PaperAutonomyAction,
    PaperAutonomyActionStatus,
    PaperAutonomyDecision,
    PaperAutonomyEventCode,
    PaperAutonomyPolicy,
    PaperAutonomyRuntimeFacts,
    PaperAutonomyStartupFacts,
    PaperAutonomySupervisorError,
    PaperAutonomySupervisorEvent,
    PaperAutonomySupervisorViolation,
    decide_paper_autonomy,
)
from us_quant.trading.ports.paper_autonomy_action_repository import (
    PaperAutonomyActionRecord,
    PaperAutonomyActionRepositoryError,
    PaperAutonomyActionRepositoryPort,
)
from us_quant.trading.ports.paper_autonomy_supervisor import (
    PaperAutonomyExecutorPort,
    PaperAutonomyIntentReaderPort,
    PaperAutonomyPreparationRequest,
    PaperAutonomyRequestOutcome,
    PaperAutonomyRuntimeFactsPort,
    PaperAutonomySchedulePort,
    PaperAutonomyStartupFactsPort,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class PaperAutonomyTickResult:
    """What one tick did, in the terms the caller can actually verify.

    ``status`` is the ledger's view and it is deliberately not named after the
    action: a tick that produced ``REQUESTED`` has *not* started a session, and a
    field called ``started`` would be read as though it had.  ``claimed`` is
    separate so a caller can tell "this tick asked" from "an earlier tick, or an
    earlier process, already did".
    """

    decision: PaperAutonomyDecision
    claimed: bool
    status: PaperAutonomyActionStatus | None
    outcome: PaperAutonomyRequestOutcome | None

    @property
    def executable(self) -> bool:
        """Whether the decision asked an owner to do something."""

        return self.decision.is_executable


class PaperAutonomySupervisor:
    """The scheduler core: read, decide, claim, ask, record, report."""

    def __init__(
        self,
        *,
        intent: PaperAutonomyIntentReaderPort,
        actions: PaperAutonomyActionRepositoryPort,
        runtime_facts: PaperAutonomyRuntimeFactsPort,
        startup_facts: PaperAutonomyStartupFactsPort,
        schedule: PaperAutonomySchedulePort,
        executor: PaperAutonomyExecutorPort,
        policy: PaperAutonomyPolicy,
        clock: Callable[[], datetime] | None = None,
        emit: Callable[[PaperAutonomySupervisorEvent], None] | None = None,
    ) -> None:
        self._intent = intent
        self._actions = actions
        self._runtime_facts = runtime_facts
        self._startup_facts = startup_facts
        self._schedule = schedule
        self._executor = executor
        self._policy = policy
        self._clock = clock or _utc_now
        self._emit = emit

        # The startup classification describes the *process's* beginning, which
        # is asked once.  This is the one fact that is not re-read per tick, and
        # it is not a cache: re-reading it later would answer a different
        # question -- "is the world safe now" -- which is what the runtime and
        # schedule facts are for.
        self._startup: PaperAutonomyStartupFacts = startup_facts.startup_facts()

    def tick(self, *, now: datetime | None = None) -> PaperAutonomyTickResult:
        """One full sequence.  Never sleeps, never blocks, never retries."""

        moment = now if now is not None else self._clock()

        readable, intent_revision, mode, latched = self._read_intent()
        if not readable:
            # Short-circuit, and this is an *operational* precedence rather than
            # a documented one: nothing else is read, nothing else is touched.
            # Continuing would ask the runtime, the schedule and the ledger about
            # a system whose authorisation cannot be read -- and the ledger
            # failing too would then surface as an exception instead of as the
            # one reason that outranks every other.  The trading day is
            # deliberately left unknown rather than fetched for the sake of
            # filling a field.
            return self._blocked_without_reading(
                reason="the Paper autonomy control-plane store could not be read",
                intent_revision=intent_revision,
            )

        runtime = self._runtime_facts.facts()
        schedule = self._schedule.schedule(now=moment)
        action_store_readable, unresolved, attempted = self._read_action_state(
            schedule.trading_day
        )

        decision = decide_paper_autonomy(
            intent_mode=mode,
            kill_switch_latched=latched,
            intent_revision=intent_revision,
            runtime=runtime,
            schedule=schedule,
            startup=self._startup,
            unresolved_action=unresolved,
            start_already_attempted_today=attempted,
            control_plane_readable=True,
            action_store_readable=action_store_readable,
        )

        if decision.action is PaperAutonomyAction.NOOP:
            # A quiet tick says nothing.  A scheduler reporting every healthy
            # no-op would emit thousands of entries a day and teach its operator
            # to ignore the log -- which is the same as having no log.
            return PaperAutonomyTickResult(
                decision=decision, claimed=False, status=None, outcome=None
            )

        if decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR:
            self._report(
                decision,
                code=(
                    PaperAutonomyEventCode.RECOVERY_REQUIRED
                    if unresolved is not None or runtime.manual_recovery_required
                    else PaperAutonomyEventCode.TICK_BLOCKED
                ),
            )
            return PaperAutonomyTickResult(
                decision=decision, claimed=False, status=None, outcome=None
            )

        return self._execute(decision, moment=moment)

    def _blocked_without_reading(
        self, *, reason: str, intent_revision: int
    ) -> PaperAutonomyTickResult:
        """The one block that is produced without consulting anything else."""

        decision = PaperAutonomyDecision(
            action=PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR,
            reason=reason,
            trading_day=None,
            intent_revision=intent_revision,
            action_key=None,
        )
        self._report(decision, code=PaperAutonomyEventCode.TICK_BLOCKED)
        return PaperAutonomyTickResult(
            decision=decision, claimed=False, status=None, outcome=None
        )

    # -- the claim, the ask, the record ---------------------------------

    def _execute(
        self, decision: PaperAutonomyDecision, *, moment: datetime
    ) -> PaperAutonomyTickResult:
        assert decision.action_key is not None  # guaranteed by is_executable
        assert decision.trading_day is not None  # ditto
        action_type = decision.action_type
        assert action_type is not None  # ditto

        claimed = self._actions.claim(
            PaperAutonomyActionRecord(
                action_key=decision.action_key,
                intent_revision=decision.intent_revision,
                trading_day=decision.trading_day,
                action=action_type,
                status=PaperAutonomyActionStatus.CLAIMED,
                claimed_at=moment,
                completed_at=None,
                detail=decision.reason,
            )
        )
        if not claimed:
            # An earlier tick, or an earlier process, already asked.  Not a
            # failure and not a retry: the answer is on the ledger.
            return PaperAutonomyTickResult(
                decision=decision, claimed=False, status=None, outcome=None
            )

        try:
            outcome = self._invoke(decision.action)
        except Exception:  # noqa: BLE001 - the owner may raise anything
            # The claim is left exactly as it is -- ``CLAIMED``, unresolved --
            # and that is the whole point.  A call that raised proves nothing
            # about whether the effect happened: a launch can have changed the
            # workflow, emitted a signal and started connecting a broker before
            # something later in the same call failed.  Recording a terminal
            # ``FAILED`` would turn "the outcome is unknown" into "it is known
            # not to have happened", which is the one direction an unattended
            # system must never take -- and the next tick then sees an async
            # outcome where there is an unknown one.
            #
            # The exception's own text is deliberately not persisted.  It can
            # carry a broker message, an account alias or a path, none of which
            # belong in a durable autonomy audit; a caller that needs it has the
            # traceback it caught.
            self._report(
                decision,
                code=PaperAutonomyEventCode.ACTION_OUTCOME_UNKNOWN,
                detail=(
                    "the owner request raised before its outcome could be "
                    "established; the action remains unresolved and no retry is "
                    "attempted"
                ),
            )
            return PaperAutonomyTickResult(
                decision=decision,
                claimed=True,
                status=PaperAutonomyActionStatus.CLAIMED,
                outcome=None,
            )

        if outcome.accepted:
            self._actions.mark_requested(
                action_key=decision.action_key, detail=outcome.detail
            )
            self._report(decision, code=REQUESTED_EVENT_CODES[decision.action])
            return PaperAutonomyTickResult(
                decision=decision,
                claimed=True,
                status=PaperAutonomyActionStatus.REQUESTED,
                outcome=outcome,
            )

        self._actions.complete(
            action_key=decision.action_key,
            status=PaperAutonomyActionStatus.REFUSED,
            completed_at=moment,
            detail=outcome.detail,
        )
        self._report(
            decision,
            code=PaperAutonomyEventCode.ACTION_REFUSED,
            detail=outcome.detail,
        )
        return PaperAutonomyTickResult(
            decision=decision,
            claimed=True,
            status=PaperAutonomyActionStatus.REFUSED,
            outcome=outcome,
        )

    def _invoke(self, action: PaperAutonomyAction) -> PaperAutonomyRequestOutcome:
        """The one place an action becomes a request to an owner.

        Every one of these is a question.  None of them connects a broker,
        submits, cancels, reconciles, releases a lease or writes a phase -- the
        owner is free to refuse, and the refusal is recorded as a refusal.
        """

        if action is PaperAutonomyAction.PREPARE:
            return self._executor.request_prepare(
                PaperAutonomyPreparationRequest(
                    candidate_limit=self._policy.candidate_limit,
                    capital_limit=self._policy.requested_capital_limit,
                )
            )
        if action is PaperAutonomyAction.START:
            return self._executor.request_start()
        if action is PaperAutonomyAction.PAUSE_ENTRIES:
            return self._executor.request_pause()
        if action is PaperAutonomyAction.RESUME_ENTRIES:
            return self._executor.request_resume()
        if action is PaperAutonomyAction.STOP:
            return self._executor.request_stop()
        raise PaperAutonomySupervisorViolation(
            f"no owner request exists for {action.value}"
        )

    # -- reads ----------------------------------------------------------

    def _read_intent(self):
        """The operator's intent, or the fact that it could not be read.

        An unreadable control plane is not an exception here: the decision's
        first clause is exactly that state, and turning it into a return value
        keeps the precedence order in one place instead of splitting it between
        the supervisor and the decision function.  The placeholder revision and
        mode are never read -- ``readable=False`` returns before them -- and
        ``DISABLED`` is the placeholder because it is the answer that fails
        closed even if that coupling were ever broken.
        """

        try:
            intent = self._intent.snapshot()
        except PaperAutonomyError:
            return (False, 0, PaperAutonomyMode.DISABLED, False)
        if intent is None:
            # A port that answers ``None`` is a port that cannot be read; there is
            # no such thing as an absent intent, so this is a failure and not a
            # default.
            return (False, 0, PaperAutonomyMode.DISABLED, False)
        return (True, intent.revision, intent.mode, intent.kill_switch_latched)

    def _read_action_state(self, trading_day):
        """The ledger's view, or the fact that it could not be read.

        The one place the ledger's failures are turned into a fact.  Letting
        ``PaperAutonomyActionRepositoryError`` escape would make the fail-closed
        answer depend on a host that has not been written yet, and the question
        the ledger answers -- "has this already been attempted" -- has no safe
        default: an unreadable ledger is not an empty one.
        """

        try:
            unresolved = self._actions.unresolved()
            attempted = self._actions.start_attempted(trading_day)
        except PaperAutonomyActionRepositoryError:
            return (False, None, False)
        return (
            True,
            unresolved[0].action_key if unresolved else None,
            attempted,
        )

    # -- reporting ------------------------------------------------------

    def _report(
        self,
        decision: PaperAutonomyDecision,
        *,
        code: PaperAutonomyEventCode,
        detail: str | None = None,
    ) -> None:
        """Hand one event to the sink, if composition gave us one.

        The sink is a plain callable rather than a port: the core does not import
        the desktop's event orchestrator, and a caller with nowhere to put the
        event is a caller that does not want one.
        """

        if self._emit is None:
            return
        self._emit(
            PaperAutonomySupervisorEvent(
                code=code,
                severity=EVENT_SEVERITIES[code],
                detail=detail or decision.reason,
                trading_day=decision.trading_day,
                intent_revision=decision.intent_revision,
                action_key=decision.action_key,
            )
        )


__all__ = ["PaperAutonomySupervisor", "PaperAutonomyTickResult"]
