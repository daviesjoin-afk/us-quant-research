from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from us_quant.intraday_universe import (
    select_intraday_watchlist,
    select_paper_rotation_rows,
)
from us_quant.scanner import MarketScan, ScanResult
from us_quant.universe import UniverseRecord, UniverseSnapshot


def result(
    symbol: str,
    *,
    sector: str,
    price: float,
    score: float,
    tier: int = 1,
    execution_symbol: str | None = None,
    eligible: bool = True,
    average_dollar_volume_20d: float = 100_000_000,
) -> ScanResult:
    return ScanResult(
        symbol=symbol,
        execution_symbol=execution_symbol or symbol,
        name=symbol,
        sector=sector,
        leader_tier=tier,
        security_type="STK",
        trading_date=date(2026, 7, 23),
        close=price,
        execution_price=price,
        whole_share_capacity=int(1500 / price),
        average_dollar_volume_20d=average_dollar_volume_20d,
        return_20d=0.02,
        return_63d=0.05,
        volatility_20d=0.2,
        drawdown_252d=-0.1,
        rsi_14d=55,
        atr_pct_14d=0.02,
        above_sma_50=True,
        above_sma_200=True,
        score=score,
        signal="趋势候选",
        research_eligible=True,
        trade_eligible=eligible,
        reason="test",
    )


class IntradayUniverseTests(unittest.TestCase):
    def test_paper_shortlist_uses_paper_capital_not_small_scan_budget(
        self,
    ) -> None:
        expensive = result(
            "EXP", sector="科技", price=900, score=99, eligible=False
        )
        blocked = result(
            "CN", sector="科技", price=20, score=100, eligible=True
        )
        scan = MarketScan(
            generated_at=datetime.now(timezone.utc),
            capital=1500,
            data_date=date(2026, 7, 23),
            results=(blocked, expensive),
            skipped={},
        )
        universe = UniverseSnapshot(
            generated_at=datetime.now(timezone.utc),
            source_timestamps={},
            records=(
                UniverseRecord(
                    symbol="EXP",
                    name="Expensive US Leader",
                    exchange="NYSE",
                    security_type="STK",
                    leader_tier=1,
                    eligible_for_research=True,
                    eligible_for_trading=True,
                ),
                UniverseRecord(
                    symbol="CN",
                    name="Blocked",
                    exchange="NYSE",
                    security_type="STK",
                    leader_tier=1,
                    eligible_for_research=False,
                    eligible_for_trading=False,
                ),
            ),
        )
        selected = select_paper_rotation_rows(
            scan,
            universe,
            capital=Decimal("1000000"),
            max_position_fraction=Decimal("0.10"),
            limit=3,
        )
        self.assertEqual(tuple(row.symbol for row in selected), ("EXP",))

    def test_paper_shortlist_can_prioritize_liquidity(self) -> None:
        low_liquidity = result(
            "LOW",
            sector="Tech",
            price=100,
            score=99,
            average_dollar_volume_20d=100_000_000,
        )
        high_liquidity = result(
            "HIGH",
            sector="Finance",
            price=100,
            score=10,
            average_dollar_volume_20d=1_000_000_000,
        )
        scan = MarketScan(
            generated_at=datetime.now(timezone.utc),
            capital=1500,
            data_date=date(2026, 7, 23),
            results=(low_liquidity, high_liquidity),
            skipped={},
        )
        universe = UniverseSnapshot(
            generated_at=datetime.now(timezone.utc),
            source_timestamps={},
            records=tuple(
                UniverseRecord(
                    symbol=symbol,
                    name=symbol,
                    exchange="NYSE",
                    security_type="STK",
                    leader_tier=1,
                    eligible_for_research=True,
                    eligible_for_trading=True,
                )
                for symbol in ("LOW", "HIGH")
            ),
        )
        kwargs = {
            "capital": Decimal("1000000"),
            "max_position_fraction": Decimal("0.10"),
            "limit": 3,
        }

        default_selected = select_paper_rotation_rows(scan, universe, **kwargs)
        liquidity_selected = select_paper_rotation_rows(
            scan, universe, liquidity_first=True, **kwargs
        )

        self.assertEqual(
            tuple(row.symbol for row in default_selected), ("LOW", "HIGH")
        )
        self.assertEqual(
            tuple(row.symbol for row in liquidity_selected), ("HIGH", "LOW")
        )

    def test_excluded_reference_does_not_consume_candidate_limit(self) -> None:
        rows = (
            result(
                "SPY",
                sector="ETF",
                price=600,
                score=100,
                average_dollar_volume_20d=50_000_000_000,
            ),
            result("A", sector="Tech", price=100, score=90),
            result("B", sector="Finance", price=100, score=80),
            result("C", sector="Health", price=100, score=70),
        )
        scan = MarketScan(
            generated_at=datetime.now(timezone.utc),
            capital=1500,
            data_date=date(2026, 7, 23),
            results=rows,
            skipped={},
        )
        universe = UniverseSnapshot(
            generated_at=datetime.now(timezone.utc),
            source_timestamps={},
            records=tuple(
                UniverseRecord(
                    symbol=row.symbol,
                    name=row.symbol,
                    exchange="NYSE",
                    security_type="STK",
                    leader_tier=1,
                    eligible_for_research=True,
                    eligible_for_trading=True,
                )
                for row in rows
            ),
        )

        selected = select_paper_rotation_rows(
            scan,
            universe,
            capital=Decimal("1000000"),
            max_position_fraction=Decimal("0.10"),
            limit=3,
            liquidity_first=True,
            excluded_symbols=("SPY",),
        )

        self.assertEqual(
            tuple(row.symbol for row in selected), ("A", "B", "C")
        )

    def test_diversifies_and_respects_small_account_cap(self) -> None:
        scan = MarketScan(
            generated_at=datetime.now(timezone.utc),
            capital=1500,
            data_date=date(2026, 7, 23),
            results=(
                result("A", sector="科技", price=100, score=90),
                result("B", sector="科技", price=90, score=80),
                result("C", sector="科技", price=80, score=70),
                result("D", sector="金融", price=50, score=60),
                result("EXP", sector="工业", price=900, score=99),
                result(
                    "QQQ",
                    sector="科技",
                    price=50,
                    score=95,
                    execution_symbol="TQQQ",
                ),
            ),
            skipped={},
        )
        selected = select_intraday_watchlist(
            scan,
            maximum_per_sector=2,
        )
        self.assertNotIn("EXP", selected)
        self.assertIn("D", selected)
        self.assertLessEqual(
            sum(symbol in {"A", "B", "C", "TQQQ"} for symbol in selected),
            2,
        )

    def test_configured_multiplier_reduces_notional_cap(self) -> None:
        scan = MarketScan(
            generated_at=datetime.now(timezone.utc),
            capital=1500,
            data_date=date(2026, 7, 23),
            results=(
                result(
                    "QQQ",
                    sector="科技",
                    price=80,
                    score=95,
                    execution_symbol="TQQQ",
                ),
                result("BAC", sector="金融", price=50, score=80),
            ),
            skipped={},
        )
        selected = select_intraday_watchlist(
            scan,
            risk_multipliers={"TQQQ": Decimal("2")},
        )
        self.assertNotIn("TQQQ", selected)
        self.assertIn("BAC", selected)


if __name__ == "__main__":
    unittest.main()
