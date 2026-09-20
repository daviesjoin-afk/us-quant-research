"""Preflight and quote readiness: the pure gate the window asks before arming.

Both live in ``trading/runtime/preflight.py`` and neither touches a session, a
risk verdict, a broker or a widget -- they answer "may this start?" and "how
fresh is the feed, split by role?" from facts the window already holds.  They
are tested away from the runtimes on purpose: a preflight that needed a session
to exist could not gate creating one.
"""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)
from us_quant.trading.runtime.preflight import (
    calculate_quote_readiness_breakdown,
    evaluate_auto_quant_preflight,
)


def _snapshot(
    observed: datetime,
    prices: dict[str, Decimal],
    *,
    ready: bool = True,
) -> MarketSnapshot:
    quotes = tuple(
        MarketQuote(
            symbol=symbol,
            bid=price,
            ask=price + Decimal("0.02"),
            last=price,
            close=None,
            bid_size=None,
            ask_size=None,
            mode=(
                MarketDataMode.REALTIME
                if ready
                else MarketDataMode.DELAYED
            ),
            updated_at=observed,
            age_seconds=0,
            stale=not ready,
            stale_reason=None if ready else "delayed",
            generation=1,
            source_id="test_feed",
            source_label="TestFeed",
            coverage="unit test",
        )
        for index, (symbol, price) in enumerate(prices.items(), 1)
    )
    return MarketSnapshot(
        generation=1,
        connected=True,
        ready=True,
        reconnect_attempt=0,
        quotes=quotes,
        error_code=None,
        message="test",
        observed_at=observed,
        source_id="test_feed",
        source_label="TestFeed",
        coverage="unit test",
    )


class PreflightTests(unittest.TestCase):
    def test_quote_readiness_separates_candidates_references_and_feed(
        self,
    ) -> None:
        candidates = tuple(f"C{index}" for index in range(20))
        observed = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        snapshot = _snapshot(
            observed,
            {symbol: Decimal("10") for symbol in (*candidates, "SPY", "QQQ")},
            ready=False,
        )
        quotes = tuple(
            replace(
                quote,
                mode=MarketDataMode.REALTIME,
                stale=False,
                stale_reason=None,
            )
            if quote.symbol in {"SPY", "QQQ"}
            else quote
            for quote in snapshot.quotes
        )
        snapshot = replace(snapshot, quotes=quotes)

        references_only = calculate_quote_readiness_breakdown(
            snapshot,
            candidate_symbols=candidates,
            reference_symbols=("SPY", "QQQ"),
            recently_ready_symbols={"SPY", "QQQ"},
        )
        self.assertEqual(references_only.candidate_count, 20)
        self.assertEqual(references_only.candidate_current_count, 0)
        self.assertEqual(references_only.candidate_recent_count, 0)
        self.assertEqual(references_only.reference_current_count, 2)
        self.assertEqual(references_only.reference_recent_count, 2)
        self.assertEqual(references_only.subscription_current_count, 2)
        self.assertEqual(references_only.subscription_count, 22)

        one_candidate_snapshot = replace(
            snapshot,
            quotes=tuple(
                replace(
                    quote,
                    mode=MarketDataMode.REALTIME,
                    stale=False,
                    stale_reason=None,
                )
                if quote.symbol == "C0"
                else quote
                for quote in snapshot.quotes
            ),
        )
        one_candidate = calculate_quote_readiness_breakdown(
            one_candidate_snapshot,
            candidate_symbols=candidates,
            reference_symbols=("SPY", "QQQ"),
            recently_ready_symbols=(),
        )
        self.assertEqual(one_candidate.candidate_current_count, 1)
        self.assertEqual(one_candidate.reference_current_count, 2)
        self.assertEqual(one_candidate.subscription_count, 22)

        no_overlap = calculate_quote_readiness_breakdown(
            snapshot,
            candidate_symbols=("SPY",),
            reference_symbols=("SPY", "QQQ"),
            recently_ready_symbols=(),
        )
        self.assertEqual(no_overlap.candidate_count, 0)
        self.assertEqual(no_overlap.reference_count, 2)

    def test_preflight_reports_every_missing_gate(self) -> None:
        result = evaluate_auto_quant_preflight(
            capability_enabled=False,
            paper_confirmed=False,
            strategy_eligible=True,
            strategy_detail="1.1.0-research",
            candidate_count=2,
            realtime_ready_count=1,
            paper_capital=None,
        )
        self.assertFalse(result.ready)
        self.assertEqual(result.passed_count, 1)
        self.assertEqual(len(result.checks), 6)

    def test_extended_session_can_start_with_one_fresh_candidate(self) -> None:
        result = evaluate_auto_quant_preflight(
            capability_enabled=True,
            paper_confirmed=True,
            strategy_eligible=True,
            strategy_detail="1.2.0-research",
            candidate_count=20,
            realtime_ready_count=1,
            recent_ready_count=1,
            minimum_realtime_quotes=1,
            paper_capital=Decimal("1000000"),
        )

        self.assertTrue(result.ready)


if __name__ == "__main__":
    unittest.main()