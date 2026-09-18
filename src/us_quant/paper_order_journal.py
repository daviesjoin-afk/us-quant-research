"""SQLite persistence for Paper orders.

Owns the local order journal: intents, broker updates, executions and the
reconciliation views derived from them. The table layout, the query order and
the stored text formats are unchanged from the module this was extracted from,
so databases written by earlier builds keep working.

Depends on the pure DTO layer only, never on the broker adapter.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from us_quant.paper_order_models import (
    PaperExecution,
    PaperOrderIntent,
    PaperOrderReconciliation,
    PaperOrderUpdate,
    ReconciliationSummary,
    TERMINAL_ORDER_STATUSES,
)
from us_quant.sqlite_support import connect_sqlite


def _decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperOrderJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def record_intent(
        self,
        intent: PaperOrderIntent,
        *,
        broker_order_id: int,
        account_alias: str,
    ) -> None:
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
                        intent.intent_id,
                        intent.session_id,
                        intent.strategy_version_id,
                        intent.symbol,
                        intent.side,
                        intent.quantity,
                        str(intent.limit_price),
                        intent.reason,
                        intent.generated_at,
                        broker_order_id,
                        account_alias,
                        intent.idempotency_key,
                    ),
                )

    def record_update(self, update: PaperOrderUpdate) -> None:
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
                        update.intent_id,
                        update.broker_order_id,
                        update.status,
                        str(update.filled),
                        str(update.remaining),
                        _decimal_text(update.average_fill_price),
                        _decimal_text(update.last_fill_price),
                        update.message,
                        update.observed_at,
                    ),
                )

    def record_execution(self, execution: PaperExecution) -> bool:
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
                        execution.execution_id,
                        execution.intent_id,
                        execution.broker_order_id,
                        execution.symbol,
                        execution.side,
                        str(execution.quantity),
                        str(execution.price),
                        execution.occurred_at,
                        _now_iso(),
                    ),
                )
                return cursor.rowcount == 1

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

    def intent_for_broker_order(
        self, broker_order_id: int
    ) -> PaperOrderIntent | None:
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
        if row is None:
            return None
        return PaperOrderIntent(
            intent_id=str(row[0]),
            session_id=str(row[1]),
            strategy_version_id=str(row[2]),
            symbol=str(row[3]),
            side=str(row[4]),
            quantity=int(row[5]),
            limit_price=Decimal(str(row[6])),
            reason=str(row[7]),
            generated_at=str(row[8]),
            idempotency_key=row[9] if len(row) > 9 else None,
        )

    def intent_for_idempotency_key(
        self, idempotency_key: str
    ) -> PaperOrderIntent | None:
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
        if row is None:
            return None
        return PaperOrderIntent(
            intent_id=str(row[0]),
            session_id=str(row[1]),
            strategy_version_id=str(row[2]),
            symbol=str(row[3]),
            side=str(row[4]),
            quantity=int(row[5]),
            limit_price=Decimal(str(row[6])),
            reason=str(row[7]),
            generated_at=str(row[8]),
            idempotency_key=row[9] if len(row) > 9 else None,
        )

    def executed_quantity(self, intent_id: str) -> Decimal:
        with closing(connect_sqlite(self.path)) as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(CAST(quantity AS REAL)), 0)
                FROM paper_execution
                WHERE intent_id = ?
                """,
                (intent_id,),
            ).fetchone()
        return Decimal(str(row[0] if row is not None else "0"))

    def max_broker_order_id(self) -> int:
        """Highest broker order id ever recorded for this journal.

        Used as the floor for the next order id so a Gateway restart can
        never regress the counter into reused ids (CR-4).
        """
        with closing(connect_sqlite(self.path)) as connection:
            row = connection.execute(
                "SELECT MAX(broker_order_id) FROM paper_order_intent"
            ).fetchone()
        value = row[0] if row is not None else None
        return int(value) if value is not None else 0

    def reconciliation_rows(
        self,
        *,
        session_id: str | None = None,
        limit: int = 1000,
    ) -> tuple[PaperOrderReconciliation, ...]:
        return self._reconciliation_rows(
            session_id=session_id, limit=limit
        )

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
            observed_at=_now_iso(),
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
            terminal = (
                status is not None
                and status.casefold() in TERMINAL_ORDER_STATUSES
            )
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
                status.casefold() == "filled"
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
    ) -> tuple[PaperOrderIntent, ...]:
        rows = self.audit_rows(limit=1000)
        results: list[PaperOrderIntent] = []
        for row in rows:
            if row["session_id"] != session_id:
                continue
            status = (row.get("latest_status") or "").casefold()
            if status in TERMINAL_ORDER_STATUSES:
                continue
            results.append(
                PaperOrderIntent(
                    intent_id=str(row["intent_id"]),
                    session_id=str(row["session_id"]),
                    strategy_version_id=str(
                        row["strategy_version_id"]
                    ),
                    symbol=str(row["symbol"]),
                    side=str(row["side"]),
                    quantity=int(row["quantity"]),
                    limit_price=Decimal(str(row["limit_price"])),
                    reason=str(row["reason"]),
                    generated_at=str(row["generated_at"]),
                    idempotency_key=row.get("idempotency_key"),
                )
            )
        return tuple(results)

    def pending_orders_for_session_dicts(
        self,
        session_id: str,
    ) -> tuple[dict[str, object], ...]:
        rows = self.audit_rows(limit=1000)
        results: list[dict[str, object]] = []
        for row in rows:
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

    def sessions(
        self, *, limit: int = 50
    ) -> tuple[dict[str, object], ...]:
        """Per-session rollup: first intent, last activity, fills, status.

        Moved here verbatim from the broker adapter, which used to run this
        query against a ``self.path`` it never had.
        """

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
