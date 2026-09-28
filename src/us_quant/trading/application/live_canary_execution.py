"""Last-moment, fail-closed guard for a small-capital Live execution channel."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from threading import RLock
from typing import Callable

from us_quant.trading.domain.live_canary import LiveCanaryTruth
from us_quant.trading.domain.live_safety import (
    LiveAuthorizationState,
    LiveSafetyRecord,
)
from us_quant.trading.domain.live_startup import (
    LIVE_STARTUP_PROOF_TTL,
    LiveStartupProof,
)
from us_quant.trading.domain.orders import ExecutionFill, OrderEvent, OrderIntent, OrderStatus, Side
from us_quant.trading.ports.broker_execution import (
    BrokerExecutionPort,
    BrokerOrderReservation,
    ExecutionRefused,
    ExecutionSubmissionUncertain,
)
from us_quant.trading.ports.live_canary_truth import LiveCanaryTruthPort
from us_quant.trading.ports.live_safety_repository import LiveSafetyRepositoryPort


class LiveCanaryExecutionGuard(BrokerExecutionPort):
    """Check fresh authorization and account truth around the shared execution port.

    This wrapper narrows the channel's authority.  The RiskApplication still
    chooses the approved size; the guard rejects an order that falls outside the
    operator's canary envelope and never resizes or retries it.
    """

    def __init__(
        self,
        broker: BrokerExecutionPort,
        *,
        authorization_state: Callable[[LiveSafetyRecord], LiveAuthorizationState],
        safety_repository: LiveSafetyRepositoryPort,
        startup_proof: Callable[[], LiveStartupProof],
        truth: LiveCanaryTruthPort,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._broker = broker
        self._authorization_state = authorization_state
        self._safety_repository = safety_repository
        self._startup_proof = startup_proof
        self._truth = truth
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._active: dict[int, tuple[OrderIntent, bool]] = {}
        self._lock = RLock()
        self._local_recovery_required = False
        self._recovery_recorded_this_check = False

    def connect(self) -> None:
        self._broker.connect()

    def disconnect(self) -> None:
        self._local_recovery_required = True
        persist_error: Exception | None = None
        try:
            with self._safety_repository.execution_lease() as lease:
                lease.require_reconciliation(
                    at=self._now(),
                    reason="Live broker disconnected",
                )
            self._local_recovery_required = False
        except Exception as error:
            persist_error = error
        try:
            self._broker.disconnect()
        finally:
            if persist_error is not None:
                raise ExecutionRefused(
                    "Live disconnected but the recovery barrier could not be persisted"
                ) from persist_error

    def reserve(self, intent: OrderIntent) -> BrokerOrderReservation:
        deferred_refusal: ExecutionRefused | None = None
        with self._safety_repository.execution_lease() as lease:
            with self._lock:
                durable_state = getattr(lease, "record", lease)
                try:
                    self._validate(
                        intent, durable_state=durable_state, safety_lease=lease
                    )
                except ExecutionRefused as error:
                    if self._recovery_recorded_this_check:
                        deferred_refusal = error
                    else:
                        raise
                if deferred_refusal is None:
                    reservation = self._broker.reserve(intent)
                    if reservation.broker_order_id in self._active:
                        raise ExecutionRefused("Live canary received a duplicate broker order id")
                    self._active[reservation.broker_order_id] = (intent, False)
                    return reservation
        if deferred_refusal is not None:
            raise deferred_refusal

    def submit(self, reservation: BrokerOrderReservation) -> None:
        uncertain: ExecutionSubmissionUncertain | None = None
        recovery_persist_error: Exception | None = None
        deferred_refusal: ExecutionRefused | None = None
        with self._safety_repository.execution_lease() as lease:
            with self._lock:
                durable_state = getattr(lease, "record", lease)
                active = self._active.get(reservation.broker_order_id)
                if active is None or active[0].order_id != reservation.order_id:
                    raise ExecutionRefused("Live canary reservation is not tracked")
                if active[1]:
                    raise ExecutionRefused(
                        "Live canary reservation has already been submitted and cannot be retried"
                    )
                intent = active[0]
                try:
                    self._validate(
                        intent,
                        own_reservation=reservation.broker_order_id,
                        durable_state=durable_state,
                        safety_lease=lease,
                    )
                    self._broker.submit(reservation)
                except ExecutionSubmissionUncertain as error:
                    self._active[reservation.broker_order_id] = (intent, True)
                    self._local_recovery_required = True
                    uncertain = error
                    try:
                        lease.require_reconciliation(
                            at=self._now(),
                            reason=(
                                "Uncertain Live submission for broker order "
                                f"{reservation.broker_order_id}"
                            ),
                            broker_order_id=reservation.broker_order_id,
                        )
                    except Exception as recovery_error:
                        recovery_persist_error = recovery_error
                    else:
                        self._local_recovery_required = False
                except ExecutionRefused as error:
                    if self._recovery_recorded_this_check:
                        deferred_refusal = error
                    else:
                        self._active.pop(reservation.broker_order_id, None)
                        raise
                else:
                    self._active[reservation.broker_order_id] = (intent, True)
        if uncertain is not None:
            if recovery_persist_error is not None:
                raise ExecutionSubmissionUncertain(
                    "Live submission outcome is uncertain and recovery could not be persisted",
                    order_id=uncertain.order_id,
                    broker_order_id=uncertain.broker_order_id,
                    intent=uncertain.intent,
                ) from recovery_persist_error
            raise uncertain
        if deferred_refusal is not None:
            raise deferred_refusal

    def cancel(self, order_id: str) -> bool:
        # Tracked cancellation is a safety operation and remains available while
        # exposure-increasing orders are blocked by the kill latch.
        return bool(self._broker.cancel(order_id))

    def events(self) -> tuple[OrderEvent, ...]:
        events = self._broker.events()
        with self._lock:
            for event in events:
                if event.status.is_terminal:
                    self._active.pop(event.broker_order_id, None)
        return events

    def fills(self) -> tuple[ExecutionFill, ...]:
        return self._broker.fills()

    def _validate(
        self,
        intent: OrderIntent,
        *,
        own_reservation: int | None = None,
        durable_state: LiveSafetyRecord,
        safety_lease: object,
    ) -> None:
        self._recovery_recorded_this_check = False
        if self._local_recovery_required:
            raise ExecutionRefused(
                "Live recovery requires fresh broker reconciliation and operator confirmation"
            )
        try:
            state = self._authorization_state(durable_state)
            proof = self._startup_proof()
            now = self._now()
        except Exception as error:
            raise ExecutionRefused(
                "Live canary authorization or current broker truth is unavailable"
            ) from error

        if not isinstance(state, LiveAuthorizationState):
            raise ExecutionRefused("Live canary authorization state is invalid")
        if not isinstance(durable_state, LiveSafetyRecord):
            raise ExecutionRefused("Live durable safety state is invalid")
        if durable_state.recovery_latch.is_required:
            raise ExecutionRefused(
                "Live recovery requires fresh broker reconciliation and operator confirmation"
            )
        if (
            state.authorization != durable_state.authorization
            or state.kill_latch != durable_state.kill_latch
            or state.recovery_latch != durable_state.recovery_latch
        ):
            raise ExecutionRefused("Live canary state differs from locked durable safety state")
        if not isinstance(proof, LiveStartupProof):
            raise ExecutionRefused("Live startup proof is invalid")
        if not proof.broker_connected:
            self._require_recovery(safety_lease, "Live broker disconnected during execution check")
            raise ExecutionRefused("Live broker is disconnected; reconciliation is required")
        try:
            truth = self._truth.snapshot()
        except Exception as error:
            self._require_recovery(safety_lease, "Live broker truth unavailable during execution check")
            raise ExecutionRefused("Live broker truth is unavailable; reconciliation is required") from error
        if not isinstance(truth, LiveCanaryTruth):
            raise ExecutionRefused("Live account truth is invalid")
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ExecutionRefused("Live canary clock is unavailable")
        authorization = state.authorization
        if authorization is None:
            raise ExecutionRefused("Live canary operator authorization is missing")
        fingerprint = authorization.expected_account_fingerprint
        if (
            proof.account_fingerprint != fingerprint
            or truth.account_fingerprint != fingerprint
            or proof.endpoint_identity != "ibkr-live-loopback:127.0.0.1:4001"
        ):
            raise ExecutionRefused("Live canary account or endpoint does not match authorization")
        if not self._proof_facts_are_fresh(proof, truth, now):
            raise ExecutionRefused("Live startup proof or broker truth is stale or incomplete")
        limits = authorization.approved_canary_limits
        normalized_symbol = intent.execution_symbol.strip().upper()
        if type(intent.quantity) is not int or intent.quantity <= 0:
            raise ExecutionRefused("Live canary requires a positive whole-share quantity")
        if not intent.limit_price.is_finite() or intent.limit_price <= 0:
            raise ExecutionRefused("Live canary requires a finite positive LMT price")
        if intent.side not in {Side.BUY, Side.SELL}:
            raise ExecutionRefused("Live canary supports BUY and reducing SELL only")

        broker_open_ids = {order.broker_order_id for order in truth.open_orders}
        broker_reserved_sells = sum(
            order.remaining_quantity
            for order in truth.open_orders
            if order.side is Side.SELL
            and order.symbol.strip().upper() == normalized_symbol
        )
        local_unrepresented_sells = sum(
            active_intent.quantity
            for broker_id, (active_intent, _) in self._active.items()
            if broker_id != own_reservation
            and broker_id not in broker_open_ids
            and active_intent.side is Side.SELL
            and active_intent.execution_symbol.strip().upper() == normalized_symbol
        )
        reserved_sells = broker_reserved_sells + local_unrepresented_sells
        owned = truth.quantity_for(normalized_symbol)
        reducing = intent.side is Side.SELL and intent.quantity <= owned - reserved_sells
        if intent.side is Side.SELL and not reducing:
            raise ExecutionRefused("Live canary SELL exceeds confirmed unreserved long shares")

        emergency_exit = reducing and state.kill_latch.is_latched
        if not emergency_exit:
            if proof.authorization_fingerprint != authorization.authorization_fingerprint:
                raise ExecutionRefused("Live startup proof belongs to an older authorization")
            if proof.kill_latched != state.kill_latch.is_latched:
                raise ExecutionRefused("Live kill state changed after startup proof")
            if not authorization.is_valid_at(now):
                raise ExecutionRefused("Live canary authorization is expired or revoked")
            if (
                not state.session_armed
                or state.session_arm_id is None
                or proof.session_arm_id != state.session_arm_id
            ):
                raise ExecutionRefused("Live canary session requires explicit operator arm")
            if state.kill_latch.is_latched:
                raise ExecutionRefused("Live kill latch blocks exposure increase")
            if (
                state.session_arm_revision != durable_state.revision
                or proof.session_arm_revision != durable_state.revision
            ):
                raise ExecutionRefused(
                    "Live canary safety revision changed; explicit re-arm and startup proof required"
                )
            if limits.blockers():
                raise ExecutionRefused("Live canary limits are invalid or zero")
            if (
                normalized_symbol
                not in {symbol.strip().upper() for symbol in limits.allowed_symbols}
                or intent.strategy_version_id not in authorization.approved_strategy_version_ids
                or intent.strategy_version_id not in limits.allowed_strategy_versions
            ):
                raise ExecutionRefused("Live canary symbol or strategy is not authorized")

            notional = Decimal(intent.quantity) * intent.limit_price
            if notional > limits.max_order_notional:
                raise ExecutionRefused("Live canary order exceeds max_order_notional")
            broker_open_ids = {order.broker_order_id for order in truth.open_orders}
            unrepresented_active = {
                broker_id
                for broker_id in self._active
                if broker_id != own_reservation and broker_id not in broker_open_ids
            }
            active_count = len(unrepresented_active)
            if truth.open_order_count + active_count >= limits.max_open_orders:
                raise ExecutionRefused("Live canary open-order limit would be exceeded")
            if intent.side is Side.SELL:
                return

            active_buys = [
                active_intent
                for broker_id, (active_intent, _) in self._active.items()
                if broker_id in unrepresented_active and active_intent.side is Side.BUY
            ]
            reserved_notional = sum(
                (Decimal(active_intent.quantity) * active_intent.limit_price
                 for active_intent in active_buys),
                Decimal("0"),
            )
            exposure_cap = min(limits.capital_limit, truth.net_liquidation)
            if (
                truth.gross_exposure
                + truth.open_buy_notional
                + reserved_notional
                + notional
                > exposure_cap
            ):
                raise ExecutionRefused("Live canary capital limit would be exceeded")
            daily_loss = max(Decimal("0"), -truth.daily_pnl)
            if daily_loss >= limits.max_daily_loss:
                raise ExecutionRefused("Live canary daily loss limit is reached")
            active_symbols = {
                order.symbol.strip().upper()
                for order in truth.open_orders
                if order.side is Side.BUY
            } | {
                active_intent.execution_symbol.strip().upper()
                for broker_id, (active_intent, _) in self._active.items()
                if broker_id in unrepresented_active and active_intent.side is Side.BUY
            }
            held_symbols = {
                position.symbol.strip().upper()
                for position in truth.positions
                if position.quantity > 0
            }
            projected_positions = truth.position_count + sum(
                symbol not in held_symbols for symbol in active_symbols
            )
            if (
                normalized_symbol not in held_symbols
                and normalized_symbol not in active_symbols
            ):
                projected_positions += 1
            if projected_positions > limits.max_positions:
                raise ExecutionRefused("Live canary position limit would be exceeded")

    def _require_recovery(self, safety_lease: object, reason: str) -> None:
        require = getattr(safety_lease, "require_reconciliation", None)
        if not callable(require):
            self._local_recovery_required = True
            raise ExecutionRefused("Live recovery barrier cannot be persisted")
        try:
            require(at=self._now(), reason=reason)
        except Exception as error:
            self._local_recovery_required = True
            raise ExecutionRefused("Live recovery barrier could not be persisted") from error
        self._recovery_recorded_this_check = True

    @staticmethod
    def _proof_facts_are_fresh(
        proof: LiveStartupProof,
        truth: LiveCanaryTruth,
        now: datetime,
    ) -> bool:
        if not (proof.observed_at <= now < proof.expires_at):
            return False
        if not (truth.observed_at <= now < truth.observed_at + LIVE_STARTUP_PROOF_TTL):
            return False
        required_facts = (
            proof.connection_observed_at,
            proof.account_truth_observed_at,
            proof.market_truth_observed_at,
            proof.open_orders_observed_at,
            proof.positions_observed_at,
            proof.reconciliation_observed_at,
        )
        if any(timestamp is None for timestamp in required_facts):
            return False
        return all(
            timestamp <= now < timestamp + LIVE_STARTUP_PROOF_TTL
            for timestamp in required_facts
            if timestamp is not None
        ) and all(
            (
                proof.broker_connected,
                proof.account_truth_known,
                proof.market_truth_known,
                proof.open_orders_known,
                proof.positions_known,
                proof.reconciliation_clean,
            )
        )

__all__ = ["LiveCanaryExecutionGuard"]
