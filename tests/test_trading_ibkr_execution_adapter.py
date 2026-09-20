from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Thread
from types import ModuleType, SimpleNamespace
from typing import Any, Callable
import unittest
from unittest.mock import patch

from us_quant.extended_hours import PaperOrderRouting, USEquitySession
from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.adapters.ibkr.execution import (
    IBKRExecutionAdapter,
    IBKRPaperOrderError,
    IBKRPaperOrderUncertainError,
    _ReconciliationRefreshAttempt,
    ensure_paper_order_config,
    validate_paper_order_intent,
)
from us_quant.trading.adapters.ibkr.execution_gateway import (
    PaperGatewaySink,
    create_paper_gateway_app,
)
from us_quant.trading.adapters.sqlite.order_repository import (
    SQLiteOrderRepository,
)
from us_quant.trading.composition.execution import (
    build_execution_application,
    build_execution_candidate,
    build_order_repository,
)
from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
    OrderStatus,
    Side,
)
from us_quant.trading.domain.risk import RiskDecision
from us_quant.trading.domain.strategy import (
    StrategyIdentity,
    TradeAction,
    TradeProposal,
)
from us_quant.trading.ports.broker_execution import (
    BrokerOrderReservation,
    ExecutionSubmissionUncertain,
)


#: The adapter reads routing through its own module namespace, so the tests
#: that need a legal route patch it there rather than depend on wall-clock time.
_ROUTING_TARGET = (
    "us_quant.trading.adapters.ibkr.execution.paper_order_routing"
)

_OCCURRED = datetime.fromisoformat("2026-07-26T12:00:00+00:00")


def _allow_routing(*, extended_hours_enabled: bool = False) -> PaperOrderRouting:
    """A permanently-open regular session, so reserve/submit can run in a test."""

    return PaperOrderRouting(
        session=USEquitySession.REGULAR,
        label="regular",
        exchange="SMART",
        tif="DAY",
        outside_rth=False,
        allowed=True,
        reason="test regular session",
    )


def _config(
    *, client_id: int = 81, timeout: float = 5
) -> IBKRConnectionConfig:
    return IBKRConnectionConfig(
        host="127.0.0.1",
        port=4002,
        client_id=client_id,
        api_read_only=False,
        paper_order_submission_enabled=True,
        connection_timeout_seconds=timeout,
    )


def _repository(directory: str) -> SQLiteOrderRepository:
    return SQLiteOrderRepository(Path(directory) / "orders.sqlite3")


def _intent(
    *,
    session_id: str = "session",
    symbol: str = "AAPL",
    side: Side = Side.BUY,
    quantity: int = 1,
    limit_price: Decimal = Decimal("200"),
    reason: str = "test",
) -> OrderIntent:
    return OrderIntent.create(
        session_id=session_id,
        strategy_version_id="version",
        signal_symbol=symbol,
        execution_symbol=symbol,
        side=side,
        quantity=quantity,
        limit_price=limit_price,
        reason=reason,
    )


class _ConnectedClient:
    """A broker client that records what the adapter asks it to do."""

    def __init__(self) -> None:
        self.placed: list[Any] = []
        self.cancelled: list[tuple[int, str]] = []

    def isConnected(self) -> bool:
        return True

    def serverVersion(self) -> int:
        return 200

    def twsConnectionTime(self) -> str:
        return "paper"

    def placeOrder(self, order_id, contract, order) -> None:
        self.placed.append((order_id, contract, order))

    def cancelOrder(self, order_id: int, manual_time: str) -> None:
        self.cancelled.append((order_id, manual_time))


class _BlockingClient(_ConnectedClient):
    """A client that parks inside ``placeOrder``.

    Holding the send open is the only way to observe the submission critical
    section from outside: another thread can then try to change the state and
    the test can tell whether the change was ordered before the send, after
    it, or inserted into the middle of it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.send_started = Event()
        self.send_release = Event()

    def placeOrder(self, order_id, contract, order) -> None:
        self.placed.append((order_id, contract, order))
        self.send_started.set()
        self.send_release.wait(5)


def _ibapi_stub_modules() -> dict[str, ModuleType]:
    """The minimum ``ibapi`` surface the gateway factory and submit need.

    CI installs no real IBKR API, and skipping the tests that need one would
    stop testing the interface the adapter actually uses.  ``Contract`` and
    ``Order`` are attribute holders: ``submit`` still builds a real order object
    out of an intent and still reaches ``client.placeOrder``, which is the
    behaviour under test.  Nothing here may make a refusal look like success.
    """

    class StubEClient:
        def __init__(self, wrapper=None) -> None:
            self.wrapper = wrapper

    class StubContract:
        pass

    class StubOrder:
        pass

    client_module = ModuleType("ibapi.client")
    client_module.EClient = StubEClient
    wrapper_module = ModuleType("ibapi.wrapper")
    wrapper_module.EWrapper = type("StubEWrapper", (), {})
    contract_module = ModuleType("ibapi.contract")
    contract_module.Contract = StubContract
    order_module = ModuleType("ibapi.order")
    order_module.Order = StubOrder
    return {
        "ibapi": ModuleType("ibapi"),
        "ibapi.client": client_module,
        "ibapi.wrapper": wrapper_module,
        "ibapi.contract": contract_module,
        "ibapi.order": order_module,
    }



class IBKRPaperOrderTests(unittest.TestCase):
    def test_only_local_paper_order_config_is_accepted(self) -> None:
        ensure_paper_order_config(_config())
        with self.assertRaises((ValueError, IBKRPaperOrderError)):
            ensure_paper_order_config(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4001,
                    client_id=81,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=5,
                )
            )

    def test_whole_share_limit_and_session_caps(self) -> None:
        intent = _intent(quantity=2, limit_price=Decimal("200"))
        validate_paper_order_intent(
            intent,
            allowed_symbols=frozenset({"AAPL"}),
            max_order_notional=Decimal("500"),
            sellable_quantities={},
        )
        invalid = _intent(
            quantity=3, limit_price=Decimal("200"), reason="too large"
        )
        with self.assertRaises(IBKRPaperOrderError):
            validate_paper_order_intent(
                invalid,
                allowed_symbols=frozenset({"AAPL"}),
                max_order_notional=Decimal("500"),
                sellable_quantities={},
            )

    def test_sell_cannot_exceed_armed_position(self) -> None:
        intent = _intent(
            side=Side.SELL, quantity=2, limit_price=Decimal("200")
        )
        with self.assertRaises(IBKRPaperOrderError):
            validate_paper_order_intent(
                intent,
                allowed_symbols=frozenset({"AAPL"}),
                max_order_notional=Decimal("1000"),
                sellable_quantities={"AAPL": 1},
            )

    def test_journal_masks_account_and_rejects_duplicate_intent(self) -> None:
        with TemporaryDirectory() as directory:
            journal = _repository(directory)
            intent = _intent()
            journal.record_intent(
                intent,
                broker_order_id=10,
                account_alias="DU***17",
            )
            with self.assertRaises(Exception):
                journal.record_intent(
                    intent,
                    broker_order_id=11,
                    account_alias="DU***17",
                )

    def test_journal_max_broker_order_id_floor(self) -> None:
        """CR-4: the journal reports the highest ever order id so a Gateway
        restart can never regress nextValidId into reused ids."""
        with TemporaryDirectory() as directory:
            journal = _repository(directory)
            self.assertEqual(journal.max_broker_order_id(), 0)
            for order_id in (10, 42, 17):
                intent = _intent()
                journal.record_intent(
                    intent,
                    broker_order_id=order_id,
                    account_alias="DU***17",
                )
            self.assertEqual(journal.max_broker_order_id(), 42)

    def test_execution_is_idempotent_and_reconciliation_is_explicit(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            journal = _repository(directory)
            intent = _intent(quantity=2, limit_price=Decimal("200"))
            journal.record_intent(
                intent,
                broker_order_id=10,
                account_alias="DU***17",
            )
            journal.record_event(
                OrderEvent(
                    order_id=intent.order_id,
                    status=OrderStatus.FILLED,
                    broker_order_id=10,
                    broker_status="Filled",
                    filled=Decimal("2"),
                    remaining=Decimal("0"),
                    average_fill_price=Decimal("200"),
                    last_fill_price=Decimal("200"),
                    message="",
                    idempotency_key=intent.idempotency_key,
                    occurred_at=_OCCURRED,
                )
            )
            before = journal.reconciliation_rows(
                session_id="session"
            )[0]
            self.assertFalse(before.reconciled)
            self.assertIn("未对齐", before.reason)
            execution = ExecutionFill(
                execution_id="execution-1",
                order_id=intent.order_id,
                broker_order_id=10,
                symbol="AAPL",
                side=Side.BUY,
                quantity=Decimal("2"),
                price=Decimal("200"),
                occurred_at=_OCCURRED,
            )
            self.assertTrue(journal.record_fill(execution))
            self.assertFalse(journal.record_fill(execution))
            after = journal.reconciliation_rows(
                session_id="session"
            )[0]
            self.assertTrue(after.reconciled)
            self.assertEqual(after.executed_quantity, Decimal("2"))
            self.assertEqual(len(journal.execution_rows()), 1)

    def test_reconciliation_summary_is_exact_and_session_scoped(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            journal = _repository(directory)

            def record(session_id: str, order_id: int):
                intent = _intent(
                    session_id=session_id, reason="summary"
                )
                journal.record_intent(
                    intent,
                    broker_order_id=order_id,
                    account_alias="DU***17",
                )
                return intent

            reconciled = record("session-a", 1)
            journal.record_event(
                OrderEvent(
                    order_id=reconciled.order_id,
                    status=OrderStatus.FILLED,
                    broker_order_id=1,
                    broker_status="Filled",
                    filled=Decimal("1"),
                    remaining=Decimal("0"),
                    average_fill_price=Decimal("200"),
                    last_fill_price=Decimal("200"),
                    message="",
                    idempotency_key=reconciled.idempotency_key,
                    occurred_at=_OCCURRED,
                )
            )
            journal.record_fill(
                ExecutionFill(
                    execution_id="summary-execution",
                    order_id=reconciled.order_id,
                    broker_order_id=1,
                    symbol="AAPL",
                    side=Side.BUY,
                    quantity=Decimal("1"),
                    price=Decimal("200"),
                    occurred_at=_OCCURRED,
                )
            )
            terminal_unreconciled = record("session-a", 2)
            journal.record_event(
                OrderEvent(
                    order_id=terminal_unreconciled.order_id,
                    status=OrderStatus.BROKER_REJECTED,
                    broker_order_id=2,
                    broker_status="Error",
                    filled=Decimal("1"),
                    remaining=Decimal("0"),
                    average_fill_price=None,
                    last_fill_price=None,
                    message="",
                    idempotency_key=terminal_unreconciled.idempotency_key,
                    occurred_at=_OCCURRED,
                )
            )
            record("session-a", 3)
            for order_id in range(4, 1006):
                record("session-b", order_id)

            summary = journal.reconciliation_summary("session-a")
            self.assertEqual(summary.total, 3)
            self.assertEqual(summary.reconciled, 1)
            self.assertEqual(summary.unreconciled, 2)
            self.assertEqual(summary.terminal, 2)
            self.assertEqual(summary.terminal_unreconciled, 1)
            self.assertEqual(summary.nonterminal, 1)
            self.assertEqual(
                journal.reconciliation_summary("session-b").total,
                1002,
            )
            self.assertEqual(len(journal.reconciliation_rows()), 1000)

            service = IBKRExecutionAdapter(
                _config(), repository=journal
            )
            service._connected = True
            service._client = _ConnectedClient()
            self.assertEqual(
                service.connection_snapshot().unreconciled_local_orders,
                1004,
            )

    def test_connection_snapshot_generation_requires_complete_snapshot(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            service = IBKRExecutionAdapter(
                _config(), repository=_repository(directory)
            )
            service._connected = True
            service._client = _ConnectedClient()
            incomplete = service.connection_snapshot()
            self.assertEqual(incomplete.connection_generation, 0)
            self.assertFalse(incomplete.snapshot_complete)

            service._publish_complete_connection_snapshot()
            completed = service.connection_snapshot()
            self.assertEqual(completed.connection_generation, 1)
            self.assertTrue(completed.snapshot_complete)
            self.assertTrue(completed.observed_at)
            self.assertEqual(
                service.connection_snapshot().connection_generation, 1
            )

            service._invalidate_connection_snapshot()
            failed = service.connection_snapshot()
            self.assertEqual(failed.connection_generation, 1)
            self.assertFalse(failed.snapshot_complete)
            self.assertEqual(failed.observed_at, "")

    def test_refresh_snapshot_uses_fresh_callbacks_and_guards_epochs(
        self,
    ) -> None:
        clients = []

        class FakeEClient:
            def __init__(self, wrapper) -> None:
                self.wrapper = wrapper
                self.connected = False
                self.refresh_started = Event()
                self.hold_refresh = False
                self.position_quantity = Decimal("0")
                self.open_order_id = 901
                self.open_quantity = 2
                self.open_status = "Submitted"
                clients.append(self)

            def run(self) -> None:
                self.wrapper.nextValidId(100)
                self.wrapper.managedAccounts("DU1234567")

            def isConnected(self) -> bool:
                return self.connected

            def serverVersion(self) -> int:
                return 200

            def twsConnectionTime(self) -> str:
                return "paper"

            def disconnect(self) -> None:
                self.connected = False

            def reqAccountSummary(self, request_id, *_args) -> None:
                if request_id == 91_101 and self.hold_refresh:
                    self.refresh_started.set()
                    return
                self.wrapper.accountSummaryEnd(request_id)

            def reqPositions(self) -> None:
                if self.hold_refresh:
                    return
                if self.position_quantity:
                    self.wrapper.position(
                        "DU1234567",
                        SimpleNamespace(symbol="AAPL"),
                        self.position_quantity,
                        100.0,
                    )
                self.wrapper.positionEnd()

            def reqPnL(self, *_args) -> None:
                pass

            def reqAllOpenOrders(self) -> None:
                if self.hold_refresh:
                    return
                self.wrapper.openOrder(
                    self.open_order_id,
                    SimpleNamespace(symbol="AAPL"),
                    SimpleNamespace(
                        action="BUY", totalQuantity=self.open_quantity
                    ),
                    SimpleNamespace(status=self.open_status),
                )
                self.wrapper.openOrderEnd()

            def reqCompletedOrders(self, *_args) -> None:
                if not self.hold_refresh:
                    self.wrapper.completedOrdersEnd()

            def reqExecutions(self, request_id, *_args) -> None:
                if not self.hold_refresh:
                    self.wrapper.execDetailsEnd(request_id)

        client_module = ModuleType("ibapi.client")
        client_module.EClient = FakeEClient
        wrapper_module = ModuleType("ibapi.wrapper")
        wrapper_module.EWrapper = type("FakeEWrapper", (), {})
        execution_module = ModuleType("ibapi.execution")
        execution_module.ExecutionFilter = lambda: object()
        ibapi_module = ModuleType("ibapi")
        with TemporaryDirectory() as directory, patch.dict(
            sys.modules,
            {
                "ibapi": ibapi_module,
                "ibapi.client": client_module,
                "ibapi.wrapper": wrapper_module,
                "ibapi.execution": execution_module,
            },
        ), patch(
            "us_quant.trading.adapters.ibkr.execution.connect_ibkr_client",
            lambda app, _config: setattr(app, "connected", True),
        ):
            service = IBKRExecutionAdapter(
                _config(timeout=0.05),
                repository=_repository(directory),
            )
            service.connect()
            old_app = service._client
            service._open_broker_orders[77] = "stale"
            client = clients[-1]
            client.position_quantity = Decimal("3")
            first = service.refresh_reconciliation_snapshot("session")
            self.assertTrue(
                service.reconciliation_snapshot_is_current(first)
            )
            self.assertEqual(first.reconciliation_generation, 1)
            self.assertEqual(first.broker_positions[0].quantity, Decimal("3"))
            self.assertEqual(first.open_broker_orders[0].broker_order_id, 901)
            self.assertNotIn(77, service._open_broker_orders)
            self.assertEqual(service.connect().connection_generation, 1)

            identical = service.refresh_reconciliation_snapshot("session")
            self.assertEqual(first.digest, identical.digest)
            self.assertFalse(
                service.reconciliation_snapshot_is_current(first)
            )
            self.assertTrue(
                service.reconciliation_snapshot_is_current(identical)
            )

            client.open_status = "Filled"
            second = service.refresh_reconciliation_snapshot("session")
            self.assertNotEqual(first.digest, second.digest)
            client.position_quantity = Decimal("4")
            third = service.refresh_reconciliation_snapshot("session")
            self.assertNotEqual(second.digest, third.digest)
            client.open_order_id = 902
            fourth = service.refresh_reconciliation_snapshot("session")
            self.assertNotEqual(third.digest, fourth.digest)
            client.open_quantity = 3
            fifth = service.refresh_reconciliation_snapshot("session")
            self.assertNotEqual(fourth.digest, fifth.digest)

            service._connected = False
            service.connect()
            old_app.connectionClosed()
            self.assertTrue(service._connected)
            old_app.position(
                "DU1234567",
                SimpleNamespace(symbol="STALE"),
                Decimal("99"),
                1.0,
            )
            self.assertNotIn("STALE", service._broker_positions)

            current = clients[-1]
            current.hold_refresh = True
            errors: list[Exception] = []

            def refresh() -> None:
                try:
                    service.refresh_reconciliation_snapshot("session")
                except Exception as error:
                    errors.append(error)

            thread = Thread(target=refresh)
            thread.start()
            self.assertTrue(current.refresh_started.wait(1))
            with self.assertRaises(IBKRPaperOrderError):
                service.refresh_reconciliation_snapshot("session")
            thread.join()
            self.assertEqual(len(errors), 1)
            self.assertIn(902, service._open_broker_orders)
            self.assertEqual(service._reconciliation_generation, 6)
            self.assertFalse(service._reconciliation_snapshot_complete)

    def test_arm_rejects_broker_open_orders(self) -> None:
        with TemporaryDirectory() as directory:
            service = IBKRExecutionAdapter(
                _config(), repository=_repository(directory)
            )
            service._connected = True
            service._client = _ConnectedClient()
            service._account = "DU1234567"
            service._open_broker_orders[10] = (
                "AAPL BUY 1 · Submitted"
            )
            with self.assertRaisesRegex(
                IBKRPaperOrderError, "未完成 API 订单"
            ):
                service.arm(
                    session_id="session",
                    allowed_symbols=("AAPL",),
                    max_order_notional=Decimal("1000"),
                )

    def test_armed_account_binding_blocks_submit_before_journal_write(self) -> None:
        with TemporaryDirectory() as directory:
            service = IBKRExecutionAdapter(
                _config(), repository=_repository(directory)
            )
            service._connected = True
            service._client = _ConnectedClient()
            service._account = "DU1234567"
            service._snapshot_complete = True
            service.arm(
                session_id="session",
                allowed_symbols=("AAPL",),
                max_order_notional=Decimal("1000"),
            )
            armed_fingerprint = service.armed_account_fingerprint()
            self.assertTrue(armed_fingerprint)
            self.assertTrue(service.armed_account_binding_is_valid())

            service._account = "DU7654321"
            intent = _intent(reason="account binding test")
            # Every hard gate runs in reserve, so the refusal happens there and
            # nothing is ever sent to the broker or written to the store.  The
            # message is pinned to the account change: a looser pattern would
            # also match an unrelated refusal (a closed routing window, say)
            # and the test would pass without the gate it exists for.
            with self.assertRaisesRegex(IBKRPaperOrderError, "账户已变化"):
                service.reserve(intent)
            self.assertEqual(service.repository.audit_rows(), ())
            self.assertFalse(service.armed_account_binding_is_valid())

            service._account = "DU1234567"
            self.assertTrue(service.armed_account_binding_is_valid())
            self.assertEqual(service.armed_account_fingerprint(), armed_fingerprint)
            service.disarm()
            self.assertEqual(service.armed_account_fingerprint(), "")
            self.assertTrue(service.armed_account_binding_is_valid())

    def test_submit_rejects_incomplete_connection_snapshot(self) -> None:
        """H-6: no order may be submitted while the recovery snapshot of a
        reconnect is still incomplete."""
        with TemporaryDirectory() as directory:
            service = IBKRExecutionAdapter(
                _config(client_id=82), repository=_repository(directory)
            )
            service._connected = True
            service._client = _ConnectedClient()
            service._account = "DU1234567"
            service._snapshot_complete = False
            service.arm(
                session_id="session",
                allowed_symbols=("AAPL",),
                max_order_notional=Decimal("1000"),
            )
            intent = _intent(reason="snapshot gate test")
            with self.assertRaisesRegex(IBKRPaperOrderError, "快照尚未完成"):
                service.reserve(intent)
            self.assertEqual(service.repository.audit_rows(), ())

    def test_error_202_preserves_executed_quantity(self) -> None:
        """H-9: a cancel confirmation racing an actual fill must not zero the
        reported filled quantity."""
        with TemporaryDirectory() as directory:
            journal = _repository(directory)
            service = IBKRExecutionAdapter(
                _config(client_id=83), repository=journal
            )
            intent = _intent(
                quantity=4, limit_price=Decimal("200"), reason="error 202 test"
            )
            journal.record_intent(
                intent,
                broker_order_id=7,
                account_alias="DU***17",
            )
            journal.record_fill(
                ExecutionFill(
                    execution_id="execution-202",
                    order_id=intent.order_id,
                    broker_order_id=7,
                    symbol="AAPL",
                    side=Side.BUY,
                    quantity=Decimal("2"),
                    price=Decimal("200"),
                    occurred_at=_OCCURRED,
                )
            )
            with service._correlation_lock:
                service._intent_by_order[7] = intent
            service._record_error(7, 202, "cancelled")
            row = journal.reconciliation_rows(
                session_id=intent.session_id
            )[0]
            self.assertEqual(row.latest_status, "Cancelled")
            self.assertEqual(row.reported_filled, Decimal("2"))
            self.assertEqual(row.reported_remaining, Decimal("2"))
            self.assertEqual(row.executed_quantity, Decimal("2"))

    def test_exact_order_cancel_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            service = IBKRExecutionAdapter(
                _config(), repository=_repository(directory)
            )
            intent = _intent(reason="test cancel")
            client = _ConnectedClient()
            service._connected = True
            service._client = client
            service._account = "DU1234567"
            with service._correlation_lock:
                service._intent_by_order[10] = intent
                service._order_by_intent[intent.order_id] = 10
            self.assertTrue(service.cancel(intent.order_id))
            self.assertFalse(service.cancel(intent.order_id))
            self.assertEqual(client.cancelled, [(10, "")])

    def test_concurrent_exact_order_cancel_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            service = IBKRExecutionAdapter(
                _config(), repository=_repository(directory)
            )
            intent = _intent(reason="concurrent cancel")
            client = _ConnectedClient()
            service._connected = True
            service._client = client
            service._account = "DU1234567"
            with service._correlation_lock:
                service._intent_by_order[10] = intent
                service._order_by_intent[intent.order_id] = 10
            barrier = Barrier(8)
            results: list[bool] = []

            def cancel() -> None:
                barrier.wait()
                results.append(service.cancel(intent.order_id))

            threads = [Thread(target=cancel) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(results.count(True), 1)
            self.assertEqual(results.count(False), 7)
            self.assertEqual(client.cancelled, [(10, "")])

    def test_the_adapter_exposes_exactly_one_cancel_spelling(self) -> None:
        """One production API: a second spelling is how the two drifted apart.

        The port declared ``cancel`` while this adapter implemented
        ``cancel_intent``, so production wiring raised ``AttributeError`` on the
        first cancel.  Both names existing at once is the shape of the bug, so
        the old spelling must be gone rather than merely unused.
        """

        self.assertTrue(callable(getattr(IBKRExecutionAdapter, "cancel", None)))
        self.assertFalse(hasattr(IBKRExecutionAdapter, "cancel_intent"))

    def test_the_production_wiring_cancels_through_to_the_broker_client(
        self,
    ) -> None:
        """Application -> adapter -> ``client.cancelOrder``, built as in prod.

        Every fake in the suite implemented the port's own spelling, which is
        why the mismatch survived: this test builds the real application over
        the real adapter over the real store and drives an order through the
        whole chain.  The second cancel is the idempotency contract: the broker
        must be told once while the confirmation is still outstanding.
        """

        strategy = StrategyIdentity(
            strategy_id="intraday-auto-rotation",
            version_id="version",
            parameter_hash="hash",
        )
        proposal = TradeProposal(
            strategy=strategy,
            symbol="AAPL",
            action=TradeAction.BUY,
            desired_quantity=1,
            reference_price=Decimal("200"),
            reason="production cancel wiring",
            generated_at=datetime.fromisoformat("2026-07-26T12:00:00+00:00"),
        )

        with TemporaryDirectory() as directory:
            client = _ConnectedClient()
            repository = build_order_repository(
                Path(directory) / "orders.sqlite3"
            )
            adapter = build_execution_candidate(_config(), repository=repository)
            adapter._connected = True
            adapter._client = client
            adapter._account = "DU1234567"
            adapter._snapshot_complete = True
            adapter._next_order_id = 100
            adapter.arm(
                session_id="session",
                allowed_symbols=("AAPL",),
                max_order_notional=Decimal("1000"),
            )
            application = build_execution_application(
                repository=repository, broker=adapter
            )

            with patch.dict(sys.modules, _ibapi_stub_modules()), patch(
                _ROUTING_TARGET, _allow_routing
            ):
                result = application.submit_approved(
                    proposal=proposal,
                    decision=RiskDecision.approve(requested_quantity=1),
                    execution_symbol="AAPL",
                    session_id="session",
                    reason="production cancel wiring",
                )
                first = application.cancel(result.intent.order_id)
                second = application.cancel(result.intent.order_id)

        self.assertEqual(len(client.placed), 1)
        self.assertEqual(result.broker_order_id, 100)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(client.cancelled, [(100, "")])

    def test_sessions_delegates_to_the_journal_without_touching_sqlite(
        self,
    ) -> None:
        """The adapter must proxy the rollup, not own it.

        The store is replaced with a recorder here: the adapter must not touch a
        broker or a database to answer this question.
        """

        class RecordingJournal:
            def __init__(self) -> None:
                self.calls: list[int] = []

            def sessions(self, *, limit: int = 50):
                self.calls.append(limit)
                return ({"session_id": "s-1", "intent_count": limit},)

        with TemporaryDirectory():
            journal = RecordingJournal()
            service = IBKRExecutionAdapter(
                _config(), repository=journal  # type: ignore[arg-type]
            )

            rows = service.sessions(limit=7)

            self.assertEqual(journal.calls, [7])
            self.assertEqual(rows, ({"session_id": "s-1", "intent_count": 7},))
            # No broker connection was needed to answer this: the delegate
            # never touches the client or the connection flags.
            self.assertFalse(service._connected)
            self.assertIsNone(service._client)

    def test_sessions_delegate_defaults_to_the_journals_default(self) -> None:
        class RecordingJournal:
            def __init__(self) -> None:
                self.calls: list[int] = []

            def sessions(self, *, limit: int = 50):
                self.calls.append(limit)
                return ()

        with TemporaryDirectory():
            journal = RecordingJournal()
            service = IBKRExecutionAdapter(
                _config(), repository=journal  # type: ignore[arg-type]
            )
            self.assertEqual(service.sessions(), ())
            self.assertEqual(journal.calls, [50])

    def test_connect_calls_the_gateway_factory(self) -> None:
        """``connect()`` must obtain its app from the factory, for this epoch.

        Patching the factory proves the wiring: the adapter no longer builds
        its own ``PaperApp``, and the epoch it passes is the one it just
        allocated.
        """

        built: list[tuple[object, int]] = []

        with TemporaryDirectory() as directory:
            service = IBKRExecutionAdapter(
                _config(timeout=0.05),
                repository=_repository(directory),
            )

            def fake_factory(*, sink, epoch):
                built.append((sink, epoch))
                raise IBKRPaperOrderError("stop after wiring")

            with patch(
                "us_quant.trading.adapters.ibkr.execution"
                ".create_paper_gateway_app",
                fake_factory,
            ), self.assertRaises(IBKRPaperOrderError):
                service.connect()

        self.assertEqual(len(built), 1)
        sink, epoch = built[0]
        self.assertIs(sink, service)
        self.assertEqual(epoch, service._physical_connection_epoch)
        self.assertIsNone(service._client)

    def test_adapter_no_longer_defines_the_ibkr_callback_transport(self) -> None:
        """The nested ``PaperApp`` must live in the gateway module only.

        The adapter keeps the meaning of every callback; it must not keep the
        ``EWrapper`` / ``EClient`` shell, or the two copies would drift.
        """

        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "us_quant"
            / "trading"
            / "adapters"
            / "ibkr"
            / "execution.py"
        ).read_text(encoding="utf-8")

        for forbidden in ("class PaperApp", "EWrapper", "EClient"):
            self.assertNotIn(forbidden, source, forbidden)
        self.assertIn("create_paper_gateway_app", source)

    def test_connect_builds_the_gateway_instead_of_a_local_app(self) -> None:
        """``connect()`` must call the factory, not define its own class."""

        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "us_quant"
            / "trading"
            / "adapters"
            / "ibkr"
            / "execution.py"
        ).read_text(encoding="utf-8")
        connect_body = source.split("def connect(self)")[1].split(
            "\n    def "
        )[0]

        self.assertIn("create_paper_gateway_app", connect_body)
        self.assertIn("sink=self", connect_body)
        self.assertNotIn("class ", connect_body)

    def test_service_implements_the_gateway_sink_protocol(self) -> None:
        """Every protocol method must exist on the service, one per callback."""

        for name in PaperGatewaySink.__protocol_attrs__:
            self.assertTrue(
                callable(getattr(IBKRExecutionAdapter, name, None)), name
            )

    def test_gateway_callbacks_reach_the_service_handlers(self) -> None:
        """A callback on a fresh gateway app must land in the service handler.

        This is the wiring the transport extraction could silently break: the
        app is built by the factory and forwards into the live service.
        """

        recorded: list[tuple[int, str]] = []

        with TemporaryDirectory() as directory:
            service = IBKRExecutionAdapter(
                _config(), repository=_repository(directory)
            )
            service._record_order_status = (  # type: ignore[method-assign]
                lambda **kwargs: recorded.append(
                    (kwargs["orderId"], kwargs["status"])
                )
            )
            service._client = None
            service._physical_connection_epoch = 4

            with patch.dict(sys.modules, _ibapi_stub_modules()):
                app = create_paper_gateway_app(sink=service, epoch=4)

            service._client = app
            app.orderStatus(
                7, "Filled", 2.0, 0.0, 10.0, 1, 0, 10.0, 9, "", 0.0
            )

            self.assertEqual(recorded, [(7, "Filled")])

    def test_a_stale_gateway_callback_never_reaches_the_service(self) -> None:
        """The epoch guard must still drop callbacks from a replaced app."""

        recorded: list[tuple[int, str]] = []

        with TemporaryDirectory() as directory:
            service = IBKRExecutionAdapter(
                _config(), repository=_repository(directory)
            )
            service._record_order_status = (  # type: ignore[method-assign]
                lambda **kwargs: recorded.append(
                    (kwargs["orderId"], kwargs["status"])
                )
            )

            with patch.dict(sys.modules, _ibapi_stub_modules()):
                app = create_paper_gateway_app(sink=service, epoch=4)
                newer = create_paper_gateway_app(sink=service, epoch=5)

            # The app built for epoch 4 is no longer the live client.
            service._client = newer
            service._physical_connection_epoch = 5
            app.orderStatus(
                7, "Filled", 2.0, 0.0, 10.0, 1, 0, 10.0, 9, "", 0.0
            )

            self.assertEqual(recorded, [])

    def _service_with_handshake(self, directory: str):
        """A service whose account binding and handshake can be inspected."""

        return IBKRExecutionAdapter(
            _config(), repository=_repository(directory)
        )

    def test_gateway_order_status_preserves_the_full_business_behaviour(
        self,
    ) -> None:
        """Full transport payload in, unchanged trading semantics out.

        The transport now hands over all eleven IBKR arguments.  The four the
        service has no use for must not leak into the recorded status: only
        the decimals and the ``whyHeld`` message may change, exactly as before
        the split.
        """

        recorded: list[dict] = []

        with TemporaryDirectory() as directory:
            service = self._service_with_handshake(directory)
            service._record_order_status = (  # type: ignore[method-assign]
                lambda **kwargs: recorded.append(kwargs)
            )
            service._client = None
            service._physical_connection_epoch = 4

            with patch.dict(sys.modules, _ibapi_stub_modules()):
                app = create_paper_gateway_app(sink=service, epoch=4)

            service._client = app
            app.orderStatus(
                7, "Filled", 2, 1, 3, 101, 202, 4, 303, "held-marker", 5
            )

        self.assertEqual(len(recorded), 1)
        entry = recorded[0]
        self.assertEqual(entry["orderId"], 7)
        self.assertEqual(entry["status"], "Filled")
        # Decimal, not float: Decimal("2") == 2.0 would hide a type change.
        self.assertIsInstance(entry["filled"], Decimal)
        self.assertIsInstance(entry["remaining"], Decimal)
        self.assertEqual(entry["filled"], Decimal("2"))
        self.assertEqual(entry["remaining"], Decimal("1"))
        self.assertIsInstance(entry["average_fill_price"], Decimal)
        self.assertIsInstance(entry["last_fill_price"], Decimal)
        self.assertEqual(entry["average_fill_price"], Decimal("3"))
        self.assertEqual(entry["last_fill_price"], Decimal("4"))
        self.assertEqual(entry["message"], "held-marker")
        # The four unused arguments must not become part of the record.
        self.assertEqual(
            set(entry),
            {
                "orderId",
                "status",
                "filled",
                "remaining",
                "average_fill_price",
                "last_fill_price",
                "message",
            },
        )

    def test_gateway_order_status_keeps_the_zero_and_empty_conventions(
        self,
    ) -> None:
        """A zero price is ``None`` and an empty ``whyHeld`` is ``""``."""

        recorded: list[dict] = []

        with TemporaryDirectory() as directory:
            service = self._service_with_handshake(directory)
            service._record_order_status = (  # type: ignore[method-assign]
                lambda **kwargs: recorded.append(kwargs)
            )
            service._client = None
            service._physical_connection_epoch = 4

            with patch.dict(sys.modules, _ibapi_stub_modules()):
                app = create_paper_gateway_app(sink=service, epoch=4)

            service._client = app
            app.orderStatus(7, "Submitted", 0, 1, 0, 101, 0, 0, 303, "", 0)

        entry = recorded[0]
        self.assertIsNone(entry["average_fill_price"])
        self.assertIsNone(entry["last_fill_price"])
        self.assertEqual(entry["message"], "")

    def test_gateway_account_summary_discards_currency(self) -> None:
        """``currency`` reaches the service and is then dropped on purpose."""

        with TemporaryDirectory() as directory:
            service = self._service_with_handshake(directory)
            service._account = "DU1234567"
            service._client = None
            service._physical_connection_epoch = 4

            with patch.dict(sys.modules, _ibapi_stub_modules()):
                app = create_paper_gateway_app(sink=service, epoch=4)

            service._client = app
            app.accountSummary(
                91_001, "DU1234567", "NetLiquidation", "100", "USD-marker"
            )

            self.assertEqual(
                service._account_metrics["NetLiquidation"], Decimal("100")
            )
            self.assertEqual(
                [tag for tag in service._account_metrics], ["NetLiquidation"]
            )

    def test_managed_accounts_rejects_a_non_du_account(self) -> None:
        """A live-looking account must be refused, permanently.

        ``DU`` is the only prefix a Paper session may bind to. This is the
        safety invariant behind "Live 永久阻断" and it had no regression test
        at all before the transport split -- it survived every mutation.
        """

        with TemporaryDirectory() as directory:
            service = self._service_with_handshake(directory)

            with patch.dict(sys.modules, _ibapi_stub_modules()):
                app = create_paper_gateway_app(sink=service, epoch=1)

            service._client = app
            service._physical_connection_epoch = 1
            app.managedAccounts("U1234567")

            self.assertEqual(service._account, "")
            self.assertIn(
                "拒绝非 DU 账户：Live 永久阻断",
                service._handshake.errors,
            )
            self.assertTrue(service._handshake.accounts_ready.is_set())

    def test_managed_accounts_requires_exactly_one_account(self) -> None:
        """Two accounts is as unworkable as none: refuse and do not bind."""

        with TemporaryDirectory() as directory:
            service = self._service_with_handshake(directory)

            with patch.dict(sys.modules, _ibapi_stub_modules()):
                app = create_paper_gateway_app(sink=service, epoch=1)

            service._client = app
            service._physical_connection_epoch = 1
            app.managedAccounts("DU1234567,DU7654321")

            self.assertEqual(service._account, "")
            self.assertIn(
                "Paper 自动量化要求 Gateway 只返回一个账户",
                service._handshake.errors,
            )

    def test_next_valid_id_never_regresses_below_the_journal_floor(
        self,
    ) -> None:
        """A Gateway restart must not hand back an already-used order id."""

        with TemporaryDirectory() as directory:
            service = self._service_with_handshake(directory)
            service._next_order_id_floor = 500

            with patch.dict(sys.modules, _ibapi_stub_modules()):
                app = create_paper_gateway_app(sink=service, epoch=1)

            service._client = app
            service._physical_connection_epoch = 1
            app.nextValidId(42)

            self.assertEqual(service._next_order_id, 500)
            self.assertTrue(service._handshake.ready.is_set())

    def test_account_summary_ignores_a_foreign_account(self) -> None:
        """Metrics for another account must never enter this session."""

        with TemporaryDirectory() as directory:
            service = self._service_with_handshake(directory)
            service._account = "DU1234567"

            with patch.dict(sys.modules, _ibapi_stub_modules()):
                app = create_paper_gateway_app(sink=service, epoch=1)

            service._client = app
            service._physical_connection_epoch = 1
            app.accountSummary(91_001, "DU7654321", "NetLiquidation", "5", "USD")

            self.assertEqual(dict(service._account_metrics), {})

            app.accountSummary(91_001, "DU1234567", "NetLiquidation", "5", "USD")
            self.assertEqual(service._account_metrics["NetLiquidation"], Decimal("5"))

    def test_position_ignores_a_foreign_account(self) -> None:
        """A position row for another account must never enter this session."""

        with TemporaryDirectory() as directory:
            service = self._service_with_handshake(directory)
            service._account = "DU1234567"

            with patch.dict(sys.modules, _ibapi_stub_modules()):
                app = create_paper_gateway_app(sink=service, epoch=1)

            service._client = app
            service._physical_connection_epoch = 1
            app.position(
                "DU7654321",
                SimpleNamespace(symbol="AAPL"),
                Decimal("3"),
                100.0,
            )
            self.assertEqual(dict(service._broker_positions), {})

            app.position(
                "DU1234567",
                SimpleNamespace(symbol="AAPL"),
                Decimal("3"),
                100.0,
            )
            self.assertEqual(
                service._broker_positions["AAPL"].quantity, Decimal("3")
            )

    def test_a_reconciliation_execution_stays_out_of_the_journal(self) -> None:
        """A snapshot refresh must buffer executions, not journal them."""

        with TemporaryDirectory() as directory:
            service = self._service_with_handshake(directory)
            recorded: list[tuple[Any, Any]] = []
            service._record_execution = (  # type: ignore[method-assign]
                lambda contract, execution: recorded.append((contract, execution))
            )

            with patch.dict(sys.modules, _ibapi_stub_modules()):
                app = create_paper_gateway_app(sink=service, epoch=1)

            service._client = app
            service._physical_connection_epoch = 1
            service._refresh_attempt = _ReconciliationRefreshAttempt(
                app=app,
                epoch=1,
                session_id="session",
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

            app.execDetails(91_103, "contract", "execution")

            self.assertEqual(recorded, [])
            self.assertEqual(
                service._refresh_attempt.executions,
                [("contract", "execution")],
            )

    def test_the_two_epoch_guards_are_both_required(self) -> None:
        """Client identity and epoch are separate conditions, not one."""

        with TemporaryDirectory() as directory:
            service = self._service_with_handshake(directory)
            client = object()
            service._client = client
            service._physical_connection_epoch = 7

            self.assertTrue(service._is_current_connection(client, 7))
            # Right epoch, wrong client.
            self.assertFalse(service._is_current_connection(object(), 7))
            # Right client, stale epoch.
            self.assertFalse(service._is_current_connection(client, 6))

    def test_a_newer_client_is_not_current_for_the_old_epoch(self) -> None:
        """Reconnecting must invalidate the previous client/epoch pair."""

        with TemporaryDirectory() as directory:
            service = self._service_with_handshake(directory)

            with patch.dict(sys.modules, _ibapi_stub_modules()):
                old = create_paper_gateway_app(sink=service, epoch=3)
                new = create_paper_gateway_app(sink=service, epoch=4)

            service._client = new
            service._physical_connection_epoch = 4

            self.assertFalse(service.gateway_is_current(old, 3))
            self.assertTrue(service.gateway_is_current(new, 4))

    # ------------------------------------------------------------------
    # Two-phase reserve / submit.  These are the new boundary: reserving
    # allocates the broker id and sends nothing, so every hard refusal and
    # the durable-correlation ordering can be asserted directly.
    # ------------------------------------------------------------------

    def _armed_service(
        self,
        directory: str,
        client: Any,
        *,
        sellable: dict[str, int] | None = None,
        next_order_id: int = 100,
    ) -> tuple[IBKRExecutionAdapter, SQLiteOrderRepository]:
        """A service armed on ``session`` with a connected, snapshotted channel."""

        repository = _repository(directory)
        service = IBKRExecutionAdapter(_config(), repository=repository)
        service._connected = True
        service._client = client
        service._account = "DU1234567"
        service._snapshot_complete = True
        service._next_order_id = next_order_id
        service.arm(
            session_id="session",
            allowed_symbols=("AAPL",),
            max_order_notional=Decimal("1000"),
            sellable_quantities=sellable,
        )
        return service, repository

    def test_reserve_allocates_a_broker_order_id_without_sending(self) -> None:
        """Reserving must allocate the id and call nothing on the broker."""

        with TemporaryDirectory() as directory:
            client = _ConnectedClient()
            service, _repository = self._armed_service(directory, client)
            intent = _intent()

            with patch(_ROUTING_TARGET, _allow_routing):
                reservation = service.reserve(intent)

            self.assertIsInstance(reservation, BrokerOrderReservation)
            self.assertEqual(reservation.order_id, intent.order_id)
            self.assertEqual(reservation.broker_order_id, 100)
            self.assertEqual(reservation.account_alias, "DU***67")
            # Reserve allocated the id but sent nothing.
            self.assertEqual(client.placed, [])

    def test_reserving_twice_reuses_the_same_broker_order_id(self) -> None:
        """A duplicate reserve for one order identity must not burn a second id."""

        with TemporaryDirectory() as directory:
            client = _ConnectedClient()
            service, _repository = self._armed_service(directory, client)
            intent = _intent()

            with patch(_ROUTING_TARGET, _allow_routing):
                first = service.reserve(intent)
                second = service.reserve(intent)

            self.assertEqual(first.broker_order_id, second.broker_order_id)
            # Exactly one id was consumed from the counter.
            self.assertEqual(service._next_order_id, 101)
            self.assertEqual(client.placed, [])

    def test_submit_uncertain_queues_an_unknown_event(self) -> None:
        """A failed place call is UNKNOWN, never FILLED, and names the order.

        The place call really happens: the fake records it and then raises, so
        the UNKNOWN event below is evidence that the uncertain path ran, not
        evidence that the order was refused earlier by some other gate.
        """

        class FailingClient(_ConnectedClient):
            def placeOrder(self, order_id, contract, order) -> None:
                self.placed.append((order_id, contract, order))
                raise RuntimeError("socket gone")

        with TemporaryDirectory() as directory:
            client = FailingClient()
            service, repository = self._armed_service(directory, client)
            intent = _intent()

            with patch.dict(sys.modules, _ibapi_stub_modules()), patch(
                _ROUTING_TARGET, _allow_routing
            ):
                reservation = service.reserve(intent)
                # The application writes the durable correlation between the
                # two phases; the adapter does not.
                repository.record_intent(
                    intent,
                    broker_order_id=reservation.broker_order_id,
                    account_alias=reservation.account_alias,
                )
                with self.assertRaises(
                    IBKRPaperOrderUncertainError
                ) as caught:
                    service.submit(reservation)

            error = caught.exception
            self.assertIsInstance(error, ExecutionSubmissionUncertain)
            self.assertEqual(error.order_id, intent.order_id)
            self.assertEqual(error.broker_order_id, reservation.broker_order_id)
            # The order reached the broker client, built from the intent.
            self.assertEqual(len(client.placed), 1)
            placed_id, contract, order = client.placed[0]
            self.assertEqual(placed_id, reservation.broker_order_id)
            self.assertEqual(contract.symbol, intent.execution_symbol)
            self.assertEqual(order.account, "DU1234567")

            events = service.events()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].broker_status, "SubmitUncertain")
            self.assertIs(events[0].status, OrderStatus.UNKNOWN)
            self.assertIsNot(events[0].status, OrderStatus.FILLED)
            self.assertEqual(events[0].order_id, intent.order_id)
            self.assertEqual(events[0].broker_order_id, reservation.broker_order_id)

    def test_events_map_ibkr_status_text_to_domain_status(self) -> None:
        """The broker's verbatim text travels with the mapped domain status."""

        with TemporaryDirectory() as directory:
            client = _ConnectedClient()
            service, repository = self._armed_service(directory, client)
            intent = _intent()

            with patch(_ROUTING_TARGET, _allow_routing):
                reservation = service.reserve(intent)
            repository.record_intent(
                intent,
                broker_order_id=reservation.broker_order_id,
                account_alias=reservation.account_alias,
            )

            with patch.dict(sys.modules, _ibapi_stub_modules()):
                app = create_paper_gateway_app(sink=service, epoch=1)

            service._client = app
            service._physical_connection_epoch = 1
            expected = (
                ("Filled", OrderStatus.FILLED),
                ("Cancelled", OrderStatus.CANCELED),
                ("Inactive", OrderStatus.INACTIVE),
                ("Submitted", OrderStatus.ACKNOWLEDGED),
            )
            for text, _domain in expected:
                app.orderStatus(
                    reservation.broker_order_id,
                    text,
                    0,
                    1,
                    0,
                    1,
                    0,
                    0,
                    1,
                    "",
                    0,
                )

            events = service.events()
            self.assertEqual(
                [(event.broker_status, event.status) for event in events],
                list(expected),
            )

    # ------------------------------------------------------------------
    # Submit-time revalidation.  reserve() runs every hard gate, but the
    # durable write sits between the two calls, and inside that gap the
    # session can be disarmed, the account rebound, the allowlist replaced,
    # the notional ceiling lowered and the sellable book shrunk.  Each test
    # below mutates exactly one of those and requires the send to be refused
    # with placeOrder never called; the durable row is allowed to stand.
    # ------------------------------------------------------------------

    def _reserve_and_record(
        self,
        service: IBKRExecutionAdapter,
        repository: SQLiteOrderRepository,
        intent: OrderIntent,
    ) -> BrokerOrderReservation:
        """Reserve, then write the correlation the way the application does."""

        with patch(_ROUTING_TARGET, _allow_routing):
            reservation = service.reserve(intent)
        repository.record_intent(
            intent,
            broker_order_id=reservation.broker_order_id,
            account_alias=reservation.account_alias,
        )
        return reservation

    def _submit_expecting_refusal(
        self,
        service: IBKRExecutionAdapter,
        reservation: BrokerOrderReservation,
        pattern: str,
    ) -> None:
        with patch.dict(sys.modules, _ibapi_stub_modules()), patch(
            _ROUTING_TARGET, _allow_routing
        ):
            with self.assertRaisesRegex(IBKRPaperOrderError, pattern):
                service.submit(reservation)

    def test_submit_refuses_after_the_bound_account_changes(self) -> None:
        """The managedAccounts race: reserve on one DU account, send on none."""

        with TemporaryDirectory() as directory:
            client = _ConnectedClient()
            service, repository = self._armed_service(directory, client)
            intent = _intent(reason="account race")
            reservation = self._reserve_and_record(
                service, repository, intent
            )
            self.assertEqual(client.placed, [])

            service._account = "DU7654321"
            self._submit_expecting_refusal(
                service, reservation, "账户已变化"
            )

            self.assertEqual(client.placed, [])
            # The row stays: the order is nameable, it simply never went out.
            self.assertIsNotNone(repository.intent(intent.order_id))

            # Re-arming on the new account makes the binding valid again, so
            # the reservation's own alias is then the only thing standing
            # between a correlation written for one book and a send on another.
            service.arm(
                session_id="session",
                allowed_symbols=("AAPL",),
                max_order_notional=Decimal("1000"),
            )
            self.assertTrue(service.armed_account_binding_is_valid())
            self._submit_expecting_refusal(
                service, reservation, "预留账户与当前账户不一致"
            )
            self.assertEqual(client.placed, [])

    def test_submit_refuses_after_the_session_is_disarmed(self) -> None:
        """disarm between reserve and submit must stop the send.

        Nothing else can catch this one: an unarmed service has no binding to
        invalidate and ``disarm`` leaves the account untouched, so only the
        armed-session gate stands between a disarmed session and a live order.
        """

        with TemporaryDirectory() as directory:
            client = _ConnectedClient()
            service, repository = self._armed_service(directory, client)
            intent = _intent(reason="disarm race")
            reservation = self._reserve_and_record(
                service, repository, intent
            )
            self.assertEqual(client.placed, [])

            service.disarm()
            self.assertTrue(service.armed_account_binding_is_valid())
            self._submit_expecting_refusal(
                service, reservation, "已解除武装"
            )

            self.assertEqual(client.placed, [])
            self.assertIsNotNone(repository.intent(intent.order_id))

    def test_submit_refuses_after_the_symbol_leaves_the_allowlist(self) -> None:
        """A re-armed session with a different candidate set must not send."""

        with TemporaryDirectory() as directory:
            client = _ConnectedClient()
            service, repository = self._armed_service(directory, client)
            intent = _intent(reason="allowlist race")
            reservation = self._reserve_and_record(
                service, repository, intent
            )

            service.disarm()
            service.arm(
                session_id="session",
                allowed_symbols=("MSFT",),
                max_order_notional=Decimal("1000"),
            )
            self._submit_expecting_refusal(
                service, reservation, "候选集"
            )

            self.assertEqual(client.placed, [])

    def test_submit_refuses_after_the_notional_ceiling_drops(self) -> None:
        """A lowered hard ceiling applies to already-reserved orders too."""

        with TemporaryDirectory() as directory:
            client = _ConnectedClient()
            service, repository = self._armed_service(directory, client)
            intent = _intent(reason="notional race")
            reservation = self._reserve_and_record(
                service, repository, intent
            )

            service.disarm()
            service.arm(
                session_id="session",
                allowed_symbols=("AAPL",),
                max_order_notional=Decimal("100"),
            )
            self._submit_expecting_refusal(
                service, reservation, "名义金额上限"
            )

            self.assertEqual(client.placed, [])

    def test_submit_refuses_after_the_sellable_quantity_drops(self) -> None:
        """A SELL sized at reserve time must fit the book at send time."""

        with TemporaryDirectory() as directory:
            client = _ConnectedClient()
            service, repository = self._armed_service(
                directory, client, sellable={"AAPL": 10}
            )
            intent = _intent(
                side=Side.SELL,
                quantity=10,
                limit_price=Decimal("50"),
                reason="sellable race",
            )
            reservation = self._reserve_and_record(
                service, repository, intent
            )

            service._sellable_quantities["AAPL"] = 4
            self._submit_expecting_refusal(
                service, reservation, "可卖整股"
            )

            self.assertEqual(client.placed, [])

    def test_submit_refuses_a_reservation_that_names_another_order(self) -> None:
        """A swapped reservation is not the order the session authorised."""

        with TemporaryDirectory() as directory:
            client = _ConnectedClient()
            service, repository = self._armed_service(directory, client)
            intent = _intent(reason="reservation mismatch")
            reservation = self._reserve_and_record(
                service, repository, intent
            )

            swapped = BrokerOrderReservation(
                order_id="some-other-order",
                broker_order_id=reservation.broker_order_id,
                account_alias=reservation.account_alias,
            )
            self._submit_expecting_refusal(service, swapped, "已预留状态")
            self.assertEqual(client.placed, [])

    # ------------------------------------------------------------------
    # The window between the last safety read and the send.  Everything
    # above mutates the state *before* submit is called, which is the case a
    # plain re-check already covers.  These tests occupy the window itself:
    # every gate has passed, the order object does not exist yet, and a
    # mutation lands from another thread.
    # ------------------------------------------------------------------

    def _submit_with_a_pause_after_the_last_check(
        self,
        service: IBKRExecutionAdapter,
        reservation: BrokerOrderReservation,
        mutate: Callable[[], None],
    ) -> list[Exception]:
        """Submit with ``mutate`` landing after the gates passed, before the send.

        The pause sits exactly on the boundary ``submit`` defines: after
        ``_revalidate_for_send`` has answered yes and before the send section
        runs, so the order has not been built and nothing has been sent.
        ``mutate`` then runs on this thread while the submitter waits -- the
        state really does change underneath it, which is what makes this a
        race and not another pre-submit setup.  Returning the failures instead
        of asserting inside lets the caller check the refusal and the send
        count together.
        """

        checked = Event()
        release = Event()
        failures: list[Exception] = []
        original = IBKRExecutionAdapter._revalidate_for_send

        def paused(self, res, order_intent):
            authorization = original(self, res, order_intent)
            checked.set()
            release.wait(5)
            return authorization

        def run() -> None:
            try:
                service.submit(reservation)
            except Exception as error:
                failures.append(error)

        with patch.dict(sys.modules, _ibapi_stub_modules()), patch(
            _ROUTING_TARGET, _allow_routing
        ), patch.object(
            IBKRExecutionAdapter, "_revalidate_for_send", paused
        ):
            thread = Thread(target=run)
            thread.start()
            try:
                self.assertTrue(
                    checked.wait(5), "submit never passed its gates"
                )
                mutate()
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive(), "submit never finished")
        return failures

    def test_a_disarm_after_the_last_check_and_before_the_send_stops_it(
        self,
    ) -> None:
        """A disarm inside the window must reach the sender, not the broker."""

        with TemporaryDirectory() as directory:
            client = _ConnectedClient()
            service, repository = self._armed_service(directory, client)
            intent = _intent(reason="disarm inside the window")
            reservation = self._reserve_and_record(
                service, repository, intent
            )

            failures = self._submit_with_a_pause_after_the_last_check(
                service, reservation, service.disarm
            )

            self.assertIsNone(service._armed_session_id)
            # The order never left, and the refusal has to name the change
            # rather than some unrelated gate.
            self.assertEqual(client.placed, [])
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], IBKRPaperOrderError)
            self.assertIn("会话武装状态已变化", str(failures[0]))
            # The durable row predates the send and is allowed to stand: the
            # order stays nameable for reconciliation.
            self.assertIsNotNone(repository.intent(intent.order_id))

    def test_an_account_rebind_after_the_last_check_and_before_the_send_stops_it(
        self,
    ) -> None:
        """The managedAccounts race: authorised on one book, sent on none."""

        with TemporaryDirectory() as directory:
            client = _ConnectedClient()
            service, repository = self._armed_service(directory, client)
            intent = _intent(reason="account rebind inside the window")
            reservation = self._reserve_and_record(
                service, repository, intent
            )

            def rebind() -> None:
                service.gateway_managed_accounts(
                    service._client, 1, "DU7654321"
                )

            failures = self._submit_with_a_pause_after_the_last_check(
                service, reservation, rebind
            )

            # The rebind really happened -- otherwise this proves nothing.
            self.assertEqual(service._account, "DU7654321")
            self.assertFalse(service.armed_account_binding_is_valid())
            self.assertEqual(client.placed, [])
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], IBKRPaperOrderError)
            self.assertIn("账户已变化", str(failures[0]))

    def test_a_disarm_during_the_send_is_ordered_after_it(self) -> None:
        """The section spans the send, so a mutation cannot be inserted into it.

        ``placeOrder`` is held open here while another thread disarms.  If the
        submitter really owns the state lock, the disarm cannot complete until
        the order has left -- so the order goes out authorised by the state it
        was sent under, and the disarm is ordered after it.  That linearisation
        is the whole point: "no insertion" means the mutation waits, and the
        only alternative would be a send that never observed the mutation
        either way.
        """

        with TemporaryDirectory() as directory:
            client = _BlockingClient()
            service, repository = self._armed_service(directory, client)
            intent = _intent(reason="disarm during the send")
            reservation = self._reserve_and_record(
                service, repository, intent
            )

            failures: list[Exception] = []

            def run() -> None:
                try:
                    service.submit(reservation)
                except Exception as error:
                    failures.append(error)

            with patch.dict(sys.modules, _ibapi_stub_modules()), patch(
                _ROUTING_TARGET, _allow_routing
            ):
                thread = Thread(target=run)
                thread.start()
                self.assertTrue(client.send_started.wait(5))

                attempted = Event()
                disarmed = Event()

                def disarm() -> None:
                    attempted.set()
                    service.disarm()
                    disarmed.set()

                other = Thread(target=disarm)
                other.start()
                self.assertTrue(attempted.wait(5))
                try:
                    self.assertFalse(
                        disarmed.wait(0.3),
                        "disarm landed inside the send section",
                    )
                    self.assertIsNotNone(service._armed_session_id)
                finally:
                    client.send_release.set()
                    thread.join(5)
                    other.join(5)

            self.assertEqual(failures, [])
            self.assertEqual(len(client.placed), 1)
            self.assertTrue(disarmed.is_set())
            self.assertIsNone(service._armed_session_id)

    def test_an_account_rebind_during_the_send_waits_for_the_send(self) -> None:
        """The account write is a lock participant, and this is why it has to be.

        An asynchronous ``managedAccounts`` that landed between the last check
        and the send would put the order on an account the session never
        authorised.  It therefore waits -- and the account it changes to is
        visible immediately afterwards, so nothing is silently dropped.
        """

        with TemporaryDirectory() as directory:
            client = _BlockingClient()
            service, repository = self._armed_service(directory, client)
            intent = _intent(reason="account rebind during the send")
            reservation = self._reserve_and_record(
                service, repository, intent
            )

            failures: list[Exception] = []

            def run() -> None:
                try:
                    service.submit(reservation)
                except Exception as error:
                    failures.append(error)

            with patch.dict(sys.modules, _ibapi_stub_modules()), patch(
                _ROUTING_TARGET, _allow_routing
            ):
                thread = Thread(target=run)
                thread.start()
                self.assertTrue(client.send_started.wait(5))

                attempted = Event()
                rebound = Event()

                def rebind() -> None:
                    attempted.set()
                    service.gateway_managed_accounts(
                        service._client, 1, "DU7654321"
                    )
                    rebound.set()

                other = Thread(target=rebind)
                other.start()
                self.assertTrue(attempted.wait(5))
                try:
                    self.assertFalse(
                        rebound.wait(0.3),
                        "the account rebind landed inside the send section",
                    )
                    self.assertEqual(service._account, "DU1234567")
                finally:
                    client.send_release.set()
                    thread.join(5)
                    other.join(5)

            self.assertEqual(failures, [])
            self.assertEqual(len(client.placed), 1)
            # The order went out on the account the session was armed on.
            self.assertEqual(client.placed[0][2].account, "DU1234567")
            self.assertTrue(rebound.is_set())
            self.assertEqual(service._account, "DU7654321")

    def test_a_fractional_execution_is_not_truncated(self) -> None:
        """A 1.5-share fill is delivered as 1.5 and never rounds the book."""

        with TemporaryDirectory() as directory:
            client = _ConnectedClient()
            service, repository = self._armed_service(
                directory, client, sellable={"AAPL": 3}
            )
            intent = _intent()

            with patch(_ROUTING_TARGET, _allow_routing):
                reservation = service.reserve(intent)
            repository.record_intent(
                intent,
                broker_order_id=reservation.broker_order_id,
                account_alias=reservation.account_alias,
            )

            with patch.dict(sys.modules, _ibapi_stub_modules()):
                app = create_paper_gateway_app(sink=service, epoch=1)

            service._client = app
            service._physical_connection_epoch = 1
            app.execDetails(
                91_003,
                SimpleNamespace(symbol="AAPL"),
                SimpleNamespace(
                    orderId=reservation.broker_order_id,
                    execId="execution-fractional",
                    side="BOT",
                    shares=Decimal("1.5"),
                    price=Decimal("100"),
                    time="20260726 12:00:00",
                ),
            )

            fills = service.fills()
            self.assertEqual(len(fills), 1)
            self.assertEqual(fills[0].quantity, Decimal("1.5"))
            self.assertNotEqual(fills[0].quantity, Decimal("1"))
            self.assertEqual(fills[0].order_id, intent.order_id)
            # Whole-share sellable bookkeeping must not move for a fractional
            # fill; the fill still reaches the runtime, which halts on it.
            self.assertEqual(service._sellable_quantities.get("AAPL", 0), 3)


if __name__ == "__main__":
    unittest.main()