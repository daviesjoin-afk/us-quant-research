from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)
from us_quant.shadow.config import ShadowSimulationConfig
from us_quant.shadow.engine import ShadowPaperEngine
from us_quant.shadow.models import ShadowFill
from us_quant.shadow.store import ShadowPaperStore


def quote(
    symbol: str,
    *,
    bid: str,
    ask: str,
    observed_at: datetime,
    ready: bool = True,
) -> MarketQuote:
    return MarketQuote(
        symbol=symbol,
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=(Decimal(bid) + Decimal(ask)) / Decimal("2"),
        close=None,
        bid_size=None,
        ask_size=None,
        mode=MarketDataMode.REALTIME if ready else MarketDataMode.DELAYED,
        updated_at=observed_at,
        age_seconds=0,
        stale=not ready,
        stale_reason=None if ready else "delayed",
        generation=1,
        source_id="alpaca_iex",
        source_label="Alpaca",
        coverage="IEX single exchange; not SIP/NBBO",
    )


def stream(
    row: MarketQuote, observed_at: datetime
) -> MarketSnapshot:
    return MarketSnapshot(
        generation=1,
        connected=True,
        ready=True,
        reconnect_attempt=1,
        quotes=(row,),
        error_code=None,
        message="authenticated",
        observed_at=observed_at,
        source_id="alpaca_iex",
        source_label="Alpaca",
        coverage="IEX single exchange; not SIP/NBBO",
    )


class ShadowPaperTests(unittest.TestCase):
    def test_minute_gap_resets_momentum_warmup(self) -> None:
        with TemporaryDirectory() as directory:
            engine = ShadowPaperEngine(
                store=ShadowPaperStore(
                    Path(directory) / "shadow.sqlite3"
                ),
                allowed_symbols=("BAC",),
                config=ShadowSimulationConfig(
                    initial_cash=Decimal("1500"),
                    capital_source="unit_test",
                    warmup_minutes=2,
                    momentum_lookback_minutes=1,
                    minimum_momentum=Decimal("0.001"),
                    maximum_momentum=Decimal("0.10"),
                ),
            )
            engine.start()
            start = datetime(
                2026, 7, 24, 15, 0, tzinfo=timezone.utc
            )
            for at, bid, ask in (
                (start, "50", "50.02"),
                (start + timedelta(minutes=10), "51", "51.02"),
            ):
                engine.on_stream(
                    stream(
                        quote(
                            "BAC",
                            bid=bid,
                            ask=ask,
                            observed_at=at,
                        ),
                        at,
                    ),
                    observed_at=at,
                )
            self.assertEqual(engine.snapshot().positions, ())
            self.assertIn("1/2", engine.snapshot().status)

    def test_orphan_fill_is_rejected_by_foreign_key(self) -> None:
        with TemporaryDirectory() as directory:
            store = ShadowPaperStore(
                Path(directory) / "shadow.sqlite3"
            )
            orphan = ShadowFill(
                session_id="missing",
                occurred_at=datetime.now(timezone.utc).isoformat(),
                symbol="BAC",
                side="BUY",
                quantity=1,
                price=Decimal("50"),
                commission=Decimal("0.35"),
                reason="test",
                provider="test",
                coverage="test",
                realized_pnl=None,
            )
            import sqlite3

            with self.assertRaises(sqlite3.IntegrityError):
                store.add_fill(orphan)

    def test_delayed_quote_never_enters(self) -> None:
        """A delayed quote must not even reach the momentum history.

        The prices below rise hard enough to clear every entry gate, so the only
        reason there is no fill is the freshness requirement itself.  Asserting
        the warmup history stays empty is what makes this test about the gate
        rather than about momentum.
        """

        with TemporaryDirectory() as directory:
            engine = ShadowPaperEngine(
                store=ShadowPaperStore(Path(directory) / "shadow.sqlite3"),
                allowed_symbols=("BAC",),
                config=ShadowSimulationConfig(
                    initial_cash=Decimal("1500"),
                    capital_source="unit_test",
                    warmup_minutes=2,
                    momentum_lookback_minutes=1,
                    minimum_momentum=Decimal("0.0001"),
                ),
            )
            engine.start()
            now = datetime(2026, 7, 24, 15, 0, tzinfo=timezone.utc)
            for minute, price in enumerate(("50.00", "50.60", "51.20", "51.80")):
                at = now + timedelta(minutes=minute)
                engine.on_stream(
                    stream(
                        quote(
                            "BAC",
                            bid=price,
                            ask=str(Decimal(price) + Decimal("0.02")),
                            observed_at=at,
                            ready=False,
                        ),
                        at,
                    ),
                    observed_at=at,
                )
            # A rising delayed quote would enter if freshness were ignored, so
            # the absence of a fill is the gate, not a coincidence of prices.
            self.assertEqual(
                len(engine._minute_prices["BAC"]), 0, "delayed quote warmed up"
            )
            self.assertEqual(engine.snapshot().positions, ())
            self.assertEqual(engine.snapshot().fills, ())

    def test_whole_share_entry_and_costed_exit_are_persisted(self) -> None:
        with TemporaryDirectory() as directory:
            store = ShadowPaperStore(Path(directory) / "shadow.sqlite3")
            engine = ShadowPaperEngine(
                store=store,
                allowed_symbols=("BAC",),
                config=ShadowSimulationConfig(
                    initial_cash=Decimal("1500"),
                    capital_source="unit_test",
                    warmup_minutes=3,
                    momentum_lookback_minutes=2,
                    minimum_momentum=Decimal("0.003"),
                ),
            )
            engine.start()
            start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
            prices = ("50.00", "50.10", "50.30")
            for minute, price in enumerate(prices):
                at = start + timedelta(minutes=minute)
                engine.on_stream(
                    stream(
                        quote(
                            "BAC",
                            bid=price,
                            ask=str(Decimal(price) + Decimal("0.02")),
                            observed_at=at,
                        ),
                        at,
                    ),
                    observed_at=at,
                )
            opened = engine.snapshot()
            self.assertEqual(len(opened.positions), 1)
            self.assertEqual(opened.positions[0].quantity, 2)
            self.assertIsInstance(opened.positions[0].quantity, int)
            # The entry is charged ask + slippage, and the commission is taken
            # out of cash as well: the last ask is 50.32, so 50.32 * 1.0002 * 2
            # plus 0.35.
            expected_entry = (
                Decimal("50.32") * (Decimal("1") + Decimal("2") / Decimal("10000"))
            )
            self.assertEqual(opened.positions[0].entry_price, expected_entry)
            self.assertEqual(
                opened.cash,
                Decimal("1500") - expected_entry * 2 - Decimal("0.35"),
            )
            self.assertEqual(opened.fills[0].commission, Decimal("0.35"))

            exit_at = start + timedelta(minutes=4)
            engine.on_stream(
                stream(
                    quote(
                        "BAC",
                        bid="51.00",
                        ask="51.02",
                        observed_at=exit_at,
                    ),
                    exit_at,
                ),
                observed_at=exit_at,
            )
            closed = engine.snapshot()
            self.assertEqual(closed.positions, ())
            self.assertEqual(len(closed.fills), 2)
            self.assertGreater(closed.realized_pnl, Decimal("0"))
            self.assertEqual(len(store.recent_fills()), 2)
            # A SELL is filled at bid minus the same slippage, and the round
            # trip is charged twice: the sell commission already subtracted from
            # proceeds, plus the buy commission paid on entry.
            expected_exit = (
                Decimal("51.00") * (Decimal("1") - Decimal("2") / Decimal("10000"))
            )
            self.assertEqual(closed.fills[1].price, expected_exit)
            self.assertEqual(
                closed.fills[1].realized_pnl,
                (expected_exit - expected_entry) * 2
                - Decimal("0.35")
                - Decimal("0.35"),
            )

    def test_configured_multiplier_reduces_position_notional(self) -> None:
        with TemporaryDirectory() as directory:
            engine = ShadowPaperEngine(
                store=ShadowPaperStore(Path(directory) / "shadow.sqlite3"),
                allowed_symbols=("TQQQ",),
                config=ShadowSimulationConfig(
                    initial_cash=Decimal("1500"),
                    capital_source="unit_test",
                    symbol_risk_multipliers={
                        "TQQQ": Decimal("2")
                    },
                    warmup_minutes=3,
                    momentum_lookback_minutes=2,
                    minimum_momentum=Decimal("0.003"),
                ),
            )
            engine.start()
            start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
            for minute, price in enumerate(("50.00", "50.10", "50.30")):
                at = start + timedelta(minutes=minute)
                engine.on_stream(
                    stream(
                        quote(
                            "TQQQ",
                            bid=price,
                            ask=str(Decimal(price) + Decimal("0.02")),
                            observed_at=at,
                        ),
                        at,
                    ),
                    observed_at=at,
                )
            self.assertEqual(engine.snapshot().positions[0].quantity, 1)

    def test_daily_limits_reset_on_new_york_trading_day(self) -> None:
        with TemporaryDirectory() as directory:
            engine = ShadowPaperEngine(
                store=ShadowPaperStore(Path(directory) / "shadow.sqlite3"),
                allowed_symbols=("BAC",),
                config=ShadowSimulationConfig(
                    initial_cash=Decimal("1500"),
                    capital_source="unit_test",
                    warmup_minutes=3,
                    momentum_lookback_minutes=2,
                    minimum_momentum=Decimal("0.003"),
                ),
            )
            engine.start()
            start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
            for minute, price in enumerate(("50.00", "50.10", "50.30")):
                at = start + timedelta(minutes=minute)
                engine.on_stream(
                    stream(
                        quote(
                            "BAC",
                            bid=price,
                            ask=str(Decimal(price) + Decimal("0.02")),
                            observed_at=at,
                        ),
                        at,
                    ),
                    observed_at=at,
                )
            exit_at = start + timedelta(minutes=4)
            engine.on_stream(
                stream(
                    quote(
                        "BAC",
                        bid="51.00",
                        ask="51.02",
                        observed_at=exit_at,
                    ),
                    exit_at,
                ),
                observed_at=exit_at,
            )
            self.assertEqual(engine.snapshot().trades_today, 1)
            self.assertNotEqual(
                engine.snapshot().daily_realized_pnl,
                Decimal("0"),
            )

            next_day = start + timedelta(days=1)
            engine.on_stream(
                stream(
                    quote(
                        "BAC",
                        bid="51.00",
                        ask="51.02",
                        observed_at=next_day,
                    ),
                    next_day,
                ),
                observed_at=next_day,
            )
            snapshot = engine.snapshot(observed_at=next_day)
            self.assertEqual(snapshot.trades_today, 0)
            self.assertEqual(snapshot.daily_realized_pnl, Decimal("0"))
            self.assertEqual(snapshot.trading_day, "2026-07-25")


if __name__ == "__main__":
    unittest.main()
