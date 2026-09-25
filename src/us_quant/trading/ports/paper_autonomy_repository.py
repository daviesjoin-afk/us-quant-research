"""Persistence for the Paper autonomy intent, behind a narrow port.

The port is deliberately dumb, for the same reason ``StrategyRepositoryPort``
is: it stores what it is handed and it decides no transition.  It does not know
that a latched kill switch blocks ``enable()``, that ``ENABLED`` follows
``PAUSED``, or that a revision moves by one.  Those are policy, they belong to
``PaperAutonomyApplication``, and keeping them out of here is what lets a second
adapter -- an in-memory one for tests, or a different store -- satisfy this
protocol without implementing a slightly different state machine.

Two protocol operations carry real safety weight and are specified rather than
left to the adapter's discretion:

* :meth:`PaperAutonomyRepositoryPort.compare_and_swap_intent` is a genuine
  compare-and-swap.  Last-write-wins is not an acceptable fallback here.  The
  writers of this value are the desktop shell, the operator CLI and, later, an
  unattended scheduler; a stale operator page that still believes the revision
  it loaded must not be able to overwrite a kill switch that was latched after
  the page was drawn.
* :meth:`PaperAutonomyRepositoryPort.load_intent` returns a domain value, never
  a row, and it fails closed.  The adapter owns the decision to refuse a value
  it cannot interpret, because "I could not read the mode" must never be
  answered with a mode.

Errors are rooted at :class:`PaperAutonomyError` rather than at ``RuntimeError``
so a caller can catch the whole feature -- store unreadable, revision conflict,
value refused -- with one clause.  A refusal and a storage failure lead to
different operator actions, so they stay distinct *types* while sharing a root.
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
    """Base class for every Paper autonomy persistence failure."""


class PaperAutonomyStoreUnreadable(PaperAutonomyRepositoryError):
    """A stored intent cannot be believed and will not be repaired.

    Raised for a stored mode that is not a ``PaperAutonomyMode``, a latch column
    that is not a boolean, a revision that is not a non-negative integer, or an
    ``updated_at`` that is not an aware timestamp.

    There is deliberately no "fall back to the default" path.  The default is
    ``DISABLED``, so a fallback would be safe here -- but it would also be
    silent, and an operator who latched a kill switch needs to be told that the
    record of it could not be read rather than shown a plausible ``DISABLED``
    that means "you never asked for anything".
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
        if self.revision < INITIAL_REVISION:
            raise PaperAutonomyRepositoryError(
                f"an autonomy event cannot precede the first revision: "
                f"{self.revision}"
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

        A missing row is ``DISABLED`` with revision
        :data:`~us_quant.trading.domain.paper_autonomy.INITIAL_REVISION`.  It is
        never ``ENABLED``: a store that has never been written describes an
        operator who has never authorised anything.

        Raises ``PaperAutonomyStoreUnreadable`` when a row exists but cannot be
        interpreted.
        """

    def compare_and_swap_intent(
        self,
        *,
        expected_revision: int,
        replacement: PaperAutonomyIntent,
    ) -> None:
        """Write ``replacement`` only if the stored revision is still expected.

        Atomic: the check and the write are one transaction, so two writers that
        both loaded revision *n* produce exactly one accepted write.

        Raises ``PaperAutonomyConflict`` when the stored revision differs, and
        leaves the stored value untouched in that case.
        """

    def append_event(self, event: PaperAutonomyEvent) -> None:
        """Append one transition to the audit trail.

        Called after an accepted :meth:`compare_and_swap_intent`, never before:
        an audit entry that claims a transition which was then refused would be
        a lie in the one record an incident review reads first.
        """

    def recent_events(
        self, limit: int = 50
    ) -> tuple[PaperAutonomyEvent, ...]:
        """The most recent events, oldest first.

        Chronological order rather than newest-first because the caller is
        replaying a decision sequence; a newest-first list would have to be
        reversed by every reader to be read as a story.
        """


__all__ = [
    "PaperAutonomyConflict",
    "PaperAutonomyEvent",
    "PaperAutonomyRepositoryError",
    "PaperAutonomyRepositoryPort",
    "PaperAutonomyStoreUnreadable",
]
