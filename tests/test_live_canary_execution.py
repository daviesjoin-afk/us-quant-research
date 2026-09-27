from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from contextlib import contextmanager
from threading import RLock, Thread, Event

import pytest

from us_quant.trading.application.live_canary_execution import (
    LiveCanaryExecutionGuard,
)
from us_quant.trading.domain.live_canary import (
    LiveCanaryOpenOrder,
    LiveCanaryPosition,
    LiveCanaryTruth,
    LiveCanaryTruthError,
)
from us_quant.trading.domain.live_safety import (
    LiveAccountFingerprint,
    LiveAuthorizationState,
    LiveCanaryLimits,
    LiveOperatorAuthorization,
    LiveSafetyRecord,
)
from us_quant.trading.domain.live_startup import (
    LiveEndpointIdentity,
    LiveStartupProof,
)
from us_quant.trading.domain.orders import (
    OrderEvent,
    OrderIntent,
    OrderStatus,
    Side,
)
from us_quant.trading.ports.broker_execution import (
    BrokerOrderReservation,
    ExecutionRefused,
)


class _SafetyRepository:
    def __init__(self, state_provider):
        self._state_provider = state_provider
        self._lock = RLock()

    @contextmanager
    def execution_lease(self):
        with self._lock:
            state = self._state_provider()
            yield LiveSafetyRecord(
                revision=1,
                authorization=state.authorization,
                kill_latch=state.kill_latch,
            )

    def update(self, replacement):
        with self._lock:
            replacement()


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
ACCOUNT_ID = "U1234567"
FINGERPRINT = LiveAccountFingerprint.from_identity(
    provider="IBKR",
    environment="LIVE",
    account_id=ACCOUNT_ID,
    endpoint_identity="ibkr-live-loopback:127.0.0.1:4001",
)


class _Broker:
    def __init__(self) -> None:
        self.reservations: list[BrokerOrderReservation] = []
        self.submissions: list[BrokerOrderReservation] = []
        self.cancellations: list[str] = []
        self.pending_events: tuple[OrderEvent, ...] = ()
        self.connection_calls = 0
        self.disconnect_calls = 0

    def connect(self) -> None:
        self.connection_calls += 1

    def disconnect(self) -> None:
        self.disconnect_calls += 1

    def reserve(self, intent: OrderIntent) -> BrokerOrderReservation:
        reservation = BrokerOrderReservation(
            intent.order_id, 40 + len(self.reservations), "U1***67"
        )
        self.reservations.append(reservation)
        return reservation

    def submit(self, reservation: BrokerOrderReservation) -> None:
        self.submissions.append(reservation)

    def cancel(self, order_id: str) -> bool:
        self.cancellations.append(order_id)
        return True

    def events(self) -> tuple[OrderEvent, ...]:
        events = self.pending_events
        self.pending_events = ()
        return events

    def fills(self):
        return ()


class _Truth:
    def __init__(self, snapshot: LiveCanaryTruth) -> None:
        self.current = snapshot

    def snapshot(self) -> LiveCanaryTruth:
        return self.current


def _limits(**changes: object) -> LiveCanaryLimits:
    values: dict[str, object] = {
        "capital_limit": Decimal("2000"),
        "max_order_notional": Decimal("500"),
        "max_daily_loss": Decimal("100"),
        "max_positions": 1,
        "max_open_orders": 1,
        "allowed_symbols": ("AAPL",),
        "allowed_strategy_versions": ("strategy-v1",),
    }
    values.update(changes)
    return LiveCanaryLimits(**values)  # type: ignore[arg-type]


def _authorization(limits: LiveCanaryLimits | None = None) -> LiveOperatorAuthorization:
    return LiveOperatorAuthorization(
        authorization_id="operator-authorization-1",
        created_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=1),
        expected_account_fingerprint=FINGERPRINT,
        approved_strategy_version_ids=("strategy-v1",),
        approved_canary_limits=limits or _limits(),
    )


def _armed_state(
    *, limits: LiveCanaryLimits | None = None
) -> LiveAuthorizationState:
    state = LiveAuthorizationState(_authorization(limits))
    return state.request_session_arm(
        now=NOW,
        account_fingerprint=FINGERPRINT,
        strategy_version_id="strategy-v1",
        operator_confirmed=True,
    )


def _proof(
    state: LiveAuthorizationState,
    *,
    now: datetime = NOW,
    account_id: str = ACCOUNT_ID,
    connected: bool = True,
    reconciliation_clean: bool = True,
    observed_at: datetime | None = None,
) -> LiveStartupProof:
    fact_time = observed_at or now
    return LiveStartupProof.capture(
        now=now,
        broker_connected=connected,
        observed_endpoint=LiveEndpointIdentity(),
        managed_account_ids=(account_id,),
        broker_account_id=account_id,
        connection_observed_at=fact_time,
        account_truth_known=True,
        account_truth_observed_at=fact_time,
        market_truth_known=True,
        market_truth_observed_at=fact_time,
        open_orders_known=True,
        open_orders_observed_at=fact_time,
        positions_known=True,
        positions_observed_at=fact_time,
        reconciliation_clean=reconciliation_clean,
        reconciliation_observed_at=fact_time,
        authorization_state=state,
    )


def _truth(
    *,
    positions: tuple[LiveCanaryPosition, ...] = (),
    daily_pnl: Decimal = Decimal("0"),
    open_orders: tuple[LiveCanaryOpenOrder, ...] = (),
    observed_at: datetime = NOW,
    net_liquidation: Decimal = Decimal("10000"),
) -> LiveCanaryTruth:
    return LiveCanaryTruth(
        account_fingerprint=FINGERPRINT,
        observed_at=observed_at,
        net_liquidation=net_liquidation,
        daily_pnl=daily_pnl,
        open_orders=open_orders,
        positions=positions,
    )


def _open_order(
    broker_order_id: int,
    *,
    side: Side = Side.BUY,
    quantity: int = 1,
    price: str = "200",
) -> LiveCanaryOpenOrder:
    return LiveCanaryOpenOrder(
        broker_order_id=broker_order_id,
        symbol="AAPL",
        side=side,
        remaining_quantity=quantity,
        limit_price=Decimal(price),
    )


def _intent(
    *,
    side: Side = Side.BUY,
    quantity: int = 1,
    symbol: str = "AAPL",
    strategy: str = "strategy-v1",
    price: str = "200",
) -> OrderIntent:
    return OrderIntent.create(
        session_id="live-canary-session",
        strategy_version_id=strategy,
        signal_symbol=symbol,
        execution_symbol=symbol,
        side=side,
        quantity=quantity,
        limit_price=Decimal(price),
        reason="test canary order",
    )


def _guard(
    *,
    state: LiveAuthorizationState | None = None,
    truth: _Truth | None = None,
    proof: LiveStartupProof | None = None,
    broker: _Broker | None = None,
) -> tuple[LiveCanaryExecutionGuard, _Broker, list[LiveAuthorizationState], _Truth]:
    current_state = state or _armed_state()
    state_holder = [current_state]
    broker_truth = truth or _Truth(_truth())
    live_proof = proof or _proof(current_state)
    underlying = broker or _Broker()
    safety_repository = _SafetyRepository(lambda: state_holder[0])
    guard = LiveCanaryExecutionGuard(
        underlying,
        authorization_state=lambda: state_holder[0],
        safety_repository=safety_repository,
        startup_proof=lambda: live_proof,
        truth=broker_truth,
        now=lambda: NOW,
    )
    return guard, underlying, state_holder, broker_truth


def test_guard_preserves_the_shared_reserve_durable_submit_boundary():
    guard, broker, _, _ = _guard()
    intent = _intent()

    reservation = guard.reserve(intent)
    assert broker.submissions == []
    guard.submit(reservation)

    assert broker.reservations == [reservation]
    assert broker.submissions == [reservation]


def test_external_open_buy_notional_counts_against_the_capital_limit():
    guard, broker, _, _ = _guard(
        state=_armed_state(limits=_limits(capital_limit=Decimal("2000"), max_open_orders=2)),
        truth=_Truth(_truth(open_orders=(_open_order(900, price="1900"),))),
    )

    with pytest.raises(ExecutionRefused, match="capital limit"):
        guard.reserve(_intent(price="200"))

    assert broker.reservations == []


def test_broker_visible_active_order_is_counted_once_by_its_identity():
    guard, broker, _, truth = _guard(
        state=_armed_state(limits=_limits(max_open_orders=2)),
    )
    first = _intent()
    reservation = guard.reserve(first)
    guard.submit(reservation)
    truth.current = _truth(
        open_orders=(_open_order(reservation.broker_order_id),)
    )

    second_reservation = guard.reserve(_intent())

    assert len(broker.reservations) == 2
    assert second_reservation.broker_order_id != reservation.broker_order_id


@pytest.mark.parametrize(
    ("limits", "truth", "intent"),
    [
        (_limits(max_order_notional=Decimal("199")), _truth(), _intent()),
        (_limits(capital_limit=Decimal("199")), _truth(), _intent()),
        (_limits(max_daily_loss=Decimal("99")), _truth(daily_pnl=Decimal("-99")), _intent()),
        (
            _limits(max_positions=1),
            _truth(positions=(LiveCanaryPosition("MSFT", 1, Decimal("100")),)),
            _intent(),
        ),
        (_limits(max_open_orders=1), _truth(open_orders=(_open_order(900),)), _intent()),
        (_limits(allowed_symbols=("MSFT",)), _truth(), _intent()),
        (_limits(), _truth(), _intent(strategy="other")),
    ],
)
def test_guard_fails_closed_at_each_canary_exposure_limit(limits, truth, intent):
    guard, broker, _, _ = _guard(
        state=_armed_state(limits=limits), truth=_Truth(truth)
    )

    with pytest.raises(ExecutionRefused):
        guard.reserve(intent)

    assert broker.reservations == []
    assert broker.submissions == []


def test_guard_rechecks_truth_after_durable_reservation_before_submit():
    guard, broker, _, truth = _guard()
    reservation = guard.reserve(_intent())
    truth.current = _truth(daily_pnl=Decimal("-100"))

    with pytest.raises(ExecutionRefused, match="daily loss"):
        guard.submit(reservation)

    assert broker.submissions == []


def test_guard_rechecks_ephemeral_session_arm_before_submit():
    guard, broker, state_holder, _ = _guard()
    original_state = state_holder[0]
    reservation = guard.reserve(_intent())
    state_holder[0] = original_state.after_restart()

    with pytest.raises(ExecutionRefused):
        guard.submit(reservation)

    assert broker.submissions == []

    state_holder[0] = original_state.request_session_arm(
        now=NOW,
        account_fingerprint=FINGERPRINT,
        strategy_version_id="strategy-v1",
        operator_confirmed=True,
    )
    guard, broker, _, _ = _guard(state=state_holder[0], proof=_proof(original_state))
    with pytest.raises(ExecutionRefused, match="explicit operator arm"):
        guard.reserve(_intent())
    assert broker.reservations == []


def test_persistent_kill_writer_is_serialized_with_submit():
    state = _armed_state()
    state_holder = [state]
    safety_repository = _SafetyRepository(lambda: state_holder[0])
    submit_reached = Event()
    writer_finished = Event()

    class _RacingTruth(_Truth):
        race = False

        def snapshot(self):
            if self.race:
                writer = Thread(
                    target=lambda: (
                        safety_repository.update(
                            lambda: state_holder.__setitem__(
                                0, state.engage_kill(at=NOW, reason="operator kill")
                            )
                        ),
                        writer_finished.set(),
                    )
                )
                writer.start()
            return super().snapshot()

    class _SubmitWitness(_Broker):
        def submit(self, reservation):
            assert not writer_finished.is_set()
            submit_reached.set()
            super().submit(reservation)

    broker = _SubmitWitness()
    truth = _RacingTruth(_truth())
    proof = _proof(state)
    guard = LiveCanaryExecutionGuard(
        broker,
        authorization_state=lambda: state_holder[0],
        safety_repository=safety_repository,
        startup_proof=lambda: proof,
        truth=truth,
        now=lambda: NOW,
    )
    reservation = guard.reserve(_intent())
    truth.race = True
    guard.submit(reservation)

    assert submit_reached.is_set()
    assert writer_finished.wait(timeout=1)
    assert broker.submissions == [reservation]
    assert state_holder[0].kill_latch.is_latched


def test_old_startup_proof_cannot_authorize_a_replaced_limit_set():
    original_state = _armed_state()
    old_proof = _proof(original_state)
    updated_authorization = replace(
        original_state.authorization,
        approved_canary_limits=_limits(max_order_notional=Decimal("100")),
    )
    updated_state = LiveAuthorizationState(updated_authorization).request_session_arm(
        now=NOW,
        account_fingerprint=FINGERPRINT,
        strategy_version_id="strategy-v1",
        operator_confirmed=True,
    )
    guard, broker, _, _ = _guard(state=updated_state, proof=old_proof)

    with pytest.raises(ExecutionRefused, match="older authorization"):
        guard.reserve(_intent())

    assert broker.reservations == []


def test_stale_or_mismatched_startup_proof_cannot_authorize_live_order():
    state = _armed_state()
    stale = _proof(state, observed_at=NOW - timedelta(minutes=1))
    guard, broker, _, _ = _guard(state=state, proof=stale)

    with pytest.raises(ExecutionRefused, match="stale or incomplete"):
        guard.reserve(_intent())
    assert broker.reservations == []

    wrong_account_proof = _proof(state, account_id="U7654321")
    guard, broker, _, _ = _guard(state=state, proof=wrong_account_proof)
    with pytest.raises(ExecutionRefused, match="account or endpoint"):
        guard.reserve(_intent())
    assert broker.reservations == []


def test_kill_blocks_buys_but_allows_only_owned_share_exit():
    state = _armed_state()
    killed_state = state.engage_kill(at=NOW, reason="operator kill")
    guard, broker, _, _ = _guard(
        state=killed_state,
        proof=_proof(killed_state),
        truth=_Truth(
            _truth(positions=(LiveCanaryPosition("AAPL", 2, Decimal("400")),))
        ),
    )

    with pytest.raises(ExecutionRefused, match="SELL exceeds"):
        guard.reserve(_intent(side=Side.SELL, quantity=3))
    with pytest.raises(ExecutionRefused):
        guard.reserve(_intent(side=Side.BUY))
    reservation = guard.reserve(_intent(side=Side.SELL, quantity=2))
    assert guard.cancel(reservation.order_id)
    assert broker.cancellations == [reservation.order_id]
    assert broker.submissions == []


def test_kill_latch_blocks_new_exposure_even_if_an_old_arm_bit_survives():
    state = _armed_state()
    killed_state = state.engage_kill(at=NOW, reason="operator kill")
    # Simulate a mixed stale read during a concurrent kill update. The guard
    # must give the persistent latch priority over the old process-local arm.
    object.__setattr__(killed_state, "session_armed", True)
    object.__setattr__(killed_state, "session_arm_id", state.session_arm_id)
    guard, broker, _, _ = _guard(
        state=killed_state,
        proof=_proof(killed_state),
    )

    with pytest.raises(ExecutionRefused, match="kill latch"):
        guard.reserve(_intent())

    assert broker.reservations == []


def test_kill_allows_an_owned_share_risk_reducing_exit():
    killed_state = _armed_state().engage_kill(
        at=NOW, reason="operator kill"
    )
    guard, _, _, _ = _guard(
        state=killed_state,
        proof=_proof(killed_state),
        truth=_Truth(
            _truth(positions=(LiveCanaryPosition("AAPL", 2, Decimal("400")),))
        ),
    )

    allowed = True
    try:
        guard.reserve(_intent(side=Side.SELL, quantity=2))
    except ExecutionRefused:
        allowed = False

    assert allowed is True


def test_reserved_sell_capacity_prevents_two_concurrent_exits_from_overselling():
    guard, broker, _, truth = _guard(
        state=_armed_state(limits=_limits(max_open_orders=2)),
        truth=_Truth(
            _truth(positions=(LiveCanaryPosition("AAPL", 2, Decimal("400")),))
        )
    )
    guard.reserve(_intent(side=Side.SELL, quantity=2))
    truth.current = _truth(positions=(LiveCanaryPosition("AAPL", 2, Decimal("400")),))

    with pytest.raises(ExecutionRefused, match="SELL exceeds"):
        guard.reserve(_intent(side=Side.SELL, quantity=1))

    assert len(broker.reservations) == 1


def test_terminal_order_event_releases_guard_local_reservation():
    guard, broker, _, _ = _guard()
    first = _intent()
    reservation = guard.reserve(first)
    broker.pending_events = (
        OrderEvent(
            order_id=first.order_id,
            status=OrderStatus.CANCELED,
            broker_order_id=reservation.broker_order_id,
            filled=Decimal("0"),
            remaining=Decimal("1"),
            average_fill_price=None,
            last_fill_price=None,
            message="cancelled",
            occurred_at=NOW,
        ),
    )
    assert guard.events()[0].status is OrderStatus.CANCELED
    guard.reserve(_intent())
    assert len(broker.reservations) == 2


def test_duplicate_position_truth_is_rejected_as_untrustworthy():
    with pytest.raises(LiveCanaryTruthError, match="duplicate"):
        _truth(
            positions=(
                LiveCanaryPosition("AAPL", 1, Decimal("200")),
                LiveCanaryPosition("aapl", 1, Decimal("200")),
            )
        )
