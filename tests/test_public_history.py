from datetime import datetime, timezone
import unittest

from us_quant.public_history import parse_yahoo_chart


class PublicHistoryTests(unittest.TestCase):
    def test_adjusts_ohlc_using_adjusted_close_factor(self) -> None:
        payload = {
            "chart": {
                "error": None,
                "result": [
                    {
                        "timestamp": [1_700_000_000],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [100.0],
                                    "high": [110.0],
                                    "low": [90.0],
                                    "close": [100.0],
                                    "volume": [1000],
                                }
                            ],
                            "adjclose": [{"adjclose": [50.0]}],
                        },
                    }
                ],
            }
        }
        series = parse_yahoo_chart(
            payload,
            symbol="TEST",
            fetched_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
            years=5,
        )
        bar = series.bars[0]
        self.assertEqual(str(bar.open), "50.00")
        self.assertEqual(str(bar.high), "55.00")
        self.assertEqual(str(bar.low), "45.00")
        self.assertEqual(str(bar.close), "50.00")


if __name__ == "__main__":
    unittest.main()
