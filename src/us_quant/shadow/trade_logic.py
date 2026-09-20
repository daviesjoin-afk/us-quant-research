"""The shadow simulator's entry and exit behaviour, as a stateless mixin.

``ShadowTradeLogic`` holds the *decisions* -- when a candidate may be entered,
how a simulated fill is priced, when a position must be closed -- and holds no
state at all.  It has no ``__init__``, it does not keep a position, cash, config
or store of its own, and it never reads a value it did not read off ``self``.
``ShadowPaperEngine`` owns every mutable fact and inherits these methods.

That shape is deliberate and is what makes the split a split rather than a second
engine: there is exactly one copy of the session's cash, position, fills and
config, and it lives in the engine.  A mixin that cached any of them would be a
second state source, which is the failure the guard tests refuse.

The behaviour is moved, not rewritten: the entry gates, the momentum window, the
spread filter, the sizing arithmetic, the slippage and commission accounting and
the exit precedence are the ones that ran before this module existed.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from zoneinfo import ZoneInfo

from us_quant.shadow.models import ShadowFill, ShadowPosition
from us_quant.trading.domain.market import MarketQuote


NEW_YORK = ZoneInfo("America/New_York")


class ShadowTradeLogic:
    """Entry/exit behaviour over state the engine owns; defines no state."""

    def _evaluate_entry(
        self,
        now: datetime,
        ready: dict[str, MarketQuote],
    ) -> None:
        session_overrides = self._session_overrides()
        eastern = now.astimezone(NEW_YORK)
        local_time = eastern.time().replace(tzinfo=None)
        entry_start = (
            session_overrides.entry_start
            or self.config.entry_start
        )
        last_entry = (
            session_overrides.last_entry
            or self.config.last_entry
        )
        maximum_trades_per_day = (
            session_overrides.maximum_trades_per_day
            if session_overrides.maximum_trades_per_day is not None
            else self.config.maximum_trades_per_day
        )
        daily_loss_limit = (
            session_overrides.daily_loss_limit
            if session_overrides.daily_loss_limit is not None
            else self.config.daily_loss_limit
        )
        if not (entry_start <= local_time <= last_entry):
            self.status = "非入场时段（纽约 10:00–15:30）"
            return
        if self.trades_today >= maximum_trades_per_day:
            self.status = "已达到当日最大交易次数"
            return
        if self.daily_realized_pnl <= -daily_loss_limit:
            self.status = "触发影子盘单日亏损停机线"
            return

        candidates: list[
            tuple[Decimal, str, MarketQuote, int, Decimal]
        ] = []
        for symbol, quote in ready.items():
            symbol_overrides = self._symbol_overrides(symbol)
            if not symbol_overrides.allowed:
                continue
            history = self._minute_prices[symbol]
            required = max(
                self.config.warmup_minutes,
                self.config.momentum_lookback_minutes + 1,
            )
            if len(history) < required:
                continue
            assert quote.bid is not None and quote.ask is not None
            mid = (quote.bid + quote.ask) / Decimal("2")
            spread_fraction = (quote.ask - quote.bid) / mid
            if spread_fraction > self.config.maximum_spread_fraction:
                continue
            lookback_price = history[
                -(self.config.momentum_lookback_minutes + 1)
            ][1]
            momentum = history[-1][1] / lookback_price - Decimal("1")
            trend_average = sum(
                (row[1] for row in history),
                Decimal("0"),
            ) / Decimal(len(history))
            if not (
                self.config.minimum_momentum
                <= momentum
                <= self.config.maximum_momentum
                and history[-1][1] > trend_average
            ):
                continue
            risk_multiplier = (
                symbol_overrides.exposure_multiplier
                if symbol_overrides.exposure_multiplier != Decimal("1")
                else self.config.symbol_risk_multipliers.get(
                    symbol, Decimal("1")
                )
            )
            max_position_fraction = (
                session_overrides.max_position_fraction
                if session_overrides.max_position_fraction is not None
                else self.config.max_position_fraction
            )
            if symbol_overrides.max_position_exposure_pct is not None:
                max_position_fraction = min(
                    max_position_fraction,
                    symbol_overrides.max_position_exposure_pct,
                )
            if self.config.layered_risk_limits is not None:
                max_position_fraction = min(
                    max_position_fraction,
                    self.config.layered_risk_limits.account
                    .max_position_exposure_pct,
                )
            notional_cap = (
                self.cash
                * max_position_fraction
                / risk_multiplier
            )
            quantity = int(
                (notional_cap / quote.ask).to_integral_value(
                    rounding=ROUND_DOWN
                )
            )
            gross = quote.ask * quantity
            if quantity < 1 or gross < self.config.min_order_notional:
                continue
            candidates.append(
                (momentum, symbol, quote, quantity, gross)
            )
        if not candidates:
            warmest = max(
                (len(history) for history in self._minute_prices.values()),
                default=0,
            )
            self.status = (
                f"扫描 {len(ready)} 个 fresh 标的；"
                f"最长预热 {warmest}/{self.config.warmup_minutes} 分钟，"
                "暂无成本后可执行信号"
            )
            return
        momentum, symbol, quote, quantity, _ = max(candidates)
        self._enter_position(
            now=now,
            quote=quote,
            quantity=quantity,
            reason=f"5分钟动量 {momentum:.2%} + 分钟均价上方",
        )

    def _enter_position(
        self,
        *,
        now: datetime,
        quote: MarketQuote,
        quantity: int,
        reason: str,
    ) -> None:
        assert quote.ask is not None
        assert self.session_id is not None
        price = _with_slippage(
            quote.ask, self.config.slippage_bps, side="BUY"
        )
        total = price * quantity + self.config.commission_per_order
        if total > self.cash:
            self.status = "现金不足，影子订单被拒绝"
            return
        self.cash -= total
        self.position = ShadowPosition(
            symbol=quote.symbol,
            quantity=quantity,
            entry_price=price,
            opened_at=now.isoformat(),
            high_water=price,
            provider=quote.source_label,
            coverage=quote.coverage,
        )
        fill = ShadowFill(
            session_id=self.session_id,
            occurred_at=now.isoformat(),
            symbol=quote.symbol,
            side="BUY",
            quantity=quantity,
            price=price,
            commission=self.config.commission_per_order,
            reason=reason,
            provider=quote.source_label,
            coverage=quote.coverage,
            realized_pnl=None,
        )
        self.fills.append(fill)
        self.store.add_fill(fill)
        self.status = (
            f"影子持仓 {quote.symbol} × {quantity}；"
            "按 ask+滑点成交，未触达券商"
        )

    def _check_exit(
        self,
        now: datetime,
        quote: MarketQuote | None,
    ) -> None:
        position = self.position
        if position is None or quote is None or quote.bid is None:
            return
        current = quote.bid
        if current > position.high_water:
            self.position = ShadowPosition(
                **{
                    **asdict(position),
                    "high_water": current,
                }
            )
            position = self.position
        opened_at = _parse_iso(position.opened_at)
        held_minutes = (now - opened_at).total_seconds() / 60
        eastern_time = now.astimezone(NEW_YORK).time().replace(
            tzinfo=None
        )
        return_fraction = current / position.entry_price - Decimal("1")
        trailing_fraction = (
            current / position.high_water - Decimal("1")
        )
        reason: str | None = None
        if eastern_time >= self.config.force_flat:
            reason = "收盘前强制影子平仓"
        elif return_fraction >= self.config.profit_target:
            reason = "达到成本前价格目标"
        elif return_fraction <= -self.config.stop_loss:
            reason = "触发影子止损"
        elif (
            position.high_water > position.entry_price
            and trailing_fraction <= -self.config.trailing_stop
        ):
            reason = "触发影子移动止盈"
        elif held_minutes >= self.config.maximum_hold_minutes:
            reason = "达到最大持仓时间"
        if reason is not None:
            self._exit_position(now=now, quote=quote, reason=reason)

    def _exit_position(
        self,
        *,
        now: datetime,
        quote: MarketQuote | None,
        reason: str,
    ) -> None:
        position = self.position
        if position is None:
            return
        raw_price = (
            quote.bid
            if quote is not None and quote.bid is not None
            else self._marks.get(position.symbol, position.entry_price)
        )
        price = _with_slippage(
            raw_price, self.config.slippage_bps, side="SELL"
        )
        commission = self.config.commission_per_order
        proceeds = price * position.quantity - commission
        pnl = (
            (price - position.entry_price) * position.quantity
            - commission
            - self.config.commission_per_order
        )
        self.cash += proceeds
        self.realized_pnl += pnl
        self.daily_realized_pnl += pnl
        self.trades_today += 1
        assert self.session_id is not None
        fill = ShadowFill(
            session_id=self.session_id,
            occurred_at=now.isoformat(),
            symbol=position.symbol,
            side="SELL",
            quantity=position.quantity,
            price=price,
            commission=commission,
            reason=reason,
            provider=(
                quote.source_label
                if quote is not None
                else position.provider
            ),
            coverage=(
                quote.coverage if quote is not None else position.coverage
            ),
            realized_pnl=pnl,
        )
        self.fills.append(fill)
        self.store.add_fill(fill)
        self.position = None
        self.status = f"{reason}；本笔净盈亏 {pnl:+.2f} 美元"


def _with_slippage(
    price: Decimal, bps: Decimal, *, side: str
) -> Decimal:
    direction = Decimal("1") if side == "BUY" else Decimal("-1")
    return price * (
        Decimal("1") + direction * bps / Decimal("10000")
    )


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = ["ShadowTradeLogic"]
