"""SQLite store for the Paper autonomy intent, behind the repository port.

One row holds the operator's intent and an append-only table holds its
transitions.  The row is addressed by a fixed key rather than by an id, because
there is exactly one Paper autonomy intent: a table that could hold two would
only be able to disagree with itself.

Four storage decisions are deliberate.

* **The singleton row is not seeded.**  ``LOAD`` of an empty table returns the
  canonical initial value, and the *first* compare-and-swap inserts it.  So "no
  row" stays a real state that the store has an answer for, rather than a state
  that a constructor silently wrote over.  The alternative -- inserting a
  ``DISABLED`` row in ``_initialize`` -- reads the same afterwards but hides the
  difference between "never written" and "written back to the default", which is
  the difference an incident review is trying to establish.
* **The compare-and-swap is one ``BEGIN IMMEDIATE`` transaction.**  A deferred
  transaction would read the revision before taking the write lock, so two
  writers could both see revision *n* and both proceed; a single-statement
  upsert cannot express "insert only if the table is empty, otherwise update
  only if the revision matches" without either half being unreachable.  Taking
  the write lock first makes the read and the write one step.
* **The broker's own text is stored, not a re-spelling of it.**  ``mode`` keeps
  the enum's value verbatim, so what the audit view shows is what was written,
  and reading it back is a strict parse rather than a case-insensitive guess.
* **Nothing is repaired on read.**  An unreadable row raises; it is never
  interpreted as ``DISABLED``.  Every failure in this module has to point the
  same way, and the permissive direction is the one that starts trading.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from pathlib import Path

from us_quant.sqlite_support import connect_sqlite
from us_quant.trading.adapters.clock import from_stored_text, to_stored_text
from us_quant.trading.domain.paper_autonomy import (
    INITIAL_REVISION,
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


class SQLitePaperAutonomyRepository:
    """The autonomy intent store: one intent row, one append-only event table."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    # -- reads ----------------------------------------------------------

    def load_intent(self) -> PaperAutonomyIntent:
        with closing(connect_sqlite(self.path)) as connection:
            row = connection.execute(
                """
                SELECT revision, mode, kill_switch_latched, reason, updated_at
                FROM paper_autonomy_intent
                WHERE key = ?
                """,
                (SINGLETON_KEY,),
            ).fetchone()
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

    # -- writes ---------------------------------------------------------

    def compare_and_swap_intent(
        self,
        *,
        expected_revision: int,
        replacement: PaperAutonomyIntent,
    ) -> None:
        """Write ``replacement`` iff the stored revision is ``expected``.

        The revision the replacement carries is *not* re-derived here.  What
        revision to write is the application's decision -- this store only
        decides whether the writer still holds the current one.
        """

        with closing(connect_sqlite(self.path)) as connection:
            # Manual transaction control: ``isolation_level = None`` stops the
            # driver from opening an implicit deferred transaction of its own,
            # which would otherwise collide with the explicit BEGIN below.
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT revision
                    FROM paper_autonomy_intent
                    WHERE key = ?
                    """,
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
                connection.execute("COMMIT")
            except BaseException:
                _rollback_quietly(connection)
                raise

    def append_event(self, event: PaperAutonomyEvent) -> None:
        with closing(connect_sqlite(self.path)) as connection:
            with connection:
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


def _rollback_quietly(connection: object) -> None:
    """Undo the open transaction, without masking the failure that caused it.

    A rollback that raises would replace the real error -- a stale writer, an
    unreadable row -- with a connection complaint, and the real error is the one
    the operator can act on.  The transaction is dropped with the connection
    either way, so a failed rollback costs nothing but also hides nothing.
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
    except PaperAutonomyRepositoryError:
        raise
    except Exception as error:
        # The domain refuses a stored value it cannot believe -- a blank reason,
        # most obviously.  Re-raise as a storage failure so the caller has one
        # class to catch, rather than a domain error leaking out of a store.
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
    except PaperAutonomyRepositoryError:
        raise
    except Exception as error:
        raise PaperAutonomyStoreUnreadable(
            f"the stored autonomy event is not a valid event: {error}"
        ) from error


__all__ = ["SINGLETON_KEY", "SQLitePaperAutonomyRepository"]
