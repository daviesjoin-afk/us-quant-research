from __future__ import annotations

from collections import deque
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any
from uuid import uuid4

from us_quant.ibkr import (
    IBKRClientConnectError,
    IBKRConnectionConfig,
    connect_ibkr_client,
)
from us_quant.ibkr_readonly import (
    INFORMATIONAL_ERROR_CODES,
    mask_account_id,
)
from us_quant.extended_hours import paper_order_routing
from us_quant.sqlite_support import connect_sqlite


class IBKRPaperOrderError(RuntimeError):
    pass


class IBKRPaperOrderUncertainError(IBKRPaperOrderError):
    """Submission may have reached the broker and requires reconciliation."""

    def __init__(
        self, message: str, *, intent_id: str, broker_order_id: int
    ) -> None:
        super().__init__(message)
        self.intent_id = intent_id
        self.broker_order_id = broker_order_id


@dataclass(frozen=True, slots=True)
class PaperOrderIntent:
    intent_id: str
    session_id: str
    strategy_version_id: str
    symbol: str
    side: str
    quantity: int
    limit_price: Decimal
    reason: str
    generated_at: str
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class PaperOrderUpdate:
    intent_id: str
    broker_order_id: int
    status: str
    filled: Decimal
    remaining: Decimal
    average_fill_price: Decimal | None
    last_fill_price: Decimal | None
    message: str
    observed_at: str


@dataclass(frozen=True, slots=True)
class PaperExecution:
    intent_id: str
    broker_order_id: int
    execution_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    occurred_at: str


@dataclass(frozen=True, slots=True)
class PaperOrderConnection:
    connected: bool
    account_alias: str
    server_version: int
    connection_time: str
    next_order_id: int
    open_broker_orders: int = 0
    unreconciled_local_orders: int = 0


@dataclass(frozen=True, slots=True)
class PaperBrokerPosition:
    symbol: str
    quantity: Decimal
    average_cost: Decimal


@dataclass(frozen=True, slots=True)
class PaperBrokerState:
    account_alias: str
    net_liquidation: Decimal | None
    cash: Decimal | None
    available_funds: Decimal | None
    buying_power: Decimal | None
    daily_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None
    positions: tuple[PaperBrokerPosition, ...]
    observed_at: str


@dataclass(frozen=True, slots=True)
class PaperOrderReconciliation:
    intent_id: str
    session_id: str
    broker_order_id: int
    symbol: str
    side: str
    intended_quantity: Decimal
    latest_status: str | None
    reported_filled: Decimal
    reported_remaining: Decimal
    executed_quantity: Decimal
    reconciled: bool
    terminal: bool
    reason: str
    observed_at: str


TERMINAL_ORDER_STATUSES = frozenset(
    {"filled", "cancelled", "apicancelled", "inactive", "error"}
)


def ensure_paper_order_config(config: IBKRConnectionConfig) -> None:
    if config.host not in {"127.0.0.1", "localhost", "::1"}:
        raise IBKRPaperOrderError(
            "Paper 订单连接只允许本机 Gateway"
        )
    if config.port != 4002:
        raise IBKRPaperOrderError(
            "Paper 订单连接硬锁 IB Gateway 端口 4002"
        )
    if config.api_read_only:
        raise IBKRPaperOrderError(
            "订单适配器要求独立的非只读 Paper 配置"
        )
    if not config.paper_order_submission_enabled:
        raise IBKRPaperOrderError("Paper 订单能力尚未开启")


def validate_paper_order_intent(
    intent: PaperOrderIntent,
    *,
    allowed_symbols: frozenset[str],
    max_order_notional: Decimal,
    sellable_quantities: dict[str, int],
) -> None:
    if intent.side not in {"BUY", "SELL"}:
        raise IBKRPaperOrderError("只允许 BUY 或 SELL")
    if intent.symbol not in allowed_symbols:
        raise IBKRPaperOrderError("订单代码不在本会话候选集中")
    if (
        not isinstance(intent.quantity, int)
        or isinstance(intent.quantity, bool)
        or intent.quantity <= 0
    ):
        raise IBKRPaperOrderError("订单数量必须是正整股")
    if intent.limit_price <= 0:
        raise IBKRPaperOrderError("限价必须为正")
    if (
        intent.limit_price * intent.quantity
        > max_order_notional
    ):
        raise IBKRPaperOrderError("订单超过本会话单笔名义金额上限")
    if (
        intent.side == "SELL"
        and intent.quantity
        > sellable_quantities.get(intent.symbol, 0)
    ):
        raise IBKRPaperOrderError("卖出数量超过本会话可卖整股")


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

    def reconciliation_rows(
        self,
        *,
        session_id: str | None = None,
        limit: int = 1000,
    ) -> tuple[PaperOrderReconciliation, ...]:
        where = "WHERE i.session_id = ?" if session_id else ""
        parameters: tuple[object, ...] = (
            (session_id, limit) if session_id else (limit,)
        )
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
                LIMIT ?
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


class IBKRPaperOrderService:
    """Narrow IBKR Paper order adapter.

    Only whole-share US stock / ETF limit orders for an explicitly armed DU
    session are exposed. Regular routing is SMART DAY; optional 5×24 Paper
    routing uses OutsideRth for pre/after-hours and direct OVERNIGHT routing
    for the IBKR overnight session. There is no Live port, market order, short
    sale, global cancel, option or margin-borrowing surface.
    """

    def __init__(
        self,
        config: IBKRConnectionConfig,
        *,
        journal: PaperOrderJournal,
        extended_hours_enabled: bool = False,
    ) -> None:
        ensure_paper_order_config(config)
        self.config = config
        self.journal = journal
        self.extended_hours_enabled = extended_hours_enabled
        self._client: Any | None = None
        self._thread: Thread | None = None
        self._connected = False
        self._account = ""
        self._next_order_id: int | None = None
        self._id_lock = Lock()
        self._state_lock = Lock()
        self._event_lock = Lock()
        self._updates: deque[PaperOrderUpdate] = deque()
        self._executions: deque[PaperExecution] = deque()
        self._intent_by_order: dict[int, PaperOrderIntent] = {}
        self._order_by_intent: dict[str, int] = {}
        self._cancel_requested: set[str] = set()
        self._armed_session_id: str | None = None
        self._allowed_symbols: frozenset[str] = frozenset()
        self._max_order_notional = Decimal("0")
        self._sellable_quantities: dict[str, int] = {}
        self._account_metrics: dict[str, Decimal] = {}
        self._broker_positions: dict[
            str, PaperBrokerPosition
        ] = {}
        self._open_broker_orders: dict[int, str] = {}
        self._completed_broker_orders: dict[
            int, tuple[str, Decimal]
        ] = {}
        self._daily_pnl: Decimal | None = None
        self._unrealized_pnl: Decimal | None = None
        self._realized_pnl: Decimal | None = None
        self._broker_observed_at = _now_iso()
        self._submit_latency: dict[str, dict[str, str]] = {}

    def connect(self) -> PaperOrderConnection:
        if self._connected and self._client is not None:
            return self.connection_snapshot()
        try:
            from ibapi.client import EClient
            from ibapi.wrapper import EWrapper
        except ImportError as error:
            raise IBKRPaperOrderError(
                "未安装 IBKR 官方 Python API"
            ) from error

        ready = Event()
        accounts_ready = Event()
        summary_ready = Event()
        positions_ready = Event()
        open_orders_ready = Event()
        completed_orders_ready = Event()
        executions_ready = Event()
        errors: list[str] = []
        service = self

        class PaperApp(EWrapper, EClient):
            def __init__(self) -> None:
                EWrapper.__init__(self)
                EClient.__init__(self, wrapper=self)

            def nextValidId(self, orderId: int) -> None:
                service._next_order_id = int(orderId)
                ready.set()

            def managedAccounts(self, accountsList: str) -> None:
                accounts = tuple(
                    row.strip()
                    for row in accountsList.split(",")
                    if row.strip()
                )
                if len(accounts) != 1:
                    errors.append(
                        "Paper 自动量化要求 Gateway 只返回一个账户"
                    )
                elif not accounts[0].upper().startswith("DU"):
                    errors.append("拒绝非 DU 账户：Live 永久阻断")
                else:
                    service._account = accounts[0]
                accounts_ready.set()

            def error(
                self,
                reqId: int,
                *args: Any,
            ) -> None:
                if len(args) >= 4:
                    _, errorCode, errorString, *_ = args
                elif len(args) >= 2:
                    errorCode, errorString, *_ = args
                else:
                    return
                errorCode = int(errorCode)
                if errorCode not in INFORMATIONAL_ERROR_CODES:
                    if reqId not in service._intent_by_order:
                        errors.append(
                            f"{errorCode}: {str(errorString)}"
                        )
                    service._record_error(
                        reqId, errorCode, str(errorString)
                    )

            def orderStatus(
                self,
                orderId: int,
                status: str,
                filled,
                remaining,
                avgFillPrice: float,
                permId: int,
                parentId: int,
                lastFillPrice: float,
                clientId: int,
                whyHeld: str,
                mktCapPrice: float,
            ) -> None:
                del permId, parentId, clientId, mktCapPrice
                service._record_order_status(
                    orderId=orderId,
                    status=status,
                    filled=Decimal(str(filled)),
                    remaining=Decimal(str(remaining)),
                    average_fill_price=(
                        Decimal(str(avgFillPrice))
                        if avgFillPrice > 0
                        else None
                    ),
                    last_fill_price=(
                        Decimal(str(lastFillPrice))
                        if lastFillPrice > 0
                        else None
                    ),
                    message=whyHeld or "",
                )

            def openOrder(
                self,
                orderId: int,
                contract,
                order,
                orderState,
            ) -> None:
                service._recover_intent_mapping(orderId)
                with service._state_lock:
                    service._open_broker_orders[int(orderId)] = (
                        f"{str(contract.symbol).upper()} "
                        f"{str(order.action).upper()} "
                        f"{order.totalQuantity} · "
                        f"{str(orderState.status)}"
                    )
                    service._broker_observed_at = _now_iso()

            def openOrderEnd(self) -> None:
                open_orders_ready.set()

            def accountSummary(
                self,
                reqId: int,
                account: str,
                tag: str,
                value: str,
                currency: str,
            ) -> None:
                del reqId, currency
                if account != service._account:
                    return
                try:
                    parsed = Decimal(value)
                except Exception:
                    return
                with service._state_lock:
                    service._account_metrics[tag] = parsed
                    service._broker_observed_at = _now_iso()

            def accountSummaryEnd(self, reqId: int) -> None:
                if reqId == 91_001:
                    summary_ready.set()

            def position(
                self,
                account: str,
                contract,
                pos,
                avgCost: float,
            ) -> None:
                if account != service._account:
                    return
                symbol = str(contract.symbol).upper()
                row = PaperBrokerPosition(
                    symbol=symbol,
                    quantity=Decimal(str(pos)),
                    average_cost=Decimal(str(avgCost)),
                )
                with service._state_lock:
                    if row.quantity == 0:
                        service._broker_positions.pop(
                            symbol, None
                        )
                    else:
                        service._broker_positions[symbol] = row
                    service._broker_observed_at = _now_iso()

            def positionEnd(self) -> None:
                positions_ready.set()

            def pnl(
                self,
                reqId: int,
                dailyPnL: float,
                unrealizedPnL: float,
                realizedPnL: float,
            ) -> None:
                del reqId
                with service._state_lock:
                    service._daily_pnl = _optional_decimal(
                        dailyPnL
                    )
                    service._unrealized_pnl = _optional_decimal(
                        unrealizedPnL
                    )
                    service._realized_pnl = _optional_decimal(
                        realizedPnL
                    )
                    service._broker_observed_at = _now_iso()

            def execDetails(
                self, reqId: int, contract, execution
            ) -> None:
                del reqId
                service._record_execution(contract, execution)

            def execDetailsEnd(self, reqId: int) -> None:
                if reqId == 91_003:
                    executions_ready.set()

            def completedOrder(
                self, contract, order, orderState
            ) -> None:
                order_id = int(order.orderId)
                service._recover_intent_mapping(order_id)
                with service._state_lock:
                    service._completed_broker_orders[order_id] = (
                        str(orderState.status),
                        Decimal(str(order.totalQuantity)),
                    )
                    service._broker_observed_at = _now_iso()

            def completedOrdersEnd(self) -> None:
                completed_orders_ready.set()

            def connectionClosed(self) -> None:
                service._connected = False

        app = PaperApp()
        self._client = app
        try:
            connect_ibkr_client(app, self.config)
        except IBKRClientConnectError as error:
            self._client = None
            raise IBKRPaperOrderError(str(error)) from error
        self._thread = Thread(
            target=app.run,
            name="ibkr-paper-order-network",
            daemon=True,
        )
        self._thread.start()
        timeout = self.config.connection_timeout_seconds
        deadline = monotonic() + timeout
        if not _wait_before_deadline(ready, deadline):
            app.disconnect()
            raise IBKRPaperOrderError(
                "等待 IBKR Paper 订单握手超时"
            )
        if not _wait_before_deadline(accounts_ready, deadline):
            app.disconnect()
            raise IBKRPaperOrderError(
                "等待 IBKR Paper 唯一 DU 账户超时"
            )
        if errors:
            app.disconnect()
            raise IBKRPaperOrderError("；".join(errors))
        self._connected = True
        app.reqAccountSummary(
            91_001,
            "All",
            "NetLiquidation,TotalCashValue,AvailableFunds,BuyingPower",
        )
        app.reqPositions()
        app.reqPnL(91_002, self._account, "")
        try:
            from ibapi.execution import ExecutionFilter

            app.reqAllOpenOrders()
            app.reqCompletedOrders(True)
            app.reqExecutions(91_003, ExecutionFilter())
        except Exception as error:
            app.disconnect()
            self._connected = False
            raise IBKRPaperOrderError(
                f"请求 Paper 订单恢复快照失败：{error}"
            ) from error
        if not _wait_before_deadline(summary_ready, deadline):
            app.disconnect()
            self._connected = False
            raise IBKRPaperOrderError(
                "等待 IBKR Paper 账户摘要超时"
            )
        if not _wait_before_deadline(positions_ready, deadline):
            app.disconnect()
            self._connected = False
            raise IBKRPaperOrderError(
                "等待 IBKR Paper 持仓对账超时"
            )
        if not _wait_before_deadline(open_orders_ready, deadline):
            app.disconnect()
            self._connected = False
            raise IBKRPaperOrderError(
                "等待 IBKR Paper 开放订单快照超时"
            )
        if not _wait_before_deadline(
            completed_orders_ready, deadline
        ):
            app.disconnect()
            self._connected = False
            raise IBKRPaperOrderError(
                "等待 IBKR Paper 已完成订单快照超时"
                + (
                    f"；Gateway：{'；'.join(errors[-3:])}"
                    if errors
                    else ""
                )
            )
        if not _wait_before_deadline(executions_ready, deadline):
            app.disconnect()
            self._connected = False
            raise IBKRPaperOrderError(
                "等待 IBKR Paper 当日成交快照超时"
            )
        self._reconcile_completed_broker_orders()
        return self.connection_snapshot()

    def connection_snapshot(self) -> PaperOrderConnection:
        client = self._client
        with self._state_lock:
            open_broker_order_count = len(
                self._open_broker_orders
            )
        unresolved = sum(
            not row.reconciled
            for row in self.journal.reconciliation_rows(limit=1000)
        )
        return PaperOrderConnection(
            connected=(
                self._connected
                and client is not None
                and bool(client.isConnected())
            ),
            account_alias=mask_account_id(self._account),
            server_version=(
                int(client.serverVersion())
                if client is not None and client.isConnected()
                else 0
            ),
            connection_time=(
                str(client.twsConnectionTime())
                if client is not None and client.isConnected()
                else ""
            ),
            next_order_id=self._next_order_id or 0,
            open_broker_orders=open_broker_order_count,
            unreconciled_local_orders=unresolved,
        )

    def arm(
        self,
        *,
        session_id: str,
        allowed_symbols: tuple[str, ...],
        max_order_notional: Decimal,
        sellable_quantities: dict[str, int] | None = None,
    ) -> None:
        if not self.connection_snapshot().connected:
            raise IBKRPaperOrderError("Paper 订单通道尚未连接")
        if not session_id.strip():
            raise IBKRPaperOrderError("会话 ID 不能为空")
        normalized = frozenset(
            symbol.strip().upper()
            for symbol in allowed_symbols
            if symbol.strip()
        )
        if not normalized:
            raise IBKRPaperOrderError("自动量化候选集不能为空")
        if max_order_notional <= 0:
            raise IBKRPaperOrderError("单笔金额上限必须为正")
        with self._state_lock:
            if self._open_broker_orders:
                details = "；".join(
                    self._open_broker_orders.values()
                )
                raise IBKRPaperOrderError(
                    "券商仍有未完成 API 订单；拒绝武装新会话："
                    f"{details}"
                )
            self._armed_session_id = session_id
            self._allowed_symbols = normalized
            self._max_order_notional = max_order_notional
            self._sellable_quantities = dict(
                sellable_quantities or {}
            )

    def broker_state(self) -> PaperBrokerState:
        with self._state_lock:
            metrics = dict(self._account_metrics)
            positions = tuple(
                sorted(
                    self._broker_positions.values(),
                    key=lambda row: row.symbol,
                )
            )
            return PaperBrokerState(
                account_alias=mask_account_id(self._account),
                net_liquidation=metrics.get("NetLiquidation"),
                cash=metrics.get("TotalCashValue"),
                available_funds=metrics.get("AvailableFunds"),
                buying_power=metrics.get("BuyingPower"),
                daily_pnl=self._daily_pnl,
                unrealized_pnl=self._unrealized_pnl,
                realized_pnl=self._realized_pnl,
                positions=positions,
                observed_at=self._broker_observed_at,
            )

    def disarm(self) -> None:
        with self._state_lock:
            self._armed_session_id = None
            self._allowed_symbols = frozenset()
            self._max_order_notional = Decimal("0")
            self._sellable_quantities.clear()

    def submit(self, intent: PaperOrderIntent) -> int:
        with self._state_lock:
            armed_session_id = self._armed_session_id
            allowed_symbols = self._allowed_symbols
            max_order_notional = self._max_order_notional
            sellable_quantities = dict(
                self._sellable_quantities
            )
        if intent.session_id != armed_session_id:
            raise IBKRPaperOrderError("订单不属于当前已武装会话")
        if intent.intent_id in self._order_by_intent:
            return self._order_by_intent[intent.intent_id]
        if intent.idempotency_key:
            existing = self.journal.intent_for_idempotency_key(
                intent.idempotency_key
            )
            if existing is not None and existing.intent_id != intent.intent_id:
                raise IBKRPaperOrderError(
                    "检测到重复幂等键，已拒绝重复订单"
                )
        validate_paper_order_intent(
            intent,
            allowed_symbols=allowed_symbols,
            max_order_notional=max_order_notional,
            sellable_quantities=sellable_quantities,
        )
        routing = paper_order_routing(
            extended_hours_enabled=self.extended_hours_enabled
        )
        if not routing.allowed:
            raise IBKRPaperOrderError(
                "当前时段禁止提交 Paper 订单：" + routing.reason
            )
        client = self._client
        if (
            client is None
            or not self.connection_snapshot().connected
            or not self._account
        ):
            raise IBKRPaperOrderError("Paper 订单通道已断开")
        try:
            from ibapi.contract import Contract
            from ibapi.order import Order
        except ImportError as error:
            raise IBKRPaperOrderError(
                "未安装 IBKR 官方 Python API"
            ) from error

        with self._id_lock:
            if self._next_order_id is None:
                raise IBKRPaperOrderError("尚未获得有效订单号")
            order_id = self._next_order_id
            self._next_order_id += 1
        contract = Contract()
        contract.symbol = intent.symbol
        contract.secType = "STK"
        contract.exchange = routing.exchange
        contract.currency = "USD"
        order = Order()
        order.action = intent.side
        order.orderType = "LMT"
        order.totalQuantity = Decimal(intent.quantity)
        order.lmtPrice = float(intent.limit_price)
        order.tif = routing.tif
        order.outsideRth = routing.outside_rth
        order.account = self._account
        order.transmit = True
        order.orderRef = (
            f"USQ-{intent.session_id[:8]}-{intent.intent_id[:8]}-"
            f"{routing.session.value[:3].upper()}"
        )
        submitted_at = _now_iso()
        self.journal.record_intent(
            intent,
            broker_order_id=order_id,
            account_alias=mask_account_id(self._account),
        )
        self._intent_by_order[order_id] = intent
        self._order_by_intent[intent.intent_id] = order_id
        self._submit_latency[intent.intent_id] = {
            "intent_generated_at": intent.generated_at,
            "submitted_at": submitted_at,
        }
        try:
            client.placeOrder(order_id, contract, order)
        except Exception as error:
            self._submit_latency.pop(intent.intent_id, None)
            update = PaperOrderUpdate(
                intent_id=intent.intent_id,
                broker_order_id=order_id,
                status="SubmitUncertain",
                filled=Decimal("0"),
                remaining=Decimal(intent.quantity),
                average_fill_price=None,
                last_fill_price=None,
                message=(
                    "本地提交调用异常；订单是否到达券商尚不确定："
                    f"{error}"
                ),
                observed_at=_now_iso(),
            )
            with self._event_lock:
                self._updates.append(update)
            self.journal.record_update(update)
            raise IBKRPaperOrderUncertainError(
                "Paper 订单提交结果不确定；会话必须停机并对账",
                intent_id=intent.intent_id,
                broker_order_id=order_id,
            ) from error
        return order_id

    def cancel_intent(self, intent_id: str) -> bool:
        """Cancel one exact order created by this service.

        This intentionally exposes no global-cancel surface. Repeated calls
        are idempotent while the broker cancellation callback is pending.
        """
        broker_order_id = self._order_by_intent.get(intent_id)
        intent = (
            self._intent_by_order.get(broker_order_id)
            if broker_order_id is not None
            else None
        )
        if broker_order_id is None or intent is None:
            raise IBKRPaperOrderError(
                "找不到本会话对应的 Paper 订单"
            )
        if intent_id in self._cancel_requested:
            return False
        client = self._client
        if (
            client is None
            or not self.connection_snapshot().connected
        ):
            raise IBKRPaperOrderError(
                "Paper 订单通道已断开，无法确认撤单"
            )
        self._cancel_requested.add(intent_id)
        try:
            client.cancelOrder(broker_order_id, "")
        except Exception as error:
            self._cancel_requested.discard(intent_id)
            raise IBKRPaperOrderUncertainError(
                "Paper 撤单结果不确定；必须停止并对账",
                intent_id=intent_id,
                broker_order_id=broker_order_id,
            ) from error
        return True

    def poll_updates(self) -> tuple[PaperOrderUpdate, ...]:
        with self._event_lock:
            rows = tuple(self._updates)
            self._updates.clear()
        return rows

    def poll_executions(self) -> tuple[PaperExecution, ...]:
        with self._event_lock:
            rows = tuple(self._executions)
            self._executions.clear()
        return rows

    def disconnect(self) -> None:
        self.disarm()
        client = self._client
        if client is not None and client.isConnected():
            try:
                client.cancelAccountSummary(91_001)
                client.cancelPnL(91_002)
                client.cancelPositions()
            except Exception:
                pass
            client.disconnect()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        self._connected = False
        self._client = None

    def _record_error(
        self, request_id: int, code: int, message: str
    ) -> None:
        intent = self._intent_by_order.get(request_id)
        if intent is None:
            return
        if code == 202:
            status = "Cancelled"
        else:
            status = "Error"
        self._record_order_status(
            orderId=request_id,
            status=status,
            filled=Decimal("0"),
            remaining=Decimal(intent.quantity),
            average_fill_price=None,
            last_fill_price=None,
            message=f"{code}: {message}",
        )

    def _recover_intent_mapping(
        self, broker_order_id: int
    ) -> PaperOrderIntent | None:
        intent = self._intent_by_order.get(broker_order_id)
        if intent is None:
            intent = self.journal.intent_for_broker_order(
                broker_order_id
            )
            if intent is not None:
                self._intent_by_order[broker_order_id] = intent
                self._order_by_intent[
                    intent.intent_id
                ] = broker_order_id
        return intent

    def _reconcile_completed_broker_orders(self) -> None:
        for order_id, (
            status,
            intended_quantity,
        ) in tuple(self._completed_broker_orders.items()):
            intent = self._recover_intent_mapping(order_id)
            if intent is None:
                continue
            executed = self.journal.executed_quantity(
                intent.intent_id
            )
            normalized = status.casefold()
            reported_filled = (
                intended_quantity
                if normalized == "filled"
                else executed
            )
            self._record_order_status(
                order_id=order_id,
                status=status,
                filled=reported_filled,
                remaining=max(
                    Decimal("0"),
                    intended_quantity - reported_filled,
                ),
                average_fill_price=None,
                last_fill_price=None,
                message="应用启动时从 IBKR completedOrders 恢复",
            )

    def _record_order_status(
        self,
        *,
        orderId: int | None = None,
        order_id: int | None = None,
        status: str,
        filled: Decimal,
        remaining: Decimal,
        average_fill_price: Decimal | None,
        last_fill_price: Decimal | None,
        message: str,
    ) -> None:
        broker_order_id = (
            order_id if order_id is not None else orderId
        )
        if broker_order_id is None:
            return
        intent = self._intent_by_order.get(broker_order_id)
        if intent is None:
            return
        update = PaperOrderUpdate(
            intent_id=intent.intent_id,
            broker_order_id=broker_order_id,
            status=status,
            filled=filled,
            remaining=remaining,
            average_fill_price=average_fill_price,
            last_fill_price=last_fill_price,
            message=message,
            observed_at=_now_iso(),
        )
        if status.casefold() in TERMINAL_ORDER_STATUSES:
            self._cancel_requested.discard(intent.intent_id)
        self.journal.record_update(update)
        with self._event_lock:
            self._updates.append(update)
        if status.casefold() in TERMINAL_ORDER_STATUSES:
            with self._state_lock:
                self._open_broker_orders.pop(
                    broker_order_id, None
                )

    def _record_execution(self, contract, execution) -> None:
        order_id = int(execution.orderId)
        intent = self._recover_intent_mapping(order_id)
        if intent is None:
            return
        paper_execution = PaperExecution(
                intent_id=intent.intent_id,
                broker_order_id=order_id,
                execution_id=str(execution.execId),
                symbol=str(contract.symbol).upper(),
                side=(
                    "BUY"
                    if str(execution.side).upper() in {"BOT", "BUY"}
                    else "SELL"
                ),
                quantity=Decimal(str(execution.shares)),
                price=Decimal(str(execution.price)),
                occurred_at=str(execution.time) or _now_iso(),
        )
        if not self.journal.record_execution(paper_execution):
            return
        whole_quantity = int(paper_execution.quantity)
        if Decimal(whole_quantity) == paper_execution.quantity:
            with self._state_lock:
                if paper_execution.side == "BUY":
                    self._sellable_quantities[
                        paper_execution.symbol
                    ] = (
                        self._sellable_quantities.get(
                            paper_execution.symbol, 0
                        )
                        + whole_quantity
                    )
                else:
                    self._sellable_quantities[
                        paper_execution.symbol
                    ] = max(
                        0,
                        self._sellable_quantities.get(
                            paper_execution.symbol, 0
                        )
                        - whole_quantity,
                    )
        with self._event_lock:
            self._executions.append(paper_execution)
        self._submit_latency.pop(intent.intent_id, None)

    def submit_latency(self, intent_id: str) -> dict[str, str] | None:
        with self._state_lock:
            return self._submit_latency.get(intent_id)

    def reconciliation_rows_with_latency(
        self, *, session_id: str | None = None, limit: int = 1000
    ) -> tuple[dict, ...]:
        rows = self.journal.reconciliation_rows(
            session_id=session_id, limit=limit
        )
        results: list[dict] = []
        for row in rows:
            data = {
                "intent_id": row.intent_id,
                "session_id": row.session_id,
                "broker_order_id": row.broker_order_id,
                "symbol": row.symbol,
                "side": row.side,
                "intended_quantity": int(row.intended_quantity),
                "executed_quantity": int(row.executed_quantity),
                "latest_status": row.latest_status,
                "reconciled": row.reconciled,
                "terminal": row.terminal,
                "reason": row.reason,
                "observed_at": row.observed_at,
            }
            latency = self.submit_latency(row.intent_id)
            if latency is not None:
                try:
                    intent_ts = datetime.fromisoformat(
                        latency["intent_generated_at"].replace("Z", "+00:00")
                    )
                    submit_ts = datetime.fromisoformat(
                        latency["submitted_at"].replace("Z", "+00:00")
                    )
                    data["submit_latency_ms"] = int(
                        (submit_ts - intent_ts).total_seconds() * 1000
                    )
                except Exception:
                    pass
            results.append(data)
        return tuple(results)

    def sessions(
        self, *, limit: int = 50
    ) -> tuple[dict[str, object], ...]:
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


def new_paper_order_intent(
    *,
    session_id: str,
    strategy_version_id: str,
    symbol: str,
    side: str,
    quantity: int,
    limit_price: Decimal,
    reason: str,
) -> PaperOrderIntent:
    return PaperOrderIntent(
        intent_id=uuid4().hex,
        session_id=session_id,
        strategy_version_id=strategy_version_id,
        symbol=symbol.strip().upper(),
        side=side.strip().upper(),
        quantity=quantity,
        limit_price=limit_price,
        reason=reason,
        generated_at=_now_iso(),
        idempotency_key=uuid4().hex,
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _optional_decimal(value: float) -> Decimal | None:
    parsed = Decimal(str(value))
    if abs(parsed) >= Decimal("1e307"):
        return None
    return parsed


def _wait_before_deadline(event: Event, deadline: float) -> bool:
    remaining = deadline - monotonic()
    return remaining > 0 and event.wait(remaining)
