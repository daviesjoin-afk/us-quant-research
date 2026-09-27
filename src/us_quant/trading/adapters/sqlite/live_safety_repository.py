"""SQLite persistence for the Stage 4-A Live safety record."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3

from us_quant.sqlite_support import connect_sqlite
from us_quant.trading.adapters.clock import from_stored_text, to_stored_text
from us_quant.trading.domain.live_safety import (
    LiveAccountFingerprint,
    LiveCanaryLimits,
    LiveKillLatch,
    LiveOperatorAuthorization,
    LiveSafetyError,
    LiveSafetyRecord,
)
from us_quant.trading.ports.live_safety_repository import (
    LiveSafetyConflict,
    LiveSafetyRepositoryError,
    LiveSafetyStoreUnreadable,
)

_KEY = "live"


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
                            kill_reason TEXT
                        )
                        """
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
                                  kill_latched_at, kill_reason
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
            with closing(connect_sqlite(self.path)) as connection:
                connection.isolation_level = None
                connection.execute("BEGIN IMMEDIATE")
                try:
                    current = connection.execute(
                        """SELECT revision, authorization_json, kill_latched,
                                  kill_latched_at, kill_reason
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
                               kill_latched_at, kill_reason
                           ) VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(key) DO UPDATE SET
                               revision=excluded.revision,
                               authorization_json=excluded.authorization_json,
                               kill_latched=excluded.kill_latched,
                               kill_latched_at=excluded.kill_latched_at,
                               kill_reason=excluded.kill_reason""",
                        (
                            _KEY,
                            replacement.revision,
                            authorization_json,
                            int(latch.is_latched),
                            None if latch.latched_at is None else to_stored_text(latch.latched_at),
                            latch.reason,
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
        allowed_symbols=tuple(limits_data["allowed_symbols"]),
        allowed_strategy_versions=tuple(limits_data["allowed_strategy_versions"]),
    )
    return LiveOperatorAuthorization(
        authorization_id=data["authorization_id"],
        created_at=_timestamp(data["created_at"]),
        expires_at=_timestamp(data["expires_at"]),
        expected_account_fingerprint=fingerprint,
        approved_strategy_version_ids=tuple(data["approved_strategy_version_ids"]),
        approved_canary_limits=limits,
        revoked_at=None if data["revoked_at"] is None else _timestamp(data["revoked_at"]),
    )


def _record_from_row(row: sqlite3.Row | tuple[object, ...]) -> LiveSafetyRecord:
    revision, authorization_json, latched, latched_at, reason = row
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
    return LiveSafetyRecord(revision, authorization, kill_latch)


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
