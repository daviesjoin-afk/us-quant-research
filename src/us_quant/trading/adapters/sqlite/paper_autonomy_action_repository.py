"""SQLite ledger of the supervisor's requested actions.

One row per requested action, keyed by the deterministic action key.  The key
*is* the primary key, and that single fact is what makes the claim atomic: a
second tick asking the same question collides with the first row instead of
racing it, so the store's uniqueness constraint is the whole idempotency
mechanism rather than a read-then-write check that two ticks could both pass.

Three storage decisions are deliberate.

* **The claim is written before the owner is asked.**  The reverse order would
  leave a window in which an action happened and nothing recorded it -- the one
  state a crash analysis cannot detect, because the ledger would show no attempt
  at all.  With this order the worst case is a claim whose outcome is unknown,
  which is a question an operator can answer.
* **Terminal outcomes are written once, by a guarded ``UPDATE``.**  The guard is
  in the ``WHERE`` clause rather than in a preceding read, so two completions
  cannot slip past each other, and a decision a restart has already had to reason
  about cannot be rewritten afterwards.
* **Nothing is repaired on read.**  An unreadable status, action or timestamp
  raises ``PaperAutonomyActionStoreUnreadable``.  A ledger that cannot be read is
  a ledger that cannot say whether today's start already happened, so the answer
  has to be "do not act" rather than a guessed default.
"""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime
from pathlib import Path
import sqlite3

from us_quant.sqlite_support import connect_sqlite
from us_quant.trading.adapters.clock import from_stored_text, to_stored_text
from us_quant.trading.domain.paper_autonomy_supervisor import (
    NON_TERMINAL_ACTION_STATUSES,
    PaperAutonomyActionStatus,
    PaperAutonomyActionType,
)
from us_quant.trading.ports.paper_autonomy_action_repository import (
    PaperAutonomyActionRecord,
    PaperAutonomyActionRepositoryError,
    PaperAutonomyActionStoreUnreadable,
)

TABLENAME = "paper_autonomy_action"

_COLUMNS = (
    "action_key, intent_revision, trading_day, action, status, "
    "claimed_at, completed_at, detail"
)

_SELECT_ONE = f"SELECT {_COLUMNS} FROM {TABLENAME} WHERE action_key = ?"

_SELECT_RECENT = (
    f"SELECT {_COLUMNS} FROM {TABLENAME} "
    f"ORDER BY claimed_at DESC, action_key DESC LIMIT ?"
)

#: Every row, oldest first.  Read in full rather than filtered in SQL, and that
#: is a correction rather than a preference: an earlier version pushed
#: ``status IN (non-terminal)`` into the query, which made a row whose status
#: could not be read *invisible* to the one query that exists to answer "is
#: anything about today unknown".  A corrupt row is exactly the state that
#: makes the answer unknown, so it has to be seen and refused.  The ledger grows
#: by a few rows a day, so reading it costs nothing worth a correctness hole.
_SELECT_ALL = (
    f"SELECT {_COLUMNS} FROM {TABLENAME} "
    f"ORDER BY claimed_at ASC, action_key ASC"
)


class SQLitePaperAutonomyActionRepository:
    """The action ledger: one append-only row per requested action."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    # -- the claim ------------------------------------------------------

    def claim(self, record: PaperAutonomyActionRecord) -> bool:
        """Take the right to perform ``record``, atomically."""

        try:
            return self._claim(record)
        except PaperAutonomyActionRepositoryError:
            raise
        except sqlite3.Error as error:
            raise PaperAutonomyActionRepositoryError(
                "the Paper autonomy action could not be claimed; no action was "
                "requested"
            ) from error

    def _claim(self, record: PaperAutonomyActionRecord) -> bool:
        with closing(connect_sqlite(self.path)) as connection:
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    f"""
                    INSERT INTO {TABLENAME}(
                        action_key, intent_revision, trading_day, action,
                        status, claimed_at, completed_at, detail
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(action_key) DO NOTHING
                    """,
                    (
                        record.action_key,
                        record.intent_revision,
                        record.trading_day.isoformat(),
                        str(record.action),
                        str(record.status),
                        to_stored_text(record.claimed_at),
                        (
                            None
                            if record.completed_at is None
                            else to_stored_text(record.completed_at)
                        ),
                        record.detail,
                    ),
                )
                won = cursor.rowcount == 1
                connection.execute("COMMIT")
                return won
            except BaseException:
                _rollback_quietly(connection)
                raise

    # -- the outcome ----------------------------------------------------

    def complete(
        self,
        *,
        action_key: str,
        status: PaperAutonomyActionStatus,
        completed_at: datetime,
        detail: str,
    ) -> None:
        """Record a terminal outcome for an already-claimed action."""

        if status in NON_TERMINAL_ACTION_STATUSES:
            raise PaperAutonomyActionRepositoryError(
                f"an action outcome must be terminal, not {status.value}"
            )
        if completed_at.tzinfo is None:
            raise PaperAutonomyActionRepositoryError(
                "an action outcome needs a timezone-aware completion time"
            )
        if not str(detail).strip():
            raise PaperAutonomyActionRepositoryError(
                "an action outcome must say how the action ended"
            )
        try:
            self._complete(action_key, status, completed_at, detail)
        except PaperAutonomyActionRepositoryError:
            raise
        except sqlite3.Error as error:
            raise PaperAutonomyActionRepositoryError(
                "the Paper autonomy action outcome could not be stored"
            ) from error

    def _complete(
        self,
        action_key: str,
        status: PaperAutonomyActionStatus,
        completed_at: datetime,
        detail: str,
    ) -> None:
        guard = ", ".join("?" for _ in NON_TERMINAL_ACTION_STATUSES)
        with closing(connect_sqlite(self.path)) as connection:
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    f"""
                    UPDATE {TABLENAME}
                    SET status = ?, completed_at = ?, detail = ?
                    WHERE action_key = ? AND status IN ({guard})
                    """,
                    (
                        str(status),
                        to_stored_text(completed_at),
                        detail,
                        action_key,
                        *[str(row) for row in NON_TERMINAL_ACTION_STATUSES],
                    ),
                )
                if cursor.rowcount != 1:
                    # Either there is no such action or it already finished.
                    # Both are refusals, and they are different operator
                    # problems, so they are told apart before raising.
                    existing = connection.execute(
                        _SELECT_ONE, (action_key,)
                    ).fetchone()
                    raise PaperAutonomyActionRepositoryError(
                        f"no open action under {action_key!r}"
                        if existing is None
                        else f"the action under {action_key!r} already has a "
                        f"terminal outcome"
                    )
                connection.execute("COMMIT")
            except BaseException:
                _rollback_quietly(connection)
                raise

    # -- reads ----------------------------------------------------------

    def unresolved(self) -> tuple[PaperAutonomyActionRecord, ...]:
        """Every claimed-but-unfinished action, oldest first.

        Reads the whole ledger and parses all of it before answering.  The
        parses are the point: a row this module cannot interpret is a row whose
        status nobody knows, and "I could not read one of today's attempts" is
        not the same answer as "nothing is outstanding" -- it is the stricter
        one.
        """

        records = self._read(_SELECT_ALL, ())
        return tuple(row for row in records if not row.is_terminal)

    def recent(
        self, limit: int = 50
    ) -> tuple[PaperAutonomyActionRecord, ...]:
        if limit <= 0:
            return ()
        rows = self._read(_SELECT_RECENT, (limit,))
        # Read newest-first so ``LIMIT`` keeps the latest, then flip for the
        # reader: the audit view is a story, not a reverse.
        return tuple(reversed(rows))

    def start_attempted(self, trading_day: date) -> bool:
        """Whether an autonomous start was already attempted on that day.

        Asked as "does any start row exist for the day", not "did one succeed".
        The distinction is the whole of PHASE 47's rule: a launch that was
        requested and whose outcome is unknown -- because the process died while
        the broker was connecting -- must not be retried, and a query that only
        looked for successes could not see it.
        """

        try:
            with closing(connect_sqlite(self.path)) as connection:
                row = connection.execute(
                    f"""
                    SELECT 1 FROM {TABLENAME}
                    WHERE trading_day = ? AND action = ?
                    LIMIT 1
                    """,
                    (trading_day.isoformat(), str(PaperAutonomyActionType.START)),
                ).fetchone()
        except sqlite3.Error as error:
            raise PaperAutonomyActionRepositoryError(
                "the Paper autonomy action ledger could not be read"
            ) from error
        return row is not None

    def _read(
        self, statement: str, parameters: tuple
    ) -> tuple[PaperAutonomyActionRecord, ...]:
        try:
            with closing(connect_sqlite(self.path)) as connection:
                rows = connection.execute(statement, parameters).fetchall()
        except sqlite3.Error as error:
            raise PaperAutonomyActionRepositoryError(
                "the Paper autonomy action ledger could not be read"
            ) from error
        return tuple(_record_from_row(row) for row in rows)

    # -- schema ---------------------------------------------------------

    def _initialize(self) -> None:
        try:
            with closing(connect_sqlite(self.path)) as connection:
                with connection:
                    connection.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {TABLENAME}(
                            action_key TEXT PRIMARY KEY,
                            intent_revision INTEGER NOT NULL,
                            trading_day TEXT NOT NULL,
                            action TEXT NOT NULL,
                            status TEXT NOT NULL,
                            claimed_at TEXT NOT NULL,
                            completed_at TEXT,
                            detail TEXT NOT NULL
                        )
                        """
                    )
                    connection.execute(
                        f"""
                        CREATE INDEX IF NOT EXISTS
                            ix_{TABLENAME}_day_action
                        ON {TABLENAME}(trading_day, action)
                        """
                    )
        except sqlite3.Error as error:
            raise PaperAutonomyActionRepositoryError(
                "the Paper autonomy action ledger could not be prepared for use"
            ) from error


def _rollback_quietly(connection: object) -> None:
    """Undo the open transaction without masking the failure that caused it."""

    try:
        connection.execute("ROLLBACK")  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - only reachable on a broken handle
        pass


def _record_from_row(row: object) -> PaperAutonomyActionRecord:
    """Rebuild one action record, or refuse to.

    Strict in both directions: an unknown action or status is a row nobody wrote
    through this module, and a completion timestamp on a non-terminal row is a
    record that contradicts itself.  Neither is repaired.
    """

    action_text = row[3]  # type: ignore[index]
    try:
        action = PaperAutonomyActionType(str(action_text))
    except ValueError as error:
        raise PaperAutonomyActionStoreUnreadable(
            f"the stored autonomy action is not a known action: {action_text!r}"
        ) from error

    status_text = row[4]  # type: ignore[index]
    try:
        status = PaperAutonomyActionStatus(str(status_text))
    except ValueError as error:
        raise PaperAutonomyActionStoreUnreadable(
            f"the stored autonomy action status is not known: {status_text!r}"
        ) from error

    try:
        revision = int(str(row[1]))  # type: ignore[index]
    except (TypeError, ValueError) as error:
        raise PaperAutonomyActionStoreUnreadable(
            "the stored autonomy action revision is not an integer"
        ) from error

    completed_at = _optional_timestamp(row[6])  # type: ignore[index]
    try:
        return PaperAutonomyActionRecord(
            action_key=str(row[0]),  # type: ignore[index]
            intent_revision=revision,
            trading_day=date.fromisoformat(str(row[2])),  # type: ignore[index]
            action=action,
            status=status,
            claimed_at=_timestamp(row[5]),  # type: ignore[index]
            completed_at=completed_at,
            detail=str(row[7]) if row[7] is not None else "",  # type: ignore[index]
        )
    except PaperAutonomyActionStoreUnreadable:
        raise
    except Exception as error:
        raise PaperAutonomyActionStoreUnreadable(
            f"the stored autonomy action is not a valid record: {error}"
        ) from error


def _timestamp(value: object) -> datetime:
    parsed = from_stored_text(None if value is None else str(value))
    if parsed is None:
        raise PaperAutonomyActionStoreUnreadable(
            f"the stored autonomy action timestamp cannot be read: {value!r}"
        )
    if parsed.tzinfo is None:
        raise PaperAutonomyActionStoreUnreadable(
            "the stored autonomy action timestamp has no timezone"
        )
    return parsed


def _optional_timestamp(value: object) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    return _timestamp(value)


__all__ = ["TABLENAME", "SQLitePaperAutonomyActionRepository"]
