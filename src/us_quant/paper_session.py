"""Paper-session coordination without UI or broker implementation imports.

The coordinator owns the ordering between an armed Paper order port and an
already-started engine.  It deliberately does not create, connect, submit,
reconnect, resubmit, or disconnect anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Callable, Mapping, Protocol, Sequence
from uuid import uuid4

from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    Side,
)
from us_quant.trading.ports.broker_execution import (
    ExecutionSubmissionUncertain,
)


class PendingOrder(Protocol):
    order_id: str
    side: Side
    created_at: datetime


class EngineSnapshot(Protocol):
    session_id: str | None
    active: bool
    positions: Sequence[object]
    pending_orders: Sequence[PendingOrder]
    entries_paused: bool
    stop_requested: bool


class PaperEngine(Protocol):
    config: object

    def snapshot(self, *, observed_at: datetime | None = None) -> EngineSnapshot: ...
    def on_stream(self, snapshot: object, *, observed_at: datetime | None = None) -> EngineSnapshot: ...
    def on_execution(self, execution: ExecutionFill) -> EngineSnapshot: ...
    def on_order_event(self, event: OrderEvent) -> EngineSnapshot: ...
    def pause_entries(self) -> EngineSnapshot: ...
    def resume_entries(self) -> EngineSnapshot: ...
    def request_stop(self) -> EngineSnapshot: ...
    def halt_for_reconciliation(self, reason: str) -> EngineSnapshot: ...
    def resume_from_reconciliation(
        self, *, session_id: str, allow_force_flat_exit: bool = True
    ) -> EngineSnapshot: ...


class PaperOrderPort(Protocol):
    """The provider-neutral execution surface this coordinator sequences.

    The event methods speak the domain's order types.  The connection,
    account and reconciliation reads stay on the same object because they
    describe the *session* the events belong to, and splitting them would let
    the coordinator compare one channel's events against another's snapshot.
    """

    def fills(self) -> Sequence[ExecutionFill]: ...
    def events(self) -> Sequence[OrderEvent]: ...
    def cancel_intent(self, order_id: str) -> bool: ...
    def connection_snapshot(self) -> object: ...
    def broker_state(self) -> object: ...
    def reconciliation_rows_with_latency(
        self, *, session_id: str | None, limit: int
    ) -> Sequence[object]: ...
    def reconciliation_summary(self, session_id: str) -> object: ...
    def refresh_reconciliation_snapshot(self, session_id: str) -> object: ...
    def reconciliation_snapshot_is_current(self, snapshot: object) -> bool: ...

    def armed_account_binding_is_valid(self) -> bool: ...
    def armed_account_fingerprint(self) -> str: ...


class PaperHealth(Protocol):
    safe_to_continue: bool
    status: str


HealthEvaluator = Callable[..., PaperHealth]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class PaperSessionEvent:
    code: str
    severity: str
    message: str


@dataclass(frozen=True, slots=True)
class PaperSessionState:
    active: bool
    entries_paused: bool
    stop_requested: bool
    halted: bool
    finalized: bool
    local_position_count: int
    pending_order_count: int
    broker_open_order_count: int
    broker_position_count: int
    unreconciled_order_count: int
    health_status: str | None


@dataclass(frozen=True, slots=True)
class PaperSessionResult:
    state: PaperSessionState
    engine_snapshot: EngineSnapshot
    health: PaperHealth | None
    events: tuple[PaperSessionEvent, ...]


@dataclass(frozen=True, slots=True)
class CoordinatorReconciliationEvidence:
    evidence_id: str
    runtime_id: str
    service_identity: int
    session_id: str
    account_fingerprint: str
    armed_account_fingerprint: str
    connection_generation: int
    reconciliation_generation: int
    state_version: int
    captured_at: str
    expires_at: str
    broker_digest: str
    engine_digest: str
    health_status: str
    safe_to_continue: bool


@dataclass(frozen=True, slots=True)
class CoordinatorFinalizationEvidence:
    evidence_id: str
    runtime_id: str
    service_identity: int
    session_id: str
    connection_generation: int
    captured_at: str
    broker_digest: str
    engine_digest: str


class PaperSessionCoordinator:
    """Coordinates a single, already-armed Paper session through ports.

    ``health_evaluator`` is injected so this module remains independent of a
    specific broker representation.  A future adapter may normalize broker
    reconciliation rows before passing them to its evaluator.
    """

    def __init__(
        self,
        *,
        engine: PaperEngine,
        orders: PaperOrderPort,
        health_evaluator: HealthEvaluator,
        candidate_symbols: frozenset[str],
        clock: Clock | None = None,
    ) -> None:
        self._engine = engine
        self._orders = orders
        self._runtime_id = uuid4().hex
        self._health_evaluator = health_evaluator
        self._candidate_symbols = candidate_symbols
        self._clock = clock or _utc_now
        self._engine_snapshot = engine.snapshot()
        self._health: PaperHealth | None = None
        self._halted = False
        self._finalized = False
        self._finalization_proven = False
        self._consumed_evidence_ids: set[str] = set()
        self._last_result = self._result(())

    @property
    def result(self) -> PaperSessionResult:
        """Return the immutable result produced by the latest operation."""
        return self._last_result

    def snapshot(self) -> PaperSessionResult:
        """Read-only alias for the latest immutable session result."""
        return self._last_result

    def capture_reconciliation_evidence(
        self, *, now: datetime | None = None
    ) -> tuple[PaperSessionResult, CoordinatorReconciliationEvidence | None]:
        """Refresh complete broker truth and publish evidence only if healthy."""

        if not self._halted:
            raise RuntimeError("Reconciliation evidence requires a halted session.")
        session_id = self._engine_snapshot.session_id
        if not session_id:
            raise RuntimeError("A halted Paper session requires a session ID.")
        if not self._armed_account_binding_is_valid():
            return self._store(()), None
        observed_at = _utc(now or self._clock())
        broker_snapshot = self._orders.refresh_reconciliation_snapshot(session_id)
        self._drain_broker_events()
        events: list[PaperSessionEvent] = []
        self._evaluate_health(observed_at, events)
        result = self._store(events)
        if not self._proof_is_safe_and_current(broker_snapshot):
            return result, None
        evidence = self._build_reconciliation_evidence(
            broker_snapshot, observed_at=observed_at
        )
        return result, evidence

    def confirm_reconciliation(
        self,
        evidence: CoordinatorReconciliationEvidence,
        *,
        now: datetime | None = None,
    ) -> "PaperSessionCoordinator":
        """Revalidate and resume once; never refresh evidence implicitly."""

        observed_at = _utc(now or self._clock())
        self._validate_evidence_identity(evidence, observed_at)
        if evidence.evidence_id in self._consumed_evidence_ids:
            raise RuntimeError("Reconciliation evidence was already consumed.")
        # Consume before the second broker refresh.  A failed or ambiguous
        # confirmation must require a new human reconciliation attempt.
        self._consumed_evidence_ids.add(evidence.evidence_id)
        if not self._armed_account_binding_is_valid():
            raise RuntimeError(
                "Paper session account changed before reconciliation confirmation."
            )
        if self._armed_account_fingerprint() != evidence.armed_account_fingerprint:
            raise RuntimeError(
                "Paper session armed-account identity changed before confirmation."
            )
        session_id = self._engine_snapshot.session_id
        assert session_id is not None
        broker_snapshot = self._orders.refresh_reconciliation_snapshot(session_id)
        self._drain_broker_events()
        events: list[PaperSessionEvent] = []
        self._evaluate_health(observed_at, events)
        self._store(events)
        if not self._proof_is_safe_and_current(broker_snapshot):
            raise RuntimeError("Fresh reconciliation evidence is no longer safe.")
        current = self._build_reconciliation_evidence(
            broker_snapshot, observed_at=observed_at
        )
        if (
            current.account_fingerprint != evidence.account_fingerprint
            or current.armed_account_fingerprint
            != evidence.armed_account_fingerprint
            or current.connection_generation != evidence.connection_generation
            or current.broker_digest != evidence.broker_digest
            or current.engine_digest != evidence.engine_digest
        ):
            raise RuntimeError("Reconciliation evidence changed before confirmation.")
        self._engine.resume_from_reconciliation(
            session_id=session_id, allow_force_flat_exit=True
        )
        return PaperSessionCoordinator(
            engine=self._engine,
            orders=self._orders,
            health_evaluator=self._health_evaluator,
            candidate_symbols=self._candidate_symbols,
            clock=self._clock,
        )

    def capture_finalization_evidence(
        self, *, now: datetime | None = None
    ) -> tuple[PaperSessionResult, CoordinatorFinalizationEvidence | None]:
        """Capture coherent zero-state proof before the service disconnects."""

        observed_at = _utc(now or self._clock())
        if self._engine_snapshot.active:
            return self._store(()), None
        if self._engine_snapshot.positions or self._engine_snapshot.pending_orders:
            return self._store(()), None
        session_id = self._engine_snapshot.session_id
        if not session_id:
            raise RuntimeError("Paper finalization requires a session ID.")
        events: list[PaperSessionEvent] = []
        try:
            broker_snapshot = self._orders.refresh_reconciliation_snapshot(session_id)
        except Exception:
            self._halt("PAPER_FINALIZATION_REFRESH_FAILED", events)
            return self._store(events), None
        self._drain_broker_events()
        self._evaluate_health(observed_at, events)
        result = self._store(events)
        summary = getattr(broker_snapshot, "reconciliation_summary", None)
        clear = bool(
            self._proof_is_safe_and_current(broker_snapshot)
            and not self._engine_snapshot.active
            and not self._engine_snapshot.positions
            and not self._engine_snapshot.pending_orders
            and not getattr(broker_snapshot, "broker_positions", ())
            and not getattr(broker_snapshot, "open_broker_orders", ())
            and int(getattr(summary, "unreconciled", 1)) == 0
        )
        if not clear:
            return result, None
        evidence = CoordinatorFinalizationEvidence(
            evidence_id=uuid4().hex,
            runtime_id=self._runtime_id,
            service_identity=id(self._orders),
            session_id=session_id,
            connection_generation=int(
                getattr(broker_snapshot, "connection_generation", 0)
            ),
            captured_at=observed_at.isoformat(),
            broker_digest=str(getattr(broker_snapshot, "digest", "")),
            engine_digest=_engine_snapshot_digest(self._engine_snapshot),
        )
        return result, evidence

    def confirm_finalization_after_disconnect(
        self, evidence: CoordinatorFinalizationEvidence
    ) -> PaperSessionResult:
        """Finalize only after callback shutdown and a final local/journal drain."""

        if evidence.evidence_id in self._consumed_evidence_ids:
            raise RuntimeError("Finalization evidence was already consumed.")
        self._consumed_evidence_ids.add(evidence.evidence_id)
        if (
            evidence.runtime_id != self._runtime_id
            or evidence.service_identity != id(self._orders)
            or evidence.session_id != self._engine_snapshot.session_id
        ):
            raise RuntimeError("Finalization evidence identity mismatch.")
        if bool(getattr(self._orders.connection_snapshot(), "connected", True)):
            raise RuntimeError("Paper service must be disconnected before finalization.")
        self._drain_broker_events()
        session_id = self._engine_snapshot.session_id or ""
        summary = self._orders.reconciliation_summary(session_id)
        clear = bool(
            not self._engine_snapshot.active
            and not self._engine_snapshot.positions
            and not self._engine_snapshot.pending_orders
            and int(getattr(summary, "unreconciled", 1)) == 0
            and _engine_snapshot_digest(self._engine_snapshot)
            == evidence.engine_digest
        )
        if not clear:
            events: list[PaperSessionEvent] = []
            self._halt("PAPER_FINALIZATION_CHANGED", events)
            return self._store(events)
        self._finalization_proven = True
        return self._store(
            (PaperSessionEvent("PAPER_FINALIZED", "info", "PAPER_FINALIZED"),)
        )

    def poll(self, now: datetime | None = None) -> PaperSessionResult:
        """Drain broker events, then refresh execution health.

        Executions must be applied before order updates because an update can
        be terminal while fills for that order are still pending locally.
        """
        observed_at = _utc(now or self._clock())
        events: list[PaperSessionEvent] = []
        self._drain_broker_events()
        self._cancel_stale_buys(observed_at, force=False, events=events)
        self._require_exit_intervention(observed_at, events)
        if self._halted:
            return self._store(events)
        self._require_engine_active(events)
        self._evaluate_health(observed_at, events)
        return self._store(events)

    def on_stream(self, snapshot: object) -> PaperSessionResult:
        """Sequence one stream event before strategy evaluation.

        The coordinator is the sole runtime sequencer: executions, order
        updates, timed-out BUY cancellation, health, then the strategy signal.
        """
        observed_at = _utc(self._clock())
        events: list[PaperSessionEvent] = []
        self._drain_broker_events()
        self._cancel_stale_buys(observed_at, force=False, events=events)
        self._require_exit_intervention(observed_at, events)
        if self._halted:
            return self._store(events)
        self._require_engine_active(events)
        self._evaluate_health(observed_at, events)
        if not self._halted and self._engine_snapshot.active:
            self._engine_snapshot = self._engine.on_stream(
                snapshot, observed_at=observed_at
            )
        return self._store(events)

    def _drain_broker_events(self) -> None:
        for fill in self._orders.fills():
            self._engine_snapshot = self._engine.on_execution(fill)
        for event in self._orders.events():
            self._engine_snapshot = self._engine.on_order_event(event)

    def set_entries_paused(self, paused: bool) -> PaperSessionResult:
        events: list[PaperSessionEvent] = []
        if paused:
            self._engine_snapshot = self._engine.pause_entries()
            events.append(PaperSessionEvent("ENTRIES_PAUSED", "info", "ENTRIES_PAUSED"))
            self._cancel_stale_buys(
                _utc(self._clock()), force=True, events=events
            )
        elif not self._halted:
            self._engine_snapshot = self._engine.resume_entries()
            events.append(PaperSessionEvent("ENTRIES_RESUMED", "info", "ENTRIES_RESUMED"))
        else:
            events.append(PaperSessionEvent("RESUME_BLOCKED", "warning", "RESUME_BLOCKED"))
        return self._store(events)

    def request_stop(
        self, stream_snapshot: object | None = None
    ) -> PaperSessionResult:
        """Request an orderly stop and return, rather than perform, teardown."""
        events: list[PaperSessionEvent] = [
            PaperSessionEvent("STOP_REQUESTED", "info", "STOP_REQUESTED")
        ]
        self._engine_snapshot = self._engine.request_stop()
        observed_at = _utc(self._clock())
        self._cancel_stale_buys(observed_at, force=True, events=events)
        self._require_exit_intervention(observed_at, events)
        if stream_snapshot is not None:
            self._evaluate_health(observed_at, events)
            if not self._halted and self._engine_snapshot.active:
                self._engine_snapshot = self._engine.on_stream(
                    stream_snapshot, observed_at=observed_at
                )
        return self._store(events)

    def _cancel_stale_buys(
        self,
        now: datetime,
        *,
        force: bool,
        events: list[PaperSessionEvent],
    ) -> None:
        timeout = getattr(self._engine.config, "entry_order_timeout_seconds")
        for intent in tuple(self._engine_snapshot.pending_orders):
            if intent.side is not Side.BUY:
                continue
            if not force and _age_seconds(intent.created_at, now) < timeout:
                continue
            try:
                requested = self._orders.cancel_intent(intent.order_id)
            except ExecutionSubmissionUncertain:
                self._halt("PAPER_CANCEL_UNCERTAIN", events)
                return
            except Exception:
                self._halt("PAPER_CANCEL_BLOCKED", events)
                return
            if requested:
                events.append(
                    PaperSessionEvent(
                        "PAPER_ENTRY_CANCEL_REQUESTED", "warning",
                        "PAPER_ENTRY_CANCEL_REQUESTED",
                    )
                )

    def _require_exit_intervention(
        self, now: datetime, events: list[PaperSessionEvent]
    ) -> None:
        """Halt on an aged SELL without cancelling the protective exit."""

        timeout = float(
            getattr(self._engine.config, "exit_order_intervention_seconds", 90)
        )
        for intent in tuple(self._engine_snapshot.pending_orders):
            if intent.side is not Side.SELL:
                continue
            if _age_seconds(intent.created_at, now) < timeout:
                continue
            self._halt("PAPER_EXIT_INTERVENTION_REQUIRED", events)
            return

    def _require_engine_active(
        self, events: list[PaperSessionEvent]
    ) -> None:
        """Fail closed when the engine stopped itself without a stop request.

        The engine can halt itself (rejected order, invalid execution,
        cross-day residue, uncertain submission) while the workflow phase is
        still RUNNING/PAUSED.  That would leave a "zombie" session that never
        evaluates risk exits or reconciles.  Only a coordinator-initiated halt
        (``halt_for_reconciliation`` sets ``stop_requested``) or an explicit
        stop request may leave the engine inactive without triggering this.
        """

        if self._halted:
            return
        if self._engine_snapshot.active:
            return
        if getattr(self._engine_snapshot, "stop_requested", False):
            return
        self._halt("PAPER_ENGINE_STOPPED", events)

    def _evaluate_health(
        self, now: datetime, events: list[PaperSessionEvent]
    ) -> None:
        rows = tuple(self._orders.reconciliation_rows_with_latency(
            session_id=self._engine_snapshot.session_id, limit=200
        ))
        session_id = self._engine_snapshot.session_id or ""
        summary = self._orders.reconciliation_summary(session_id)
        self._health = self._health_evaluator(
            connection=self._orders.connection_snapshot(),
            broker_state=self._orders.broker_state(),
            engine_snapshot=self._engine_snapshot,
            reconciliations=rows,
            reconciliation_summary=summary,
            candidate_symbols=self._candidate_symbols,
            now=now,
        )
        if not self._health.safe_to_continue:
            self._halt("PAPER_RECONCILIATION_HALT", events)

    def _halt(self, code: str, events: list[PaperSessionEvent]) -> None:
        if not self._halted:
            self._engine_snapshot = self._engine.halt_for_reconciliation(code)
            self._halted = True
        events.append(PaperSessionEvent(code, "error", code))

    def _store(
        self, events: Sequence[PaperSessionEvent]
    ) -> PaperSessionResult:
        self._last_result = self._result(tuple(events))
        return self._last_result

    def _result(
        self, events: tuple[PaperSessionEvent, ...]
    ) -> PaperSessionResult:
        rows = tuple(self._orders.reconciliation_rows_with_latency(
            session_id=self._engine_snapshot.session_id, limit=200
        ))
        session_id = self._engine_snapshot.session_id or ""
        summary = self._orders.reconciliation_summary(session_id)
        connection = self._orders.connection_snapshot()
        broker_open_order_count = int(
            getattr(connection, "open_broker_orders", 0) or 0
        )
        broker_state = self._orders.broker_state()
        broker_positions = tuple(getattr(broker_state, "positions", ()))
        broker_position_count = sum(
            1 for position in broker_positions
            if getattr(position, "quantity", 0) != 0
        )
        unreconciled = int(
            getattr(
                summary,
                "unreconciled",
                sum(1 for row in rows if not _value(row, "reconciled", False)),
            )
        )
        candidate_finalized = bool(
            not self._engine_snapshot.active
            and not self._engine_snapshot.positions
            and not self._engine_snapshot.pending_orders
            and broker_open_order_count == 0
            and broker_position_count == 0
            and unreconciled == 0
        )
        self._finalized = bool(self._finalization_proven and candidate_finalized)
        state = PaperSessionState(
            active=self._engine_snapshot.active,
            entries_paused=getattr(self._engine_snapshot, "entries_paused", False),
            stop_requested=getattr(self._engine_snapshot, "stop_requested", False),
            halted=self._halted,
            finalized=self._finalized,
            local_position_count=len(self._engine_snapshot.positions),
            pending_order_count=len(self._engine_snapshot.pending_orders),
            broker_open_order_count=broker_open_order_count,
            broker_position_count=broker_position_count,
            unreconciled_order_count=unreconciled,
            health_status=(getattr(self._health, "status", None) if self._health else None),
        )
        return PaperSessionResult(state, self._engine_snapshot, self._health, events)

    def _proof_is_safe_and_current(self, broker_snapshot: object) -> bool:
        return bool(
            getattr(broker_snapshot, "snapshot_complete", False)
            and self._armed_account_binding_is_valid()
            and self._health is not None
            and self._health.safe_to_continue
            and getattr(self._health, "status", "") == "HEALTHY"
            and self._orders.reconciliation_snapshot_is_current(broker_snapshot)
        )

    def _armed_account_binding_is_valid(self) -> bool:
        """Use the concrete service guard when present; preserve port fakes."""

        check = getattr(self._orders, "armed_account_binding_is_valid", None)
        return True if not callable(check) else bool(check())

    def _armed_account_fingerprint(self) -> str:
        """Read the service-owned armed identity without exposing an account ID."""

        read = getattr(self._orders, "armed_account_fingerprint", None)
        if callable(read):
            return str(read())
        return "legacy-port"

    def _build_reconciliation_evidence(
        self, broker_snapshot: object, *, observed_at: datetime
    ) -> CoordinatorReconciliationEvidence:
        session_id = self._engine_snapshot.session_id
        if not session_id:
            raise RuntimeError("Reconciliation evidence has no session ID.")
        return CoordinatorReconciliationEvidence(
            evidence_id=uuid4().hex,
            runtime_id=self._runtime_id,
            service_identity=id(self._orders),
            session_id=session_id,
            account_fingerprint=str(
                getattr(broker_snapshot, "account_fingerprint", "")
            ),
            armed_account_fingerprint=self._armed_account_fingerprint(),
            connection_generation=int(
                getattr(broker_snapshot, "connection_generation", 0)
            ),
            reconciliation_generation=int(
                getattr(broker_snapshot, "reconciliation_generation", 0)
            ),
            state_version=int(getattr(broker_snapshot, "state_version", 0)),
            captured_at=observed_at.isoformat(),
            expires_at=(observed_at + timedelta(seconds=30)).isoformat(),
            broker_digest=str(getattr(broker_snapshot, "digest", "")),
            engine_digest=_engine_snapshot_digest(self._engine_snapshot),
            health_status=str(getattr(self._health, "status", "")),
            safe_to_continue=bool(
                self._health and self._health.safe_to_continue
            ),
        )

    def _validate_evidence_identity(
        self,
        evidence: CoordinatorReconciliationEvidence,
        observed_at: datetime,
    ) -> None:
        if not self._halted:
            raise RuntimeError("Only a halted session can consume reconciliation evidence.")
        if evidence.runtime_id != self._runtime_id:
            raise RuntimeError("Reconciliation evidence belongs to another runtime.")
        if evidence.service_identity != id(self._orders):
            raise RuntimeError("Reconciliation evidence belongs to another service.")
        if evidence.session_id != self._engine_snapshot.session_id:
            raise RuntimeError("Reconciliation evidence belongs to another session.")
        expires_at = datetime.fromisoformat(evidence.expires_at.replace("Z", "+00:00"))
        if observed_at > _utc(expires_at):
            raise RuntimeError("Reconciliation evidence has expired.")
        if not evidence.safe_to_continue or evidence.health_status != "HEALTHY":
            raise RuntimeError("Reconciliation evidence is not healthy.")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(timezone.utc)


def _age_seconds(created_at: datetime, now: datetime) -> float:
    try:
        return max(0.0, (now - _utc(created_at)).total_seconds())
    except (AttributeError, TypeError, ValueError):
        return float("inf")


def _value(row: object, key: str, default: object) -> object:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _engine_snapshot_digest(snapshot: EngineSnapshot) -> str:
    """Hash exact local execution state without relying on display counts."""

    positions = [
        {
            "symbol": str(_value(row, "symbol", "")),
            "quantity": str(_value(row, "quantity", "")),
            "average_price": str(
                _value(row, "average_price", _value(row, "average_cost", ""))
            ),
        }
        for row in snapshot.positions
    ]
    pending = [
        {
            "order_id": str(_value(row, "order_id", "")),
            "symbol": str(_value(row, "execution_symbol", "")),
            "side": str(_value(row, "side", "")),
            "quantity": str(_value(row, "quantity", "")),
            "limit_price": str(_value(row, "limit_price", "")),
        }
        for row in snapshot.pending_orders
    ]
    payload = {
        "session_id": snapshot.session_id,
        "positions": sorted(positions, key=lambda row: (row["symbol"], row["quantity"])),
        "pending_orders": sorted(
            pending, key=lambda row: (row["order_id"], row["symbol"])
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return sha256(encoded).hexdigest()
