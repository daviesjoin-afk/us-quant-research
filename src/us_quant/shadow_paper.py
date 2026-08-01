from __future__ import annotations

from collections import deque
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
import sqlite3
from typing import Iterable, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

from us_quant.ibkr_stream import StreamQuote, StreamSnapshot
from us_quant.risk import (
    LayeredRiskLimits,
    SessionRiskOverrides,
    SymbolRiskOverrides,
    resolve_session_risk_overrides,
    resolve_symbol_risk_overrides,
)
from us_quant.sqlite_support import connect_sqlite


NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class ShadowConfig:
    initial_cash: Decimal
    capital_source: str
    max_position_fraction: Decimal = Decimal("0.10")
    symbol_risk_multipliers: Mapping[str, Decimal] = field(
        default_factory=dict
    )
    min_order_notional: Decimal = Decimal("50")
    commission_per_order: Decimal = Decimal("0.35")
    slippage_bps: Decimal = Decimal("2")
    maximum_spread_fraction: Decimal = Decimal("0.002")
    momentum_lookback_minutes: int = 5
    warmup_minutes: int = 10
    minimum_momentum: Decimal = Decimal("0.0035")
    maximum_momentum: Decimal = Decimal("0.025")
    minimum_positive_steps: int = 0
    maximum_one_minute_move: Decimal = Decimal("1")
    profit_target: Decimal = Decimal("0.012")
    stop_loss: Decimal = Decimal("0.007")
    trailing_stop: Decimal = Decimal("0.006")
    maximum_hold_minutes: int = 45
    maximum_trades_per_day: int = 4
    entry_order_timeout_seconds: int = 90
    daily_loss_limit: Decimal = Decimal("15")
    entry_start: time = time(10, 0)
    last_entry: time = time(15, 30)
    force_flat: time = time(15, 45)
    max_open_symbols: int = 1
    layered_risk_limits: LayeredRiskLimits | None = field(
        default=None, repr=False
    )


@dataclass(frozen=True, slots=True)
class ShadowPosition:
    symbol: str
    quantity: int
    entry_price: Decimal
    opened_at: str
    high_water: Decimal
    provider: str
    coverage: str


@dataclass(frozen=True, slots=True)
class ShadowFill:
    session_id: str
    occurred_at: str
    symbol: str
    side: str
    quantity: int
    price: Decimal
    commission: Decimal
    reason: str
    provider: str
    coverage: str
    realized_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class ShadowSessionProvenance:
    session_id: str
    strategy_version_id: str
    parameter_hash: str
    target_symbol: str
    allowed_symbols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ShadowSnapshot:
    session_id: str | None
    strategy_version_id: str
    parameter_hash: str
    target_symbol: str
    active: bool
    initial_cash: Decimal
    capital_source: str
    cash: Decimal
    equity: Decimal
    realized_pnl: Decimal
    daily_realized_pnl: Decimal
    unrealized_pnl: Decimal
    positions: tuple[ShadowPosition, ...]
    fills: tuple[ShadowFill, ...]
    trades_today: int
    trading_day: str | None
    status: str
    observed_at: str


class ShadowPaperStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_session(
        self,
        *,
        session_id: str,
        initial_cash: Decimal,
        capital_source: str,
        allowed_symbols: tuple[str, ...],
        strategy_version_id: str,
        parameter_hash: str,
        target_symbol: str,
    ) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO shadow_session(
                        session_id, started_at, stopped_at, mode,
                        initial_cash, capital_source, allowed_symbols,
                        strategy_version_id, parameter_hash, target_symbol
                    ) VALUES (
                        ?, ?, NULL, 'internal_shadow', ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        session_id,
                        _now_iso(),
                        str(initial_cash),
                        capital_source,
                        ",".join(allowed_symbols),
                        strategy_version_id,
                        parameter_hash,
                        target_symbol,
                    ),
                )

    def stop_session(self, session_id: str) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE shadow_session
                    SET stopped_at = ?
                    WHERE session_id = ? AND stopped_at IS NULL
                    """,
                    (_now_iso(), session_id),
                )

    def add_fill(self, fill: ShadowFill) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO shadow_fill(
                        session_id, occurred_at, symbol, side, quantity,
                        price, commission, reason, provider, coverage,
                        realized_pnl
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fill.session_id,
                        fill.occurred_at,
                        fill.symbol,
                        fill.side,
                        fill.quantity,
                        str(fill.price),
                        str(fill.commission),
                        fill.reason,
                        fill.provider,
                        fill.coverage,
                        (
                            str(fill.realized_pnl)
                            if fill.realized_pnl is not None
                            else None
                        ),
                    ),
                )

    def recent_fills(self, limit: int = 200) -> tuple[ShadowFill, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT session_id, occurred_at, symbol, side, quantity,
                       price, commission, reason, provider, coverage,
                       realized_pnl
                FROM shadow_fill
                ORDER BY fill_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(
            ShadowFill(
                session_id=row[0],
                occurred_at=row[1],
                symbol=row[2],
                side=row[3],
                quantity=int(row[4]),
                price=Decimal(row[5]),
                commission=Decimal(row[6]),
                reason=row[7],
                provider=row[8],
                coverage=row[9],
                realized_pnl=(
                    Decimal(row[10]) if row[10] is not None else None
                ),
            )
            for row in rows
        )

    def session_provenance(
        self, session_id: str
    ) -> ShadowSessionProvenance:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT session_id, strategy_version_id, parameter_hash,
                       target_symbol, allowed_symbols
                FROM shadow_session
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return ShadowSessionProvenance(
            session_id=row[0],
            strategy_version_id=row[1],
            parameter_hash=row[2],
            target_symbol=row[3],
            allowed_symbols=tuple(
                symbol for symbol in row[4].split(",") if symbol
            ),
        )

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS shadow_session(
                        session_id TEXT PRIMARY KEY,
                        started_at TEXT NOT NULL,
                        stopped_at TEXT,
                        mode TEXT NOT NULL,
                        initial_cash TEXT NOT NULL,
                        capital_source TEXT NOT NULL,
                        allowed_symbols TEXT NOT NULL,
                        strategy_version_id TEXT NOT NULL
                            DEFAULT 'legacy_unversioned',
                        parameter_hash TEXT NOT NULL
                            DEFAULT 'legacy_unverified',
                        target_symbol TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(shadow_session)"
                    ).fetchall()
                }
                if "capital_source" not in columns:
                    connection.execute(
                        """
                        ALTER TABLE shadow_session
                        ADD COLUMN capital_source TEXT NOT NULL
                        DEFAULT 'legacy_unspecified'
                        """
                    )
                for name, default in (
                    ("strategy_version_id", "legacy_unversioned"),
                    ("parameter_hash", "legacy_unverified"),
                    ("target_symbol", ""),
                ):
                    if name not in columns:
                        connection.execute(
                            f"""
                            ALTER TABLE shadow_session
                            ADD COLUMN {name} TEXT NOT NULL
                            DEFAULT '{default}'
                            """
                        )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS shadow_fill(
                        fill_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        quantity INTEGER NOT NULL,
                        price TEXT NOT NULL,
                        commission TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        coverage TEXT NOT NULL,
                        realized_pnl TEXT,
                        FOREIGN KEY(session_id)
                            REFERENCES shadow_session(session_id)
                    )
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path)


class ShadowPaperEngine:
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
        config: ShadowConfig,
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
        stream: StreamSnapshot,
        *,
        observed_at: datetime | None = None,
    ) -> ShadowSnapshot:
        now = _utc(observed_at or _parse_iso(stream.observed_at))
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

    def _evaluate_entry(
        self,
        now: datetime,
        ready: dict[str, StreamQuote],
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
            tuple[Decimal, str, StreamQuote, int, Decimal]
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
        quote: StreamQuote,
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
            provider=quote.provider,
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
            provider=quote.provider,
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
        quote: StreamQuote | None,
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
        quote: StreamQuote | None,
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
                quote.provider if quote is not None else position.provider
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
    return _utc(parsed)


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
