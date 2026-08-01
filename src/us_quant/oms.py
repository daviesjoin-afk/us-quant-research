from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Generator, Iterable

from us_quant.domain import OrderEvent, OrderIntent, OrderStatus


ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {
        OrderStatus.RISK_APPROVED,
        OrderStatus.RISK_REJECTED,
    },
    OrderStatus.RISK_APPROVED: {
        OrderStatus.SUBMITTING,
        OrderStatus.CANCELED,
    },
    OrderStatus.SUBMITTING: {
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.BROKER_REJECTED,
        OrderStatus.UNKNOWN,
    },
    OrderStatus.ACKNOWLEDGED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.BROKER_REJECTED,
        OrderStatus.UNKNOWN,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.UNKNOWN,
    },
    OrderStatus.CANCEL_PENDING: {
        OrderStatus.CANCELED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.UNKNOWN,
    },
    OrderStatus.UNKNOWN: {
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.BROKER_REJECTED,
    },
    OrderStatus.RISK_REJECTED: set(),
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELED: set(),
    OrderStatus.BROKER_REJECTED: set(),
}


class InvalidOrderTransition(ValueError):
    pass


def validate_transition(
    current: OrderStatus, requested: OrderStatus
) -> None:
    if requested not in ALLOWED_TRANSITIONS[current]:
        raise InvalidOrderTransition(
            f"invalid order transition: {current} -> {requested}"
        )


class SQLiteOrderJournal:
    """Append-only event journal. It is the local audit trail, not the broker."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS order_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_order_events_order_id
                ON order_events(order_id, event_id)
                """
            )

    def append(self, event: OrderEvent) -> bool:
        payload_json = json.dumps(
            event.payload, sort_keys=True, separators=(",", ":")
        )
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO order_events (
                        order_id,
                        status,
                        idempotency_key,
                        occurred_at,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event.order_id,
                        event.status.value,
                        event.idempotency_key,
                        event.occurred_at.isoformat(),
                        payload_json,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def contains_idempotency_key(self, idempotency_key: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM order_events
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        return row is not None

    def events_for(self, order_id: str) -> list[OrderEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT order_id, status, idempotency_key, occurred_at,
                       payload_json
                FROM order_events
                WHERE order_id = ?
                ORDER BY event_id
                """,
                (order_id,),
            ).fetchall()
        return [
            OrderEvent(
                order_id=row["order_id"],
                status=OrderStatus(row["status"]),
                idempotency_key=row["idempotency_key"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def current_status(self, order_id: str) -> OrderStatus | None:
        events = self.events_for(order_id)
        return events[-1].status if events else None

    def all_events(self) -> Iterable[OrderEvent]:
        with self._connection() as connection:
            order_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT order_id FROM order_events"
                ).fetchall()
            ]
        for order_id in order_ids:
            yield from self.events_for(order_id)


class OrderManager:
    def __init__(self, journal: SQLiteOrderJournal) -> None:
        self._journal = journal

    def register_intent(self, intent: OrderIntent) -> bool:
        payload = asdict(intent)
        payload["side"] = intent.side.value
        payload["estimated_price"] = str(intent.estimated_price)
        payload["exposure_multiplier"] = str(intent.exposure_multiplier)
        payload["created_at"] = intent.created_at.isoformat()
        return self._journal.append(
            OrderEvent(
                order_id=intent.order_id,
                status=OrderStatus.CREATED,
                idempotency_key=f"{intent.order_id}:created",
                occurred_at=intent.created_at,
                payload=payload,
            )
        )

    def transition(
        self,
        *,
        order_id: str,
        status: OrderStatus,
        idempotency_key: str,
        payload: dict | None = None,
    ) -> bool:
        if self._journal.contains_idempotency_key(idempotency_key):
            return False
        current = self._journal.current_status(order_id)
        if current is None:
            raise KeyError(f"unknown order: {order_id}")
        validate_transition(current, status)
        return self._journal.append(
            OrderEvent(
                order_id=order_id,
                status=status,
                idempotency_key=idempotency_key,
                payload=payload or {},
            )
        )
