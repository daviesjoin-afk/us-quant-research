from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from time import perf_counter
import unittest

from us_quant.desktop_v2.pages.market.rows import quote_rows
from us_quant.desktop_v2.pages.market.tables import QuoteTableModel
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)


def _snapshot(age: float = 0.1) -> MarketSnapshot:
    quotes = tuple(
        MarketQuote(
            symbol=f"T{index:02d}",
            bid=Decimal("99.95") + index,
            ask=Decimal("100.05") + index,
            last=Decimal("100") + index,
            close=Decimal("99") + index,
            bid_size=None,
            ask_size=None,
            mode=MarketDataMode.REALTIME,
            updated_at=datetime(2026, 7, 25, 18, 0, tzinfo=timezone.utc),
            age_seconds=age,
            stale=False,
            stale_reason=None,
            generation=1,
            source_id="finnhub_trades",
            source_label="Finnhub",
            coverage="实时成交；影子执行带，非 NBBO",
        )
        for index in range(30)
    )
    return MarketSnapshot(
        generation=1,
        connected=True,
        ready=True,
        reconnect_attempt=0,
        quotes=quotes,
        error_code=None,
        message="ready",
        observed_at=datetime(2026, 7, 25, 18, 0, tzinfo=timezone.utc),
        source_id="finnhub_trades",
        source_label="Finnhub",
        coverage="实时成交",
    )


class QuoteTableModelTests(unittest.TestCase):
    def test_repeated_updates_do_not_reset_or_rebuild_rows(self) -> None:
        model = QuoteTableModel()
        first = _snapshot()
        model.update_rows(quote_rows(first))
        self.assertEqual(model.reset_count, 1)
        model.update_rows(quote_rows(first))
        self.assertEqual(model.reset_count, 1)
        self.assertEqual(model.changed_row_count, 0)

        model.update_rows(quote_rows(_snapshot(age=0.6)))
        self.assertEqual(model.reset_count, 1)
        self.assertEqual(model.changed_row_count, 30)
        self.assertEqual(model.rowCount(), 30)
        self.assertEqual(model.columnCount(), 14)

    def test_thousand_incremental_snapshots_stay_bounded(self) -> None:
        model = QuoteTableModel()
        model.update_rows(quote_rows(_snapshot()))
        started = perf_counter()
        for index in range(1000):
            model.update_rows(quote_rows(_snapshot(age=index / 10)))
        elapsed = perf_counter() - started
        self.assertEqual(model.reset_count, 1)
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
