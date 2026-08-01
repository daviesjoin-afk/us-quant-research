from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.ibkr_stream import StreamQuote, StreamSnapshot
from us_quant.minute_data import MinuteQuoteRecord, MinuteQuoteStore
from us_quant.targeted_replay import (
    load_targeted_replays,
    run_targeted_replay,
    save_targeted_replay,
)
from us_quant.targeted_robustness import (
    group_regular_sessions,
    load_targeted_robustness,
    run_targeted_robustness,
    save_targeted_robustness,
)


PARAMETERS = {
    "momentum_lookback_minutes": 5,
    "warmup_minutes": 10,
    "maximum_hold_minutes": 45,
    "maximum_trades_per_day": 4,
    "max_position_fraction": "0.10",
    "min_order_notional": "50",
    "commission_per_order": "0.35",
    "slippage_bps": "2",
    "maximum_spread_fraction": "0.002",
    "minimum_momentum": "0.0035",
    "maximum_momentum": "0.025",
    "profit_target": "0.012",
    "stop_loss": "0.007",
    "trailing_stop": "0.006",
    "whole_shares": True,
}


def snapshot(
    minute: datetime,
    *,
    bid: str,
    ask: str,
    stale: bool = False,
) -> StreamSnapshot:
    quote = StreamQuote(
        symbol="AAPL",
        request_id=1,
        generation=1,
        requested_market_data_type=1,
        effective_market_data_type=1,
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=Decimal(bid),
        close=None,
        updated_at=minute.isoformat(),
        age_seconds=0,
        bid_size=Decimal("400"),
        ask_size=Decimal("600"),
        stale=stale,
        stale_reason="test stale" if stale else None,
        provider="TestFeed",
        coverage="unit-test Level-I",
    )
    return StreamSnapshot(
        generation=1,
        socket_connected=True,
        handshake_complete=True,
        reconnect_attempt=0,
        quotes=(quote,),
        last_error_code=None,
        last_message="test",
        observed_at=minute.isoformat(),
        provider="TestFeed",
        coverage="unit-test Level-I",
    )


class MinuteReplayTests(unittest.TestCase):
    def test_store_upserts_minutes_and_preserves_quality(self) -> None:
        with TemporaryDirectory() as directory:
            store = MinuteQuoteStore(Path(directory) / "minute.sqlite3")
            minute = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
            store.record_snapshot(
                snapshot(minute, bid="50.00", ask="50.02")
            )
            store.record_snapshot(
                snapshot(minute, bid="50.10", ask="50.12")
            )
            store.record_snapshot(
                snapshot(
                    minute + timedelta(minutes=1),
                    bid="50.10",
                    ask="50.12",
                    stale=True,
                )
            )
            all_rows = store.load("AAPL", usable_only=False)
            usable = store.load("AAPL")
            summary = store.summary("AAPL")
        self.assertEqual(len(all_rows), 2)
        self.assertEqual(len(usable), 1)
        self.assertEqual(usable[0].bid, Decimal("50.10"))
        self.assertEqual(usable[0].source_age_seconds, 0)
        self.assertEqual(usable[0].bid_size, Decimal("400"))
        self.assertEqual(usable[0].ask_size, Decimal("600"))
        self.assertEqual(summary.total_rows, 2)
        self.assertEqual(summary.usable_rows, 1)

    def test_replay_is_versioned_and_saves_no_order_claim(self) -> None:
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        prices = [
            Decimal("50") + Decimal(index) * Decimal("0.08")
            for index in range(13)
        ] + [Decimal("50.20"), Decimal("50.10")]
        records = tuple(
            MinuteQuoteRecord(
                symbol="AAPL",
                minute=(start + timedelta(minutes=index)).isoformat(),
                provider="TestFeed",
                coverage="unit-test Level-I",
                bid=price,
                ask=price + Decimal("0.02"),
                last=price,
                market_data_type=1,
                realtime_ready=True,
                stale=False,
                stale_reason=None,
                generation=1,
            )
            for index, price in enumerate(prices)
        )
        result = run_targeted_replay(
            records,
            strategy_version_id="version-1",
            strategy_semver="1.1.0-research",
            parameter_hash="parameter-1",
            parameters=PARAMETERS,
            initial_equity=Decimal("1500"),
        )
        self.assertEqual(result.strategy_version_id, "version-1")
        self.assertEqual(result.symbol, "AAPL")
        self.assertEqual(result.row_count, len(records))
        self.assertGreaterEqual(len(result.fills), 2)
        with TemporaryDirectory() as directory:
            path = save_targeted_replay(result, directory)
            payload = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_targeted_replays(directory)
        self.assertFalse(payload["orders_submitted"])
        self.assertEqual(payload["data_hash"], result.data_hash)
        self.assertEqual(payload["parameter_hash"], "parameter-1")
        self.assertEqual(loaded[0].strategy_semver, "1.1.0-research")
        self.assertEqual(loaded[0].parameters, PARAMETERS)

    def test_replay_rejects_cross_provider_stitching(self) -> None:
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        records = tuple(
            MinuteQuoteRecord(
                symbol="AAPL",
                minute=(start + timedelta(minutes=index)).isoformat(),
                provider="FeedA" if index < 5 else "FeedB",
                coverage="test",
                bid=Decimal("50"),
                ask=Decimal("50.02"),
                last=Decimal("50"),
                market_data_type=1,
                realtime_ready=True,
                stale=False,
                stale_reason=None,
                generation=1,
            )
            for index in range(10)
        )
        with self.assertRaisesRegex(ValueError, "跨行情源"):
            run_targeted_replay(
                records,
                strategy_version_id="version-1",
                strategy_semver="1.1.0-research",
                parameter_hash="parameter-1",
                parameters=PARAMETERS,
                initial_equity=Decimal("1500"),
            )

    def test_single_replay_rejects_cross_session_stitching(self) -> None:
        records = _multi_session_records(2)
        with self.assertRaisesRegex(ValueError, "跨交易日"):
            run_targeted_replay(
                records,
                strategy_version_id="version-1",
                strategy_semver="1.2.0-research",
                parameter_hash="parameter-1",
                parameters=PARAMETERS,
                initial_equity=Decimal("1500"),
            )

    def test_regular_sessions_exclude_extended_hours(self) -> None:
        start = datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc)
        records = tuple(
            MinuteQuoteRecord(
                symbol="AAPL",
                minute=(start + timedelta(minutes=index * 30)).isoformat(),
                provider="TestFeed",
                coverage="unit-test Level-I",
                bid=Decimal("50"),
                ask=Decimal("50.02"),
                last=Decimal("50"),
                market_data_type=1,
                realtime_ready=True,
                stale=False,
                stale_reason=None,
                generation=1,
            )
            for index in range(9)
        )
        sessions = group_regular_sessions(records)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0][0], "2026-07-20")
        self.assertEqual(len(sessions[0][1]), 8)

    def test_multi_session_robustness_is_versioned_and_persisted(
        self,
    ) -> None:
        records = _multi_session_records(5)
        result = run_targeted_robustness(
            records,
            strategy_version_id="version-robust",
            strategy_semver="1.2.0-research",
            parameter_hash="baseline-hash",
            parameters=PARAMETERS,
            initial_equity=Decimal("1500"),
        )
        self.assertEqual(result.usable_sessions, 5)
        self.assertEqual(len(result.scenario_summaries), 5)
        self.assertEqual(len(result.session_outcomes), 25)
        self.assertEqual(result.evidence_grade, "初步多会话")
        self.assertFalse(result.review_ready)
        self.assertEqual(
            result.scenario_summaries[0].parameter_hash,
            "baseline-hash",
        )
        with TemporaryDirectory() as directory:
            path = save_targeted_robustness(result, directory)
            payload = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_targeted_robustness(directory)
        self.assertFalse(payload["orders_submitted"])
        self.assertFalse(payload["automatic_strategy_promotion"])
        self.assertEqual(loaded[0], result)

    def test_robustness_skips_non_contiguous_short_sessions(self) -> None:
        complete = _multi_session_records(1)
        broken_start = datetime(
            2026, 7, 21, 14, 0, tzinfo=timezone.utc
        )
        broken = tuple(
            MinuteQuoteRecord(
                symbol="AAPL",
                minute=(
                    broken_start + timedelta(minutes=index * 2)
                ).isoformat(),
                provider="TestFeed",
                coverage="unit-test Level-I",
                bid=Decimal("50"),
                ask=Decimal("50.02"),
                last=Decimal("50"),
                market_data_type=1,
                realtime_ready=True,
                stale=False,
                stale_reason=None,
                generation=1,
            )
            for index in range(12)
        )
        result = run_targeted_robustness(
            complete + broken,
            strategy_version_id="version-robust",
            strategy_semver="1.2.0-research",
            parameter_hash="baseline-hash",
            parameters=PARAMETERS,
            initial_equity=Decimal("1500"),
        )
        self.assertEqual(result.total_sessions, 2)
        self.assertEqual(result.usable_sessions, 1)
        self.assertEqual(result.skipped_sessions, ("2026-07-21",))


def _multi_session_records(days: int) -> tuple[MinuteQuoteRecord, ...]:
    records = []
    base = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
    prices = [
        Decimal("50")
        + Decimal(min(index, 12)) * Decimal("0.08")
        for index in range(346)
    ]
    for day in range(days):
        start = base + timedelta(days=day)
        for index, price in enumerate(prices):
            records.append(
                MinuteQuoteRecord(
                    symbol="AAPL",
                    minute=(
                        start + timedelta(minutes=index)
                    ).isoformat(),
                    provider="TestFeed",
                    coverage="unit-test Level-I",
                    bid=price,
                    ask=price + Decimal("0.02"),
                    last=price,
                    market_data_type=1,
                    realtime_ready=True,
                    stale=False,
                    stale_reason=None,
                    generation=1,
                )
            )
    return tuple(records)


if __name__ == "__main__":
    unittest.main()
