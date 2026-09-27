"""IBKR Live implementation of the provider-neutral execution port.

This adapter is intentionally not constructed by production composition in
Stage 4-C. It accepts only the local Live Gateway profile, one exact bound
account, whole-share stock LMT orders, and exact tracked-order cancellation.
The shared execution application still owns the durable-before-submit step.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from threading import Event, Lock, RLock, Thread
from time import monotonic
from typing import Any, Callable, Protocol

from us_quant.ibkr import (
    IBKRClientConnectError,
    IBKRConnectionConfig,
    connect_ibkr_client,
)
from us_quant.trading.adapters.ibkr.support import (
    INFORMATIONAL_ERROR_CODES,
    mask_account_id,
)
from us_quant.trading.adapters.order_status_mapping import order_status_from_text
from us_quant.trading.domain.live_safety import LiveAccountFingerprint
from us_quant.trading.domain.live_startup import LiveEndpointIdentity
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


class IBKRLiveExecutionError(ExecutionRefused):
    """The Live adapter refused an order before sending it."""


class IBKRLiveSubmissionUncertain(ExecutionSubmissionUncertain):
    """IBKR may have received a submit/cancel; stop and reconcile, never retry."""


class LiveOrderStorePort(Protocol):
    def max_broker_order_id(self) -> int: ...


class LiveGatewaySink(Protocol):
    def gateway_is_current(self, app: Any, epoch: int) -> bool: ...

    def gateway_next_valid_id(self, app: Any, epoch: int, order_id: int) -> None: ...

    def gateway_managed_accounts(self, app: Any, epoch: int, accounts: str) -> None: ...

    def gateway_error(self, app: Any, epoch: int, request_id: int, args: tuple[Any, ...]) -> None: ...

    def gateway_connection_closed(self, app: Any, epoch: int) -> None: ...

    def gateway_position(self, app: Any, epoch: int, account: str, contract: Any, quantity: Any, average_cost: Any) -> None: ...

    def gateway_position_end(self, app: Any, epoch: int) -> None: ...

    def gateway_order_status(self, app: Any, epoch: int, args: tuple[Any, ...]) -> None: ...

    def gateway_exec_details(self, app: Any, epoch: int, contract: Any, execution: Any) -> None: ...


def _create_live_gateway_app(*, sink: LiveGatewaySink, epoch: int) -> Any:
    """Create the small official-API callback bridge used by the Live adapter."""

    try:
        from ibapi.client import EClient
        from ibapi.wrapper import EWrapper
    except ImportError as error:
        raise IBKRLiveExecutionError("未安装 IBKR 官方 Python API") from error

    class LiveApp(EWrapper, EClient):
        def __init__(self) -> None:
            EWrapper.__init__(self)
            EClient.__init__(self, wrapper=self)

        def _current(self) -> bool:
            return sink.gateway_is_current(self, epoch)

        def nextValidId(self, orderId: int) -> None:
            if self._current():
                sink.gateway_next_valid_id(self, epoch, orderId)

        def managedAccounts(self, accountsList: str) -> None:
            if self._current():
                sink.gateway_managed_accounts(self, epoch, accountsList)

        def error(self, reqId: int, *args: Any) -> None:
            if self._current():
                sink.gateway_error(self, epoch, reqId, args)

        def connectionClosed(self) -> None:
            if self._current():
                sink.gateway_connection_closed(self, epoch)

        def position(self, account: str, contract: Any, position: Any, averageCost: Any) -> None:
            if self._current():
                sink.gateway_position(self, epoch, account, contract, position, averageCost)

        def positionEnd(self) -> None:
            if self._current():
                sink.gateway_position_end(self, epoch)

        def orderStatus(self, *args: Any) -> None:
            if self._current():
                sink.gateway_order_status(self, epoch, args)

        def execDetails(self, reqId: int, contract: Any, execution: Any) -> None:
            if self._current():
                sink.gateway_exec_details(self, epoch, contract, execution)

    return LiveApp()


def _aware_execution_time(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value or "").strip()
    for pattern in ("%Y%m%d  %H:%M:%S %Z", "%Y%m%d %H:%M:%S %Z"):
        try:
            parsed = datetime.strptime(text, pattern)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _whole_quantity(value: Decimal) -> bool:
    return value.is_finite() and value >= 0 and value == value.to_integral_value()


class IBKRLiveExecutionAdapter:
    """One-account IBKR Live adapter; intentionally absent from composition in 4-C."""

    def __init__(
        self,
        config: IBKRConnectionConfig,
        *,
        repository: LiveOrderStorePort,
        expected_account_fingerprint: LiveAccountFingerprint,
        allowed_symbols: frozenset[str],
        allowed_strategy_version_ids: frozenset[str],
        gateway_factory: Callable[..., Any] | None = None,
        client_connector: Callable[..., None] | None = None,
        contract_factory: Callable[[], Any] | None = None,
        order_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._validate_config(config)
        if not isinstance(expected_account_fingerprint, LiveAccountFingerprint):
            raise IBKRLiveExecutionError("必须绑定一个确切的 Live 账户指纹")
        if not allowed_symbols or any(not item.strip() for item in allowed_symbols):
            raise IBKRLiveExecutionError("Live adapter 必须显式绑定允许的标的")
        if not allowed_strategy_version_ids or any(
            not item.strip() for item in allowed_strategy_version_ids
        ):
            raise IBKRLiveExecutionError("Live adapter 必须显式绑定策略版本")
        if not callable(getattr(repository, "max_broker_order_id", None)):
            raise IBKRLiveExecutionError("Live adapter 需要订单号持久化下界")
        self.config = config
        self.repository = repository
        self.expected_account_fingerprint = expected_account_fingerprint
        self.allowed_symbols = frozenset(item.strip().upper() for item in allowed_symbols)
        self.allowed_strategy_version_ids = frozenset(
            item.strip() for item in allowed_strategy_version_ids
        )
        self._gateway_factory = gateway_factory or _create_live_gateway_app
        self._client_connector = client_connector or connect_ibkr_client
        self._contract_factory = contract_factory
        self._order_factory = order_factory
        self._client: Any | None = None
        self._thread: Thread | None = None
        self._epoch = 0
        self._connected = False
        self._halted = False
        self._halt_reason = ""
        self._account = ""
        self._managed_accounts: tuple[str, ...] = ()
        self._next_valid_id: int | None = None
        self._next_order_id: int | None = None
        self._positions: dict[str, Decimal] = {}
        self._reserved_sell_remaining: dict[int, int] = {}
        self._reserved_sell_by_symbol: dict[str, int] = {}
        self._execution_quantity_by_order: dict[int, Decimal] = {}
        self._intent_by_order: dict[int, OrderIntent] = {}
        self._order_by_intent: dict[str, int] = {}
        self._submitted_orders: set[int] = set()
        self._cancel_requested: set[str] = set()
        self._seen_executions: set[str] = set()
        self._updates: deque[OrderEvent] = deque()
        self._fills: deque[ExecutionFill] = deque()
        self._errors: list[str] = []
        self._next_id_ready = Event()
        self._accounts_ready = Event()
        self._positions_ready = Event()
        self._state_lock = RLock()
        self._event_lock = Lock()

    @staticmethod
    def _validate_config(config: IBKRConnectionConfig) -> None:
        if not isinstance(config, IBKRConnectionConfig):
            raise IBKRLiveExecutionError("IBKR Live 配置无效")
        try:
            endpoint = LiveEndpointIdentity(host=config.host, port=config.port)
        except ValueError as error:
            raise IBKRLiveExecutionError(str(error)) from error
        if endpoint.identity != "ibkr-live-loopback:127.0.0.1:4001":
            raise IBKRLiveExecutionError("仅支持 Stage 4 Live Gateway 配置")
        if config.api_read_only:
            raise IBKRLiveExecutionError("Live execution adapter 需要非只读 API 配置")
        if config.paper_order_submission_enabled:
            raise IBKRLiveExecutionError("Live adapter 不接受 Paper submission flag")

    @property
    def connected(self) -> bool:
        with self._state_lock:
            client = self._client
            return bool(
                self._connected
                and not self._halted
                and client is not None
                and client.isConnected()
            )

    @property
    def halted(self) -> bool:
        with self._state_lock:
            return self._halted

    @property
    def halt_reason(self) -> str:
        with self._state_lock:
            return self._halt_reason

    def connect(self) -> None:
        with self._state_lock:
            if self._halted:
                raise IBKRLiveExecutionError("Live adapter 已 HALT；必须先完成恢复对账")
            if self._connected and self._client is not None and self._client.isConnected():
                return
            self._epoch += 1
            epoch = self._epoch
            self._next_id_ready.clear()
            self._accounts_ready.clear()
            self._positions_ready.clear()
            self._errors.clear()
            self._managed_accounts = ()
            self._positions.clear()
            self._next_valid_id = None
            self._account = ""
        try:
            app = self._gateway_factory(sink=self, epoch=epoch)
            self._client = app
            self._client_connector(app, self.config)
            thread = Thread(target=app.run, name="ibkr-live-execution-network", daemon=True)
            self._thread = thread
            thread.start()
            deadline = monotonic() + self.config.connection_timeout_seconds
            if not self._wait_for_handshake(deadline):
                raise IBKRLiveExecutionError("等待 IBKR Live 账户握手超时")
            with self._state_lock:
                accounts = self._managed_accounts
                next_valid_id = self._next_valid_id
                errors = tuple(self._errors)
            if errors:
                raise IBKRLiveExecutionError("；".join(errors))
            if len(accounts) != 1:
                raise IBKRLiveExecutionError("Live Gateway 必须只返回一个 managed account")
            fingerprint = LiveAccountFingerprint.from_identity(
                provider="IBKR",
                environment="LIVE",
                account_id=accounts[0],
                endpoint_identity=LiveEndpointIdentity(host=self.config.host, port=self.config.port).identity,
            )
            if fingerprint != self.expected_account_fingerprint:
                raise IBKRLiveExecutionError("Live managed account 与授权账户指纹不匹配")
            if next_valid_id is None:
                raise IBKRLiveExecutionError("IBKR Live 未提供有效的下一个订单号")
            with self._state_lock:
                self._account = accounts[0]
                self._next_order_id = max(
                    next_valid_id,
                    self.repository.max_broker_order_id() + 1,
                )
            app.reqPositions()
            if not _wait_before_deadline(self._positions_ready, deadline):
                raise IBKRLiveExecutionError("等待 Live 持仓快照超时")
            with self._state_lock:
                if self._errors:
                    raise IBKRLiveExecutionError("；".join(self._errors))
                if any(not _whole_quantity(quantity) for quantity in self._positions.values()):
                    raise IBKRLiveExecutionError("Live 账户含有空头或 fractional 持仓")
                self._connected = True
        except (IBKRClientConnectError, TimeoutError, OSError) as error:
            self._disconnect_after_failed_connect()
            raise IBKRLiveExecutionError(f"IBKR Live 连接失败：{error}") from error
        except Exception:
            self._disconnect_after_failed_connect()
            raise

    def _wait_for_handshake(self, deadline: float) -> bool:
        return bool(
            _wait_before_deadline(self._next_id_ready, deadline)
            and _wait_before_deadline(self._accounts_ready, deadline)
        )

    def _disconnect_after_failed_connect(self) -> None:
        client = self._client
        if client is not None:
            try:
                if client.isConnected():
                    client.disconnect()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        with self._state_lock:
            self._connected = False
            self._client = None
            self._account = ""
            self._positions.clear()

    def reserve(self, intent: OrderIntent) -> BrokerOrderReservation:
        with self._state_lock:
            self._ensure_ready_locked()
            if intent.strategy_version_id not in self.allowed_strategy_version_ids:
                raise IBKRLiveExecutionError("策略版本未绑定到 Live adapter")
            if intent.execution_symbol not in self.allowed_symbols:
                raise IBKRLiveExecutionError("标的不在 Live adapter allowlist 中")
            if not intent.idempotency_key:
                raise IBKRLiveExecutionError("Live order intent 缺少幂等键")
            if (
                not isinstance(intent.quantity, int)
                or isinstance(intent.quantity, bool)
                or intent.quantity <= 0
            ):
                raise IBKRLiveExecutionError("Live 订单必须使用正整股数量")
            if not intent.limit_price.is_finite() or intent.limit_price <= 0:
                raise IBKRLiveExecutionError("Live LMT 价格必须为有限正数")
            existing = self._order_by_intent.get(intent.order_id)
            if existing is not None:
                if self._intent_by_order[existing] != intent:
                    raise IBKRLiveExecutionError(
                        "已存在的 Live order id 与新 intent 内容冲突"
                    )
                return BrokerOrderReservation(
                    order_id=intent.order_id,
                    broker_order_id=existing,
                    account_alias=mask_account_id(self._account),
                )
            if intent.side is Side.SELL:
                owned = int(self._positions.get(intent.execution_symbol, Decimal("0")))
                reserved = self._reserved_sell_by_symbol.get(intent.execution_symbol, 0)
                if intent.quantity > owned - reserved:
                    raise IBKRLiveExecutionError("Live SELL 超过已确认的 long 持仓")
            if self._next_order_id is None:
                raise IBKRLiveExecutionError("Live broker order id 尚未就绪")
            broker_order_id = self._next_order_id
            self._next_order_id += 1
            self._intent_by_order[broker_order_id] = intent
            self._order_by_intent[intent.order_id] = broker_order_id
            if intent.side is Side.SELL:
                self._reserved_sell_remaining[broker_order_id] = intent.quantity
                self._reserved_sell_by_symbol[intent.execution_symbol] = (
                    self._reserved_sell_by_symbol.get(intent.execution_symbol, 0)
                    + intent.quantity
                )
            return BrokerOrderReservation(
                order_id=intent.order_id,
                broker_order_id=broker_order_id,
                account_alias=mask_account_id(self._account),
            )

    def submit(self, reservation: BrokerOrderReservation) -> None:
        with self._state_lock:
            self._ensure_ready_locked()
            intent = self._intent_by_order.get(reservation.broker_order_id)
            if intent is None or intent.order_id != reservation.order_id:
                raise IBKRLiveExecutionError("Live reservation 与 order intent 不匹配")
            if reservation.account_alias != mask_account_id(self._account):
                raise IBKRLiveExecutionError("Live reservation 账户指纹发生变化")
            if reservation.broker_order_id in self._submitted_orders:
                raise IBKRLiveExecutionError("Live reservation 已提交，不得再次提交")
            client = self._client
            if client is None:
                raise IBKRLiveExecutionError("Live channel 已断开")
            try:
                contract = self._make_contract(intent.execution_symbol)
                order = self._make_order(intent, self._account)
            except IBKRLiveExecutionError:
                raise
            except Exception as error:
                raise IBKRLiveExecutionError(
                    f"Live order 构造失败，尚未提交：{error}"
                ) from error
            try:
                client.placeOrder(reservation.broker_order_id, contract, order)
            except Exception as error:
                self._halt_locked(f"Live submission uncertain: {error}")
                self._record_uncertain(intent, reservation.broker_order_id, str(error))
                raise IBKRLiveSubmissionUncertain(
                    "Live submission 结果不确定；禁止重试并要求对账",
                    order_id=intent.order_id,
                    broker_order_id=reservation.broker_order_id,
                    intent=intent,
                ) from error
            self._submitted_orders.add(reservation.broker_order_id)

    def _make_contract(self, symbol: str) -> Any:
        factory = self._contract_factory
        if factory is None:
            try:
                from ibapi.contract import Contract
            except ImportError as error:
                raise IBKRLiveExecutionError("未安装 IBKR 官方 Python API") from error
            factory = Contract
        contract = factory()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        return contract

    def _make_order(self, intent: OrderIntent, account: str) -> Any:
        factory = self._order_factory
        if factory is None:
            try:
                from ibapi.order import Order
            except ImportError as error:
                raise IBKRLiveExecutionError("未安装 IBKR 官方 Python API") from error
            factory = Order
        order = factory()
        order.action = intent.side.order_text
        order.orderType = "LMT"
        order.totalQuantity = int(intent.quantity)
        order.lmtPrice = float(intent.limit_price)
        order.tif = "DAY"
        order.outsideRth = False
        order.account = account
        order.transmit = True
        order.orderRef = f"USQ-LV-{intent.session_id[:8]}-{intent.order_id[:8]}"
        return order

    def cancel(self, order_id: str) -> bool:
        with self._state_lock:
            client = self._client
            if (
                client is None
                or not self._connected
                or not client.isConnected()
            ):
                raise IBKRLiveExecutionError("Live channel is disconnected")
            broker_order_id = self._order_by_intent.get(order_id)
            intent = (
                self._intent_by_order.get(broker_order_id)
                if broker_order_id is not None
                else None
            )
            if broker_order_id is None or intent is None:
                raise IBKRLiveExecutionError("Live cancellation requires a tracked order")
            if order_id in self._cancel_requested:
                return False
            self._cancel_requested.add(order_id)
            try:
                client.cancelOrder(broker_order_id, "")
            except Exception as error:
                self._cancel_requested.discard(order_id)
                self._halt_locked(f"Live cancellation uncertain: {error}")
                raise IBKRLiveSubmissionUncertain(
                    "Live cancel 结果不确定；必须停止并对账",
                    order_id=order_id,
                    broker_order_id=broker_order_id,
                    intent=intent,
                ) from error
        return True

    def events(self) -> tuple[OrderEvent, ...]:
        with self._event_lock:
            result = tuple(self._updates)
            self._updates.clear()
            return result

    def fills(self) -> tuple[ExecutionFill, ...]:
        with self._event_lock:
            result = tuple(self._fills)
            self._fills.clear()
            return result

    def disconnect(self) -> None:
        with self._state_lock:
            client = self._client
            self._connected = False
        if client is not None:
            try:
                if client.isConnected():
                    client.cancelPositions()
                    client.disconnect()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        with self._state_lock:
            self._client = None
            self._account = ""
            self._positions.clear()
            if self._intent_by_order:
                self._halt_locked("Live channel disconnected with tracked order state")

    def gateway_is_current(self, app: Any, epoch: int) -> bool:
        with self._state_lock:
            return app is self._client and epoch == self._epoch

    def gateway_next_valid_id(self, app: Any, epoch: int, order_id: int) -> None:
        if not self.gateway_is_current(app, epoch):
            return
        with self._state_lock:
            self._next_valid_id = int(order_id)
            self._next_id_ready.set()

    def gateway_managed_accounts(self, app: Any, epoch: int, accounts: str) -> None:
        if not self.gateway_is_current(app, epoch):
            return
        values = tuple(item.strip() for item in str(accounts).split(",") if item.strip())
        with self._state_lock:
            self._managed_accounts = values
            self._accounts_ready.set()

    def gateway_error(self, app: Any, epoch: int, request_id: int, args: tuple[Any, ...]) -> None:
        if not self.gateway_is_current(app, epoch):
            return
        code = 0
        message = "IBKR Live Gateway error"
        modern_error_time_signature = False
        if len(args) >= 3:
            try:
                modern_code = int(args[1])
            except (TypeError, ValueError):
                modern_code = None
            modern_error_time_signature = modern_code is not None and isinstance(args[2], str)
            if modern_error_time_signature:
                code = modern_code
                message = args[2]
        if not modern_error_time_signature and len(args) >= 2:
            try:
                code = int(args[0])
            except (TypeError, ValueError):
                code = 0
            message = str(args[1])
        if code in INFORMATIONAL_ERROR_CODES:
            return
        with self._state_lock:
            self._errors.append(f"IBKR {code}: {message}")
            intent = self._intent_by_order.get(request_id)
            if intent is not None:
                if code == 202:
                    status = OrderStatus.CANCELED
                elif code == 201:
                    status = OrderStatus.BROKER_REJECTED
                else:
                    status = OrderStatus.UNKNOWN
                event = OrderEvent(
                    order_id=intent.order_id,
                    status=status,
                    broker_order_id=request_id,
                    broker_status=f"Error {code}",
                    filled=self._execution_quantity_by_order.get(request_id, Decimal("0")),
                    remaining=Decimal(intent.quantity),
                    message=message,
                    idempotency_key=intent.idempotency_key,
                )
                self._append_event(event)

    def gateway_connection_closed(self, app: Any, epoch: int) -> None:
        if not self.gateway_is_current(app, epoch):
            return
        with self._state_lock:
            self._connected = False
            self._halt_locked("IBKR Live connection closed; reconcile before reuse")

    def gateway_position(
        self,
        app: Any,
        epoch: int,
        account: str,
        contract: Any,
        quantity: Any,
        average_cost: Any,
    ) -> None:
        if not self.gateway_is_current(app, epoch):
            return
        with self._state_lock:
            if self._account and account != self._account:
                self._errors.append("Live position snapshot contains another account")
                return
            symbol = str(getattr(contract, "symbol", "")).strip().upper()
            if not symbol:
                self._errors.append("Live position snapshot contains an unknown symbol")
                return
            try:
                self._positions[symbol] = Decimal(str(quantity))
            except (InvalidOperation, TypeError, ValueError):
                self._errors.append("Live position snapshot contains an invalid quantity")

    def gateway_position_end(self, app: Any, epoch: int) -> None:
        if self.gateway_is_current(app, epoch):
            self._positions_ready.set()

    def gateway_order_status(self, app: Any, epoch: int, args: tuple[Any, ...]) -> None:
        if not self.gateway_is_current(app, epoch) or len(args) < 4:
            return
        broker_id = int(args[0])
        raw_status = str(args[1])
        try:
            filled = Decimal(str(args[2]))
            remaining = Decimal(str(args[3]))
        except (InvalidOperation, TypeError, ValueError):
            filled, remaining = Decimal("0"), Decimal("0")
        with self._state_lock:
            intent = self._intent_by_order.get(broker_id)
            if intent is None:
                return
            status = order_status_from_text(raw_status)
            event = OrderEvent(
                order_id=intent.order_id,
                status=status,
                broker_order_id=broker_id,
                broker_status=raw_status,
                filled=filled,
                remaining=remaining,
                average_fill_price=_optional_decimal(args[4] if len(args) > 4 else None),
                last_fill_price=_optional_decimal(args[7] if len(args) > 7 else None),
                message="",
                idempotency_key=intent.idempotency_key,
                occurred_at=datetime.now(timezone.utc),
            )
            self._append_event(event)
            if status.is_terminal and broker_id in self._reserved_sell_remaining:
                executed = self._execution_quantity_by_order.get(broker_id, Decimal("0"))
                if executed < filled:
                    self._halt_locked("Terminal Live status arrived before its executions")
                self._release_sell_reservation_locked(broker_id)

    def gateway_exec_details(self, app: Any, epoch: int, contract: Any, execution: Any) -> None:
        if not self.gateway_is_current(app, epoch):
            return
        broker_id = int(getattr(execution, "orderId", 0) or 0)
        execution_id = str(getattr(execution, "execId", "")).strip()
        if not execution_id:
            self._halt("Live execution event has no execution id")
            return
        with self._state_lock:
            if execution_id in self._seen_executions:
                return
            intent = self._intent_by_order.get(broker_id)
            if intent is None:
                self._halt_locked("Untracked Live execution requires reconciliation")
                return
            execution_account = str(getattr(execution, "acctNumber", "")).strip()
            execution_symbol = str(getattr(contract, "symbol", "")).strip().upper()
            security_type = str(getattr(contract, "secType", "")).strip().upper()
            if execution_account != self._account:
                self._halt_locked("IBKR Live execution account differs from bound account")
                return
            if execution_symbol != intent.execution_symbol or security_type != "STK":
                self._halt_locked("IBKR Live execution contract differs from reserved stock intent")
                return
            try:
                quantity = Decimal(str(getattr(execution, "shares", "")))
                price = Decimal(str(getattr(execution, "price", "")))
            except (InvalidOperation, TypeError, ValueError):
                self._halt_locked("IBKR Live returned invalid execution facts")
                return
            if not _whole_quantity(quantity) or quantity <= 0 or not price.is_finite() or price <= 0:
                self._halt_locked("IBKR Live returned fractional or invalid fill facts")
                return
            raw_side = str(getattr(execution, "side", "")).strip().upper()
            expected_side = "BOT" if intent.side is Side.BUY else "SLD"
            if raw_side != expected_side:
                self._halt_locked("IBKR Live execution side differs from reserved intent")
                return
            cumulative_quantity = (
                self._execution_quantity_by_order.get(broker_id, Decimal("0")) + quantity
            )
            if cumulative_quantity > Decimal(intent.quantity):
                self._halt_locked("IBKR Live cumulative execution exceeds reserved quantity")
                return
            fill = ExecutionFill(
                execution_id=execution_id,
                order_id=intent.order_id,
                broker_order_id=broker_id,
                symbol=intent.execution_symbol,
                side=intent.side,
                quantity=quantity,
                price=price,
                occurred_at=_aware_execution_time(getattr(execution, "time", None)),
            )
            self._seen_executions.add(execution_id)
            self._execution_quantity_by_order[broker_id] = cumulative_quantity
            position_delta = quantity if intent.side is Side.BUY else -quantity
            next_position = self._positions.get(intent.execution_symbol, Decimal("0")) + position_delta
            if not _whole_quantity(next_position):
                self._halt_locked("Live fill would create a fractional or short position")
            self._positions[intent.execution_symbol] = next_position
            if intent.side is Side.SELL:
                self._decrement_sell_reservation_locked(broker_id, int(quantity))
            with self._event_lock:
                self._fills.append(fill)

    def _ensure_ready_locked(self) -> None:
        client = self._client
        if self._halted:
            raise IBKRLiveExecutionError(
                "Live adapter is HALTED: " + (self._halt_reason or "reconciliation required")
            )
        if not self._connected or client is None or not client.isConnected() or not self._account:
            raise IBKRLiveExecutionError("Live channel is disconnected or account-unverified")

    def _halt_locked(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason

    def _halt(self, reason: str) -> None:
        with self._state_lock:
            self._halt_locked(reason)

    def _record_uncertain(self, intent: OrderIntent, broker_id: int, detail: str) -> None:
        self._append_event(
            OrderEvent(
                order_id=intent.order_id,
                status=OrderStatus.UNKNOWN,
                broker_order_id=broker_id,
                broker_status="SubmitUncertain",
                filled=self._execution_quantity_by_order.get(broker_id, Decimal("0")),
                remaining=Decimal(intent.quantity),
                message="Live submit outcome is uncertain: " + detail,
                idempotency_key=intent.idempotency_key,
                occurred_at=datetime.now(timezone.utc),
            )
        )

    def _append_event(self, event: OrderEvent) -> None:
        with self._event_lock:
            self._updates.append(event)

    def _decrement_sell_reservation_locked(self, broker_id: int, filled: int) -> None:
        remaining = self._reserved_sell_remaining.get(broker_id, 0)
        decrement = min(remaining, filled)
        if decrement <= 0:
            return
        self._reserved_sell_remaining[broker_id] = remaining - decrement
        intent = self._intent_by_order[broker_id]
        self._reserved_sell_by_symbol[intent.execution_symbol] = max(
            0, self._reserved_sell_by_symbol.get(intent.execution_symbol, 0) - decrement
        )

    def _release_sell_reservation_locked(self, broker_id: int) -> None:
        remaining = self._reserved_sell_remaining.pop(broker_id, 0)
        intent = self._intent_by_order.get(broker_id)
        if intent is None:
            return
        self._reserved_sell_by_symbol[intent.execution_symbol] = max(
            0, self._reserved_sell_by_symbol.get(intent.execution_symbol, 0) - remaining
        )


def _optional_decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _wait_before_deadline(event: Event, deadline: float) -> bool:
    remaining = deadline - monotonic()
    return remaining > 0 and event.wait(remaining)


__all__ = [
    "IBKRLiveExecutionAdapter",
    "IBKRLiveExecutionError",
    "IBKRLiveSubmissionUncertain",
]
