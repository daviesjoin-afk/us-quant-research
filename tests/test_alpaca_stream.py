import json
from decimal import Decimal
import unittest

from us_quant.alpaca_stream import (
    AlpacaCredentialsMissing,
    AlpacaIEXStream,
)


class AlpacaStreamTests(unittest.TestCase):
    def test_credentials_are_required(self) -> None:
        with self.assertRaises(AlpacaCredentialsMissing):
            AlpacaIEXStream(
                symbols=("AAPL",),
                api_key="",
                api_secret="",
            )

    def test_quote_and_trade_messages_build_realtime_iex_quote(
        self,
    ) -> None:
        stream = AlpacaIEXStream(
            symbols=("AAPL",),
            api_key="key",
            api_secret="secret",
            stale_after_seconds=60,
        )
        stream.reducer.start_generation(1, 1)
        stream.reducer.handshake(1)
        request_id = stream._request_ids["AAPL"]
        stream.reducer.register_quote(
            generation=1,
            request_id=request_id,
            symbol="AAPL",
            requested_market_data_type=1,
        )
        stream.reducer.market_data_type(1, request_id, 1)
        stream.process_message(
            json.dumps(
                [
                    {
                        "T": "q",
                        "S": "AAPL",
                        "bp": 200.1,
                        "ap": 200.2,
                        "bs": 400,
                        "as": 600,
                        "t": "2026-07-24T15:00:00Z",
                    },
                    {
                        "T": "t",
                        "S": "AAPL",
                        "p": 200.15,
                        "t": "2026-07-24T15:00:00Z",
                    },
                ]
            ),
            generation=1,
        )
        snapshot = stream.snapshot()
        quote = snapshot.quotes[0]
        self.assertEqual(snapshot.provider, "Alpaca")
        self.assertIn("非全市场", snapshot.coverage)
        self.assertEqual(str(quote.bid), "200.1")
        self.assertEqual(str(quote.ask), "200.2")
        self.assertEqual(str(quote.last), "200.15")
        self.assertEqual(quote.bid_size, Decimal("400"))
        self.assertEqual(quote.ask_size, Decimal("600"))
        self.assertTrue(quote.realtime_ready)
