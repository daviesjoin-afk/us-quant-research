from datetime import date, timedelta
from decimal import Decimal
import unittest

from us_quant.cross_sectional import CrossSectionalCandidate
from us_quant.executable_research import (
    allocate_whole_share_targets,
    simulate_executable_cross_sectional,
)
from us_quant.market_data import DailyBar
from us_quant.portfolio import SubstitutionRule
from us_quant.universe import UniverseRecord


def record(symbol: str, sector: str) -> UniverseRecord:
    return UniverseRecord(
        symbol=symbol,
        name=symbol,
        exchange="NASDAQ",
        security_type="STK",
        sector=sector,
        leader_tier=1,
        country_status="verified_us",
        eligible_for_research=True,
        eligible_for_trading=True,
        exclusion_reason="",
    )


class ExecutableResearchTests(unittest.TestCase):
    def test_research_budget_can_hold_three_distinct_sectors(self) -> None:
        targets, rejected = allocate_whole_share_targets(
            ["A", "B", "C"],
            records={
                "A": record("A", "Technology"),
                "B": record("B", "Healthcare"),
                "C": record("C", "Financials"),
            },
            prices={"A": 50, "B": 50, "C": 50},
            substitutions={},
            equity=1500,
            max_gross_risk_pct=0.30,
            max_position_risk_pct=0.10,
            max_holdings=3,
            minimum_commission=0.35,
            allow_short_term_substitutions=False,
        )
        self.assertEqual(rejected, 0)
        self.assertEqual(len(targets), 3)
        self.assertLessEqual(
            sum(target.risk_exposure for target in targets),
            450,
        )

    def test_muu_uses_two_times_risk_and_whole_shares(self) -> None:
        targets, _ = allocate_whole_share_targets(
            ["MU"],
            records={"MU": record("MU", "Technology")},
            prices={"MU": 990, "MUU": 36},
            substitutions={
                "MU": SubstitutionRule(
                    source_symbol="MU",
                    execution_symbol="MUU",
                    exposure_multiplier=Decimal("2"),
                    holding_mode="intraday_or_short_term",
                )
            },
            equity=1500,
            max_gross_risk_pct=0.10,
            max_position_risk_pct=0.10,
            max_holdings=3,
            minimum_commission=0.35,
            allow_short_term_substitutions=True,
        )
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].execution_symbol, "MUU")
        self.assertEqual(targets[0].quantity, 2)
        self.assertEqual(targets[0].market_value, 72)
        self.assertEqual(targets[0].risk_exposure, 144)

    def test_unaffordable_leader_backfills_next_sector_candidate(
        self,
    ) -> None:
        targets, rejected = allocate_whole_share_targets(
            ["EXPENSIVE", "AFFORDABLE"],
            records={
                "EXPENSIVE": record("EXPENSIVE", "Technology"),
                "AFFORDABLE": record("AFFORDABLE", "Healthcare"),
            },
            prices={"EXPENSIVE": 900, "AFFORDABLE": 100},
            substitutions={},
            equity=1500,
            max_gross_risk_pct=0.10,
            max_position_risk_pct=0.10,
            max_holdings=3,
            minimum_commission=0.35,
            allow_short_term_substitutions=False,
        )
        self.assertEqual(rejected, 1)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].execution_symbol, "AFFORDABLE")
        self.assertEqual(targets[0].quantity, 1)
        self.assertLessEqual(targets[0].risk_exposure, 150)

    def test_long_horizon_does_not_silently_use_muu(self) -> None:
        targets, rejected = allocate_whole_share_targets(
            ["MU"],
            records={"MU": record("MU", "Technology")},
            prices={"MU": 990, "MUU": 36},
            substitutions={
                "MU": SubstitutionRule(
                    source_symbol="MU",
                    execution_symbol="MUU",
                    exposure_multiplier=Decimal("2"),
                    holding_mode="intraday_or_short_term",
                )
            },
            equity=1500,
            max_gross_risk_pct=0.10,
            max_position_risk_pct=0.10,
            max_holdings=3,
            minimum_commission=0.35,
            allow_short_term_substitutions=False,
        )
        self.assertEqual(targets, ())
        self.assertEqual(rejected, 1)

    def test_muu_is_forced_out_after_five_trading_days(self) -> None:
        first = date(2025, 1, 1)

        def bars(symbol: str, base: Decimal) -> tuple[DailyBar, ...]:
            result = []
            for index in range(230):
                close = (
                    base
                    + Decimal(index) * Decimal("0.20")
                    + Decimal(index % 7) * Decimal("0.03")
                )
                trading_date = first + timedelta(days=index)
                result.append(
                    DailyBar(
                        symbol=symbol,
                        trading_date=trading_date,
                        open=close,
                        high=close + Decimal("0.5"),
                        low=close - Decimal("0.5"),
                        close=close,
                        volume=Decimal("1000000"),
                        average=close,
                        bar_count=100,
                    )
                )
            return tuple(result)

        data = {
            "SPY": bars("SPY", Decimal("400")),
            "MU": bars("MU", Decimal("900")),
            "MUU": bars("MUU", Decimal("30")),
        }
        simulation = simulate_executable_cross_sectional(
            CrossSectionalCandidate(
                lookback_days=5,
                rebalance_days=5,
                top_n=1,
            ),
            data=data,
            records={"MU": record("MU", "Technology")},
            substitutions={
                "MU": SubstitutionRule(
                    source_symbol="MU",
                    execution_symbol="MUU",
                    exposure_multiplier=Decimal("2"),
                    holding_mode="intraday_or_short_term",
                )
            },
            start_date=data["SPY"][205].trading_date,
            end_date=data["SPY"][224].trading_date,
            initial_equity=1500,
            max_gross_risk_pct=0.10,
            max_position_risk_pct=0.10,
            per_share_commission=0.0035,
            minimum_commission=0.35,
            slippage_bps=2,
            max_substitution_holding_days=5,
        )
        self.assertGreaterEqual(
            simulation.substitution_forced_exit_count, 2
        )
        self.assertLessEqual(
            simulation.max_substitution_holding_days_observed, 5
        )
