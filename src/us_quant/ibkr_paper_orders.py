from __future__ import annotations

from collections import deque
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from threading import Event, Lock, RLock, Thread
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
from us_quant.paper_order_journal import PaperOrderJournal, _now_iso
from us_quant.paper_order_models import (
    PaperBrokerOrder,
    PaperBrokerPosition,
    PaperBrokerState,
    PaperExecution,
    PaperOrderConnection,
    PaperOrderIntent,
    PaperOrderReconciliation,
    PaperOrderUpdate,
    PaperReconciliationSnapshot,
    ReconciliationSummary,
    TERMINAL_ORDER_STATUSES,
)
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


@dataclass(slots=True)
class _ReconciliationRefreshAttempt:
    app: Any
    epoch: int
    session_id: str
    summary_ready: Event
    positions_ready: Event
    open_orders_ready: Event
    completed_orders_ready: Event
    executions_ready: Event
    account_metrics: dict[str, Decimal]
    positions: dict[str, PaperBrokerPosition]
    open_orders: dict[int, PaperBrokerOrder]
    completed_orders: dict[int, PaperBrokerOrder]
    executions: list[tuple[Any, Any]]


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
        self._physical_connection_epoch = 0
        self._connection_generation = 0
        self._reconciliation_generation = 0
        self._state_version = 0
        self._snapshot_complete = False
        self._reconciliation_snapshot_complete = False
        self._connection_observed_at = ""
        self._account = ""
        self._next_order_id: int | None = None
        self._next_order_id_floor = 0
        self._id_lock = Lock()
        self._state_lock = Lock()
        self._event_lock = Lock()
        self._correlation_lock = RLock()
        self._updates: deque[PaperOrderUpdate] = deque()
        self._executions: deque[PaperExecution] = deque()
        self._intent_by_order: dict[int, PaperOrderIntent] = {}
        self._order_by_intent: dict[str, int] = {}
        self._cancel_requested: set[str] = set()
        self._armed_session_id: str | None = None
        # This is deliberately an internal, unmasked identity.  A displayed
        # account alias is insufficient to bind a session across reconnects.
        self._armed_account_fingerprint: str | None = None
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
        self._refresh_lock = Lock()
        self._refresh_attempt: _ReconciliationRefreshAttempt | None = None

    def connect(self) -> PaperOrderConnection:
        if self._connected and self._client is not None:
            return self.connection_snapshot()
        self._invalidate_connection_snapshot()
        with self._state_lock:
            self._physical_connection_epoch += 1
            connection_epoch = self._physical_connection_epoch
        # CR-4: the journal's highest ever order id bounds nextValidId below,
        # so a Gateway restart can never hand back reused ids.
        self._next_order_id_floor = (
            self.journal.max_broker_order_id() + 1
        )
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

            def _current(self) -> bool:
                return service._is_current_connection(
                    self, connection_epoch
                )

            def nextValidId(self, orderId: int) -> None:
                if not self._current():
                    return
                # CR-4 fix: never let a Gateway restart regress the order-id
                # counter.  IBKR order IDs must stay strictly increasing so a
                # reused ID can never amend/cancel an earlier journal order.
                service._next_order_id = max(
                    int(orderId), service._next_order_id_floor
                )
                service._mark_state_changed()
                ready.set()

            def managedAccounts(self, accountsList: str) -> None:
                if not self._current():
                    return
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
                    service._mark_state_changed()
                accounts_ready.set()

            def error(
                self,
                reqId: int,
                *args: Any,
            ) -> None:
                if not self._current():
                    return
                if len(args) >= 4:
                    _, errorCode, errorString, *_ = args
                elif len(args) >= 2:
                    errorCode, errorString, *_ = args
                else:
                    return
                errorCode = int(errorCode)
                if errorCode not in INFORMATIONAL_ERROR_CODES:
                    with service._correlation_lock:
                        known_order = reqId in service._intent_by_order
                    if not known_order:
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
                if not self._current():
                    return
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
                if not self._current():
                    return
                attempt = service._refresh_attempt_for(
                    self, connection_epoch
                )
                broker_order = _paper_broker_order(
                    orderId, contract, order, orderState
                )
                if attempt is not None:
                    attempt.open_orders[broker_order.broker_order_id] = (
                        broker_order
                    )
                    return
                service._recover_intent_mapping(orderId)
                with service._state_lock:
                    service._open_broker_orders[int(orderId)] = (
                        f"{str(contract.symbol).upper()} "
                        f"{str(order.action).upper()} "
                        f"{order.totalQuantity} · "
                        f"{str(orderState.status)}"
                    )
                    service._broker_observed_at = _now_iso()
                    service._mark_state_changed_locked()

            def openOrderEnd(self) -> None:
                if not self._current():
                    return
                attempt = service._refresh_attempt_for(
                    self, connection_epoch
                )
                if attempt is not None:
                    attempt.open_orders_ready.set()
                    return
                open_orders_ready.set()

            def accountSummary(
                self,
                reqId: int,
                account: str,
                tag: str,
                value: str,
                currency: str,
            ) -> None:
                if not self._current():
                    return
                del currency
                if account != service._account:
                    return
                try:
                    parsed = Decimal(value)
                except Exception:
                    return
                attempt = service._refresh_attempt_for(
                    self, connection_epoch
                )
                if attempt is not None and reqId == 91_101:
                    attempt.account_metrics[tag] = parsed
                    return
                with service._state_lock:
                    service._account_metrics[tag] = parsed
                    service._broker_observed_at = _now_iso()
                    service._mark_state_changed_locked()

            def accountSummaryEnd(self, reqId: int) -> None:
                if not self._current():
                    return
                attempt = service._refresh_attempt_for(
                    self, connection_epoch
                )
                if attempt is not None and reqId == 91_101:
                    attempt.summary_ready.set()
                    return
                if reqId == 91_001:
                    summary_ready.set()

            def position(
                self,
                account: str,
                contract,
                pos,
                avgCost: float,
            ) -> None:
                if not self._current():
                    return
                if account != service._account:
                    return
                symbol = str(contract.symbol).upper()
                row = PaperBrokerPosition(
                    symbol=symbol,
                    quantity=Decimal(str(pos)),
                    average_cost=Decimal(str(avgCost)),
                )
                attempt = service._refresh_attempt_for(
                    self, connection_epoch
                )
                if attempt is not None:
                    if row.quantity != 0:
                        attempt.positions[symbol] = row
                    return
                with service._state_lock:
                    if row.quantity == 0:
                        service._broker_positions.pop(
                            symbol, None
                        )
                    else:
                        service._broker_positions[symbol] = row
                    service._broker_observed_at = _now_iso()
                    service._mark_state_changed_locked()

            def positionEnd(self) -> None:
                if not self._current():
                    return
                attempt = service._refresh_attempt_for(
                    self, connection_epoch
                )
                if attempt is not None:
                    attempt.positions_ready.set()
                    return
                positions_ready.set()

            def pnl(
                self,
                reqId: int,
                dailyPnL: float,
                unrealizedPnL: float,
                realizedPnL: float,
            ) -> None:
                if not self._current():
                    return
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
                    service._mark_state_changed_locked()

            def execDetails(
                self, reqId: int, contract, execution
            ) -> None:
                if not self._current():
                    return
                attempt = service._refresh_attempt_for(
                    self, connection_epoch
                )
                if attempt is not None and reqId == 91_103:
                    attempt.executions.append((contract, execution))
                    return
                del reqId
                service._record_execution(contract, execution)

            def execDetailsEnd(self, reqId: int) -> None:
                if not self._current():
                    return
                attempt = service._refresh_attempt_for(
                    self, connection_epoch
                )
                if attempt is not None and reqId == 91_103:
                    attempt.executions_ready.set()
                    return
                if reqId == 91_003:
                    executions_ready.set()

            def completedOrder(
                self, contract, order, orderState
            ) -> None:
                if not self._current():
                    return
                order_id = int(order.orderId)
                attempt = service._refresh_attempt_for(
                    self, connection_epoch
                )
                broker_order = _paper_broker_order(
                    order_id, contract, order, orderState
                )
                if attempt is not None:
                    attempt.completed_orders[order_id] = broker_order
                    return
                service._recover_intent_mapping(order_id)
                with service._state_lock:
                    service._completed_broker_orders[order_id] = (
                        str(orderState.status),
                        Decimal(str(order.totalQuantity)),
                    )
                    service._broker_observed_at = _now_iso()
                    service._mark_state_changed_locked()

            def completedOrdersEnd(self) -> None:
                if not self._current():
                    return
                attempt = service._refresh_attempt_for(
                    self, connection_epoch
                )
                if attempt is not None:
                    attempt.completed_orders_ready.set()
                    return
                completed_orders_ready.set()

            def connectionClosed(self) -> None:
                if not self._current():
                    return
                service._connected = False
                service._invalidate_connection_snapshot()

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
        self._publish_complete_connection_snapshot()
        return self.connection_snapshot()

    def connection_snapshot(self) -> PaperOrderConnection:
        client = self._client
        with self._state_lock:
            open_broker_order_count = len(
                self._open_broker_orders
            )
            connection_generation = self._connection_generation
            snapshot_complete = self._snapshot_complete
            observed_at = self._connection_observed_at
        reconciliation = self.journal.reconciliation_summary()
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
            unreconciled_local_orders=reconciliation.unreconciled,
            connection_generation=connection_generation,
            snapshot_complete=snapshot_complete,
            observed_at=observed_at,
        )

    def reconciliation_summary(
        self, session_id: str
    ) -> ReconciliationSummary:
        return self.journal.reconciliation_summary(session_id)

    def reconciliation_snapshot_is_current(
        self, snapshot: PaperReconciliationSnapshot
    ) -> bool:
        """Return whether a proof still describes current broker/journal state."""

        client = self._client
        with self._state_lock:
            return bool(
                self._connected
                and client is not None
                and client.isConnected()
                and self._reconciliation_snapshot_complete
                and snapshot.snapshot_complete
                and self._connection_generation
                == snapshot.connection_generation
                and self._reconciliation_generation
                == snapshot.reconciliation_generation
                and self._state_version == snapshot.state_version
                and self._armed_account_binding_is_valid_locked()
                and _account_fingerprint(self._account)
                == snapshot.account_fingerprint
            )

    def armed_account_binding_is_valid(self) -> bool:
        """Whether an armed session still points at its original DU account.

        The result is intentionally not exposed in a user-facing snapshot: it
        is a safety guard for the Paper coordinator, not account information.
        An unarmed service has no binding to invalidate.
        """

        with self._state_lock:
            return self._armed_account_binding_is_valid_locked()

    def armed_account_fingerprint(self) -> str:
        """Return the immutable internal account identity bound by ``arm``."""

        with self._state_lock:
            return self._armed_account_fingerprint or ""

    def refresh_reconciliation_snapshot(
        self, session_id: str
    ) -> PaperReconciliationSnapshot:
        if not self.armed_account_binding_is_valid():
            raise IBKRPaperOrderError(
                "Paper 会话账户已变化；必须人工停止并重新武装"
            )
        client = self._client
        if (
            not self._connected
            or client is None
            or not bool(client.isConnected())
        ):
            raise IBKRPaperOrderError(
                "Paper 订单通道已断开，无法刷新对账快照"
            )
        if not self._refresh_lock.acquire(blocking=False):
            raise IBKRPaperOrderError("已有对账快照刷新正在进行")
        try:
            with self._state_lock:
                epoch = self._physical_connection_epoch
                attempt = _ReconciliationRefreshAttempt(
                    app=client,
                    epoch=epoch,
                    session_id=session_id,
                    summary_ready=Event(),
                    positions_ready=Event(),
                    open_orders_ready=Event(),
                    completed_orders_ready=Event(),
                    executions_ready=Event(),
                    account_metrics={},
                    positions={},
                    open_orders={},
                    completed_orders={},
                    executions=[],
                )
                self._refresh_attempt = attempt
                self._reconciliation_snapshot_complete = False
            try:
                self._request_reconciliation_snapshot(client)
                deadline = monotonic() + self.config.connection_timeout_seconds
                for event, message in (
                    (attempt.summary_ready, "账户摘要"),
                    (attempt.positions_ready, "持仓"),
                    (attempt.open_orders_ready, "开放订单"),
                    (attempt.completed_orders_ready, "已完成订单"),
                    (attempt.executions_ready, "成交"),
                ):
                    if not _wait_before_deadline(event, deadline):
                        raise IBKRPaperOrderError(
                            f"等待 Paper 对账{message}快照超时"
                        )
                return self._publish_reconciliation_snapshot(attempt)
            except Exception:
                with self._state_lock:
                    if self._refresh_attempt is attempt:
                        self._refresh_attempt = None
                    self._reconciliation_snapshot_complete = False
                raise
        finally:
            self._refresh_lock.release()

    def _request_reconciliation_snapshot(self, client: Any) -> None:
        try:
            from ibapi.execution import ExecutionFilter
        except ImportError:
            execution_filter = None
        else:
            execution_filter = ExecutionFilter()
        try:
            client.reqAccountSummary(
                91_101,
                "All",
                "NetLiquidation,TotalCashValue,AvailableFunds,BuyingPower",
            )
            client.reqPositions()
            client.reqAllOpenOrders()
            client.reqCompletedOrders(True)
            client.reqExecutions(91_103, execution_filter)
        except Exception as error:
            raise IBKRPaperOrderError(
                f"请求 Paper 对账快照失败：{error}"
            ) from error

    def _publish_reconciliation_snapshot(
        self, attempt: _ReconciliationRefreshAttempt
    ) -> PaperReconciliationSnapshot:
        with self._state_lock:
            if (
                self._refresh_attempt is not attempt
                or not self._is_current_connection_locked(
                    attempt.app, attempt.epoch
                )
                or not self._armed_account_binding_is_valid_locked()
            ):
                raise IBKRPaperOrderError("对账快照连接已失效")
        for contract, execution in attempt.executions:
            self._record_execution(contract, execution)
        with self._state_lock:
            if (
                self._refresh_attempt is not attempt
                or not self._is_current_connection_locked(
                    attempt.app, attempt.epoch
                )
            ):
                raise IBKRPaperOrderError("对账快照连接已失效")
            self._account_metrics = dict(attempt.account_metrics)
            self._broker_positions = dict(attempt.positions)
            self._open_broker_orders = {
                order_id: _broker_order_text(order)
                for order_id, order in attempt.open_orders.items()
            }
            self._completed_broker_orders = {
                order_id: (order.status, order.quantity)
                for order_id, order in attempt.completed_orders.items()
            }
            self._broker_observed_at = _now_iso()
            self._reconciliation_generation += 1
            self._mark_state_changed_locked()
            connection_generation = self._connection_generation
            reconciliation_generation = self._reconciliation_generation
            state_version = self._state_version
            captured_at = self._broker_observed_at
            positions = tuple(
                sorted(self._broker_positions.values(), key=lambda row: row.symbol)
            )
            open_orders = tuple(
                sorted(attempt.open_orders.values(), key=lambda row: row.broker_order_id)
            )
            completed_orders = tuple(
                sorted(
                    attempt.completed_orders.values(),
                    key=lambda row: row.broker_order_id,
                )
            )
            self._refresh_attempt = None
        summary = self.journal.reconciliation_summary(attempt.session_id)
        account_fingerprint = _account_fingerprint(self._account)
        digest = _reconciliation_snapshot_digest(
            account_fingerprint=account_fingerprint,
            positions=positions,
            open_orders=open_orders,
            completed_orders=completed_orders,
            summary=summary,
        )
        with self._state_lock:
            if self._state_version != state_version:
                self._reconciliation_snapshot_complete = False
                raise IBKRPaperOrderError(
                    "对账快照捕获期间状态发生变化"
                )
            self._reconciliation_snapshot_complete = True
        return PaperReconciliationSnapshot(
            account_fingerprint=account_fingerprint,
            connection_generation=connection_generation,
            reconciliation_generation=reconciliation_generation,
            captured_at=captured_at,
            broker_positions=positions,
            open_broker_orders=open_orders,
            completed_broker_orders=completed_orders,
            reconciliation_summary=summary,
            state_version=state_version,
            digest=digest,
        )

    def _is_current_connection(self, app: Any, epoch: int) -> bool:
        with self._state_lock:
            return self._is_current_connection_locked(app, epoch)

    def _is_current_connection_locked(self, app: Any, epoch: int) -> bool:
        return (
            self._client is app
            and self._physical_connection_epoch == epoch
        )

    def _refresh_attempt_for(
        self, app: Any, epoch: int
    ) -> _ReconciliationRefreshAttempt | None:
        with self._state_lock:
            attempt = self._refresh_attempt
            if (
                attempt is not None
                and attempt.app is app
                and attempt.epoch == epoch
            ):
                return attempt
        return None

    def _mark_state_changed(self) -> None:
        with self._state_lock:
            self._mark_state_changed_locked()

    def _mark_state_changed_locked(self) -> None:
        self._state_version += 1
        self._reconciliation_snapshot_complete = False

    def _armed_account_binding_is_valid_locked(self) -> bool:
        """Check the immutable armed-account fingerprint under ``_state_lock``."""

        if self._armed_session_id is None:
            return True
        if not self._armed_account_fingerprint or not self._account:
            return False
        return _account_fingerprint(self._account) == (
            self._armed_account_fingerprint
        )

    def _invalidate_connection_snapshot(self) -> None:
        with self._state_lock:
            self._snapshot_complete = False
            self._connection_observed_at = ""

    def _publish_complete_connection_snapshot(self) -> None:
        with self._state_lock:
            self._connection_generation += 1
            self._snapshot_complete = True
            self._connection_observed_at = _now_iso()

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
            if not self._account:
                raise IBKRPaperOrderError(
                    "Paper 账户未就绪；拒绝武装会话"
                )
            if self._open_broker_orders:
                details = "；".join(
                    self._open_broker_orders.values()
                )
                raise IBKRPaperOrderError(
                    "券商仍有未完成 API 订单；拒绝武装新会话："
                    f"{details}"
                )
            self._armed_session_id = session_id
            self._armed_account_fingerprint = _account_fingerprint(
                self._account
            )
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
            self._armed_account_fingerprint = None
            self._allowed_symbols = frozenset()
            self._max_order_notional = Decimal("0")
            self._sellable_quantities.clear()

    def submit(self, intent: PaperOrderIntent) -> int:
        with self._state_lock:
            armed_session_id = self._armed_session_id
            account = self._account
            armed_account_binding_is_valid = (
                self._armed_account_binding_is_valid_locked()
            )
            allowed_symbols = self._allowed_symbols
            max_order_notional = self._max_order_notional
            sellable_quantities = dict(
                self._sellable_quantities
            )
            snapshot_complete = self._snapshot_complete
        if intent.session_id != armed_session_id:
            raise IBKRPaperOrderError("订单不属于当前已武装会话")
        if not armed_account_binding_is_valid:
            raise IBKRPaperOrderError(
                "Paper 会话账户已变化；拒绝提交订单"
            )
        if not snapshot_complete:
            # H-6 fix: never submit while the connection snapshot is not
            # complete (e.g. during the recovery window of a reconnect).
            raise IBKRPaperOrderError(
                "Paper 连接快照尚未完成；拒绝提交订单"
            )
        with self._correlation_lock:
            existing_order_id = self._order_by_intent.get(
                intent.intent_id
            )
        if existing_order_id is not None:
            return existing_order_id
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
            or not account
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
        order.account = account
        order.transmit = True
        order.orderRef = (
            f"USQ-{intent.session_id[:8]}-{intent.intent_id[:8]}-"
            f"{routing.session.value[:3].upper()}"
        )
        submitted_at = _now_iso()
        submit_error: Exception | None = None
        with self._correlation_lock:
            existing_order_id = self._order_by_intent.get(
                intent.intent_id
            )
            if existing_order_id is not None:
                return existing_order_id
            self.journal.record_intent(
                intent,
                broker_order_id=order_id,
                account_alias=mask_account_id(account),
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
                submit_error = error
            else:
                return order_id
        if submit_error is not None:
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
                    f"{submit_error}"
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
            ) from submit_error

    def cancel_intent(self, intent_id: str) -> bool:
        """Cancel one exact order created by this service.

        This intentionally exposes no global-cancel surface. Repeated calls
        are idempotent while the broker cancellation callback is pending.
        """
        client = self._client
        if (
            client is None
            or not self.connection_snapshot().connected
        ):
            raise IBKRPaperOrderError(
                "Paper 订单通道已断开，无法确认撤单"
            )
        with self._correlation_lock:
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
        # A transport reconnect must not silently change the account bound to
        # an armed session.  ``disarm`` is the only operation that clears the
        # binding; submit remains impossible while disconnected.
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
        self._invalidate_connection_snapshot()
        self._client = None

    def _record_error(
        self, request_id: int, code: int, message: str
    ) -> None:
        with self._correlation_lock:
            intent = self._intent_by_order.get(request_id)
        if intent is None:
            return
        if code == 202:
            status = "Cancelled"
        else:
            status = "Error"
        # H-9 fix: a cancel confirmation (202) racing an actual fill must not
        # overwrite the reported fill with zero; otherwise the engine sees a
        # terminal "Cancelled filled=0" against already-recorded executions
        # and halts on a spurious mismatch.
        executed = (
            self.journal.executed_quantity(intent.intent_id)
            if code == 202
            else Decimal("0")
        )
        self._record_order_status(
            orderId=request_id,
            status=status,
            filled=executed,
            remaining=max(
                Decimal("0"), intent.quantity - executed
            ),
            average_fill_price=None,
            last_fill_price=None,
            message=f"{code}: {message}",
        )

    def _recover_intent_mapping(
        self, broker_order_id: int
    ) -> PaperOrderIntent | None:
        with self._correlation_lock:
            intent = self._intent_by_order.get(broker_order_id)
        if intent is None:
            intent = self.journal.intent_for_broker_order(
                broker_order_id
            )
            if intent is not None:
                with self._correlation_lock:
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
        with self._correlation_lock:
            intent = self._intent_by_order.get(broker_order_id)
            if intent is None:
                return
            if status.casefold() in TERMINAL_ORDER_STATUSES:
                self._cancel_requested.discard(intent.intent_id)
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
        self.journal.record_update(update)
        self._mark_state_changed()
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
        self._mark_state_changed()
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
        with self._correlation_lock:
            self._submit_latency.pop(intent.intent_id, None)

    def submit_latency(self, intent_id: str) -> dict[str, str] | None:
        with self._correlation_lock:
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


def _optional_decimal(value: float) -> Decimal | None:
    parsed = Decimal(str(value))
    if abs(parsed) >= Decimal("1e307"):
        return None
    return parsed


def _paper_broker_order(
    order_id: int, contract: Any, order: Any, order_state: Any
) -> PaperBrokerOrder:
    return PaperBrokerOrder(
        broker_order_id=int(order_id),
        symbol=str(contract.symbol).upper(),
        side=str(order.action).upper(),
        quantity=Decimal(str(order.totalQuantity)),
        status=str(order_state.status),
    )


def _broker_order_text(order: PaperBrokerOrder) -> str:
    return (
        f"{order.symbol} {order.side} {order.quantity} 路 {order.status}"
    )


def _account_fingerprint(account: str) -> str:
    """Return the opaque internal identity used for Paper session binding."""

    return sha256(account.encode("utf-8")).hexdigest()


def _reconciliation_snapshot_digest(
    *,
    account_fingerprint: str,
    positions: tuple[PaperBrokerPosition, ...],
    open_orders: tuple[PaperBrokerOrder, ...],
    completed_orders: tuple[PaperBrokerOrder, ...],
    summary: ReconciliationSummary,
) -> str:
    payload = {
        "account_fingerprint": account_fingerprint,
        "positions": [
            (row.symbol, str(row.quantity), str(row.average_cost))
            for row in positions
        ],
        "open_orders": [
            (
                row.broker_order_id,
                row.symbol,
                row.side,
                str(row.quantity),
                row.status,
            )
            for row in open_orders
        ],
        "completed_orders": [
            (
                row.broker_order_id,
                row.symbol,
                row.side,
                str(row.quantity),
                row.status,
            )
            for row in completed_orders
        ],
        "reconciliation_summary": (
            summary.session_id,
            summary.total,
            summary.reconciled,
            summary.unreconciled,
            summary.terminal,
            summary.terminal_unreconciled,
            summary.nonterminal,
        ),
    }
    canonical = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _wait_before_deadline(event: Event, deadline: float) -> bool:
    remaining = deadline - monotonic()
    return remaining > 0 and event.wait(remaining)
