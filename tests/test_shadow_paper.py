from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.ibkr_stream import StreamQuote, StreamSnapshot
from us_quant.shadow_paper import (
    ShadowConfig,
    ShadowFill,
    ShadowPaperEngine,
    ShadowPaperStore,
)


def quote(
    symbol: str,
    *,
    bid: str,
    ask: str,
    observed_at: datetime,
    ready: bool = True,
) -> StreamQuote:
    return StreamQuote(
        symbol=symbol,
        request_id=1,
        generation=1,
        requested_market_data_type=1,
        effective_market_data_type=1 if ready else 3,
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=(Decimal(bid) + Decimal(ask)) / Decimal("2"),
        close=None,
        updated_at=observed_at.isoformat(),
        age_seconds=0,
        stale=not ready,
        stale_reason=None if ready else "delayed",
        provider="Alpaca",
        coverage="IEX single exchange; not SIP/NBBO",
    )


def stream(
    row: StreamQuote, observed_at: datetime
) -> StreamSnapshot:
    return StreamSnapshot(
        generation=1,
        socket_connected=True,
        handshake_complete=True,
        reconnect_attempt=1,
        quotes=(row,),
        last_error_code=None,
        last_message="authenticated",
        observed_at=observed_at.isoformat(),
        provider="Alpaca",
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
                config=ShadowConfig(
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
        with TemporaryDirectory() as directory:
            engine = ShadowPaperEngine(
                store=ShadowPaperStore(Path(directory) / "shadow.sqlite3"),
                allowed_symbols=("BAC",),
                config=ShadowConfig(
                    initial_cash=Decimal("1500"),
                    capital_source="unit_test",
                    warmup_minutes=2,
                ),
            )
            engine.start()
            now = datetime(2026, 7, 24, 15, 0, tzinfo=timezone.utc)
            for minute in range(4):
                at = now + timedelta(minutes=minute)
                engine.on_stream(
                    stream(
                        quote(
                            "BAC",
                            bid="50",
                            ask="50.02",
                            observed_at=at,
                            ready=False,
                        ),
                        at,
                    ),
                    observed_at=at,
                )
            self.assertEqual(engine.snapshot().positions, ())
            self.assertEqual(engine.snapshot().fills, ())

    def test_whole_share_entry_and_costed_exit_are_persisted(self) -> None:
        with TemporaryDirectory() as directory:
            store = ShadowPaperStore(Path(directory) / "shadow.sqlite3")
            engine = ShadowPaperEngine(
                store=store,
                allowed_symbols=("BAC",),
                config=ShadowConfig(
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

    def test_configured_multiplier_reduces_position_notional(self) -> None:
        with TemporaryDirectory() as directory:
            engine = ShadowPaperEngine(
                store=ShadowPaperStore(Path(directory) / "shadow.sqlite3"),
                allowed_symbols=("TQQQ",),
                config=ShadowConfig(
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
                config=ShadowConfig(
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
