"""Persistence for the Paper autonomy intent, behind a narrow port.

The port is deliberately dumb, for the same reason ``StrategyRepositoryPort``
is: it stores what it is handed and it decides no transition.  It does not know
that a latched kill switch blocks ``enable()``, that ``ENABLED`` follows
``PAUSED``, or that a revision moves by one.  Those are policy, they belong to
``PaperAutonomyApplication``, and keeping them out of here is what lets a second
adapter -- an in-memory one for tests, or a different store -- satisfy this
protocol without implementing a slightly different state machine.

The port has **one** write operation.  That is the whole point of its shape, and
it is a correction rather than a preference.  An earlier version exposed two
calls -- compare-and-swap the intent, then append the event -- and two calls are
two transactions.  The gap between them is a real correctness hole, not a
theoretical one:

* a process that dies in the gap leaves a committed intent whose transition was
  never recorded, so the audit trail is missing exactly the revision an incident
  review reads first;
* worse, the gap is *observable* by a concurrent writer.  Writer A can load
  revision *n*, commit *n+1*, and be descheduled before appending its event;
  writer B then loads *n+1*, commits *n+2* and appends its event first.  The
  event table's autoincrement order is now ``n+2, n+1`` while the revisions are
  ``n+1, n+2`` -- and any reader that treats row order as the decision timeline
  reads the decisions out of order;
* and a failed event write leaves the operator looking at an error for a
  transition that has, in fact, permanently happened.

So :meth:`PaperAutonomyRepositoryPort.commit_transition` takes the intent and
its event **together**, and the implementation must hold one transaction across
the compare, the intent write and the event write.

Two further protocol operations carry real safety weight:

* ``load_intent`` returns a domain value, never a row, and it fails closed -- on
  a value it cannot interpret *and* on a history that does not agree with it.  A
  revision whose event is missing is not a revision that can be believed, and
  "no intent row but a non-empty trail" is not "never configured".
* the compare-and-swap inside ``commit_transition`` is genuine.  Last-write-wins
  is not an acceptable fallback.  The writers of this value are the desktop
  shell, the operator CLI and, later, an unattended scheduler; a stale operator
  page must not be able to overwrite a kill switch latched after it was drawn.

Errors are rooted at :class:`PaperAutonomyError` rather than at ``RuntimeError``
so a caller can catch the whole feature -- store unreadable, revision conflict,
value refused, storage failure -- with one clause.  A refusal and a storage
failure lead to different operator actions, so they stay distinct *types* while
sharing a root.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from us_quant.trading.domain.paper_autonomy import (
    INITIAL_REVISION,
    PaperAutonomyError,
    PaperAutonomyEventKind,
    PaperAutonomyIntent,
)


class PaperAutonomyRepositoryError(PaperAutonomyError):
    """Base class for every Paper autonomy persistence failure.

    Also raised when a transition's own parts do not agree with each other -- a
    replacement that does not advance the revision by one, an event naming a
    different revision, or an event whose instant or reason is not the intent's.
    That is not a policy refusal: it is a record that could not have been
    produced by one accepted transition, and a store that accepted it would hold
    a trail that does not describe the intent beside it.
    """


class PaperAutonomyStoreUnreadable(PaperAutonomyRepositoryError):
    """A stored intent or audit trail cannot be believed and will not be repaired.

    Raised for a stored mode that is not a ``PaperAutonomyMode``, a latch column
    that is not a boolean, a revision that is not a non-negative integer, an
    ``updated_at`` that is not an aware timestamp, an event kind that is not a
    ``PaperAutonomyEventKind``, a stale or naive event timestamp, and -- the half
    that only a history can violate -- an audit trail that does not describe the
    intent in front of it: events with no intent row, a gap in the revisions, or
    a count that disagrees with the intent's revision.

    There is deliberately no "fall back to the default" path.  The default is
    ``DISABLED``, so a fallback would be safe here -- but it would also be
    silent, and an operator who latched a kill switch needs to be told that the
    record of it could not be read rather than shown a plausible ``DISABLED``
    that means "you never asked for anything".

    Duplicate revisions are refused by the store's own uniqueness constraint
    rather than by a read, and that failure surfaces here too: choosing which of
    two conflicting entries to believe is an operator decision, not this
    module's.
    """


class PaperAutonomyConflict(PaperAutonomyRepositoryError):
    """The stored revision is not the one the writer expected.

    The write is refused and the stored value is left exactly as it was.  This
    is what makes a stale writer detectable instead of merely unlikely: two
    operator surfaces racing on one intent produce one accepted transition and
    one refusal naming the revision that actually won.
    """


@dataclass(frozen=True, slots=True)
class PaperAutonomyEvent:
    """One transition of the intent, written to the audit trail.

    ``revision`` is the revision the transition *produced*, so the trail and the
    value order against each other: the event with revision ``n`` is the record
    of the write that created revision ``n``.

    The timestamp must be aware and the detail must be non-empty for the same
    reason the intent's own reason must be: this is the only record of who moved
    the system into unsupervised Paper trading and why, and an entry that cannot
    be ordered or explained is not an audit entry.
    """

    revision: int
    kind: PaperAutonomyEventKind
    detail: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        # Strictly greater than the initial revision, not merely non-negative:
        # an event exists to record the transition that *produced* a revision,
        # and revision 0 is the state of having produced none.  An event at
        # revision 0 would name a transition that never happened, and the trail
        # would then claim a decision the operator never made.
        if self.revision <= INITIAL_REVISION:
            raise PaperAutonomyRepositoryError(
                f"an autonomy event cannot describe revision {self.revision}: "
                f"it records the transition that produced its revision, and "
                f"revision {INITIAL_REVISION} is produced by no transition"
            )
        if self.occurred_at.tzinfo is None:
            raise PaperAutonomyRepositoryError(
                "an autonomy event needs a timezone-aware occurred_at"
            )
        if not str(self.detail).strip():
            raise PaperAutonomyRepositoryError(
                "an autonomy event must record what the operator decided"
            )


@runtime_checkable
class PaperAutonomyRepositoryPort(Protocol):
    """Storage for the operator's autonomy intent and its audit trail."""

    def load_intent(self) -> PaperAutonomyIntent:
        """The stored intent, or the canonical initial one when none is stored.

        "None is stored" means the whole store is empty: no intent row **and**
        no audit events.  A brand-new database is the only thing that reads as
        :data:`~us_quant.trading.domain.paper_autonomy.INITIAL_REVISION` /
        ``DISABLED``; it is never ``ENABLED``, because a store that has never
        been written describes an operator who has never authorised anything.

        Reading the intent means reading the *record*: the whole trail is read
        and parsed, and the latest event has to still describe the intent beside
        it.  Returning an intent while leaving its trail unchecked would hand
        back exactly the value an unattended reader acts on, from a store whose
        only record of how it got there is unreadable.

        Raises ``PaperAutonomyStoreUnreadable`` when a row exists but cannot be
        interpreted, when an event cannot be interpreted, and when the trail and
        the intent do not agree -- an intent at revision *n* whose trail is not
        exactly ``1..n`` in order and does not end on the transition that
        produced *n*, or a trail with no intent row in front of it.  An
        incomplete record is not a fresh one; it is a store an operator has to
        look at.
        """

    def commit_transition(
        self,
        *,
        expected_revision: int,
        replacement: PaperAutonomyIntent,
        event: PaperAutonomyEvent,
    ) -> None:
        """Store one transition -- the intent **and** its audit event -- atomically.

        One transaction holds the compare, the intent write and the event
        write.  Any failure rolls back all of them, so there is no stored state
        in which the intent has moved and the trail has not.

        Implementations must also validate the *existing* stored state inside
        that transaction before modifying anything, to the same standard
        :meth:`load_intent` applies.  The caller reads before it writes, so a
        corrupt trail is not reachable through it -- but the state can change
        between that read and this lock, and a store that appended to a trail it
        cannot read would be extending a record nobody can interpret.  "The
        caller usually reads first" is not a storage guarantee.

        The three arguments are one fact, and implementations must refuse a set
        that could not have come from a single accepted transition: the
        replacement must advance the revision by exactly one, the event must
        name that same revision, and the two must share one instant and one
        operator reason.  That is storage integrity -- the record has to be
        self-consistent -- not lifecycle policy, and it is checked here rather
        than trusted because a store that accepted an incoherent pair would
        leave the trail unable to describe the intent it belongs to.

        Raises ``PaperAutonomyConflict`` when the stored revision differs, in
        which case nothing is written.  Raises ``PaperAutonomyRepositoryError``
        for an incoherent pair or any storage failure, also writing nothing.
        """

    def recent_events(
        self, limit: int = 50
    ) -> tuple[PaperAutonomyEvent, ...]:
        """The most recent events, oldest first.

        Chronological order rather than newest-first because the caller is
        replaying a decision sequence; a newest-first list would have to be
        reversed by every reader to be read as a story.

        The events are never pruned, so this is a read *window* over the whole
        trail rather than a separate history: the revision of the intent and the
        number of events in the store are always the same number, and a reader
        that needs the whole sequence asks for a limit large enough to hold it.
        """


__all__ = [
    "PaperAutonomyConflict",
    "PaperAutonomyEvent",
    "PaperAutonomyRepositoryError",
    "PaperAutonomyRepositoryPort",
    "PaperAutonomyStoreUnreadable",
]
