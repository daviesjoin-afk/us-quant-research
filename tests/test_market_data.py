from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
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
    ResearchDataEligibilityError,
    ResearchDataMode,
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

    def test_legacy_artifact_remains_available_for_exploratory_research(self) -> None:
        with TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "normalized"
                / "ibkr"
                / "daily"
                / "ABC"
                / "legacy.json"
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"source_sha256":"legacy","symbol":"ABC","bars":[]}',
                encoding="utf-8",
            )

            loaded = load_latest_normalized_series(
                "ABC", data_root=Path(directory)
            )

            self.assertEqual(loaded.price_basis, "unspecified_legacy")
            self.assertFalse(loaded.point_in_time_membership)

    def test_execution_candidate_rejects_legacy_adjusted_and_current_only_data(self) -> None:
        base = {
            "source_sha256": "digest",
            "symbol": "ABC",
            "bars": [],
        }
        cases = (
            ("legacy", {}),
            ("adjusted", {"price_basis": "adjusted_research_proxy", "point_in_time_membership": True}),
            ("current_only", {"price_basis": "raw_trade_ohlc", "point_in_time_membership": False}),
        )
        for case, metadata in cases:
            with TemporaryDirectory() as directory:
                path = (
                    Path(directory)
                    / "normalized"
                    / "ibkr"
                    / "daily"
                    / "ABC"
                    / f"{case}.json"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(base | metadata),
                    encoding="utf-8",
                )
                with self.assertRaises(ResearchDataEligibilityError):
                    load_latest_normalized_series(
                        "ABC",
                        data_root=Path(directory),
                        research_mode=(
                            ResearchDataMode.EXECUTION_CANDIDATE_PIT_RAW
                        ),
                    )

    def test_normalized_tampering_fails_integrity_check(self) -> None:
        """CR-5: editing a normalized file in place must fail to load."""
        start = date(2026, 1, 1)
        bars = tuple(
            _bar("MUU", start + timedelta(days=index))
            for index in range(20)
        )
        series = _series(bars)
        with TemporaryDirectory() as directory:
            saved = save_historical_series(
                series, data_root=Path(directory)
            )
            normalized_path = saved.normalized_path
            self.assertIsNotNone(normalized_path)
            payload = json.loads(
                normalized_path.read_text(encoding="utf-8")
            )
            self.assertIn("normalized_sha256", payload)
            payload["bars"][0]["close"] = "999"
            normalized_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "integrity"):
                load_latest_normalized_series(
                    "MUU", data_root=Path(directory)
                )

    def test_normalized_without_self_hash_loads_as_legacy(self) -> None:
        """CR-5: files written before the self-hash field remain readable."""
        with TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "normalized"
                / "ibkr"
                / "daily"
                / "ABC"
                / "prehash.json"
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"source_sha256":"digest","symbol":"ABC","price_basis":"raw_trade_ohlc","bars":[]}',
                encoding="utf-8",
            )
            loaded = load_latest_normalized_series(
                "ABC", data_root=Path(directory)
            )
            self.assertEqual(loaded.symbol, "ABC")
            self.assertEqual(loaded.bars, ())

    def test_execution_candidate_accepts_raw_data_with_pit_membership(self) -> None:
        with TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "normalized"
                / "ibkr"
                / "daily"
                / "ABC"
                / "eligible.json"
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"source_sha256":"digest","symbol":"ABC","price_basis":"raw_trade_ohlc","point_in_time_membership":true,"bars":[]}',
                encoding="utf-8",
            )

            loaded = load_latest_normalized_series(
                "ABC",
                data_root=Path(directory),
                research_mode=ResearchDataMode.EXECUTION_CANDIDATE_PIT_RAW,
            )

            self.assertTrue(loaded.point_in_time_membership)


if __name__ == "__main__":
    unittest.main()
