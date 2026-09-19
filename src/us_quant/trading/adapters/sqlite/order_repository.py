"""SQLite order store, behind ``OrderRepositoryPort``.

This is the Paper order journal relocated and retyped.  The table layout, the
query order and the stored text formats are unchanged from ``paper_order_journal``
-- a database written by any earlier build keeps opening, reading and
reconciling identically -- so the migration is a relocation plus a type
boundary, not a redesign.

What is new is the boundary.  The write path now speaks the domain's
``OrderIntent``, ``OrderEvent`` and ``ExecutionFill`` instead of the Paper DTOs,
and the broker's status text is translated once, through
``order_status_from_text``, rather than compared as strings by every reader.

The store keeps the *broker's* status text in its ``status`` column, so
reconciliation and the audit view see exactly what the channel reported.  The
domain status is derived on read.

Query surface kept deliberately beyond the port: the reconciliation rows and
summary, the audit view, the per-session rollup and the execution export.  Those
are the reads the runtime and the window perform, and they are the reason this
module may know the Paper DTOs -- a store that cannot report what it holds would
push those joins back into the application layer.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from us_quant.paper_order_models import (
    PaperOrderReconciliation,
    ReconciliationSummary,
    TERMINAL_ORDER_STATUSES,
)
from us_quant.sqlite_support import connect_sqlite
from us_quant.trading.adapters.clock import from_stored_text, now_iso
from us_quant.trading.adapters.order_status_mapping import (
    order_status_from_text,
)
from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
    OrderStatus,
    Side,
)

#: An unreadable stored timestamp reads as this instead of as "now".  An order
#: whose creation time cannot be trusted must never look freshly submitted.
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)

#: Columns added after the first order databases were written.
#:
#: ``CREATE TABLE IF NOT EXISTS`` never grows a table that already exists, so a
#: database created before ``idempotency_key`` was introduced rejects every
#: write that names it -- including the first order.  Adding the column in
#: place is the one non-destructive repair available: no row is rewritten, no
#: stored value changes, and a database that already has the column is left
#: exactly as it was.
_ADDED_COLUMNS = (
    ("paper_order_intent", "idempotency_key", "TEXT"),
)


def _decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


class SQLiteOrderRepository:
    """The order store: intents, broker events, executions and their views."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    # -- writes (the ``OrderRepositoryPort`` surface) --------------------

    def record_intent(
        self,
        intent: OrderIntent,
        *,
        broker_order_id: int,
        account_alias: str,
    ) -> None:
        """Make the order durable, with the broker id already reserved.

        This is called *before* anything is sent to the broker.  The row is what
        lets a submission whose outcome is unknown be recognised and named,
        so it must never move to after the send.
        """

        with closing(connect_sqlite(self.path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO paper_order_intent(
                        intent_id, session_id, strategy_version_id,
                        symbol, side, quantity, limit_price, reason,
                        generated_at, broker_order_id, account_alias,
                        idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent.order_id,
                        intent.session_id,
                        intent.strategy_version_id,
                        intent.execution_symbol,
                        intent.side.order_text,
                        intent.quantity,
                        str(intent.limit_price),
                        intent.reason,
                        intent.created_at.isoformat(),
                        broker_order_id,
                        account_alias,
                        intent.idempotency_key,
                    ),
                )

    def record_event(self, event: OrderEvent) -> None:
        with closing(connect_sqlite(self.path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO paper_order_update(
                        intent_id, broker_order_id, status, filled,
                        remaining, average_fill_price, last_fill_price,
                        message, observed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.order_id,
                        event.broker_order_id,
                        event.broker_status or event.status.value,
                        str(event.filled),
                        str(event.remaining),
                        _decimal_text(event.average_fill_price),
                        _decimal_text(event.last_fill_price),
                        event.message,
                        event.occurred_at.isoformat(),
                    ),
                )

    def record_fill(self, fill: ExecutionFill) -> bool:
        with closing(connect_sqlite(self.path)) as connection:
            with connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO paper_execution(
                        execution_id, intent_id, broker_order_id,
                        symbol, side, quantity, price, occurred_at,
                        recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fill.execution_id,
                        fill.order_id,
                        fill.broker_order_id,
                        fill.symbol,
                        fill.side.order_text,
                        str(fill.quantity),
                        str(fill.price),
                        fill.occurred_at.isoformat(),
                        now_iso(),
                    ),
                )
                return cursor.rowcount == 1

    # -- reads (the ``OrderRepositoryPort`` surface) ---------------------

    def status(self, order_id: str) -> OrderStatus | None:
        with closing(connect_sqlite(self.path)) as connection:
            row = connection.execute(
                """
                SELECT status
                FROM paper_order_update
                WHERE intent_id = ?
                ORDER BY update_id DESC
                LIMIT 1
                """,
                (order_id,),
            ).fetchone()
        if row is None:
            return None
        return order_status_from_text(str(row[0]))

    def intent(self, order_id: str) -> OrderIntent | None:
        with closing(connect_sqlite(self.path)) as connection:
            row = connection.execute(
                """
                SELECT intent_id, session_id, strategy_version_id,
                       symbol, side, quantity, limit_price, reason,
                       generated_at, idempotency_key
                FROM paper_order_intent
                WHERE intent_id = ?
                """,
                (order_id,),
            ).fetchone()
        return _intent_from_row(row)

    def broker_order_id(self, order_id: str) -> int | None:
        with closing(connect_sqlite(self.path)) as connection:
            row = connection.execute(
                """
                SELECT broker_order_id
                FROM paper_order_intent
                WHERE intent_id = ?
                """,
                (order_id,),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0])

    def fills(self, order_id: str) -> tuple[ExecutionFill, ...]:
        with closing(connect_sqlite(self.path)) as connection:
            rows = connection.execute(
                """
                SELECT execution_id, intent_id, broker_order_id,
                       symbol, side, quantity, price, occurred_at
                FROM paper_execution
                WHERE intent_id = ?
                ORDER BY execution_row_id ASC
                """,
                (order_id,),
            ).fetchall()
        return tuple(_fill_from_row(row) for row in rows)

    def intent_for_idempotency_key(
        self, idempotency_key: str
    ) -> OrderIntent | None:
        if not idempotency_key:
            return None
        with closing(connect_sqlite(self.path)) as connection:
            row = connection.execute(
                """
                SELECT intent_id, session_id, strategy_version_id,
                       symbol, side, quantity, limit_price, reason,
                       generated_at, idempotency_key
                FROM paper_order_intent
                WHERE idempotency_key = ?
                ORDER BY generated_at DESC
                LIMIT 1
                """,
                (idempotency_key,),
            ).fetchone()
        return _intent_from_row(row)

    def intent_for_broker_order(
        self, broker_order_id: int
    ) -> OrderIntent | None:
        with closing(connect_sqlite(self.path)) as connection:
            row = connection.execute(
                """
                SELECT intent_id, session_id, strategy_version_id,
                       symbol, side, quantity, limit_price, reason,
                       generated_at, idempotency_key
                FROM paper_order_intent
                WHERE broker_order_id = ?
                """,
                (broker_order_id,),
            ).fetchone()
        return _intent_from_row(row)

    def executed_quantity(self, order_id: str) -> Decimal:
        with closing(connect_sqlite(self.path)) as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(CAST(quantity AS REAL)), 0)
                FROM paper_execution
                WHERE intent_id = ?
                """,
                (order_id,),
            ).fetchone()
        return Decimal(str(row[0] if row is not None else "0"))

    def max_broker_order_id(self) -> int:
        """Highest broker order id ever recorded for this store.

        Used as the floor for the next order id so a Gateway restart can
        never regress the counter into reused ids (CR-4).
        """
        with closing(connect_sqlite(self.path)) as connection:
            row = connection.execute(
                "SELECT MAX(broker_order_id) FROM paper_order_intent"
            ).fetchone()
        value = row[0] if row is not None else None
        return int(value) if value is not None else 0

    # -- reconciliation views -------------------------------------------

    def reconciliation_rows(
        self,
        *,
        session_id: str | None = None,
        limit: int = 1000,
    ) -> tuple[PaperOrderReconciliation, ...]:
        return self._reconciliation_rows(session_id=session_id, limit=limit)

    def reconciliation_summary(
        self, session_id: str | None = None
    ) -> ReconciliationSummary:
        rows = self._reconciliation_rows(session_id=session_id)
        total = len(rows)
        reconciled = sum(row.reconciled for row in rows)
        terminal = sum(row.terminal for row in rows)
        terminal_unreconciled = sum(
            row.terminal and not row.reconciled for row in rows
        )
        return ReconciliationSummary(
            session_id=session_id or "",
            total=total,
            reconciled=reconciled,
            unreconciled=total - reconciled,
            terminal=terminal,
            terminal_unreconciled=terminal_unreconciled,
            nonterminal=total - terminal,
            observed_at=now_iso(),
        )

    def _reconciliation_rows(
        self,
        *,
        session_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[PaperOrderReconciliation, ...]:
        where = "WHERE i.session_id = ?" if session_id else ""
        limit_clause = "LIMIT ?" if limit is not None else ""
        parameters: tuple[object, ...] = ()
        if session_id:
            parameters += (session_id,)
        if limit is not None:
            parameters += (limit,)
        with closing(connect_sqlite(self.path)) as connection:
            rows = connection.execute(
                f"""
                SELECT i.intent_id, i.session_id, i.broker_order_id,
                       i.symbol, i.side, i.quantity,
                       u.status, u.filled, u.remaining,
                       COALESCE((
                           SELECT SUM(CAST(e.quantity AS REAL))
                           FROM paper_execution e
                           WHERE e.intent_id = i.intent_id
                       ), 0),
                       COALESCE(u.observed_at, i.generated_at)
                FROM paper_order_intent i
                LEFT JOIN paper_order_update u
                  ON u.update_id = (
                    SELECT MAX(u2.update_id)
                    FROM paper_order_update u2
                    WHERE u2.intent_id = i.intent_id
                  )
                {where}
                ORDER BY i.generated_at DESC
                {limit_clause}
                """,
                parameters,
            ).fetchall()
        results: list[PaperOrderReconciliation] = []
        for row in rows:
            status = str(row[6]) if row[6] is not None else None
            intended = Decimal(str(row[5]))
            reported_filled = Decimal(str(row[7] or "0"))
            reported_remaining = Decimal(
                str(row[8] if row[8] is not None else row[5])
            )
            executed = Decimal(str(row[9] or "0"))
            domain_status = (
                order_status_from_text(status) if status is not None else None
            )
            terminal = domain_status is not None and domain_status.is_terminal
            quantities_match = executed == reported_filled
            reconciled = terminal and quantities_match
            if status is None:
                reason = "等待券商首次订单状态"
            elif not terminal:
                reason = f"券商状态仍在途：{status}"
            elif not quantities_match:
                reason = (
                    "订单状态与逐笔成交未对齐："
                    f"reported={reported_filled}, executions={executed}"
                )
            elif (
                domain_status is OrderStatus.FILLED
                and executed != intended
            ):
                reconciled = False
                reason = (
                    "Filled 数量与订单数量不一致："
                    f"intended={intended}, executions={executed}"
                )
            else:
                reason = "券商状态与逐笔成交已对齐"
            results.append(
                PaperOrderReconciliation(
                    intent_id=str(row[0]),
                    session_id=str(row[1]),
                    broker_order_id=int(row[2]),
                    symbol=str(row[3]),
                    side=str(row[4]),
                    intended_quantity=intended,
                    latest_status=status,
                    reported_filled=reported_filled,
                    reported_remaining=reported_remaining,
                    executed_quantity=executed,
                    reconciled=reconciled,
                    terminal=terminal,
                    reason=reason,
                    observed_at=str(row[10]),
                )
            )
        return tuple(results)

    def pending_orders_for_session(
        self,
        session_id: str,
    ) -> tuple[OrderIntent, ...]:
        results: list[OrderIntent] = []
        for row in self.audit_rows(limit=1000):
            if row["session_id"] != session_id:
                continue
            status = (row.get("latest_status") or "").casefold()
            if status in TERMINAL_ORDER_STATUSES:
                continue
            results.append(_intent_from_audit_row(row))
        return tuple(results)

    def pending_orders_for_session_dicts(
        self,
        session_id: str,
    ) -> tuple[dict[str, object], ...]:
        results: list[dict[str, object]] = []
        for row in self.audit_rows(limit=1000):
            if row["session_id"] != session_id:
                continue
            status = (row.get("latest_status") or "").casefold()
            if status in TERMINAL_ORDER_STATUSES:
                continue
            results.append(
                {
                    "intent_id": row["intent_id"],
                    "session_id": row["session_id"],
                    "strategy_version_id": row[
                        "strategy_version_id"
                    ],
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "quantity": row["quantity"],
                    "limit_price": row["limit_price"],
                    "reason": row["reason"],
                    "generated_at": row["generated_at"],
                    "broker_order_id": row["broker_order_id"],
                    "latest_status": row.get("latest_status"),
                    "account_alias": row["account_alias"],
                    "idempotency_key": row.get(
                        "idempotency_key"
                    ),
                }
            )
        return tuple(results)

    def audit_rows(self, limit: int = 1000) -> tuple[dict, ...]:
        with closing(connect_sqlite(self.path)) as connection:
            rows = connection.execute(
                """
                SELECT i.intent_id, i.session_id,
                       i.strategy_version_id, i.symbol, i.side,
                       i.quantity, i.limit_price, i.reason,
                       i.generated_at, i.broker_order_id,
                       i.account_alias,
                       u.status, u.filled, u.remaining,
                       u.average_fill_price, u.last_fill_price,
                       u.message, u.observed_at
                FROM paper_order_intent i
                LEFT JOIN paper_order_update u
                  ON u.update_id = (
                    SELECT MAX(u2.update_id)
                    FROM paper_order_update u2
                    WHERE u2.intent_id = i.intent_id
                  )
                ORDER BY i.generated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(
            {
                "intent_id": row[0],
                "session_id": row[1],
                "strategy_version_id": row[2],
                "symbol": row[3],
                "side": row[4],
                "quantity": row[5],
                "limit_price": row[6],
                "reason": row[7],
                "generated_at": row[8],
                "broker_order_id": row[9],
                "account_alias": row[10],
                "latest_status": row[11],
                "filled": row[12],
                "remaining": row[13],
                "average_fill_price": row[14],
                "last_fill_price": row[15],
                "latest_message": row[16],
                "latest_update_at": row[17],
                "environment": "paper",
                "live_order": False,
            }
            for row in rows
        )

    def execution_rows(self, limit: int = 1000) -> tuple[dict, ...]:
        with closing(connect_sqlite(self.path)) as connection:
            rows = connection.execute(
                """
                SELECT e.execution_id, e.intent_id, i.session_id,
                       i.strategy_version_id, e.broker_order_id,
                       e.symbol, e.side, e.quantity, e.price,
                       e.occurred_at, e.recorded_at, i.account_alias
                FROM paper_execution e
                JOIN paper_order_intent i
                  ON i.intent_id = e.intent_id
                ORDER BY e.execution_row_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(
            {
                "execution_id": row[0],
                "intent_id": row[1],
                "session_id": row[2],
                "strategy_version_id": row[3],
                "broker_order_id": row[4],
                "symbol": row[5],
                "side": row[6],
                "quantity": row[7],
                "price": row[8],
                "occurred_at": row[9],
                "recorded_at": row[10],
                "account_alias": row[11],
                "environment": "paper",
                "live_order": False,
            }
            for row in rows
        )

    def sessions(
        self, *, limit: int = 50
    ) -> tuple[dict[str, object], ...]:
        """Per-session rollup: first intent, last activity, fills, status."""

        with closing(connect_sqlite(self.path)) as connection:
            rows = connection.execute(
                """
                SELECT
                    i.session_id,
                    MIN(i.generated_at) AS started_at,
                    MAX(COALESCE(u.observed_at, i.generated_at)) AS last_activity_at,
                    COUNT(*) AS intent_count,
                    SUM(CAST(COALESCE(u.filled, 0) AS REAL)) AS filled_quantity,
                    MAX(COALESCE(u.status, '')) AS latest_status
                FROM paper_order_intent i
                LEFT JOIN paper_order_update u
                  ON u.update_id = (
                    SELECT MAX(u2.update_id)
                    FROM paper_order_update u2
                    WHERE u2.intent_id = i.intent_id
                  )
                GROUP BY i.session_id
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(
            {
                "session_id": row[0],
                "started_at": row[1],
                "last_activity_at": row[2],
                "intent_count": row[3],
                "filled_quantity": Decimal(str(row[4] or 0)),
                "latest_status": row[5],
            }
            for row in rows
        )

    def _initialize(self) -> None:
        with closing(connect_sqlite(self.path)) as connection:
            with connection:
                self._add_missing_columns(connection)
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_order_intent(
                        intent_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        strategy_version_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        quantity INTEGER NOT NULL,
                        limit_price TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        generated_at TEXT NOT NULL,
                        broker_order_id INTEGER NOT NULL UNIQUE,
                        account_alias TEXT NOT NULL,
                        idempotency_key TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_order_update(
                        update_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        intent_id TEXT NOT NULL,
                        broker_order_id INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        filled TEXT NOT NULL,
                        remaining TEXT NOT NULL,
                        average_fill_price TEXT,
                        last_fill_price TEXT,
                        message TEXT NOT NULL,
                        observed_at TEXT NOT NULL,
                        FOREIGN KEY(intent_id)
                            REFERENCES paper_order_intent(intent_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_execution(
                        execution_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        execution_id TEXT NOT NULL UNIQUE,
                        intent_id TEXT NOT NULL,
                        broker_order_id INTEGER NOT NULL,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        quantity TEXT NOT NULL,
                        price TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        recorded_at TEXT NOT NULL,
                        FOREIGN KEY(intent_id)
                            REFERENCES paper_order_intent(intent_id)
                    )
                    """
                )

    @staticmethod
    def _add_missing_columns(connection: object) -> None:
        """Grow an older database into the current layout, in place.

        Only ever an ``ADD COLUMN``, and only when the column is genuinely
        absent: no row is rewritten and an up-to-date database takes no write
        at all.  Done before the ``CREATE TABLE IF NOT EXISTS`` statements so
        the very first query of a legacy database succeeds.
        """

        for table, column, kind in _ADDED_COLUMNS:
            present = {
                row[1]
                for row in connection.execute(  # type: ignore[attr-defined]
                    f"PRAGMA table_info({table})"
                )
            }
            if present and column not in present:
                connection.execute(  # type: ignore[attr-defined]
                    f"ALTER TABLE {table} ADD COLUMN {column} {kind}"
                )


def _intent_from_row(row: object) -> OrderIntent | None:
    """Rebuild a domain intent from a stored row.

    Two fields are derived rather than read.  ``client_order_id`` is the
    deterministic spelling of the order id (``uq-<order id>``), and the signal
    symbol is the execution symbol: the store has always held exactly one symbol
    column, and the two have never differed for an order this system sent.  The
    exposure multiplier is not stored either -- the journal never recorded it --
    so a reconstructed intent carries the neutral default.
    """

    if row is None:
        return None
    return _intent_from_columns(
        order_id=str(row[0]),
        session_id=str(row[1]),
        strategy_version_id=str(row[2]),
        symbol=str(row[3]),
        side=str(row[4]),
        quantity=int(row[5]),
        limit_price=str(row[6]),
        reason=str(row[7]),
        generated_at=str(row[8]),
        idempotency_key=row[9],
    )


def _intent_from_audit_row(row: dict) -> OrderIntent:
    return _intent_from_columns(
        order_id=str(row["intent_id"]),
        session_id=str(row["session_id"]),
        strategy_version_id=str(row["strategy_version_id"]),
        symbol=str(row["symbol"]),
        side=str(row["side"]),
        quantity=int(row["quantity"]),
        limit_price=str(row["limit_price"]),
        reason=str(row["reason"]),
        generated_at=str(row["generated_at"]),
        idempotency_key=row.get("idempotency_key"),
    )


def _intent_from_columns(
    *,
    order_id: str,
    session_id: str,
    strategy_version_id: str,
    symbol: str,
    side: str,
    quantity: int,
    limit_price: str,
    reason: str,
    generated_at: str,
    idempotency_key: object,
) -> OrderIntent:
    return OrderIntent(
        order_id=order_id,
        client_order_id=f"uq-{order_id}",
        session_id=session_id,
        strategy_version_id=strategy_version_id,
        signal_symbol=symbol,
        execution_symbol=symbol,
        side=Side.from_order_text(side),
        quantity=quantity,
        limit_price=Decimal(limit_price),
        reason=reason,
        idempotency_key=(
            str(idempotency_key) if idempotency_key is not None else ""
        ),
        created_at=from_stored_text(generated_at) or _EPOCH_UTC,
    )


def _fill_from_row(row: object) -> ExecutionFill:
    return ExecutionFill(
        execution_id=str(row[0]),
        order_id=str(row[1]),
        broker_order_id=int(row[2]),
        symbol=str(row[3]),
        side=Side.from_order_text(str(row[4])),
        quantity=Decimal(str(row[5])),
        price=Decimal(str(row[6])),
        occurred_at=from_stored_text(str(row[7])) or _EPOCH_UTC,
    )