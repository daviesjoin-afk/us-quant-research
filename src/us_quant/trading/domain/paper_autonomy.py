"""The Paper autonomy vocabulary: what an *operator* asked the system to do.

This module holds one fact and nothing else: the operator's standing intent for
unsupervised Paper trading.  It is deliberately not a description of a Paper
session.  ``PaperWorkflowController`` owns whether a Paper session exists and
what phase it is in; this owns only whether the operator has authorised the
system to run Paper *without* a human at the keyboard, and whether that
authorisation has been latched off by a kill switch.

The distinction is load bearing, which is why the two never meet:

* an intent survives a process restart, so it is stored;
* a session phase does not, and is therefore re-derived from the broker and the
  workflow every time the process starts.

Persisting a copy of the second kind here would create a second truth that can
disagree with the first -- and the disagreement would be discovered by a
scheduler placing an order, which is the worst place to find it.

Three properties of the value type are also deliberate:

* ``kill_switch_latched`` implies the mode is **not** ``ENABLED``.  A latched
  kill switch is a promise that no future ``enable()`` can resurrect trading,
  and that promise is only keepable if the forbidden combination cannot be
  constructed at all.  The check lives in ``__post_init__`` rather than in the
  repository so that it holds for every construction site, the store included.
* ``updated_at`` must be aware.  The intent's revision is ordered against a
  wall-clock audit trail, and a naive timestamp cannot be ordered against
  anything.
* ``reason`` must be non-empty.  Every write here is an operator decision, and a
  decision whose reason was not recorded is not auditable.

Only Paper appears in this vocabulary.  There is no environment field, no mode
field and no broker field, so a future Live path cannot inherit an autonomy
permission by extending an enum: unsupervised *Live* trading needs its own
authorisation surface and its own review, not a fourth member on this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class PaperAutonomyMode(StrEnum):
    """What the operator wants the autonomous Paper system to be doing.

    The three members are intents, not runtime states.  ``PREPARING``,
    ``RUNNING`` and ``FINALIZED`` are *not* members and must not become members:
    those describe a Paper session, they are owned by ``PaperWorkflowController``,
    and a scheduler that could read them from here would be reading a stale copy
    of a fact it is supposed to re-derive.
    """

    DISABLED = "disabled"
    ENABLED = "enabled"
    PAUSED = "paused"


class PaperAutonomyEventKind(StrEnum):
    """The audit vocabulary for one operator decision.

    A member is written once per accepted transition, so the event trail is a
    replay of the transitions rather than a snapshot of the state: an operator
    reviewing "why was autonomous trading on this morning" needs the sequence,
    not the final value.
    """

    ENABLED = "AUTONOMY_ENABLED"
    PAUSED = "AUTONOMY_PAUSED"
    DISABLED = "AUTONOMY_DISABLED"
    KILL_LATCHED = "AUTONOMY_KILL_LATCHED"
    KILL_CLEARED = "AUTONOMY_KILL_CLEARED"


#: The revision of an intent that has never been written.
#:
#: Zero is also what a caller must pass to perform the *first* write, so a
#: missing row and a never-written row are the same compare-and-swap expectation
#: instead of two special cases.
INITIAL_REVISION = 0

#: The reason recorded on the canonical "nothing has been stored yet" value.
#: Non-empty for the same reason every other reason must be: it is what an
#: operator sees when they ask a system that has never been configured.
NEVER_WRITTEN = "no Paper autonomy intent has been stored"

#: The timestamp on that same value.  Deliberately not "now": a default that
#: moved with the clock would make an unwritten intent look like a recent
#: operator decision.
_NEVER = datetime(1970, 1, 1, tzinfo=timezone.utc)


class PaperAutonomyError(RuntimeError):
    """Base class for every Paper autonomy failure."""


class PaperAutonomyViolation(PaperAutonomyError):
    """A value or a stored row would break an invariant of this vocabulary.

    Raised for an intent that is latched *and* enabled, a naive timestamp, a
    negative revision, a blank reason, and for a stored row whose mode is not a
    ``PaperAutonomyMode``.  None of those are repaired with a guessed default:
    an autonomy permission whose stored value had to be invented is worse than
    no permission at all, and the direction the guess would take is the
    permissive one.
    """


@dataclass(frozen=True, slots=True)
class PaperAutonomyIntent:
    """The operator's standing intent for unsupervised Paper trading.

    Every field describes the *operator*, not the market, the account, the
    strategy or the session.  In particular there is no strategy id, no symbol,
    no size and no broker state here -- those belong to their canonical owners
    and are read live by whoever needs them.
    """

    revision: int
    mode: PaperAutonomyMode
    kill_switch_latched: bool
    updated_at: datetime
    reason: str

    def __post_init__(self) -> None:
        if self.revision < INITIAL_REVISION:
            raise PaperAutonomyViolation(
                f"an autonomy revision cannot be negative: {self.revision}"
            )
        if self.updated_at.tzinfo is None:
            raise PaperAutonomyViolation(
                "an autonomy intent needs a timezone-aware updated_at"
            )
        if not str(self.reason).strip():
            raise PaperAutonomyViolation(
                "an autonomy intent must record the operator's reason"
            )
        if self.kill_switch_latched and self.mode is PaperAutonomyMode.ENABLED:
            raise PaperAutonomyViolation(
                "a latched kill switch is never an enabled intent: clearing the "
                "latch must not be able to resurrect autonomous trading"
            )

    @property
    def allows_autonomous_work(self) -> bool:
        """Whether this intent authorises the system to *start new* Paper work.

        Read-only and derived, so no caller has to remember that the latch wins
        over the mode.  It is a statement about authorisation only: a ``True``
        here says nothing about the market, the account, the lease or the
        strategy, all of which a start still has to pass.
        """

        return (
            self.mode is PaperAutonomyMode.ENABLED
            and not self.kill_switch_latched
        )


def initial_intent() -> PaperAutonomyIntent:
    """The canonical state of an operator who has never decided anything.

    ``DISABLED`` rather than ``ENABLED``, and that direction is the whole point:
    a store that cannot be read, a row that was never written and a value that
    cannot be interpreted all have to fail towards *not* trading.  The opposite
    default would turn a missing file into an unsupervised trading permission.
    """

    return PaperAutonomyIntent(
        revision=INITIAL_REVISION,
        mode=PaperAutonomyMode.DISABLED,
        kill_switch_latched=False,
        updated_at=_NEVER,
        reason=NEVER_WRITTEN,
    )


__all__ = [
    "INITIAL_REVISION",
    "NEVER_WRITTEN",
    "PaperAutonomyError",
    "PaperAutonomyEventKind",
    "PaperAutonomyIntent",
    "PaperAutonomyMode",
    "PaperAutonomyViolation",
    "initial_intent",
]
