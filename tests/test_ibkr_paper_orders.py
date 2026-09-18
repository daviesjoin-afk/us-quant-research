from decimal import Decimal
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Thread
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.ibkr_paper_gateway import create_paper_gateway_app
from us_quant.ibkr_paper_orders import (
    IBKRPaperOrderService,
    IBKRPaperOrderError,
    PaperExecution,
    PaperOrderJournal,
    PaperOrderUpdate,
    _ReconciliationRefreshAttempt,
    ensure_paper_order_config,
    new_paper_order_intent,
    validate_paper_order_intent,
)


def _ibapi_stub_modules() -> dict[str, ModuleType]:
    """The minimum ``ibapi`` surface the gateway factory needs."""

    class StubEClient:
        def __init__(self, wrapper=None) -> None:
            self.wrapper = wrapper

    client_module = ModuleType("ibapi.client")
    client_module.EClient = StubEClient
    wrapper_module = ModuleType("ibapi.wrapper")
    wrapper_module.EWrapper = type("StubEWrapper", (), {})
    return {
        "ibapi": ModuleType("ibapi"),
        "ibapi.client": client_module,
        "ibapi.wrapper": wrapper_module,
    }



class IBKRPaperOrderTests(unittest.TestCase):
    def test_only_local_paper_order_config_is_accepted(self) -> None:
        ensure_paper_order_config(
            IBKRConnectionConfig(
                host="127.0.0.1",
                port=4002,
                client_id=81,
                api_read_only=False,
                paper_order_submission_enabled=True,
                connection_timeout_seconds=5,
            )
        )
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
        intent = new_paper_order_intent(
            session_id="session",
            strategy_version_id="version",
            symbol="AAPL",
            side="BUY",
            quantity=2,
            limit_price=Decimal("200"),
            reason="test",
        )
        validate_paper_order_intent(
            intent,
            allowed_symbols=frozenset({"AAPL"}),
            max_order_notional=Decimal("500"),
            sellable_quantities={},
        )
        invalid = new_paper_order_intent(
            session_id="session",
            strategy_version_id="version",
            symbol="AAPL",
            side="BUY",
            quantity=3,
            limit_price=Decimal("200"),
            reason="too large",
        )
        with self.assertRaises(IBKRPaperOrderError):
            validate_paper_order_intent(
                invalid,
                allowed_symbols=frozenset({"AAPL"}),
                max_order_notional=Decimal("500"),
                sellable_quantities={},
            )

    def test_sell_cannot_exceed_armed_position(self) -> None:
        intent = new_paper_order_intent(
            session_id="session",
            strategy_version_id="version",
            symbol="AAPL",
            side="SELL",
            quantity=2,
            limit_price=Decimal("200"),
            reason="exit",
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
            journal = PaperOrderJournal(
                Path(directory) / "orders.sqlite3"
            )
            intent = new_paper_order_intent(
                session_id="session",
                strategy_version_id="version",
                symbol="AAPL",
                side="BUY",
                quantity=1,
                limit_price=Decimal("200"),
                reason="test",
            )
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
            journal = PaperOrderJournal(
                Path(directory) / "orders.sqlite3"
            )
            self.assertEqual(journal.max_broker_order_id(), 0)
            for order_id in (10, 42, 17):
                intent = new_paper_order_intent(
                    session_id="session",
                    strategy_version_id="version",
                    symbol="AAPL",
                    side="BUY",
                    quantity=1,
                    limit_price=Decimal("200"),
                    reason="test",
                )
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
            journal = PaperOrderJournal(
                Path(directory) / "orders.sqlite3"
            )
            intent = new_paper_order_intent(
                session_id="session",
                strategy_version_id="version",
                symbol="AAPL",
                side="BUY",
                quantity=2,
                limit_price=Decimal("200"),
                reason="test",
            )
            journal.record_intent(
                intent,
                broker_order_id=10,
                account_alias="DU***17",
            )
            journal.record_update(
                PaperOrderUpdate(
                    intent_id=intent.intent_id,
                    broker_order_id=10,
                    status="Filled",
                    filled=Decimal("2"),
                    remaining=Decimal("0"),
                    average_fill_price=Decimal("200"),
                    last_fill_price=Decimal("200"),
                    message="",
                    observed_at="2026-07-26T12:00:00+00:00",
                )
            )
            before = journal.reconciliation_rows(
                session_id="session"
            )[0]
            self.assertFalse(before.reconciled)
            self.assertIn("未对齐", before.reason)
            execution = PaperExecution(
                intent_id=intent.intent_id,
                broker_order_id=10,
                execution_id="execution-1",
                symbol="AAPL",
                side="BUY",
                quantity=Decimal("2"),
                price=Decimal("200"),
                occurred_at="2026-07-26T12:00:00+00:00",
            )
            self.assertTrue(journal.record_execution(execution))
            self.assertFalse(journal.record_execution(execution))
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
            journal = PaperOrderJournal(
                Path(directory) / "orders.sqlite3"
            )

            def record(session_id: str, order_id: int):
                intent = new_paper_order_intent(
                    session_id=session_id,
                    strategy_version_id="version",
                    symbol="AAPL",
                    side="BUY",
                    quantity=1,
                    limit_price=Decimal("200"),
                    reason="summary",
                )
                journal.record_intent(
                    intent,
                    broker_order_id=order_id,
                    account_alias="DU***17",
                )
                return intent

            reconciled = record("session-a", 1)
            journal.record_update(
                PaperOrderUpdate(
                    intent_id=reconciled.intent_id,
                    broker_order_id=1,
                    status="Filled",
                    filled=Decimal("1"),
                    remaining=Decimal("0"),
                    average_fill_price=Decimal("200"),
                    last_fill_price=Decimal("200"),
                    message="",
                    observed_at="2026-07-26T12:00:00+00:00",
                )
            )
            journal.record_execution(
                PaperExecution(
                    intent_id=reconciled.intent_id,
                    broker_order_id=1,
                    execution_id="summary-execution",
                    symbol="AAPL",
                    side="BUY",
                    quantity=Decimal("1"),
                    price=Decimal("200"),
                    occurred_at="2026-07-26T12:00:00+00:00",
                )
            )
            terminal_unreconciled = record("session-a", 2)
            journal.record_update(
                PaperOrderUpdate(
                    intent_id=terminal_unreconciled.intent_id,
                    broker_order_id=2,
                    status="Error",
                    filled=Decimal("1"),
                    remaining=Decimal("0"),
                    average_fill_price=None,
                    last_fill_price=None,
                    message="",
                    observed_at="2026-07-26T12:01:00+00:00",
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

            class ConnectedClient:
                def isConnected(self) -> bool:
                    return True

                def serverVersion(self) -> int:
                    return 200

                def twsConnectionTime(self) -> str:
                    return "paper"

            service = IBKRPaperOrderService(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4002,
                    client_id=81,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=5,
                ),
                journal=journal,
            )
            service._connected = True
            service._client = ConnectedClient()
            self.assertEqual(
                service.connection_snapshot().unreconciled_local_orders,
                1004,
            )

    def test_connection_snapshot_generation_requires_complete_snapshot(
        self,
    ) -> None:
        class ConnectedClient:
            def isConnected(self) -> bool:
                return True

            def serverVersion(self) -> int:
                return 200

            def twsConnectionTime(self) -> str:
                return "paper"

        with TemporaryDirectory() as directory:
            service = IBKRPaperOrderService(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4002,
                    client_id=81,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=5,
                ),
                journal=PaperOrderJournal(
                    Path(directory) / "orders.sqlite3"
                ),
            )
            service._connected = True
            service._client = ConnectedClient()
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
            "us_quant.ibkr_paper_orders.connect_ibkr_client",
            lambda app, _config: setattr(app, "connected", True),
        ):
            service = IBKRPaperOrderService(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4002,
                    client_id=81,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=0.05,
                ),
                journal=PaperOrderJournal(
                    Path(directory) / "orders.sqlite3"
                ),
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
        class ConnectedClient:
            def isConnected(self) -> bool:
                return True

            def serverVersion(self) -> int:
                return 200

            def twsConnectionTime(self) -> str:
                return "paper"

        with TemporaryDirectory() as directory:
            service = IBKRPaperOrderService(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4002,
                    client_id=81,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=5,
                ),
                journal=PaperOrderJournal(
                    Path(directory) / "orders.sqlite3"
                ),
            )
            service._connected = True
            service._client = ConnectedClient()
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
        class ConnectedClient:
            def isConnected(self) -> bool:
                return True

            def serverVersion(self) -> int:
                return 200

            def twsConnectionTime(self) -> str:
                return "paper"

        with TemporaryDirectory() as directory:
            service = IBKRPaperOrderService(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4002,
                    client_id=81,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=5,
                ),
                journal=PaperOrderJournal(Path(directory) / "orders.sqlite3"),
            )
            service._connected = True
            service._client = ConnectedClient()
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
            intent = new_paper_order_intent(
                session_id="session",
                strategy_version_id="version",
                symbol="AAPL",
                side="BUY",
                quantity=1,
                limit_price=Decimal("200"),
                reason="account binding test",
            )
            with self.assertRaisesRegex(IBKRPaperOrderError, "Paper"):
                service.submit(intent)
            self.assertEqual(service.journal.audit_rows(), ())
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
        class ConnectedClient:
            def isConnected(self) -> bool:
                return True

            def serverVersion(self) -> int:
                return 200

            def twsConnectionTime(self) -> str:
                return "paper"

        with TemporaryDirectory() as directory:
            service = IBKRPaperOrderService(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4002,
                    client_id=82,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=5,
                ),
                journal=PaperOrderJournal(Path(directory) / "orders.sqlite3"),
            )
            service._connected = True
            service._client = ConnectedClient()
            service._account = "DU1234567"
            service._snapshot_complete = False
            service.arm(
                session_id="session",
                allowed_symbols=("AAPL",),
                max_order_notional=Decimal("1000"),
            )
            intent = new_paper_order_intent(
                session_id="session",
                strategy_version_id="version",
                symbol="AAPL",
                side="BUY",
                quantity=1,
                limit_price=Decimal("200"),
                reason="snapshot gate test",
            )
            with self.assertRaisesRegex(IBKRPaperOrderError, "快照尚未完成"):
                service.submit(intent)
            self.assertEqual(service.journal.audit_rows(), ())

    def test_error_202_preserves_executed_quantity(self) -> None:
        """H-9: a cancel confirmation racing an actual fill must not zero the
        reported filled quantity."""
        with TemporaryDirectory() as directory:
            journal = PaperOrderJournal(
                Path(directory) / "orders.sqlite3"
            )
            service = IBKRPaperOrderService(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4002,
                    client_id=83,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=5,
                ),
                journal=journal,
            )
            intent = new_paper_order_intent(
                session_id="session",
                strategy_version_id="version",
                symbol="AAPL",
                side="BUY",
                quantity=4,
                limit_price=Decimal("200"),
                reason="error 202 test",
            )
            journal.record_intent(
                intent,
                broker_order_id=7,
                account_alias="DU***17",
            )
            journal.record_execution(
                PaperExecution(
                    intent_id=intent.intent_id,
                    broker_order_id=7,
                    execution_id="execution-202",
                    symbol="AAPL",
                    side="BUY",
                    quantity=Decimal("2"),
                    price=Decimal("200"),
                    occurred_at="2026-07-26T12:00:00+00:00",
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

    def test_exact_intent_cancel_is_idempotent(self) -> None:
        class ConnectedClient:
            def __init__(self) -> None:
                self.cancelled = []

            def isConnected(self) -> bool:
                return True

            def serverVersion(self) -> int:
                return 200

            def twsConnectionTime(self) -> str:
                return "paper"

            def cancelOrder(
                self, order_id: int, manual_time: str
            ) -> None:
                self.cancelled.append((order_id, manual_time))

        with TemporaryDirectory() as directory:
            service = IBKRPaperOrderService(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4002,
                    client_id=81,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=5,
                ),
                journal=PaperOrderJournal(
                    Path(directory) / "orders.sqlite3"
                ),
            )
            intent = new_paper_order_intent(
                session_id="session",
                strategy_version_id="version",
                symbol="AAPL",
                side="BUY",
                quantity=1,
                limit_price=Decimal("200"),
                reason="test cancel",
            )
            client = ConnectedClient()
            service._connected = True
            service._client = client
            service._account = "DU1234567"
            with service._correlation_lock:
                service._intent_by_order[10] = intent
                service._order_by_intent[intent.intent_id] = 10
            self.assertTrue(service.cancel_intent(intent.intent_id))
            self.assertFalse(service.cancel_intent(intent.intent_id))
            self.assertEqual(client.cancelled, [(10, "")])

    def test_concurrent_exact_intent_cancel_is_idempotent(self) -> None:
        class ConnectedClient:
            def __init__(self) -> None:
                self.cancelled = []

            def isConnected(self) -> bool:
                return True

            def serverVersion(self) -> int:
                return 200

            def twsConnectionTime(self) -> str:
                return "paper"

            def cancelOrder(
                self, order_id: int, manual_time: str
            ) -> None:
                self.cancelled.append((order_id, manual_time))

        with TemporaryDirectory() as directory:
            service = IBKRPaperOrderService(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4002,
                    client_id=81,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=5,
                ),
                journal=PaperOrderJournal(
                    Path(directory) / "orders.sqlite3"
                ),
            )
            intent = new_paper_order_intent(
                session_id="session",
                strategy_version_id="version",
                symbol="AAPL",
                side="BUY",
                quantity=1,
                limit_price=Decimal("200"),
                reason="concurrent cancel",
            )
            client = ConnectedClient()
            service._connected = True
            service._client = client
            service._account = "DU1234567"
            with service._correlation_lock:
                service._intent_by_order[10] = intent
                service._order_by_intent[intent.intent_id] = 10
            barrier = Barrier(8)
            results: list[bool] = []

            def cancel() -> None:
                barrier.wait()
                results.append(service.cancel_intent(intent.intent_id))

            threads = [Thread(target=cancel) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(results.count(True), 1)
            self.assertEqual(results.count(False), 7)
            self.assertEqual(client.cancelled, [(10, "")])

    def test_sessions_delegates_to_the_journal_without_touching_sqlite(
        self,
    ) -> None:
        """The adapter must proxy the rollup, not own it.

        ``sessions`` used to run its own SQL against ``self.path`` -- an
        attribute this class never had, so every call raised AttributeError.
        The journal is replaced with a recorder here: the adapter must not
        touch a broker or a database to answer this question.
        """

        class RecordingJournal:
            def __init__(self) -> None:
                self.calls: list[int] = []

            def sessions(self, *, limit: int = 50):
                self.calls.append(limit)
                return ({"session_id": "s-1", "intent_count": limit},)

        with TemporaryDirectory() as directory:
            journal = RecordingJournal()
            service = IBKRPaperOrderService(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4002,
                    client_id=81,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=5,
                ),
                journal=journal,  # type: ignore[arg-type]
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
            service = IBKRPaperOrderService(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4002,
                    client_id=81,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=5,
                ),
                journal=journal,  # type: ignore[arg-type]
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
            service = IBKRPaperOrderService(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4002,
                    client_id=81,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=0.05,
                ),
                journal=PaperOrderJournal(
                    Path(directory) / "orders.sqlite3"
                ),
            )

            def fake_factory(*, sink, epoch):
                built.append((sink, epoch))
                raise IBKRPaperOrderError("stop after wiring")

            with patch(
                "us_quant.ibkr_paper_orders.create_paper_gateway_app",
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
            / "ibkr_paper_orders.py"
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
            / "ibkr_paper_orders.py"
        ).read_text(encoding="utf-8")
        connect_body = source.split("def connect(self)")[1].split(
            "\n    def "
        )[0]

        self.assertIn("create_paper_gateway_app", connect_body)
        self.assertIn("sink=self", connect_body)
        self.assertNotIn("class ", connect_body)

    def test_service_implements_the_gateway_sink_protocol(self) -> None:
        """Every protocol method must exist on the service, one per callback."""

        from us_quant.ibkr_paper_gateway import PaperGatewaySink

        for name in PaperGatewaySink.__protocol_attrs__:
            self.assertTrue(
                callable(getattr(IBKRPaperOrderService, name, None)), name
            )

    def test_gateway_callbacks_reach_the_service_handlers(self) -> None:
        """A callback on a fresh gateway app must land in the service handler.

        This is the wiring the transport extraction could silently break: the
        app is built by the factory and forwards into the live service.
        """

        recorded: list[tuple[int, str]] = []

        with TemporaryDirectory() as directory:
            service = IBKRPaperOrderService(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4002,
                    client_id=81,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=5,
                ),
                journal=PaperOrderJournal(
                    Path(directory) / "orders.sqlite3"
                ),
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
            service = IBKRPaperOrderService(
                IBKRConnectionConfig(
                    host="127.0.0.1",
                    port=4002,
                    client_id=81,
                    api_read_only=False,
                    paper_order_submission_enabled=True,
                    connection_timeout_seconds=5,
                ),
                journal=PaperOrderJournal(
                    Path(directory) / "orders.sqlite3"
                ),
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

        return IBKRPaperOrderService(
            IBKRConnectionConfig(
                host="127.0.0.1",
                port=4002,
                client_id=81,
                api_read_only=False,
                paper_order_submission_enabled=True,
                connection_timeout_seconds=5,
            ),
            journal=PaperOrderJournal(
                Path(directory) / "orders.sqlite3"
            ),
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
            journaled: list[tuple[Any, Any]] = []
            service._record_execution = (  # type: ignore[method-assign]
                lambda contract, execution: journaled.append((contract, execution))
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

            self.assertEqual(journaled, [])
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


if __name__ == "__main__":
    unittest.main()
