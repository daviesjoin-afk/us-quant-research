import unittest

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.ibkr_stream import (
    IBKRReadOnlyStream,
    ReadOnlyEClientGuard,
    ReadOnlyViolation,
    StreamStateReducer,
)


class IBKRStreamTests(unittest.TestCase):
    def test_extended_stream_labels_direct_overnight_venue(self) -> None:
        stream = IBKRReadOnlyStream(
            IBKRConnectionConfig(
                host="127.0.0.1",
                port=4002,
                client_id=17,
                api_read_only=True,
                paper_order_submission_enabled=False,
                connection_timeout_seconds=2,
            ),
            symbols=("SPY",),
            requested_market_data_type=1,
            market_exchange="OVERNIGHT",
            provider_label="IBKR 5×24",
        )
        snapshot = stream.snapshot()
        self.assertEqual(stream.market_exchange, "OVERNIGHT")
        self.assertEqual(snapshot.provider, "IBKR 5×24")
        self.assertIn("OVERNIGHT", snapshot.coverage)

    def test_live_quote_is_ready_only_with_type1_and_bid_ask(self) -> None:
        reducer = StreamStateReducer(stale_after_seconds=8)
        reducer.start_generation(1, 1)
        reducer.handshake(1)
        reducer.register_quote(
            generation=1,
            request_id=10,
            symbol="AAPL",
            requested_market_data_type=3,
        )
        reducer.market_data_type(1, 10, 1)
        reducer.tick_price(
            1, 10, 1, 35.9, now_monotonic=100, now_iso="t"
        )
        reducer.tick_price(
            1, 10, 2, 36.0, now_monotonic=100, now_iso="t"
        )
        quote = reducer.snapshot(now_monotonic=101).quotes[0]
        self.assertTrue(quote.realtime_ready)
        self.assertFalse(quote.stale)
        self.assertIsNone(quote.bid_size)
        self.assertIsNone(quote.ask_size)

    def test_delayed_quote_is_never_realtime_ready(self) -> None:
        reducer = StreamStateReducer(stale_after_seconds=8)
        reducer.start_generation(1, 1)
        reducer.handshake(1)
        reducer.register_quote(
            generation=1,
            request_id=10,
            symbol="AAPL",
            requested_market_data_type=3,
        )
        reducer.market_data_type(1, 10, 3)
        reducer.tick_price(
            1, 10, 66, 35.9, now_monotonic=100, now_iso="t"
        )
        reducer.tick_price(
            1, 10, 67, 36.0, now_monotonic=100, now_iso="t"
        )
        quote = reducer.snapshot(now_monotonic=101).quotes[0]
        self.assertFalse(quote.realtime_ready)
        self.assertTrue(quote.stale)
        self.assertIn("非实时", quote.stale_reason or "")

    def test_top_of_book_sizes_are_exposed_without_changing_readiness(
        self,
    ) -> None:
        reducer = StreamStateReducer(stale_after_seconds=8)
        reducer.start_generation(1, 1)
        reducer.handshake(1)
        reducer.register_quote(
            generation=1,
            request_id=10,
            symbol="AAPL",
            requested_market_data_type=1,
        )
        reducer.market_data_type(1, 10, 1)
        reducer.tick_size(1, 10, 0, 500)
        reducer.tick_size(1, 10, 3, 700)
        before_prices = reducer.snapshot().quotes[0]
        self.assertFalse(before_prices.realtime_ready)
        self.assertEqual(str(before_prices.bid_size), "500")
        self.assertEqual(str(before_prices.ask_size), "700")
        reducer.tick_price(1, 10, 1, 200, now_monotonic=100)
        reducer.tick_price(1, 10, 2, 200.1, now_monotonic=100)
        quote = reducer.snapshot(now_monotonic=101).quotes[0]
        self.assertTrue(quote.realtime_ready)
        self.assertEqual(str(quote.bid_size), "500")
        self.assertEqual(str(quote.ask_size), "700")

    def test_stale_threshold_and_generation_drop_old_callbacks(self) -> None:
        reducer = StreamStateReducer(stale_after_seconds=5)
        reducer.start_generation(1, 1)
        reducer.handshake(1)
        reducer.register_quote(
            generation=1,
            request_id=10,
            symbol="MU",
            requested_market_data_type=3,
        )
        reducer.market_data_type(1, 10, 1)
        reducer.tick_price(
            1, 10, 1, 900, now_monotonic=100, now_iso="t"
        )
        self.assertTrue(
            reducer.snapshot(now_monotonic=106).quotes[0].stale
        )
        reducer.start_generation(2, 2)
        reducer.tick_price(
            1, 10, 1, 999, now_monotonic=107, now_iso="old"
        )
        self.assertEqual(reducer.snapshot().quotes, ())

    def test_last_trade_cannot_refresh_stale_bid_ask(self) -> None:
        reducer = StreamStateReducer(stale_after_seconds=5)
        reducer.start_generation(1, 1)
        reducer.handshake(1)
        reducer.register_quote(
            generation=1,
            request_id=10,
            symbol="BAC",
            requested_market_data_type=1,
        )
        reducer.market_data_type(1, 10, 1)
        reducer.tick_price(1, 10, 1, 50, now_monotonic=100)
        reducer.tick_price(1, 10, 2, 50.02, now_monotonic=100)
        reducer.tick_price(1, 10, 4, 50.01, now_monotonic=110)
        quote = reducer.snapshot(now_monotonic=110).quotes[0]
        self.assertTrue(quote.stale)
        self.assertFalse(quote.realtime_ready)
        self.assertIn("bid/ask", quote.stale_reason or "")

    def test_crossed_market_is_never_realtime_ready(self) -> None:
        reducer = StreamStateReducer(stale_after_seconds=5)
        reducer.start_generation(1, 1)
        reducer.handshake(1)
        reducer.register_quote(
            generation=1,
            request_id=10,
            symbol="BAC",
            requested_market_data_type=1,
        )
        reducer.market_data_type(1, 10, 1)
        reducer.tick_price(1, 10, 1, 50.02, now_monotonic=100)
        reducer.tick_price(1, 10, 2, 50.00, now_monotonic=100)
        quote = reducer.snapshot(now_monotonic=101).quotes[0]
        self.assertTrue(quote.stale)
        self.assertFalse(quote.realtime_ready)
        self.assertIn("倒挂", quote.stale_reason or "")

    def test_out_of_order_external_tick_is_ignored(self) -> None:
        reducer = StreamStateReducer(stale_after_seconds=5)
        reducer.start_generation(1, 1)
        reducer.register_quote(
            generation=1,
            request_id=10,
            symbol="BAC",
            requested_market_data_type=1,
        )
        reducer.market_data_type(1, 10, 1)
        reducer.tick_price(
            1,
            10,
            1,
            50.00,
            now_monotonic=100,
            now_iso="2026-07-24T15:00:01+00:00",
        )
        reducer.tick_price(
            1,
            10,
            1,
            40.00,
            now_monotonic=101,
            now_iso="2026-07-24T15:00:00+00:00",
        )
        self.assertEqual(
            str(reducer.snapshot(now_monotonic=101).quotes[0].bid),
            "50.0",
        )

    def test_10197_marks_quote_hard_stale(self) -> None:
        reducer = StreamStateReducer()
        reducer.start_generation(1, 1)
        reducer.register_quote(
            generation=1,
            request_id=10,
            symbol="MU",
            requested_market_data_type=3,
        )
        reducer.error(1, 10, 10197, "competing session")
        snapshot = reducer.snapshot()
        self.assertEqual(snapshot.last_error_code, 10197)
        self.assertIn(
            "10197", snapshot.quotes[0].stale_reason or ""
        )
        reducer.tick_price(1, 10, 4, 901)
        still_blocked = reducer.snapshot().quotes[0]
        self.assertTrue(still_blocked.stale)
        self.assertFalse(still_blocked.realtime_ready)
        self.assertIn(
            "10197", still_blocked.stale_reason or ""
        )

    def test_disconnect_cannot_be_cleared_by_late_tick(self) -> None:
        reducer = StreamStateReducer()
        reducer.start_generation(1, 1)
        reducer.handshake(1)
        reducer.register_quote(
            generation=1,
            request_id=10,
            symbol="MU",
            requested_market_data_type=1,
        )
        reducer.market_data_type(1, 10, 1)
        reducer.tick_price(1, 10, 1, 900)
        reducer.tick_price(1, 10, 2, 901)
        reducer.error(1, 10, 1100, "disconnected")
        reducer.tick_price(1, 10, 4, 900.5)
        snapshot = reducer.snapshot()
        self.assertFalse(snapshot.socket_connected)
        self.assertFalse(snapshot.realtime_ready)
        self.assertTrue(snapshot.quotes[0].stale)

    def test_1102_requires_fresh_bid_and_ask_before_ready(self) -> None:
        reducer = StreamStateReducer()
        reducer.start_generation(1, 1)
        reducer.handshake(1)
        reducer.register_quote(
            generation=1,
            request_id=10,
            symbol="MU",
            requested_market_data_type=1,
        )
        reducer.market_data_type(1, 10, 1)
        reducer.tick_price(1, 10, 1, 900)
        reducer.tick_price(1, 10, 2, 901)
        reducer.error(1, 10, 1102, "restored")
        reducer.tick_price(1, 10, 1, 900.5)
        self.assertFalse(reducer.snapshot().realtime_ready)
        reducer.tick_price(1, 10, 2, 901.5)
        self.assertTrue(reducer.snapshot().realtime_ready)

    def test_all_trading_mutations_are_hard_disabled(self) -> None:
        guard = ReadOnlyEClientGuard()
        for method_name in (
            "placeOrder",
            "placeOrderProtoBuf",
            "cancelOrder",
            "cancelOrderProtoBuf",
            "reqGlobalCancel",
            "exerciseOptions",
            "exerciseOptionsProtoBuf",
            "replaceFA",
            "updateDisplayGroup",
        ):
            with self.subTest(method=method_name):
                with self.assertRaises(ReadOnlyViolation):
                    getattr(guard, method_name)()
