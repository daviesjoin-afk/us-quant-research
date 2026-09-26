"""Persistence for what the supervisor *asked for*, behind its own narrow port.

Deliberately a separate store from ``PaperAutonomyRepositoryPort``, which owns
the operator's intent and nothing else.  The two answer different questions --
"what has the operator authorised" and "what has the scheduler already tried" --
and folding the second into the first would put a scheduler's bookkeeping inside
the record of an operator's decision.

The port exists for one property: **a supervisor tick is not idempotent by
construction, so the claim has to be.**  A tick runs again after a timer fires,
after a repaint, after a restart; without an atomic claim, every one of those
would ask the Execution capability for another scan and the Paper capability for
another launch.  Closing that needs three things, and this port is all three:

* a **deterministic key** built from what the action is rather than from when it
  was decided, so the same intent produces the same key and a different one
  cannot collide with it;
* an **atomic claim** -- :meth:`PaperAutonomyActionRepositoryPort.claim` inserts
  and reports whether *this* caller won, with the uniqueness enforced by the
  database rather than by a read followed by a write;
* **terminal outcomes written once**, so a late callback cannot rewrite a
  decision that a restart has already had to reason about.

What the ledger is *not* is a description of the system.  It records requests and
their outcomes; it holds no candidate, no strategy, no session phase, no
position, no order and no broker state, because every one of those is owned
elsewhere and a copy here could only be stale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from us_quant.trading.domain.paper_autonomy import INITIAL_REVISION
from us_quant.trading.domain.paper_autonomy_supervisor import (
    NON_TERMINAL_ACTION_STATUSES,
    PaperAutonomyActionStatus,
    PaperAutonomyActionType,
    PaperAutonomySupervisorError,
    PaperAutonomySupervisorViolation,
)


class PaperAutonomyActionRepositoryError(PaperAutonomySupervisorError):
    """Base class for every failure of the action ledger."""


class PaperAutonomyActionStoreUnreadable(PaperAutonomyActionRepositoryError):
    """A stored action record cannot be believed and will not be repaired.

    Raised for a status, an action type or a timestamp that cannot be read.  An
    unreadable ledger is treated exactly like a corrupt intent store: the
    scheduler cannot know whether it already acted today, so it does not act.
    """


@dataclass(frozen=True, slots=True)
class PaperAutonomyActionRecord:
    """One requested action, and how far it got.

    ``completed_at`` is present exactly when the status is terminal, and that is
    an invariant rather than a convention: a non-terminal record that carried a
    completion time would be an outcome the scheduler might reason from, while a
    terminal one without it would leave the crash analysis unable to say whether
    the record is finished.
    """

    action_key: str
    intent_revision: int
    trading_day: date
    action: PaperAutonomyActionType
    status: PaperAutonomyActionStatus
    claimed_at: datetime
    completed_at: datetime | None
    detail: str

    def __post_init__(self) -> None:
        if not str(self.action_key).strip():
            raise PaperAutonomySupervisorViolation(
                "an action record needs the deterministic key it was claimed under"
            )
        # A negative revision is not a revision any operator intent could have.
        # The decision layer already refuses to build a key from one, and a
        # record that names one could only be produced by a caller reaching past
        # that -- so the value type refuses it too rather than relying on the
        # layer above.
        if self.intent_revision < INITIAL_REVISION:
            raise PaperAutonomySupervisorViolation(
                f"an action record cannot name revision {self.intent_revision}; "
                f"no operator intent has produced it"
            )
        if self.claimed_at.tzinfo is None:
            raise PaperAutonomySupervisorViolation(
                "an action record needs a timezone-aware claimed_at"
            )
        if not str(self.detail).strip():
            raise PaperAutonomySupervisorViolation(
                "an action record must say why the action was requested or how "
                "it ended"
            )
        terminal = self.status not in NON_TERMINAL_ACTION_STATUSES
        if terminal and self.completed_at is None:
            raise PaperAutonomySupervisorViolation(
                f"a terminal action record needs its completion time: "
                f"{self.status.value}"
            )
        if not terminal and self.completed_at is not None:
            raise PaperAutonomySupervisorViolation(
                f"a non-terminal action record cannot carry a completion time: "
                f"{self.status.value}"
            )
        if (
            self.completed_at is not None
            and self.completed_at.tzinfo is None
        ):
            raise PaperAutonomySupervisorViolation(
                "an action record needs a timezone-aware completed_at"
            )

    @property
    def is_terminal(self) -> bool:
        """Whether this record describes a finished action.

        A false here is what the crash analysis turns into "a human has to look
        at this": the request may have been performed, half-performed, or never
        performed at all, and this record cannot tell the three apart.
        """

        return self.status not in NON_TERMINAL_ACTION_STATUSES


@runtime_checkable
class PaperAutonomyActionRepositoryPort(Protocol):
    """The ledger of requested actions and their observed outcomes."""

    def claim(self, record: PaperAutonomyActionRecord) -> bool:
        """Take the right to perform ``record``, atomically, as a *new* claim.

        Returns ``True`` when *this* caller won and ``False`` when the key was
        already claimed.  The uniqueness is enforced by the store, not by a read
        followed by a write: two ticks that both read "not claimed" must still
        produce exactly one winner, and a two-step check would produce two.

        ``record`` must carry the opening state of an action --
        ``CLAIMED`` with no completion time.  Implementations must refuse
        anything else rather than trusting the caller: a claim that accepted
        ``SUCCEEDED`` would let a caller skip the state machine the ledger exists
        to record, and the resulting row would be indistinguishable from one the
        canonical owner had actually reported.

        A claim is persisted *before* the owner is asked.  That ordering is what
        makes a crash recoverable at all -- a claim written afterwards would
        leave an action that happened and was never recorded, which is the one
        state a crash analysis cannot detect.
        """

    def mark_requested(self, *, action_key: str, detail: str) -> None:
        """Record that the owner accepted a claimed action's request.

        The ``CLAIMED`` to ``REQUESTED`` step, exactly once.  It exists because
        the two states mean different things to a restart: a claim whose request
        never reached an owner is a request that certainly did not execute,
        while an accepted one may have.  Without this step the ledger could not
        distinguish them, and the transition was reachable in the enum but not
        through any legal API.

        Raises ``PaperAutonomyActionRepositoryError`` for an unknown key, a key
        that is already ``REQUESTED``, and a key that already holds a terminal
        outcome -- the step happens once or it does not happen.
        """

    def complete(
        self,
        *,
        action_key: str,
        status: PaperAutonomyActionStatus,
        completed_at: datetime,
        detail: str,
    ) -> None:
        """Record a terminal outcome for an action in progress.

        ``status`` must be terminal.  Which transitions are legal is part of the
        contract, because it is what keeps "the owner accepted the request" and
        "the action succeeded" apart:

        * ``CLAIMED`` may go to ``REFUSED`` or ``FAILED`` -- an owner can refuse
          a request, or the call can raise, without the request ever being
          accepted;
        * ``REQUESTED`` may go to any terminal state, including ``SUCCEEDED``,
          because that is the only state from which a canonical finished fact
          can have been observed;
        * ``CLAIMED`` may **not** go straight to ``SUCCEEDED``.  A success has to
          come from a completion the owner published, and an owner that was
          never recorded as having accepted the request cannot have published
          one.

        Raises ``PaperAutonomyActionRepositoryError`` for an unknown key, for a
        key that already holds a terminal outcome, and for a transition the
        rules above forbid.
        """

    def unresolved(self) -> tuple[PaperAutonomyActionRecord, ...]:
        """Every claimed-but-unfinished action, oldest first.

        Read once at startup and once per tick.  A non-empty answer is not a
        problem to be worked around: it means the previous attempt's outcome is
        genuinely unknown, and no retry can establish it.

        An implementation must parse the whole ledger rather than filter in the
        query.  A row it cannot interpret is a row whose status nobody knows,
        and "I could not read one of today's attempts" is the stricter answer,
        not the same one as "nothing is outstanding".
        """

    def start_attempted(self, trading_day: date) -> bool:
        """Whether an autonomous start was already attempted on that day.

        Any status counts, including ``REFUSED`` and ``FAILED``.  v1 permits one
        unattended start per trading day and does not retry a failed one on its
        own authority: a request whose outcome is unknown or unhappy is a
        question for the operator, and the manual route is how a second session
        is asked for.
        """

    def recent(
        self, limit: int = 50
    ) -> tuple[PaperAutonomyActionRecord, ...]:
        """The most recent requests, oldest first, for the audit surface."""


__all__ = [
    "PaperAutonomyActionRecord",
    "PaperAutonomyActionRepositoryError",
    "PaperAutonomyActionRepositoryPort",
    "PaperAutonomyActionStoreUnreadable",
]
