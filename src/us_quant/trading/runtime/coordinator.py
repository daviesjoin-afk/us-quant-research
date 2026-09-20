"""Sequencing for one already-armed Paper session.

The coordinator owns the *order* of a tick and nothing else.  It does not create,
connect, submit, reconnect, resubmit or disconnect anything: it drains what the
broker channel already produced, enforces the timing rules that keep a session
honest (a stale BUY is cancelled, an aged protective SELL is not), asks the
injected health evaluator whether the session may continue, and only then lets
the trading runtime see the market stream.

Two orderings here are load-bearing:

* executions are applied before order updates, because a terminal broker status
  can be consumed before the local fill queue has been drained, and applying the
  update first makes the pending/fill reconciliation see a false conflict;
* a session that just halted never reaches the strategy, because the engine can
  halt itself (rejected order, invalid execution, cross-day residue, uncertain
  submission) while the workflow phase is still RUNNING.

The halted and stopping flows -- the ones a human drives after that -- are mixed
in from ``recovery``; they share this class's state and primitives rather than
keeping a second copy of either.  Proofs are built and checked in
``reconciliation``, and the immutable result is projected in ``paper_models``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from typing import Sequence
from uuid import uuid4

from us_quant.trading.domain.orders import Side
from us_quant.trading.ports.broker_execution import (
    ExecutionSubmissionUncertain,
)
from us_quant.trading.runtime.paper_contracts import (
    Clock,
    EngineSnapshot,
    HealthEvaluator,
    PaperEngine,
    PaperHealth,
    PaperOrderPort,
)
from us_quant.trading.runtime.paper_models import (
    PaperSessionEvent,
    PaperSessionResult,
    PaperSessionState,
)
from us_quant.trading.runtime.reconciliation import (
    CoordinatorReconciliationEvidence,
    EvidenceBinding,
    OneShotEvidence,
    build_reconciliation_evidence,
    engine_snapshot_digest,
    proof_is_safe_and_current,
    row_value,
    utc,
)
from us_quant.trading.runtime.recovery import SessionRecovery


class PaperSessionCoordinator(SessionRecovery):
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
        self._engine_snapshot: EngineSnapshot = engine.snapshot()
        self._health: PaperHealth | None = None
        self._halted = False
        self._finalized = False
        self._finalization_proven = False
        self._consumed = OneShotEvidence()
        self._last_result = self._result(())

    @property
    def result(self) -> PaperSessionResult:
        """Return the immutable result produced by the latest operation."""

        return self._last_result

    def snapshot(self) -> PaperSessionResult:
        """Read-only alias for the latest immutable session result."""

        return self._last_result

    def poll(self, now: datetime | None = None) -> PaperSessionResult:
        """Drain broker events, then refresh execution health.

        Executions must be applied before order updates because an update can
        be terminal while fills for that order are still pending locally.
        """

        observed_at = utc(now or self._clock())
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

        observed_at = utc(self._clock())
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
            self._cancel_stale_buys(utc(self._clock()), force=True, events=events)
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
        observed_at = utc(self._clock())
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
                requested = self._orders.cancel(intent.order_id)
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
        """Project the current engine and broker facts into a frozen result."""

        rows = tuple(self._orders.reconciliation_rows_with_latency(
            session_id=self._engine_snapshot.session_id, limit=200
        ))
        summary = self._orders.reconciliation_summary(
            self._engine_snapshot.session_id or ""
        )
        connection = self._orders.connection_snapshot()
        broker_open_order_count = int(
            getattr(connection, "open_broker_orders", 0) or 0
        )
        broker_positions = tuple(
            getattr(self._orders.broker_state(), "positions", ())
        )
        broker_position_count = sum(
            1 for position in broker_positions
            if getattr(position, "quantity", 0) != 0
        )
        unreconciled = int(
            getattr(
                summary,
                "unreconciled",
                sum(1 for row in rows if not row_value(row, "reconciled", False)),
            )
        )
        snapshot = self._engine_snapshot
        self._finalized = bool(
            self._finalization_proven
            and not snapshot.active
            and not snapshot.positions
            and not snapshot.pending_orders
            and broker_open_order_count == 0
            and broker_position_count == 0
            and unreconciled == 0
        )
        state = PaperSessionState(
            active=snapshot.active,
            entries_paused=getattr(snapshot, "entries_paused", False),
            stop_requested=getattr(snapshot, "stop_requested", False),
            halted=self._halted,
            finalized=self._finalized,
            local_position_count=len(snapshot.positions),
            pending_order_count=len(snapshot.pending_orders),
            broker_open_order_count=broker_open_order_count,
            broker_position_count=broker_position_count,
            unreconciled_order_count=unreconciled,
            health_status=(getattr(self._health, "status", None) if self._health else None),
        )
        return PaperSessionResult(state, snapshot, self._health, events)

    def _binding(self) -> EvidenceBinding:
        """The identity any proof issued by this coordinator must carry."""

        return EvidenceBinding(
            runtime_id=self._runtime_id,
            service_identity=id(self._orders),
            session_id=self._engine_snapshot.session_id,
        )

    def _evidence(
        self, broker_snapshot: object, observed_at: datetime
    ) -> CoordinatorReconciliationEvidence:
        return build_reconciliation_evidence(
            binding=self._binding(),
            broker_snapshot=broker_snapshot,
            armed_account_fingerprint=self._armed_account_fingerprint(),
            health=self._health,
            engine_digest=engine_snapshot_digest(self._engine_snapshot),
            observed_at=observed_at,
        )

    def _proof_is_safe_and_current(self, broker_snapshot: object) -> bool:
        return proof_is_safe_and_current(
            broker_snapshot,
            health=self._health,
            armed_account_binding_is_valid=self._arming_is_valid(),
            snapshot_is_current=self._orders.reconciliation_snapshot_is_current(
                broker_snapshot
            ),
        )

    def _arming_is_valid(self) -> bool:
        """Use the concrete service guard when present; preserve port fakes."""

        check = getattr(self._orders, "armed_account_binding_is_valid", None)
        return True if not callable(check) else bool(check())

    def _armed_account_fingerprint(self) -> str:
        """Read the service-owned armed identity without exposing an account ID."""

        read = getattr(self._orders, "armed_account_fingerprint", None)
        if callable(read):
            return str(read())
        return "legacy-port"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _age_seconds(created_at: datetime, now: datetime) -> float:
    try:
        return max(0.0, (now - utc(created_at)).total_seconds())
    except (AttributeError, TypeError, ValueError):
        return float("inf")