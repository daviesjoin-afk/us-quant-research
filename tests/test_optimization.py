from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from us_quant.backtest import CostModel
from us_quant.domain import Bar, MarketSlice
from us_quant.optimization import (
    MovingAverageCandidate,
    walk_forward_moving_average,
)
from us_quant.portfolio import IntegerPositionSizer, SubstitutionRule


def _slices(count: int) -> tuple[MarketSlice, ...]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    output: list[MarketSlice] = []
    for index in range(count):
        timestamp = start + timedelta(days=index)
        leader_close = Decimal("100") + Decimal(index) / Decimal("10")
        execution_close = Decimal("20") + Decimal(index) / Decimal("20")
        output.append(
            MarketSlice(
                timestamp=timestamp,
                bars={
                    "LEADER": Bar(
                        symbol="LEADER",
                        timestamp=timestamp,
                        open=leader_close,
                        high=leader_close + 1,
                        low=leader_close - 1,
                        close=leader_close,
                        volume=1_000_000,
                    ),
                    "EXECUTION": Bar(
                        symbol="EXECUTION",
                        timestamp=timestamp,
                        open=execution_close,
                        high=execution_close + 1,
                        low=execution_close - 1,
                        close=execution_close,
                        volume=1_000_000,
                    ),
                },
            )
        )
    return tuple(output)


class OptimizationTests(unittest.TestCase):
    def test_walk_forward_evaluates_only_post_training_days(self) -> None:
        result = walk_forward_moving_average(
            _slices(100),
            signal_symbol="LEADER",
            initial_equity=Decimal("1500"),
            position_sizer=IntegerPositionSizer(
                {
                    "LEADER": SubstitutionRule(
                        source_symbol="LEADER",
                        execution_symbol="EXECUTION",
                        exposure_multiplier=Decimal("2"),
                        holding_mode="intraday_or_short_term",
                    )
                }
            ),
            cost_model=CostModel(
                per_share_commission=Decimal("0.0035"),
                minimum_commission=Decimal("0.35"),
                slippage_bps=Decimal("2"),
            ),
            candidates=(
                MovingAverageCandidate(3, 10),
                MovingAverageCandidate(5, 20),
            ),
            target_weight=Decimal("0.10"),
            minimum_train_days=40,
            test_days=20,
        )
        self.assertEqual(len(result.folds), 3)
        self.assertEqual(result.out_of_sample_days, 60)

    def test_training_window_must_exceed_longest_candidate(self) -> None:
        with self.assertRaises(ValueError):
            walk_forward_moving_average(
                _slices(100),
                signal_symbol="LEADER",
                initial_equity=Decimal("1500"),
                position_sizer=IntegerPositionSizer({}),
                cost_model=CostModel(
                    per_share_commission=Decimal("0"),
                    minimum_commission=Decimal("0"),
                    slippage_bps=Decimal("0"),
                ),
                candidates=(MovingAverageCandidate(10, 50),),
                target_weight=Decimal("0.10"),
                minimum_train_days=50,
                test_days=20,
            )

    def test_incomplete_tail_is_excluded_by_default(self) -> None:
        result = walk_forward_moving_average(
            _slices(101),
            signal_symbol="LEADER",
            initial_equity=Decimal("1500"),
            position_sizer=IntegerPositionSizer(
                {
                    "LEADER": SubstitutionRule(
                        source_symbol="LEADER",
                        execution_symbol="EXECUTION",
                        exposure_multiplier=Decimal("2"),
                        holding_mode="intraday_or_short_term",
                    )
                },
                force_substitution_symbols=frozenset({"LEADER"}),
            ),
            cost_model=CostModel(
                per_share_commission=Decimal("0.0035"),
                minimum_commission=Decimal("0.35"),
                slippage_bps=Decimal("2"),
            ),
            candidates=(
                MovingAverageCandidate(3, 10),
                MovingAverageCandidate(5, 20),
            ),
            target_weight=Decimal("0.10"),
            minimum_train_days=40,
            test_days=20,
        )
        self.assertEqual(len(result.folds), 3)
        self.assertEqual(result.out_of_sample_days, 60)
        self.assertEqual(result.excluded_tail_days, 1)


if __name__ == "__main__":
    unittest.main()
