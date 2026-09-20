from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from us_quant.trading.runtime.coordinator import PaperSessionCoordinator
from us_quant.trading.domain.orders import Side
from us_quant.trading.ports.broker_execution import (
    ExecutionSubmissionUncertain,
)


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Intent:
    """A pending order as the coordinator reads it: domain names and types."""

    order_id: str
    side: Side
    created_at: datetime


@dataclass(frozen=True)
class Position:
    quantity: int


@dataclass(frozen=True)
class Snapshot:
    session_id: str | None = "session"
    active: bool = True
    positions: tuple[Position, ...] = ()
    pending_orders: tuple[Intent, ...] = ()
    entries_paused: bool = False
    stop_requested: bool = False


@dataclass(frozen=True)
class Health:
    safe_to_continue: bool = True
    status: str = "HEALTHY"


@dataclass(frozen=True)
class BrokerState:
    positions: tuple[Position, ...] = ()


@dataclass(frozen=True)
class Connection:
    open_broker_orders: int = 0
    connected: bool = True


@dataclass(frozen=True)
class Summary:
    unreconciled: int = 0
    terminal_unreconciled: int = 0


@dataclass(frozen=True)
class ReconciliationSnapshot:
    account_fingerprint: str = "DU***17"
    connection_generation: int = 1
    reconciliation_generation: int = 1
    state_version: int = 1
    digest: str = "broker-digest"
    snapshot_complete: bool = True
    broker_positions: tuple[object, ...] = ()
    open_broker_orders: tuple[object, ...] = ()
    reconciliation_summary: Summary = Summary()


@dataclass(frozen=True)
class Row:
    reconciled: bool


class Config:
    entry_order_timeout_seconds = 30
    exit_order_intervention_seconds = 90


class FakeEngine:
    config = Config()

    def __init__(
        self, snapshot: Snapshot = Snapshot(), timeline: list[str] | None = None
    ) -> None:
        self.current = snapshot
        self.calls: list[str] = []
        self.timeline = timeline

    def _record(self, call: str) -> None:
        self.calls.append(call)
        if self.timeline is not None:
            self.timeline.append(call)

    def snapshot(self, *, observed_at=None):
        return self.current

    def on_execution(self, execution):
        self._record(f"execution:{execution}")
        return self.current

    def on_order_event(self, event):
        self._record(f"update:{event}")
        return self.current

    def on_stream(self, snapshot, *, observed_at=None):
        self._record("stream")
        return self.current

    def pause_entries(self):
        self._record("pause")
        self.current = Snapshot(**{**self.current.__dict__, "entries_paused": True})
        return self.current

    def resume_entries(self):
        self._record("resume")
        self.current = Snapshot(**{**self.current.__dict__, "entries_paused": False})
        return self.current

    def request_stop(self):
        self._record("stop")
        self.current = Snapshot(**{**self.current.__dict__, "active": False, "stop_requested": True, "entries_paused": True})
        return self.current

    def halt_for_reconciliation(self, reason):
        self._record(f"halt:{reason}")
        self.current = Snapshot(**{**self.current.__dict__, "active": False, "stop_requested": True, "entries_paused": True})
        return self.current

    def resume_from_reconciliation(self, *, session_id, allow_force_flat_exit=True):
        self._record("resume_from_reconciliation")
        self.current = Snapshot(
            **{
                **self.current.__dict__,
                "active": True,
                "stop_requested": False,
                "entries_paused": False,
            }
        )
        return self.current


class FakeOrders:
    def __init__(self, timeline: list[str] | None = None) -> None:
        self.executions = ("fill",)
        self.updates = ("update",)
        self.rows: tuple[Row, ...] = ()
        self.state = BrokerState()
        self.connection = Connection()
        self.calls: list[str] = []
        self.timeline = timeline
        self.cancel_error: Exception | None = None
        self.summary = Summary()
        self.reconciliation_snapshot = ReconciliationSnapshot()
        self.reconciliation_snapshot_current = True
        self.armed_account_binding_valid = True
        self.armed_account_fingerprint_value = "armed-account-a"

    def _record(self, call: str) -> None:
        self.calls.append(call)
        if self.timeline is not None:
            self.timeline.append(call)

    def fills(self):
        self._record("fills")
        return self.executions

    def events(self):
        self._record("events")
        return self.updates

    def cancel(self, order_id):
        self._record(f"cancel:{order_id}")
        if self.cancel_error:
            raise self.cancel_error
        return True

    def connection_snapshot(self):
        return self.connection

    def broker_state(self):
        return self.state

    def reconciliation_rows_with_latency(self, *, session_id, limit):
        return self.rows

    def reconciliation_summary(self, session_id):
        return self.summary

    def refresh_reconciliation_snapshot(self, session_id):
        self._record("refresh_reconciliation")
        return self.reconciliation_snapshot

    def reconciliation_snapshot_is_current(self, snapshot):
        return self.reconciliation_snapshot_current and snapshot == self.reconciliation_snapshot

    def armed_account_binding_is_valid(self):
        return self.armed_account_binding_valid

    def armed_account_fingerprint(self):
        return self.armed_account_fingerprint_value


def coordinator(
    engine: FakeEngine,
    orders: FakeOrders,
    health=Health(),
    timeline: list[str] | None = None,
):
    def evaluate(**kwargs):
        orders._record("health")
        return health

    return PaperSessionCoordinator(
        engine=engine,
        orders=orders,
        health_evaluator=evaluate,
        candidate_symbols=frozenset({"AAA"}),
        clock=lambda: NOW,
    )


def test_poll_applies_executions_before_updates_then_health() -> None:
    engine = FakeEngine()
    orders = FakeOrders()

    coordinator(engine, orders).poll()

    assert orders.calls == ["fills", "events", "health"]
    assert engine.calls == ["execution:fill", "update:update"]


def test_poll_halts_when_engine_stopped_itself_without_stop_request() -> None:
    """P0-2 regression: a self-stopped engine must fail the session closed
    instead of leaving a zombie RUNNING phase without risk management."""
    engine = FakeEngine(Snapshot(active=False, stop_requested=False))
    orders = FakeOrders()

    result = coordinator(engine, orders).poll()

    assert "halt:PAPER_ENGINE_STOPPED" in engine.calls
    assert result.state.halted
    assert any(
        event.code == "PAPER_ENGINE_STOPPED" for event in result.events
    )


def test_poll_does_not_halt_when_stop_was_requested() -> None:
    """An explicit stop request legitimately leaves the engine inactive."""
    engine = FakeEngine(Snapshot(active=False, stop_requested=True))
    orders = FakeOrders()

    result = coordinator(engine, orders).poll()

    assert "halt" not in engine.calls
    assert not result.state.halted


def test_poll_cancels_only_stale_buy_using_engine_timeout() -> None:
    stale = Intent("stale-buy", Side.BUY, NOW - timedelta(seconds=30))
    fresh = Intent("fresh-buy", Side.BUY, NOW - timedelta(seconds=29))
    sell = Intent("old-sell", Side.SELL, NOW - timedelta(minutes=2))
    engine = FakeEngine(Snapshot(pending_orders=(stale, fresh, sell)))
    orders = FakeOrders()

    result = coordinator(engine, orders).poll()

    assert "cancel:stale-buy" in orders.calls
    assert "cancel:fresh-buy" not in orders.calls
    assert "cancel:old-sell" not in orders.calls
    assert result.events[0].code == "PAPER_ENTRY_CANCEL_REQUESTED"


def test_aged_sell_halts_for_human_intervention_without_cancelling_exit() -> None:
    sell = Intent("aged-exit", Side.SELL, NOW - timedelta(seconds=90))
    engine = FakeEngine(Snapshot(pending_orders=(sell,)))
    orders = FakeOrders()

    result = coordinator(engine, orders).poll()

    assert result.state.halted
    assert "cancel:aged-exit" not in orders.calls
    assert any(
        event.code == "PAPER_EXIT_INTERVENTION_REQUIRED"
        for event in result.events
    )


def test_recent_sell_remains_live_and_is_never_cancelled() -> None:
    sell = Intent("fresh-exit", Side.SELL, NOW - timedelta(seconds=89))
    engine = FakeEngine(Snapshot(pending_orders=(sell,)))
    orders = FakeOrders()

    result = coordinator(engine, orders).poll()

    assert not result.state.halted
    assert "cancel:fresh-exit" not in orders.calls


def test_poll_does_not_cancel_stale_buy_cleared_by_queued_update() -> None:
    class UpdateClearsPendingEngine(FakeEngine):
        def on_order_event(self, event):
            self._record(f"update:{event}")
            self.current = Snapshot(
                **{**self.current.__dict__, "pending_orders": ()}
            )
            return self.current

    stale = Intent("stale-buy", Side.BUY, NOW - timedelta(minutes=1))
    engine = UpdateClearsPendingEngine(Snapshot(pending_orders=(stale,)))
    orders = FakeOrders()

    coordinator(engine, orders).poll()

    assert "cancel:stale-buy" not in orders.calls
    assert orders.calls == ["fills", "events", "health"]


def test_uncertain_stale_buy_cancel_halts_session() -> None:
    class CancelUncertainError(ExecutionSubmissionUncertain):
        """The provider-neutral uncertainty type, not a name that looks like it.

        The coordinator used to guess by sniffing the class name for
        "uncertain"; it now catches the port's type, so a cancel whose outcome
        cannot be established has to say so through that type.
        """

        def __init__(self) -> None:
            super().__init__(
                "cancel outcome unknown",
                order_id="buy",
                broker_order_id=1,
            )

    intent = Intent("buy", Side.BUY, NOW - timedelta(minutes=1))
    engine = FakeEngine(Snapshot(pending_orders=(intent,)))
    orders = FakeOrders()
    orders.cancel_error = CancelUncertainError()

    result = coordinator(engine, orders).poll()

    assert result.state.halted
    assert engine.calls[-1] == "halt:PAPER_CANCEL_UNCERTAIN"
    assert result.events[0].code == "PAPER_CANCEL_UNCERTAIN"


def test_unsafe_health_halts_before_stream_evaluation() -> None:
    timeline: list[str] = []
    engine = FakeEngine(timeline=timeline)
    orders = FakeOrders(timeline)

    result = coordinator(
        engine, orders, Health(False, "HALT"), timeline
    ).on_stream(object())

    assert result.state.halted
    assert timeline == [
        "fills", "execution:fill", "events", "update:update",
        "health", "halt:PAPER_RECONCILIATION_HALT",
    ]


def test_stream_sequences_once_before_strategy_evaluation() -> None:
    timeline: list[str] = []
    engine = FakeEngine(timeline=timeline)
    orders = FakeOrders(timeline)

    coordinator(engine, orders, timeline=timeline).on_stream(object())

    assert timeline == [
        "fills", "execution:fill", "events", "update:update",
        "health", "stream",
    ]


def test_stream_cancels_only_timed_out_buys_before_health_and_strategy() -> None:
    timeline: list[str] = []
    stale = Intent("stale-buy", Side.BUY, NOW - timedelta(seconds=30))
    sell = Intent("fresh-sell", Side.SELL, NOW - timedelta(seconds=89))
    engine = FakeEngine(Snapshot(pending_orders=(stale, sell)), timeline=timeline)
    orders = FakeOrders(timeline)

    coordinator(engine, orders, timeline=timeline).on_stream(object())

    assert timeline == [
        "fills", "execution:fill", "events", "update:update",
        "cancel:stale-buy", "health", "stream",
    ]
    assert "cancel:fresh-sell" not in orders.calls


def test_pause_forces_buy_cancellation_without_waiting_for_timeout() -> None:
    young = Intent("young-buy", Side.BUY, NOW)
    engine = FakeEngine(Snapshot(pending_orders=(young,)))
    orders = FakeOrders()

    coordinator(engine, orders).set_entries_paused(True)

    assert engine.calls == ["pause"]
    assert "cancel:young-buy" in orders.calls


def test_stop_finalizes_only_when_engine_broker_and_journal_are_clear() -> None:
    engine = FakeEngine()
    orders = FakeOrders()
    orders.state = BrokerState((Position(1),))
    orders.rows = (Row(False),)
    orders.connection = Connection(open_broker_orders=1)
    session = coordinator(engine, orders)

    assert not session.request_stop().state.finalized
    orders.state = BrokerState()
    assert not session.poll().state.finalized
    orders.rows = ()
    assert not session.poll().state.finalized
    orders.connection = Connection()
    interim, evidence = session.capture_finalization_evidence()

    assert not interim.state.finalized
    assert evidence is not None
    orders.connection = Connection(connected=False)
    result = session.confirm_finalization_after_disconnect(evidence)

    assert result.state.finalized
    assert "disconnect" not in " ".join(orders.calls)


def test_late_execution_after_zero_state_proof_blocks_finalization() -> None:
    class LateFillEngine(FakeEngine):
        def on_execution(self, execution):
            self._record(f"execution:{execution}")
            if execution == "late-fill":
                self.current = Snapshot(
                    **{
                        **self.current.__dict__,
                        "positions": (Position(1),),
                    }
                )
            return self.current

    engine = LateFillEngine()
    orders = FakeOrders()
    orders.executions = ()
    orders.updates = ()
    session = coordinator(engine, orders)
    session.request_stop()

    _, evidence = session.capture_finalization_evidence()
    assert evidence is not None
    orders.executions = ("late-fill",)
    orders.connection = Connection(connected=False)

    result = session.confirm_finalization_after_disconnect(evidence)

    assert result.state.halted
    assert not result.state.finalized


def test_finalization_evidence_is_one_shot() -> None:
    engine = FakeEngine()
    orders = FakeOrders()
    orders.executions = ()
    orders.updates = ()
    session = coordinator(engine, orders)
    session.request_stop()
    _, evidence = session.capture_finalization_evidence()
    assert evidence is not None
    orders.connection = Connection(connected=False)

    assert session.confirm_finalization_after_disconnect(evidence).state.finalized
    with pytest.raises(RuntimeError, match="already consumed"):
        session.confirm_finalization_after_disconnect(evidence)


def test_reconciliation_evidence_requires_uncapped_exact_summary() -> None:
    """A capped detail table must not allow an unsafe session to recover."""

    engine = FakeEngine()
    orders = FakeOrders()
    session = coordinator(engine, orders, Health(False, "HALT"))
    session.poll()
    orders.summary = Summary(unreconciled=1)

    result, evidence = session.capture_reconciliation_evidence()

    assert result.state.halted
    assert evidence is None


def test_reconciliation_evidence_is_one_shot_and_requires_same_fresh_proof() -> None:
    engine = FakeEngine()
    orders = FakeOrders()
    session = coordinator(engine, orders, Health(False, "HALT"))
    session.poll()
    # The human reconciliation is evaluated with a now-healthy evidence pass.
    session._health_evaluator = lambda **_kwargs: Health(True, "HEALTHY")  # type: ignore[attr-defined]
    _result, evidence = session.capture_reconciliation_evidence()
    assert evidence is not None

    resumed = session.confirm_reconciliation(evidence)

    assert engine.calls[-1] == "resume_from_reconciliation"
    assert resumed.result.state.active
    with pytest.raises(RuntimeError, match="already consumed"):
        session.confirm_reconciliation(evidence)


def test_reconciliation_confirmation_rejects_changed_broker_proof() -> None:
    engine = FakeEngine()
    orders = FakeOrders()
    session = coordinator(engine, orders, Health(False, "HALT"))
    session.poll()
    session._health_evaluator = lambda **_kwargs: Health(True, "HEALTHY")  # type: ignore[attr-defined]
    _result, evidence = session.capture_reconciliation_evidence()
    assert evidence is not None
    orders.reconciliation_snapshot = ReconciliationSnapshot(digest="changed")

    with pytest.raises(RuntimeError, match="changed"):
        session.confirm_reconciliation(evidence)

    assert "resume_from_reconciliation" not in engine.calls


def test_reconciliation_evidence_expires_before_confirmation() -> None:
    engine = FakeEngine()
    orders = FakeOrders()
    session = coordinator(engine, orders, Health(False, "HALT"))
    session.poll()
    session._health_evaluator = lambda **_kwargs: Health(True, "HEALTHY")  # type: ignore[attr-defined]
    _result, evidence = session.capture_reconciliation_evidence()
    assert evidence is not None

    with pytest.raises(RuntimeError, match="expired"):
        session.confirm_reconciliation(evidence, now=NOW + timedelta(seconds=31))

    assert "resume_from_reconciliation" not in engine.calls


def test_reconciliation_rejects_account_change_before_capture() -> None:
    engine = FakeEngine()
    orders = FakeOrders()
    session = coordinator(engine, orders, Health(False, "HALT"))
    session.poll()
    session._health_evaluator = lambda **_kwargs: Health(True, "HEALTHY")  # type: ignore[attr-defined]
    orders.armed_account_binding_valid = False

    result, evidence = session.capture_reconciliation_evidence()

    assert result.state.halted
    assert evidence is None
    assert "resume_from_reconciliation" not in engine.calls


def test_reconciliation_rejects_account_change_before_confirmation() -> None:
    engine = FakeEngine()
    orders = FakeOrders()
    session = coordinator(engine, orders, Health(False, "HALT"))
    session.poll()
    session._health_evaluator = lambda **_kwargs: Health(True, "HEALTHY")  # type: ignore[attr-defined]
    _result, evidence = session.capture_reconciliation_evidence()
    assert evidence is not None
    orders.armed_account_fingerprint_value = "armed-account-b"

    with pytest.raises(RuntimeError, match="armed-account identity changed"):
        session.confirm_reconciliation(evidence)

    assert "resume_from_reconciliation" not in engine.calls
