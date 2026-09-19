from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from collections import deque

from us_quant.auto_quant import (
    AutoQuantCandidate,
    AutoQuantEngine,
    AutoQuantPosition,
    calculate_quote_readiness_breakdown,
    evaluate_auto_quant_preflight,
)
from us_quant.auto_intraday import resolve_paper_session_capital
from us_quant.ibkr_paper_orders import (
    IBKRPaperOrderUncertainError,
    PaperExecution,
    PaperOrderIntent,
    PaperOrderUpdate,
    new_paper_order_intent,
)
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)
from us_quant.trading.application.risk import RiskApplication
from us_quant.trading.domain.risk import (
    LayeredRiskLimits,
    RiskDecision,
    RiskEvaluationRequest,
    RiskLimits,
    SessionRiskOverrides,
    SymbolRiskOverrides,
)
from us_quant.trading.domain.strategy import (
    StrategyIdentity,
    TradeAction,
)
from us_quant.shadow_paper import ShadowConfig


#: The identity the engine binds.  AutoQuantSnapshot still reports
#: ``strategy_version_id`` / ``parameter_hash``; they are now projections of
#: this object rather than two more constructor arguments.
_STRATEGY = StrategyIdentity(
    strategy_id="intraday-auto-rotation",
    version_id="version",
    parameter_hash="hash",
)


def _risk(
    *,
    account_limits: RiskLimits | None = None,
    symbols: dict[str, SymbolRiskOverrides] | None = None,
    exposure_multipliers: dict[str, Decimal] | None = None,
) -> RiskApplication:
    """A risk application that is permissive by default.

    The ceilings default to "all of it" on purpose.  These tests are about the
    session's signal, sizing and order-lifecycle behaviour; a permissive
    application keeps the strategy's own ``max_position_fraction`` the binding
    ceiling, which is the sizing the pre-migration engine produced.  The risk
    arithmetic itself is asserted in ``test_trading_risk_application``.
    """

    return RiskApplication(
        LayeredRiskLimits(
            account=account_limits
            or RiskLimits(
                max_gross_exposure_pct=Decimal("1"),
                max_position_exposure_pct=Decimal("1"),
                daily_loss_halt_pct=Decimal("1"),
                drawdown_halt_pct=Decimal("1"),
            ),
            symbols=symbols or {},
        ),
        exposure_multipliers=exposure_multipliers,
    )


class AutoQuantTests(unittest.TestCase):
    def test_quote_readiness_separates_candidates_references_and_feed(self) -> None:
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
            recently_ready_symbols={"SPY", "QQQ", "C0"},
        )
        self.assertEqual(one_candidate.candidate_current_count, 1)
        self.assertEqual(one_candidate.candidate_recent_count, 1)

        no_overlap = calculate_quote_readiness_breakdown(
            snapshot,
            candidate_symbols=("SPY", "C0"),
            reference_symbols=("SPY",),
            recently_ready_symbols={"SPY", "C0"},
        )
        self.assertEqual(no_overlap.candidate_count, 1)
        self.assertEqual(no_overlap.candidate_current_count, 0)
        self.assertEqual(no_overlap.reference_current_count, 1)

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

    def test_session_capital_uses_cash_and_optional_limit(self) -> None:
        self.assertEqual(
            resolve_paper_session_capital(
                net_liquidation=Decimal("1000000"),
                cash=Decimal("900000"),
                requested_limit=Decimal("25000"),
            ),
            Decimal("25000"),
        )
        self.assertEqual(
            resolve_paper_session_capital(
                net_liquidation=Decimal("1000000"),
                cash=Decimal("900000"),
                requested_limit=Decimal("0"),
            ),
            Decimal("900000"),
        )

    def test_strongest_candidate_generates_whole_share_paper_limit(
        self,
    ) -> None:
        submitted = []
        engine = _engine(submitted)
        engine.start()
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute in range(3):
            at = start + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(
                    at,
                    {
                        "AAPL": Decimal("200")
                        * (Decimal("1.001") ** minute),
                        "MSFT": Decimal("400")
                        * (Decimal("1.003") ** minute),
                    },
                ),
                observed_at=at,
            )
        self.assertEqual(len(submitted), 1)
        intent = submitted[0]
        self.assertEqual(intent.symbol, "MSFT")
        self.assertEqual(intent.side, "BUY")
        self.assertIsInstance(intent.quantity, int)
        self.assertEqual(intent.quantity, 2)
        self.assertGreater(intent.limit_price, Decimal("400"))

    def test_execution_updates_position_and_stop_emits_sell(self) -> None:
        submitted = []
        engine = _engine(submitted)
        started = engine.start()
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute in range(3):
            at = start + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(
                    at,
                    {
                        "AAPL": Decimal("200")
                        * (Decimal("1.003") ** minute),
                        "MSFT": Decimal("400"),
                    },
                ),
                observed_at=at,
            )
        buy = submitted[0]
        engine.on_execution(
            PaperExecution(
                intent_id=buy.intent_id,
                broker_order_id=1,
                execution_id="exec-buy",
                symbol=buy.symbol,
                side="BUY",
                quantity=Decimal(buy.quantity),
                price=buy.limit_price,
                occurred_at=(start + timedelta(minutes=2)).isoformat(),
            )
        )
        engine.on_order_update(
            PaperOrderUpdate(
                intent_id=buy.intent_id,
                broker_order_id=1,
                status="Filled",
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                average_fill_price=buy.limit_price,
                last_fill_price=buy.limit_price,
                message="",
                observed_at=start.isoformat(),
            )
        )
        self.assertEqual(len(engine.snapshot().positions), 1)
        engine.request_stop()
        at = start + timedelta(minutes=3)
        engine.on_stream(
            _snapshot(at, {buy.symbol: buy.limit_price}),
            observed_at=at,
        )
        self.assertEqual(len(submitted), 2)
        self.assertEqual(submitted[-1].side, "SELL")
        self.assertEqual(submitted[-1].quantity, buy.quantity)
        self.assertEqual(started.candidate_count, 2)

    def test_stop_emits_one_sell_per_position_across_many_ticks(self) -> None:
        """CR-1 regression: stop must never re-submit a SELL per market tick."""
        submitted = []
        engine = _engine(submitted)
        engine.start()
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute in range(3):
            at = start + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(
                    at,
                    {
                        "AAPL": Decimal("200")
                        * (Decimal("1.003") ** minute),
                        "MSFT": Decimal("400"),
                    },
                ),
                observed_at=at,
            )
        buy = submitted[0]
        engine.on_execution(
            PaperExecution(
                intent_id=buy.intent_id,
                broker_order_id=1,
                execution_id="exec-buy-stop",
                symbol=buy.symbol,
                side="BUY",
                quantity=Decimal(buy.quantity),
                price=buy.limit_price,
                occurred_at=start.isoformat(),
            )
        )
        engine.on_order_update(
            PaperOrderUpdate(
                intent_id=buy.intent_id,
                broker_order_id=1,
                status="Filled",
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                average_fill_price=buy.limit_price,
                last_fill_price=buy.limit_price,
                message="",
                observed_at=start.isoformat(),
            )
        )
        engine.request_stop()
        for tick in range(10):
            at = start + timedelta(minutes=3) + timedelta(seconds=tick * 30)
            engine.on_stream(
                _snapshot(at, {buy.symbol: buy.limit_price}),
                observed_at=at,
            )
        sells = [intent for intent in submitted if intent.side == "SELL"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0].symbol, buy.symbol)
        self.assertEqual(sells[0].quantity, buy.quantity)

    def test_delayed_quotes_never_generate_order(self) -> None:
        submitted = []
        engine = _engine(submitted)
        engine.start()
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute in range(4):
            at = start + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(
                    at,
                    {"AAPL": Decimal("200") + minute},
                    ready=False,
                ),
                observed_at=at,
            )
        self.assertEqual(submitted, [])

    def test_reference_gate_requires_fresh_positive_spy_and_qqq_for_buy(
        self,
    ) -> None:
        submitted = []
        engine = _engine(submitted, market_reference_symbols=("SPY", "QQQ"))
        engine.start()
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute in range(3):
            at = start + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(
                    at,
                    {
                        "AAPL": Decimal("200") * (Decimal("1.004") ** minute),
                        "MSFT": Decimal("400"),
                        "SPY": Decimal("500") * (Decimal("1.001") ** minute),
                        "QQQ": Decimal("450") * (Decimal("1.001") ** minute),
                    },
                ),
                observed_at=at,
            )
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0].side, "BUY")

    def test_reference_gate_blocks_buy_when_reference_is_missing_or_negative(
        self,
    ) -> None:
        submitted = []
        engine = _engine(submitted, market_reference_symbols=("SPY", "QQQ"))
        engine.start()
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute in range(3):
            at = start + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(
                    at,
                    {
                        "AAPL": Decimal("200") * (Decimal("1.004") ** minute),
                        "MSFT": Decimal("400"),
                        "SPY": Decimal("500") * (Decimal("0.999") ** minute),
                    },
                ),
                observed_at=at,
        )
        self.assertEqual(submitted, [])
        self.assertIn(
            "entry regime gate blocked", engine.snapshot().status
        )

    def test_reference_gate_never_blocks_stop_requested_sell(self) -> None:
        submitted = []
        engine = _engine(submitted, market_reference_symbols=("SPY", "QQQ"))
        engine.start()
        engine.positions["AAPL"] = AutoQuantPosition(
            symbol="AAPL",
            quantity=2,
            average_price=Decimal("200"),
            opened_at="2026-07-24T14:00:00+00:00",
            high_water=Decimal("200"),
            provider="test",
        )
        engine.request_stop()
        at = datetime(2026, 7, 24, 14, 3, tzinfo=timezone.utc)
        engine.on_stream(
            _snapshot(at, {"AAPL": Decimal("199")}), observed_at=at
        )
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0].side, "SELL")

    def test_pause_blocks_new_entries_without_flattening_and_can_resume(
        self,
    ) -> None:
        submitted = []
        engine = _engine(submitted)
        engine.start()
        paused = engine.pause_entries()
        self.assertTrue(paused.active)
        self.assertTrue(paused.entries_paused)
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute in range(3):
            at = start + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(
                    at,
                    {
                        "AAPL": Decimal("200"),
                        "MSFT": Decimal("400")
                        * (Decimal("1.003") ** minute),
                    },
                ),
                observed_at=at,
            )
        self.assertEqual(submitted, [])
        resumed = engine.resume_entries()
        self.assertFalse(resumed.entries_paused)
        at = start + timedelta(minutes=3)
        engine.on_stream(
            _snapshot(
                at,
                {
                    "AAPL": Decimal("200"),
                    "MSFT": Decimal("404"),
                },
            ),
            observed_at=at,
        )
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0].side, "BUY")

    def test_filled_status_waits_for_execution_before_new_entry(
        self,
    ) -> None:
        submitted = []
        engine = _engine(submitted)
        engine.start()
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute in range(3):
            at = start + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(
                    at,
                    {
                        "AAPL": Decimal("200"),
                        "MSFT": Decimal("400")
                        * (Decimal("1.003") ** minute),
                    },
                ),
                observed_at=at,
            )
        intent = submitted[0]
        waiting = engine.on_order_update(
            PaperOrderUpdate(
                intent_id=intent.intent_id,
                broker_order_id=1,
                status="Filled",
                filled=Decimal(intent.quantity),
                remaining=Decimal("0"),
                average_fill_price=intent.limit_price,
                last_fill_price=intent.limit_price,
                message="",
                observed_at=start.isoformat(),
            )
        )
        self.assertEqual(len(waiting.pending_orders), 1)
        engine.on_stream(
            _snapshot(
                start + timedelta(minutes=3),
                {"MSFT": Decimal("405")},
            ),
            observed_at=start + timedelta(minutes=3),
        )
        self.assertEqual(len(submitted), 1)
        reconciled = engine.on_execution(
            PaperExecution(
                intent_id=intent.intent_id,
                broker_order_id=1,
                execution_id="exec-late",
                symbol=intent.symbol,
                side=intent.side,
                quantity=Decimal(intent.quantity),
                price=intent.limit_price,
                occurred_at=start.isoformat(),
            )
        )
        self.assertEqual(len(reconciled.pending_orders), 0)
        self.assertEqual(len(reconciled.positions), 1)

    def test_rejection_halts_instead_of_retrying_each_minute(self) -> None:
        submitted = []
        engine = _engine(submitted)
        engine.start()
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute in range(3):
            at = start + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(
                    at,
                    {
                        "AAPL": Decimal("200"),
                        "MSFT": Decimal("400")
                        * (Decimal("1.003") ** minute),
                    },
                ),
                observed_at=at,
            )
        intent = submitted[0]
        rejected = engine.on_order_update(
            PaperOrderUpdate(
                intent_id=intent.intent_id,
                broker_order_id=1,
                status="Inactive",
                filled=Decimal("0"),
                remaining=Decimal(intent.quantity),
                average_fill_price=None,
                last_fill_price=None,
                message="order rejected",
                observed_at=start.isoformat(),
            )
        )
        self.assertFalse(rejected.active)
        engine.on_stream(
            _snapshot(
                start + timedelta(minutes=3),
                {"MSFT": Decimal("405")},
            ),
            observed_at=start + timedelta(minutes=3),
        )
        self.assertEqual(len(submitted), 1)

    def test_uncertain_submission_is_retained_and_halts(self) -> None:
        def uncertain(intent):
            raise IBKRPaperOrderUncertainError(
                "uncertain",
                intent_id=intent.intent_id,
                broker_order_id=77,
            )

        engine = AutoQuantEngine(
            candidates=(
                AutoQuantCandidate(
                    "AAPL", "Apple", "科技", 1,
                    Decimal("90"), "趋势候选",
                ),
            ),
            config=ShadowConfig(
                initial_cash=Decimal("10000"),
                capital_source="IBKR Paper",
                max_position_fraction=Decimal("0.10"),
                warmup_minutes=3,
                momentum_lookback_minutes=2,
                minimum_momentum=Decimal("0.003"),
                maximum_momentum=Decimal("0.10"),
                slippage_bps=Decimal("2"),
            ),
            strategy=_STRATEGY,
            risk=_risk(),
            order_sink=uncertain,
        )
        engine.start()
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute in range(3):
            at = start + timedelta(minutes=minute)
            snapshot = engine.on_stream(
                _snapshot(
                    at,
                    {
                        "AAPL": Decimal("200")
                        * (Decimal("1.003") ** minute),
                    },
                ),
                observed_at=at,
            )
        self.assertFalse(snapshot.active)
        self.assertEqual(len(snapshot.pending_orders), 1)
        self.assertIn("不确定", snapshot.status)

    def test_single_minute_spike_is_filtered(self) -> None:
        submitted = []
        engine = AutoQuantEngine(
            candidates=(
                AutoQuantCandidate(
                    "AAPL", "Apple", "科技", 1,
                    Decimal("90"), "趋势候选",
                ),
            ),
            config=ShadowConfig(
                initial_cash=Decimal("10000"),
                capital_source="IBKR Paper",
                max_position_fraction=Decimal("0.10"),
                warmup_minutes=4,
                momentum_lookback_minutes=3,
                minimum_momentum=Decimal("0.003"),
                maximum_momentum=Decimal("0.10"),
                minimum_positive_steps=2,
                maximum_one_minute_move=Decimal("0.01"),
            ),
            strategy=_STRATEGY,
            risk=_risk(),
            order_sink=lambda intent: submitted.append(intent) or 1,
        )
        engine.start()
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute, price in enumerate(
            (
                Decimal("100"),
                Decimal("100"),
                Decimal("100"),
                Decimal("102"),
            )
        ):
            at = start + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(at, {"AAPL": price}),
                observed_at=at,
            )
        self.assertEqual(submitted, [])

    def test_stop_cancels_remainder_then_keeps_partial_position_active(
        self,
    ) -> None:
        submitted = []
        engine = _engine(submitted)
        engine.start()
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute in range(3):
            at = start + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(
                    at,
                    {
                        "AAPL": Decimal("200"),
                        "MSFT": Decimal("400")
                        * (Decimal("1.003") ** minute),
                    },
                ),
                observed_at=at,
            )
        buy = submitted[0]
        engine.on_execution(
            PaperExecution(
                intent_id=buy.intent_id,
                broker_order_id=1,
                execution_id="partial",
                symbol=buy.symbol,
                side="BUY",
                quantity=Decimal("1"),
                price=buy.limit_price,
                occurred_at=start.isoformat(),
            )
        )
        engine.request_stop()
        snapshot = engine.on_order_update(
            PaperOrderUpdate(
                intent_id=buy.intent_id,
                broker_order_id=1,
                status="Cancelled",
                filled=Decimal("1"),
                remaining=Decimal(buy.quantity - 1),
                average_fill_price=buy.limit_price,
                last_fill_price=buy.limit_price,
                message="remainder cancelled",
                observed_at=start.isoformat(),
            )
        )
        self.assertTrue(snapshot.active)
        self.assertEqual(len(snapshot.positions), 1)
        self.assertEqual(len(snapshot.pending_orders), 0)


class AutoQuantRecoveryTests(unittest.TestCase):
    def test_resubmission_replaces_pending_intent_with_fresh_key(self) -> None:
        engine = _engine([])
        started = engine.start()
        assert started.session_id is not None
        original = new_paper_order_intent(
            session_id=started.session_id,
            strategy_version_id="version",
            symbol="AAPL",
            side="BUY",
            quantity=1,
            limit_price=Decimal("200"),
            reason="manual recovery fixture",
        )
        engine.pending[original.intent_id] = original
        engine._intents[original.intent_id] = original

        replacement = engine.resubmit_pending_intent(original)

        self.assertIsNotNone(replacement)
        assert replacement is not None
        self.assertNotEqual(replacement.intent_id, original.intent_id)
        self.assertNotEqual(
            replacement.idempotency_key, original.idempotency_key
        )
        self.assertNotIn(original.intent_id, engine.pending)
        self.assertIs(engine.pending[replacement.intent_id], replacement)


def _engine(
    submitted: list,
    *,
    market_reference_symbols: tuple[str, ...] = (),
) -> AutoQuantEngine:
    def sink(intent):
        submitted.append(intent)
        return len(submitted)

    return AutoQuantEngine(
        candidates=(
            AutoQuantCandidate(
                "AAPL", "Apple", "科技", 1,
                Decimal("90"), "趋势候选",
            ),
            AutoQuantCandidate(
                "MSFT", "Microsoft", "科技", 1,
                Decimal("88"), "趋势候选",
            ),
        ),
        config=ShadowConfig(
            initial_cash=Decimal("10000"),
            capital_source="IBKR Paper",
            max_position_fraction=Decimal("0.10"),
            warmup_minutes=3,
            momentum_lookback_minutes=2,
            minimum_momentum=Decimal("0.003"),
            maximum_momentum=Decimal("0.10"),
            slippage_bps=Decimal("2"),
        ),
        strategy=_STRATEGY,
        risk=_risk(),
        order_sink=sink,
        market_reference_symbols=market_reference_symbols,
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


if __name__ == "__main__":
    unittest.main()


class MultiSymbolTests(unittest.TestCase):
    def test_multi_symbol_entry_respects_symbol_risk_limit(self) -> None:
        candidates = (
            AutoQuantCandidate(symbol="AAA", name="A", sector="T", leader_tier=1, scan_score=Decimal("80"), signal="UP"),
            AutoQuantCandidate(symbol="BBB", name="B", sector="T", leader_tier=1, scan_score=Decimal("75"), signal="UP"),
        )
        intents: list[PaperOrderIntent] = []
        def sink(intent: PaperOrderIntent) -> int:
            intents.append(intent)
            return 1
        engine = AutoQuantEngine(
            candidates=candidates,
            config=ShadowConfig(
                initial_cash=Decimal("10000"),
                capital_source="test",
                max_open_symbols=2,
                max_position_fraction=Decimal("0.5"),
                minimum_momentum=Decimal("0"),
                maximum_momentum=Decimal("1"),
                warmup_minutes=0,
                momentum_lookback_minutes=1,
            ),
            strategy=_STRATEGY,
            risk=_risk(
                symbols={
                    "AAA": SymbolRiskOverrides(
                        max_position_exposure_pct=Decimal("0.05")
                    ),
                    "BBB": SymbolRiskOverrides(allowed=False),
                }
            ),
            order_sink=sink,
        )
        engine.start()
        engine._histories = {
            "AAA": deque([
                (datetime(2024, 1, 2, 19, 59, tzinfo=timezone.utc), Decimal("9.95")),
                (datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc), Decimal("10")),
            ], maxlen=10),
            "BBB": deque([
                (datetime(2024, 1, 2, 19, 59, tzinfo=timezone.utc), Decimal("19.95")),
                (datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc), Decimal("20")),
            ], maxlen=10),
        }
        observed = datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc)
        quotes = (
            MarketQuote(symbol="AAA", mode=MarketDataMode.REALTIME, bid=Decimal("10"), ask=Decimal("10.02"), last=Decimal("10"), close=None, bid_size=None, ask_size=None, updated_at=observed, age_seconds=0, stale=False, stale_reason=None, generation=1, source_id="test_feed", source_label="TestFeed", coverage="test"),
            MarketQuote(symbol="BBB", mode=MarketDataMode.REALTIME, bid=Decimal("20"), ask=Decimal("20.02"), last=Decimal("20"), close=None, bid_size=None, ask_size=None, updated_at=observed, age_seconds=0, stale=False, stale_reason=None, generation=1, source_id="test_feed", source_label="TestFeed", coverage="test"),
        )
        snapshot = engine.on_stream(MarketSnapshot(generation=1, connected=True, ready=True, reconnect_attempt=0, quotes=quotes, error_code=None, message="test", observed_at=observed, source_id="test_feed", source_label="TestFeed", coverage="test"), observed_at=observed)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].symbol, "AAA")
        self.assertEqual(intents[0].quantity, 49)
        self.assertEqual(len(snapshot.positions), 0)
        self.assertEqual(len(snapshot.pending_orders), 1)

    def test_account_daily_loss_halts_entries(self) -> None:
        """H-10: the configured account daily-loss halt must block entries."""
        submitted = []
        engine = AutoQuantEngine(
            candidates=(
                AutoQuantCandidate(
                    "AAPL", "Apple", "科技", 1,
                    Decimal("90"), "趋势候选",
                ),
                AutoQuantCandidate(
                    "MSFT", "Microsoft", "科技", 1,
                    Decimal("88"), "趋势候选",
                ),
            ),
            config=ShadowConfig(
                initial_cash=Decimal("10000"),
                capital_source="IBKR Paper",
                max_position_fraction=Decimal("0.10"),
                warmup_minutes=3,
                momentum_lookback_minutes=2,
                minimum_momentum=Decimal("0.003"),
                maximum_momentum=Decimal("0.10"),
                slippage_bps=Decimal("2"),
                stop_loss=Decimal("0.5"),
                profit_target=Decimal("0.5"),
                trailing_stop=Decimal("0.5"),
                maximum_hold_minutes=10_000,
                maximum_trades_per_day=10,
                max_open_symbols=2,
            ),
            strategy=_STRATEGY,
            risk=_risk(
                account_limits=RiskLimits(
                    max_gross_exposure_pct=Decimal("1"),
                    max_position_exposure_pct=Decimal("1"),
                    daily_loss_halt_pct=Decimal("0.01"),
                    drawdown_halt_pct=Decimal("0.50"),
                )
            ),
            order_sink=lambda intent: submitted.append(intent) or len(submitted),
        )
        engine.start()
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute in range(3):
            at = start + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(
                    at,
                    {
                        "AAPL": Decimal("200")
                        * (Decimal("1.003") ** minute),
                        "MSFT": Decimal("400"),
                    },
                ),
                observed_at=at,
            )
        buys = [i for i in submitted if i.side == "BUY"]
        self.assertEqual(len(buys), 1)
        buy = buys[0]
        engine.on_execution(
            PaperExecution(
                intent_id=buy.intent_id,
                broker_order_id=1,
                execution_id="exec-loss-buy",
                symbol=buy.symbol,
                side="BUY",
                quantity=Decimal(buy.quantity),
                price=buy.limit_price,
                occurred_at=(start + timedelta(minutes=2)).isoformat(),
            )
        )
        engine.on_order_update(
            PaperOrderUpdate(
                intent_id=buy.intent_id,
                broker_order_id=1,
                status="Filled",
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                average_fill_price=buy.limit_price,
                last_fill_price=buy.limit_price,
                message="",
                observed_at=start.isoformat(),
            )
        )
        # AAPL collapses 15% (below the 1% account daily-loss halt) while
        # MSFT develops a fresh momentum signal; the entry must be blocked.
        # The verdict now comes from the risk layer, and the status names it.
        at = start + timedelta(minutes=3)
        engine.on_stream(
            _snapshot(
                at,
                {"AAPL": Decimal("170"), "MSFT": Decimal("404")},
            ),
            observed_at=at,
        )
        self.assertIn("daily account loss halt is active", engine.status)
        self.assertEqual(
            len([i for i in submitted if i.side == "BUY"]), 1
        )

    def test_account_drawdown_halts_entries_after_peak(self) -> None:
        """H-10: the configured account drawdown halt must block entries."""
        submitted = []
        engine = AutoQuantEngine(
            candidates=(
                AutoQuantCandidate(
                    "AAPL", "Apple", "科技", 1,
                    Decimal("90"), "趋势候选",
                ),
                AutoQuantCandidate(
                    "MSFT", "Microsoft", "科技", 1,
                    Decimal("88"), "趋势候选",
                ),
            ),
            config=ShadowConfig(
                initial_cash=Decimal("10000"),
                capital_source="IBKR Paper",
                max_position_fraction=Decimal("0.10"),
                warmup_minutes=3,
                momentum_lookback_minutes=2,
                minimum_momentum=Decimal("0.003"),
                maximum_momentum=Decimal("0.10"),
                slippage_bps=Decimal("2"),
                stop_loss=Decimal("0.95"),
                profit_target=Decimal("0.95"),
                trailing_stop=Decimal("0.95"),
                maximum_hold_minutes=10_000,
                maximum_trades_per_day=10,
                max_open_symbols=2,
            ),
            strategy=_STRATEGY,
            risk=_risk(
                account_limits=RiskLimits(
                    max_gross_exposure_pct=Decimal("1"),
                    max_position_exposure_pct=Decimal("1"),
                    daily_loss_halt_pct=Decimal("0.10"),
                    drawdown_halt_pct=Decimal("0.05"),
                )
            ),
            order_sink=lambda intent: submitted.append(intent) or len(submitted),
        )
        engine.start()
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        prices = [Decimal("200"), Decimal("206"), Decimal("212")]
        for minute in range(3):
            at = start + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(
                    at,
                    {"AAPL": prices[minute], "MSFT": Decimal("400")},
                ),
                observed_at=at,
            )
        buys = [i for i in submitted if i.side == "BUY"]
        self.assertEqual(len(buys), 1)
        buy = buys[0]
        engine.on_execution(
            PaperExecution(
                intent_id=buy.intent_id,
                broker_order_id=1,
                execution_id="exec-dd-buy",
                symbol=buy.symbol,
                side="BUY",
                quantity=Decimal(buy.quantity),
                price=buy.limit_price,
                occurred_at=(start + timedelta(minutes=2)).isoformat(),
            )
        )
        engine.on_order_update(
            PaperOrderUpdate(
                intent_id=buy.intent_id,
                broker_order_id=1,
                status="Filled",
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                average_fill_price=buy.limit_price,
                last_fill_price=buy.limit_price,
                message="",
                observed_at=start.isoformat(),
            )
        )
        # AAPL peaks near 212 then collapses to 80 (-62% from peak, far
        # beyond the 5% drawdown halt) while MSFT gains momentum.
        at = start + timedelta(minutes=3)
        engine.on_stream(
            _snapshot(at, {"AAPL": Decimal("212"), "MSFT": Decimal("400")}),
            observed_at=at,
        )
        at = start + timedelta(minutes=4)
        engine.on_stream(
            _snapshot(at, {"AAPL": Decimal("80"), "MSFT": Decimal("404")}),
            observed_at=at,
        )
        self.assertIn("account drawdown halt is active", engine.status)
        self.assertEqual(
            len([i for i in submitted if i.side == "BUY"]), 1
        )

    def test_risk_exit_limit_prices_off_the_bid(self) -> None:
        """H-11: stop-loss exits must use the bid, not the mid-mark, so the
        resting limit can actually trade."""
        submitted = []
        engine = _engine(submitted)
        engine.start()
        start = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute in range(3):
            at = start + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(
                    at,
                    {
                        "AAPL": Decimal("200")
                        * (Decimal("1.003") ** minute),
                        "MSFT": Decimal("400"),
                    },
                ),
                observed_at=at,
            )
        buy = submitted[0]
        engine.on_execution(
            PaperExecution(
                intent_id=buy.intent_id,
                broker_order_id=1,
                execution_id="exec-bid-exit",
                symbol=buy.symbol,
                side="BUY",
                quantity=Decimal(buy.quantity),
                price=buy.limit_price,
                occurred_at=(start + timedelta(minutes=2)).isoformat(),
            )
        )
        engine.on_order_update(
            PaperOrderUpdate(
                intent_id=buy.intent_id,
                broker_order_id=1,
                status="Filled",
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                average_fill_price=buy.limit_price,
                last_fill_price=buy.limit_price,
                message="",
                observed_at=start.isoformat(),
            )
        )
        # Wide spread: bid 195 / ask 205.  The mid (200) triggers the 0.7%
        # stop-loss; the exit limit must still be derived from the bid.
        at = start + timedelta(minutes=3)
        snapshot = _snapshot(
            at, {"AAPL": Decimal("195"), "MSFT": Decimal("400")}
        )
        snapshot = replace(
            snapshot,
            quotes=tuple(
                replace(
                    quote,
                    ask=Decimal("205"),
                )
                if quote.symbol == "AAPL"
                else quote
                for quote in snapshot.quotes
            ),
        )
        engine.on_stream(snapshot, observed_at=at)
        sells = [i for i in submitted if i.side == "SELL"]
        self.assertEqual(len(sells), 1)
        self.assertLessEqual(sells[0].limit_price, Decimal("195"))

    def test_multi_symbol_exit_uses_matching_quote_only(self) -> None:
        candidates = (
            AutoQuantCandidate(symbol="AAA", name="A", sector="T", leader_tier=1, scan_score=Decimal("80"), signal="UP"),
            AutoQuantCandidate(symbol="BBB", name="B", sector="T", leader_tier=1, scan_score=Decimal("75"), signal="UP"),
        )
        intents: list[PaperOrderIntent] = []
        def sink(intent: PaperOrderIntent) -> int:
            intents.append(intent)
            return 1
        engine = AutoQuantEngine(
            candidates=candidates,
            config=ShadowConfig(
                initial_cash=Decimal("10000"),
                capital_source="test",
                max_open_symbols=2,
                max_position_fraction=Decimal("0.5"),
                profit_target=Decimal("0.01"),
                stop_loss=Decimal("0.01"),
                warmup_minutes=0,
                momentum_lookback_minutes=1,
            ),
            strategy=_STRATEGY,
            risk=_risk(),
            order_sink=sink,
        )
        engine.positions = {
            "AAA": AutoQuantPosition(symbol="AAA", quantity=10, average_price=Decimal("10"), opened_at=datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc).isoformat(), high_water=Decimal("10.5"), provider="test"),
            "BBB": AutoQuantPosition(symbol="BBB", quantity=5, average_price=Decimal("20"), opened_at=datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc).isoformat(), high_water=Decimal("20.5"), provider="test"),
        }
        observed = datetime(2024, 1, 2, 10, 30, tzinfo=timezone.utc)
        quote = MarketQuote(symbol="AAA", bid=Decimal("11.1"), ask=Decimal("11.12"), last=Decimal("11.1"), close=None, bid_size=None, ask_size=None, mode=MarketDataMode.REALTIME, updated_at=observed, age_seconds=0, stale=False, stale_reason=None, generation=1, source_id="test_feed", source_label="TestFeed", coverage="test")
        engine._check_exit(observed, quote)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].symbol, "AAA")


class _RecordingRisk(RiskApplication):
    """A real risk application that remembers what it was asked."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.requests: list[RiskEvaluationRequest] = []

    def evaluate(self, *, request, **kwargs):
        self.requests.append(request)
        return super().evaluate(request=request, **kwargs)

    @property
    def actions(self) -> list[TradeAction]:
        return [
            recorded.proposal.action for recorded in self.requests
        ]


class _RefusingExitRisk(_RecordingRisk):
    """Refuses every reduction, to prove the halt path is reachable."""

    def evaluate(self, *, request, **kwargs):
        self.requests.append(request)
        if request.proposal.action is TradeAction.SELL:
            return RiskDecision.reject(
                "synthetic refusal",
                requested_quantity=request.proposal.desired_quantity,
            )
        return super().evaluate(request=request, **kwargs)


def _single_candidate_engine(
    risk: RiskApplication,
    submitted: list,
    *,
    config: ShadowConfig | None = None,
) -> AutoQuantEngine:
    """One candidate, warmed up, with a permissive risk application."""

    engine = AutoQuantEngine(
        candidates=(
            AutoQuantCandidate(
                "AAA", "A", "T", 1, Decimal("80"), "趋势候选"
            ),
        ),
        config=config
        or ShadowConfig(
            initial_cash=Decimal("10000"),
            capital_source="test",
            max_position_fraction=Decimal("0.5"),
            minimum_momentum=Decimal("0"),
            maximum_momentum=Decimal("1"),
            warmup_minutes=0,
            momentum_lookback_minutes=1,
            maximum_spread_fraction=Decimal("1"),
        ),
        strategy=_STRATEGY,
        risk=risk,
        order_sink=lambda intent: submitted.append(intent) or 1,
    )
    engine.start()
    engine._histories["AAA"] = deque(
        [
            (
                datetime(2024, 1, 2, 19, 59, tzinfo=timezone.utc),
                Decimal("9.95"),
            ),
            (
                datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc),
                Decimal("10"),
            ),
        ],
        maxlen=10,
    )
    return engine


class AutoQuantRiskIntegrationTests(unittest.TestCase):
    """The runtime chain: signal → TradeProposal → RiskApplication → intent.

    These are the tests that would have caught the wiring defect this round
    exists to close: the engine's own risk arithmetic used to be authoritative
    and a unit test could pass while the running system enforced nothing.
    """

    def test_the_engine_no_longer_owns_a_second_risk_policy(self) -> None:
        engine = _single_candidate_engine(_risk(), [])
        for retired in (
            "_layered_risk_limits",
            "_symbol_risk_overrides",
            "_session_risk_overrides",
            "risk_multipliers",
            "strategy_version_id",
            "parameter_hash",
        ):
            self.assertFalse(
                hasattr(engine, retired),
                f"AutoQuantEngine.{retired} should be gone",
            )
        self.assertIsInstance(engine.risk, RiskApplication)
        self.assertIs(engine.strategy, _STRATEGY)

    def test_the_engine_refuses_a_missing_or_wrong_risk_application(self) -> None:
        with self.assertRaises(TypeError):
            AutoQuantEngine(
                candidates=(
                    AutoQuantCandidate(
                        "AAA", "A", "T", 1, Decimal("80"), "UP"
                    ),
                ),
                config=ShadowConfig(
                    initial_cash=Decimal("1000"), capital_source="test"
                ),
                strategy=_STRATEGY,
                risk=None,
                order_sink=lambda intent: 1,
            )
        with self.assertRaises(TypeError):
            AutoQuantEngine(
                candidates=(
                    AutoQuantCandidate(
                        "AAA", "A", "T", 1, Decimal("80"), "UP"
                    ),
                ),
                config=ShadowConfig(
                    initial_cash=Decimal("1000"), capital_source="test"
                ),
                strategy="version",
                risk=_risk(),
                order_sink=lambda intent: 1,
            )

    def test_the_snapshot_projects_the_bound_identity(self) -> None:
        engine = _single_candidate_engine(_risk(), [])
        snapshot = engine.snapshot()
        self.assertEqual(
            snapshot.strategy_version_id, _STRATEGY.version_id
        )
        self.assertEqual(
            snapshot.parameter_hash, _STRATEGY.parameter_hash
        )

    def test_the_risk_layer_trims_the_quantity_the_sink_receives(self) -> None:
        """Spec 105: strategy wants half the account, risk allows 10%."""

        submitted: list[PaperOrderIntent] = []
        risk = _RecordingRisk(
            LayeredRiskLimits(
                account=RiskLimits(
                    max_gross_exposure_pct=Decimal("1"),
                    max_position_exposure_pct=Decimal("0.10"),
                    daily_loss_halt_pct=Decimal("1"),
                    drawdown_halt_pct=Decimal("1"),
                )
            )
        )
        engine = _single_candidate_engine(risk, submitted)
        observed = datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc)
        engine.on_stream(
            _snapshot(observed, {"AAA": Decimal("10")}),
            observed_at=observed,
        )
        self.assertEqual(len(submitted), 1)
        intent = submitted[0]
        # Strategy request: (10000 × 0.5 − 0.35) ÷ 10.02 = 498 whole shares.
        # Risk ceiling: 10000 × 10% = 1000, i.e. 99 whole shares at 10.02.
        self.assertEqual(intent.quantity, 99)
        self.assertLess(intent.quantity, 498)
        self.assertIn("风险缩量 498 → 99", intent.reason)
        self.assertIn("position exposure cap", intent.reason)
        self.assertEqual(risk.requests[-1].proposal.desired_quantity, 498)

    def test_a_blocked_leader_does_not_stop_the_next_candidate(self) -> None:
        """Spec 106: the blocked strongest candidate is skipped, not fatal."""

        candidates = (
            AutoQuantCandidate(
                "AAA", "A", "T", 1, Decimal("80"), "趋势候选"
            ),
            AutoQuantCandidate(
                "BBB", "B", "T", 1, Decimal("75"), "趋势候选"
            ),
        )
        intents: list[PaperOrderIntent] = []
        engine = AutoQuantEngine(
            candidates=candidates,
            config=ShadowConfig(
                initial_cash=Decimal("10000"),
                capital_source="test",
                max_open_symbols=1,
                max_position_fraction=Decimal("0.5"),
                minimum_momentum=Decimal("0"),
                maximum_momentum=Decimal("1"),
                warmup_minutes=0,
                momentum_lookback_minutes=1,
                maximum_spread_fraction=Decimal("1"),
            ),
            strategy=_STRATEGY,
            risk=_risk(
                symbols={"AAA": SymbolRiskOverrides(allowed=False)}
            ),
            order_sink=lambda intent: intents.append(intent) or 1,
        )
        engine.start()
        engine._histories = {
            "AAA": deque(
                [
                    (
                        datetime(2024, 1, 2, 19, 59, tzinfo=timezone.utc),
                        Decimal("9.95"),
                    ),
                    (
                        datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc),
                        Decimal("10"),
                    ),
                ],
                maxlen=10,
            ),
            "BBB": deque(
                [
                    (
                        datetime(2024, 1, 2, 19, 59, tzinfo=timezone.utc),
                        Decimal("19.95"),
                    ),
                    (
                        datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc),
                        Decimal("20"),
                    ),
                ],
                maxlen=10,
            ),
        }
        observed = datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc)
        engine.on_stream(
            _snapshot(
                observed, {"AAA": Decimal("10"), "BBB": Decimal("20")}
            ),
            observed_at=observed,
        )
        self.assertEqual([intent.symbol for intent in intents], ["BBB"])
        self.assertEqual(len(intents), 1)

    def test_an_account_wide_halt_leaves_no_buy_intents(self) -> None:
        """Spec 107: many candidates, one account-wide halt, zero orders."""

        candidates = (
            AutoQuantCandidate(
                "AAA", "A", "T", 1, Decimal("80"), "趋势候选"
            ),
            AutoQuantCandidate(
                "BBB", "B", "T", 1, Decimal("75"), "趋势候选"
            ),
        )
        intents: list[PaperOrderIntent] = []
        engine = AutoQuantEngine(
            candidates=candidates,
            config=ShadowConfig(
                initial_cash=Decimal("10000"),
                capital_source="test",
                max_open_symbols=1,
                max_position_fraction=Decimal("0.5"),
                minimum_momentum=Decimal("0"),
                maximum_momentum=Decimal("1"),
                warmup_minutes=0,
                momentum_lookback_minutes=1,
                maximum_spread_fraction=Decimal("1"),
            ),
            strategy=_STRATEGY,
            risk=_risk(
                account_limits=RiskLimits(
                    max_gross_exposure_pct=Decimal("1"),
                    max_position_exposure_pct=Decimal("1"),
                    daily_loss_halt_pct=Decimal("0.05"),
                    drawdown_halt_pct=Decimal("1"),
                )
            ),
            order_sink=lambda intent: intents.append(intent) or 1,
        )
        engine.start()
        # A 10% equity loss with no positions: the halt is an account fact,
        # not something a candidate can route around.
        engine.estimated_cash = Decimal("9000")
        engine._histories = {
            "AAA": deque(
                [
                    (
                        datetime(2024, 1, 2, 19, 59, tzinfo=timezone.utc),
                        Decimal("9.95"),
                    ),
                    (
                        datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc),
                        Decimal("10"),
                    ),
                ],
                maxlen=10,
            ),
            "BBB": deque(
                [
                    (
                        datetime(2024, 1, 2, 19, 59, tzinfo=timezone.utc),
                        Decimal("19.95"),
                    ),
                    (
                        datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc),
                        Decimal("20"),
                    ),
                ],
                maxlen=10,
            ),
        }
        observed = datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc)
        engine.on_stream(
            _snapshot(
                observed, {"AAA": Decimal("10"), "BBB": Decimal("20")}
            ),
            observed_at=observed,
        )
        self.assertEqual(intents, [])
        self.assertIn("daily account loss halt is active", engine.status)

    def test_every_exit_is_proposed_to_the_risk_layer(self) -> None:
        """Spec 108: stop-loss, force-flat and user stops all pass risk."""

        submitted: list[PaperOrderIntent] = []
        risk = _RecordingRisk(
            LayeredRiskLimits(
                account=RiskLimits(
                    max_gross_exposure_pct=Decimal("1"),
                    max_position_exposure_pct=Decimal("1"),
                    daily_loss_halt_pct=Decimal("1"),
                    drawdown_halt_pct=Decimal("1"),
                )
            )
        )
        engine = _single_candidate_engine(risk, submitted)
        observed = datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc)
        engine.on_stream(
            _snapshot(observed, {"AAA": Decimal("10")}),
            observed_at=observed,
        )
        buy = submitted[0]
        engine.on_execution(
            PaperExecution(
                intent_id=buy.intent_id,
                broker_order_id=1,
                execution_id="exec-risk-exit",
                symbol=buy.symbol,
                side="BUY",
                quantity=Decimal(buy.quantity),
                price=buy.limit_price,
                occurred_at=observed.isoformat(),
            )
        )
        engine.on_order_update(
            PaperOrderUpdate(
                intent_id=buy.intent_id,
                broker_order_id=1,
                status="Filled",
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                average_fill_price=buy.limit_price,
                last_fill_price=buy.limit_price,
                message="",
                observed_at=observed.isoformat(),
            )
        )
        # A 5% collapse trips the 0.7% stop-loss on the next tick.
        later = observed + timedelta(minutes=1)
        engine.on_stream(
            _snapshot(later, {"AAA": Decimal("9.5")}),
            observed_at=later,
        )
        sells = [intent for intent in submitted if intent.side == "SELL"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0].quantity, buy.quantity)
        self.assertIn(TradeAction.SELL, risk.actions)
        sell_request = risk.requests[-1]
        self.assertIs(sell_request.proposal.action, TradeAction.SELL)
        self.assertEqual(
            sell_request.proposal.desired_quantity, buy.quantity
        )

    def test_a_refused_exit_halts_the_session_for_a_human(self) -> None:
        """Spec 77: a refusal of a lawful reduction is not retried through."""

        submitted: list[PaperOrderIntent] = []
        risk = _RefusingExitRisk(
            LayeredRiskLimits(
                account=RiskLimits(
                    max_gross_exposure_pct=Decimal("1"),
                    max_position_exposure_pct=Decimal("1"),
                    daily_loss_halt_pct=Decimal("1"),
                    drawdown_halt_pct=Decimal("1"),
                )
            )
        )
        engine = _single_candidate_engine(risk, submitted)
        observed = datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc)
        engine.on_stream(
            _snapshot(observed, {"AAA": Decimal("10")}),
            observed_at=observed,
        )
        buy = submitted[0]
        engine.on_execution(
            PaperExecution(
                intent_id=buy.intent_id,
                broker_order_id=1,
                execution_id="exec-refused-exit",
                symbol=buy.symbol,
                side="BUY",
                quantity=Decimal(buy.quantity),
                price=buy.limit_price,
                occurred_at=observed.isoformat(),
            )
        )
        engine.on_order_update(
            PaperOrderUpdate(
                intent_id=buy.intent_id,
                broker_order_id=1,
                status="Filled",
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                average_fill_price=buy.limit_price,
                last_fill_price=buy.limit_price,
                message="",
                observed_at=observed.isoformat(),
            )
        )
        later = observed + timedelta(minutes=1)
        engine.on_stream(
            _snapshot(later, {"AAA": Decimal("9.5")}),
            observed_at=later,
        )
        self.assertFalse(engine.active)
        self.assertIn("人工对账", engine.status)
        self.assertEqual(
            [intent for intent in submitted if intent.side == "SELL"], []
        )

    def test_only_one_active_sell_per_position_is_ever_open(self) -> None:
        """CR-1: a refused-then-approved exit loop must not flood the broker."""

        submitted: list[PaperOrderIntent] = []
        engine = _single_candidate_engine(_risk(), submitted)
        observed = datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc)
        engine.on_stream(
            _snapshot(observed, {"AAA": Decimal("10")}),
            observed_at=observed,
        )
        buy = submitted[0]
        engine.on_execution(
            PaperExecution(
                intent_id=buy.intent_id,
                broker_order_id=1,
                execution_id="exec-cr1",
                symbol=buy.symbol,
                side="BUY",
                quantity=Decimal(buy.quantity),
                price=buy.limit_price,
                occurred_at=observed.isoformat(),
            )
        )
        engine.on_order_update(
            PaperOrderUpdate(
                intent_id=buy.intent_id,
                broker_order_id=1,
                status="Filled",
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                average_fill_price=buy.limit_price,
                last_fill_price=buy.limit_price,
                message="",
                observed_at=observed.isoformat(),
            )
        )
        for minute in range(1, 4):
            at = observed + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(at, {"AAA": Decimal("9.5")}),
                observed_at=at,
            )
        self.assertEqual(
            len([i for i in submitted if i.side == "SELL"]), 1
        )
        self.assertEqual(len(engine.pending), 1)

    def test_held_symbols_are_priced_from_their_marks_with_an_average_fallback(
        self,
    ) -> None:
        """Spec 68: an inherited fallback, pinned so it stays a decision.

        The session's own equity and snapshot calculations already value a
        holding at its last mark when one exists and at its average price
        otherwise.  Passing the same convention to the risk layer keeps a
        stale quote from refusing every purchase -- a behaviour change this
        migration does not intend -- while the risk layer itself still fails
        closed when a caller supplies no price at all.
        """

        engine = _single_candidate_engine(_risk(), [])
        engine.positions = {
            "AAA": AutoQuantPosition(
                symbol="AAA",
                quantity=5,
                average_price=Decimal("10"),
                opened_at=datetime(
                    2024, 1, 2, 10, 0, tzinfo=timezone.utc
                ).isoformat(),
                high_water=Decimal("11"),
                provider="test",
            )
        }
        engine._marks.clear()
        self.assertEqual(
            engine._risk_market_prices(), {"AAA": Decimal("10")}
        )
        engine._marks["AAA"] = Decimal("12")
        self.assertEqual(
            engine._risk_market_prices(), {"AAA": Decimal("12")}
        )
        positions = engine._risk_positions()
        self.assertEqual(positions["AAA"].quantity, 5)
        self.assertEqual(positions["AAA"].average_price, Decimal("10"))
        self.assertEqual(
            positions["AAA"].exposure_multiplier,
            engine.risk.exposure_multiplier("AAA"),
        )

    def test_the_session_fraction_is_visible_as_a_risk_reduction(self) -> None:
        """The strategy requests its size; risk alone applies the session cap."""

        submitted: list[PaperOrderIntent] = []
        risk = _RecordingRisk(
            LayeredRiskLimits(
                account=RiskLimits(
                    max_gross_exposure_pct=Decimal("1"),
                    max_position_exposure_pct=Decimal("1"),
                    daily_loss_halt_pct=Decimal("1"),
                    drawdown_halt_pct=Decimal("1"),
                ),
                session=SessionRiskOverrides(
                    max_position_fraction=Decimal("0.25")
                ),
            )
        )
        engine = _single_candidate_engine(risk, submitted)
        engine.config = replace(
            engine.config, max_position_fraction=Decimal("0.5")
        )
        observed = datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc)
        engine.on_stream(
            _snapshot(observed, {"AAA": Decimal("10")}),
            observed_at=observed,
        )
        self.assertEqual(len(submitted), 1)
        # Strategy request: (10000 × 0.5 − 0.35) ÷ 10.02 = 498 whole shares.
        # Session risk cap: 10000 × 25% = 2500, i.e. 249 whole shares.
        self.assertEqual(risk.requests[-1].proposal.desired_quantity, 498)
        self.assertEqual(submitted[0].quantity, 249)
        self.assertIn("风险缩量 498 → 249", submitted[0].reason)
        self.assertIn("position exposure cap", submitted[0].reason)
