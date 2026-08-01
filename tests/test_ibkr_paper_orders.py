from decimal import Decimal
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Thread
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.ibkr_paper_orders import (
    IBKRPaperOrderService,
    IBKRPaperOrderError,
    PaperExecution,
    PaperOrderJournal,
    PaperOrderUpdate,
    ensure_paper_order_config,
    new_paper_order_intent,
    validate_paper_order_intent,
)


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


if __name__ == "__main__":
    unittest.main()
