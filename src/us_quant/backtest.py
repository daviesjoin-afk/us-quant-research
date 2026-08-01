from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from typing import Iterable, Mapping

from us_quant.domain import MarketSlice, ONE, ZERO, Side
from us_quant.portfolio import IntegerPositionSizer, ResolvedInstrument
from us_quant.strategy import Strategy, TargetAllocation


@dataclass(frozen=True, slots=True)
class CostModel:
    per_share_commission: Decimal
    minimum_commission: Decimal
    slippage_bps: Decimal

    def commission(self, quantity: int) -> Decimal:
        if quantity <= 0:
            return ZERO
        return max(
            self.minimum_commission,
            self.per_share_commission * quantity,
        )

    def execution_price(self, price: Decimal, side: Side) -> Decimal:
        adjustment = self.slippage_bps / Decimal("10000")
        multiplier = ONE + adjustment if side == Side.BUY else ONE - adjustment
        return price * multiplier


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    signal_timestamp: object
    timestamp: object
    signal_symbol: str
    execution_symbol: str
    side: Side
    quantity: int
    raw_price: Decimal
    fill_price: Decimal
    notional: Decimal
    slippage_cost: Decimal
    commission: Decimal
    position_after: int
    cash_after: Decimal
    reason: str
    used_substitution: bool


@dataclass(frozen=True, slots=True)
class BacktestResult:
    initial_equity: Decimal
    final_equity: Decimal
    total_return: Decimal
    max_drawdown: Decimal
    total_commission: Decimal
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[tuple[object, Decimal], ...]


@dataclass(slots=True)
class _Holding:
    quantity: int
    exposure_multiplier: Decimal
    signal_symbol: str
    used_substitution: bool


@dataclass(frozen=True, slots=True)
class _PendingTarget:
    signal_timestamp: object
    target: TargetAllocation


class BacktestEngine:
    """Daily/bar engine with signal-at-close and fill-at-next-open semantics."""

    def __init__(
        self,
        *,
        initial_equity: Decimal,
        strategy: Strategy,
        position_sizer: IntegerPositionSizer,
        cost_model: CostModel,
    ) -> None:
        if initial_equity <= ZERO:
            raise ValueError("initial equity must be positive")
        self.initial_equity = initial_equity
        self.strategy = strategy
        self.position_sizer = position_sizer
        self.cost_model = cost_model

    def run(self, slices: Iterable[MarketSlice]) -> BacktestResult:
        ordered = sorted(slices, key=lambda item: item.timestamp)
        if not ordered:
            raise ValueError("backtest requires market data")

        cash = self.initial_equity
        holdings: dict[str, _Holding] = {}
        close_history: dict[str, list[Decimal]] = defaultdict(list)
        pending: _PendingTarget | None = None
        trades: list[BacktestTrade] = []
        equity_curve: list[tuple[object, Decimal]] = []
        peak = self.initial_equity
        max_drawdown = ZERO

        for market_slice in ordered:
            open_prices = {
                symbol: bar.open
                for symbol, bar in market_slice.bars.items()
            }
            if pending is not None:
                cash = self._rebalance(
                    pending=pending,
                    timestamp=market_slice.timestamp,
                    prices=open_prices,
                    cash=cash,
                    holdings=holdings,
                    trades=trades,
                )
                pending = None

            close_prices = {
                symbol: bar.close
                for symbol, bar in market_slice.bars.items()
            }
            equity = cash + sum(
                close_prices[symbol] * holding.quantity
                for symbol, holding in holdings.items()
                if symbol in close_prices
            )
            equity_curve.append((market_slice.timestamp, equity))
            peak = max(peak, equity)
            drawdown = (peak - equity) / peak if peak > ZERO else ZERO
            max_drawdown = max(max_drawdown, drawdown)

            for symbol, bar in market_slice.bars.items():
                close_history[symbol].append(bar.close)
            target = self.strategy.on_close(
                market_slice, close_history
            )
            pending = (
                _PendingTarget(
                    signal_timestamp=market_slice.timestamp,
                    target=target,
                )
                if target is not None
                else None
            )

        final_equity = equity_curve[-1][1]
        total_commission = sum(
            (trade.commission for trade in trades), start=ZERO
        )
        return BacktestResult(
            initial_equity=self.initial_equity,
            final_equity=final_equity,
            total_return=(final_equity / self.initial_equity) - ONE,
            max_drawdown=max_drawdown,
            total_commission=total_commission,
            trades=tuple(trades),
            equity_curve=tuple(equity_curve),
        )

    def _rebalance(
        self,
        *,
        pending: _PendingTarget,
        timestamp: object,
        prices: Mapping[str, Decimal],
        cash: Decimal,
        holdings: dict[str, _Holding],
        trades: list[BacktestTrade],
    ) -> Decimal:
        equity = cash + sum(
            prices[symbol] * holding.quantity
            for symbol, holding in holdings.items()
            if symbol in prices
        )
        target = pending.target
        target_exposure = equity * target.target_weight
        resolved = self.position_sizer.resolve(
            signal_symbol=target.signal_symbol,
            target_risk_exposure=target_exposure,
            prices=prices,
        )

        target_symbol = resolved.execution_symbol if resolved else None
        for symbol in list(holdings):
            if symbol != target_symbol:
                cash = self._trade(
                    timestamp=timestamp,
                    signal_timestamp=pending.signal_timestamp,
                    signal_symbol=holdings[symbol].signal_symbol,
                    execution_symbol=symbol,
                    side=Side.SELL,
                    quantity=holdings[symbol].quantity,
                    raw_price=prices[symbol],
                    used_substitution=holdings[symbol].used_substitution,
                    cash=cash,
                    holdings=holdings,
                    trades=trades,
                    exposure_multiplier=holdings[
                        symbol
                    ].exposure_multiplier,
                    reason=target.reason,
                )

        if resolved is None:
            return cash

        current_quantity = holdings.get(
            resolved.execution_symbol,
            _Holding(
                quantity=0,
                exposure_multiplier=resolved.exposure_multiplier,
                signal_symbol=resolved.signal_symbol,
                used_substitution=resolved.used_substitution,
            ),
        ).quantity
        delta = resolved.quantity - current_quantity
        if delta == 0:
            return cash
        side = Side.BUY if delta > 0 else Side.SELL
        quantity = abs(delta)
        if side == Side.BUY:
            quantity = self._cap_to_available_cash(
                quantity=quantity,
                raw_price=resolved.price,
                cash=cash,
            )
            if quantity == 0:
                return cash

        return self._trade(
            timestamp=timestamp,
            signal_timestamp=pending.signal_timestamp,
            signal_symbol=resolved.signal_symbol,
            execution_symbol=resolved.execution_symbol,
            side=side,
            quantity=quantity,
            raw_price=resolved.price,
            used_substitution=resolved.used_substitution,
            cash=cash,
            holdings=holdings,
            trades=trades,
            exposure_multiplier=resolved.exposure_multiplier,
            reason=target.reason,
        )

    def _cap_to_available_cash(
        self, *, quantity: int, raw_price: Decimal, cash: Decimal
    ) -> int:
        fill_price = self.cost_model.execution_price(raw_price, Side.BUY)
        estimated_commission = self.cost_model.commission(quantity)
        if fill_price * quantity + estimated_commission <= cash:
            return quantity
        affordable = int(
            ((cash - self.cost_model.minimum_commission) / fill_price)
            .to_integral_value(rounding=ROUND_FLOOR)
        )
        return max(affordable, 0)

    def _trade(
        self,
        *,
        timestamp: object,
        signal_timestamp: object,
        signal_symbol: str,
        execution_symbol: str,
        side: Side,
        quantity: int,
        raw_price: Decimal,
        used_substitution: bool,
        cash: Decimal,
        holdings: dict[str, _Holding],
        trades: list[BacktestTrade],
        exposure_multiplier: Decimal,
        reason: str,
    ) -> Decimal:
        fill_price = self.cost_model.execution_price(raw_price, side)
        commission = self.cost_model.commission(quantity)
        notional = fill_price * quantity
        current = holdings.get(execution_symbol)
        current_quantity = current.quantity if current else 0

        if side == Side.BUY:
            cash -= notional + commission
            new_quantity = current_quantity + quantity
        else:
            if quantity > current_quantity:
                raise ValueError("backtest attempted to create a short")
            cash += notional - commission
            new_quantity = current_quantity - quantity

        if new_quantity == 0:
            holdings.pop(execution_symbol, None)
        else:
            holdings[execution_symbol] = _Holding(
                quantity=new_quantity,
                exposure_multiplier=exposure_multiplier,
                signal_symbol=signal_symbol,
                used_substitution=used_substitution,
            )

        cash_after = cash
        trades.append(
            BacktestTrade(
                signal_timestamp=signal_timestamp,
                timestamp=timestamp,
                signal_symbol=signal_symbol,
                execution_symbol=execution_symbol,
                side=side,
                quantity=quantity,
                raw_price=raw_price,
                fill_price=fill_price,
                notional=notional,
                slippage_cost=abs(fill_price - raw_price) * quantity,
                commission=commission,
                position_after=new_quantity,
                cash_after=cash_after,
                reason=reason,
                used_substitution=used_substitution,
            )
        )
        return cash
