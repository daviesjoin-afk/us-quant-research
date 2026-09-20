"""``ShadowPaperEngine``: the one owner of a simulated session's state.

The engine owns the lifecycle (``start``/``stop``), the stream sequencing
(``on_stream``), the minute history and the trading-day roll, and it is the only
object that holds the mutable facts of a session: session id, active flag, cash,
realized and daily PnL, the open position, the fills, the trade count, the minute
prices, the marks and the cached risk overrides.  Its entry and exit behaviour
comes from ``ShadowTradeLogic``, which holds no state of its own.

It is deterministic and broker-isolated: it consumes fresh bid/ask quotes, fills
them worse than the touch by the configured slippage and charges a per-order
commission, and it exposes no broker order method at all -- not even a name for
one.  The store is the only thing it writes to.

The class keeps its historical name.  Renaming it to ``ShadowEngine`` would touch
every caller for no behavioural gain, and this round is a package migration.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

from us_quant.shadow.config import ShadowSimulationConfig
from us_quant.shadow.models import (
    ShadowFill,
    ShadowPosition,
    ShadowSnapshot,
)
from us_quant.shadow.store import ShadowPaperStore
from us_quant.shadow.trade_logic import ShadowTradeLogic
from us_quant.trading.domain.market import MarketSnapshot
from us_quant.trading.domain.risk import (
    SessionRiskOverrides,
    SymbolRiskOverrides,
    resolve_session_risk_overrides,
    resolve_symbol_risk_overrides,
)


NEW_YORK = ZoneInfo("America/New_York")


class ShadowPaperEngine(ShadowTradeLogic):
    """Deterministic, broker-isolated intraday execution simulator.

    It consumes only fresh bid/ask quotes and never exposes a broker order
    method. Fills are deliberately worse than the observed touch by the
    configured slippage, then charged a per-order commission.
    """

    def __init__(
        self,
        *,
        store: ShadowPaperStore,
        allowed_symbols: Iterable[str],
        config: ShadowSimulationConfig,
        strategy_version_id: str = "unversioned",
        parameter_hash: str = "unverified",
        target_symbol: str | None = None,
    ) -> None:
        self.store = store
        self.config = config
        if self.config.initial_cash <= 0:
            raise ValueError("shadow initial cash must be positive")
        if not self.config.capital_source.strip():
            raise ValueError("shadow capital source is required")
        self.allowed_symbols = tuple(
            dict.fromkeys(
                symbol.strip().upper()
                for symbol in allowed_symbols
                if symbol.strip()
            )
        )
        if not self.allowed_symbols:
            raise ValueError("shadow universe cannot be empty")
        self._allowed_set = frozenset(self.allowed_symbols)
        self.strategy_version_id = strategy_version_id.strip()
        self.parameter_hash = parameter_hash.strip()
        self.target_symbol = (
            target_symbol or (
                self.allowed_symbols[0]
                if len(self.allowed_symbols) == 1
                else ""
            )
        ).strip().upper()
        if not self.strategy_version_id or not self.parameter_hash:
            raise ValueError("shadow strategy provenance is required")
        if self.target_symbol and self.target_symbol not in self._allowed_set:
            raise ValueError("shadow target must be an allowed symbol")
        self.session_id: str | None = None
        self.active = False
        self.cash = self.config.initial_cash
        self.realized_pnl = Decimal("0")
        self.daily_realized_pnl = Decimal("0")
        self.position: ShadowPosition | None = None
        self.fills: list[ShadowFill] = []
        self.trades_today = 0
        self._trading_day = None
        self.status = "未启动；内部影子成交，不连接券商订单"
        self._minute_prices: dict[
            str, deque[tuple[datetime, Decimal]]
        ] = {
            symbol: deque(maxlen=max(60, self.config.warmup_minutes + 5))
            for symbol in self.allowed_symbols
        }
        self._last_evaluation_minute: datetime | None = None
        self._marks: dict[str, Decimal] = {}
        self._session_risk_overrides: SessionRiskOverrides | None = None
        self._symbol_risk_overrides: dict[str, SymbolRiskOverrides] = {}

    def _session_overrides(self) -> SessionRiskOverrides:
        if self._session_risk_overrides is None:
            self._session_risk_overrides = (
                resolve_session_risk_overrides(
                    self.config.layered_risk_limits
                )
                if self.config.layered_risk_limits is not None
                else SessionRiskOverrides()
            )
        return self._session_risk_overrides

    def _symbol_overrides(
        self, symbol: str
    ) -> SymbolRiskOverrides:
        overrides = self._symbol_risk_overrides.get(symbol)
        if overrides is not None:
            return overrides
        overrides = (
            resolve_symbol_risk_overrides(
                symbol, self.config.layered_risk_limits
            )
            if self.config.layered_risk_limits is not None
            else SymbolRiskOverrides()
        )
        self._symbol_risk_overrides[symbol] = overrides
        return overrides

    def start(self) -> ShadowSnapshot:
        if self.active:
            return self.snapshot()
        self.session_id = uuid4().hex
        self.active = True
        self.cash = self.config.initial_cash
        self.realized_pnl = Decimal("0")
        self.daily_realized_pnl = Decimal("0")
        self.position = None
        self.fills = []
        self.trades_today = 0
        self._trading_day = None
        self._marks.clear()
        self._session_risk_overrides = None
        self._symbol_risk_overrides.clear()
        for history in self._minute_prices.values():
            history.clear()
        self._last_evaluation_minute = None
        self.status = (
            f"预热中：需 {self.config.warmup_minutes} 个分钟样本；"
            "仅使用 fresh bid/ask"
        )
        self.store.create_session(
            session_id=self.session_id,
            initial_cash=self.config.initial_cash,
            capital_source=self.config.capital_source,
            allowed_symbols=self.allowed_symbols,
            strategy_version_id=self.strategy_version_id,
            parameter_hash=self.parameter_hash,
            target_symbol=self.target_symbol,
        )
        return self.snapshot()

    def stop(self, *, observed_at: datetime | None = None) -> ShadowSnapshot:
        if not self.active:
            return self.snapshot(observed_at=observed_at)
        now = _utc(observed_at)
        if self.position is not None:
            self._exit_position(
                now=now,
                quote=None,
                reason="手动停止；按最后有效 mark 影子平仓",
            )
        assert self.session_id is not None
        self.store.stop_session(self.session_id)
        self.active = False
        self.status = "已停止；没有向 IBKR 或其他券商发送订单"
        return self.snapshot(observed_at=now)

    def on_stream(
        self,
        stream: MarketSnapshot,
        *,
        observed_at: datetime | None = None,
    ) -> ShadowSnapshot:
        now = _utc(observed_at or stream.observed_at)
        if not self.active:
            return self.snapshot(observed_at=now)
        self._roll_trading_day(now)
        ready = {
            quote.symbol: quote
            for quote in stream.quotes
            if quote.symbol in self._allowed_set
            and quote.realtime_ready
            and quote.bid is not None
            and quote.ask is not None
        }
        for symbol, quote in ready.items():
            # A fresh bid/ask pair is the execution truth. `last` can be
            # older than either side and must not refresh momentum.
            mark = (quote.bid + quote.ask) / Decimal("2")
            self._marks[symbol] = mark
            self._update_minute(symbol, now, mark)

        had_position = self.position is not None
        if had_position:
            eastern_time = now.astimezone(NEW_YORK).time().replace(
                tzinfo=None
            )
            active_quote = ready.get(self.position.symbol)
            if eastern_time >= self.config.force_flat:
                self._exit_position(
                    now=now,
                    quote=active_quote,
                    reason=(
                        "收盘前强制影子平仓"
                        if active_quote is not None
                        else "收盘前保护；无 fresh quote，按最后有效 mark"
                    ),
                )
            else:
                self._check_exit(now, active_quote)
        exited_this_tick = had_position and self.position is None

        minute = now.replace(second=0, microsecond=0)
        if (
            self.position is None
            and not exited_this_tick
            and self._last_evaluation_minute != minute
        ):
            self._last_evaluation_minute = minute
            self._evaluate_entry(now, ready)
        if not ready:
            self.status = "等待 fresh bid/ask；stale 或非实时行情不会触发信号"
        return self.snapshot(observed_at=now)

    def snapshot(
        self, *, observed_at: datetime | None = None
    ) -> ShadowSnapshot:
        mark = (
            self._marks.get(self.position.symbol)
            if self.position is not None
            else None
        )
        unrealized = Decimal("0")
        position_value = Decimal("0")
        if self.position is not None:
            safe_mark = mark or self.position.entry_price
            position_value = safe_mark * self.position.quantity
            unrealized = (
                safe_mark - self.position.entry_price
            ) * self.position.quantity
        equity = self.cash + position_value
        return ShadowSnapshot(
            session_id=self.session_id,
            strategy_version_id=self.strategy_version_id,
            parameter_hash=self.parameter_hash,
            target_symbol=self.target_symbol,
            active=self.active,
            initial_cash=self.config.initial_cash,
            capital_source=self.config.capital_source,
            cash=self.cash,
            equity=equity,
            realized_pnl=self.realized_pnl,
            daily_realized_pnl=self.daily_realized_pnl,
            unrealized_pnl=unrealized,
            positions=(self.position,) if self.position is not None else (),
            fills=tuple(self.fills),
            trades_today=self.trades_today,
            trading_day=(
                self._trading_day.isoformat()
                if self._trading_day is not None
                else None
            ),
            status=self.status,
            observed_at=_utc(observed_at).isoformat(),
        )

    def _update_minute(
        self, symbol: str, now: datetime, price: Decimal
    ) -> None:
        minute = now.replace(second=0, microsecond=0)
        history = self._minute_prices[symbol]
        if history and history[-1][0] == minute:
            history[-1] = (minute, price)
        else:
            if (
                history
                and (minute - history[-1][0]).total_seconds() > 60
            ):
                history.clear()
            history.append((minute, price))

    def _roll_trading_day(self, now: datetime) -> None:
        trading_day = now.astimezone(NEW_YORK).date()
        if self._trading_day is None:
            self._trading_day = trading_day
            return
        if trading_day == self._trading_day:
            return
        if self.position is not None:
            self._exit_position(
                now=now,
                quote=None,
                reason="跨交易日保护；按最后有效 mark 影子平仓",
            )
        self._trading_day = trading_day
        self.trades_today = 0
        self.daily_realized_pnl = Decimal("0")
        self._last_evaluation_minute = None
        for history in self._minute_prices.values():
            history.clear()
        self.status = f"新交易日 {trading_day.isoformat()}；日内计数已重置"


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


__all__ = ["ShadowPaperEngine"]
