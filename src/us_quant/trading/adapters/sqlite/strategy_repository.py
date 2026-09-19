"""SQLite strategy repository: the storage half of strategy governance.

This adapter is the only module that knows both the domain and the on-disk
shape, and it owns every conversion between them.  Three properties are
deliberate and load-bearing.

**The schema is frozen.**  The four tables, their columns, their types and
their uniqueness constraints are byte-for-byte the ones ``StrategyRegistry``
created, and the ``CREATE`` text below therefore carries its historical
indentation.  A user's existing ``strategies.sqlite3`` must open directly:
there is no drop, no delete, no rename, no rebuild, and no version of this
module that "tidies up" the schema.  ``tests/test_trading_strategy_repository``
proves it by building a database from the retired DDL literal and comparing
both the recorded schema text and the structure against a freshly created one.

**Reads fail closed.**  A status that is not a ``StrategyStatus``, a ``mode``
that is not a ``StrategyMode``, a non-decimal risk budget, malformed
``parameters_json``, a ``gate_passed`` that is not 0/1, or a naive timestamp
raises ``StrategyRepositoryError``.  Nothing is repaired with a default: a
version whose governance state had to be guessed must not become runnable.

**Writes are atomic.**  A version is four rows (definition, version,
deployment, audit) and a transition is two (deployment, audit).  Each group
shares one transaction, so a failed audit insert leaves the deployment exactly
where it was rather than moving a version's status with no record of why.

One inherited behaviour is preserved on purpose and is now a **contract**, not
a storage accident.  ``strategy_definition`` is keyed on ``strategy_id`` and
written with ``INSERT OR IGNORE``, so a family's name and description come from
its *first* registered version and later versions read that text back.  The
seeded catalogue has one family whose second version carries different
description text, which is how the recorded baseline shows it.  "Fixing" this
by upserting the definition would silently rewrite what the operator sees for a
version nobody edited.

Because it is a contract, it holds on every read path: ``StrategyApplication``
returns what this store holds -- including from ``register`` itself -- so the
return value of a write and a later ``get_version`` cannot disagree.  The
in-memory repository used by the application tests mirrors the same rule for
exactly that reason.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from us_quant.sqlite_support import connect_sqlite
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
    canonical_parameters_json,
)
from us_quant.trading.ports.strategy_repository import (
    StrategyAuditEvent,
    StrategyRepositoryConflict,
    StrategyRepositoryError,
    StrategyRepositoryNotFound,
)

#: The frozen schema, reproduced character for character from
#: ``StrategyRegistry._initialize``.  The indentation looks arbitrary because
#: it is: SQLite records the statement text it was given, so matching it keeps
#: an upgraded database indistinguishable from a freshly created one.
_SCHEMA = """
                    CREATE TABLE IF NOT EXISTS strategy_definition (
                        strategy_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS strategy_version (
                        version_id TEXT PRIMARY KEY,
                        strategy_id TEXT NOT NULL,
                        semver TEXT NOT NULL,
                        parameters_json TEXT NOT NULL,
                        parameter_hash TEXT NOT NULL,
                        universe_hash TEXT NOT NULL,
                        code_hash TEXT NOT NULL,
                        risk_budget_pct TEXT NOT NULL,
                        gate_passed INTEGER NOT NULL,
                        gate_reason TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE (strategy_id, semver),
                        FOREIGN KEY (strategy_id)
                            REFERENCES strategy_definition(strategy_id)
                    );
                    CREATE TABLE IF NOT EXISTS strategy_deployment (
                        deployment_id TEXT PRIMARY KEY,
                        version_id TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY (version_id)
                            REFERENCES strategy_version(version_id)
                    );
                    CREATE TABLE IF NOT EXISTS strategy_audit (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        strategy_id TEXT NOT NULL,
                        version_id TEXT NOT NULL,
                        event TEXT NOT NULL,
                        detail TEXT NOT NULL,
                        occurred_at TEXT NOT NULL
                    );
                    """

#: ``created_at`` lives on ``strategy_version``; ``updated_at`` only exists on
#: ``strategy_deployment``.  A version's "last changed" time is a deployment
#: fact, so it is read from the join -- the recorded history of this schema.
_VERSION_QUERY = """
    SELECT d.strategy_id, d.name, d.description,
           v.version_id, v.semver,
           p.status, p.mode,
           v.parameters_json, v.parameter_hash,
           v.universe_hash, v.code_hash, v.risk_budget_pct,
           v.gate_passed, v.gate_reason,
           v.created_at, p.updated_at
    FROM strategy_definition d
    JOIN strategy_version v ON v.strategy_id = d.strategy_id
    JOIN strategy_deployment p ON p.version_id = v.version_id
"""


class SQLiteStrategyRepository:
    """A ``StrategyRepositoryPort`` backed by one SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    # -- reads ----------------------------------------------------------

    def list_versions(self) -> tuple[StrategyVersion, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(_VERSION_QUERY).fetchall()
        return tuple(_row_to_version(row) for row in rows)

    def get_version(self, version_id: str) -> StrategyVersion:
        with closing(self._connect()) as connection:
            row = connection.execute(
                _VERSION_QUERY + " WHERE v.version_id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise StrategyRepositoryNotFound(version_id)
        return _row_to_version(row)

    # -- writes ---------------------------------------------------------

    def insert_version(
        self,
        version: StrategyVersion,
        *,
        audit: StrategyAuditEvent,
    ) -> None:
        parameters_json = canonical_parameters_json(version.parameters)
        try:
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO strategy_definition (
                            strategy_id, name, description, created_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            version.strategy_id,
                            version.name,
                            version.description,
                            _isoformat(version.created_at, "created_at"),
                        ),
                    )
                    existing = connection.execute(
                        """
                        SELECT 1 FROM strategy_version
                        WHERE strategy_id = ? AND semver = ?
                        """,
                        (version.strategy_id, version.semver),
                    ).fetchone()
                    if existing:
                        raise StrategyRepositoryConflict(
                            f"{version.strategy_id} {version.semver} "
                            "already exists"
                        )
                    connection.execute(
                        """
                        INSERT INTO strategy_version (
                            version_id, strategy_id, semver,
                            parameters_json, parameter_hash,
                            universe_hash, code_hash, risk_budget_pct,
                            gate_passed, gate_reason, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            version.version_id,
                            version.strategy_id,
                            version.semver,
                            parameters_json,
                            version.parameter_hash,
                            version.universe_hash,
                            version.code_hash,
                            str(version.risk_budget_pct),
                            int(version.gate_passed),
                            version.gate_reason,
                            _isoformat(version.created_at, "created_at"),
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO strategy_deployment (
                            deployment_id, version_id, status, mode,
                            updated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid4()),
                            version.version_id,
                            version.status.value,
                            version.mode.value,
                            _isoformat(version.updated_at, "updated_at"),
                        ),
                    )
                    self._insert_audit(connection, audit)
        except StrategyRepositoryError:
            raise
        except sqlite3.IntegrityError as error:
            # Raised by the constraint the pre-check exists to describe, or by
            # a foreign key.  Either way the write is refused, not retried.
            raise StrategyRepositoryConflict(str(error)) from error
        except sqlite3.Error as error:
            raise StrategyRepositoryError(
                f"cannot store strategy version: {error}"
            ) from error

    def update_deployment(
        self,
        *,
        version_id: str,
        status: StrategyStatus,
        mode: StrategyMode,
        updated_at: datetime,
        audit: StrategyAuditEvent,
    ) -> None:
        try:
            with closing(self._connect()) as connection:
                with connection:
                    cursor = connection.execute(
                        """
                        UPDATE strategy_deployment
                        SET status = ?, mode = ?, updated_at = ?
                        WHERE version_id = ?
                        """,
                        (
                            status.value,
                            mode.value,
                            _isoformat(updated_at, "updated_at"),
                            version_id,
                        ),
                    )
                    if cursor.rowcount == 0:
                        raise StrategyRepositoryNotFound(version_id)
                    self._insert_audit(connection, audit)
        except StrategyRepositoryError:
            raise
        except sqlite3.Error as error:
            raise StrategyRepositoryError(
                f"cannot update strategy deployment: {error}"
            ) from error

    # -- internals ------------------------------------------------------

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.executescript(_SCHEMA)

    @staticmethod
    def _insert_audit(
        connection: sqlite3.Connection,
        audit: StrategyAuditEvent,
    ) -> None:
        connection.execute(
            """
            INSERT INTO strategy_audit (
                strategy_id, version_id, event, detail, occurred_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                audit.strategy_id,
                audit.version_id,
                audit.event,
                audit.detail,
                _isoformat(audit.occurred_at, "audit occurred_at"),
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        try:
            return connect_sqlite(self.path)
        except sqlite3.Error as error:
            raise StrategyRepositoryError(
                f"cannot open strategy store: {error}"
            ) from error


def _row_to_version(row: tuple[Any, ...]) -> StrategyVersion:
    """Convert one joined row, refusing anything it cannot fully believe."""

    try:
        (
            strategy_id,
            name,
            description,
            version_id,
            semver,
            status,
            mode,
            parameters_json,
            parameter_hash,
            universe_hash,
            code_hash,
            risk_budget_pct,
            gate_passed,
            gate_reason,
            created_at,
            updated_at,
        ) = row
    except ValueError as error:
        raise StrategyRepositoryError(
            "strategy row has an unexpected column count"
        ) from error

    parameters = _parameters(parameters_json, version_id)
    return StrategyVersion(
        definition=StrategyDefinition(
            strategy_id=str(strategy_id),
            name=str(name),
            description=str(description),
        ),
        identity=StrategyIdentity(
            strategy_id=str(strategy_id),
            version_id=str(version_id),
            parameter_hash=str(parameter_hash),
        ),
        semver=str(semver),
        status=_status(status, version_id),
        mode=_mode(mode, version_id),
        parameters=parameters,
        universe_hash=str(universe_hash),
        code_hash=str(code_hash),
        risk_budget_pct=_risk_budget(risk_budget_pct, version_id),
        gate_passed=_gate_passed(gate_passed, version_id),
        gate_reason=str(gate_reason),
        created_at=_timestamp(created_at, "created_at", version_id),
        updated_at=_timestamp(updated_at, "updated_at", version_id),
    )


def _status(value: Any, version_id: Any) -> StrategyStatus:
    try:
        return StrategyStatus(value)
    except ValueError as error:
        raise StrategyRepositoryError(
            f"stored status {value!r} is not a known strategy status "
            f"(version {version_id})"
        ) from error


def _mode(value: Any, version_id: Any) -> StrategyMode:
    try:
        return StrategyMode(value)
    except ValueError as error:
        raise StrategyRepositoryError(
            f"stored mode {value!r} is not a known strategy mode "
            f"(version {version_id})"
        ) from error


def _risk_budget(value: Any, version_id: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise StrategyRepositoryError(
            f"stored risk_budget_pct {value!r} is not a decimal "
            f"(version {version_id})"
        ) from error


def _gate_passed(value: Any, version_id: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StrategyRepositoryError(
            f"stored gate_passed {value!r} is not 0 or 1 "
            f"(version {version_id})"
        )
    if value not in (0, 1):
        raise StrategyRepositoryError(
            f"stored gate_passed {value!r} is not 0 or 1 "
            f"(version {version_id})"
        )
    return bool(value)


def _parameters(value: Any, version_id: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        raise StrategyRepositoryError(
            f"stored parameters_json is not text (version {version_id})"
        )
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as error:
        raise StrategyRepositoryError(
            f"stored parameters_json is not valid JSON "
            f"(version {version_id})"
        ) from error
    if not isinstance(parsed, dict):
        raise StrategyRepositoryError(
            f"stored parameters_json is not an object "
            f"(version {version_id})"
        )
    return parsed


def _timestamp(value: Any, name: str, version_id: Any) -> datetime:
    if not isinstance(value, str):
        raise StrategyRepositoryError(
            f"stored {name} is not text (version {version_id})"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise StrategyRepositoryError(
            f"stored {name} is not a valid ISO timestamp "
            f"(version {version_id})"
        ) from error
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise StrategyRepositoryError(
            f"stored {name} is timezone-naive (version {version_id}); "
            "refusing to assume a zone for it"
        )
    return parsed


def _isoformat(value: datetime, name: str) -> str:
    if not isinstance(value, datetime):
        raise StrategyRepositoryError(f"{name} must be a datetime")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise StrategyRepositoryError(
            f"{name} must be timezone-aware; refusing to write a naive "
            "timestamp into the governance audit trail"
        )
    return value.isoformat()


__all__ = ["SQLiteStrategyRepository"]
