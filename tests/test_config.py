from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
