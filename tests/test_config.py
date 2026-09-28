from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.config import load_config


class ConfigTests(unittest.TestCase):
    def test_paper_config_is_safe_by_default(self) -> None:
        config = load_config(Path("configs/paper.toml"))
        self.assertEqual(config.environment.value, "paper")
        self.assertFalse(config.live_trading_enabled)
        self.assertTrue(config.whole_shares_only)
        self.assertFalse(config.allow_margin_borrowing)
        self.assertEqual(
            config.research_portfolio.max_gross_exposure_pct,
            config.risk_limits.max_position_exposure_pct * 5,
        )
        self.assertEqual(
            config.research_portfolio.max_position_exposure_pct,
            config.risk_limits.max_position_exposure_pct,
        )
        self.assertEqual(config.ibkr.host, "127.0.0.1")
        self.assertEqual(config.ibkr.port, 4002)
        self.assertTrue(config.ibkr.api_read_only)
        self.assertFalse(config.ibkr.paper_order_submission_enabled)
        self.assertEqual(config.substitutions, {})
        self.assertFalse(config.portfolio_capital_policy.is_configured)
        self.assertIn("missing", config.portfolio_policy_config_error or "")

    def test_explicit_portfolio_policy_loads_allocations(self) -> None:
        source = Path("configs/paper.toml").read_text(encoding="utf-8")
        source += """

[portfolio]
total_capital_limit = "1000"
max_gross_exposure = "900"
max_net_exposure = "800"
max_single_position_notional = "300"
max_symbol_concentration = "0.5"
max_strategy_concentration = "0.5"
max_positions = 5
max_open_orders = 5

[[portfolio.allocations]]
strategy_version_id = "strategy-a"
capital_weight = "1"
max_capital = "1000"
max_gross_exposure = "900"
enabled = true
"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "paper.toml"
            path.write_text(source, encoding="utf-8")
            config = load_config(path)

        self.assertTrue(config.portfolio_capital_policy.is_configured)
        self.assertEqual(
            config.portfolio_capital_policy.allocations[0].strategy_version_id,
            "strategy-a",
        )
        self.assertIsNone(config.portfolio_policy_config_error)

    def test_invalid_portfolio_policy_falls_back_to_disabled_policy(self) -> None:
        source = Path("configs/paper.toml").read_text(encoding="utf-8")
        source += """

[portfolio]
total_capital_limit = "1000"
"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "paper.toml"
            path.write_text(source, encoding="utf-8")
            config = load_config(path)

        self.assertFalse(config.portfolio_capital_policy.is_configured)
        self.assertIsNotNone(config.portfolio_policy_config_error)


if __name__ == "__main__":
    unittest.main()
