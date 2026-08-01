from __future__ import annotations

import json
import unittest

from us_quant.finnhub_stream import (
    FinnhubCredentialsMissing,
    FinnhubTradeStream,
)


class FinnhubStreamTests(unittest.TestCase):
    def test_credentials_are_required(self) -> None:
        with self.assertRaises(FinnhubCredentialsMissing):
            FinnhubTradeStream(
                symbols=("BAC",),
                api_key="",
            )

    def test_trade_creates_clearly_labeled_synthetic_band(self) -> None:
        stream = FinnhubTradeStream(
            symbols=("BAC",),
            api_key="key",
            stale_after_seconds=60,
        )
        stream.reducer.start_generation(1, 1)
        stream.reducer.handshake(1)
        request_id = stream._request_ids["BAC"]
        stream.reducer.register_quote(
            generation=1,
            request_id=request_id,
            symbol="BAC",
            requested_market_data_type=1,
        )
        stream.reducer.market_data_type(1, request_id, 1)
        stream.process_message(
            json.dumps(
                {
                    "type": "trade",
                    "data": [
                        {
                            "s": "BAC",
                            "p": 50.0,
                            "t": 1784905200000,
                            "v": 100,
                        }
                    ],
                }
            ),
            generation=1,
        )
        snapshot = stream.snapshot()
        row = snapshot.quotes[0]
        self.assertEqual(snapshot.provider, "Finnhub")
        self.assertIn("非市场盘口", row.coverage)
        self.assertEqual(str(row.last), "50.0")
        self.assertLess(row.bid, row.last)
        self.assertGreater(row.ask, row.last)
        self.assertTrue(row.realtime_ready)


if __name__ == "__main__":
    unittest.main()
