from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.domain import OrderIntent, OrderStatus, Side
from us_quant.oms import (
    InvalidOrderTransition,
    OrderManager,
    SQLiteOrderJournal,
)


class OrderManagerTests(unittest.TestCase):
    def test_journal_is_append_only_and_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            journal = SQLiteOrderJournal(Path(directory) / "orders.sqlite3")
            manager = OrderManager(journal)
            intent = OrderIntent.create(
                signal_symbol="MU",
                execution_symbol="MUU",
                side=Side.BUY,
                quantity=2,
                estimated_price=Decimal("36"),
                exposure_multiplier=Decimal("2"),
            )
            self.assertTrue(manager.register_intent(intent))
            self.assertFalse(manager.register_intent(intent))
            self.assertTrue(
                manager.transition(
                    order_id=intent.order_id,
                    status=OrderStatus.RISK_APPROVED,
                    idempotency_key=f"{intent.order_id}:risk-approved",
                )
            )
            self.assertTrue(
                manager.transition(
                    order_id=intent.order_id,
                    status=OrderStatus.SUBMITTING,
                    idempotency_key=f"{intent.order_id}:submitting",
                )
            )
            self.assertTrue(
                manager.transition(
                    order_id=intent.order_id,
                    status=OrderStatus.ACKNOWLEDGED,
                    idempotency_key="broker-event-123",
                )
            )
            self.assertFalse(
                manager.transition(
                    order_id=intent.order_id,
                    status=OrderStatus.ACKNOWLEDGED,
                    idempotency_key="broker-event-123",
                )
            )
            self.assertEqual(
                journal.current_status(intent.order_id),
                OrderStatus.ACKNOWLEDGED,
            )

    def test_invalid_transition_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            journal = SQLiteOrderJournal(Path(directory) / "orders.sqlite3")
            manager = OrderManager(journal)
            intent = OrderIntent.create(
                signal_symbol="X",
                execution_symbol="X",
                side=Side.BUY,
                quantity=1,
                estimated_price=Decimal("10"),
            )
            manager.register_intent(intent)
            with self.assertRaises(InvalidOrderTransition):
                manager.transition(
                    order_id=intent.order_id,
                    status=OrderStatus.FILLED,
                    idempotency_key="impossible-fill",
                )


if __name__ == "__main__":
    unittest.main()

