"""Strategy runtime: what the strategy would like to do, and nothing else.

This class answers two questions per tick -- "which entries does this tick
justify, strongest first?" and "which holdings should be reduced, and why?" --
and both answers are ``TradeProposal``s.  It cannot act on one.  It holds no
risk service, no execution service, no order identity, no broker, no fills and
no pending book, so it is structurally incapable of submitting anything, of
granting itself permission, or of disagreeing with the ledger about what is
held: the session owns the book and hands in frozen projections.

Signal detection lives in ``SignalScanner``: the minute histories, the entry
gates and the ranking.  What is here is what the strategy *wants* -- the
whole-share sizing it would like, the limit price it would send, the exit gates
(take profit, stop loss, trailing stop, maximum hold) and the proposal text.

What it deliberately does not own, because each has exactly one other owner:
whether an order may be sent and at what size (risk), and whether it is sent at
all (execution, driven by the session).

Every field it reads from the session arrives as a frozen view
(``StrategyPositionView``, ``StrategySessionPolicy``), which is what lets the
session policy keep living in ``RiskApplication`` without the strategy holding
a reference to it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from us_quant.trading.domain.market import MarketQuote, MarketSnapshot
from us_quant.trading.domain.strategy import (
    StrategyIdentity,
    TradeAction,
    TradeProposal,
)
from us_quant.trading.runtime.models import (
    AutoQuantCandidate,
    EntryEvaluation,
    ExitEvaluation,
    StrategyPositionView,
    StrategySessionPolicy,
)
from us_quant.trading.runtime.signals import SignalScanner
from us_quant.shadow_paper import ShadowConfig


NEW_YORK = ZoneInfo("America/New_York")


def limit_price(
    price: Decimal, slippage_bps: Decimal, *, buy: bool
) -> Decimal:
    """The limit price sent for a reference price, slipped in the safe way.

    A buy slips *up* and a sell slips *down*: the order is priced at the edge
    that can still fill, rather than at a price the market has already left.
    """

    adjustment = slippage_bps / Decimal("10000")
    value = price * (
        Decimal("1") + adjustment
        if buy
        else Decimal("1") - adjustment
    )
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class StrategyRuntime:
    def __init__(
        self,
        *,
        candidates: tuple[AutoQuantCandidate, ...],
        config: ShadowConfig,
        strategy: StrategyIdentity,
        market_reference_symbols: tuple[str, ...] = (),
    ) -> None:
        if not candidates:
            raise ValueError("自动量化候选集不能为空")
        symbols = [row.symbol.strip().upper() for row in candidates]
        if len(set(symbols)) != len(symbols):
            raise ValueError("自动量化候选代码不能重复")
        if not isinstance(strategy, StrategyIdentity):
            raise TypeError("自动量化必须绑定一个策略版本身份")
        self.config = config
        self.identity = strategy
        self.candidates = tuple(
            AutoQuantCandidate(
                symbol=row.symbol.strip().upper(),
                name=row.name,
                sector=row.sector,
                leader_tier=row.leader_tier,
                scan_score=row.scan_score,
                signal=row.signal,
            )
            for row in candidates
        )
        self._scanner = SignalScanner(
            config=config,
            candidates=self.candidates,
            market_reference_symbols=market_reference_symbols,
        )

    @property
    def market_reference_symbols(self) -> tuple[str, ...]:
        return self._scanner.market_reference_symbols

    # -- history ---------------------------------------------------------

    def start(self) -> None:
        """Clear every history: a new session starts cold."""

        self._scanner.clear()

    def roll_trading_day(self) -> None:
        """A new trading day invalidates the minute histories."""

        self._scanner.clear()

    def observe(
        self,
        *,
        now: datetime,
        ready: dict[str, MarketQuote],
        reference_ready: dict[str, MarketQuote],
    ) -> None:
        self._scanner.observe(
            now=now, ready=ready, reference_ready=reference_ready
        )

    def ready_quotes(
        self, snapshot: MarketSnapshot
    ) -> tuple[dict[str, MarketQuote], dict[str, MarketQuote]]:
        """Split one market snapshot into the quotes this strategy can use.

        Only fresh quotes for its own candidates and its market references
        qualify; anything else is somebody else's subscription.  Deciding that
        here rather than in the session keeps "which symbols does this strategy
        read" with the strategy.
        """

        ready = {
            quote.symbol: quote
            for quote in snapshot.quotes
            if quote.symbol in self._scanner_scores()
            and quote.realtime_ready
        }
        reference_ready = {
            quote.symbol: quote
            for quote in snapshot.quotes
            if quote.symbol in self.market_reference_symbols
            and quote.realtime_ready
        }
        return ready, reference_ready

    def _scanner_scores(self) -> frozenset[str]:
        return frozenset(row.symbol for row in self.candidates)

    # -- entries ---------------------------------------------------------

    def entry_evaluation(
        self,
        *,
        now: datetime,
        ready: dict[str, MarketQuote],
        reference_ready: dict[str, MarketQuote],
        positions: dict[str, StrategyPositionView],
        trades_today: int,
        realized_pnl: Decimal,
        policy: StrategySessionPolicy,
    ) -> EntryEvaluation:
        """Scan the candidates and rank the entries this tick justifies.

        The verdict about whether any of them *may* happen is not taken here:
        the proposals come back in rank order and the session asks risk about
        them one at a time, because a leader blocked for a reason that does not
        apply to the next candidate must not hide an executable second.

        The four session gates come first and can end the scan on their own --
        outside the entry window, the trade-count ceiling reached, the
        strategy's own loss gate, or a closed regime.
        """

        local_time = (
            now.astimezone(NEW_YORK).time().replace(tzinfo=None)
        )
        if not (policy.entry_start <= local_time <= policy.last_entry):
            return EntryEvaluation(
                proposals=(), status="等待纽约入场时段 10:00–15:30"
            )
        if trades_today >= policy.maximum_trades_per_day:
            return EntryEvaluation(
                proposals=(), status="已达到当日最大交易次数"
            )
        if realized_pnl <= -policy.daily_loss_limit:
            return EntryEvaluation(
                proposals=(), status="触发自动量化单日亏损停机线"
            )
        regime_block = self._scanner.regime_block(reference_ready)
        if regime_block is not None:
            return EntryEvaluation(
                proposals=(),
                status=f"entry regime gate blocked: {regime_block}",
            )
        proposals: list[TradeProposal] = []
        notes: list[str] = []
        for momentum, symbol, quote, positive_steps, step_count in (
            self._scanner.ranked(ready)
        ):
            assert quote.ask is not None
            proposal = self._entry_proposal(
                now=now,
                symbol=symbol,
                ask=quote.ask,
                momentum=momentum,
                positive_steps=positive_steps,
                step_count=step_count,
                positions=positions,
            )
            if proposal is None:
                notes.append(f"{symbol} 信号通过，但整股资金不足")
                continue
            proposals.append(proposal)
        if proposals:
            return EntryEvaluation(proposals=tuple(proposals))
        if notes:
            return EntryEvaluation(
                proposals=(), status="；".join(notes), notes=tuple(notes)
            )
        return EntryEvaluation(
            proposals=(),
            status=(
                f"候选预热 {self._scanner.warmed_count()}/"
                f"{len(self.candidates)}；"
                "暂无通过点差与动量门的信号"
            ),
        )

    def _entry_proposal(
        self,
        *,
        now: datetime,
        symbol: str,
        ask: Decimal,
        momentum: Decimal,
        positive_steps: int,
        step_count: int,
        positions: dict[str, StrategyPositionView],
    ) -> TradeProposal | None:
        """The entry this candidate justifies, or nothing if it cannot be sized."""

        price = limit_price(
            ask, self.config.slippage_bps, buy=True
        )
        quantity = self._requested_entry_quantity(
            symbol=symbol, limit_price=price, positions=positions
        )
        if quantity <= 0:
            return None
        return TradeProposal(
            strategy=self.identity,
            symbol=symbol,
            action=TradeAction.BUY,
            desired_quantity=quantity,
            # TRANSITIONAL PRICING OWNERSHIP: execution submits this price and
            # risk sized against it, so the two cannot drift apart.  Moving
            # slippage into execution would break that agreement.
            reference_price=price,
            reason=(
                "自动轮动入场；"
                f"{self.config.momentum_lookback_minutes}分钟动量 "
                f"{momentum:.2%}；"
                f"正收益步数 {positive_steps}/{step_count}"
            ),
            generated_at=now,
        )

    def _requested_entry_quantity(
        self,
        *,
        symbol: str,
        limit_price: Decimal,
        positions: dict[str, StrategyPositionView],
    ) -> int:
        """How many whole shares the *strategy* would like, before risk.

        This is strategy/session sizing and nothing else.  It does not divide
        by an exposure multiplier, does not subtract the account's gross room
        and does not cap itself at the estimated cash -- all three are risk
        decisions, and computing them here as well is how the running system
        came to disagree with the risk layer about what was affordable.  What
        remains is ``initial_cash × max_position_fraction``, less whatever this
        symbol already holds, less the commission, converted to whole shares at
        the limit price.
        """

        notional_cap = (
            self.config.initial_cash
            * self.config.max_position_fraction
        )
        existing = positions.get(symbol)
        if existing is not None:
            notional_cap -= (
                existing.average_price * existing.quantity
            )
        affordable = max(
            Decimal("0"),
            notional_cap - self.config.commission_per_order,
        )
        return int(
            (affordable / limit_price).to_integral_value(
                rounding=ROUND_DOWN
            )
        )

    # -- exits -----------------------------------------------------------

    def exit_evaluation(
        self,
        *,
        now: datetime,
        positions: dict[str, StrategyPositionView],
        bids: dict[str, Decimal],
        marks: dict[str, Decimal],
        reason: str | None = None,
        skip: frozenset[str] | set[str] = frozenset(),
    ) -> ExitEvaluation:
        """The reductions due on this tick, one per holding at most.

        ``reason`` turns this into an unconditional pass -- a user stop or the
        close-of-session flatten -- and skips the gate evaluation, because the
        caller has already decided.  Without it, the four hold-based gates are
        checked in their historical order.  ``skip`` is how the caller keeps a
        symbol that is already exiting out of the pass, which is what stops a
        user stop from re-sending the same SELL on every tick.
        """

        proposals: list[TradeProposal] = []
        notes: list[str] = []
        for symbol, position in positions.items():
            if symbol in skip:
                continue
            bid = bids.get(symbol)
            if bid is None:
                if reason is not None:
                    notes.append(
                        f"{reason}，但缺少 fresh bid；禁止生成无报价订单"
                    )
                continue
            proposal = self._exit_proposal(
                now=now,
                position=position,
                bid=bid,
                mark=marks.get(symbol),
                reason=reason,
            )
            if proposal is not None:
                proposals.append(proposal)
        return ExitEvaluation(
            proposals=tuple(proposals), notes=tuple(notes)
        )

    def _exit_proposal(
        self,
        *,
        now: datetime,
        position: StrategyPositionView,
        bid: Decimal,
        mark: Decimal | None,
        reason: str | None,
    ) -> TradeProposal | None:
        """One holding's reduction, or ``None`` when no gate fires.

        The proposal is priced off ``bid``: a limit derived from the mid-mark
        can rest above the bid and never fill, which is how a risk exit turns
        into an intervention halt instead of a flat position.
        """

        if reason is None:
            price = mark if mark is not None else bid
            entry = position.average_price
            minutes_held = (
                now - _utc(position.opened_at)
            ).total_seconds() / 60
            if price >= entry * (
                Decimal("1") + self.config.profit_target
            ):
                reason = "达到止盈门"
            elif price <= entry * (
                Decimal("1") - self.config.stop_loss
            ):
                reason = "触发止损门"
            elif price <= position.high_water * (
                Decimal("1") - self.config.trailing_stop
            ):
                reason = "触发移动止损"
            elif minutes_held >= self.config.maximum_hold_minutes:
                reason = "达到最长持有时间"
            else:
                return None
        return TradeProposal(
            strategy=self.identity,
            symbol=position.symbol,
            action=TradeAction.SELL,
            desired_quantity=position.quantity,
            reference_price=limit_price(
                bid, self.config.slippage_bps, buy=False
            ),
            reason=reason,
            generated_at=now,
        )