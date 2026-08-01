from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, time, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Callable, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

from us_quant.ibkr_paper_orders import (
    IBKRPaperOrderUncertainError,
    PaperExecution,
    PaperOrderIntent,
    PaperOrderUpdate,
    new_paper_order_intent,
)
from us_quant.ibkr_stream import StreamQuote, StreamSnapshot
from us_quant.risk import (
    LayeredRiskLimits,
    SessionRiskOverrides,
    SymbolRiskOverrides,
    resolve_session_risk_overrides,
    resolve_symbol_risk_overrides,
)
from us_quant.shadow_paper import ShadowConfig


NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class AutoQuantCandidate:
    symbol: str
    name: str
    sector: str
    leader_tier: int
    scan_score: Decimal
    signal: str


@dataclass(frozen=True, slots=True)
class AutoQuantPosition:
    symbol: str
    quantity: int
    average_price: Decimal
    opened_at: str
    high_water: Decimal
    provider: str


@dataclass(frozen=True, slots=True)
class AutoQuantFill:
    execution_id: str
    intent_id: str
    occurred_at: str
    symbol: str
    side: str
    quantity: int
    price: Decimal
    estimated_commission: Decimal
    realized_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class AutoQuantSnapshot:
    session_id: str | None
    active: bool
    strategy_version_id: str
    parameter_hash: str
    candidate_count: int
    initial_equity: Decimal
    estimated_cash: Decimal
    estimated_equity: Decimal
    estimated_realized_pnl: Decimal
    estimated_unrealized_pnl: Decimal
    positions: tuple[AutoQuantPosition, ...]
    fills: tuple[AutoQuantFill, ...]
    intents: tuple[PaperOrderIntent, ...]
    pending_orders: tuple[PaperOrderIntent, ...]
    trades_today: int
    trading_day: str | None
    status: str
    observed_at: str
    entries_paused: bool = False
    stop_requested: bool = False
    broker_truth_required: bool = True


@dataclass(frozen=True, slots=True)
class AutoQuantPreflightCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class AutoQuantPreflight:
    ready: bool
    checks: tuple[AutoQuantPreflightCheck, ...]

    @property
    def passed_count(self) -> int:
        return sum(row.passed for row in self.checks)


def evaluate_auto_quant_preflight(
    *,
    capability_enabled: bool,
    paper_confirmed: bool,
    strategy_eligible: bool,
    strategy_detail: str,
    candidate_count: int,
    realtime_ready_count: int,
    paper_capital: Decimal | None,
    recent_ready_count: int | None = None,
) -> AutoQuantPreflight:
    required_quotes = min(3, candidate_count)
    checks = (
        AutoQuantPreflightCheck(
            "Paper 能力",
            capability_enabled,
            "已开启" if capability_enabled else "请在系统设置开启",
        ),
        AutoQuantPreflightCheck(
            "策略版本",
            strategy_eligible,
            strategy_detail,
        ),
        AutoQuantPreflightCheck(
            "动态候选",
            candidate_count >= 3,
            f"{candidate_count} 个；至少需要 3 个",
        ),
        AutoQuantPreflightCheck(
            "实时行情",
            candidate_count >= 3
            and realtime_ready_count >= required_quotes,
            (
                f"当前 fresh {realtime_ready_count}/{candidate_count}；"
                + (
                    f"近30秒覆盖 {recent_ready_count}/{candidate_count}；"
                    if recent_ready_count is not None
                    else ""
                )
                + f"启动至少需要当前 fresh {required_quotes}"
            ),
        ),
        AutoQuantPreflightCheck(
            "Paper 资金",
            paper_capital is not None and paper_capital > 0,
            (
                f"新鲜净值 {paper_capital:,.2f}"
                if paper_capital is not None
                else "请刷新 IBKR Paper 账户"
            ),
        ),
        AutoQuantPreflightCheck(
            "本次确认",
            paper_confirmed,
            "仅 DU 模拟账户"
            if paper_confirmed
            else "尚未勾选会话确认",
        ),
    )
    return AutoQuantPreflight(
        ready=all(row.passed for row in checks),
        checks=checks,
    )


class AutoQuantEngine:
    """Multi-symbol signal engine that emits IBKR Paper limit intents.

    It never calls IBKR directly. The injected sink is the only execution
    boundary, which keeps signal generation deterministic and testable.
    Current research mode holds at most one position while scanning all
    candidates for the strongest eligible intraday momentum.
    """

    def __init__(
        self,
        *,
        candidates: tuple[AutoQuantCandidate, ...],
        config: ShadowConfig,
        strategy_version_id: str,
        parameter_hash: str,
        order_sink: Callable[[PaperOrderIntent], int],
        symbol_risk_multipliers: Mapping[str, Decimal] | None = None,
        layered_risk_limits: LayeredRiskLimits | None = None,
    ) -> None:
        if not candidates:
            raise ValueError("自动量化候选集不能为空")
        symbols = [row.symbol.strip().upper() for row in candidates]
        if len(set(symbols)) != len(symbols):
            raise ValueError("自动量化候选代码不能重复")
        if config.initial_cash <= 0:
            raise ValueError("自动量化初始净值必须为正")
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
        self.config = config
        self.strategy_version_id = strategy_version_id
        self.parameter_hash = parameter_hash
        self.order_sink = order_sink
        self.risk_multipliers = dict(
            symbol_risk_multipliers or {}
        )
        self._layered_risk_limits = layered_risk_limits
        self._session_risk_overrides: SessionRiskOverrides | None = None
        self._symbol_risk_overrides: dict[str, SymbolRiskOverrides] = {}
        self.session_id: str | None = None
        self.active = False
        self.positions: dict[str, AutoQuantPosition] = {}
        self.pending: dict[str, PaperOrderIntent] = {}
        self._intents: dict[str, PaperOrderIntent] = {}
        self.fills: list[AutoQuantFill] = []
        self.trades_today = 0
        self._trading_day = None
        self._histories = {
            row.symbol: deque(
                maxlen=max(60, config.warmup_minutes + 5)
            )
            for row in self.candidates
        }
        self._scores = {
            row.symbol: row.scan_score for row in self.candidates
        }
        self._marks: dict[str, Decimal] = {}
        self._seen_executions: set[str] = set()
        self._executed_quantities: dict[str, Decimal] = {}
        self._terminal_updates: dict[str, PaperOrderUpdate] = {}
        self._last_evaluation_minute: datetime | None = None
        self._stop_requested = False
        self._entries_paused = False
        self.estimated_cash = config.initial_cash
        self.estimated_realized_pnl = Decimal("0")
        self.status = "未启动"

    @staticmethod
    def _current_time_utc() -> datetime:
        return datetime.now(timezone.utc)

    def _session_overrides(self) -> SessionRiskOverrides:
        if self._session_risk_overrides is None:
            self._session_risk_overrides = (
                resolve_session_risk_overrides(
                    self._layered_risk_limits
                )
                if self._layered_risk_limits is not None
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
                symbol, self._layered_risk_limits
            )
            if self._layered_risk_limits is not None
            else SymbolRiskOverrides()
        )
        self._symbol_risk_overrides[symbol] = overrides
        return overrides

    def start(self) -> AutoQuantSnapshot:
        if self.active:
            return self.snapshot()
        self.session_id = uuid4().hex
        self.active = True
        self.positions.clear()
        self.pending.clear()
        self._intents.clear()
        self.fills.clear()
        self.trades_today = 0
        self._trading_day = None
        self._marks.clear()
        self._seen_executions.clear()
        self._executed_quantities.clear()
        self._terminal_updates.clear()
        self._last_evaluation_minute = None
        self._session_risk_overrides = None
        self._symbol_risk_overrides.clear()
        self._stop_requested = False
        self._entries_paused = False
        self.estimated_cash = self.config.initial_cash
        self.estimated_realized_pnl = Decimal("0")
        for history in self._histories.values():
            history.clear()
        self.status = (
            f"已武装；扫描 {len(self.candidates)} 个候选，"
            f"等待 {self.config.warmup_minutes} 个连续分钟"
        )
        return self.snapshot()

    def on_stream(
        self,
        snapshot: StreamSnapshot,
        *,
        observed_at: datetime | None = None,
    ) -> AutoQuantSnapshot:
        if not self.active:
            return self.snapshot(observed_at=observed_at)
        now = _utc(observed_at)
        self._roll_trading_day(now)
        ready = {
            quote.symbol: quote
            for quote in snapshot.quotes
            if (
                quote.symbol in self._histories
                and quote.realtime_ready
            )
        }
        for symbol, quote in ready.items():
            assert quote.bid is not None and quote.ask is not None
            mark = (quote.bid + quote.ask) / Decimal("2")
            self._marks[symbol] = mark
            self._update_minute(symbol, now, mark)
        for position in list(self.positions.values()):
            mark = self._marks.get(position.symbol)
            if mark is not None and mark > position.high_water:
                self.positions[position.symbol] = (
                    AutoQuantPosition(
                        symbol=position.symbol,
                        quantity=position.quantity,
                        average_price=position.average_price,
                        opened_at=position.opened_at,
                        high_water=mark,
                        provider=position.provider,
                    )
                )

        eastern_time = now.astimezone(NEW_YORK).time().replace(
            tzinfo=None
        )
        if self._stop_requested:
            if not self.positions and not self.pending:
                self.active = False
                self.status = "已停止；以 IBKR Paper 回报完成对账"
            else:
                for position in list(self.positions.values()):
                    quote = ready.get(position.symbol)
                    if quote is not None and quote.bid is not None:
                        self._emit_exit(
                            now,
                            quote,
                            position.symbol,
                            quote.bid,
                            "用户停止；提交 Paper 限价平仓",
                        )
            return self.snapshot(observed_at=now)
        if self.positions and not self.pending:
            if eastern_time >= self.config.force_flat:
                for position in list(self.positions.values()):
                    quote = ready.get(position.symbol)
                    if quote is not None and quote.bid is not None:
                        self._emit_exit(
                            now,
                            quote,
                            position.symbol,
                            quote.bid,
                            "收盘前提交 Paper 限价平仓",
                        )
            else:
                for position in list(self.positions.values()):
                    self._check_exit(
                        now, ready.get(position.symbol)
                    )
        minute = now.replace(second=0, microsecond=0)
        if (
            len(self.positions) < self.config.max_open_symbols
            and not self.pending
            and self._last_evaluation_minute != minute
        ):
            self._last_evaluation_minute = minute
            if self._entries_paused:
                self.status = (
                    "已暂停新开仓；现有持仓的止损、止盈和时段平仓仍运行"
                )
            else:
                self._evaluate_entry(now, ready)
        if not ready and not self._entries_paused:
            self.status = "等待 fresh 实时 bid/ask"
        return self.snapshot(observed_at=now)

    def on_order_update(
        self, update: PaperOrderUpdate
    ) -> AutoQuantSnapshot:
        intent = self.pending.get(update.intent_id)
        if intent is None:
            return self.snapshot()
        normalized_status = update.status.casefold()
        terminal = normalized_status in {
            "cancelled",
            "apicancelled",
            "inactive",
            "error",
            "filled",
        }
        if terminal:
            self._terminal_updates[update.intent_id] = update
            self._finalize_pending_if_reconciled(update.intent_id)
        if normalized_status in {
            "cancelled",
            "apicancelled",
            "inactive",
            "error",
        }:
            if (
                self._stop_requested
                and normalized_status
                in {"cancelled", "apicancelled"}
            ):
                self.status = (
                    f"{intent.symbol} {intent.side} 在途单已撤销；"
                    "继续处理已成交持仓并完成停止"
                )
                if not self.positions and not self.pending:
                    self.active = False
                    self.status = "已停止；在途买入单已撤销并完成对账"
            else:
                self.active = False
                self.status = (
                    f"{intent.symbol} {intent.side} 被 IBKR Paper "
                    f"{update.status}；会话已停机等待对账："
                    f"{update.message or '无附加消息'}"
                )
        elif terminal and update.intent_id in self.pending:
            self.status = (
                f"{intent.symbol} {intent.side} 状态为 "
                f"{update.status}，等待逐笔成交回报对账"
            )
        return self.snapshot()

    def on_execution(
        self, execution: PaperExecution
    ) -> AutoQuantSnapshot:
        if execution.execution_id in self._seen_executions:
            return self.snapshot()
        self._seen_executions.add(execution.execution_id)
        intent = self._intents.get(execution.intent_id)
        if intent is None:
            return self.snapshot()
        quantity = int(execution.quantity)
        if Decimal(quantity) != execution.quantity or quantity <= 0:
            self.status = "收到非整股成交回报；会话已停机等待人工核对"
            self.active = False
            return self.snapshot()
        if (
            execution.symbol != intent.symbol
            or execution.side != intent.side
        ):
            self.status = "成交方向或代码与订单意图不一致；会话已停机"
            self.active = False
            return self.snapshot()
        executed_total = (
            self._executed_quantities.get(
                execution.intent_id, Decimal("0")
            )
            + execution.quantity
        )
        if executed_total > Decimal(intent.quantity):
            self.status = "累计成交超过订单整股数量；会话已停机"
            self.active = False
            return self.snapshot()
        self._executed_quantities[
            execution.intent_id
        ] = executed_total
        commission = self.config.commission_per_order
        realized: Decimal | None = None
        if execution.side == "BUY":
            existing = self.positions.get(execution.symbol)
            previous_quantity = (
                existing.quantity if existing is not None else 0
            )
            previous_cost = (
                existing.average_price * previous_quantity
                if existing is not None
                else Decimal("0")
            )
            total_quantity = previous_quantity + quantity
            average_price = (
                previous_cost + execution.price * quantity
            ) / total_quantity
            provider = (
                existing.provider
                if existing is not None
                else "IBKR Paper execution"
            )
            self.positions[execution.symbol] = AutoQuantPosition(
                symbol=execution.symbol,
                quantity=total_quantity,
                average_price=average_price,
                opened_at=(
                    existing.opened_at
                    if existing is not None
                    else execution.occurred_at
                ),
                high_water=max(
                    execution.price,
                    (
                        existing.high_water
                        if existing is not None
                        else execution.price
                    ),
                ),
                provider=provider,
            )
            self.estimated_cash -= (
                execution.price * quantity + commission
            )
            self.status = (
                f"IBKR Paper 已成交 BUY {execution.symbol} "
                f"{quantity} 股"
            )
        elif execution.side == "SELL":
            position = self.positions.get(execution.symbol)
            if (
                position is None
                or quantity > position.quantity
            ):
                self.status = "卖出成交与本地持仓不一致；会话停机"
                self.active = False
                return self.snapshot()
            realized = (
                execution.price - position.average_price
            ) * quantity - commission
            self.estimated_realized_pnl += realized
            self.estimated_cash += (
                execution.price * quantity - commission
            )
            remaining = position.quantity - quantity
            if remaining > 0:
                self.positions[execution.symbol] = (
                    AutoQuantPosition(
                        symbol=position.symbol,
                        quantity=remaining,
                        average_price=position.average_price,
                        opened_at=position.opened_at,
                        high_water=position.high_water,
                        provider=position.provider,
                    )
                )
            else:
                self.positions.pop(execution.symbol, None)
            if remaining == 0:
                self.trades_today += 1
            self.status = (
                f"IBKR Paper 已成交 SELL {execution.symbol} "
                f"{quantity} 股"
            )
        self.fills.append(
            AutoQuantFill(
                execution_id=execution.execution_id,
                intent_id=execution.intent_id,
                occurred_at=execution.occurred_at,
                symbol=execution.symbol,
                side=execution.side,
                quantity=quantity,
                price=execution.price,
                estimated_commission=commission,
                realized_pnl=realized,
            )
        )
        self._finalize_pending_if_reconciled(execution.intent_id)
        return self.snapshot()

    def request_stop(self) -> AutoQuantSnapshot:
        if not self.active:
            return self.snapshot()
        self._stop_requested = True
        self._entries_paused = True
        self.status = "停止请求已登记；先处理在途单和 Paper 持仓"
        return self.snapshot()

    def pause_entries(self) -> AutoQuantSnapshot:
        if not self.active or self._stop_requested:
            return self.snapshot()
        self._entries_paused = True
        self.status = (
            "已暂停新开仓；现有持仓的风控和平仓逻辑继续运行"
        )
        return self.snapshot()

    def resume_entries(self) -> AutoQuantSnapshot:
        if not self.active or self._stop_requested:
            return self.snapshot()
        self._entries_paused = False
        self.status = "已恢复新开仓；继续等待 fresh 行情和策略信号"
        return self.snapshot()

    def halt_for_reconciliation(self, reason: str) -> AutoQuantSnapshot:
        self.active = False
        self._stop_requested = True
        self._entries_paused = True
        self.status = f"{reason}；已停机，禁止新订单并等待人工对账"
        return self.snapshot()

    def resume_from_reconciliation(
        self,
        *,
        session_id: str,
        allow_force_flat_exit: bool = True,
    ) -> AutoQuantSnapshot:
        if self.session_id != session_id:
            raise ValueError("会话 ID 不匹配，不允许恢复其他会话")
        if self.active:
            return self.snapshot()
        self._stop_requested = False
        self._entries_paused = False
        self.active = True
        if allow_force_flat_exit and self.positions:
            self.status = (
                "已恢复对账后运行；保留现有持仓，"
                "继续执行止盈止损和平仓逻辑"
            )
        else:
            self.status = "已恢复对账后运行；继续扫描新开仓机会"
        return self.snapshot()

    def resubmit_pending_intent(
        self,
        intent: PaperOrderIntent,
    ) -> PaperOrderIntent | None:
        if intent.intent_id not in self.pending:
            return None
        new_intent = new_paper_order_intent(
            session_id=self.session_id or intent.session_id,
            strategy_version_id=self.strategy_version_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            limit_price=intent.limit_price,
            reason="对账恢复后人工复核重挂；" + intent.reason,
        )
        # A manual re-hang is a distinct broker order after reconciliation.
        # Keep the original intent in the audit trail, but move the active
        # pending state to the new intent ID and its fresh idempotency key.
        self.pending.pop(intent.intent_id, None)
        self.pending[new_intent.intent_id] = new_intent
        self._intents[new_intent.intent_id] = new_intent
        return new_intent

    def snapshot(
        self, *, observed_at: datetime | None = None
    ) -> AutoQuantSnapshot:
        unrealized = Decimal("0")
        position_value = Decimal("0")
        positions_list = list(self.positions.values())
        for position in positions_list:
            mark = self._marks.get(
                position.symbol,
                position.average_price,
            )
            position_value += mark * position.quantity
            unrealized += (
                mark - position.average_price
            ) * position.quantity
        return AutoQuantSnapshot(
            session_id=self.session_id,
            active=self.active,
            strategy_version_id=self.strategy_version_id,
            parameter_hash=self.parameter_hash,
            candidate_count=len(self.candidates),
            initial_equity=self.config.initial_cash,
            estimated_cash=self.estimated_cash,
            estimated_equity=self.estimated_cash + position_value,
            estimated_realized_pnl=self.estimated_realized_pnl,
            estimated_unrealized_pnl=unrealized,
            positions=tuple(positions_list),
            fills=tuple(self.fills),
            intents=tuple(self._intents.values()),
            pending_orders=tuple(self.pending.values()),
            trades_today=self.trades_today,
            trading_day=(
                self._trading_day.isoformat()
                if self._trading_day is not None
                else None
            ),
            status=self.status,
            observed_at=_utc(observed_at).isoformat(),
            entries_paused=self._entries_paused,
            stop_requested=self._stop_requested,
        )

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
            self.status = "等待纽约入场时段 10:00–15:30"
            return
        if self.trades_today >= maximum_trades_per_day:
            self.status = "已达到当日最大交易次数"
            return
        if self.estimated_realized_pnl <= -daily_loss_limit:
            self.status = "触发自动量化单日亏损停机线"
            return
        ranked: list[
            tuple[Decimal, Decimal, str, StreamQuote]
        ] = []
        required = max(
            self.config.warmup_minutes,
            self.config.momentum_lookback_minutes + 1,
        )
        for symbol, quote in ready.items():
            symbol_overrides = self._symbol_overrides(symbol)
            if not symbol_overrides.allowed:
                continue
            history = self._histories[symbol]
            if len(history) < required:
                continue
            assert quote.bid is not None and quote.ask is not None
            mid = (quote.bid + quote.ask) / Decimal("2")
            spread_fraction = (quote.ask - quote.bid) / mid
            if spread_fraction > self.config.maximum_spread_fraction:
                continue
            lookback = history[
                -(self.config.momentum_lookback_minutes + 1)
            ][1]
            momentum = history[-1][1] / lookback - Decimal("1")
            recent = tuple(history)[
                -(self.config.momentum_lookback_minutes + 1):
            ]
            step_returns = tuple(
                recent[index][1] / recent[index - 1][1]
                - Decimal("1")
                for index in range(1, len(recent))
            )
            if any(
                abs(value) > self.config.maximum_one_minute_move
                for value in step_returns
            ):
                continue
            positive_steps = sum(
                value > 0 for value in step_returns
            )
            if (
                positive_steps
                < self.config.minimum_positive_steps
            ):
                continue
            average = sum(
                (row[1] for row in history), Decimal("0")
            ) / len(history)
            if (
                self.config.minimum_momentum
                <= momentum
                <= self.config.maximum_momentum
                and history[-1][1] > average
            ):
                ranked.append(
                    (
                        momentum,
                        self._scores[symbol],
                        symbol,
                        quote,
                    )
                )
        if not ranked:
            warmed = sum(
                len(history) >= required
                for history in self._histories.values()
            )
            self.status = (
                f"候选预热 {warmed}/{len(self.candidates)}；"
                "暂无通过点差与动量门的信号"
            )
            return
        momentum, _, symbol, quote = max(ranked)
        assert quote.ask is not None
        symbol_overrides = self._symbol_overrides(symbol)
        multiplier = (
            symbol_overrides.exposure_multiplier
            if symbol_overrides.exposure_multiplier != Decimal("1")
            else self.risk_multipliers.get(symbol, Decimal("1"))
        )
        session_max_position_fraction = (
            session_overrides.max_position_fraction
            if session_overrides.max_position_fraction is not None
            else self.config.max_position_fraction
        )
        max_position_fraction = session_max_position_fraction
        if symbol_overrides.max_position_exposure_pct is not None:
            max_position_fraction = min(
                max_position_fraction,
                symbol_overrides.max_position_exposure_pct,
            )
        account_limits = (
            self._layered_risk_limits.account
            if self._layered_risk_limits is not None
            else None
        )
        if account_limits is not None:
            max_position_fraction = min(
                max_position_fraction,
                account_limits.max_position_exposure_pct,
            )
        notional_cap = (
            self.config.initial_cash
            * max_position_fraction
            / multiplier
        )
        if account_limits is not None:
            current_risk_exposure = sum(
                (
                    position.average_price
                    * position.quantity
                    * self._effective_risk_multiplier(position.symbol)
                    for position in self.positions.values()
                ),
                Decimal("0"),
            )
            remaining_risk_exposure = max(
                Decimal("0"),
                (
                    self.config.initial_cash
                    * account_limits.max_gross_exposure_pct
                    - current_risk_exposure
                ),
            )
            notional_cap = min(
                notional_cap,
                remaining_risk_exposure / multiplier,
            )
        if symbol in self.positions:
            existing = self.positions[symbol]
            notional_cap = max(Decimal("0"), notional_cap - existing.average_price * existing.quantity)
        limit_price = _limit_price(
            quote.ask, self.config.slippage_bps, buy=True
        )
        affordable = min(notional_cap, self.estimated_cash)
        quantity = int(
            (
                (
                    affordable
                    - self.config.commission_per_order
                )
                / limit_price
            ).to_integral_value(rounding=ROUND_DOWN)
        )
        if (
            quantity <= 0
            or quantity * limit_price
            < self.config.min_order_notional
        ):
            self.status = f"{symbol} 信号通过，但整股资金不足"
            return
        self._emit(
            new_paper_order_intent(
                session_id=self.session_id or "",
                strategy_version_id=self.strategy_version_id,
                symbol=symbol,
                side="BUY",
                quantity=quantity,
                limit_price=limit_price,
                reason=(
                    "自动轮动入场；"
                    f"{self.config.momentum_lookback_minutes}分钟动量 "
                    f"{momentum:.2%}；"
                    f"正收益步数 {positive_steps}/"
                    f"{len(step_returns)}"
                ),
            )
        )

    def _check_exit(
        self,
        now: datetime,
        quote: StreamQuote | None,
    ) -> None:
        if quote is None or quote.bid is None:
            return
        for position in (self.positions.get(quote.symbol),):
            if position is None:
                return
            mark = self._marks.get(position.symbol, quote.bid)
            price = mark if mark is not None else quote.bid
            entry = position.average_price
            opened = datetime.fromisoformat(
                position.opened_at.replace("Z", "+00:00")
            )
            minutes_held = (now - _utc(opened)).total_seconds() / 60
            reason = None
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
            if reason is not None:
                self._emit_exit(
                    now, quote, position.symbol, price, reason
                )

    def _effective_risk_multiplier(self, symbol: str) -> Decimal:
        overrides = self._symbol_overrides(symbol)
        if overrides.exposure_multiplier != Decimal("1"):
            return overrides.exposure_multiplier
        return self.risk_multipliers.get(symbol, Decimal("1"))

    def _emit_exit(
        self,
        now: datetime,
        quote: StreamQuote | None,
        symbol: str,
        price: Decimal,
        reason: str,
    ) -> None:
        del now
        if quote is None or quote.bid is None:
            self.status = (
                f"{reason}，但缺少 fresh bid；禁止生成无报价订单"
            )
            return
        self._emit(
            new_paper_order_intent(
                session_id=self.session_id or "",
                strategy_version_id=self.strategy_version_id,
                symbol=symbol,
                side="SELL",
                quantity=self.positions[symbol].quantity,
                limit_price=_limit_price(
                    price,
                    self.config.slippage_bps,
                    buy=False,
                ),
                reason=reason,
            )
        )

    def _emit(self, intent: PaperOrderIntent) -> None:
        try:
            order_id = self.order_sink(intent)
        except IBKRPaperOrderUncertainError as error:
            self.pending[intent.intent_id] = intent
            self._intents[intent.intent_id] = intent
            self.active = False
            self.status = (
                "Paper 订单提交结果不确定；已停机并保留在途意图，"
                f"等待券商对账（Order {error.broker_order_id}）"
            )
            return
        except Exception as error:
            self.active = False
            self.status = (
                f"Paper 订单提交被阻断：{error}；"
                "会话已停机，避免自动重试形成重复订单"
            )
            return
        self.pending[intent.intent_id] = intent
        self._intents[intent.intent_id] = intent
        self.status = (
            f"已提交 IBKR Paper {intent.side} {intent.symbol} "
            f"{intent.quantity} 股 @ {intent.limit_price} · "
            f"Order {order_id}"
        )

    def _finalize_pending_if_reconciled(
        self, intent_id: str
    ) -> None:
        update = self._terminal_updates.get(intent_id)
        intent = self.pending.get(intent_id)
        if update is None or intent is None:
            return
        executed = self._executed_quantities.get(
            intent_id, Decimal("0")
        )
        if executed != update.filled:
            return
        if (
            update.status.casefold() == "filled"
            and executed != Decimal(intent.quantity)
        ):
            self.active = False
            self.status = (
                "Filled 状态与订单数量不一致；"
                "会话已停机等待人工对账"
            )
            return
        self.pending.pop(intent_id, None)

    def _update_minute(
        self, symbol: str, now: datetime, price: Decimal
    ) -> None:
        minute = now.replace(second=0, microsecond=0)
        history = self._histories[symbol]
        if history and history[-1][0] == minute:
            history[-1] = (minute, price)
            return
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
        if self.positions or self.pending:
            self.active = False
            self.status = "跨交易日仍有持仓/在途单；已停机等待人工对账"
            return
        self._trading_day = trading_day
        self.trades_today = 0
        self.estimated_realized_pnl = Decimal("0")
        self._last_evaluation_minute = None
        self._session_risk_overrides = None
        for history in self._histories.values():
            history.clear()


def _limit_price(
    price: Decimal, slippage_bps: Decimal, *, buy: bool
) -> Decimal:
    adjustment = slippage_bps / Decimal("10000")
    value = price * (
        Decimal("1") + adjustment
        if buy
        else Decimal("1") - adjustment
    )
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _utc(value: datetime | None) -> datetime:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(timezone.utc)
