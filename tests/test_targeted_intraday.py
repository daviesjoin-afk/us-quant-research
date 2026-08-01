from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.shadow_paper import ShadowPaperEngine, ShadowPaperStore
from us_quant.targeted_intraday import build_targeted_shadow_config


PARAMETERS = {
    "momentum_lookback_minutes": 7,
    "warmup_minutes": 12,
    "maximum_hold_minutes": 35,
    "maximum_trades_per_day": 3,
    "max_position_fraction": "0.08",
    "min_order_notional": "40",
    "commission_per_order": "0.25",
    "slippage_bps": "3",
    "maximum_spread_fraction": "0.003",
    "minimum_momentum": "0.004",
    "maximum_momentum": "0.03",
    "profit_target": "0.015",
    "stop_loss": "0.008",
    "trailing_stop": "0.007",
    "whole_shares": True,
}


class TargetedIntradayTests(unittest.TestCase):
    def test_version_parameters_build_runtime_config(self) -> None:
        config = build_targeted_shadow_config(
            PARAMETERS,
            initial_cash=Decimal("1500"),
            capital_source="IBKR Paper DU***",
            daily_loss_limit=Decimal("15"),
        )
        self.assertEqual(config.momentum_lookback_minutes, 7)
        self.assertEqual(
            config.max_position_fraction, Decimal("0.08")
        )
        self.assertEqual(config.commission_per_order, Decimal("0.25"))

    def test_session_persists_strategy_and_target_provenance(self) -> None:
        with TemporaryDirectory() as directory:
            store = ShadowPaperStore(Path(directory) / "shadow.sqlite3")
            config = build_targeted_shadow_config(
                PARAMETERS,
                initial_cash=Decimal("1500"),
                capital_source="IBKR Paper DU***",
                daily_loss_limit=Decimal("15"),
            )
            engine = ShadowPaperEngine(
                store=store,
                allowed_symbols=("AAPL",),
                config=config,
                strategy_version_id="strategy-version-123",
                parameter_hash="parameter-hash-456",
                target_symbol="AAPL",
            )
            snapshot = engine.start()
            provenance = store.session_provenance(
                snapshot.session_id or ""
            )
        self.assertEqual(
            provenance.strategy_version_id, "strategy-version-123"
        )
        self.assertEqual(provenance.parameter_hash, "parameter-hash-456")
        self.assertEqual(provenance.target_symbol, "AAPL")
        self.assertEqual(provenance.allowed_symbols, ("AAPL",))


if __name__ == "__main__":
    unittest.main()
