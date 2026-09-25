"""SQLite store for the Paper autonomy intent, behind the repository port.

Two tables: one row holds the operator's intent, and an append-only table holds
its transitions.  The row is addressed by a fixed key rather than by an id,
because there is exactly one Paper autonomy intent: a table that could hold two
would only be able to disagree with itself.

Five storage decisions are deliberate.

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
* **The broker's own text is stored, not a re-spelling of it.**  ``mode`` keeps
  the enum's value verbatim, so what the audit view shows is what was written,
  and reading it back is a strict parse rather than a case-insensitive guess.
* **Nothing is repaired on read.**  A row, an event, or a *history* that cannot
  be believed raises.  Every failure in this module has to point the same way,
  and the permissive direction is the one that starts trading.
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

_HISTORY_QUERY = """
SELECT COUNT(*), MIN(revision), MAX(revision)
FROM paper_autonomy_events
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

        The two reads share one transaction.  Without it the intent row and the
        history aggregate are two independent reads, and a transition committed
        between them would make a perfectly healthy store look incoherent -- the
        one failure mode a coherence check must not invent.
        """

        with closing(connect_sqlite(self.path)) as connection:
            connection.isolation_level = None
            connection.execute("BEGIN")
            try:
                row = connection.execute(
                    _INTENT_QUERY, (SINGLETON_KEY,)
                ).fetchone()
                history = connection.execute(_HISTORY_QUERY).fetchone()
            finally:
                connection.execute("COMMIT")

        _require_coherent_history(row, history)
        if row is None:
            return initial_intent()
        return _intent_from_row(row)

    def recent_events(
        self, limit: int = 50
    ) -> tuple[PaperAutonomyEvent, ...]:
        if limit <= 0:
            return ()
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
        """

        _require_coherent_pair(expected_revision, replacement, event)

        with closing(connect_sqlite(self.path)) as connection:
            # Manual transaction control: ``isolation_level = None`` stops the
            # driver from opening an implicit deferred transaction of its own,
            # which would otherwise collide with the explicit BEGIN below.
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT revision FROM paper_autonomy_intent WHERE key = ?",
                    (SINGLETON_KEY,),
                ).fetchone()
                stored = (
                    INITIAL_REVISION if row is None else _revision_from_row(row)
                )
                if stored != expected_revision:
                    # Left to the handler below so the rollback happens exactly
                    # once, on the one path that ends every failed write.
                    raise PaperAutonomyConflict(
                        f"the stored autonomy revision is {stored}, not the "
                        f"expected {expected_revision}; the write was refused "
                        f"and the stored intent is unchanged"
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
            except PaperAutonomyError:
                # A refusal or an unreadable row is already the right error and
                # the right vocabulary; it just has to reach the caller with
                # nothing written behind it.
                _rollback_quietly(connection)
                raise
            except sqlite3.Error as error:
                _rollback_quietly(connection)
                # Deliberately not a chained message: a SQL statement and a
                # database path are not things an operator surface should print.
                raise PaperAutonomyRepositoryError(
                    "the Paper autonomy transition could not be stored "
                    "atomically; no part of it was written"
                ) from error
            except BaseException:
                _rollback_quietly(connection)
                raise

    # -- schema ---------------------------------------------------------

    def _initialize(self) -> None:
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


def _require_coherent_history(row: object, history: object) -> None:
    """Refuse a history that does not describe the intent standing in front of it.

    Three shapes are rejected, and each of them is a state a *previous* version
    of this store could actually reach, which is why none of them is treated as
    hypothetical:

    * events with no intent row -- the trail of a transition whose intent was
      lost, which must not read as "this system was never configured";
    * an intent row below revision 1 -- a row no accepted transition can write;
    * an intent at revision *n* whose trail is not exactly ``1..n``.

    The third is checked as count, minimum and maximum rather than by walking
    the rows.  With the revision index in place the events are distinct
    integers, so ``COUNT == n`` together with ``MIN == 1`` and ``MAX == n``
    leaves no room for a gap: any missing revision would have to be paid for by
    a duplicate that the index forbids.  Any two of the three would already
    suffice; all three are asserted because they name the three ways the trail
    can fail to be the sequence it claims to be.
    """

    count = int(history[0]) if history[0] is not None else 0  # type: ignore[index]

    if row is None:
        if count:
            raise PaperAutonomyStoreUnreadable(
                f"the Paper autonomy audit trail holds {count} transition(s) "
                f"but no intent row; the store is incomplete and an operator "
                f"has to inspect it"
            )
        return

    revision = _revision_from_row(row)
    if revision < 1:
        raise PaperAutonomyStoreUnreadable(
            f"a stored autonomy intent is at revision {revision}, which no "
            f"accepted transition can produce"
        )

    lowest = history[1]  # type: ignore[index]
    highest = history[2]  # type: ignore[index]
    if count != revision or lowest != 1 or highest != revision:
        raise PaperAutonomyStoreUnreadable(
            f"the autonomy intent is at revision {revision} but its audit "
            f"trail holds {count} event(s) covering {lowest}..{highest}; the "
            f"trail does not describe the intent it belongs to"
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
