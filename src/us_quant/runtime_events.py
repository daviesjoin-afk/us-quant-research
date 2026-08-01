from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from us_quant.sqlite_support import connect_sqlite
from us_quant.redaction import redact_text


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_id: int
    occurred_at: str
    severity: str
    component: str
    code: str
    message: str
    resolved: bool


class RuntimeEventStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def add(
        self,
        *,
        severity: str,
        component: str,
        code: str,
        message: str,
    ) -> RuntimeEvent:
        if severity not in {"info", "warning", "error"}:
            raise ValueError("unsupported event severity")
        safe_message = redact_text(message)
        occurred_at = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    """
                    INSERT INTO runtime_event (
                        occurred_at, severity, component,
                        code, message, resolved
                    ) VALUES (?, ?, ?, ?, ?, 0)
                    """,
                    (
                        occurred_at,
                        severity,
                        component,
                        code,
                        safe_message,
                    ),
                )
                event_id = int(cursor.lastrowid)
        return RuntimeEvent(
            event_id=event_id,
            occurred_at=occurred_at,
            severity=severity,
            component=component,
            code=code,
            message=safe_message,
            resolved=False,
        )

    def list_recent(self, limit: int = 500) -> tuple[RuntimeEvent, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT event_id, occurred_at, severity,
                       component, code, message, resolved
                FROM runtime_event
                ORDER BY event_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(
            RuntimeEvent(
                event_id=int(row[0]),
                occurred_at=row[1],
                severity=row[2],
                component=row[3],
                code=row[4],
                message=row[5],
                resolved=bool(row[6]),
            )
            for row in rows
        )

    def resolve(self, event_id: int) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE runtime_event SET resolved = 1
                    WHERE event_id = ?
                    """,
                    (event_id,),
                )

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_event (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        occurred_at TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        component TEXT NOT NULL,
                        code TEXT NOT NULL,
                        message TEXT NOT NULL,
                        resolved INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path)
