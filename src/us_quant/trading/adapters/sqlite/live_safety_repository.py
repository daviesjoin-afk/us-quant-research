"""SQLite persistence for the Stage 4-A Live safety record."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
from collections.abc import Iterator

from us_quant.sqlite_support import connect_sqlite
from us_quant.trading.adapters.clock import from_stored_text, to_stored_text
from us_quant.trading.domain.live_safety import (
    LiveAccountFingerprint,
    LiveCanaryLimits,
    LiveKillLatch,
    LiveOperatorAuthorization,
    LiveRecoveryLatch,
    LiveSafetyError,
    LiveSafetyRecord,
)
from us_quant.trading.ports.live_safety_repository import (
    LiveSafetyConflict,
    LiveSafetyExecutionLease,
    LiveSafetyRepositoryError,
    LiveSafetyStoreUnreadable,
)

_KEY = "live"


class _SQLiteLiveSafetyExecutionLease:
    def __init__(self, connection: sqlite3.Connection, record: LiveSafetyRecord) -> None:
        self._connection = connection
        self._record = record

    @property
    def record(self) -> LiveSafetyRecord:
        return self._record

    def require_reconciliation(
        self,
        *,
        at: datetime,
        reason: str,
        broker_order_id: int | None = None,
    ) -> LiveSafetyRecord:
        if self._record.recovery_latch.is_required:
            return self._record
        recovery = LiveRecoveryLatch().require(
            at=at,
            reason=reason,
            broker_order_id=broker_order_id,
        )
        replacement = LiveSafetyRecord(
            self._record.revision + 1,
            self._record.authorization,
            self._record.kill_latch,
            recovery,
        )
        if self._record.revision == 0:
            self._connection.execute(
                """INSERT INTO live_safety_state(
                       key, revision, authorization_json, kill_latched,
                       kill_latched_at, kill_reason, recovery_latched,
                       recovery_required_at, recovery_reason, recovery_broker_order_id
                   ) VALUES (?, ?, NULL, 0, NULL, NULL, 1, ?, ?, ?)""",
                (
                    _KEY,
                    replacement.revision,
                    to_stored_text(at),
                    reason.strip(),
                    broker_order_id,
                ),
            )
        else:
            cursor = self._connection.execute(
                """UPDATE live_safety_state
                   SET revision = ?, recovery_latched = 1,
                       recovery_required_at = ?, recovery_reason = ?,
                       recovery_broker_order_id = ?
                   WHERE key = ? AND revision = ?""",
                (
                    replacement.revision,
                    to_stored_text(at),
                    reason.strip(),
                    broker_order_id,
                    _KEY,
                    self._record.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise LiveSafetyConflict("Live recovery barrier lost its safety revision")
        self._record = replacement
        return replacement

    def __eq__(self, other: object) -> bool:
        return self._record == other


class SQLiteLiveSafetyRepository:
    """Owns the separate Live safety database and its fail-closed decoding."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(connect_sqlite(self.path)) as connection:
                with connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS live_safety_state(
                            key TEXT PRIMARY KEY,
                            revision INTEGER NOT NULL,
                            authorization_json TEXT,
                            kill_latched INTEGER NOT NULL,
                            kill_latched_at TEXT,
                            kill_reason TEXT,
                            recovery_latched INTEGER NOT NULL DEFAULT 0,
                            recovery_required_at TEXT,
                            recovery_reason TEXT,
                            recovery_broker_order_id INTEGER
                        )
                        """
                    )
                    columns = {
                        row[1]
                        for row in connection.execute(
                            "PRAGMA table_info(live_safety_state)"
                        ).fetchall()
                    }
                    for name, declaration in (
                        ("recovery_latched", "INTEGER NOT NULL DEFAULT 0"),
                        ("recovery_required_at", "TEXT"),
                        ("recovery_reason", "TEXT"),
                        ("recovery_broker_order_id", "INTEGER"),
                    ):
                        if name not in columns:
                            connection.execute(
                                f"ALTER TABLE live_safety_state ADD COLUMN {name} {declaration}"
                            )
        except sqlite3.Error as error:
            raise LiveSafetyRepositoryError(
                "the Live safety store could not be prepared"
            ) from error

    def load(self) -> LiveSafetyRecord:
        try:
            with closing(connect_sqlite(self.path)) as connection:
                connection.isolation_level = None
                connection.execute("BEGIN")
                try:
                    row = connection.execute(
                        """SELECT revision, authorization_json, kill_latched,
                                  kill_latched_at, kill_reason, recovery_latched,
                                  recovery_required_at, recovery_reason,
                                  recovery_broker_order_id
                           FROM live_safety_state WHERE key = ?""",
                        (_KEY,),
                    ).fetchone()
                    if row is None:
                        return LiveSafetyRecord()
                    return _record_from_row(row)
                finally:
                    connection.execute("ROLLBACK")
        except LiveSafetyRepositoryError:
            raise
        except (
            sqlite3.Error,
            LiveSafetyError,
            ValueError,
            TypeError,
            KeyError,
            InvalidOperation,
            AttributeError,
        ) as error:
            raise LiveSafetyStoreUnreadable(
                "the Live safety record is unreadable; Live remains unavailable"
            ) from error

    @contextmanager
    def execution_lease(self) -> Iterator[LiveSafetyExecutionLease]:
        """Hold SQLite's write reservation until the broker submit is decided.

        Every durable safety update uses ``BEGIN IMMEDIATE`` in ``save``. This
        lease uses the same database lock, so a concurrent kill/revoke either
        commits first and is observed here, or waits until this submission has
        been linearized ahead of that update.
        """

        try:
            with closing(connect_sqlite(self.path)) as connection:
                connection.isolation_level = None
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute(
                        """SELECT revision, authorization_json, kill_latched,
                                  kill_latched_at, kill_reason, recovery_latched,
                                  recovery_required_at, recovery_reason,
                                  recovery_broker_order_id
                           FROM live_safety_state WHERE key = ?""",
                        (_KEY,),
                    ).fetchone()
                    record = LiveSafetyRecord() if row is None else _record_from_row(row)
                    lease = _SQLiteLiveSafetyExecutionLease(connection, record)
                    yield lease
                    connection.execute("COMMIT")
                except BaseException:
                    _rollback_quietly(connection)
                    raise
        except LiveSafetyRepositoryError:
            raise
        except sqlite3.Error as error:
            raise LiveSafetyRepositoryError(
                "the Live safety execution lease could not be acquired"
            ) from error
        except (LiveSafetyError, ValueError, TypeError, KeyError, InvalidOperation, AttributeError) as error:
            raise LiveSafetyStoreUnreadable(
                "the existing Live safety record is unreadable; execution remains unavailable"
            ) from error

    def save(self, *, expected_revision: int, replacement: LiveSafetyRecord) -> None:
        if replacement.revision != expected_revision + 1:
            raise LiveSafetyRepositoryError(
                "replacement revision must advance exactly once"
            )
        try:
            authorization_json = (
                None
                if replacement.authorization is None
                else json.dumps(
                    _authorization_to_dict(replacement.authorization),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            latch = replacement.kill_latch
            recovery = replacement.recovery_latch
            with closing(connect_sqlite(self.path)) as connection:
                connection.isolation_level = None
                connection.execute("BEGIN IMMEDIATE")
                try:
                    current = connection.execute(
                        """SELECT revision, authorization_json, kill_latched,
                                  kill_latched_at, kill_reason, recovery_latched,
                                  recovery_required_at, recovery_reason,
                                  recovery_broker_order_id
                           FROM live_safety_state WHERE key = ?""",
                        (_KEY,),
                    ).fetchone()
                    actual_revision = (
                        0 if current is None else _record_from_row(current).revision
                    )
                    if type(actual_revision) is not int or actual_revision != expected_revision:
                        raise LiveSafetyConflict(
                            "Live safety state changed; stale update was refused"
                        )
                    connection.execute(
                        """INSERT INTO live_safety_state(
                               key, revision, authorization_json, kill_latched,
                               kill_latched_at, kill_reason, recovery_latched,
                               recovery_required_at, recovery_reason,
                               recovery_broker_order_id
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(key) DO UPDATE SET
                               revision=excluded.revision,
                               authorization_json=excluded.authorization_json,
                               kill_latched=excluded.kill_latched,
                               kill_latched_at=excluded.kill_latched_at,
                               kill_reason=excluded.kill_reason,
                               recovery_latched=excluded.recovery_latched,
                               recovery_required_at=excluded.recovery_required_at,
                               recovery_reason=excluded.recovery_reason,
                               recovery_broker_order_id=excluded.recovery_broker_order_id""",
                        (
                            _KEY,
                            replacement.revision,
                            authorization_json,
                            int(latch.is_latched),
                            None if latch.latched_at is None else to_stored_text(latch.latched_at),
                            latch.reason,
                            int(recovery.is_required),
                            None
                            if recovery.required_at is None
                            else to_stored_text(recovery.required_at),
                            recovery.reason,
                            recovery.broker_order_id,
                        ),
                    )
                    connection.execute("COMMIT")
                except BaseException:
                    _rollback_quietly(connection)
                    raise
        except LiveSafetyRepositoryError:
            raise
        except sqlite3.Error as error:
            raise LiveSafetyRepositoryError(
                "the Live safety record could not be stored atomically"
            ) from error
        except (LiveSafetyError, ValueError, TypeError, KeyError, InvalidOperation, AttributeError) as error:
            raise LiveSafetyStoreUnreadable(
                "the existing Live safety record is unreadable; it was not overwritten"
            ) from error


def _authorization_to_dict(value: LiveOperatorAuthorization) -> dict[str, object]:
    fingerprint = value.expected_account_fingerprint
    limits = value.approved_canary_limits
    return {
        "authorization_id": value.authorization_id,
        "created_at": to_stored_text(value.created_at),
        "expires_at": to_stored_text(value.expires_at),
        "revoked_at": None if value.revoked_at is None else to_stored_text(value.revoked_at),
        "fingerprint": {
            "sha256": fingerprint.sha256,
            "masked_account": fingerprint.masked_account,
        },
        "approved_strategy_version_ids": list(value.approved_strategy_version_ids),
        "limits": {
            "capital_limit": str(limits.capital_limit),
            "max_order_notional": str(limits.max_order_notional),
            "max_daily_loss": str(limits.max_daily_loss),
            "max_positions": limits.max_positions,
            "max_open_orders": limits.max_open_orders,
            "allowed_symbols": list(limits.allowed_symbols),
            "allowed_strategy_versions": list(limits.allowed_strategy_versions),
        },
    }


def _authorization_from_json(value: str) -> LiveOperatorAuthorization:
    data = json.loads(value)
    fingerprint_data = data["fingerprint"]
    limits_data = data["limits"]
    approved_strategy_version_ids = _stored_string_array(
        data["approved_strategy_version_ids"], "approved_strategy_version_ids"
    )
    allowed_symbols = _stored_string_array(
        limits_data["allowed_symbols"], "allowed_symbols"
    )
    allowed_strategy_versions = _stored_string_array(
        limits_data["allowed_strategy_versions"], "allowed_strategy_versions"
    )
    fingerprint = LiveAccountFingerprint(
        sha256=fingerprint_data["sha256"],
        masked_account=fingerprint_data["masked_account"],
    )
    limits = LiveCanaryLimits(
        capital_limit=Decimal(limits_data["capital_limit"]),
        max_order_notional=Decimal(limits_data["max_order_notional"]),
        max_daily_loss=Decimal(limits_data["max_daily_loss"]),
        max_positions=limits_data["max_positions"],
        max_open_orders=limits_data["max_open_orders"],
        allowed_symbols=allowed_symbols,
        allowed_strategy_versions=allowed_strategy_versions,
    )
    return LiveOperatorAuthorization(
        authorization_id=data["authorization_id"],
        created_at=_timestamp(data["created_at"]),
        expires_at=_timestamp(data["expires_at"]),
        expected_account_fingerprint=fingerprint,
        approved_strategy_version_ids=approved_strategy_version_ids,
        approved_canary_limits=limits,
        revoked_at=None if data["revoked_at"] is None else _timestamp(data["revoked_at"]),
    )


def _stored_string_array(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise LiveSafetyStoreUnreadable(
            f"stored {name} must be a JSON array of strings"
        )
    return tuple(value)


def _record_from_row(row: sqlite3.Row | tuple[object, ...]) -> LiveSafetyRecord:
    revision, authorization_json, latched, latched_at, reason, *recovery_values = row
    if type(revision) is not int or revision < 1:
        raise LiveSafetyStoreUnreadable("invalid Live safety revision")
    if type(latched) is not int or latched not in (0, 1):
        raise LiveSafetyStoreUnreadable("invalid Live kill latch flag")
    if latched == 0 and (latched_at is not None or reason is not None):
        raise LiveSafetyStoreUnreadable("unlatched kill state contains latch details")
    if latched == 1 and (latched_at is None or reason is None):
        raise LiveSafetyStoreUnreadable("latched kill state is missing details")
    authorization = (
        None if authorization_json is None else _authorization_from_json(authorization_json)
    )
    kill_latch = (
        LiveKillLatch()
        if not latched
        else LiveKillLatch(_timestamp(latched_at), reason)
    )
    if recovery_values:
        if len(recovery_values) == 3:
            recovery_latched, recovery_at, recovery_reason = recovery_values
            recovery_order_id = None
        elif len(recovery_values) == 4:
            recovery_latched, recovery_at, recovery_reason, recovery_order_id = recovery_values
        else:
            raise LiveSafetyStoreUnreadable("invalid Live recovery latch shape")
        if type(recovery_latched) is not int or recovery_latched not in (0, 1):
            raise LiveSafetyStoreUnreadable("invalid Live recovery latch flag")
        if recovery_latched == 0 and (
            recovery_at is not None or recovery_reason is not None or recovery_order_id is not None
        ):
            raise LiveSafetyStoreUnreadable("unlatched recovery state contains latch details")
        if recovery_latched == 1 and (recovery_at is None or recovery_reason is None):
            raise LiveSafetyStoreUnreadable("latched recovery state is missing details")
        recovery_latch = (
            LiveRecoveryLatch()
            if not recovery_latched
            else LiveRecoveryLatch(_timestamp(recovery_at), recovery_reason, recovery_order_id)
        )
    else:
        recovery_latch = LiveRecoveryLatch()
    return LiveSafetyRecord(revision, authorization, kill_latch, recovery_latch)


def _timestamp(value: str) -> datetime:
    result = from_stored_text(value)
    if result.tzinfo is None:
        raise LiveSafetyStoreUnreadable("stored Live safety timestamp is naive")
    return result


def _rollback_quietly(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass


__all__ = ["SQLiteLiveSafetyRepository"]
