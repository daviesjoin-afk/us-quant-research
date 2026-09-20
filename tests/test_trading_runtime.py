from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from collections import deque

from us_quant.trading.composition.runtime import build_trading_runtime
from us_quant.trading.runtime.artifacts import AutoQuantPosition
from us_quant.trading.runtime.models import AutoQuantCandidate
from us_quant.trading.runtime.trading import TradingRuntime
from us_quant.trading.composition.session_config import resolve_paper_session_capital
from us_quant.trading.application.execution import ExecutionApplication
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)
from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
    OrderStatus,
    Side,
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
from us_quant.trading.ports.broker_execution import (
    BrokerOrderReservation,
    ExecutionRefused,
    ExecutionSubmissionUncertain,
)
from us_quant.trading.runtime.config import TradingSessionConfig


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

def _runtime(
    *,
    candidates: tuple[AutoQuantCandidate, ...],
    config: TradingSessionConfig,
    strategy: StrategyIdentity,
    risk: RiskApplication | None,
    execution: ExecutionApplication | None,
    market_reference_symbols: tuple[str, ...] = (),
) -> TradingRuntime:
    """The session under test, assembled the way the window assembles it.

    It goes through the composition root rather than constructing the runtimes
    directly, so a test can never hand the session a different strategy than the
    one the window would build for the same candidates.
    """

    return build_trading_runtime(
        config=config,
        candidates=candidates,
        identity=strategy,
        risk=risk,
        execution=execution,
        market_reference_symbols=market_reference_symbols,
    )


#: A fixed observation time so order events and fills sort deterministically.
_OBSERVED_AT = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)


class _FakeOrderRepository:
    """In-memory order store for the execution application.

    ``intent``, ``broker_order_id``, ``intent_for_idempotency_key`` and
    ``record_intent`` must be genuinely consistent: the application's
    duplicate-submit guard reads them back before it will reserve a broker id,
    so an inconsistent fake would let the guard be bypassed silently.
    """

    def __init__(self) -> None:
        self._intents: dict[str, OrderIntent] = {}
        self._broker_order_ids: dict[str, int] = {}
        self._statuses: dict[str, OrderStatus] = {}
        self._fills: dict[str, list[ExecutionFill]] = {}
        self._events: list[OrderEvent] = []
        self._by_idempotency_key: dict[str, OrderIntent] = {}

    @property
    def recorded_intents(self) -> tuple[OrderIntent, ...]:
        return tuple(self._intents.values())

    @property
    def recorded_count(self) -> int:
        return len(self._intents)

    def record_intent(
        self,
        intent: OrderIntent,
        *,
        broker_order_id: int,
        account_alias: str,
    ) -> None:
        self._intents[intent.order_id] = intent
        self._broker_order_ids[intent.order_id] = broker_order_id
        self._statuses[intent.order_id] = OrderStatus.SUBMITTING
        if intent.idempotency_key:
            self._by_idempotency_key[intent.idempotency_key] = intent

    def record_event(self, event: OrderEvent) -> None:
        self._events.append(event)
        self._statuses[event.order_id] = event.status

    def record_fill(self, fill: ExecutionFill) -> bool:
        stored = self._fills.setdefault(fill.order_id, [])
        if any(row.execution_id == fill.execution_id for row in stored):
            return False
        stored.append(fill)
        return True

    def status(self, order_id: str) -> OrderStatus | None:
        return self._statuses.get(order_id)

    def intent(self, order_id: str) -> OrderIntent | None:
        return self._intents.get(order_id)

    def broker_order_id(self, order_id: str) -> int | None:
        return self._broker_order_ids.get(order_id)

    def fills(self, order_id: str) -> tuple[ExecutionFill, ...]:
        return tuple(self._fills.get(order_id, ()))

    def intent_for_idempotency_key(
        self, idempotency_key: str
    ) -> OrderIntent | None:
        return self._by_idempotency_key.get(idempotency_key)

    def executed_quantity(self, order_id: str) -> Decimal:
        return sum(
            (row.quantity for row in self._fills.get(order_id, ())),
            Decimal("0"),
        )

    def max_broker_order_id(self) -> int:
        return max(self._broker_order_ids.values(), default=0)


class _RecordingBroker:
    """A broker port that records every reserve/submit pair.

    ``submitted`` collects the intents that actually reached ``submit``, in
    send order, so a test can assert what the strategy asked for by reading the
    intent off it.  ``refuse_reserve`` and ``uncertain_submit`` drive the two
    failure modes the engine must react to without ever retrying.
    """

    def __init__(
        self,
        *,
        submitted: list[OrderIntent] | None = None,
        refuse_reserve: bool = False,
        uncertain_submit: bool = False,
    ) -> None:
        self.submitted = submitted if submitted is not None else []
        self.reserved: list[OrderIntent] = []
        self._refuse_reserve = refuse_reserve
        self._uncertain_submit = uncertain_submit
        self._next_broker_order_id = 1000
        self._intents_by_order_id: dict[str, OrderIntent] = {}

    @property
    def submitted_count(self) -> int:
        return len(self.submitted)

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def reserve(self, intent: OrderIntent) -> BrokerOrderReservation:
        if self._refuse_reserve:
            raise ExecutionRefused("synthetic reserve refusal")
        self._next_broker_order_id += 1
        self._intents_by_order_id[intent.order_id] = intent
        self.reserved.append(intent)
        return BrokerOrderReservation(
            order_id=intent.order_id,
            broker_order_id=self._next_broker_order_id,
            account_alias="DU***TEST",
        )

    def submit(self, reservation: BrokerOrderReservation) -> None:
        if self._uncertain_submit:
            # The application re-raises this with the intent attached; the
            # engine must then keep the order in its pending book.
            raise ExecutionSubmissionUncertain(
                "synthetic uncertain submission",
                order_id=reservation.order_id,
                broker_order_id=reservation.broker_order_id,
            )
        self.submitted.append(
            self._intents_by_order_id[reservation.order_id]
        )

    def cancel(self, order_id: str) -> bool:
        return True

    def events(self) -> tuple[OrderEvent, ...]:
        return ()

    def fills(self) -> tuple[ExecutionFill, ...]:
        return ()


def _execution_app(
    *,
    submitted: list[OrderIntent] | None = None,
    repository: _FakeOrderRepository | None = None,
    refuse_reserve: bool = False,
    uncertain_submit: bool = False,
) -> ExecutionApplication:
    """A real execution application over the in-memory fakes."""

    return ExecutionApplication(
        repository=repository or _FakeOrderRepository(),
        broker=_RecordingBroker(
            submitted=submitted,
            refuse_reserve=refuse_reserve,
            uncertain_submit=uncertain_submit,
        ),
    )


def _intent_event(
    intent: OrderIntent,
    *,
    status: OrderStatus,
    filled: Decimal = Decimal("0"),
    remaining: Decimal | None = None,
    message: str = "",
    occurred_at: datetime | None = None,
) -> OrderEvent:
    """A broker order-status report for one intent.

    ``remaining`` defaults to the whole-share remainder so the cancel and
    reject fixtures read naturally instead of restating the arithmetic.
    """

    return OrderEvent(
        order_id=intent.order_id,
        status=status,
        broker_order_id=1,
        broker_status=status.value,
        filled=filled,
        remaining=(
            Decimal(intent.quantity) - filled
            if remaining is None
            else remaining
        ),
        average_fill_price=intent.limit_price,
        last_fill_price=intent.limit_price,
        message=message,
        idempotency_key=intent.idempotency_key,
        occurred_at=occurred_at or _OBSERVED_AT,
    )


def _intent_fill(
    intent: OrderIntent,
    *,
    execution_id: str,
    quantity: Decimal | int,
    price: Decimal | None = None,
    occurred_at: datetime | None = None,
) -> ExecutionFill:
    """One broker execution for one intent, reported at broker precision."""

    return ExecutionFill(
        execution_id=execution_id,
        order_id=intent.order_id,
        broker_order_id=1,
        symbol=intent.execution_symbol,
        side=intent.side,
        quantity=Decimal(quantity),
        price=intent.limit_price if price is None else price,
        occurred_at=occurred_at or _OBSERVED_AT,
    )


class AutoQuantTests(unittest.TestCase):

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
        self.assertEqual(intent.execution_symbol, "MSFT")
        self.assertEqual(intent.side, Side.BUY)
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
            _intent_fill(
                buy,
                execution_id="exec-buy",
                quantity=buy.quantity,
                price=buy.limit_price,
                occurred_at=start + timedelta(minutes=2),
            )
        )
        engine.on_order_event(
            _intent_event(
                buy,
                status=OrderStatus.FILLED,
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                occurred_at=start,
            )
        )
        self.assertEqual(len(engine.snapshot().positions), 1)
        engine.request_stop()
        at = start + timedelta(minutes=3)
        engine.on_stream(
            _snapshot(at, {buy.execution_symbol: buy.limit_price}),
            observed_at=at,
        )
        self.assertEqual(len(submitted), 2)
        self.assertEqual(submitted[-1].side.order_text, "SELL")
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
            _intent_fill(
                buy,
                execution_id="exec-buy-stop",
                quantity=buy.quantity,
                occurred_at=start,
            )
        )
        engine.on_order_event(
            _intent_event(
                buy,
                status=OrderStatus.FILLED,
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                occurred_at=start,
            )
        )
        engine.request_stop()
        for tick in range(10):
            at = start + timedelta(minutes=3) + timedelta(seconds=tick * 30)
            engine.on_stream(
                _snapshot(at, {buy.execution_symbol: buy.limit_price}),
                observed_at=at,
            )
        sells = [
            intent
            for intent in submitted
            if intent.side.order_text == "SELL"
        ]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0].execution_symbol, buy.execution_symbol)
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
        self.assertEqual(submitted[0].side, Side.BUY)

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
        engine.book.positions["AAPL"] = AutoQuantPosition(
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
        self.assertEqual(submitted[0].side.order_text, "SELL")

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
        self.assertEqual(submitted[0].side, Side.BUY)

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
        waiting = engine.on_order_event(
            _intent_event(
                intent,
                status=OrderStatus.FILLED,
                filled=Decimal(intent.quantity),
                remaining=Decimal("0"),
                occurred_at=start,
            )
        )
        self.assertEqual(len(waiting.pending_orders), 1)
        # A terminal status is not reconciliation: the order stays pending and
        # the session keeps running until the executions say the same thing.
        # Halting here instead would stop a session that is merely waiting.
        self.assertTrue(waiting.active)
        self.assertIn("等待逐笔成交回报对账", waiting.status)
        engine.on_stream(
            _snapshot(
                start + timedelta(minutes=3),
                {"MSFT": Decimal("405")},
            ),
            observed_at=start + timedelta(minutes=3),
        )
        self.assertEqual(len(submitted), 1)
        reconciled = engine.on_execution(
            _intent_fill(
                intent,
                execution_id="exec-late",
                quantity=intent.quantity,
                occurred_at=start,
            )
        )
        self.assertEqual(len(reconciled.pending_orders), 0)
        self.assertEqual(len(reconciled.positions), 1)

    def test_a_fractional_broker_fill_halts_the_session(self) -> None:
        """A fractional fill is a fact to halt on, never a number to round.

        Whole shares are the only thing this session may hold, so a fractional
        execution means the broker and the session disagree about the order.
        Truncating it would leave a position the book can never explain and a
        cash figure that silently disagrees with the broker.
        """

        submitted: list[OrderIntent] = []
        engine = _engine(submitted)
        engine.start()
        observed = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        for minute in range(3):
            at = observed + timedelta(minutes=minute)
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

        halted = engine.on_execution(
            _intent_fill(
                intent,
                execution_id="exec-fractional",
                quantity=Decimal("1.5"),
                occurred_at=observed,
            )
        )

        self.assertFalse(halted.active)
        self.assertIn("非整股", halted.status)
        # Nothing was booked: no position, no fill, and the order is still
        # pending for a human to reconcile rather than quietly cleared.
        self.assertEqual(halted.positions, ())
        self.assertEqual(halted.fills, ())
        self.assertEqual(len(halted.pending_orders), 1)
        self.assertEqual(engine.book.cash, engine.config.initial_cash)

    def test_a_cross_day_halt_reaches_no_risk_and_no_execution(self) -> None:
        """A halt taken inside a tick must not let that tick send anything.

        The rollover halts a session that still holds something, and it does so
        *inside* ``on_stream``, after the tick has already decided it may do
        work.  So the exits that follow are the dangerous part: a fresh quote on
        the new day can breach a stop-loss gate, and without a check that the
        session is still active that reduction would go through risk and reach
        the broker on a session that has just stopped for a human.  The local
        marks may still move -- that is bookkeeping, not trading.
        """

        submitted: list[OrderIntent] = []
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
        engine = _runtime(
            candidates=(
                AutoQuantCandidate(
                    "AAA", "A", "T", 1, Decimal("80"), "趋势候选"
                ),
            ),
            config=TradingSessionConfig(
                initial_cash=Decimal("10000"),
                capital_source="test",
                max_position_fraction=Decimal("0.5"),
                stop_loss=Decimal("0.01"),
                profit_target=Decimal("0.01"),
                warmup_minutes=0,
                momentum_lookback_minutes=1,
            ),
            strategy=_STRATEGY,
            risk=risk,
            execution=_execution_app(submitted=submitted),
        )
        engine.start()
        engine.book.positions = {
            "AAA": AutoQuantPosition(
                symbol="AAA",
                quantity=10,
                average_price=Decimal("10"),
                opened_at=datetime(
                    2024, 1, 2, 19, 30, tzinfo=timezone.utc
                ).isoformat(),
                high_water=Decimal("10"),
                provider="test",
            )
        }
        day_one = datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc)
        engine.on_stream(
            _snapshot(day_one, {"AAA": Decimal("10")}),
            observed_at=day_one,
        )
        # Flat on the day, so nothing was proposed before the rollover.
        self.assertEqual(risk.requests, [])
        self.assertEqual(submitted, [])

        day_two = datetime(2024, 1, 3, 14, 0, tzinfo=timezone.utc)
        halted = engine.on_stream(
            _snapshot(day_two, {"AAA": Decimal("9")}),
            observed_at=day_two,
        )

        self.assertFalse(halted.active)
        self.assertIn("跨交易日", halted.status)
        self.assertIn("人工对账", halted.status)
        self.assertEqual(len(halted.pending_orders), 0)
        # Nothing asked risk, and nothing was submitted: the stop-loss gate
        # below the halt was never evaluated.
        self.assertEqual(risk.requests, [])
        self.assertEqual(submitted, [])
        # The mark still moved: a halt stops orders, not local bookkeeping.
        self.assertEqual(engine.book.marks["AAA"], Decimal("9.01"))

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
        rejected = engine.on_order_event(
            _intent_event(
                intent,
                status=OrderStatus.INACTIVE,
                filled=Decimal("0"),
                remaining=Decimal(intent.quantity),
                message="order rejected",
                occurred_at=start,
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
        engine = _runtime(
            candidates=(
                AutoQuantCandidate(
                    "AAPL", "Apple", "科技", 1,
                    Decimal("90"), "趋势候选",
                ),
            ),
            config=TradingSessionConfig(
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
            execution=_execution_app(uncertain_submit=True),
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
        engine = _runtime(
            candidates=(
                AutoQuantCandidate(
                    "AAPL", "Apple", "科技", 1,
                    Decimal("90"), "趋势候选",
                ),
            ),
            config=TradingSessionConfig(
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
            execution=_execution_app(submitted=submitted),
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
            _intent_fill(
                buy,
                execution_id="partial",
                quantity=1,
                price=buy.limit_price,
                occurred_at=start,
            )
        )
        engine.request_stop()
        snapshot = engine.on_order_event(
            _intent_event(
                buy,
                status=OrderStatus.CANCELED,
                filled=Decimal("1"),
                remaining=Decimal(buy.quantity - 1),
                message="remainder cancelled",
                occurred_at=start,
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
        original = OrderIntent.create(
            session_id=started.session_id,
            strategy_version_id="version",
            signal_symbol="AAPL",
            execution_symbol="AAPL",
            side=Side.BUY,
            quantity=1,
            limit_price=Decimal("200"),
            reason="manual recovery fixture",
        )
        engine.book.pending[original.order_id] = original
        engine.book._intents[original.order_id] = original

        replacement = engine.resubmit_pending_intent(original)

        self.assertIsNotNone(replacement)
        assert replacement is not None
        self.assertNotEqual(replacement.order_id, original.order_id)
        self.assertNotEqual(
            replacement.idempotency_key, original.idempotency_key
        )
        self.assertNotIn(original.order_id, engine.book.pending)
        self.assertIs(engine.book.pending[replacement.order_id], replacement)


def _engine(
    submitted: list,
    *,
    market_reference_symbols: tuple[str, ...] = (),
) -> TradingRuntime:
    return _runtime(
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
        config=TradingSessionConfig(
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
        execution=_execution_app(submitted=submitted),
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
        intents: list[OrderIntent] = []
        engine = _runtime(
            candidates=candidates,
            config=TradingSessionConfig(
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
            execution=_execution_app(submitted=intents),
        )
        engine.start()
        engine.strategy._scanner._histories = {
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
        self.assertEqual(intents[0].execution_symbol, "AAA")
        self.assertEqual(intents[0].quantity, 49)
        self.assertEqual(len(snapshot.positions), 0)
        self.assertEqual(len(snapshot.pending_orders), 1)

    def test_account_daily_loss_halts_entries(self) -> None:
        """H-10: the configured account daily-loss halt must block entries."""
        submitted = []
        engine = _runtime(
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
            config=TradingSessionConfig(
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
            execution=_execution_app(submitted=submitted),
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
        buys = [i for i in submitted if i.side.order_text == "BUY"]
        self.assertEqual(len(buys), 1)
        buy = buys[0]
        engine.on_execution(
            _intent_fill(
                buy,
                execution_id="exec-loss-buy",
                quantity=buy.quantity,
                price=buy.limit_price,
                occurred_at=start + timedelta(minutes=2),
            )
        )
        engine.on_order_event(
            _intent_event(
                buy,
                status=OrderStatus.FILLED,
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                occurred_at=start,
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
        self.assertIn("daily account loss halt is active", engine.session.status)
        self.assertEqual(
            len([i for i in submitted if i.side.order_text == "BUY"]), 1
        )

    def test_account_drawdown_halts_entries_after_peak(self) -> None:
        """H-10: the configured account drawdown halt must block entries."""
        submitted = []
        engine = _runtime(
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
            config=TradingSessionConfig(
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
            execution=_execution_app(submitted=submitted),
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
        buys = [i for i in submitted if i.side.order_text == "BUY"]
        self.assertEqual(len(buys), 1)
        buy = buys[0]
        engine.on_execution(
            _intent_fill(
                buy,
                execution_id="exec-dd-buy",
                quantity=buy.quantity,
                price=buy.limit_price,
                occurred_at=start + timedelta(minutes=2),
            )
        )
        engine.on_order_event(
            _intent_event(
                buy,
                status=OrderStatus.FILLED,
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                occurred_at=start,
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
        self.assertIn("account drawdown halt is active", engine.session.status)
        self.assertEqual(
            len([i for i in submitted if i.side.order_text == "BUY"]), 1
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
            _intent_fill(
                buy,
                execution_id="exec-bid-exit",
                quantity=buy.quantity,
                price=buy.limit_price,
                occurred_at=start + timedelta(minutes=2),
            )
        )
        engine.on_order_event(
            _intent_event(
                buy,
                status=OrderStatus.FILLED,
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                occurred_at=start,
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
        sells = [i for i in submitted if i.side.order_text == "SELL"]
        self.assertEqual(len(sells), 1)
        self.assertLessEqual(sells[0].limit_price, Decimal("195"))

    def test_multi_symbol_exit_uses_matching_quote_only(self) -> None:
        candidates = (
            AutoQuantCandidate(symbol="AAA", name="A", sector="T", leader_tier=1, scan_score=Decimal("80"), signal="UP"),
            AutoQuantCandidate(symbol="BBB", name="B", sector="T", leader_tier=1, scan_score=Decimal("75"), signal="UP"),
        )
        intents: list[OrderIntent] = []
        engine = _runtime(
            candidates=candidates,
            config=TradingSessionConfig(
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
            execution=_execution_app(submitted=intents),
        )
        engine.start()
        engine.book.positions = {
            "AAA": AutoQuantPosition(symbol="AAA", quantity=10, average_price=Decimal("10"), opened_at=datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc).isoformat(), high_water=Decimal("10.5"), provider="test"),
            "BBB": AutoQuantPosition(symbol="BBB", quantity=5, average_price=Decimal("20"), opened_at=datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc).isoformat(), high_water=Decimal("20.5"), provider="test"),
        }
        observed = datetime(2024, 1, 2, 10, 30, tzinfo=timezone.utc)
        quote = MarketQuote(symbol="AAA", bid=Decimal("11.1"), ask=Decimal("11.12"), last=Decimal("11.1"), close=None, bid_size=None, ask_size=None, mode=MarketDataMode.REALTIME, updated_at=observed, age_seconds=0, stale=False, stale_reason=None, generation=1, source_id="test_feed", source_label="TestFeed", coverage="test")
        engine._flatten(observed, {quote.symbol: quote})
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].execution_symbol, "AAA")


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
    config: TradingSessionConfig | None = None,
) -> TradingRuntime:
    """One candidate, warmed up, with a permissive risk application."""

    engine = _runtime(
        candidates=(
            AutoQuantCandidate(
                "AAA", "A", "T", 1, Decimal("80"), "趋势候选"
            ),
        ),
        config=config
        or TradingSessionConfig(
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
        execution=_execution_app(submitted=submitted),
    )
    engine.start()
    engine.strategy._scanner._histories["AAA"] = deque(
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
                f"TradingRuntime.{retired} should be gone",
            )
        self.assertIsInstance(engine.risk, RiskApplication)
        self.assertIs(engine.identity, _STRATEGY)

    def test_the_engine_refuses_a_missing_or_wrong_risk_application(self) -> None:
        with self.assertRaises(TypeError):
            _runtime(
                candidates=(
                    AutoQuantCandidate(
                        "AAA", "A", "T", 1, Decimal("80"), "UP"
                    ),
                ),
                config=TradingSessionConfig(
                    initial_cash=Decimal("1000"), capital_source="test"
                ),
                strategy=_STRATEGY,
                risk=None,
                execution=_execution_app(),
            )
        with self.assertRaises(TypeError):
            _runtime(
                candidates=(
                    AutoQuantCandidate(
                        "AAA", "A", "T", 1, Decimal("80"), "UP"
                    ),
                ),
                config=TradingSessionConfig(
                    initial_cash=Decimal("1000"), capital_source="test"
                ),
                strategy="version",
                risk=_risk(),
                execution=_execution_app(),
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

        submitted: list[OrderIntent] = []
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
        intents: list[OrderIntent] = []
        engine = _runtime(
            candidates=candidates,
            config=TradingSessionConfig(
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
            execution=_execution_app(submitted=intents),
        )
        engine.start()
        engine.strategy._scanner._histories = {
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
        self.assertEqual([intent.execution_symbol for intent in intents], ["BBB"])
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
        intents: list[OrderIntent] = []
        engine = _runtime(
            candidates=candidates,
            config=TradingSessionConfig(
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
            execution=_execution_app(submitted=intents),
        )
        engine.start()
        # A 10% equity loss with no positions: the halt is an account fact,
        # not something a candidate can route around.
        engine.book.cash = Decimal("9000")
        engine.strategy._scanner._histories = {
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
        self.assertIn("daily account loss halt is active", engine.session.status)

    def test_every_exit_is_proposed_to_the_risk_layer(self) -> None:
        """Spec 108: stop-loss, force-flat and user stops all pass risk."""

        submitted: list[OrderIntent] = []
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
            _intent_fill(
                buy,
                execution_id="exec-risk-exit",
                quantity=buy.quantity,
                price=buy.limit_price,
                occurred_at=observed,
            )
        )
        engine.on_order_event(
            _intent_event(
                buy,
                status=OrderStatus.FILLED,
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                occurred_at=observed,
            )
        )
        # A 5% collapse trips the 0.7% stop-loss on the next tick.
        later = observed + timedelta(minutes=1)
        engine.on_stream(
            _snapshot(later, {"AAA": Decimal("9.5")}),
            observed_at=later,
        )
        sells = [intent for intent in submitted if intent.side.order_text == "SELL"]
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

        submitted: list[OrderIntent] = []
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
            _intent_fill(
                buy,
                execution_id="exec-refused-exit",
                quantity=buy.quantity,
                price=buy.limit_price,
                occurred_at=observed,
            )
        )
        engine.on_order_event(
            _intent_event(
                buy,
                status=OrderStatus.FILLED,
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                occurred_at=observed,
            )
        )
        later = observed + timedelta(minutes=1)
        engine.on_stream(
            _snapshot(later, {"AAA": Decimal("9.5")}),
            observed_at=later,
        )
        self.assertFalse(engine.session.active)
        self.assertIn("人工对账", engine.session.status)
        self.assertEqual(
            [intent for intent in submitted if intent.side.order_text == "SELL"], []
        )

    def test_only_one_active_sell_per_position_is_ever_open(self) -> None:
        """CR-1: a refused-then-approved exit loop must not flood the broker."""

        submitted: list[OrderIntent] = []
        engine = _single_candidate_engine(_risk(), submitted)
        observed = datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc)
        engine.on_stream(
            _snapshot(observed, {"AAA": Decimal("10")}),
            observed_at=observed,
        )
        buy = submitted[0]
        engine.on_execution(
            _intent_fill(
                buy,
                execution_id="exec-cr1",
                quantity=buy.quantity,
                price=buy.limit_price,
                occurred_at=observed,
            )
        )
        engine.on_order_event(
            _intent_event(
                buy,
                status=OrderStatus.FILLED,
                filled=Decimal(buy.quantity),
                remaining=Decimal("0"),
                occurred_at=observed,
            )
        )
        for minute in range(1, 4):
            at = observed + timedelta(minutes=minute)
            engine.on_stream(
                _snapshot(at, {"AAA": Decimal("9.5")}),
                observed_at=at,
            )
        self.assertEqual(
            len([i for i in submitted if i.side.order_text == "SELL"]), 1
        )
        self.assertEqual(len(engine.book.pending), 1)

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
        engine.book.positions = {
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
        engine.book.marks.clear()
        self.assertEqual(
            engine.book.risk_market_prices(), {"AAA": Decimal("10")}
        )
        engine.book.marks["AAA"] = Decimal("12")
        self.assertEqual(
            engine.book.risk_market_prices(), {"AAA": Decimal("12")}
        )
        positions = engine.book.risk_positions(
            engine.risk.exposure_multiplier
        )
        self.assertEqual(positions["AAA"].quantity, 5)
        self.assertEqual(positions["AAA"].average_price, Decimal("10"))
        self.assertEqual(
            positions["AAA"].exposure_multiplier,
            engine.risk.exposure_multiplier("AAA"),
        )

    def test_the_session_fraction_is_visible_as_a_risk_reduction(self) -> None:
        """The strategy requests its size; risk alone applies the session cap."""

        submitted: list[OrderIntent] = []
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
