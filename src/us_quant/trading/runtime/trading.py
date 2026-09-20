"""Trading runtime: the trading session that turns proposals into orders.

This is the only object in the production path that sees the strategy, risk and
execution layers together, and it owns everything about *the session*: its
identity and activity, its estimated book, its pending orders, its fill history,
its trade count, its pause/halt state and the snapshot the window renders.  It
also owns the ordering that makes a tick safe:

    mark the book, lift the high-water marks, revalue the session,
    flatten or exit if that is due, and only then consider opening anything
    new -- and never in the same tick that just halted.

What it does not do is decide *what* to trade.  ``StrategyRuntime`` answers that
with ranked ``TradeProposal``s; ``OrderDispatch`` walks them and asks
``RiskApplication``, submitting the first that clears it.  Nothing
reduction-shaped bypasses risk either: a stop loss, a take profit, a trailing
stop, a maximum-hold exit, the close-of-session flatten and a user stop all
arrive as proposals and all go through the same verdict.

The strategy never learns that a verdict exists, and the dispatch never learns
why a proposal was made.  Only this class holds both halves, which is what makes
"risk and execution have exactly one caller per session" checkable.  The
session's own state machine lives in ``SessionState``; this class decides *when*
to take one of its transitions.

Order identity, durability and the broker remain where Execution v2 put them:
this class calls ``ExecutionApplication.submit_approved`` through the dispatch
and reads domain facts back.  It builds no ``OrderIntent``, reserves no broker
id, writes no store and reaches no broker.

``AutoQuant`` remains in the snapshot's name and its rows because those are the
artifact and UI vocabulary, and this migration does not rewrite the
presentation layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from us_quant.trading.application.execution import ExecutionApplication
from us_quant.trading.application.risk import RiskApplication
from us_quant.trading.domain.market import MarketQuote, MarketSnapshot
from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
)
from us_quant.trading.runtime.dispatch import DispatchOutcome, OrderDispatch
from us_quant.trading.runtime.artifacts import (
    AutoQuantSnapshot,
    build_snapshot,
)
from us_quant.trading.runtime.models import StrategyPositionView
from us_quant.trading.runtime.portfolio import SessionBook
from us_quant.trading.runtime.session import SessionState
from us_quant.trading.runtime.strategy import StrategyRuntime
from us_quant.shadow_paper import ShadowConfig


NEW_YORK = ZoneInfo("America/New_York")


def _utc(value: datetime | None) -> datetime:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(timezone.utc)


class TradingRuntime:
    def __init__(
        self,
        *,
        config: ShadowConfig,
        strategy: StrategyRuntime,
        risk: RiskApplication,
        execution: ExecutionApplication,
        book: SessionBook | None = None,
        session: SessionState | None = None,
    ) -> None:
        if config.initial_cash <= 0:
            raise ValueError("自动量化初始净值必须为正")
        if not isinstance(strategy, StrategyRuntime):
            raise TypeError("交易运行时必须绑定一个策略运行时")
        if not isinstance(risk, RiskApplication):
            raise TypeError(
                "自动量化必须注入唯一风险评估服务，"
                "不能再由运行时自行解释风险限额"
            )
        if not isinstance(execution, ExecutionApplication):
            raise TypeError(
                "自动量化必须注入唯一执行服务；"
                "策略不得自行构造或提交订单"
            )
        self.config = config
        self.strategy = strategy
        self.identity = strategy.identity
        self.risk = risk
        self.execution = execution
        self.book = book or SessionBook(
            initial_cash=config.initial_cash,
            commission=config.commission_per_order,
        )
        self.session = session or SessionState()
        # Built from the injected authorities, never from a fresh one: the
        # session dispatches through the same risk and execution services it
        # was handed.
        self.dispatch = OrderDispatch(
            config=config, risk=risk, execution=execution
        )

    @property
    def candidates(self) -> tuple:
        return self.strategy.candidates

    # -- session lifecycle -----------------------------------------------

    def start(self) -> AutoQuantSnapshot:
        if self.session.active:
            return self.snapshot()
        self.session.begin(
            candidate_count=len(self.candidates),
            warmup_minutes=self.config.warmup_minutes,
        )
        self.book.reset()
        self.strategy.start()
        return self.snapshot()

    def on_stream(
        self,
        snapshot: MarketSnapshot,
        *,
        observed_at: datetime | None = None,
    ) -> AutoQuantSnapshot:
        if not self.session.active:
            return self.snapshot(observed_at=observed_at)
        now = _utc(observed_at)
        self._roll_trading_day(now)
        ready, reference_ready = self.strategy.ready_quotes(snapshot)
        for symbol, quote in ready.items():
            assert quote.bid is not None and quote.ask is not None
            self.book.mark(symbol, (quote.bid + quote.ask) / Decimal("2"))
        self.strategy.observe(
            now=now, ready=ready, reference_ready=reference_ready
        )
        self.book.update_high_water()
        self.book.update_peak_equity()

        eastern_time = now.astimezone(NEW_YORK).time().replace(
            tzinfo=None
        )
        if self.session.stop_requested:
            if not self.book.positions and not self.book.pending:
                self.session.finish("已停止；以 IBKR Paper 回报完成对账")
                return self.snapshot(observed_at=now)
            # CR-1 regression guard: never re-submit a SELL for a symbol that
            # already has an active sell intent.  Emitting a fresh SELL on
            # every tick is how the broker ends up holding a flood of
            # identical exit orders until fills or a halt.
            pending_sells = self.book.pending_sell_symbols()
            self._flatten(
                now,
                ready,
                reason="用户停止；提交 Paper 限价平仓",
                skip=pending_sells,
            )
            if self.session.active and not pending_sells:
                self.session.status = (
                    "已请求停止；等待 fresh bid 生成 Paper 限价平仓单"
                )
            return self.snapshot(observed_at=now)
        if self.book.positions and not self.book.pending:
            if eastern_time >= self.config.force_flat:
                self._flatten(
                    now, ready, reason="收盘前提交 Paper 限价平仓"
                )
            else:
                self._flatten(now, ready)
        minute = now.replace(second=0, microsecond=0)
        if (
            # The exit paths above can halt the session -- a refused reduction
            # does exactly that -- and a halted session must not go on to open
            # a new position in the same tick.
            self.session.active
            and len(self.book.positions) < self.config.max_open_symbols
            and not self.book.pending
            and self.session.last_evaluation_minute != minute
        ):
            self.session.last_evaluation_minute = minute
            if self.session.entries_paused:
                self.session.status = (
                    "已暂停新开仓；现有持仓的止损、止盈和时段平仓仍运行"
                )
            else:
                self._evaluate_entry(now, ready, reference_ready)
        if (
            self.session.active
            and not ready
            and not self.session.entries_paused
        ):
            self.session.status = "等待 fresh 实时 bid/ask"
        return self.snapshot(observed_at=now)

    def request_stop(self) -> AutoQuantSnapshot:
        if not self.session.active:
            return self.snapshot()
        self.session.request_stop()
        return self.snapshot()

    def pause_entries(self) -> AutoQuantSnapshot:
        if not self.session.active or self.session.stop_requested:
            return self.snapshot()
        self.session.pause_entries()
        return self.snapshot()

    def resume_entries(self) -> AutoQuantSnapshot:
        if not self.session.active or self.session.stop_requested:
            return self.snapshot()
        self.session.resume_entries()
        return self.snapshot()

    def halt_for_reconciliation(self, reason: str) -> AutoQuantSnapshot:
        self.session.halt(reason)
        return self.snapshot()

    def resume_from_reconciliation(
        self,
        *,
        session_id: str,
        allow_force_flat_exit: bool = True,
    ) -> AutoQuantSnapshot:
        if self.session.session_id != session_id:
            raise ValueError("会话 ID 不匹配，不允许恢复其他会话")
        if self.session.active:
            return self.snapshot()
        self.session.resume_after_reconciliation(
            session_id=session_id,
            keeping_positions=(
                allow_force_flat_exit and bool(self.book.positions)
            ),
        )
        return self.snapshot()

    def resubmit_pending_intent(
        self, intent: OrderIntent
    ) -> OrderIntent | None:
        """Re-hang an existing pending order under a fresh identity.

        This is the manual-reconciliation path, not a new strategy signal, so
        the order is re-hung with the quantity, price and side it already had
        and is deliberately *not* re-evaluated by risk.  It builds the new
        identity only: the caller owns the submission, and nothing here reaches
        the broker.
        """

        if intent.order_id not in self.book.pending:
            return None
        new_intent = self.execution.reissue(
            intent,
            reason="对账恢复后人工复核重挂；" + intent.reason,
        )
        # A manual re-hang is a distinct broker order after reconciliation.
        # Keep the original intent in the audit trail, but move the active
        # pending state to the new intent ID and its fresh idempotency key.
        self.book.rehang(intent, new_intent)
        return new_intent

    # -- broker facts ----------------------------------------------------

    def on_order_event(self, event: OrderEvent) -> AutoQuantSnapshot:
        """Apply one broker order-status report.

        The book decides what the report means and whether it is reconciled;
        the session only records how that reads, and stops when it must.
        """

        result = self.book.apply_order_event(
            event, stopping=self.session.stop_requested
        )
        if result.halt:
            self.session.active = False
        if result.status:
            self.session.status = result.status
        return self.snapshot()

    def on_execution(
        self, execution: ExecutionFill
    ) -> AutoQuantSnapshot:
        """Apply one fill: the book moves, or the session stops.

        A duplicate execution id and a fill the book has no intent for are both
        ignored; a fill that contradicts the book or leaves an order
        unreconciled halts the session instead.
        """

        result = self.book.apply_fill(
            execution,
            intent=self.book.intent(execution.order_id),
        )
        if result.closed:
            self.session.trades_today += 1
        if result.halt:
            self.session.active = False
        if result.status:
            self.session.status = result.status
        return self.snapshot()

    # -- snapshot --------------------------------------------------------

    def snapshot(
        self, *, observed_at: datetime | None = None
    ) -> AutoQuantSnapshot:
        return build_snapshot(
            session=self.session,
            book=self.book,
            config=self.config,
            identity=self.identity,
            candidate_count=len(self.candidates),
            observed_at=_utc(observed_at),
        )

    # -- the entry path --------------------------------------------------

    def _evaluate_entry(
        self,
        now: datetime,
        ready: dict[str, MarketQuote],
        reference_ready: dict[str, MarketQuote],
    ) -> None:
        """Ask the strategy what it wants, then let the dispatch walk it.

        The session contributes the facts -- the book projection, the trade
        count, the resolved policy -- and reports whatever the walk produced;
        the ranking and the verdict belong to other layers.
        """

        evaluation = self.strategy.entry_evaluation(
            now=now,
            ready=ready,
            reference_ready=reference_ready,
            positions=self._strategy_views(),
            trades_today=self.session.trades_today,
            realized_pnl=self.book.realized_pnl,
            policy=self.dispatch.policy(),
        )
        if not evaluation.proposals:
            self.session.status = evaluation.status
            return
        outcome, blockages = self.dispatch.run_entries(
            now=now,
            proposals=evaluation.proposals,
            book=self.book,
            session_id=self.session.session_id or "",
            allowed_symbols=self._candidate_symbols(),
        )
        if outcome is not None:
            self._apply_dispatch(outcome)
            return
        self.session.status = "；".join(
            (*evaluation.notes, *blockages)
        )

    def _apply_dispatch(self, outcome: DispatchOutcome) -> None:
        """Make one dispatch outcome visible in the session.

        The dispatch reports; the session is the only thing that mutates its own
        state.  An uncertain submission keeps its intent pending: the broker may
        be holding that order.
        """

        if outcome.intent is not None:
            self.book.add(outcome.intent)
        if not outcome.halt:
            self.session.status = outcome.status
            return
        if outcome.reconciliation_required:
            self.session.halt(outcome.status)
            return
        self.session.active = False
        self.session.status = outcome.status

    # -- the exit path ---------------------------------------------------

    def _flatten(
        self,
        now: datetime,
        ready: dict[str, MarketQuote],
        *,
        reason: str | None = None,
        skip: frozenset[str] | set[str] = frozenset(),
    ) -> None:
        """Reduce the holdings each exit gate calls for on this tick.

        One driver for all three reasons a position is reduced -- a gate firing,
        the close-of-session flatten and a user stop -- because the difference
        is the reason text and whether already-exiting symbols are skipped.  A
        dispatch that halts ends the walk: a refused reduction means the runtime
        and risk disagree about the book, and emitting more orders after that
        would contradict the halt.

        The first line is the other half of that rule, and it is load-bearing.
        A session can be stopped *inside* a tick -- the cross-day rollover halts
        one that still holds a position -- after ``on_stream`` has already
        decided this tick may do work.  An inactive session must never reach
        risk or execution: the marks above may still be updated, because that is
        local bookkeeping, but nothing here may send.  The old engine had the
        same test at the top of each of its exit loops.
        """

        if not self.session.active:
            return
        evaluation = self.strategy.exit_evaluation(
            now=now,
            positions=self._strategy_views(),
            bids={
                symbol: quote.bid
                for symbol, quote in ready.items()
                if quote.bid is not None
            },
            marks=self.book.marks,
            reason=reason,
            skip=skip,
        )
        if evaluation.notes:
            self.session.status = "；".join(evaluation.notes)
        for proposal in evaluation.proposals:
            outcome = self.dispatch.exit(
                now=now,
                proposal=proposal,
                symbol=proposal.symbol,
                book=self.book,
                session_id=self.session.session_id or "",
                allowed_symbols=self._candidate_symbols(),
            )
            self._apply_dispatch(outcome)
            if outcome.halt:
                # A refused reduction has already stopped the session; sending
                # the remaining exits would contradict the halt it just took.
                break

    # -- projections and bookkeeping -------------------------------------

    def _strategy_views(self) -> dict[str, StrategyPositionView]:
        """The session's book, projected for the strategy to read."""

        return {
            symbol: StrategyPositionView.of(position)
            for symbol, position in self.book.positions.items()
        }

    def _candidate_symbols(self) -> frozenset[str]:
        return frozenset(
            candidate.symbol for candidate in self.candidates
        )

    def _roll_trading_day(self, now: datetime) -> None:
        rolled = self.session.roll_trading_day(
            now.astimezone(NEW_YORK).date(),
            has_open_work=bool(self.book.positions or self.book.pending),
        )
        if not rolled:
            return
        self.book.realized_pnl = Decimal("0")
        self.strategy.roll_trading_day()