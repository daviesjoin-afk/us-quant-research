"""Execution application: order identity, idempotency and durable ordering.

The property this file exists to protect is the *order of operations* in
``ExecutionApplication._submit``: reserve the broker order id, write the
correlation to the store, and only then let the broker see the order.  A crash
in between must leave a nameable order rather than a live broker order nobody
can identify, so the tests assert the exact call sequence rather than the end
state alone.

Everything else here is the boundary the application must not cross: it may
check a verdict's own consistency, and it may refuse, but it must never
recompute what risk decided or raise the quantity risk approved.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from unittest.mock import patch

import us_quant.trading.application.execution as execution_module
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

_STRATEGY = StrategyIdentity(
    strategy_id="intraday-auto-rotation",
    version_id="version-7",
    parameter_hash="hash",
)
_NOW = datetime(2026, 7, 26, 14, 0, tzinfo=timezone.utc)


def _proposal(
    *,
    symbol: str = "AAPL",
    action: TradeAction = TradeAction.BUY,
    desired_quantity: int = 10,
    reference_price: Decimal = Decimal("101.25"),
    reason: str = "momentum entry",
) -> TradeProposal:
    return TradeProposal(
        strategy=_STRATEGY,
        symbol=symbol,
        action=action,
        desired_quantity=desired_quantity,
        reference_price=reference_price,
        reason=reason,
        generated_at=_NOW,
    )


class _Log:
    """One ordered trace shared by the two fakes.

    The sequence is the assertion: ``reserve`` must precede ``record_intent``,
    and ``record_intent`` must precede ``submit``.  Reading it after the fact
    is what makes "the durable write happens before anything can reach the
    broker" a testable claim rather than a comment.
    """

    def __init__(self) -> None:
        self.entries: list[str] = []

    def add(self, entry: str) -> None:
        self.entries.append(entry)


class _FakeRepository:
    def __init__(self, log: _Log) -> None:
        self._log = log
        self.intents: dict[str, OrderIntent] = {}
        self.broker_ids: dict[str, int] = {}
        self.statuses: dict[str, OrderStatus] = {}
        self.events: list[OrderEvent] = []
        self.fills: list[ExecutionFill] = []
        self.by_key: dict[str, OrderIntent] = {}

    def record_intent(
        self,
        intent: OrderIntent,
        *,
        broker_order_id: int,
        account_alias: str,
    ) -> None:
        self._log.add(f"record_intent:{intent.order_id}")
        self.intents[intent.order_id] = intent
        self.broker_ids[intent.order_id] = broker_order_id
        self.statuses[intent.order_id] = OrderStatus.SUBMITTING
        if intent.idempotency_key:
            self.by_key[intent.idempotency_key] = intent

    def record_event(self, event: OrderEvent) -> None:
        self.events.append(event)
        self.statuses[event.order_id] = event.status

    def record_fill(self, fill: ExecutionFill) -> bool:
        if any(row.execution_id == fill.execution_id for row in self.fills):
            return False
        self.fills.append(fill)
        return True

    def status(self, order_id: str) -> OrderStatus | None:
        return self.statuses.get(order_id)

    def intent(self, order_id: str) -> OrderIntent | None:
        return self.intents.get(order_id)

    def broker_order_id(self, order_id: str) -> int | None:
        return self.broker_ids.get(order_id)

    def fills(self, order_id: str) -> tuple[ExecutionFill, ...]:
        return tuple(row for row in self.fills if row.order_id == order_id)

    def intent_for_idempotency_key(
        self, idempotency_key: str
    ) -> OrderIntent | None:
        return self.by_key.get(idempotency_key)

    def executed_quantity(self, order_id: str) -> Decimal:
        return sum(
            (
                row.quantity
                for row in self.fills
                if row.order_id == order_id
            ),
            Decimal("0"),
        )

    def max_broker_order_id(self) -> int:
        return max(self.broker_ids.values(), default=0)


class _RecordingBroker:
    def __init__(
        self,
        log: _Log,
        *,
        fail_submit: bool = False,
        fail_reserve: bool = False,
    ) -> None:
        self._log = log
        self._fail_submit = fail_submit
        self._fail_reserve = fail_reserve
        self._next = 500
        self.submitted: list[OrderIntent] = []
        self.submit_attempts = 0
        self._by_order_id: dict[str, OrderIntent] = {}
        self.reservations: list[BrokerOrderReservation] = []

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def reserve(self, intent: OrderIntent) -> BrokerOrderReservation:
        if self._fail_reserve:
            raise ExecutionRefused("synthetic reserve refusal")
        self._log.add(f"reserve:{intent.order_id}")
        self._next += 1
        self._by_order_id[intent.order_id] = intent
        reservation = BrokerOrderReservation(
            order_id=intent.order_id,
            broker_order_id=self._next,
            account_alias="DU***TEST",
        )
        self.reservations.append(reservation)
        return reservation

    def submit(self, reservation: BrokerOrderReservation) -> None:
        self._log.add(f"submit:{reservation.order_id}")
        self.submit_attempts += 1
        if self._fail_submit:
            raise ExecutionSubmissionUncertain(
                "synthetic uncertain submission",
                order_id=reservation.order_id,
                broker_order_id=reservation.broker_order_id,
            )
        self.submitted.append(self._by_order_id[reservation.order_id])

    def cancel(self, order_id: str) -> bool:
        self._log.add(f"cancel:{order_id}")
        return True

    def events(self) -> tuple[OrderEvent, ...]:
        return ()

    def fills(self) -> tuple[ExecutionFill, ...]:
        return ()


def _app(
    *,
    log: _Log | None = None,
    fail_submit: bool = False,
    fail_reserve: bool = False,
) -> tuple[ExecutionApplication, _FakeRepository, _RecordingBroker]:
    trace = log or _Log()
    repository = _FakeRepository(trace)
    broker = _RecordingBroker(
        trace, fail_submit=fail_submit, fail_reserve=fail_reserve
    )
    return (
        ExecutionApplication(repository=repository, broker=broker),
        repository,
        broker,
    )


# -- the happy path -------------------------------------------------------


def test_an_approved_proposal_becomes_exactly_one_order() -> None:
    app, repository, broker = _app()
    proposal = _proposal()
    decision = RiskDecision.approve(requested_quantity=10)

    result = app.submit_approved(
        proposal=proposal,
        decision=decision,
        execution_symbol="AAPL",
        session_id="s-1",
        reason="momentum entry",
    )

    assert broker.submitted == [result.intent]
    assert repository.intents[result.intent.order_id] is result.intent
    assert result.broker_order_id == broker.reservations[0].broker_order_id


def test_the_order_records_the_context_the_runtime_asked_for() -> None:
    app, _, _ = _app()
    result = app.submit_approved(
        proposal=_proposal(
            symbol="MSFT",
            reference_price=Decimal("410.25"),
            reason="trimmed 10 → 4; cash cap",
        ),
        decision=RiskDecision.approve(
            requested_quantity=10,
            approved_quantity=4,
            adjustments=("cash cap reduced quantity 10 → 4",),
        ),
        execution_symbol="MSFT",
        session_id="session-9",
        reason="trimmed 10 → 4; cash cap",
    )

    intent = result.intent
    assert intent.session_id == "session-9"
    assert intent.strategy_version_id == _STRATEGY.version_id
    assert intent.signal_symbol == "MSFT"
    assert intent.execution_symbol == "MSFT"
    assert intent.side is Side.BUY
    # Execution owns the price the order is sent with, and the strategy's
    # reference price is what risk sized against -- so they must agree.
    assert intent.limit_price == Decimal("410.25")
    assert intent.reason == "trimmed 10 → 4; cash cap"
    assert intent.quantity == 4


def test_a_sell_proposal_maps_to_the_sell_side() -> None:
    app, _, broker = _app()
    app.submit_approved(
        proposal=_proposal(action=TradeAction.SELL, desired_quantity=3),
        decision=RiskDecision.approve(requested_quantity=3),
        execution_symbol="AAPL",
        session_id="s-1",
        reason="stop loss",
    )
    assert broker.submitted[0].side is Side.SELL


# -- durable before the broker can see it ---------------------------------


def test_the_durable_write_happens_before_the_broker_is_told() -> None:
    """The P0 ordering: reserve, then record, then send.

    A crash between reserve and record leaves an order with no correlation; a
    crash between record and send leaves a correlated order that never went
    out.  Both are recoverable.  Sending *before* recording leaves a live
    broker order nobody can name, which is the failure this order prevents.
    """

    log = _Log()
    app, _, _ = _app(log=log)
    result = app.submit_approved(
        proposal=_proposal(),
        decision=RiskDecision.approve(requested_quantity=10),
        execution_symbol="AAPL",
        session_id="s-1",
        reason="momentum entry",
    )

    order_id = result.intent.order_id
    assert log.entries == [
        f"reserve:{order_id}",
        f"record_intent:{order_id}",
        f"submit:{order_id}",
    ]


def test_an_unrecorded_order_is_never_sent() -> None:
    """The store is a hard prerequisite, not an audit trail written later."""

    app, repository, broker = _app()
    original = repository.record_intent

    def exploding(intent: OrderIntent, **kwargs: object) -> None:
        original(intent, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("store is down")

    repository.record_intent = exploding  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        app.submit_approved(
            proposal=_proposal(),
            decision=RiskDecision.approve(requested_quantity=10),
            execution_symbol="AAPL",
            session_id="s-1",
            reason="momentum entry",
        )
    assert broker.submit_attempts == 0


# -- idempotency ----------------------------------------------------------


def _fixed_intent(
    *,
    order_id: str,
    idempotency_key: str,
) -> OrderIntent:
    return OrderIntent(
        order_id=order_id,
        client_order_id=f"uq-{order_id}",
        session_id="s-1",
        strategy_version_id=_STRATEGY.version_id,
        signal_symbol="AAPL",
        execution_symbol="AAPL",
        side=Side.BUY,
        quantity=10,
        limit_price=Decimal("101.25"),
        reason="momentum entry",
        idempotency_key=idempotency_key,
    )


def test_submitting_the_same_order_twice_reaches_the_broker_once() -> None:
    """One order identity, one broker order -- even if the caller retries."""

    app, _, broker = _app()
    fixed = _fixed_intent(order_id="o-fixed", idempotency_key="k-fixed")
    with patch.object(
        execution_module.OrderIntent,
        "create",
        staticmethod(lambda **_: fixed),
    ):
        first = app.submit_approved(
            proposal=_proposal(),
            decision=RiskDecision.approve(requested_quantity=10),
            execution_symbol="AAPL",
            session_id="s-1",
            reason="momentum entry",
        )
        second = app.submit_approved(
            proposal=_proposal(),
            decision=RiskDecision.approve(requested_quantity=10),
            execution_symbol="AAPL",
            session_id="s-1",
            reason="momentum entry",
        )

    assert first.intent.order_id == second.intent.order_id == "o-fixed"
    assert broker.submit_attempts == 1
    assert len(broker.submitted) == 1
    # The retry still names the broker order it already holds, so a caller can
    # report it instead of guessing.
    assert second.broker_order_id == first.broker_order_id


def test_a_duplicate_idempotency_key_for_a_different_order_is_refused() -> None:
    app, repository, broker = _app()
    repository.by_key["k-shared"] = _fixed_intent(
        order_id="o-first", idempotency_key="k-shared"
    )
    clashing = _fixed_intent(
        order_id="o-second", idempotency_key="k-shared"
    )
    with patch.object(
        execution_module.OrderIntent,
        "create",
        staticmethod(lambda **_: clashing),
    ):
        with pytest.raises(ExecutionRefused, match="幂等键"):
            app.submit_approved(
                proposal=_proposal(),
                decision=RiskDecision.approve(requested_quantity=10),
                execution_symbol="AAPL",
                session_id="s-1",
                reason="momentum entry",
            )
    assert broker.submit_attempts == 0
    assert broker.reservations == []


# -- uncertain submission -------------------------------------------------


def test_an_uncertain_submission_carries_the_order_the_broker_may_hold() -> None:
    app, repository, broker = _app(fail_submit=True)

    with pytest.raises(ExecutionSubmissionUncertain) as captured:
        app.submit_approved(
            proposal=_proposal(),
            decision=RiskDecision.approve(requested_quantity=10),
            execution_symbol="AAPL",
            session_id="s-1",
            reason="momentum entry",
        )

    error = captured.value
    assert error.intent is not None
    assert error.order_id == error.intent.order_id
    assert error.broker_order_id == broker.reservations[0].broker_order_id
    # The order is durable and the caller can name it, which is what lets the
    # runtime keep it pending instead of forgetting an order that may be live.
    assert repository.intents[error.order_id] is error.intent
    # One attempt only: no retry happened, and none is offered.
    assert broker.submit_attempts == 1


# -- the verdict is not re-interpreted ------------------------------------


def test_a_rejected_verdict_never_creates_an_order() -> None:
    app, repository, broker = _app()
    with pytest.raises(ExecutionRefused, match="未批准"):
        app.submit_approved(
            proposal=_proposal(),
            decision=RiskDecision.reject(
                "cash cap", requested_quantity=10
            ),
            execution_symbol="AAPL",
            session_id="s-1",
            reason="momentum entry",
        )
    assert repository.intents == {}
    assert broker.reservations == []


def test_a_trimmed_verdict_is_submitted_at_the_approved_quantity() -> None:
    app, _, broker = _app()
    app.submit_approved(
        proposal=_proposal(desired_quantity=10),
        decision=RiskDecision.approve(
            requested_quantity=10,
            approved_quantity=3,
            adjustments=("position cap reduced quantity 10 → 3",),
        ),
        execution_symbol="AAPL",
        session_id="s-1",
        reason="trimmed",
    )
    assert broker.submitted[0].quantity == 3


def test_an_oversized_approval_cannot_even_be_built() -> None:
    """Execution never raises risk's number -- and cannot be handed one.

    ``RiskDecision`` refuses to construct an approval above its own request, and
    the application separately refuses a verdict taken against a different
    request, so "approved more than the proposal asked for" is unreachable from
    both ends.  This pins the domain half; the application half is the
    same-request check below.
    """

    with pytest.raises(ValueError, match="cannot exceed"):
        RiskDecision.approve(requested_quantity=4, approved_quantity=9)


def test_a_verdict_from_a_different_request_is_refused() -> None:
    app, _, broker = _app()
    with pytest.raises(ExecutionRefused, match="同一次请求"):
        app.submit_approved(
            proposal=_proposal(desired_quantity=10),
            decision=RiskDecision.approve(
                requested_quantity=7, approved_quantity=7
            ),
            execution_symbol="AAPL",
            session_id="s-1",
            reason="momentum entry",
        )
    assert broker.reservations == []


def test_a_broker_refusal_propagates_without_creating_anything() -> None:
    app, repository, broker = _app(fail_reserve=True)
    with pytest.raises(ExecutionRefused, match="reserve"):
        app.submit_approved(
            proposal=_proposal(),
            decision=RiskDecision.approve(requested_quantity=10),
            execution_symbol="AAPL",
            session_id="s-1",
            reason="momentum entry",
        )
    assert repository.intents == {}
    assert broker.submit_attempts == 0


# -- reads ----------------------------------------------------------------


def test_the_reads_go_through_the_ports() -> None:
    app, repository, _ = _app()
    intent = _fixed_intent(order_id="o-1", idempotency_key="k-1")
    repository.record_intent(
        intent, broker_order_id=42, account_alias="DU***TEST"
    )

    assert app.intent("o-1") is intent
    assert app.status("o-1") is OrderStatus.SUBMITTING
    assert app.status("missing") is None
    assert app.events() == ()
    assert app.fills() == ()
    assert app.cancel("o-1") is True