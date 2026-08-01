from decimal import Decimal
import unittest

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.ibkr_readonly import (
    AccountMetric,
    BrokerPosition,
    ContractRecord,
    IBKRReadOnlyError,
    IBKRReadOnlySnapshot,
    MarketQuote,
    ensure_readonly_paper_config,
    intraday_market_data_reasons,
    mask_account_id,
    snapshot_to_redacted_dict,
)


class IBKRReadOnlyTests(unittest.TestCase):
    def test_masks_account_ids(self) -> None:
        self.assertEqual(mask_account_id("DU1234567"), "DU***67")
        self.assertEqual(mask_account_id("1234"), "****")

    def test_rejects_live_gateway_port(self) -> None:
        config = IBKRConnectionConfig(
            host="127.0.0.1",
            port=4001,
            client_id=17,
            api_read_only=True,
            paper_order_submission_enabled=False,
            connection_timeout_seconds=2,
        )
        with self.assertRaises(IBKRReadOnlyError):
            ensure_readonly_paper_config(config)

    def test_redacted_snapshot_never_exposes_raw_account_id(self) -> None:
        snapshot = IBKRReadOnlySnapshot(
            server_version=180,
            connection_time="20260724 20:00:00 CST",
            accounts=("DU1234567",),
            metrics=(
                AccountMetric(
                    account="DU1234567",
                    tag="NetLiquidation",
                    value="1000000",
                    currency="USD",
                ),
            ),
            positions=(
                BrokerPosition(
                    account="DU1234567",
                    con_id=1,
                    symbol="MUU",
                    local_symbol="MUU",
                    security_type="STK",
                    exchange="NASDAQ",
                    currency="USD",
                    quantity=Decimal("2"),
                    average_cost=Decimal("36"),
                ),
            ),
            contracts=(
                ContractRecord(
                    request_id=9100,
                    con_id=2,
                    symbol="MU",
                    local_symbol="MU",
                    security_type="STK",
                    primary_exchange="NASDAQ",
                    currency="USD",
                    trading_class="NMS",
                    long_name="MICRON TECHNOLOGY INC",
                    min_tick=Decimal("0.01"),
                ),
            ),
            quotes=(),
            messages=(),
        )
        output = snapshot_to_redacted_dict(snapshot)
        self.assertNotIn("DU1234567", str(output))
        self.assertEqual(output["accounts"], ["DU***67"])
        self.assertEqual(output["positions"][0]["quantity"], "2")

    def test_delayed_quotes_are_not_intraday_ready(self) -> None:
        snapshot = IBKRReadOnlySnapshot(
            server_version=223,
            connection_time="20260724",
            accounts=(),
            metrics=(),
            positions=(),
            contracts=(),
            quotes=(
                MarketQuote(
                    request_id=9200,
                    symbol="MU",
                    market_data_type=3,
                    bid=Decimal("969"),
                    ask=Decimal("970"),
                    last=Decimal("969.5"),
                    close=Decimal("990"),
                ),
                MarketQuote(
                    request_id=9201,
                    symbol="MUU",
                    market_data_type=3,
                    bid=Decimal("34.9"),
                    ask=Decimal("35"),
                    last=Decimal("34.95"),
                    close=Decimal("36.49"),
                ),
            ),
            messages=(),
        )
        reasons = intraday_market_data_reasons(
            snapshot, required_symbols=("MU", "MUU")
        )
        self.assertTrue(reasons)
        self.assertIn("MU market data is not real-time type 1", reasons)


if __name__ == "__main__":
    unittest.main()
