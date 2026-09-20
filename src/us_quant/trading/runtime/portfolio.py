"""The session's own book: what it holds, what is in flight, what filled.

One object owns the four things that must agree with each other -- estimated
cash, estimated realised PnL, the position ledger and the pending orders -- plus
the execution facts that produced them.  Keeping them together is what makes
"a fill updates the position, the cash and the trade count, or none of them"
an invariant instead of a sequence a caller has to remember.

It also projects itself into the risk domain.  That projection is the caller's
job by design: ``RiskApplication`` never imports ``AutoQuantPosition``, so
whatever holds positions converts them, and the risk layer stays independent of
how a position came to exist.

Deliberately absent: any decision.  This file never decides that a position
should be sold, whether a symbol may be bought, or whether an order may be
sent.  ``apply_fill`` reports a refusal that the session turns into a halt; it
does not halt the session itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from us_quant.trading.domain.account import Position, RiskAccountSnapshot
from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
    OrderStatus,
    Side,
)
from us_quant.trading.runtime.artifacts import (
    AutoQuantFill,
    AutoQuantPosition,
)


@dataclass(frozen=True, slots=True)
class FillResult:
    """What one execution did to the book.

    ``applied`` is false for a duplicate execution id and for a fill the book
    has no intent for; both are ignored rather than refused, because neither is
    evidence of a disagreement.  ``halt`` is the opposite: the broker's report
    contradicts the book or the order's own reconciliation, which only a human
    can resolve, and ``status`` is the text that says which.
    """

    applied: bool = False
    closed: bool = False
    halt: bool = False
    status: str = ""


@dataclass(frozen=True, slots=True)
class OrderEventResult:
    """What one broker order-status report did to the session."""

    known: bool = True
    halt: bool = False
    status: str = ""


class SessionBook:
    def __init__(
        self, *, initial_cash: Decimal, commission: Decimal
    ) -> None:
        self.initial_cash = initial_cash
        self.commission = commission
        self.cash = initial_cash
        self.realized_pnl = Decimal("0")
        self.peak_equity = Decimal("0")
        self.positions: dict[str, AutoQuantPosition] = {}
        self.marks: dict[str, Decimal] = {}
        self.pending: dict[str, OrderIntent] = {}
        self.fills: list[AutoQuantFill] = []
        self._intents: dict[str, OrderIntent] = {}
        self._seen_executions: set[str] = set()
        self._executed_quantities: dict[str, Decimal] = {}
        self._terminal_updates: dict[str, OrderEvent] = {}

    def reset(self) -> None:
        """A new session starts empty, with the configured capital."""

        self.cash = self.initial_cash
        self.realized_pnl = Decimal("0")
        self.peak_equity = self.initial_cash
        self.positions.clear()
        self.marks.clear()
        self.pending.clear()
        self.fills.clear()
        self._intents.clear()
        self._seen_executions.clear()
        self._executed_quantities.clear()
        self._terminal_updates.clear()

    # -- orders ----------------------------------------------------------

    def intent(self, order_id: str) -> OrderIntent | None:
        return self._intents.get(order_id)

    def add(self, intent: OrderIntent) -> None:
        """Record a submitted order: known for fills, in flight for the session."""

        self.pending[intent.order_id] = intent
        self._intents[intent.order_id] = intent

    def rehang(self, intent: OrderIntent, replacement: OrderIntent) -> None:
        """Move the active pending state to a fresh identity.

        The original stays in the intent history -- it is part of the audit
        trail -- while the in-flight slot follows the new identity, which is
        what the broker will report against.
        """

        self.pending.pop(intent.order_id, None)
        self.pending[replacement.order_id] = replacement
        self._intents[replacement.order_id] = replacement

    def pending_sell_symbols(self) -> set[str]:
        return {
            intent.execution_symbol
            for intent in self.pending.values()
            if intent.side is Side.SELL
        }

    def all_intents(self) -> tuple[OrderIntent, ...]:
        return tuple(self._intents.values())

    def pending_intents(self) -> tuple[OrderIntent, ...]:
        return tuple(self.pending.values())

    def note_terminal(self, event: OrderEvent) -> None:
        self._terminal_updates[event.order_id] = event

    def finalize_if_reconciled(self, order_id: str) -> str | None:
        """Clear a pending order once the broker's two reports agree.

        A terminal status and the fills are separate messages that can arrive
        in either order, so removal waits for the executed total to match the
        broker's own filled quantity.  A ``FILLED`` that does not match the
        order's whole-share quantity is not reconciled at all: it is a
        disagreement, and the caller halts on it.
        """

        event = self._terminal_updates.get(order_id)
        intent = self.pending.get(order_id)
        if event is None or intent is None:
            return None
        executed = self._executed_quantities.get(order_id, Decimal("0"))
        if executed != event.filled:
            return None
        if (
            event.status is OrderStatus.FILLED
            and executed != Decimal(intent.quantity)
        ):
            return (
                "Filled 状态与订单数量不一致；"
                "会话已停机等待人工对账"
            )
        self.pending.pop(order_id, None)
        return None

    # -- marks and valuation ---------------------------------------------

    def mark(self, symbol: str, price: Decimal) -> None:
        self.marks[symbol] = price

    def update_high_water(self) -> None:
        """Lift each holding's high-water mark to the latest mark."""

        for position in list(self.positions.values()):
            price = self.marks.get(position.symbol)
            if price is not None and price > position.high_water:
                self.positions[position.symbol] = AutoQuantPosition(
                    symbol=position.symbol,
                    quantity=position.quantity,
                    average_price=position.average_price,
                    opened_at=position.opened_at,
                    high_water=price,
                    provider=position.provider,
                )

    def unrealized_pnl(self) -> Decimal:
        total = Decimal("0")
        for position in self.positions.values():
            mark = self.marks.get(
                position.symbol, position.average_price
            )
            total += (mark - position.average_price) * position.quantity
        return total

    def equity(self) -> Decimal:
        """Estimated equity at last marks: estimated cash plus position value."""

        value = Decimal("0")
        for position in self.positions.values():
            mark = self.marks.get(
                position.symbol, position.average_price
            )
            value += mark * position.quantity
        return self.cash + value

    def update_peak_equity(self) -> None:
        equity = self.equity()
        if equity > self.peak_equity:
            self.peak_equity = equity

    # -- projection into the risk domain ---------------------------------

    def account_snapshot(
        self, *, now: datetime
    ) -> RiskAccountSnapshot:
        """The account as this session estimates it, in risk-domain form.

        ``day_start_equity`` is the session's starting capital and
        ``high_watermark`` the peak equity seen -- exactly what the two account
        halts compare against.  Cash and net liquidation are floored at zero
        because a negative balance cannot be represented and because a negative
        estimate means there is nothing left to buy with; the risk layer then
        refuses purchases, which is the fail-closed direction.

        This is the session's own book, not broker truth.  Reconciling it
        against the Paper account is the workflow's job.
        """

        return RiskAccountSnapshot(
            net_liquidation=max(Decimal("0"), self.equity()),
            cash=max(Decimal("0"), self.cash),
            day_start_equity=self.initial_cash,
            high_watermark=self.peak_equity,
            timestamp=now,
        )

    def risk_positions(self, exposure_multiplier) -> dict[str, Position]:
        """The session's holdings projected into the risk domain.

        The exposure multiplier is read from the risk application, which owns
        that policy, so this record and the calculation it feeds cannot
        disagree about how a holding is weighted.
        """

        return {
            symbol: Position(
                symbol=symbol,
                quantity=position.quantity,
                average_price=position.average_price,
                exposure_multiplier=exposure_multiplier(symbol),
            )
            for symbol, position in self.positions.items()
        }

    def risk_market_prices(self) -> dict[str, Decimal]:
        """Marks for the held symbols, falling back to average price.

        The fallback is inherited sizing semantics rather than a new decision:
        the session values a holding at its mark when one exists and at its
        average price otherwise.  Zero would be wrong -- it would report a
        holding as having no exposure at all -- and omitting the symbol would
        make the risk layer refuse every purchase for as long as a quote is
        stale, which is a behaviour change this migration does not intend.
        """

        return {
            symbol: self.marks.get(symbol, position.average_price)
            for symbol, position in self.positions.items()
        }

    # -- fills -----------------------------------------------------------

    def apply_fill(
        self, execution: ExecutionFill, *, intent: OrderIntent | None
    ) -> FillResult:
        """Fold one execution into the book, or refuse to.

        Every refusal here is a disagreement between the broker's report and
        this book -- a fractional share, another symbol, the other side, more
        than the order asked for, more than is held.  None of them is
        recoverable by retrying, so each comes back as a halt for the session.
        """

        if execution.execution_id in self._seen_executions:
            return FillResult()
        if intent is None:
            return FillResult()
        quantity = int(execution.quantity)
        if Decimal(quantity) != execution.quantity or quantity <= 0:
            return FillResult(
                halt=True,
                status="收到非整股成交回报；会话已停机等待人工核对",
            )
        if (
            execution.symbol != intent.execution_symbol
            or execution.side is not intent.side
        ):
            return FillResult(
                halt=True,
                status="成交方向或代码与订单意图不一致；会话已停机",
            )
        executed_total = (
            self._executed_quantities.get(
                execution.order_id, Decimal("0")
            )
            + execution.quantity
        )
        if executed_total > Decimal(intent.quantity):
            return FillResult(
                halt=True,
                status="累计成交超过订单整股数量；会话已停机",
            )
        self._seen_executions.add(execution.execution_id)
        self._executed_quantities[
            execution.order_id
        ] = executed_total
        if execution.side is Side.BUY:
            self._apply_buy(execution, quantity)
            realized: Decimal | None = None
            closed = False
        else:
            position = self.positions.get(execution.symbol)
            if position is None or quantity > position.quantity:
                return FillResult(
                    halt=True, status="卖出成交与本地持仓不一致；会话停机"
                )
            closed, realized = self._apply_sell(
                execution, quantity, position
            )
        self.fills.append(
            AutoQuantFill(
                execution_id=execution.execution_id,
                intent_id=execution.order_id,
                occurred_at=execution.occurred_at.isoformat(),
                symbol=execution.symbol,
                side=execution.side.order_text,
                quantity=quantity,
                price=execution.price,
                estimated_commission=self.commission,
                realized_pnl=realized,
            )
        )
        refusal = self.finalize_if_reconciled(execution.order_id)
        if refusal is not None:
            return FillResult(applied=True, closed=closed, halt=True, status=refusal)
        return FillResult(
            applied=True,
            closed=closed,
            status=(
                f"IBKR Paper 已成交 {execution.side.order_text} "
                f"{execution.symbol} {quantity} 股"
            ),
        )

    def _apply_buy(
        self, execution: ExecutionFill, quantity: int
    ) -> None:
        existing = self.positions.get(execution.symbol)
        previous_quantity = existing.quantity if existing is not None else 0
        previous_cost = (
            existing.average_price * previous_quantity
            if existing is not None
            else Decimal("0")
        )
        total_quantity = previous_quantity + quantity
        self.positions[execution.symbol] = AutoQuantPosition(
            symbol=execution.symbol,
            quantity=total_quantity,
            average_price=(
                previous_cost + execution.price * quantity
            ) / total_quantity,
            opened_at=(
                existing.opened_at
                if existing is not None
                else execution.occurred_at.isoformat()
            ),
            high_water=max(
                execution.price,
                (
                    existing.high_water
                    if existing is not None
                    else execution.price
                ),
            ),
            provider=(
                existing.provider
                if existing is not None
                else "IBKR Paper execution"
            ),
        )
        self.cash -= execution.price * quantity + self.commission

    def _apply_sell(
        self,
        execution: ExecutionFill,
        quantity: int,
        position: AutoQuantPosition,
    ) -> tuple[bool, Decimal]:
        realized = (
            execution.price - position.average_price
        ) * quantity - self.commission
        self.realized_pnl += realized
        self.cash += execution.price * quantity - self.commission
        remaining = position.quantity - quantity
        if remaining > 0:
            self.positions[execution.symbol] = AutoQuantPosition(
                symbol=position.symbol,
                quantity=remaining,
                average_price=position.average_price,
                opened_at=position.opened_at,
                high_water=position.high_water,
                provider=position.provider,
            )
        else:
            self.positions.pop(execution.symbol, None)
        return remaining == 0, realized

    def apply_order_event(
        self, event: OrderEvent, *, stopping: bool
    ) -> OrderEventResult:
        """Fold one broker order-status report into the pending book.

        A terminal report is remembered but does not clear the order on its
        own: removal waits for the executed total to agree with the broker's
        filled quantity, because a status and its fills arrive in either order.
        A ``FILLED`` whose quantity never agrees is a disagreement, and it comes
        back as a halt rather than being resolved here.
        """

        intent = self.pending.get(event.order_id)
        if intent is None:
            return OrderEventResult(known=False)
        status_text = event.broker_status or event.status.value
        terminal = event.status.is_terminal
        if terminal:
            self._terminal_updates[event.order_id] = event
            refusal = self.finalize_if_reconciled(event.order_id)
            if refusal is not None:
                return OrderEventResult(halt=True, status=refusal)
        if event.status in {
            OrderStatus.CANCELED,
            OrderStatus.INACTIVE,
            OrderStatus.BROKER_REJECTED,
        }:
            if stopping and event.status is OrderStatus.CANCELED:
                if not self.positions and not self.pending:
                    return OrderEventResult(
                        halt=True,
                        status="已停止；在途买入单已撤销并完成对账",
                    )
                return OrderEventResult(
                    status=(
                        f"{intent.execution_symbol} "
                        f"{intent.side.order_text} 在途单已撤销；"
                        "继续处理已成交持仓并完成停止"
                    )
                )
            return OrderEventResult(
                halt=True,
                status=(
                    f"{intent.execution_symbol} "
                    f"{intent.side.order_text} 被 IBKR Paper "
                    f"{status_text}；会话已停机等待对账："
                    f"{event.message or '无附加消息'}"
                ),
            )
        if terminal and event.order_id in self.pending:
            return OrderEventResult(
                status=(
                    f"{intent.execution_symbol} "
                    f"{intent.side.order_text} 状态为 "
                    f"{status_text}，等待逐笔成交回报对账"
                )
            )
        return OrderEventResult()