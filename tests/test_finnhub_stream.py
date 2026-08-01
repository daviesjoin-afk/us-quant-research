from __future__ import annotations

import json
import os
import unittest

from us_quant.finnhub_stream import (
    FinnhubCredentialsMissing,
    FinnhubRejectedError,
    FinnhubTradeStream,
    classify_connect_error,
    proxy_from_environment,
)
from us_quant.extended_hours import USEquitySession


class _RejectedResponse:
    status_code = 401


class _InvalidStatusError(Exception):
    response = _RejectedResponse()


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

    def test_extended_hours_use_a_wider_but_bounded_fresh_window(self) -> None:
        session = [USEquitySession.PREMARKET]
        stream = FinnhubTradeStream(
            symbols=("BAC",),
            api_key="key",
            stale_after_seconds=20,
            extended_stale_after_seconds=120,
            session_provider=lambda: session[0],
        )
        stream.reducer.start_generation(1, 1)
        stream.reducer.handshake(1)

        extended = stream.snapshot()

        self.assertEqual(stream.reducer.stale_after_seconds, 120)
        self.assertIn("120", extended.coverage)
        self.assertIn("等待新的成交", extended.last_message)
        session[0] = USEquitySession.REGULAR
        regular = stream.snapshot()
        self.assertEqual(stream.reducer.stale_after_seconds, 20)
        self.assertIn("20", regular.coverage)

    def test_http_401_is_fatal_with_diagnosable_message(self) -> None:
        fatal, message = classify_connect_error(_InvalidStatusError())
        self.assertTrue(fatal)
        self.assertIn("HTTP 401", message)
        self.assertIn("API Key", message)

    def test_connect_timeout_is_not_fatal_and_mentions_network(self) -> None:
        fatal, message = classify_connect_error(TimeoutError("timed out"))
        self.assertFalse(fatal)
        self.assertIn("超时", message)
        self.assertIn("ws.finnhub.io", message)

    def test_generic_connect_error_keeps_detail(self) -> None:
        fatal, message = classify_connect_error(
            RuntimeError("unexpected protocol state")
        )
        self.assertFalse(fatal)
        self.assertIn("unexpected protocol state", message)

    def test_tls_reset_is_not_fatal_and_mentions_proxy(self) -> None:
        fatal, message = classify_connect_error(
            ConnectionError("[SSL: UNEXPECTED_EOF_WHILE_READING]")
        )
        self.assertFalse(fatal)
        self.assertIn("HTTPS_PROXY", message)

    def test_eof_reset_is_classified_as_transport_interruption(self) -> None:
        fatal, message = classify_connect_error(EOFError())
        self.assertFalse(fatal)
        self.assertIn("网络连接被中断", message)

    def test_proxy_resolution_falls_back_to_builtin_clash(self) -> None:
        old_upper = os.environ.pop("HTTPS_PROXY", None)
        old_lower = os.environ.pop("https_proxy", None)
        try:
            # No environment proxy -> the built-in Clash default is used.
            self.assertEqual(
                proxy_from_environment(), "http://127.0.0.1:7897"
            )
            os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
            self.assertEqual(
                proxy_from_environment(), "http://127.0.0.1:7890"
            )
        finally:
            if old_upper is not None:
                os.environ["HTTPS_PROXY"] = old_upper
            if old_lower is not None:
                os.environ["https_proxy"] = old_lower

    def test_rejected_error_short_circuits_to_fatal(self) -> None:
        fatal, message = classify_connect_error(
            FinnhubRejectedError("Finnhub 拒绝连接：Invalid token")
        )
        self.assertTrue(fatal)
        self.assertIn("Invalid token", message)

    def test_auth_error_message_raises_rejected(self) -> None:
        stream = FinnhubTradeStream(
            symbols=("BAC",),
            api_key="key",
        )
        stream.reducer.start_generation(1, 1)
        with self.assertRaisesRegex(FinnhubRejectedError, "Invalid token"):
            stream.process_message(
                json.dumps({"type": "error", "msg": "Invalid token"}),
                generation=1,
            )

    def test_other_error_message_records_9102_without_raising(self) -> None:
        stream = FinnhubTradeStream(
            symbols=("BAC",),
            api_key="key",
        )
        stream.reducer.start_generation(1, 1)
        stream.process_message(
            json.dumps({"type": "error", "msg": "rate limit"}),
            generation=1,
        )
        self.assertEqual(stream.reducer.snapshot().last_error_code, 9102)
        self.assertIn("rate limit", stream.reducer.snapshot().last_message)


if __name__ == "__main__":
    unittest.main()
