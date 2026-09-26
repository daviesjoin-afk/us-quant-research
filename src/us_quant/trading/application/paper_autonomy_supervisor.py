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
    PaperAutonomyActionRepositoryPort,
)
from us_quant.trading.ports.paper_autonomy_supervisor import (
    PaperAutonomyExecutorPort,
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
        intent,
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

        intent_revision, mode, latched, readable = self._read_intent()
        runtime = self._runtime_facts.facts()
        schedule = self._schedule.schedule(now=moment)
        unresolved = self._first_unresolved()
        attempted = self._start_attempted(schedule.trading_day)

        decision = decide_paper_autonomy(
            intent_mode=mode,
            kill_switch_latched=latched,
            intent_revision=intent_revision,
            runtime=runtime,
            schedule=schedule,
            startup=self._startup,
            unresolved_action=unresolved,
            start_already_attempted_today=attempted,
            control_plane_readable=readable,
        )

        if not decision.is_executable:
            self._report(
                decision,
                code=(
                    PaperAutonomyEventCode.RECOVERY_REQUIRED
                    if decision.action
                    is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
                    and (unresolved is not None or runtime.manual_recovery_required)
                    else PaperAutonomyEventCode.TICK_BLOCKED
                ),
            )
            return PaperAutonomyTickResult(
                decision=decision, claimed=False, status=None, outcome=None
            )

        return self._execute(decision, moment=moment)

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
        except Exception as error:  # noqa: BLE001 - the owner may raise anything
            self._actions.complete(
                action_key=decision.action_key,
                status=PaperAutonomyActionStatus.FAILED,
                completed_at=moment,
                detail=f"the owner raised while the request was made: {error}",
            )
            self._report(decision, code=PaperAutonomyEventCode.ACTION_FAILED)
            return PaperAutonomyTickResult(
                decision=decision,
                claimed=True,
                status=PaperAutonomyActionStatus.FAILED,
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
        mode are never read -- ``control_plane_readable=False`` returns before
        them -- and ``DISABLED`` is the placeholder because it is the answer that
        fails closed even if that coupling were ever broken.
        """

        try:
            intent = self._intent.snapshot()
        except PaperAutonomyError:
            return (0, PaperAutonomyMode.DISABLED, False, False)
        if intent is None:
            raise PaperAutonomySupervisorError(
                "the intent application returned no snapshot"
            )
        return (
            intent.revision,
            intent.mode,
            intent.kill_switch_latched,
            True,
        )

    def _first_unresolved(self) -> str | None:
        unresolved = self._actions.unresolved()
        return unresolved[0].action_key if unresolved else None

    def _start_attempted(self, trading_day) -> bool:
        return self._actions.start_attempted(trading_day)

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
