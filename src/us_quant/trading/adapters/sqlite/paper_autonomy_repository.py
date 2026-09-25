"""SQLite store for the Paper autonomy intent, behind the repository port.

Two tables: one row holds the operator's intent, and an append-only table holds
its transitions.  The row is addressed by a fixed key rather than by an id,
because there is exactly one Paper autonomy intent: a table that could hold two
would only be able to disagree with itself.

Six storage decisions are deliberate.

* **The singleton row is not seeded.**  ``LOAD`` of an empty table returns the
  canonical initial value, and the *first* transition inserts it.  So "no row"
  stays a real state that the store has an answer for, rather than a state a
  constructor silently wrote over.
* **A transition is one transaction.**  The revision compare, the intent write
  and the audit-event write share a single ``BEGIN IMMEDIATE``.  The port's
  docstring explains why two transactions were a correctness defect rather than
  an inefficiency; the short version is that the gap between them is observable
  by a concurrent writer, and the event table's autoincrement order can then
  disagree with the revision order it is supposed to describe.
* **One event per revision, enforced by the schema.**  A unique index on
  ``paper_autonomy_events(revision)`` makes "an accepted transition produced
  this revision" a property of the store instead of a convention.  A database
  written before the index existed can already hold a duplicate, and that case
  is *not* repaired: dropping one of the two would be this module choosing which
  half of a conflicting record to believe.
* **A stored record is only believed as a *pair*.**  Reading the intent means
  reading its whole trail and parsing every row of it, and the latest event has
  to still describe the intent standing beside it.  Revision arithmetic that
  adds up over rows nobody parsed is not coherence: a trail of rows that cannot
  be turned into events would otherwise hand back a perfectly plausible intent,
  and an unattended reader would act on an authorisation whose record is broken.
* **The same validation guards reads and writes.**  :func:`_read_and_validate_state`
  is called by ``load_intent`` *and* by ``commit_transition`` before it modifies
  anything, so the store can never extend a trail it cannot read.  Two copies of
  this logic is how the read path ends up knowing about corruption while the
  write path does not; R1/R11 of the final review is the record of that.
* **Nothing is repaired on read.**  A row, an event, or a history that cannot be
  believed raises.  Every failure in this module has to point the same way, and
  the permissive direction is the one that starts trading.
* **Every storage failure lands in this module's vocabulary.**  A refused write
  lock, an unreadable file, a malformed schema and a failed ``COMMIT`` all
  surface as :class:`PaperAutonomyRepositoryError` (or
  :class:`PaperAutonomyStoreUnreadable` when the stored bytes are the problem),
  never as a bare ``sqlite3.Error``.  A caller that has to catch sqlite3 to fail
  closed is a caller that can forget to.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3

from us_quant.sqlite_support import connect_sqlite
from us_quant.trading.adapters.clock import from_stored_text, to_stored_text
from us_quant.trading.domain.paper_autonomy import (
    INITIAL_REVISION,
    PaperAutonomyError,
    PaperAutonomyEventKind,
    PaperAutonomyIntent,
    PaperAutonomyMode,
    initial_intent,
)
from us_quant.trading.ports.paper_autonomy_repository import (
    PaperAutonomyConflict,
    PaperAutonomyEvent,
    PaperAutonomyRepositoryError,
    PaperAutonomyStoreUnreadable,
)

#: The key of the one intent row.
SINGLETON_KEY = "paper"

#: The name of the uniqueness constraint that makes one event per revision a
#: property of the store rather than a convention.
REVISION_INDEX = "ux_paper_autonomy_events_revision"

_INTENT_QUERY = """
SELECT revision, mode, kill_switch_latched, reason, updated_at
FROM paper_autonomy_intent
WHERE key = ?
"""

#: The whole trail, in the order it has to be read: revisions ascending.  The
#: uniqueness index makes that order total, and the atomic transition commit is
#: what keeps it identical to the order the rows were written in.
_EVENT_QUERY = """
SELECT revision, event, detail, occurred_at
FROM paper_autonomy_events
ORDER BY revision ASC
"""


class SQLitePaperAutonomyRepository:
    """The autonomy intent store: one intent row, one append-only event table."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    # -- reads ----------------------------------------------------------

    def load_intent(self) -> PaperAutonomyIntent:
        """The stored intent, or the canonical initial one when nothing is stored.

        The intent, its whole trail and the coherence between them are read and
        checked in **one** read transaction: without that, the intent row and
        the history could be read at two different moments, and a transition
        committed in between would make a healthy store look broken -- the one
        failure mode a coherence check must not invent.
        """

        try:
            return self._load_intent()
        except PaperAutonomyError:
            raise
        except sqlite3.Error as error:
            raise PaperAutonomyRepositoryError(
                "the Paper autonomy store could not be read"
            ) from error

    def recent_events(
        self, limit: int = 50
    ) -> tuple[PaperAutonomyEvent, ...]:
        if limit <= 0:
            return ()
        try:
            with closing(connect_sqlite(self.path)) as connection:
                rows = connection.execute(
                    """
                    SELECT revision, event, detail, occurred_at
                    FROM paper_autonomy_events
                    ORDER BY event_id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except PaperAutonomyError:
            raise
        except sqlite3.Error as error:
            raise PaperAutonomyRepositoryError(
                "the Paper autonomy audit trail could not be read"
            ) from error
        # Read newest-first so ``LIMIT`` keeps the *latest* events, then flip to
        # chronological order for the reader.
        return tuple(_event_from_row(row) for row in reversed(rows))

    # -- the one write --------------------------------------------------

    def commit_transition(
        self,
        *,
        expected_revision: int,
        replacement: PaperAutonomyIntent,
        event: PaperAutonomyEvent,
    ) -> None:
        """Compare, write the intent and append its event, in one transaction.

        The pair is validated before the transaction opens, so an incoherent
        transition costs no write and no lock.  The revision the replacement
        carries is *not* re-derived here: what revision to write is the
        application's decision -- the store only checks that the writer still
        holds the current one, and that the three arguments describe one
        transition.

        The stored state is validated *again* inside the write lock, before
        anything is modified.  The application reads before it writes, so a
        corrupt trail is not reachable through it -- but "the caller usually
        reads first" is not a storage guarantee, and the state can change
        between that read and this lock.  Validating here is what lets the port
        promise that this store never extends a trail it cannot read.
        """

        _require_coherent_pair(expected_revision, replacement, event)

        try:
            self._commit_transition(expected_revision, replacement, event)
        except PaperAutonomyError:
            # A refusal or an unreadable record is already the right error and
            # the right vocabulary; it just has to reach the caller with nothing
            # written behind it.
            raise
        except sqlite3.Error as error:
            # Deliberately not a chained message: a SQL statement and a database
            # path are not things an operator surface should print.
            raise PaperAutonomyRepositoryError(
                "the Paper autonomy transition could not be stored atomically; "
                "no part of it was written"
            ) from error

    # -- internals ------------------------------------------------------

    def _load_intent(self) -> PaperAutonomyIntent:
        with closing(connect_sqlite(self.path)) as connection:
            connection.isolation_level = None
            connection.execute("BEGIN")
            try:
                intent, _events = _read_and_validate_state(connection)
            finally:
                # A read transaction has nothing to commit, and ending it must
                # not be able to replace the failure being reported: the
                # snapshot has already been consumed into Python values, and
                # closing the connection would release it anyway.
                _rollback_quietly(connection)
        return intent

    def _commit_transition(
        self,
        expected_revision: int,
        replacement: PaperAutonomyIntent,
        event: PaperAutonomyEvent,
    ) -> None:
        with closing(connect_sqlite(self.path)) as connection:
            # Manual transaction control: ``isolation_level = None`` stops the
            # driver from opening an implicit deferred transaction of its own,
            # which would otherwise collide with the explicit BEGIN below.
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            try:
                stored_intent, _stored_events = _read_and_validate_state(
                    connection
                )
                if stored_intent.revision != expected_revision:
                    raise PaperAutonomyConflict(
                        f"the stored autonomy revision is "
                        f"{stored_intent.revision}, not the expected "
                        f"{expected_revision}; the write was refused and the "
                        f"stored intent is unchanged"
                    )
                connection.execute(
                    """
                    INSERT INTO paper_autonomy_intent(
                        key, revision, mode, kill_switch_latched,
                        reason, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        revision = excluded.revision,
                        mode = excluded.mode,
                        kill_switch_latched = excluded.kill_switch_latched,
                        reason = excluded.reason,
                        updated_at = excluded.updated_at
                    """,
                    (
                        SINGLETON_KEY,
                        replacement.revision,
                        str(replacement.mode),
                        int(replacement.kill_switch_latched),
                        replacement.reason,
                        to_stored_text(replacement.updated_at),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO paper_autonomy_events(
                        revision, event, detail, occurred_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        event.revision,
                        str(event.kind),
                        event.detail,
                        to_stored_text(event.occurred_at),
                    ),
                )
                connection.execute("COMMIT")
            except BaseException:
                # One path ends every failed write, so the rollback cannot be
                # forgotten on a branch someone adds later.
                _rollback_quietly(connection)
                raise

    # -- schema ---------------------------------------------------------

    def _initialize(self) -> None:
        try:
            self._create_schema()
        except PaperAutonomyError:
            # A duplicate revision is a known corruption and keeps its own type;
            # it must not be downgraded to a generic storage failure.
            raise
        except sqlite3.Error as error:
            raise PaperAutonomyRepositoryError(
                "the Paper autonomy store could not be prepared for use"
            ) from error

    def _create_schema(self) -> None:
        with closing(connect_sqlite(self.path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_autonomy_intent(
                        key TEXT PRIMARY KEY,
                        revision INTEGER NOT NULL,
                        mode TEXT NOT NULL,
                        kill_switch_latched INTEGER NOT NULL,
                        reason TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_autonomy_events(
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        revision INTEGER NOT NULL,
                        event TEXT NOT NULL,
                        detail TEXT NOT NULL,
                        occurred_at TEXT NOT NULL
                    )
                    """
                )
                _require_unique_event_revision(connection)


def _read_and_validate_state(
    connection: object,
) -> tuple[PaperAutonomyIntent, tuple[PaperAutonomyEvent, ...]]:
    """The stored intent and its whole trail, or a refusal.

    The single place the stored state is interpreted, shared by the read path
    and by the write path so the two cannot drift.  Everything a reader could be
    misled by is checked here:

    * every event row has to *parse* -- a kind that is not an event kind, a
      blank reason or an unreadable timestamp is a broken record, not a shorter
      one;
    * the revisions have to be exactly ``1..n``, in order, for the intent's own
      revision ``n`` -- which is also what says the latest event is the
      transition that produced the stored value;
    * the latest event has to still describe the intent: same instant, same
      operator reason, and (through the sequence above) the same revision.  That
      is the pair the atomic commit wrote, and this is the check that it is
      still one pair on disk.

    What is deliberately **not** derived here is the mode.  Reconstructing
    ``ENABLED`` / ``PAUSED`` / ``DISABLED`` from the event kinds would put
    lifecycle policy in the store, and transition legality belongs to
    ``PaperAutonomyApplication`` alone.
    """

    row = connection.execute(  # type: ignore[attr-defined]
        _INTENT_QUERY, (SINGLETON_KEY,)
    ).fetchone()
    event_rows = connection.execute(  # type: ignore[attr-defined]
        _EVENT_QUERY
    ).fetchall()

    intent = None if row is None else _intent_from_row(row)
    events = tuple(_event_from_row(event_row) for event_row in event_rows)
    _require_coherent_history(intent, events)
    return (initial_intent() if intent is None else intent), events


def _require_coherent_history(
    intent: PaperAutonomyIntent | None,
    events: tuple[PaperAutonomyEvent, ...],
) -> None:
    """Refuse a history that does not describe the intent standing in front of it.

    Four shapes are rejected, and each of them is a state an *earlier* version
    of this store could actually reach, which is why none of them is treated as
    hypothetical:

    * events with no intent row -- the trail of a transition whose intent was
      lost, which must not read as "this system was never configured";
    * an intent row below revision 1 -- a row no accepted transition can write;
    * an intent at revision *n* whose trail is not exactly ``1..n``;
    * an intent whose trail no longer ends on the transition that produced it.

    The comparison is positional rather than an aggregate: ``COUNT``/``MIN``/
    ``MAX`` would prove the revision *topology* while proving nothing about the
    rows themselves, and a trail of unparseable rows is the corruption that
    matters most, because the intent beside it still looks perfectly plausible.
    Operator transitions are rare, so reading the sequence is affordable and
    correctness comes first.
    """

    if intent is None:
        if events:
            raise PaperAutonomyStoreUnreadable(
                f"the Paper autonomy audit trail holds {len(events)} "
                f"transition(s) but no intent row; the store is incomplete and "
                f"an operator has to inspect it"
            )
        return

    if intent.revision <= INITIAL_REVISION:
        raise PaperAutonomyStoreUnreadable(
            f"a stored autonomy intent is at revision {intent.revision}, which "
            f"no accepted transition can produce"
        )

    # One comparison is the whole sequence check: the trail has to be exactly
    # ``1..n`` for the intent's own revision ``n``, which is what makes the
    # latest event the transition that produced the stored value.  It also
    # covers ``latest.revision == intent.revision``, and it is what makes
    # ``events[-1]`` below safe to take.
    revisions = [event.revision for event in events]
    if revisions != list(range(1, intent.revision + 1)):
        raise PaperAutonomyStoreUnreadable(
            f"the autonomy intent is at revision {intent.revision} but its "
            f"audit trail holds revisions {revisions}; the trail does not "
            f"describe the intent it belongs to"
        )

    latest = events[-1]
    if latest.occurred_at != intent.updated_at:
        raise PaperAutonomyStoreUnreadable(
            "the latest audit event and the intent it produced do not share "
            "one instant"
        )
    if latest.detail != intent.reason:
        raise PaperAutonomyStoreUnreadable(
            "the latest audit event does not record the reason the intent was "
            "written with"
        )


def _require_unique_event_revision(connection: object) -> None:
    """Make "one event per revision" a constraint rather than a convention.

    Created as an index rather than as a column constraint so an existing
    database keeps opening: ``CREATE TABLE IF NOT EXISTS`` never grows or
    re-declares a table that already exists, so a ``UNIQUE`` on the column
    definition would only apply to databases created after this change.

    A store that predates the index can already hold two events for one
    revision, in which case the index cannot be built.  That is not repaired
    here.  Deleting one of the rows, or rebuilding the trail from the revision
    count, would be this module inventing a history -- and the whole point of
    the trail is that it is the record, not a reconstruction of one.
    """

    try:
        connection.execute(  # type: ignore[attr-defined]
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS {REVISION_INDEX}
            ON paper_autonomy_events(revision)
            """
        )
    except sqlite3.Error as error:
        raise PaperAutonomyStoreUnreadable(
            "the stored Paper autonomy audit trail holds more than one event "
            "for a revision; the store cannot be read as a transition sequence "
            "and an operator has to inspect it"
        ) from error


def _require_coherent_pair(
    expected_revision: int,
    replacement: PaperAutonomyIntent,
    event: PaperAutonomyEvent,
) -> None:
    """Refuse a triple that could not have come from one accepted transition.

    Each check is a statement about the *record*, not about the lifecycle: this
    module still decides no transition.  What it does decide is that a stored
    intent and its stored event describe the same transition, because a reader
    reconstructs one from the other and a store that accepted a mismatched pair
    would hand every future reader two facts that cannot both be true.
    """

    if replacement.revision != expected_revision + 1:
        raise PaperAutonomyRepositoryError(
            f"a transition must advance the revision by exactly one, not from "
            f"{expected_revision} to {replacement.revision}"
        )
    if event.revision != replacement.revision:
        raise PaperAutonomyRepositoryError(
            f"the audit event names revision {event.revision} but the intent "
            f"advances to {replacement.revision}"
        )
    if event.occurred_at != replacement.updated_at:
        raise PaperAutonomyRepositoryError(
            "the audit event and the intent it records must share one instant"
        )
    if event.detail != replacement.reason:
        raise PaperAutonomyRepositoryError(
            "the audit event must record the operator reason the intent was "
            "written with"
        )


def _rollback_quietly(connection: object) -> None:
    """Undo the open transaction, without masking the failure that caused it.

    A rollback that raises would replace the real error -- a stale writer, an
    unreadable row, a rejected event -- with a connection complaint, and the
    real error is the one the operator can act on.  The transaction is dropped
    with the connection either way, so a failed rollback costs nothing but also
    hides nothing.
    """

    try:
        connection.execute("ROLLBACK")  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - only reachable on a broken handle
        pass


def _revision_from_row(row: object) -> int:
    try:
        revision = int(str(row[0]))  # type: ignore[index]
    except (TypeError, ValueError) as error:
        raise PaperAutonomyStoreUnreadable(
            "the stored autonomy revision is not an integer"
        ) from error
    if revision < INITIAL_REVISION:
        raise PaperAutonomyStoreUnreadable(
            f"the stored autonomy revision is negative: {revision}"
        )
    return revision


def _intent_from_row(row: object) -> PaperAutonomyIntent:
    """Rebuild the domain intent from a stored row, or refuse to.

    Every field is parsed strictly.  ``PaperAutonomyMode(text)`` is a
    case-sensitive lookup on purpose: a stored ``ENABLED`` is not the stored
    value of ``enabled``, and accepting it here would mean this module had
    invented a normalisation rule for a value nobody wrote.
    """

    revision = _revision_from_row(row)
    mode_text = row[1]  # type: ignore[index]
    try:
        mode = PaperAutonomyMode(str(mode_text))
    except ValueError as error:
        raise PaperAutonomyStoreUnreadable(
            f"the stored autonomy mode is not a known mode: {mode_text!r}"
        ) from error

    latched_text = row[2]  # type: ignore[index]
    try:
        latched = int(str(latched_text))
    except (TypeError, ValueError) as error:
        raise PaperAutonomyStoreUnreadable(
            f"the stored kill-switch latch is not a boolean: {latched_text!r}"
        ) from error
    if latched not in (0, 1):
        raise PaperAutonomyStoreUnreadable(
            f"the stored kill-switch latch is not a boolean: {latched_text!r}"
        )

    reason = row[3]  # type: ignore[index]
    updated_at = _updated_at_from_row(row[4])  # type: ignore[index]

    try:
        return PaperAutonomyIntent(
            revision=revision,
            mode=mode,
            kill_switch_latched=bool(latched),
            updated_at=updated_at,
            reason=str(reason) if reason is not None else "",
        )
    except PaperAutonomyStoreUnreadable:
        raise
    except Exception as error:
        # The domain refuses a stored value it cannot believe -- a blank reason,
        # most obviously.  Re-raise as a storage failure so the *read* path has
        # one vocabulary: whatever a row's contents were, the outcome here is
        # "these bytes are not an intent", never "a value rule was broken",
        # which would send the caller looking at the application instead of the
        # store.
        raise PaperAutonomyStoreUnreadable(
            f"the stored autonomy intent is not a valid intent: {error}"
        ) from error


def _updated_at_from_row(value: object) -> datetime:
    parsed = from_stored_text(None if value is None else str(value))
    if parsed is None:
        raise PaperAutonomyStoreUnreadable(
            f"the stored autonomy timestamp cannot be read: {value!r}"
        )
    if parsed.tzinfo is None:
        raise PaperAutonomyStoreUnreadable(
            "the stored autonomy timestamp has no timezone"
        )
    return parsed


def _event_from_row(row: object) -> PaperAutonomyEvent:
    kind_text = row[1]  # type: ignore[index]
    try:
        kind = PaperAutonomyEventKind(str(kind_text))
    except ValueError as error:
        raise PaperAutonomyStoreUnreadable(
            f"the stored autonomy event is not a known event: {kind_text!r}"
        ) from error
    revision = _revision_from_row(row)
    occurred_at = _updated_at_from_row(row[3])  # type: ignore[index]
    detail = row[2]  # type: ignore[index]
    try:
        return PaperAutonomyEvent(
            revision=revision,
            kind=kind,
            detail=str(detail) if detail is not None else "",
            occurred_at=occurred_at,
        )
    except PaperAutonomyStoreUnreadable:
        raise
    except Exception as error:
        # Same reasoning as the intent read: a stored event that breaks a rule
        # of its own type is still, from here, a row that cannot be read.
        raise PaperAutonomyStoreUnreadable(
            f"the stored autonomy event is not a valid event: {error}"
        ) from error


__all__ = [
    "REVISION_INDEX",
    "SINGLETON_KEY",
    "SQLitePaperAutonomyRepository",
]
