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
from us_quant.trading.application.live_recovery import LiveCanaryRecovery
from us_quant.trading.adapters.sqlite.live_safety_repository import (
    SQLiteLiveSafetyRepository,
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
    LiveRecoveryLatch,
    LiveSafetyRecord,
)
from us_quant.trading.domain.live_recovery import LiveRecoveryEvidence
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
    ExecutionSubmissionUncertain,
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
        self.submit_error: Exception | None = None
        self.submit_calls = 0

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
        self.submit_calls += 1
        if self.submit_error is not None:
            raise self.submit_error
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
        safety_revision=1,
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
    symbol: str = "AAPL",
    side: Side = Side.BUY,
    quantity: int = 1,
    price: str = "200",
) -> LiveCanaryOpenOrder:
    return LiveCanaryOpenOrder(
        broker_order_id=broker_order_id,
        symbol=symbol,
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
        authorization_state=lambda _record: state_holder[0],
        safety_repository=safety_repository,
        startup_proof=lambda: live_proof,
        truth=broker_truth,
        now=lambda: NOW,
    )
    return guard, underlying, state_holder, broker_truth


def _guard_with_durable_safety(
    tmp_path,
    broker: _Broker,
    *,
    truth: _Truth | None = None,
    proof: LiveStartupProof | None = None,
    state: LiveAuthorizationState | None = None,
):
    repository = SQLiteLiveSafetyRepository(tmp_path / "live-safety.sqlite3")
    state = state or _armed_state()
    repository.save(
        expected_revision=0,
        replacement=LiveSafetyRecord(1, state.authorization, state.kill_latch),
    )
    state_holder = [state]
    broker_truth = truth or _Truth(_truth())
    guard = LiveCanaryExecutionGuard(
        broker,
        authorization_state=lambda _record: state_holder[0],
        safety_repository=repository,
        startup_proof=lambda: proof or _proof(state_holder[0]),
        truth=broker_truth,
        now=lambda: NOW,
    )
    return guard, repository, state_holder, broker_truth


def test_guard_preserves_the_shared_reserve_durable_submit_boundary():
    guard, broker, _, _ = _guard()
    intent = _intent()

    reservation = guard.reserve(intent)
    assert broker.submissions == []
    guard.submit(reservation)

    assert broker.reservations == [reservation]
    assert broker.submissions == [reservation]


def test_disconnect_persists_recovery_barrier_and_reconnect_does_not_clear_it(tmp_path):
    broker = _Broker()
    guard, repository, _, _ = _guard_with_durable_safety(tmp_path, broker)

    guard.disconnect()
    guard.connect()

    assert broker.disconnect_calls == 1
    assert broker.connection_calls == 1
    assert repository.load().recovery_latch.is_required
    with pytest.raises(ExecutionRefused, match="recovery"):
        guard.reserve(_intent())


def test_fresh_execution_check_that_observes_disconnected_broker_latches_recovery(tmp_path):
    broker = _Broker()
    state = _armed_state()
    guard, repository, _, _ = _guard_with_durable_safety(
        tmp_path, broker, proof=_proof(state, connected=False)
    )

    with pytest.raises(ExecutionRefused, match="disconnected"):
        guard.reserve(_intent())

    assert repository.load().recovery_latch.is_required
    assert broker.reservations == []


def test_uncertain_submission_persists_recovery_and_is_never_retried(tmp_path):
    broker = _Broker()
    guard, repository, state_holder, _ = _guard_with_durable_safety(tmp_path, broker)
    intent = _intent()
    reservation = guard.reserve(intent)
    broker.submit_error = ExecutionSubmissionUncertain(
        "connection lost after send",
        order_id=intent.order_id,
        broker_order_id=reservation.broker_order_id,
        intent=intent,
    )

    with pytest.raises(ExecutionSubmissionUncertain):
        guard.submit(reservation)

    assert len(broker.reservations) == 1
    assert len(broker.submissions) == 0
    assert broker.submit_calls == 1
    recovery_latch = repository.load().recovery_latch
    assert recovery_latch.is_required
    assert recovery_latch.broker_order_id == reservation.broker_order_id
    with pytest.raises(ExecutionRefused, match="recovery"):
        guard.reserve(_intent())

    evidence = LiveRecoveryEvidence.capture(
        observed_at=NOW + timedelta(seconds=1),
        account_fingerprint=FINGERPRINT,
        broker_connected=True,
        open_orders_known=True,
        positions_known=True,
        fills_known=True,
        reconciliation_clean=True,
        uncertain_submission_resolved=True,
        reconciled_broker_order_ids=(reservation.broker_order_id,),
    )
    cleared = LiveCanaryRecovery(
        repository, now=lambda: NOW + timedelta(seconds=2)
    ).confirm_reconciled(evidence, operator_confirmed=True)
    refreshed_state = LiveAuthorizationState(
        cleared.authorization, cleared.kill_latch, cleared.recovery_latch
    ).request_session_arm(
        safety_revision=cleared.revision,
        now=NOW,
        account_fingerprint=FINGERPRINT,
        strategy_version_id="strategy-v1",
        operator_confirmed=True,
    )
    state_holder[0] = refreshed_state

    with pytest.raises(ExecutionRefused, match="already been submitted"):
        guard.submit(reservation)
    assert broker.submit_calls == 1


def test_uncertain_submission_barrier_serializes_other_guard_instances(tmp_path):
    first_entered = Event()
    release_first = Event()

    class _BlockingBroker(_Broker):
        intent_by_id: dict[int, OrderIntent] = {}
        submit_attempts = 0

        def reserve(self, intent: OrderIntent) -> BrokerOrderReservation:
            reservation = super().reserve(intent)
            self.intent_by_id[reservation.broker_order_id] = intent
            return reservation

        def submit(self, reservation: BrokerOrderReservation) -> None:
            self.submit_attempts += 1
            if reservation.broker_order_id == 40:
                first_entered.set()
                assert release_first.wait(timeout=2)
                intent = self.intent_by_id[reservation.broker_order_id]
                raise ExecutionSubmissionUncertain(
                    "connection lost after send",
                    order_id=intent.order_id,
                    broker_order_id=reservation.broker_order_id,
                    intent=intent,
                )
            super().submit(reservation)

    broker = _BlockingBroker()
    state = _armed_state(limits=_limits(max_open_orders=2))
    first_guard, repository, _, truth = _guard_with_durable_safety(
        tmp_path, broker, state=state
    )
    second_guard = LiveCanaryExecutionGuard(
        broker,
        authorization_state=lambda _record: state,
        safety_repository=repository,
        startup_proof=lambda: _proof(state),
        truth=truth,
        now=lambda: NOW,
    )
    first_reservation = first_guard.reserve(_intent())
    second_reservation = second_guard.reserve(_intent())
    errors: list[Exception] = []

    def first_submit():
        try:
            first_guard.submit(first_reservation)
        except Exception as error:
            errors.append(error)

    def second_submit():
        try:
            second_guard.submit(second_reservation)
        except Exception as error:
            errors.append(error)

    first_thread = Thread(target=first_submit)
    first_thread.start()
    assert first_entered.wait(timeout=1)
    second_thread = Thread(target=second_submit)
    second_thread.start()
    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert len(errors) == 2
    assert broker.submit_attempts == 1
    assert repository.load().recovery_latch.broker_order_id == first_reservation.broker_order_id


def _persist_kill_latch(repository, state_holder, reason: str = "kill drill"):
    current = repository.load()
    latch = current.kill_latch.engage(at=NOW, reason=reason)
    replacement = LiveSafetyRecord(
        current.revision + 1,
        current.authorization,
        latch,
        current.recovery_latch,
    )
    repository.save(expected_revision=current.revision, replacement=replacement)
    state_holder[0] = LiveAuthorizationState(
        current.authorization, latch, current.recovery_latch
    )
    return replacement


def test_kill_drill_blocks_when_latched_before_reserve(tmp_path):
    broker = _Broker()
    guard, repository, state_holder, _ = _guard_with_durable_safety(tmp_path, broker)
    _persist_kill_latch(repository, state_holder)

    with pytest.raises(ExecutionRefused):
        guard.reserve(_intent())
    assert broker.reservations == []


def test_kill_drill_blocks_after_reserve_and_before_submit(tmp_path):
    broker = _Broker()
    guard, repository, state_holder, _ = _guard_with_durable_safety(tmp_path, broker)
    reservation = guard.reserve(_intent())
    _persist_kill_latch(repository, state_holder)

    with pytest.raises(ExecutionRefused):
        guard.submit(reservation)
    assert broker.submissions == []


def test_kill_drill_cancels_a_tracked_pending_order(tmp_path):
    broker = _Broker()
    guard, repository, state_holder, _ = _guard_with_durable_safety(tmp_path, broker)
    reservation = guard.reserve(_intent())
    guard.submit(reservation)
    _persist_kill_latch(repository, state_holder)

    assert guard.cancel(reservation.order_id)
    assert broker.cancellations == [reservation.order_id]


def test_kill_drill_keeps_confirmed_risk_reducing_exit_available(tmp_path):
    broker = _Broker()
    truth = _Truth(
        _truth(positions=(LiveCanaryPosition("AAPL", 2, Decimal("400")),))
    )
    guard, repository, state_holder, _ = _guard_with_durable_safety(
        tmp_path, broker, truth=truth
    )
    _persist_kill_latch(repository, state_holder)

    reservation = guard.reserve(_intent(side=Side.SELL, quantity=2))
    guard.submit(reservation)

    assert broker.submissions == [reservation]
    assert repository.load().kill_latch.is_latched


def test_kill_drill_reconnect_does_not_remove_the_durable_kill(tmp_path):
    broker = _Broker()
    guard, repository, state_holder, _ = _guard_with_durable_safety(tmp_path, broker)
    _persist_kill_latch(repository, state_holder)

    guard.connect()

    with pytest.raises(ExecutionRefused):
        guard.reserve(_intent())
    assert repository.load().kill_latch.is_latched


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


def test_broker_visible_open_buy_counts_toward_position_cap():
    guard, broker, _, _ = _guard(
        state=_armed_state(
            limits=_limits(
                max_positions=1,
                max_open_orders=2,
                allowed_symbols=("AAPL", "MSFT"),
            )
        ),
        truth=_Truth(
            _truth(
                positions=(LiveCanaryPosition("AAPL", 0, Decimal("0")),),
                open_orders=(_open_order(900, symbol="AAPL"),),
            )
        ),
    )

    with pytest.raises(ExecutionRefused, match="position limit"):
        guard.reserve(_intent(symbol="MSFT"))

    assert broker.reservations == []


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
        safety_revision=1,
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
        authorization_state=lambda _record: state_holder[0],
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


def test_clear_kill_does_not_restore_arm_from_an_older_safety_revision(tmp_path):
    armed = _armed_state()
    repository = SQLiteLiveSafetyRepository(tmp_path / "live-safety.sqlite3")
    repository.save(
        expected_revision=0,
        replacement=LiveSafetyRecord(1, armed.authorization, armed.kill_latch),
    )
    latched = armed.engage_kill(at=NOW, reason="operator kill")
    repository.save(
        expected_revision=1,
        replacement=LiveSafetyRecord(2, latched.authorization, latched.kill_latch),
    )
    cleared_record = LiveSafetyRecord(
        3, armed.authorization, latched.kill_latch.clear()
    )
    repository.save(expected_revision=2, replacement=cleared_record)

    broker = _Broker()
    guard = LiveCanaryExecutionGuard(
        broker,
        authorization_state=lambda _record: armed,
        safety_repository=repository,
        startup_proof=lambda: _proof(armed),
        truth=_Truth(_truth()),
        now=lambda: NOW,
    )
    with pytest.raises(ExecutionRefused, match="safety revision changed"):
        guard.reserve(_intent())

    assert broker.reservations == []


def test_old_startup_proof_cannot_authorize_a_replaced_limit_set():
    original_state = _armed_state()
    old_proof = _proof(original_state)
    updated_authorization = replace(
        original_state.authorization,
        approved_canary_limits=_limits(max_order_notional=Decimal("100")),
    )
    updated_state = LiveAuthorizationState(updated_authorization).request_session_arm(
        safety_revision=1,
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


def test_broker_visible_sell_reservation_is_subtracted_from_owned_shares():
    guard, broker, _, _ = _guard(
        state=_armed_state(limits=_limits(max_open_orders=2)),
        truth=_Truth(
            _truth(
                positions=(LiveCanaryPosition("AAPL", 2, Decimal("400")),),
                open_orders=(_open_order(900, side=Side.SELL, quantity=2),),
            )
        ),
    )

    with pytest.raises(ExecutionRefused, match="SELL exceeds"):
        guard.reserve(_intent(side=Side.SELL, quantity=1))

    assert broker.reservations == []


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
