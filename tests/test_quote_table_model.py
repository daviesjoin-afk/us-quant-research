from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from time import perf_counter
import unittest

from us_quant.desktop import QuoteTableModel
from us_quant.ibkr_stream import StreamQuote, StreamSnapshot


def _snapshot(age: float = 0.1) -> StreamSnapshot:
    quotes = tuple(
        StreamQuote(
            symbol=f"T{index:02d}",
            request_id=index,
            generation=1,
            requested_market_data_type=1,
            effective_market_data_type=1,
            bid=Decimal("99.95") + index,
            ask=Decimal("100.05") + index,
            last=Decimal("100") + index,
            close=Decimal("99") + index,
            updated_at="2026-07-25T18:00:00+00:00",
            age_seconds=age,
            stale=False,
            stale_reason=None,
            provider="Finnhub",
            coverage="实时成交；影子执行带，非 NBBO",
        )
        for index in range(30)
    )
    return StreamSnapshot(
        generation=1,
        socket_connected=True,
        handshake_complete=True,
        reconnect_attempt=0,
        quotes=quotes,
        last_error_code=None,
        last_message="ready",
        observed_at="2026-07-25T18:00:00+00:00",
        provider="Finnhub",
        coverage="实时成交",
    )


class QuoteTableModelTests(unittest.TestCase):
    def test_repeated_updates_do_not_reset_or_rebuild_rows(self) -> None:
        model = QuoteTableModel()
        first = _snapshot()
        model.update_snapshot(first)
        self.assertEqual(model.reset_count, 1)
        model.update_snapshot(first)
        self.assertEqual(model.reset_count, 1)
        self.assertEqual(model.changed_row_count, 0)

        model.update_snapshot(_snapshot(age=0.6))
        self.assertEqual(model.reset_count, 1)
        self.assertEqual(model.changed_row_count, 30)
        self.assertEqual(model.rowCount(), 30)
        self.assertEqual(model.columnCount(), 14)

    def test_thousand_incremental_snapshots_stay_bounded(self) -> None:
        model = QuoteTableModel()
        model.update_snapshot(_snapshot())
        started = perf_counter()
        for index in range(1000):
            model.update_snapshot(_snapshot(age=index / 10))
        elapsed = perf_counter() - started
        self.assertEqual(model.reset_count, 1)
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
