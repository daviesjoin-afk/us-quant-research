from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.backtest_workspace import (
    BacktestRequest,
    STRATEGY_SPECS,
    run_backtest,
    save_backtest_run,
)


class BacktestWorkspaceTests(unittest.TestCase):
    def test_all_catalog_strategies_bind_version_and_save_run(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            symbol_root = (
                data_root / "normalized" / "ibkr" / "daily" / "XLF"
            )
            symbol_root.mkdir(parents=True)
            first = date(2025, 1, 1)
            rows = []
            price = Decimal("40")
            for index in range(180):
                price += (
                    Decimal("0.20")
                    if index % 17 < 11
                    else Decimal("-0.25")
                )
                rows.append(
                    {
                        "trading_date": (
                            first + timedelta(days=index)
                        ).isoformat(),
                        "open": str(price),
                        "high": str(price + Decimal("0.5")),
                        "low": str(price - Decimal("0.5")),
                        "close": str(price),
                        "volume": "1000000",
                        "average": str(price),
                        "bar_count": 100,
                    }
                )
            (symbol_root / "sample.json").write_text(
                json.dumps(
                    {
                        "symbol": "XLF",
                        "source_sha256": "data-hash",
                        "source": "unit-test",
                        "price_basis": "raw",
                        "bars": rows,
                    }
                ),
                encoding="utf-8",
            )
            for spec in STRATEGY_SPECS:
                request = BacktestRequest(
                    strategy_id=spec.strategy_id,
                    strategy_version_id=f"{spec.strategy_id}-v1",
                    parameter_hash=f"{spec.strategy_id}-parameters",
                    code_hash="unit-test-code-hash",
                    parameters=spec.default_parameters,
                    symbol="XLF",
                    start_date=first,
                    end_date=first + timedelta(days=179),
                    initial_equity=Decimal("1500"),
                    target_weight=Decimal("1"),
                    per_share_commission=Decimal("0.0035"),
                    minimum_commission=Decimal("0.35"),
                    slippage_bps=Decimal("2"),
                )
                run = run_backtest(request, data_root=data_root)
                path = save_backtest_run(
                    run, output_root=root / "runs"
                )
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    payload["strategy"]["strategy_version_id"],
                    request.strategy_version_id,
                )
                self.assertEqual(
                    payload["strategy"]["parameter_hash"],
                    request.parameter_hash,
                )
                self.assertEqual(
                    payload["strategy"]["code_hash"],
                    request.code_hash,
                )
                self.assertEqual(payload["data"]["data_hash"], "data-hash")
                self.assertTrue(payload["run_hash"])


if __name__ == "__main__":
    unittest.main()
