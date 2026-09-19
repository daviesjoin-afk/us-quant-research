"""Execution application: the only place an order identity is created.

The strategy path ends at a ``TradeProposal``; the risk layer answers with a
``RiskDecision``.  This service is what turns that pair into exactly one
``OrderIntent``, makes it durable, and only then lets the broker see it.  A
strategy cannot reach the broker because a strategy cannot construct an intent,
and this module is the only production code that calls ``OrderIntent.create``.

What it does *not* do is decide anything twice.  It re-checks the verdict's own
consistency -- approved, a positive whole-share quantity, no more than the
proposal asked for, and a decision taken against this very proposal -- and
refuses anything else.  It never recomputes a position, gross, cash or halt
ceiling, because those belong to ``RiskApplication`` and a second copy here is
how two layers come to disagree about what is affordable.

The order of operations in ``_submit`` is the safety property this change
exists to preserve: reserve the broker id, write the correlation durably, and
only then send.  If the process dies in between, the store already names the
order; if the send itself fails, the caller gets
``ExecutionSubmissionUncertain`` and halts for reconciliation rather than
retrying into a duplicate.

Dependencies: the domain and the two ports.  No adapter, no ``sqlite3``, no
``ibapi``, no Qt.
"""

from __future__ import annotations

from dataclasses import dataclass

from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
    OrderStatus,
    Side,
)
from us_quant.trading.domain.risk import RiskDecision
from us_quant.trading.domain.strategy import TradeAction, TradeProposal
from us_quant.trading.ports.broker_execution import (
    BrokerExecutionPort,
    ExecutionRefused,
    ExecutionSubmissionUncertain,
)
from us_quant.trading.ports.order_repository import OrderRepositoryPort


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    """What one accepted order looks like to the caller.

    The broker order id is carried because the runtime names it in the status a
    human reads while reconciling; it is not an input to any decision.
    """

    intent: OrderIntent
    broker_order_id: int


class ExecutionApplication:
    def __init__(
        self,
        *,
        repository: OrderRepositoryPort,
        broker: BrokerExecutionPort,
    ) -> None:
        self._repository = repository
        self._broker = broker

    # -- the write path --------------------------------------------------

    def submit_approved(
        self,
        *,
        proposal: TradeProposal,
        decision: RiskDecision,
        execution_symbol: str,
        session_id: str,
        reason: str,
    ) -> SubmissionResult:
        """Turn one risk-approved proposal into one submitted order.

        The limit price is the proposal's ``reference_price``: execution owns the
        price the order is sent with, and the strategy's reference price is the
        number risk sized against, so reusing it keeps sizing and cash
        semantics identical while leaving the order's price under this layer.
        """

        self._validate(proposal=proposal, decision=decision)
        intent = OrderIntent.create(
            session_id=session_id,
            strategy_version_id=proposal.strategy.version_id,
            signal_symbol=proposal.symbol,
            execution_symbol=execution_symbol,
            side=(
                Side.BUY
                if proposal.action is TradeAction.BUY
                else Side.SELL
            ),
            quantity=decision.approved_quantity,
            limit_price=proposal.reference_price,
            reason=reason,
        )
        return self._submit(intent)

    @staticmethod
    def _validate(
        *, proposal: TradeProposal, decision: RiskDecision
    ) -> None:
        if not decision.approved:
            raise ExecutionRefused(
                "风险层未批准该提案；执行层不得提交订单"
            )
        if decision.approved_quantity <= 0:
            raise ExecutionRefused("风险层批准的整股数量必须为正")
        if decision.approved_quantity > proposal.desired_quantity:
            raise ExecutionRefused(
                "风险层批准的数量超过提案请求的数量；拒绝提交"
            )
        if decision.requested_quantity != proposal.desired_quantity:
            raise ExecutionRefused(
                "风险裁决与提案不是同一次请求；拒绝提交"
            )

    def _submit(self, intent: OrderIntent) -> SubmissionResult:
        if self._repository.intent(intent.order_id) is not None:
            # The order identity is already durable: it has been submitted, and
            # sending it again is how one intent becomes two broker orders.
            existing = self._repository.broker_order_id(intent.order_id)
            return SubmissionResult(
                intent=intent,
                broker_order_id=existing if existing is not None else 0,
            )
        if intent.idempotency_key:
            duplicate = self._repository.intent_for_idempotency_key(
                intent.idempotency_key
            )
            if duplicate is not None and duplicate.order_id != intent.order_id:
                raise ExecutionRefused("检测到重复幂等键，已拒绝重复订单")
        reservation = self._broker.reserve(intent)
        self._repository.record_intent(
            intent,
            broker_order_id=reservation.broker_order_id,
            account_alias=reservation.account_alias,
        )
        try:
            self._broker.submit(reservation)
        except ExecutionSubmissionUncertain as error:
            # Re-raise with the order attached: the caller must be able to keep
            # the order in its pending book, because the broker may be holding
            # it.  Dropping it would leave a live order nobody is tracking.
            raise ExecutionSubmissionUncertain(
                str(error),
                order_id=intent.order_id,
                broker_order_id=error.broker_order_id,
                intent=intent,
            ) from error
        return SubmissionResult(
            intent=intent,
            broker_order_id=reservation.broker_order_id,
        )

    def cancel(self, order_id: str) -> bool:
        return bool(self._broker.cancel(order_id))

    def reissue(self, intent: OrderIntent, *, reason: str) -> OrderIntent:
        """A fresh identity for an order a human is re-hanging.

        Manual reconciliation re-hangs the *same* order after a broker
        disconnect, so the quantity, price and side are carried over unchanged
        and the verdict is deliberately not asked again -- the human already
        decided.  A new identity is issued because the broker sees a new order,
        and the old one stays in the audit trail.

        Identity is created here rather than by the caller because this service
        is the only place an order identity may come into existence; a second
        creator is a second place orders appear from.
        """

        return OrderIntent.create(
            session_id=intent.session_id,
            strategy_version_id=intent.strategy_version_id,
            signal_symbol=intent.signal_symbol,
            execution_symbol=intent.execution_symbol,
            side=intent.side,
            quantity=intent.quantity,
            limit_price=intent.limit_price,
            reason=reason,
            exposure_multiplier=intent.exposure_multiplier,
        )

    # -- the read path ---------------------------------------------------

    def status(self, order_id: str) -> OrderStatus | None:
        return self._repository.status(order_id)

    def intent(self, order_id: str) -> OrderIntent | None:
        return self._repository.intent(order_id)

    def events(self) -> tuple[OrderEvent, ...]:
        return self._broker.events()

    def fills(self) -> tuple[ExecutionFill, ...]:
        return self._broker.fills()

    def connect(self) -> None:
        self._broker.connect()

    def disconnect(self) -> None:
        self._broker.disconnect()