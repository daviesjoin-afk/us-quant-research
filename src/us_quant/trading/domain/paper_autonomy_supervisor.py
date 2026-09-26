"""The Paper autonomy supervisor's vocabulary: policy, facts, decisions, actions.

v1-A owns *what the operator asked for* and nothing else.  This module owns the
next layer up, and it is deliberately a vocabulary rather than a component: the
types here are what a supervisor reads, decides with, and records, with no
behaviour attached to any of the machinery it drives.

Four separations are load-bearing, and each one exists because collapsing it
would create a second truth.

* **Intent vs policy.**  :class:`PaperAutonomyIntent` (v1-A) is the operator's
  authorisation.  :class:`PaperAutonomyPolicy` is deployment configuration --
  when preparation may begin, when a start is still allowed, when the session
  must be wound down.  They are different truths with different owners, so the
  policy is *not* a field on the intent: an operator turning autonomy on must not
  silently be choosing the trading times, and a redeployment must not need the
  operator's signature.
* **Facts vs truth.**  The runtime, schedule and startup facts here are
  per-tick *projections* of facts other components own.  A supervisor holds them
  for the duration of one tick and never caches them: the honest source of "is a
  Paper session running" is the Paper capability, and a copy of it in a scheduler
  is exactly the stale value an unattended process would act on.
* **Decision vs execution.**  :func:`decide_paper_autonomy` is pure: intent,
  facts, action state and policy in, one :class:`PaperAutonomyDecision` out.  It
  performs no I/O so the precedence order can be tested exhaustively and mutated,
  which matters because a scheduler's mistakes are made in its precedence.
* **Request vs completion.**  A supervisor can *ask* an owner to prepare or to
  start; it cannot know that either happened.  So the action record distinguishes
  a claimed request from an observed outcome, and nothing in this vocabulary
  lets a caller record "started" on the strength of a call returning.

There is no environment field, no live mode and no broker anywhere in this
vocabulary.  Autonomous Paper is the only thing it can describe, on purpose:
widening that would need its own review, not another enum member.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from us_quant.trading.domain.paper_autonomy import (
    INITIAL_REVISION,
    PaperAutonomyError,
    PaperAutonomyMode,
    PaperAutonomyViolation,
)


class PaperAutonomySupervisorError(PaperAutonomyError):
    """Base class for every failure of the autonomy supervisor layer."""


class PaperAutonomySupervisorViolation(PaperAutonomySupervisorError):
    """A value or a stored record would break an invariant of this vocabulary."""


class PaperAutonomySessionWindow(StrEnum):
    """The canonical US equity session, in the autonomy vocabulary.

    A *label*, not a classification.  Which window a moment falls in is decided
    by the canonical session provider (``us_quant.extended_hours``) and mapped
    here by the adapter; this module never compares a clock to a boundary, so
    there is exactly one holiday calendar and one set of session times in the
    system.  The members exist so a decision can be recorded and reviewed in the
    same words an operator uses, and so "which windows may start" is a statement
    this vocabulary can make.

    The members mirror ``USEquitySession`` one for one, including the two an
    autonomous scheduler can do nothing with.  Folding ``MAINTENANCE`` into
    ``CLOSED`` would be this vocabulary making a session judgement it is not
    entitled to make, and the day the canonical provider distinguishes them
    again the fold would be silently wrong.
    """

    CLOSED = "closed"
    OVERNIGHT = "overnight"
    MAINTENANCE = "maintenance"
    PREMARKET = "premarket"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"


#: The windows autonomous v1 is allowed to *start* in.
#:
#: Regular hours only, and the narrowness is the point: an unattended start
#: should be permitted strictly less than a human one, not more.  Manual Paper
#: keeps premarket / after-hours / overnight; autonomous start does not, and
#: there is deliberately no configuration flag that widens this -- a future
#: autonomous extended-hours decision needs its own review rather than a switch.
AUTONOMOUS_START_WINDOWS = (PaperAutonomySessionWindow.REGULAR,)

#: The windows autonomous v1 is allowed to *prepare* in.
#:
#: A superset of the start windows: getting a candidate shortlist ready before
#: the open is useful and harmless, since nothing can be launched from it until
#: the start window opens.
AUTONOMOUS_PREPARE_WINDOWS = (
    PaperAutonomySessionWindow.PREMARKET,
    PaperAutonomySessionWindow.REGULAR,
)


class PaperAutonomyAction(StrEnum):
    """What a supervisor has decided to do about the current state.

    Two members are not actions at all, and they are members anyway because
    "nothing should happen" and "a human has to look at this" are decisions the
    audit trail has to record.  A scheduler that only logged its actions would
    leave an operator unable to tell a quiet system from a stuck one.

    Deliberately absent: anything that *repairs* Paper state.  ``AUTO_RECONCILE``,
    ``CONFIRM_RECONCILIATION``, ``FORCE_FLAT``, ``FORCE_RESUME`` and
    ``FORCE_CANCEL_ALL`` are not actions this system may take at all, so there is
    no member for them to be decided as -- an unattended process holding a
    reconciliation authority is the failure mode the manual gate exists for.
    """

    NOOP = "noop"
    PREPARE = "prepare"
    START = "start"
    PAUSE_ENTRIES = "pause_entries"
    RESUME_ENTRIES = "resume_entries"
    STOP = "stop"
    BLOCKED_REQUIRES_OPERATOR = "blocked_requires_operator"


class PaperAutonomyActionType(StrEnum):
    """The executable subset of :class:`PaperAutonomyAction`.

    A separate enum because the action ledger records *requests*, and no request
    exists for the two non-actions.  Keeping them apart means the idempotency
    key can never be built for something that is not an action.
    """

    PREPARE = "prepare"
    START = "start"
    PAUSE_ENTRIES = "pause_entries"
    RESUME_ENTRIES = "resume_entries"
    STOP = "stop"


class PaperAutonomyActionStatus(StrEnum):
    """How far one requested action got.

    ``CLAIMED`` and ``REQUESTED`` are the two non-terminal states and they are
    distinct on purpose.  ``CLAIMED`` means this supervisor took the right to
    perform the action and had not yet asked anyone; ``REQUESTED`` means the
    owner accepted the request and the outcome is still unknown.  Both stay
    non-terminal until the canonical owner publishes a finished fact, because a
    request that returned is not an action that happened.

    Crash recovery treats *any* non-terminal record as unknown.  Nothing in this
    module converts one to a terminal state on a guess -- a ``CLAIMED`` start
    that was interrupted may or may not have connected a broker, and the only
    safe reading is "a human has to look".
    """

    CLAIMED = "claimed"
    REQUESTED = "requested"
    SUCCEEDED = "succeeded"
    REFUSED = "refused"
    FAILED = "failed"


#: The states an action can still move out of.
NON_TERMINAL_ACTION_STATUSES = (
    PaperAutonomyActionStatus.CLAIMED,
    PaperAutonomyActionStatus.REQUESTED,
)

#: The states an action record may be written in exactly once.
TERMINAL_ACTION_STATUSES = (
    PaperAutonomyActionStatus.SUCCEEDED,
    PaperAutonomyActionStatus.REFUSED,
    PaperAutonomyActionStatus.FAILED,
)


@dataclass(frozen=True, slots=True)
class PaperAutonomyPolicy:
    """When autonomous Paper may act, and within what bounds.

    Configuration rather than authorisation, and the distinction is what keeps
    the two honest: an operator enabling autonomy is not thereby choosing the
    session times, and a deployment changing them is not thereby enabling
    autonomy.

    Every time in this type is an Eastern-time wall clock because that is how the
    equity session is defined; the *comparison* against it happens in the
    schedule adapter, next to the canonical session provider, not here.
    """

    prepare_not_before_et: time
    start_not_before_et: time
    latest_start_et: time
    orderly_stop_at_et: time
    candidate_limit: int
    requested_capital_limit: Decimal
    tick_interval_seconds: int

    def __post_init__(self) -> None:
        # A policy that cannot describe a legal day is refused at construction
        # rather than at the moment it would have mattered.  Silence here would
        # surface as a supervisor that never starts and a log with no reason.
        if not (
            self.prepare_not_before_et
            <= self.start_not_before_et
            <= self.latest_start_et
            <= self.orderly_stop_at_et
        ):
            raise PaperAutonomySupervisorViolation(
                "the autonomy policy's times must be ordered: preparation, "
                "start, latest start, orderly stop"
            )
        if self.candidate_limit <= 0:
            raise PaperAutonomySupervisorViolation(
                f"a candidate limit must be positive, not {self.candidate_limit}"
            )
        if self.requested_capital_limit < 0:
            raise PaperAutonomySupervisorViolation(
                "a requested capital limit cannot be negative"
            )
        if self.tick_interval_seconds <= 0:
            raise PaperAutonomySupervisorViolation(
                "a tick interval must be positive; a zero interval is a busy "
                "loop"
            )

    @property
    def bounded_tick_interval_seconds(self) -> int:
        """The tick interval, held inside the range a desktop timer can bear.

        Clamped rather than rejected so a hand-edited config cannot produce a
        busy loop, and clamped *here* rather than in the host so the bound is a
        property of the policy rather than of whichever timer reads it.
        """

        return min(
            max(self.tick_interval_seconds, MINIMUM_TICK_INTERVAL_SECONDS),
            MAXIMUM_TICK_INTERVAL_SECONDS,
        )


#: A timer below this is a busy loop, and above it the scheduler is useless.
MINIMUM_TICK_INTERVAL_SECONDS = 1
MAXIMUM_TICK_INTERVAL_SECONDS = 3600


class PaperAutonomySessionProvenance(StrEnum):
    """Who started the Paper session that is currently up.

    The fact the whole control boundary rests on.  The operator's autonomy
    authorisation says a supervisor may drive the sessions *it* started; it says
    nothing about a session somebody launched from the Execution route by hand,
    and a supervisor that paused or stopped one of those would be changing the
    semantics of a feature the operator never put under automation.

    An ephemeral per-tick fact, never persisted: a restart loses it, and that
    loss is the point -- after a restart the supervisor has no claim on whatever
    session it finds, which is exactly what ``UNKNOWN`` says.  It is deliberately
    not a field on the intent (the operator's authorisation is not a session
    fact) and not a field on the action ledger (the ledger records requests).

    ``UNKNOWN`` is not a synonym for ``MANUAL``: a manual session is one the
    supervisor knows it must leave alone, while an unknown one is a session
    nobody can currently account for.  The first is a no-op, the second is a
    reason to stop and ask a human.
    """

    NONE = "none"
    AUTONOMOUS = "autonomous"
    MANUAL = "manual"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PaperAutonomyRuntimeFacts:
    """What the Paper capability is doing, projected for one tick.

    Booleans only, and that is the design rather than a simplification.  The
    phase vocabulary belongs to the Paper capability; a supervisor that received
    it would be a supervisor that could branch on it, and the whole point of the
    facts port is that the sequencing decisions have already been made by the
    owner.  Whatever the phase is, this is what it *means* to a scheduler.

    Nothing here is retained.  Each tick reads these again, so a value cannot
    outlive the observation that produced it.

    The invariants below are enforced at construction so that a self-contradictory
    observation can never reach the decision function.  A fact set that says "a
    session is running" and "no session was ever started" at once is not a
    situation for the precedence order to resolve -- it is a caller that has lost
    track, and the decision function must not be the place that finds out.
    """

    shutting_down: bool
    preparation_active: bool
    preparation_ready: bool
    launch_in_flight: bool
    session_running: bool
    session_paused: bool
    session_provenance: PaperAutonomySessionProvenance
    manual_recovery_required: bool
    finalization_pending: bool
    paper_ownership_consistent: bool

    def __post_init__(self) -> None:
        if self.session_running and self.session_paused:
            raise PaperAutonomySupervisorViolation(
                "a Paper session cannot be running and paused at once"
            )
        if self.session_active and (
            self.session_provenance is PaperAutonomySessionProvenance.NONE
        ):
            raise PaperAutonomySupervisorViolation(
                "an active Paper session must have a provenance; 'none' means "
                "no session is up"
            )
        if not self.session_active and (
            self.session_provenance is not PaperAutonomySessionProvenance.NONE
        ):
            raise PaperAutonomySupervisorViolation(
                f"no Paper session is active, so its provenance cannot be "
                f"'{self.session_provenance.value}'"
            )

    @property
    def session_active(self) -> bool:
        """Whether a Paper session is up in either of its two live shapes."""

        return self.session_running or self.session_paused

    @property
    def autonomous_session_active(self) -> bool:
        """Whether the live session is one this supervisor started.

        The only condition under which it may pause, resume or stop a session.
        """

        return (
            self.session_active
            and self.session_provenance
            is PaperAutonomySessionProvenance.AUTONOMOUS
        )

    @property
    def idle(self) -> bool:
        """Whether nothing Paper-side is in flight at all."""

        return not (
            self.preparation_active
            or self.preparation_ready
            or self.launch_in_flight
            or self.session_active
            or self.finalization_pending
        )


@dataclass(frozen=True, slots=True)
class PaperAutonomyScheduleFacts:
    """Where the trading day is, and what that permits, for one tick.

    Derived from the canonical session provider by the adapter; the supervisor
    reads a verdict and never a clock.  ``exceptional_schedule_uncertain`` exists
    so that "the calendar could not be read" is a state the supervisor can be
    *told* rather than one it has to guess: an unknown calendar is not the same
    as a closed market, and both are not the same as an open one.
    """

    trading_day: date
    session: PaperAutonomySessionWindow
    preparation_allowed: bool
    start_allowed: bool
    orderly_stop_due: bool
    exceptional_schedule_uncertain: bool

    def __post_init__(self) -> None:
        # The window limits are enforced on the *value*, not left to whoever
        # builds it.  While ``start_allowed`` was the only thing the decision
        # read, "regular only" was a promise the adapter made; a fact carrying
        # ``session=after_hours, start_allowed=True`` would have launched a
        # session outside the one window v1 permits, and nothing would have
        # objected.  A schedule that cannot be expressed cannot be acted on.
        if self.start_allowed and (
            self.session not in AUTONOMOUS_START_WINDOWS
        ):
            raise PaperAutonomySupervisorViolation(
                f"autonomous starts are not permitted in the "
                f"'{self.session.value}' window"
            )
        if self.preparation_allowed and (
            self.session not in AUTONOMOUS_PREPARE_WINDOWS
        ):
            raise PaperAutonomySupervisorViolation(
                f"autonomous preparation is not permitted in the "
                f"'{self.session.value}' window"
            )
        # A mandatory wind-down and a permission to add work are opposites.  The
        # decision function must not be asked which of two contradictory facts
        # to believe -- and it would believe whichever branch came first.
        if self.orderly_stop_due and (
            self.start_allowed or self.preparation_allowed
        ):
            raise PaperAutonomySupervisorViolation(
                "the orderly stop boundary has arrived, so the same schedule "
                "cannot also permit starting or preparing"
            )


@dataclass(frozen=True, slots=True)
class PaperAutonomyStartupFacts:
    """What the broker and account said when this process last asked.

    The point of this type is the ``None``.  A startup classification has to
    distinguish "no open orders" from "I could not find out", and an ``int`` that
    defaulted to zero would erase exactly that difference -- the difference
    between a system that is safe to start and one that only looks safe.

    v1-B never repairs what these facts describe.  An unknown account, a
    position, an open order or an unreconciled row is a human's problem, not a
    scheduler's opportunity.
    """

    intent_store_readable: bool
    action_store_readable: bool
    broker_state_known: bool
    account_identity_known: bool
    open_broker_orders: int | None
    broker_positions: int | None
    unreconciled_rows: int | None
    paper_ownership_clear: bool
    manual_recovery_required: bool

    @property
    def proven_safe(self) -> bool:
        """Whether startup may proceed without a human.

        Every clause is a proof obligation, so an unknown quantity fails here as
        surely as a non-zero one.
        """

        return (
            self.intent_store_readable
            and self.action_store_readable
            and self.broker_state_known
            and self.account_identity_known
            and self.open_broker_orders == 0
            and self.broker_positions == 0
            and self.unreconciled_rows == 0
            and self.paper_ownership_clear
            and not self.manual_recovery_required
        )


@dataclass(frozen=True, slots=True)
class PaperAutonomyDecision:
    """One tick's verdict: what to do, why, and under which identity.

    ``reason`` is not decoration.  A blocked scheduler whose reason cannot be
    read is indistinguishable from a broken one, and the reasons below are
    written to name the component the operator has to go and look at.

    ``action_key`` is present exactly when the decision is executable, and it is
    deterministic rather than random: idempotency is the property being bought,
    and a freshly generated identifier proves only that two requests differ.
    """

    action: PaperAutonomyAction
    reason: str
    trading_day: date | None
    intent_revision: int
    action_key: str | None

    def __post_init__(self) -> None:
        if self.action is PaperAutonomyAction.NOOP:
            return
        if self.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR:
            return
        if self.action_key is None:
            raise PaperAutonomySupervisorViolation(
                f"an executable decision needs a deterministic action key: "
                f"{self.action.value}"
            )
        if self.trading_day is None:
            raise PaperAutonomySupervisorViolation(
                f"an executable decision needs the trading day it belongs to: "
                f"{self.action.value}"
            )

    @property
    def is_executable(self) -> bool:
        """Whether this decision asks an owner to do something."""

        return self.action not in (
            PaperAutonomyAction.NOOP,
            PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR,
        )

    @property
    def action_type(self) -> PaperAutonomyActionType | None:
        """This decision as a ledger action, or ``None`` if it is not one."""

        if not self.is_executable:
            return None
        return PaperAutonomyActionType(self.action.value)


#: The windows in which an action key is allowed to repeat.
#:
#: The slot is part of the key so that a retry *after* a policy backoff is a
#: different key from the attempt it follows, while two ticks in the same slot
#: collide and the second one finds the action already claimed.
class PaperAutonomyActionSlot(StrEnum):
    """A named policy slot, so an action key is never built from a raw clock."""

    SESSION = "session"
    OPERATOR = "operator"
    RETRY = "retry"


def action_key_for(
    *,
    trading_day: date,
    intent_revision: int,
    action: PaperAutonomyActionType,
    slot: PaperAutonomyActionSlot,
    attempt: int = 0,
) -> str:
    """The deterministic identity of one requested action.

    Built from what the action *is* rather than from when it was decided, so the
    same request made by two ticks -- or by a tick that ran before a crash and a
    tick that ran after it -- carries the same key and can be claimed only once.
    That is the whole mechanism: no random component, because a random component
    would guarantee the opposite of what this is for.  ``attempt`` is the one
    counter, and it only moves under an explicit policy backoff.
    """

    if intent_revision < INITIAL_REVISION:
        raise PaperAutonomySupervisorViolation(
            f"an action key cannot be built from revision {intent_revision}"
        )
    if attempt < 0:
        raise PaperAutonomySupervisorViolation(
            "an action key cannot be built from a negative attempt"
        )
    base = (
        f"paper:{trading_day.isoformat()}:r{intent_revision}:"
        f"{action.value}:{slot.value}"
    )
    return base if attempt == 0 else f"{base}#{attempt}"


def decide_paper_autonomy(
    *,
    intent_mode: PaperAutonomyMode,
    kill_switch_latched: bool,
    intent_revision: int,
    runtime: PaperAutonomyRuntimeFacts,
    schedule: PaperAutonomyScheduleFacts,
    startup: PaperAutonomyStartupFacts,
    unresolved_action: str | None,
    start_already_attempted_today: bool,
    control_plane_readable: bool,
) -> PaperAutonomyDecision:
    """The one precedence order, evaluated top to bottom.

    Written as an ordered sequence of guards rather than as a nest of conditions
    because the *order* is the policy: which reason outranks which is a decision
    someone had to make, and a reader should be able to check it by reading down
    the function.  Each branch returns, so no later clause can shadow an earlier
    one and the list cannot drift as branches are added.

    The order is:

    1.  the control-plane store cannot be read -- nothing else can be trusted;
    2.  the Paper ownership observed does not match the canonical lifecycle;
    3.  the kill switch is latched -- the operator has already said stop;
    4.  the process is shutting down;
    5.  an action from a previous tick (or a previous process) is unresolved;
    6.  startup could not be proven safe;
    7.  the Paper capability needs a human;
    8.  finalization is still pending -- the session is not safely gone yet;
    9.  the operator disabled autonomy;
    10. the operator paused autonomy;
    11. an autonomous session is up under this intent;
    12. an autonomous start was already attempted today;
    13. preparation is in flight or done;
    14. the schedule cannot be trusted;
    15. it is time to prepare;
    16. it is time to start;
    17. nothing to do.

    Two things about a *live session* are decided in more than one branch, and
    both are load-bearing rather than incidental.

    **Provenance decides authority.**  Every branch that would touch a running
    session first asks whether the session is one this supervisor started.  A
    session launched by hand is not under automation: pausing or stopping it
    because an autonomy switch moved would change the semantics of a feature the
    operator never enrolled.  The manual cases are therefore no-ops carrying a
    reason that says so, and an unattributable session blocks.

    **The orderly stop boundary outranks the intent.**  Inside an autonomous
    session the wind-down is checked before the operator's mode is, because a
    session that has to be flat by a deadline is not a session that should keep
    opening entries or be resumed into the close.  An earlier version read the
    active session first and returned from it, so a paused session at the
    boundary was *resumed* -- the one action the boundary exists to prevent.
    """

    day = schedule.trading_day

    if not control_plane_readable:
        return _blocked(
            "the Paper autonomy control-plane store could not be read",
            day,
            intent_revision,
        )

    if not runtime.paper_ownership_consistent:
        return _blocked(
            "the Paper ownership observed does not match the canonical Paper "
            "lifecycle; a human has to establish what owns it",
            day,
            intent_revision,
        )

    if kill_switch_latched:
        # The kill switch is a stop-requesting authority, not a finalization
        # authority.  If the session is one this supervisor started it is asked
        # to wind down through the canonical stop; if a human is already
        # required, that outranks a stop this process may not be able to
        # complete.
        if runtime.manual_recovery_required:
            return _blocked(
                "the kill switch is latched and the Paper session needs manual "
                "recovery; the supervisor will not reconcile",
                day,
                intent_revision,
            )
        foreign = _not_our_session(
            runtime,
            day,
            intent_revision,
            manual_reason=(
                "the kill switch is latched, but the active Paper session is "
                "manual and is not controlled by the supervisor"
            ),
        )
        if foreign is not None:
            return foreign
        if runtime.session_active:
            return _act(
                PaperAutonomyAction.STOP,
                "the kill switch is latched; winding the autonomous session "
                "down through the canonical stop",
                day,
                intent_revision,
            )
        return _noop(
            "the kill switch is latched; no autonomous work will be requested",
            day,
            intent_revision,
        )

    if runtime.shutting_down:
        return _noop(
            "the process is shutting down; no new autonomous work is admitted",
            day,
            intent_revision,
        )

    if unresolved_action is not None:
        return _blocked(
            f"an autonomy action from an earlier tick is unresolved "
            f"({unresolved_action}); a human has to establish what happened",
            day,
            intent_revision,
        )

    if not startup.proven_safe:
        return _blocked(
            "startup could not be proven safe: the broker and account state is "
            "unknown or non-empty, or the Paper capability needs a human",
            day,
            intent_revision,
        )

    if runtime.manual_recovery_required:
        return _blocked(
            "the Paper session requires manual reconciliation; the supervisor "
            "never reconciles automatically",
            day,
            intent_revision,
        )

    if runtime.finalization_pending:
        return _noop(
            "the Paper session is still finalizing; waiting for the canonical "
            "completion before anything else",
            day,
            intent_revision,
        )

    if intent_mode is PaperAutonomyMode.DISABLED:
        foreign = _not_our_session(
            runtime,
            day,
            intent_revision,
            manual_reason=(
                "autonomy is disabled, but the active Paper session is manual "
                "and is not controlled by the supervisor"
            ),
        )
        if foreign is not None:
            return foreign
        if runtime.session_active:
            return _act(
                PaperAutonomyAction.STOP,
                "the operator disabled autonomy; winding the autonomous session "
                "down through the canonical stop",
                day,
                intent_revision,
            )
        return _noop(
            "the operator has not authorised autonomous Paper trading",
            day,
            intent_revision,
        )

    if intent_mode is PaperAutonomyMode.PAUSED:
        foreign = _not_our_session(
            runtime,
            day,
            intent_revision,
            manual_reason=(
                "autonomy is paused, but the active Paper session is manual and "
                "is not controlled by the supervisor"
            ),
        )
        if foreign is not None:
            return foreign
        if runtime.session_active:
            if schedule.orderly_stop_due:
                return _act(
                    PaperAutonomyAction.STOP,
                    "the orderly stop boundary has arrived while the operator's "
                    "intent is paused; winding the autonomous session down "
                    "through the canonical stop",
                    day,
                    intent_revision,
                )
            if runtime.session_running:
                return _act(
                    PaperAutonomyAction.PAUSE_ENTRIES,
                    "the operator paused autonomy; closing new entries through "
                    "the canonical pause",
                    day,
                    intent_revision,
                )
            return _noop(
                "the operator paused autonomy and its session is already paused",
                day,
                intent_revision,
            )
        return _noop(
            "the operator paused autonomy; no new entries and no new session",
            day,
            intent_revision,
        )

    # From here the operator has authorised autonomous work.
    foreign = _not_our_session(
        runtime,
        day,
        intent_revision,
        manual_reason=(
            "autonomy is enabled, but the active Paper session is manual and is "
            "not controlled by the supervisor"
        ),
    )
    if foreign is not None:
        return foreign

    if runtime.session_active:
        if schedule.orderly_stop_due:
            return _act(
                PaperAutonomyAction.STOP,
                "the orderly stop boundary has arrived; winding the autonomous "
                "session down through the canonical stop",
                day,
                intent_revision,
            )
        if runtime.session_paused:
            return _act(
                PaperAutonomyAction.RESUME_ENTRIES,
                "the operator re-enabled autonomy and its session is paused; "
                "resuming entries through the canonical resume",
                day,
                intent_revision,
            )
        return _noop(
            "an autonomous Paper session is already running under this intent",
            day,
            intent_revision,
        )

    if start_already_attempted_today:
        return _noop(
            "an autonomous start has already been attempted this trading day; "
            "v1 permits one, and a second session is a manual decision",
            day,
            intent_revision,
        )

    if runtime.preparation_active:
        return _noop(
            "candidate preparation is already in flight",
            day,
            intent_revision,
        )

    if runtime.launch_in_flight:
        return _noop(
            "a Paper launch is already in flight",
            day,
            intent_revision,
        )

    if schedule.exceptional_schedule_uncertain:
        return _noop(
            "the trading calendar could not be read; the supervisor will not "
            "act on an uncertain schedule",
            day,
            intent_revision,
        )

    if runtime.preparation_ready:
        if (
            schedule.start_allowed
            and schedule.session in AUTONOMOUS_START_WINDOWS
        ):
            return _act(
                PaperAutonomyAction.START,
                "candidates are ready and the session is open for an "
                "autonomous launch",
                day,
                intent_revision,
            )
        return _noop(
            f"candidates are ready but this session "
            f"({schedule.session.value}) does not permit an autonomous start",
            day,
            intent_revision,
        )

    if (
        schedule.preparation_allowed
        and schedule.session in AUTONOMOUS_PREPARE_WINDOWS
    ):
        return _act(
            PaperAutonomyAction.PREPARE,
            f"the operator authorised autonomy and this session "
            f"({schedule.session.value}) permits preparation",
            day,
            intent_revision,
        )

    return _noop(
        f"the operator authorised autonomy but this session "
        f"({schedule.session.value}) is outside the autonomy policy windows",
        day,
        intent_revision,
    )


def _not_our_session(
    runtime: PaperAutonomyRuntimeFacts,
    day: date,
    intent_revision: int,
    *,
    manual_reason: str,
) -> PaperAutonomyDecision | None:
    """The verdict for a live session this supervisor did not start, or ``None``.

    ``None`` means the session is the supervisor's and the caller should go on to
    control it.  The two other answers are deliberately different, because the
    situations are: a **manual** session is one the supervisor knows it must
    leave alone -- a no-op, with a reason that says exactly that rather than
    implying an autonomy switch acted on it -- while an **unattributable** one is
    a session nobody can currently account for, which is a reason to stop and ask
    rather than to guess a provenance and act on it.
    """

    if not runtime.session_active:
        return None
    if (
        runtime.session_provenance
        is PaperAutonomySessionProvenance.AUTONOMOUS
    ):
        return None
    if runtime.session_provenance is PaperAutonomySessionProvenance.UNKNOWN:
        return _blocked(
            "a Paper session is active but cannot be attributed to the autonomy "
            "supervisor; a human has to establish what it is before the "
            "scheduler may act",
            day,
            intent_revision,
        )
    return _noop(manual_reason, day, intent_revision)


def _noop(
    reason: str, day: date, intent_revision: int
) -> PaperAutonomyDecision:
    return PaperAutonomyDecision(
        action=PaperAutonomyAction.NOOP,
        reason=reason,
        trading_day=day,
        intent_revision=intent_revision,
        action_key=None,
    )


def _blocked(
    reason: str, day: date, intent_revision: int
) -> PaperAutonomyDecision:
    return PaperAutonomyDecision(
        action=PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR,
        reason=reason,
        trading_day=day,
        intent_revision=intent_revision,
        action_key=None,
    )


def _act(
    action: PaperAutonomyAction,
    reason: str,
    day: date,
    intent_revision: int,
) -> PaperAutonomyDecision:
    slot = (
        PaperAutonomyActionSlot.OPERATOR
        if action
        in (
            PaperAutonomyAction.PAUSE_ENTRIES,
            PaperAutonomyAction.RESUME_ENTRIES,
            PaperAutonomyAction.STOP,
        )
        else PaperAutonomyActionSlot.SESSION
    )
    return PaperAutonomyDecision(
        action=action,
        reason=reason,
        trading_day=day,
        intent_revision=intent_revision,
        action_key=action_key_for(
            trading_day=day,
            intent_revision=intent_revision,
            action=PaperAutonomyActionType(action.value),
            slot=slot,
        ),
    )


def observe_action_outcome(
    *,
    action: PaperAutonomyAction,
    accepted: bool,
    detail: str,
) -> PaperAutonomyActionStatus:
    """How far a *request* got, which is not how far the action got.

    A supervisor can know that an owner accepted a request; it cannot know that
    the action completed.  So an accepted request becomes ``REQUESTED`` -- still
    non-terminal -- and only the owner's own finished fact moves it further.
    Anything that returned refusal or raised becomes terminal here, because a
    refusal is a completed outcome.
    """

    if accepted:
        return PaperAutonomyActionStatus.REQUESTED
    return PaperAutonomyActionStatus.REFUSED


def shuts_down_action(action: PaperAutonomyAction) -> bool:
    """Whether an action is about taking work away rather than adding it.

    Used by the shutdown path: once admission is closed, an action that reduces
    work is still allowed and an action that adds it is not.  Stated as a
    predicate rather than as a list checked in two places, so a new action has to
    be classified once.
    """

    return action in (
        PaperAutonomyAction.PAUSE_ENTRIES,
        PaperAutonomyAction.STOP,
        PaperAutonomyAction.NOOP,
        PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR,
    )


__all__ = [
    "AUTONOMOUS_PREPARE_WINDOWS",
    "AUTONOMOUS_START_WINDOWS",
    "MAXIMUM_TICK_INTERVAL_SECONDS",
    "MINIMUM_TICK_INTERVAL_SECONDS",
    "NON_TERMINAL_ACTION_STATUSES",
    "TERMINAL_ACTION_STATUSES",
    "PaperAutonomyAction",
    "PaperAutonomyActionSlot",
    "PaperAutonomyActionStatus",
    "PaperAutonomyActionType",
    "PaperAutonomyDecision",
    "PaperAutonomyPolicy",
    "PaperAutonomyRuntimeFacts",
    "PaperAutonomyScheduleFacts",
    "PaperAutonomySessionWindow",
    "PaperAutonomyStartupFacts",
    "PaperAutonomySupervisorError",
    "PaperAutonomySupervisorViolation",
    "action_key_for",
    "decide_paper_autonomy",
    "observe_action_outcome",
    "shuts_down_action",
]
