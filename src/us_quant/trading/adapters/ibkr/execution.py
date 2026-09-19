"""IBKR Paper execution adapter: the broker side of ``BrokerExecutionPort``.

This is the Paper order service relocated into the trading layer, with one
boundary replaced.  The channel behaviour is deliberately *moved*, not
rewritten: the session binding, the account fingerprint, the connection-snapshot
(H-6) gate, the idempotency checks, the routing rules, the reconciliation
snapshot machinery and the gateway callback bridge are the ones that have been
running in Paper for months.  Re-inventing an order state machine here is how a
migration quietly changes what the broker is told.

What changed at the boundary:

* ``reserve`` and ``submit`` replace the single ``submit``.  Reserving allocates
  the broker order id and sends nothing; the caller writes the durable
  correlation and only then calls ``submit``.  The two-phase split exists so the
  "durable before the broker can see it" ordering cannot be collapsed by a
  future caller.
* ``OrderIntent`` / ``OrderEvent`` / ``ExecutionFill`` replace the Paper DTOs.
  The broker's raw status text still travels with each event -- reconciliation
  and the audit view read it -- but the mapped ``OrderStatus`` is what policy
  reads.
* The ``IBKR*`` error classes are subclasses of the port's provider-neutral
  errors, so a caller can catch ``ExecutionRefused`` /
  ``ExecutionSubmissionUncertain`` without importing anything from here.

Still frozen, because they are safety rules rather than implementation detail:
this host and port 4002 only, non-read-only config, whole-share US equity/ETF
limit (LMT) orders only for an explicitly armed DU session, no market order, no
short sale, no fractional share, no global cancel, no option and no
margin-borrowing surface.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from threading import Event, Lock, RLock, Thread
from time import monotonic
from typing import Any, Protocol

from us_quant.ibkr import (
    IBKRClientConnectError,
    IBKRConnectionConfig,
    connect_ibkr_client,
)
from us_quant.trading.adapters.ibkr.execution_gateway import (
    IBKRPaperGatewayError,
    PaperGatewayHandshake,
    create_paper_gateway_app,
)
from us_quant.trading.adapters.ibkr.support import (
    INFORMATIONAL_ERROR_CODES,
    mask_account_id,
)
from us_quant.extended_hours import paper_order_routing
from us_quant.paper_order_models import (
    PaperBrokerOrder,
    PaperBrokerPosition,
    PaperBrokerState,
    PaperOrderConnection,
    PaperOrderReconciliation,
    PaperReconciliationSnapshot,
    ReconciliationSummary,
    TERMINAL_ORDER_STATUSES,
)
from us_quant.trading.adapters.clock import now_iso
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
from us_quant.trading.ports.broker_execution import (
    BrokerOrderReservation,
    ExecutionRefused,
    ExecutionSubmissionUncertain,
)


class IBKRPaperOrderError(ExecutionRefused):
    """The IBKR Paper execution channel refused or failed.

    A subclass of the port's ``ExecutionRefused`` so callers above this module
    catch the provider-neutral type; the IBKR name stays in this file.
    """


class IBKRPaperOrderUncertainError(ExecutionSubmissionUncertain):
    """Submission may have reached IBKR and requires reconciliation."""

    def __init__(
        self, message: str, *, order_id: str, broker_order_id: int
    ) -> None:
        super().__init__(
            message, order_id=order_id, broker_order_id=broker_order_id
        )


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
    intent: OrderIntent,
    *,
    allowed_symbols: frozenset[str],
    max_order_notional: Decimal,
    sellable_quantities: dict[str, int],
) -> None:
    if intent.side.order_text not in {"BUY", "SELL"}:
        raise IBKRPaperOrderError("只允许 BUY 或 SELL")
    if intent.execution_symbol not in allowed_symbols:
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
        intent.side is Side.SELL
        and intent.quantity
        > sellable_quantities.get(intent.execution_symbol, 0)
    ):
        raise IBKRPaperOrderError("卖出数量超过本会话可卖整股")


class OrderStorePort(Protocol):
    """The store slice this adapter reads and reconciles against.

    Wider than ``OrderRepositoryPort`` on purpose: reconciliation compares the
    broker's open/completed orders against the stored rows, and the per-session
    rollup is a store query.  Declaring the slice here keeps the adapter honest
    about what it needs instead of typing the parameter as the concrete store,
    which would have made this module depend on a SQLite class.
    """

    def record_event(self, event: OrderEvent) -> None: ...

    def record_fill(self, fill: ExecutionFill) -> bool: ...

    def intent_for_broker_order(
        self, broker_order_id: int
    ) -> OrderIntent | None: ...

    def intent_for_idempotency_key(
        self, idempotency_key: str
    ) -> OrderIntent | None: ...

    def executed_quantity(self, order_id: str) -> Decimal: ...

    def max_broker_order_id(self) -> int: ...

    def reconciliation_rows(
        self, *, session_id: str | None = None, limit: int = 1000
    ) -> tuple[PaperOrderReconciliation, ...]: ...

    def reconciliation_summary(
        self, session_id: str | None = None
    ) -> ReconciliationSummary: ...

    def sessions(
        self, *, limit: int = 50
    ) -> tuple[dict[str, object], ...]: ...


class IBKRExecutionAdapter:
    """Narrow IBKR Paper execution adapter.

    Only whole-share US stock / ETF limit orders for an explicitly armed DU
    session are exposed. Regular routing is SMART DAY; optional 5x24 Paper
    routing uses OutsideRth for pre/after-hours and direct OVERNIGHT routing
    for the IBKR overnight session. There is no Live port, market order, short
    sale, global cancel, option or margin-borrowing surface.

    It satisfies ``BrokerExecutionPort`` and, beyond it, keeps the Paper
    session surface the runtime has always used: arming and disarming, the
    connection snapshot, the broker account read, the reconciliation snapshot
    and its currentness proof, and the per-session rollup.
    """

    def __init__(
        self,
        config: IBKRConnectionConfig,
        *,
        repository: OrderStorePort,
        extended_hours_enabled: bool = False,
    ) -> None:
        ensure_paper_order_config(config)
        self.config = config
        self.repository = repository
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
        self._updates: deque[OrderEvent] = deque()
        self._executions: deque[ExecutionFill] = deque()
        self._intent_by_order: dict[int, OrderIntent] = {}
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
        self._broker_observed_at = now_iso()
        self._submit_latency: dict[str, dict[str, str]] = {}
        self._refresh_lock = Lock()
        self._refresh_attempt: _ReconciliationRefreshAttempt | None = None
        # One connection-attempt truth: replaced by every connect().
        self._handshake = PaperGatewayHandshake()

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
            self.repository.max_broker_order_id() + 1
        )
        handshake = PaperGatewayHandshake()
        self._handshake = handshake
        errors = handshake.errors
        ready = handshake.ready
        accounts_ready = handshake.accounts_ready
        summary_ready = handshake.summary_ready
        positions_ready = handshake.positions_ready
        open_orders_ready = handshake.open_orders_ready
        completed_orders_ready = handshake.completed_orders_ready
        executions_ready = handshake.executions_ready
        try:
            app = create_paper_gateway_app(
                sink=self, epoch=connection_epoch
            )
        except IBKRPaperGatewayError as error:
            raise IBKRPaperOrderError(str(error)) from error
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
        reconciliation = self.repository.reconciliation_summary()
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
        return self.repository.reconciliation_summary(session_id)

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
            self._broker_observed_at = now_iso()
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
        summary = self.repository.reconciliation_summary(attempt.session_id)
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
            self._connection_observed_at = now_iso()

    # ------------------------------------------------------------------
    # IBKR callback handlers.
    #
    # The bridge in ``us_quant.ibkr_paper_gateway`` only forwards callbacks;
    # everything below is what a callback *means* for the Paper session.
    # These bodies were relocated verbatim from the nested ``PaperApp`` class,
    # so the ordering of the state writes is unchanged.
    # ------------------------------------------------------------------

    def gateway_is_current(self, app: Any, epoch: int) -> bool:
        """Whether a callback still belongs to the live physical connection.

        The bridge asks this before forwarding anything; the judgement itself
        stays here, where the client identity and the epoch both live.
        """

        return self._is_current_connection(app, epoch)

    def gateway_next_valid_id(
        self, app: Any, epoch: int, order_id: int
    ) -> None:
        # CR-4 fix: never let a Gateway restart regress the order-id
        # counter.  IBKR order IDs must stay strictly increasing so a
        # reused ID can never amend/cancel an earlier journal order.
        self._next_order_id = max(
            int(order_id), self._next_order_id_floor
        )
        self._mark_state_changed()
        self._handshake.ready.set()

    def gateway_managed_accounts(
        self, app: Any, epoch: int, accounts_list: str
    ) -> None:
        accounts = tuple(
            row.strip()
            for row in accounts_list.split(",")
            if row.strip()
        )
        if len(accounts) != 1:
            self._handshake.errors.append(
                "Paper 自动量化要求 Gateway 只返回一个账户"
            )
        elif not accounts[0].upper().startswith("DU"):
            self._handshake.errors.append("拒绝非 DU 账户：Live 永久阻断")
        else:
            self._account = accounts[0]
            self._mark_state_changed()
        self._handshake.accounts_ready.set()

    def gateway_error(
        self,
        app: Any,
        epoch: int,
        req_id: int,
        args: tuple[Any, ...],
    ) -> None:
        if len(args) >= 4:
            _, errorCode, errorString, *_ = args
        elif len(args) >= 2:
            errorCode, errorString, *_ = args
        else:
            return
        errorCode = int(errorCode)
        if errorCode not in INFORMATIONAL_ERROR_CODES:
            with self._correlation_lock:
                known_order = req_id in self._intent_by_order
            if not known_order:
                self._handshake.errors.append(
                    f"{errorCode}: {str(errorString)}"
                )
            self._record_error(
                req_id, errorCode, str(errorString)
            )

    def gateway_order_status(
        self,
        app: Any,
        epoch: int,
        order_id: int,
        status: str,
        filled: Any,
        remaining: Any,
        average_fill_price: Any,
        perm_id: int,
        parent_id: int,
        last_fill_price: Any,
        client_id: int,
        why_held: str,
        market_cap_price: Any,
    ) -> None:
        del perm_id, parent_id, client_id, market_cap_price
        self._record_order_status(
            orderId=order_id,
            status=status,
            filled=Decimal(str(filled)),
            remaining=Decimal(str(remaining)),
            average_fill_price=(
                Decimal(str(average_fill_price))
                if average_fill_price > 0
                else None
            ),
            last_fill_price=(
                Decimal(str(last_fill_price))
                if last_fill_price > 0
                else None
            ),
            message=why_held or "",
        )

    def gateway_open_order(
        self,
        app: Any,
        epoch: int,
        order_id: int,
        contract: Any,
        order: Any,
        order_state: Any,
    ) -> None:
        attempt = self._refresh_attempt_for(app, epoch)
        broker_order = _paper_broker_order(
            order_id, contract, order, order_state
        )
        if attempt is not None:
            attempt.open_orders[broker_order.broker_order_id] = (
                broker_order
            )
            return
        self._recover_intent_mapping(order_id)
        with self._state_lock:
            self._open_broker_orders[int(order_id)] = (
                f"{str(contract.symbol).upper()} "
                f"{str(order.action).upper()} "
                f"{order.totalQuantity} · "
                f"{str(order_state.status)}"
            )
            self._broker_observed_at = now_iso()
            self._mark_state_changed_locked()

    def gateway_open_order_end(self, app: Any, epoch: int) -> None:
        attempt = self._refresh_attempt_for(app, epoch)
        if attempt is not None:
            attempt.open_orders_ready.set()
            return
        self._handshake.open_orders_ready.set()

    def gateway_account_summary(
        self,
        app: Any,
        epoch: int,
        req_id: int,
        account: str,
        tag: str,
        value: str,
        currency: str,
    ) -> None:
        del currency
        if account != self._account:
            return
        try:
            parsed = Decimal(value)
        except Exception:
            return
        attempt = self._refresh_attempt_for(app, epoch)
        if attempt is not None and req_id == 91_101:
            attempt.account_metrics[tag] = parsed
            return
        with self._state_lock:
            self._account_metrics[tag] = parsed
            self._broker_observed_at = now_iso()
            self._mark_state_changed_locked()

    def gateway_account_summary_end(
        self, app: Any, epoch: int, req_id: int
    ) -> None:
        attempt = self._refresh_attempt_for(app, epoch)
        if attempt is not None and req_id == 91_101:
            attempt.summary_ready.set()
            return
        if req_id == 91_001:
            self._handshake.summary_ready.set()

    def gateway_position(
        self,
        app: Any,
        epoch: int,
        account: str,
        contract: Any,
        position: Any,
        average_cost: Any,
    ) -> None:
        if account != self._account:
            return
        symbol = str(contract.symbol).upper()
        row = PaperBrokerPosition(
            symbol=symbol,
            quantity=Decimal(str(position)),
            average_cost=Decimal(str(average_cost)),
        )
        attempt = self._refresh_attempt_for(app, epoch)
        if attempt is not None:
            if row.quantity != 0:
                attempt.positions[symbol] = row
            return
        with self._state_lock:
            if row.quantity == 0:
                self._broker_positions.pop(
                    symbol, None
                )
            else:
                self._broker_positions[symbol] = row
            self._broker_observed_at = now_iso()
            self._mark_state_changed_locked()

    def gateway_position_end(self, app: Any, epoch: int) -> None:
        attempt = self._refresh_attempt_for(app, epoch)
        if attempt is not None:
            attempt.positions_ready.set()
            return
        self._handshake.positions_ready.set()

    def gateway_pnl(
        self,
        app: Any,
        epoch: int,
        req_id: int,
        daily_pnl: Any,
        unrealized_pnl: Any,
        realized_pnl: Any,
    ) -> None:
        with self._state_lock:
            self._daily_pnl = _optional_decimal(
                daily_pnl
            )
            self._unrealized_pnl = _optional_decimal(
                unrealized_pnl
            )
            self._realized_pnl = _optional_decimal(
                realized_pnl
            )
            self._broker_observed_at = now_iso()
            self._mark_state_changed_locked()

    def gateway_exec_details(
        self,
        app: Any,
        epoch: int,
        req_id: int,
        contract: Any,
        execution: Any,
    ) -> None:
        attempt = self._refresh_attempt_for(app, epoch)
        if attempt is not None and req_id == 91_103:
            attempt.executions.append((contract, execution))
            return
        self._record_execution(contract, execution)

    def gateway_exec_details_end(
        self, app: Any, epoch: int, req_id: int
    ) -> None:
        attempt = self._refresh_attempt_for(app, epoch)
        if attempt is not None and req_id == 91_103:
            attempt.executions_ready.set()
            return
        if req_id == 91_003:
            self._handshake.executions_ready.set()

    def gateway_completed_order(
        self,
        app: Any,
        epoch: int,
        contract: Any,
        order: Any,
        order_state: Any,
    ) -> None:
        order_id = int(order.orderId)
        attempt = self._refresh_attempt_for(app, epoch)
        broker_order = _paper_broker_order(
            order_id, contract, order, order_state
        )
        if attempt is not None:
            attempt.completed_orders[order_id] = broker_order
            return
        self._recover_intent_mapping(order_id)
        with self._state_lock:
            self._completed_broker_orders[order_id] = (
                str(order_state.status),
                Decimal(str(order.totalQuantity)),
            )
            self._broker_observed_at = now_iso()
            self._mark_state_changed_locked()

    def gateway_completed_orders_end(
        self, app: Any, epoch: int
    ) -> None:
        attempt = self._refresh_attempt_for(app, epoch)
        if attempt is not None:
            attempt.completed_orders_ready.set()
            return
        self._handshake.completed_orders_ready.set()

    def gateway_connection_closed(
        self, app: Any, epoch: int
    ) -> None:
        self._connected = False
        self._invalidate_connection_snapshot()


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

    def reserve(self, intent: OrderIntent) -> BrokerOrderReservation:
        """Allocate a broker order id for one intent; send nothing.

        Every hard gate runs here, because reserving an id for an order this
        session is not allowed to send would only move the refusal later and
        leave a durable row for an order that never existed.  The caller writes
        the returned correlation to its store and only then calls ``submit``.
        """

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
        if not intent.idempotency_key:
            raise IBKRPaperOrderError("订单意图缺少幂等键")
        existing = self.repository.intent_for_idempotency_key(
            intent.idempotency_key
        )
        if existing is not None and existing.order_id != intent.order_id:
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
        if (
            self._client is None
            or not self.connection_snapshot().connected
            or not account
        ):
            raise IBKRPaperOrderError("Paper 订单通道已断开")
        with self._correlation_lock:
            reserved = self._order_by_intent.get(intent.order_id)
        if reserved is not None:
            # The same order identity already holds a broker id: reuse it
            # rather than burning a second one for a duplicate submission.
            return BrokerOrderReservation(
                order_id=intent.order_id,
                broker_order_id=reserved,
                account_alias=mask_account_id(account),
            )
        with self._id_lock:
            if self._next_order_id is None:
                raise IBKRPaperOrderError("尚未获得有效订单号")
            order_id = self._next_order_id
            self._next_order_id += 1
        with self._correlation_lock:
            self._intent_by_order[order_id] = intent
            self._order_by_intent[intent.order_id] = order_id
        return BrokerOrderReservation(
            order_id=intent.order_id,
            broker_order_id=order_id,
            account_alias=mask_account_id(account),
        )

    def submit(self, reservation: BrokerOrderReservation) -> None:
        """Send one reserved order to IBKR.

        The caller must already have written the durable correlation: this
        method exists after that write, and it places the order with the id the
        correlation names.  If the place call fails the outcome is genuinely
        unknown, so the order is reported as uncertain and the caller halts for
        reconciliation instead of retrying.
        """

        with self._correlation_lock:
            intent = self._intent_by_order.get(reservation.broker_order_id)
        if intent is None:
            raise IBKRPaperOrderError(
                "订单意图未处于已预留状态；拒绝提交"
            )
        with self._state_lock:
            account = self._account
            snapshot_complete = self._snapshot_complete
        if not snapshot_complete:
            raise IBKRPaperOrderError(
                "Paper 连接快照尚未完成；拒绝提交订单"
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

        order_id = reservation.broker_order_id
        contract = Contract()
        contract.symbol = intent.execution_symbol
        contract.secType = "STK"
        contract.exchange = routing.exchange
        contract.currency = "USD"
        order = Order()
        order.action = intent.side.order_text
        order.orderType = "LMT"
        order.totalQuantity = Decimal(intent.quantity)
        order.lmtPrice = float(intent.limit_price)
        order.tif = routing.tif
        order.outsideRth = routing.outside_rth
        order.account = account
        order.transmit = True
        order.orderRef = (
            f"USQ-{intent.session_id[:8]}-{intent.order_id[:8]}-"
            f"{routing.session.value[:3].upper()}"
        )
        submitted_at = now_iso()
        submit_error: Exception | None = None
        with self._correlation_lock:
            self._submit_latency[intent.order_id] = {
                "intent_generated_at": intent.created_at.isoformat(),
                "submitted_at": submitted_at,
            }
            try:
                client.placeOrder(order_id, contract, order)
            except Exception as error:
                self._submit_latency.pop(intent.order_id, None)
                submit_error = error
        if submit_error is None:
            return
        event = OrderEvent(
            order_id=intent.order_id,
            status=OrderStatus.UNKNOWN,
            broker_order_id=order_id,
            broker_status="SubmitUncertain",
            filled=Decimal("0"),
            remaining=Decimal(intent.quantity),
            average_fill_price=None,
            last_fill_price=None,
            message=(
                "本地提交调用异常；订单是否到达券商尚不确定："
                f"{submit_error}"
            ),
            idempotency_key=intent.idempotency_key,
            occurred_at=datetime.now(timezone.utc),
        )
        with self._event_lock:
            self._updates.append(event)
        self.repository.record_event(event)
        raise IBKRPaperOrderUncertainError(
            "Paper 订单提交结果不确定；会话必须停机并对账",
            order_id=intent.order_id,
            broker_order_id=order_id,
        ) from submit_error

    def cancel_intent(self, order_id: str) -> bool:
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
            broker_order_id = self._order_by_intent.get(order_id)
            intent = (
                self._intent_by_order.get(broker_order_id)
                if broker_order_id is not None
                else None
            )
            if broker_order_id is None or intent is None:
                raise IBKRPaperOrderError(
                    "找不到本会话对应的 Paper 订单"
                )
            if order_id in self._cancel_requested:
                return False
            self._cancel_requested.add(order_id)
            try:
                client.cancelOrder(broker_order_id, "")
            except Exception as error:
                self._cancel_requested.discard(order_id)
                raise IBKRPaperOrderUncertainError(
                    "Paper 撤单结果不确定；必须停止并对账",
                    order_id=order_id,
                    broker_order_id=broker_order_id,
                ) from error
        return True

    def events(self) -> tuple[OrderEvent, ...]:
        with self._event_lock:
            rows = tuple(self._updates)
            self._updates.clear()
        return rows

    def fills(self) -> tuple[ExecutionFill, ...]:
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
            self.repository.executed_quantity(intent.order_id)
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
    ) -> OrderIntent | None:
        with self._correlation_lock:
            intent = self._intent_by_order.get(broker_order_id)
        if intent is None:
            intent = self.repository.intent_for_broker_order(
                broker_order_id
            )
            if intent is not None:
                with self._correlation_lock:
                    self._intent_by_order[broker_order_id] = intent
                    self._order_by_intent[
                        intent.order_id
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
            executed = self.repository.executed_quantity(
                intent.order_id
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
                self._cancel_requested.discard(intent.order_id)
        event = OrderEvent(
            order_id=intent.order_id,
            status=order_status_from_text(status),
            broker_order_id=broker_order_id,
            broker_status=status,
            filled=filled,
            remaining=remaining,
            average_fill_price=average_fill_price,
            last_fill_price=last_fill_price,
            message=message,
            idempotency_key=intent.idempotency_key,
            occurred_at=datetime.now(timezone.utc),
        )
        self.repository.record_event(event)
        self._mark_state_changed()
        with self._event_lock:
            self._updates.append(event)
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
        fill = ExecutionFill(
                order_id=intent.order_id,
                broker_order_id=order_id,
                execution_id=str(execution.execId),
                symbol=str(contract.symbol).upper(),
                side=(
                    Side.BUY
                    if str(execution.side).upper() in {"BOT", "BUY"}
                    else Side.SELL
                ),
                quantity=Decimal(str(execution.shares)),
                price=Decimal(str(execution.price)),
                occurred_at=_execution_time(execution.time),
        )
        if not self.repository.record_fill(fill):
            return
        self._mark_state_changed()
        whole_quantity = int(fill.quantity)
        if Decimal(whole_quantity) == fill.quantity:
            # The sellable count is whole-share bookkeeping, so it only moves
            # for a whole-share fill.  A fractional execution is still recorded
            # and still reaches the runtime, which halts on it: silently
            # rounding here would leave the local book quietly wrong.
            with self._state_lock:
                if fill.side is Side.BUY:
                    self._sellable_quantities[
                        fill.symbol
                    ] = (
                        self._sellable_quantities.get(
                            fill.symbol, 0
                        )
                        + whole_quantity
                    )
                else:
                    self._sellable_quantities[
                        fill.symbol
                    ] = max(
                        0,
                        self._sellable_quantities.get(
                            fill.symbol, 0
                        )
                        - whole_quantity,
                    )
        with self._event_lock:
            self._executions.append(fill)
        with self._correlation_lock:
            self._submit_latency.pop(intent.order_id, None)

    def submit_latency(self, order_id: str) -> dict[str, str] | None:
        with self._correlation_lock:
            return self._submit_latency.get(order_id)

    def reconciliation_rows_with_latency(
        self, *, session_id: str | None = None, limit: int = 1000
    ) -> tuple[dict, ...]:
        rows = self.repository.reconciliation_rows(
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
        """Compatibility delegate; the order store owns this query.

        Kept because callers outside this adapter cannot be ruled out. The
        adapter deliberately holds no SQLite handle of its own.
        """

        return self.repository.sessions(limit=limit)


def _execution_time(value: object) -> datetime:
    """IBKR's execution timestamp as a ``datetime``.

    IBKR reports the time as text -- either an ISO instant or its own
    ``YYYYMMDD HH:MM:SS`` form -- so both are accepted.  A value that matches
    neither becomes the observation time rather than being stored as unreadable
    text: the fill is real either way, and a timestamp the store can never parse
    back would make the row useless to reconciliation later.
    """

    text = str(value).strip()
    for parser in (
        lambda raw: datetime.fromisoformat(raw.replace("Z", "+00:00")),
        lambda raw: datetime.strptime(raw, "%Y%m%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ),
        lambda raw: datetime.strptime(raw, "%Y%m%d  %H:%M:%S").replace(
            tzinfo=timezone.utc
        ),
    ):
        try:
            return parser(text)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


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
