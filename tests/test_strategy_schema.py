import unittest

from us_quant.strategy_schema import (
    StrategyParameterError,
    validate_strategy_parameters,
)


class StrategySchemaTests(unittest.TestCase):
    @staticmethod
    def targeted_parameters() -> dict[str, object]:
        return {
            "momentum_lookback_minutes": 5,
            "warmup_minutes": 10,
            "maximum_hold_minutes": 45,
            "maximum_trades_per_day": 4,
            "max_position_fraction": "0.10",
            "min_order_notional": "50",
            "commission_per_order": "0.35",
            "slippage_bps": "2",
            "maximum_spread_fraction": "0.002",
            "minimum_momentum": "0.0035",
            "maximum_momentum": "0.025",
            "profit_target": "0.012",
            "stop_loss": "0.007",
            "trailing_stop": "0.006",
            "whole_shares": True,
        }

    def test_dual_ma_requires_short_below_long(self) -> None:
        with self.assertRaises(StrategyParameterError):
            validate_strategy_parameters(
                "dual-ma-trend",
                {
                    "short_window": 100,
                    "long_window": 20,
                    "whole_shares": True,
                },
            )

    def test_donchian_and_rsi_ranges_are_validated(self) -> None:
        with self.assertRaises(StrategyParameterError):
            validate_strategy_parameters(
                "donchian-breakout",
                {
                    "entry_window": 20,
                    "exit_window": 20,
                    "whole_shares": True,
                },
            )
        with self.assertRaises(StrategyParameterError):
            validate_strategy_parameters(
                "rsi-mean-reversion",
                {
                    "window": 5,
                    "entry_threshold": 60,
                    "exit_threshold": 40,
                    "whole_shares": True,
                },
            )

    def test_whole_share_constraint_cannot_be_disabled(self) -> None:
        with self.assertRaises(StrategyParameterError):
            validate_strategy_parameters(
                "buy-hold", {"whole_shares": False}
            )

    def test_targeted_intraday_parameters_are_symbol_agnostic(self) -> None:
        validated = validate_strategy_parameters(
            "intraday-targeted-t",
            self.targeted_parameters(),
        )
        self.assertNotIn("symbol", validated)
        with self.assertRaises(StrategyParameterError):
            invalid = self.targeted_parameters()
            invalid["momentum_lookback_minutes"] = 1
            validate_strategy_parameters("intraday-targeted-t", invalid)

    def test_auto_rotation_continuity_filter_matches_lookback(
        self,
    ) -> None:
        invalid = self.targeted_parameters()
        invalid["minimum_positive_steps"] = 6
        invalid["maximum_one_minute_move"] = "0.01"
        invalid["entry_order_timeout_seconds"] = 45
        with self.assertRaises(StrategyParameterError):
            validate_strategy_parameters(
                "intraday-auto-rotation", invalid
            )


if __name__ == "__main__":
    unittest.main()
