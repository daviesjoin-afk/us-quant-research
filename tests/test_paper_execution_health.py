from datetime import datetime, timezone
from decimal import Decimal
import unittest

from us_quant.auto_quant import (
    AutoQuantPosition,
    AutoQuantSnapshot,
)
from us_quant.ibkr_paper_orders import (
    PaperBrokerPosition,
    PaperBrokerState,
    PaperOrderConnection,
    PaperOrderReconciliation,
)
from us_quant.paper_execution_health import (
    evaluate_paper_execution_health,
)


NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


class PaperExecutionHealthTests(unittest.TestCase):
    def test_matching_broker_and_local_position_is_healthy(self) -> None:
        result = evaluate_paper_execution_health(
            connection=_connection(),
            broker_state=_broker_state(quantity=Decimal("2")),
            engine_snapshot=_snapshot(quantity=2),
            reconciliations=(),
            candidate_symbols=frozenset({"AAPL"}),
            now=NOW,
        )
        self.assertEqual(result.status, "HEALTHY")
        self.assertTrue(result.safe_to_continue)

    def test_terminal_fill_without_execution_is_halt(self) -> None:
        result = evaluate_paper_execution_health(
            connection=_connection(),
            broker_state=_broker_state(quantity=Decimal("0")),
            engine_snapshot=_snapshot(quantity=0),
            reconciliations=(
                PaperOrderReconciliation(
                    intent_id="intent",
                    session_id="session",
                    broker_order_id=10,
                    symbol="AAPL",
                    side="BUY",
                    intended_quantity=Decimal("2"),
                    latest_status="Filled",
                    reported_filled=Decimal("2"),
                    reported_remaining=Decimal("0"),
                    executed_quantity=Decimal("0"),
                    reconciled=False,
                    terminal=True,
                    reason="mismatch",
                    observed_at=NOW.isoformat(),
                ),
            ),
            candidate_symbols=frozenset({"AAPL"}),
            now=NOW,
        )
        self.assertEqual(result.status, "HALT")
        self.assertFalse(result.safe_to_continue)

    def test_disconnect_and_unexpected_position_are_halt(self) -> None:
        connection = _connection(connected=False)
        broker = _broker_state(
            quantity=Decimal("1"), symbol="BABA"
        )
        result = evaluate_paper_execution_health(
            connection=connection,
            broker_state=broker,
            engine_snapshot=_snapshot(quantity=0),
            reconciliations=(),
            candidate_symbols=frozenset({"AAPL"}),
            now=NOW,
        )
        self.assertEqual(result.status, "HALT")
        codes = {issue.code for issue in result.issues}
        self.assertIn("broker_disconnected", codes)
        self.assertIn("unexpected_symbol", codes)


def _connection(*, connected: bool = True) -> PaperOrderConnection:
    return PaperOrderConnection(
        connected=connected,
        account_alias="DU***17",
        server_version=200,
        connection_time=NOW.isoformat(),
        next_order_id=10,
    )


def _broker_state(
    *, quantity: Decimal, symbol: str = "AAPL"
) -> PaperBrokerState:
    return PaperBrokerState(
        account_alias="DU***17",
        net_liquidation=Decimal("1000000"),
        cash=Decimal("999600"),
        available_funds=Decimal("999600"),
        buying_power=Decimal("1000000"),
        daily_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
        positions=(
            (
                PaperBrokerPosition(
                    symbol=symbol,
                    quantity=quantity,
                    average_cost=Decimal("200"),
                ),
            )
            if quantity
            else ()
        ),
        observed_at=NOW.isoformat(),
    )


def _snapshot(*, quantity: int) -> AutoQuantSnapshot:
    return AutoQuantSnapshot(
        session_id="session",
        active=True,
        strategy_version_id="version",
        parameter_hash="hash",
        candidate_count=3,
        initial_equity=Decimal("1000000"),
        estimated_cash=Decimal("999600"),
        estimated_equity=Decimal("1000000"),
        estimated_realized_pnl=Decimal("0"),
        estimated_unrealized_pnl=Decimal("0"),
        positions=(
            (
                AutoQuantPosition(
                    symbol="AAPL",
                    quantity=quantity,
                    average_price=Decimal("200"),
                    opened_at=NOW.isoformat(),
                    high_water=Decimal("200"),
                    provider="IBKR Paper execution",
                ),
            )
            if quantity
            else ()
        ),
        fills=(),
        intents=(),
        pending_orders=(),
        trades_today=0,
        trading_day="2026-07-26",
        status="test",
        observed_at=NOW.isoformat(),
    )


if __name__ == "__main__":
    unittest.main()
