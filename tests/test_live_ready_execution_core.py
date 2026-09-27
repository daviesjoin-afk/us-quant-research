from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.trading.application.execution import ExecutionApplication
from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
    OrderStatus,
    Side,
)
from us_quant.trading.domain.risk import RiskDecision
from us_quant.trading.domain.strategy import (
    StrategyIdentity,
    TradeAction,
    TradeProposal,
)
from us_quant.trading.ports.broker_execution import (
    BrokerOrderReservation,
    ExecutionRefused,
    ExecutionSubmissionUncertain,
)
from us_quant.trading.runtime.config import TradingSessionConfig
from us_quant.trading.runtime.dispatch import OrderDispatch


class _Repository:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.intents: dict[str, OrderIntent] = {}
        self.broker_ids: dict[str, int] = {}

    def record_intent(self, intent, *, broker_order_id, account_alias) -> None:
        self.trace.append("durable")
        self.intents[intent.order_id] = intent
        self.broker_ids[intent.order_id] = broker_order_id

    def intent(self, order_id):
        return self.intents.get(order_id)

    def intent_for_idempotency_key(self, key):
        return next(
            (row for row in self.intents.values() if row.idempotency_key == key),
            None,
        )

    def broker_order_id(self, order_id):
        return self.broker_ids.get(order_id)

    def record_event(self, event: OrderEvent) -> None:
        return None

    def record_fill(self, fill: ExecutionFill) -> bool:
        return True

    def status(self, order_id: str) -> OrderStatus | None:
        return None

    def fills(self, order_id: str) -> tuple[ExecutionFill, ...]:
        return ()

    def executed_quantity(self, order_id: str) -> Decimal:
        return Decimal("0")

    def max_broker_order_id(self) -> int:
        return max(self.broker_ids.values(), default=0)


class RecordingBrokerExecutionPort:
    """In-memory provider double; it never connects to any broker."""

    def __init__(self, trace: list[str], *, uncertain: bool = False) -> None:
        self.trace = trace
        self.uncertain = uncertain
        self.submit_attempts = 0

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def reserve(self, intent: OrderIntent) -> BrokerOrderReservation:
        self.trace.append("reserve")
        return BrokerOrderReservation(
            order_id=intent.order_id,
            broker_order_id=701,
            account_alias="ALTERNATE-TEST",
        )

    def submit(self, reservation: BrokerOrderReservation) -> None:
        self.trace.append("submit")
        self.submit_attempts += 1
        if self.uncertain:
            raise ExecutionSubmissionUncertain(
                "test double outcome is unknown",
                order_id=reservation.order_id,
                broker_order_id=reservation.broker_order_id,
            )

    def cancel(self, order_id: str) -> bool:
        return True

    def events(self) -> tuple[OrderEvent, ...]:
        return ()

    def fills(self) -> tuple[ExecutionFill, ...]:
        return ()


def _dispatch(*, uncertain: bool = False):
    trace: list[str] = []
    repository = _Repository(trace)
    broker = RecordingBrokerExecutionPort(trace, uncertain=uncertain)
    execution = ExecutionApplication(repository=repository, broker=broker)
    dispatch = OrderDispatch(
        config=TradingSessionConfig(initial_cash=Decimal("10000"), capital_source="test"),
        risk=object(),  # submit() consumes the already-issued domain verdict.
        execution=execution,
    )
    return dispatch, repository, broker, trace


def _proposal() -> TradeProposal:
    return TradeProposal(
        strategy=StrategyIdentity("alternate", "v1", "hash"),
        symbol="AAPL",
        action=TradeAction.BUY,
        desired_quantity=2,
        reference_price=Decimal("100"),
        reason="alternate broker core proof",
        generated_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )


def test_alternate_provider_runs_through_dispatch_and_durable_execution() -> None:
    dispatch, repository, broker, trace = _dispatch()
    result = dispatch.submit(
        proposal=_proposal(),
        decision=RiskDecision.approve(requested_quantity=2),
        execution_symbol="AAPL",
        reason="test-only alternate port",
        session_id="test-session",
    )

    assert result.submitted is True
    assert trace == ["reserve", "durable", "submit"]
    assert repository.intent(result.intent.order_id) is result.intent
    assert broker.submit_attempts == 1
    assert result.intent.side is Side.BUY


def test_uncertain_alternate_submission_halts_without_retry() -> None:
    dispatch, repository, broker, trace = _dispatch(uncertain=True)
    result = dispatch.submit(
        proposal=_proposal(),
        decision=RiskDecision.approve(requested_quantity=2),
        execution_symbol="AAPL",
        reason="uncertainty proof",
        session_id="test-session",
    )

    assert result.halt is True
    assert result.intent is not None
    assert repository.intent(result.intent.order_id) is result.intent
    assert trace == ["reserve", "durable", "submit"]
    assert broker.submit_attempts == 1


def test_explicit_alternate_refusal_does_not_create_a_fallback_channel() -> None:
    class RefusingBroker(RecordingBrokerExecutionPort):
        def reserve(self, intent: OrderIntent) -> BrokerOrderReservation:
            self.trace.append("refused")
            raise ExecutionRefused("alternate test provider refused")

    trace: list[str] = []
    repository = _Repository(trace)
    broker = RefusingBroker(trace)
    dispatch = OrderDispatch(
        config=TradingSessionConfig(initial_cash=Decimal("10000"), capital_source="test"),
        risk=object(),
        execution=ExecutionApplication(repository=repository, broker=broker),
    )
    result = dispatch.submit(
        proposal=_proposal(),
        decision=RiskDecision.approve(requested_quantity=2),
        execution_symbol="AAPL",
        reason="refusal proof",
        session_id="test-session",
    )

    assert result.halt is True
    assert result.submitted is False
    assert trace == ["refused"]
    assert repository.intents == {}
    assert broker.submit_attempts == 0
