from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

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
            service._intent_by_order[10] = intent
            service._order_by_intent[intent.intent_id] = 10
            self.assertTrue(service.cancel_intent(intent.intent_id))
            self.assertFalse(service.cancel_intent(intent.intent_id))
            self.assertEqual(client.cancelled, [(10, "")])


if __name__ == "__main__":
    unittest.main()
