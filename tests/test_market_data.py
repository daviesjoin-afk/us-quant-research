from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.ibkr_history import default_completed_session_end
from us_quant.market_data import (
    build_aligned_market_slices,
    DailyBar,
    HistoricalRequest,
    HistoricalSeries,
    load_latest_normalized_series,
    save_historical_series,
    validate_daily_series,
    _current_us_trading_date,
)


def _bar(symbol: str, day: date, close: str = "100") -> DailyBar:
    close_value = Decimal(close)
    return DailyBar(
        symbol=symbol,
        trading_date=day,
        open=close_value,
        high=close_value + Decimal("1"),
        low=close_value - Decimal("1"),
        close=close_value,
        volume=Decimal("100000"),
        average=close_value,
        bar_count=1000,
    )


def _series(bars: tuple[DailyBar, ...]) -> HistoricalSeries:
    return HistoricalSeries(
        source="ibkr_tws_api",
        server_version=223,
        fetched_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        request=HistoricalRequest(
            symbol="MUU",
            end_datetime="20260723 23:59:59 US/Eastern",
            duration="1 Y",
        ),
        returned_start="20250724",
        returned_end="20260723",
        bars=bars,
    )


class MarketDataTests(unittest.TestCase):
    def test_current_us_session_is_not_a_completed_bar(self) -> None:
        now = datetime(2026, 7, 25, 1, 0, tzinfo=timezone.utc)
        self.assertEqual(
            _current_us_trading_date(now),
            date(2026, 7, 24),
        )

    def test_uses_previous_new_york_date_as_completed_end(self) -> None:
        now = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(
            default_completed_session_end(now),
            "20260723 23:59:59 US/Eastern",
        )

    def test_valid_series_passes_quality_gate(self) -> None:
        start = date(2026, 1, 1)
        bars = tuple(
            _bar("MUU", start + timedelta(days=index))
            for index in range(20)
        )
        report = validate_daily_series(_series(bars))
        self.assertTrue(report.passed, report.issues)

    def test_duplicate_date_fails_quality_gate(self) -> None:
        start = date(2026, 1, 1)
        bars = tuple(
            _bar("MUU", start + timedelta(days=index))
            for index in range(19)
        ) + (_bar("MUU", start + timedelta(days=18)),)
        report = validate_daily_series(_series(bars))
        self.assertFalse(report.passed)
        self.assertIn(
            "duplicate_date",
            {issue.code for issue in report.issues},
        )

    def test_saved_artifact_has_stable_hash_and_is_immutable(self) -> None:
        start = date(2026, 1, 1)
        bars = tuple(
            _bar("MUU", start + timedelta(days=index))
            for index in range(20)
        )
        series = _series(bars)
        with TemporaryDirectory() as directory:
            first = save_historical_series(
                series, data_root=Path(directory)
            )
            second = save_historical_series(
                series, data_root=Path(directory)
            )
            self.assertEqual(first.content_sha256, second.content_sha256)
            self.assertTrue(first.raw_path.exists())
            self.assertIsNotNone(first.normalized_path)
            self.assertTrue(first.normalized_path.exists())

            loaded = load_latest_normalized_series(
                "MUU", data_root=Path(directory)
            )
            slices = build_aligned_market_slices((loaded,))
            self.assertEqual(len(slices), 20)
            self.assertEqual(
                slices[0].bars["MUU"].close,
                Decimal("100"),
            )
            self.assertEqual(loaded.source, "ibkr_tws_api")
            self.assertEqual(
                loaded.price_basis, "raw_trade_ohlc"
            )


if __name__ == "__main__":
    unittest.main()
