from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.account_ledger import AccountLedger
from us_quant.ibkr_readonly import (
    AccountMetric,
    AccountPnl,
    BrokerPosition,
    IBKRReadOnlySnapshot,
    MarketQuote,
    PositionPnl,
)
from us_quant.portfolio_view import build_portfolio_view


def snapshot() -> IBKRReadOnlySnapshot:
    return IBKRReadOnlySnapshot(
        server_version=223,
        connection_time="20260724",
        accounts=("DU1234567",),
        metrics=(
            AccountMetric(
                "DU1234567", "NetLiquidation", "1500", "USD"
            ),
            AccountMetric(
                "DU1234567", "TotalCashValue", "1428", "USD"
            ),
        ),
        positions=(
            BrokerPosition(
                account="DU1234567",
                con_id=99,
                symbol="MUU",
                local_symbol="MUU",
                security_type="STK",
                exchange="NASDAQ",
                currency="USD",
                quantity=Decimal("2"),
                average_cost=Decimal("35"),
            ),
        ),
        contracts=(),
        quotes=(
            MarketQuote(
                request_id=1,
                symbol="MUU",
                market_data_type=1,
                bid=Decimal("35.95"),
                ask=Decimal("36.05"),
                last=Decimal("36"),
                close=Decimal("35"),
            ),
        ),
        messages=(),
        account_pnl=(
            AccountPnl(
                2,
                "DU1234567",
                Decimal("4"),
                Decimal("2"),
                Decimal("2"),
            ),
        ),
        position_pnl=(
            PositionPnl(
                3,
                "DU1234567",
                99,
                Decimal("2"),
                Decimal("2"),
                Decimal("2"),
                Decimal("0"),
                Decimal("72"),
            ),
        ),
    )

def with_account(account: str) -> IBKRReadOnlySnapshot:
    base = snapshot()
    return IBKRReadOnlySnapshot(
        server_version=base.server_version,
        connection_time=base.connection_time,
        accounts=(account,),
        metrics=tuple(
            AccountMetric(account, row.tag, row.value, row.currency)
            for row in base.metrics
        ),
        positions=(),
        contracts=(),
        quotes=(),
        messages=(),
    )


class PortfolioViewTests(unittest.TestCase):
    def test_configured_multiplier_changes_risk_not_market_value(self) -> None:
        view = build_portfolio_view(
            snapshot(),
            observed_at="2026-07-24T00:00:00+00:00",
            exposure_multipliers={"MUU": Decimal("2")},
        )
        position = view.positions[0]
        self.assertEqual(position.market_value, Decimal("72"))
        self.assertEqual(position.risk_exposure, Decimal("144"))
        self.assertEqual(
            position.local_unrealized_pnl, Decimal("2")
        )
        self.assertFalse(position.stale)
        self.assertEqual(view.account.daily_pnl, Decimal("4"))

    def test_missing_mark_never_becomes_zero_pnl(self) -> None:
        base = snapshot()
        missing = IBKRReadOnlySnapshot(
            server_version=base.server_version,
            connection_time=base.connection_time,
            accounts=base.accounts,
            metrics=base.metrics,
            positions=base.positions,
            contracts=(),
            quotes=(),
            messages=(),
        )
        position = build_portfolio_view(missing).positions[0]
        self.assertIsNone(position.mark)
        self.assertIsNone(position.market_value)
        self.assertIsNone(position.local_unrealized_pnl)
        self.assertTrue(position.stale)

    def test_non_du_account_cannot_be_labeled_paper(self) -> None:
        with self.assertRaisesRegex(ValueError, "非 DU"):
            build_portfolio_view(
                with_account("U1234567"),
                environment="paper",
            )

    def test_du_account_cannot_be_labeled_live(self) -> None:
        with self.assertRaisesRegex(ValueError, "DU 模拟账户"):
            build_portfolio_view(
                snapshot(),
                environment="live",
            )

    def test_ledger_keeps_environments_isolated(self) -> None:
        paper = build_portfolio_view(
            snapshot(), observed_at="2026-07-24T00:00:00+00:00"
        )
        with TemporaryDirectory() as directory:
            ledger = AccountLedger(Path(directory) / "equity.sqlite3")
            ledger.append(paper.account)
            paper_rows = ledger.list_points(
                environment="paper",
                account_alias=paper.account.account_alias,
            )
            live_rows = ledger.list_points(
                environment="live",
                account_alias=paper.account.account_alias,
            )
        self.assertEqual(len(paper_rows), 1)
        self.assertEqual(live_rows, ())
