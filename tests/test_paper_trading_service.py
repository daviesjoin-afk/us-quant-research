"""Unit coverage for the Paper trading service: ownership, reads, lifecycle.

Nothing here touches IBKR: the order service is a local fake built by a fake
factory, so the ownership contract -- candidate registration, the two-phase
promotion (reserve, then commit or cancel), stale-candidate disposal, and the
fail-closed clearing rules -- is pinned without a broker, a Qt event loop, or a
real order service.

The service moved into the ``trading.application.paper`` package in Trading
Framework Closure v2C, so the structural assertions below read the *service
module* rather than a root module of the same name.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path
from threading import Event as ThreadingEvent
from threading import Thread

import pytest

from us_quant.trading.application.paper import service as module
from us_quant.trading.application.paper import (
    PaperActiveReleaseReservation,
    PaperPromotionReservation,
    PaperReconciliationStatus,
    PaperTradingLifecycleError,
    PaperTradingService,
    PaperTradingSnapshot,
)
from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase


# -- fakes ---------------------------------------------------------------


class _FakeConnection:
    def __init__(self, connected: bool) -> None:
        self.connected = connected


class _FakeState:
    def __init__(self, finalized: bool) -> None:
        self.finalized = finalized


class _FakeResult:
    def __init__(self, finalized: bool) -> None:
        self.state = _FakeState(finalized)


class _FakeService:
    """Records every call so 'exactly once' can be asserted, not assumed.

    ``park_connection_reads`` and ``park_connects`` make the two calls into the broker
    boundary *deterministically* interruptible: the parked call signals that it has been
    entered and waits for the test to release it, so a race can be driven by Events instead
    of by timing.  They are consumed one at a time (one-shot) so a test turns parking on for
    exactly the call it means.
    """

    def __init__(
        self,
        *,
        connected: bool = True,
        error: Exception | None = None,
        connect_error: Exception | None = None,
        read_error: Exception | None = None,
        park_connection_reads: bool = False,
        park_connects: bool = False,
    ) -> None:
        self.connected = connected
        self.error = error
        self.connect_error = connect_error
        self.read_error = read_error
        self.park_connection_reads = park_connection_reads
        self.park_connects = park_connects
        self.entered = ThreadingEvent()
        self.released = ThreadingEvent()
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.broker_state_calls = 0
        self.row_calls: list[dict] = []
        self.broker_state_value: object = object()
        self.rows: tuple[dict, ...] = ()
        self.connection = object()

    def _park(self) -> None:
        self.entered.set()
        assert self.released.wait(10), "the parked call was never released"

    def connect(self) -> object:
        self.connect_calls += 1
        if self.park_connects:
            self.park_connects = False
            self._park()
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True
        return self.connection

    def connection_snapshot(self) -> _FakeConnection:
        if self.park_connection_reads:
            self.park_connection_reads = False
            self._park()
        if self.read_error is not None:
            raise self.read_error
        return _FakeConnection(self.connected)

    def broker_state(self) -> object:
        self.broker_state_calls += 1
        return self.broker_state_value

    def reconciliation_rows_with_latency(
        self, *, session_id: str | None = None, limit: int = 1000
    ) -> tuple[dict, ...]:
        self.row_calls.append({"session_id": session_id, "limit": limit})
        return self.rows

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        if self.error is not None:
            raise self.error
        self.connected = False


class _FakeFactory:
    """Builds fakes on demand and records exactly how it was called."""

    def __init__(self, *services: object) -> None:
        self._queued = list(services)
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def __call__(
        self,
        config: object,
        *,
        repository: object,
        extended_hours_enabled: bool,
    ) -> object:
        self.calls.append(
            {
                "config": config,
                "repository": repository,
                "extended_hours_enabled": extended_hours_enabled,
            }
        )
        if self.error is not None:
            raise self.error
        if not self._queued:
            raise AssertionError("the factory was called more often than expected")
        return self._queued.pop(0)


class _FakeWorkflow:
    def __init__(
        self,
        *,
        phase: PaperWorkflowPhase = PaperWorkflowPhase.IDLE,
        result: object | None = None,
        evidence: object | None = None,
    ) -> None:
        self.phase = phase
        self.result = result
        self.reconciliation_evidence = evidence


class _Holder:
    """A getter that counts resolutions and can be re-pointed mid-test.

    Counting is what makes "read per call, never cached" falsifiable: a service
    that captured the workflow at construction would resolve exactly once.
    """

    def __init__(self, workflow: _FakeWorkflow | None = None) -> None:
        self.workflow = workflow if workflow is not None else _FakeWorkflow()
        self.calls = 0

    def __call__(self) -> _FakeWorkflow:
        self.calls += 1
        return self.workflow


def _service(
    *,
    factory: object | None = None,
    holder: _Holder | None = None,
) -> PaperTradingService:
    return PaperTradingService(
        workflow_getter=holder or _Holder(),
        order_service_factory=factory or _FakeFactory(),  # type: ignore[arg-type]
    )


def _owned(
    service: _FakeService,
    *,
    factory: _FakeFactory | None = None,
    candidate_id: str = "attempt-1",
    holder: _Holder | None = None,
) -> PaperTradingService:
    """A service whose active slot already holds ``service`` via a real promotion."""

    factory = factory if factory is not None else _FakeFactory(service)
    boundary = _service(factory=factory, holder=holder)
    boundary.connect_candidate(
        candidate_id,
        config=object(),
        repository=object(),
        extended_hours_enabled=False,
    )
    boundary.commit_candidate_promotion(
        boundary.reserve_candidate_promotion(candidate_id)
    )
    return boundary


def _owned_disconnected(
    service: _FakeService,
    *,
    candidate_id: str = "attempt-1",
    factory: _FakeFactory | None = None,
) -> PaperTradingService:
    """An owned service whose broker connection has been closed.

    ``connect_candidate`` connects the adapter, so a promoted-but-closed session
    has to be produced by running the real ``disconnect`` -- the same way the
    finalization path leaves it.
    """

    boundary = _owned(service, factory=factory, candidate_id=candidate_id)
    boundary.disconnect()
    return boundary


# -- no order service ----------------------------------------------------


def test_without_a_service_there_is_nothing_connected() -> None:
    boundary = _service()

    assert boundary.has_order_service() is False
    assert boundary.is_connected() is False


def test_disconnect_without_a_service_is_safe_and_does_nothing() -> None:
    boundary = _service()

    boundary.disconnect()

    assert boundary.snapshot().last_error is None


def test_read_only_order_queries_degrade_to_empty_without_a_service() -> None:
    boundary = _service()

    assert boundary.broker_state() is None
    assert boundary.reconciliation_rows_with_latency(session_id="s", limit=10) == ()


def test_connect_active_without_a_service_is_refused_not_invented() -> None:
    """Manual reconciliation must never create a service of its own."""

    boundary = _service()

    with pytest.raises(PaperTradingLifecycleError, match="no active Paper"):
        boundary.connect_active()


# -- with a service ------------------------------------------------------


def test_connected_state_follows_the_active_service() -> None:
    assert _owned(_FakeService(connected=True)).is_connected() is True
    assert _owned_disconnected(_FakeService()).is_connected() is False


def test_connection_state_is_read_from_the_live_service_not_a_copy() -> None:
    service = _FakeService()
    boundary = _owned(service)

    assert boundary.is_connected() is True
    service.connected = False

    assert boundary.is_connected() is False


def test_read_only_order_queries_forward_to_the_active_service() -> None:
    service = _FakeService()
    service.rows = ({"intent_id": "abc"},)
    boundary = _owned(service)

    assert boundary.broker_state() is service.broker_state_value
    assert boundary.reconciliation_rows_with_latency(
        session_id="session-1", limit=100
    ) == ({"intent_id": "abc"},)
    assert service.row_calls == [{"session_id": "session-1", "limit": 100}]


def test_reads_ignore_candidates_until_they_are_promoted() -> None:
    """An unarmed candidate is not the session, even while it is connected."""

    service = _FakeService(connected=True)
    boundary = _service(factory=_FakeFactory(service))

    boundary.connect_candidate(
        "attempt-1", config=object(), repository=object(), extended_hours_enabled=False
    )

    assert boundary.has_candidate("attempt-1") is True
    assert boundary.has_order_service() is False
    assert boundary.is_connected() is False
    assert boundary.broker_state() is None


# -- snapshot ------------------------------------------------------------


def test_snapshot_is_frozen() -> None:
    boundary = _owned(_FakeService(connected=True))

    snapshot = boundary.snapshot()

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.phase = "HALTED"  # type: ignore[misc]


def test_snapshot_carries_only_the_lifecycle_fields() -> None:
    field_names = {field.name for field in dataclasses.fields(PaperTradingSnapshot)}

    assert field_names == {"phase", "connected", "finalized", "last_error"}


def test_snapshot_reports_lifecycle_truth() -> None:
    workflow = _FakeWorkflow(
        phase=PaperWorkflowPhase.RUNNING,
        result=_FakeResult(finalized=False),
    )
    boundary = _owned(_FakeService(connected=True), holder=_Holder(workflow))

    snapshot = boundary.snapshot()

    assert snapshot.phase == "RUNNING"
    assert snapshot.connected is True
    assert snapshot.finalized is False
    assert snapshot.last_error is None


def test_snapshot_does_not_carry_orders_positions_or_fills() -> None:
    """Order truth stays in the existing service and journal."""

    field_names = {field.name for field in dataclasses.fields(PaperTradingSnapshot)}

    for forbidden in ("positions", "orders", "fills", "reconciliation", "pending"):
        assert not any(forbidden in name for name in field_names), forbidden


# -- phase / finalized / reconciliation ----------------------------------


def test_phase_comes_from_the_workflow_controller() -> None:
    holder = _Holder(_FakeWorkflow(phase=PaperWorkflowPhase.RECONCILING_READY))
    boundary = _service(holder=holder)

    assert boundary.phase() is PaperWorkflowPhase.RECONCILING_READY
    assert holder.calls == 1


def test_the_workflow_is_resolved_on_every_call_not_captured() -> None:
    """Replacing the controller must be visible, exactly as it is for the window."""

    holder = _Holder(_FakeWorkflow(phase=PaperWorkflowPhase.IDLE))
    boundary = _service(holder=holder)

    assert boundary.phase() is PaperWorkflowPhase.IDLE

    holder.workflow = _FakeWorkflow(phase=PaperWorkflowPhase.HALTED)

    assert boundary.phase() is PaperWorkflowPhase.HALTED
    assert holder.calls == 2


def test_finalized_requires_the_controllers_own_result() -> None:
    assert (
        _service(holder=_Holder(_FakeWorkflow(result=_FakeResult(True)))).is_finalized()
        is True
    )
    assert (
        _service(holder=_Holder(_FakeWorkflow(result=_FakeResult(False)))).is_finalized()
        is False
    )


def test_a_window_that_never_started_a_session_counts_as_finalized() -> None:
    """Nothing is outstanding, so the close gate must not refuse."""

    assert _service(holder=_Holder(_FakeWorkflow(result=None))).is_finalized() is True


def test_reconciliation_status_reports_awaiting_confirmation() -> None:
    waiting = _service(holder=_Holder(_FakeWorkflow(evidence=object())))
    idle = _service(holder=_Holder(_FakeWorkflow(evidence=None)))

    assert waiting.reconciliation_status() == PaperReconciliationStatus(True)
    assert waiting.reconciliation_status().awaiting_confirmation is True
    assert idle.reconciliation_status().awaiting_confirmation is False


# -- candidate connect ---------------------------------------------------


def test_connect_candidate_connects_once_and_tracks_without_promoting() -> None:
    service = _FakeService()
    factory = _FakeFactory(service)
    boundary = _service(factory=factory)
    config, repository = object(), object()

    connection = boundary.connect_candidate(
        "attempt-7",
        config=config,
        repository=repository,
        extended_hours_enabled=True,
    )

    assert connection is service.connection
    assert service.connect_calls == 1
    assert factory.calls == [
        {"config": config, "repository": repository, "extended_hours_enabled": True}
    ]
    assert boundary.has_candidate("attempt-7") is True
    # Connecting is not permission to own the session.
    assert boundary.has_order_service() is False
    assert boundary.candidate_service("attempt-7") is service


def test_connect_candidate_failure_disconnects_and_registers_nothing() -> None:
    service = _FakeService(connect_error=RuntimeError("no gateway"))
    boundary = _service(factory=_FakeFactory(service))

    with pytest.raises(RuntimeError, match="no gateway"):
        boundary.connect_candidate(
            "attempt-1",
            config=object(),
            repository=object(),
            extended_hours_enabled=False,
        )

    assert service.disconnect_calls == 1
    assert boundary.has_candidate("attempt-1") is False
    assert boundary.has_order_service() is False
    assert "no gateway" in (boundary.snapshot().last_error or "")


def test_constructor_failure_registers_nothing_and_leaves_no_connection() -> None:
    factory = _FakeFactory()
    factory.error = RuntimeError("cannot build")
    boundary = _service(factory=factory)

    with pytest.raises(RuntimeError, match="cannot build"):
        boundary.connect_candidate(
            "attempt-1",
            config=object(),
            repository=object(),
            extended_hours_enabled=False,
        )

    assert boundary.has_candidate("attempt-1") is False
    assert boundary.has_order_service() is False


def test_a_candidate_whose_cleanup_fails_is_still_tracked() -> None:
    """Dropping the reference would abandon a possibly-live broker socket."""

    service = _FakeService(
        connect_error=RuntimeError("connect blew up"),
        error=RuntimeError("disconnect also failed"),
    )
    boundary = _service(factory=_FakeFactory(service))

    with pytest.raises(RuntimeError, match="connect blew up"):
        boundary.connect_candidate(
            "attempt-1",
            config=object(),
            repository=object(),
            extended_hours_enabled=False,
        )

    assert service.disconnect_calls == 1
    assert boundary.has_candidate("attempt-1") is True
    assert "disconnect also failed" in (boundary.snapshot().last_error or "")


def test_a_candidate_id_cannot_be_reused() -> None:
    first, second = _FakeService(), _FakeService()
    boundary = _service(factory=_FakeFactory(first, second))
    boundary.connect_candidate(
        "attempt-1", config=object(), repository=object(), extended_hours_enabled=False
    )

    with pytest.raises(PaperTradingLifecycleError, match="already registered"):
        boundary.connect_candidate(
            "attempt-1",
            config=object(),
            repository=object(),
            extended_hours_enabled=False,
        )

    assert second.connect_calls == 0
    assert boundary.candidate_service("attempt-1") is first


def test_a_candidate_id_must_be_a_non_empty_string() -> None:
    boundary = _service()

    for bad in ("", "   ", None, 7):
        with pytest.raises(PaperTradingLifecycleError, match="non-empty string"):
            boundary.connect_candidate(
                bad,  # type: ignore[arg-type]
                config=object(),
                repository=object(),
                extended_hours_enabled=False,
            )


def test_candidate_service_refuses_an_unknown_id() -> None:
    boundary = _service()

    with pytest.raises(PaperTradingLifecycleError, match="unknown Paper candidate"):
        boundary.candidate_service("nope")


# -- discard -------------------------------------------------------------


def test_discard_disconnects_only_the_named_candidate() -> None:
    active, stale, other = _FakeService(), _FakeService(), _FakeService()
    boundary = _owned(active, factory=_FakeFactory(active, stale, other))
    boundary.connect_candidate(
        "stale", config=object(), repository=object(), extended_hours_enabled=False
    )
    boundary.connect_candidate(
        "other", config=object(), repository=object(), extended_hours_enabled=False
    )

    boundary.discard_candidate("stale")

    assert stale.disconnect_calls == 1
    assert other.disconnect_calls == 0
    assert active.disconnect_calls == 0
    assert boundary.has_candidate("stale") is False
    assert boundary.has_candidate("other") is True
    assert boundary.has_order_service() is True
    assert boundary.is_connected() is True


def test_discard_failure_keeps_the_candidate_tracked_and_raises() -> None:
    stale = _FakeService(error=RuntimeError("socket stuck"))
    boundary = _service(factory=_FakeFactory(stale))
    boundary.connect_candidate(
        "stale", config=object(), repository=object(), extended_hours_enabled=False
    )

    with pytest.raises(RuntimeError, match="socket stuck"):
        boundary.discard_candidate("stale")

    assert boundary.has_candidate("stale") is True
    assert "socket stuck" in (boundary.snapshot().last_error or "")


def test_discard_refuses_an_unknown_id() -> None:
    boundary = _service()

    with pytest.raises(PaperTradingLifecycleError, match="unknown Paper candidate"):
        boundary.discard_candidate("nope")


# -- promotion: reserve / commit / cancel ---------------------------------


def _reserved(
    service: _FakeService, candidate_id: str = "attempt-1"
) -> tuple[PaperTradingService, PaperPromotionReservation]:
    """A service with one connected candidate whose promotion is reserved."""

    boundary = _service(factory=_FakeFactory(service))
    boundary.connect_candidate(
        candidate_id, config=object(), repository=object(), extended_hours_enabled=False
    )
    return boundary, boundary.reserve_candidate_promotion(candidate_id)


def test_reserving_installs_the_candidate_and_takes_the_slot() -> None:
    """A reservation is the promotion itself, not a promise to promote later.

    This is the property the launch depends on: the owner has to exist *before* the
    publication that creates a session expecting one, or there is a window in which a
    published session has an order port nobody owns.
    """

    service = _FakeService()
    boundary, _reservation = _reserved(service)

    assert boundary.has_order_service() is True
    assert boundary.has_candidate("attempt-1") is False
    assert boundary.is_connected() is True
    assert service.connect_calls == 1


def test_reserving_refuses_to_replace_a_live_active_service() -> None:
    old, new = _FakeService(), _FakeService()
    boundary = _owned(old, factory=_FakeFactory(old, new))
    boundary.connect_candidate(
        "attempt-2", config=object(), repository=object(), extended_hours_enabled=False
    )

    with pytest.raises(PaperTradingLifecycleError, match="already active"):
        boundary.reserve_candidate_promotion("attempt-2")

    # The old owner is untouched, and the refused candidate is still disposable.
    assert old.disconnect_calls == 0
    assert boundary.broker_state() is old.broker_state_value
    boundary.discard_candidate("attempt-2")
    assert new.disconnect_calls == 1
    assert boundary.has_order_service() is True


def test_a_second_reservation_cannot_overlap_the_first() -> None:
    """Exclusivity is what makes the ending deterministic rather than a race."""

    first, second = _FakeService(), _FakeService()
    boundary = _service(factory=_FakeFactory(first, second))
    boundary.connect_candidate(
        "attempt-1", config=object(), repository=object(), extended_hours_enabled=False
    )
    boundary.reserve_candidate_promotion("attempt-1")
    boundary.connect_candidate(
        "attempt-2", config=object(), repository=object(), extended_hours_enabled=False
    )

    with pytest.raises(
        PaperTradingLifecycleError, match="already holds the promotion reservation"
    ):
        boundary.reserve_candidate_promotion("attempt-2")

    # The refused candidate is untouched, and still disposable the ordinary way.
    assert boundary.has_candidate("attempt-2") is True
    assert boundary.broker_state() is first.broker_state_value


def test_reserving_refuses_an_unknown_candidate() -> None:
    boundary = _service()

    with pytest.raises(PaperTradingLifecycleError, match="unknown Paper candidate"):
        boundary.reserve_candidate_promotion("nope")


def test_commit_ends_the_claim_without_moving_anything() -> None:
    """The commit says which ending this was; the ownership move already happened."""

    service = _FakeService()
    boundary, reservation = _reserved(service)

    boundary.commit_candidate_promotion(reservation)

    assert boundary.has_order_service() is True
    assert boundary.has_candidate("attempt-1") is False
    assert service.disconnect_calls == 0


def test_a_committed_launch_frees_the_slot_for_the_next_one() -> None:
    """An unended claim costs the *next* launch, not this one.

    That asymmetry is deliberate and is what makes it safe to take ownership before
    publication: the worst a broken commit can do is refuse a later reservation, which
    is fail-closed, whereas the retired promote-after-publish could leave a live
    session owned by nobody.
    """

    first, second = _FakeService(), _FakeService()
    boundary = _service(factory=_FakeFactory(first, second))
    boundary.connect_candidate(
        "attempt-1", config=object(), repository=object(), extended_hours_enabled=False
    )
    boundary.commit_candidate_promotion(
        boundary.reserve_candidate_promotion("attempt-1")
    )

    # The session ends the way finalization leaves it...
    boundary.disconnect()
    boundary.clear_active()

    # ...and the next launch can take the slot again.
    boundary.connect_candidate(
        "attempt-2", config=object(), repository=object(), extended_hours_enabled=False
    )
    assert boundary.reserve_candidate_promotion("attempt-2") is not None


def test_commit_refuses_a_reservation_that_was_already_ended() -> None:
    service = _FakeService()
    boundary, reservation = _reserved(service)
    boundary.commit_candidate_promotion(reservation)

    with pytest.raises(PaperTradingLifecycleError, match="stale or foreign"):
        boundary.commit_candidate_promotion(reservation)


def test_commit_refuses_an_equal_but_foreign_reservation() -> None:
    """Identity *is* the meaning of a reservation, so an equal one is not the same one.

    Naming the same candidate is not enough to end a claim: otherwise any caller that
    could construct the value could cut another launch's promotion short.
    """

    service = _FakeService()
    boundary, _reservation = _reserved(service)

    with pytest.raises(PaperTradingLifecycleError, match="stale or foreign"):
        boundary.commit_candidate_promotion(PaperPromotionReservation("attempt-1"))

    # The real claim is untouched by the attempt.
    assert boundary.has_order_service() is True


def test_cancel_returns_the_service_to_the_candidate_slot() -> None:
    """The rollback is exactly the reverse of the installation."""

    service = _FakeService()
    boundary, reservation = _reserved(service)

    assert boundary.cancel_candidate_promotion(reservation) is True

    assert boundary.has_order_service() is False
    assert boundary.has_candidate("attempt-1") is True
    assert service.disconnect_calls == 0
    # And the ordinary candidate path works on it again, which is what the launch's
    # own rollback goes on to use.
    boundary.discard_candidate("attempt-1")
    assert service.disconnect_calls == 1


def test_cancel_reports_that_a_foreign_reservation_released_nothing() -> None:
    """It must not raise -- it runs where raising would skip the rejection -- so the
    only honest way to tell a stale caller is to say it released nothing."""

    service = _FakeService()
    boundary, _reservation = _reserved(service)

    assert (
        boundary.cancel_candidate_promotion(PaperPromotionReservation("attempt-1"))
        is False
    )
    assert boundary.has_order_service() is True


def test_a_cancelled_reservation_leaves_the_slot_reservable_again() -> None:
    service = _FakeService()
    boundary, reservation = _reserved(service)
    boundary.cancel_candidate_promotion(reservation)

    assert boundary.reserve_candidate_promotion("attempt-1") is not None


# -- a reservation owns its id --------------------------------------------


def test_the_reserved_id_cannot_be_registered_again() -> None:
    """A replacement would be orphaned by the reservation's own rollback.

    Reserving moves the candidate into the active slot, so the id leaves the candidate
    map and a plain duplicate check would not see it.  A second service registered under
    that id is then *overwritten* by ``cancel`` -- cancelled into the map it had already
    replaced -- so its broker connection stays open with nothing tracking it.  The
    refusal must come before the factory too, or a connection gets built that nothing
    can reach.
    """

    first, second = _FakeService(), _FakeService()
    factory = _FakeFactory(first, second)
    boundary = _service(factory=factory)
    boundary.connect_candidate(
        "attempt-1", config=object(), repository=object(), extended_hours_enabled=False
    )
    reservation = boundary.reserve_candidate_promotion("attempt-1")

    with pytest.raises(
        PaperTradingLifecycleError, match="reserved by an in-flight promotion"
    ):
        boundary.connect_candidate(
            "attempt-1", config=object(), repository=object(), extended_hours_enabled=False
        )

    # Nothing was built, and nothing moved.
    assert len(factory.calls) == 1
    assert second.connect_calls == 0
    assert boundary.has_order_service() is True
    # The claim is still the live one, so the rollback still has exactly one thing to do.
    boundary.commit_candidate_promotion(reservation)
    assert boundary.has_order_service() is True


def test_cancel_refuses_to_overwrite_an_unaccountable_candidate() -> None:
    """The corruption defence: never put the reserved service back over someone else.

    Unreachable while ``connect_candidate`` refuses a reserved id, and checked anyway
    because the failure it prevents is both silent and permanent -- the overwritten
    service would leave every ownership map while its connection stayed open.  The
    refusal is a whole-call no-op: owner, claim and the existing candidate all stay.
    """

    first, second = _FakeService(), _FakeService()
    boundary = _service(factory=_FakeFactory(first, second))
    boundary.connect_candidate(
        "attempt-1", config=object(), repository=object(), extended_hours_enabled=False
    )
    reservation = boundary.reserve_candidate_promotion("attempt-1")
    # Constructed directly: the collision the public API can no longer produce.
    boundary.connect_candidate(
        "attempt-2", config=object(), repository=object(), extended_hours_enabled=False
    )
    boundary._candidates["attempt-1"] = boundary._candidates.pop("attempt-2")

    assert boundary.cancel_candidate_promotion(reservation) is False

    # Everything exactly as it was, including the candidate that would have been lost.
    assert boundary.candidate_service("attempt-1") is second
    assert second.disconnect_calls == 0
    assert boundary.has_order_service() is True
    # And the claim was not consumed by the refused cancel.
    boundary.commit_candidate_promotion(reservation)
    assert boundary.has_order_service() is True


# -- a reservation locks the slot -----------------------------------------


def test_clearing_is_refused_while_a_promotion_is_reserved() -> None:
    """``clear_active`` is the other public way to empty the slot, so it must refuse.

    Without this refusal the reservation merely *intends* to lock the slot: a
    finalization or recovery caller could empty it between the reserve and the commit,
    and the launch would publish a session whose owner had already been dropped --
    the ownerless ``RUNNING`` state the two-phase promotion exists to prevent,
    reachable again through a different public method.
    """

    service = _FakeService()
    boundary, reservation = _reserved(service)
    boundary.disconnect()  # not a finalization: the socket goes, the session does not

    with pytest.raises(
        PaperTradingLifecycleError, match="holds the promotion reservation"
    ):
        boundary.clear_active()

    # Nothing moved: still owned, and still the same live claim.
    assert boundary.has_order_service() is True
    boundary.commit_candidate_promotion(reservation)
    assert boundary.has_order_service() is True


def test_clearing_is_refused_before_it_even_reads_the_connection() -> None:
    """The refusal is a whole-call no-op, not something that happens late.

    A live connection is refused too, but for a different reason and by a different
    check; the reservation refusal must come first so a caller cannot learn anything
    about the connection state from a slot that is not theirs to inspect.
    """

    service = _FakeService()
    boundary, _reservation = _reserved(service)
    assert boundary.is_connected() is True

    with pytest.raises(
        PaperTradingLifecycleError, match="holds the promotion reservation"
    ):
        boundary.clear_active()

    assert service.disconnect_calls == 0
    assert boundary.has_order_service() is True


def test_commit_refuses_when_the_reserved_slot_was_lost() -> None:
    """The second half of the lock: a commit must not declare an empty slot owned.

    ``clear_active`` refusing a reserved slot is what makes this unreachable, so the
    state is built directly -- this guard is the reason "a reservation locks the slot"
    is a property rather than a comment, and it has to be exercised even though nothing
    can reach it.
    """

    first, second = _FakeService(), _FakeService()
    boundary = _service(factory=_FakeFactory(first, second))
    boundary.connect_candidate(
        "attempt-1", config=object(), repository=object(), extended_hours_enabled=False
    )
    reservation = boundary.reserve_candidate_promotion("attempt-1")
    # Unreachable through the public API; constructed to exercise the guard.
    boundary._order_service = None

    with pytest.raises(
        PaperTradingLifecycleError, match="no longer holds the active slot"
    ):
        boundary.commit_candidate_promotion(reservation)

    assert boundary.has_order_service() is False
    # The claim is deliberately left standing: the owner cannot be accounted for, so
    # nothing here may hand the slot back for reuse.
    boundary.connect_candidate(
        "attempt-2", config=object(), repository=object(), extended_hours_enabled=False
    )
    with pytest.raises(
        PaperTradingLifecycleError, match="already holds the promotion reservation"
    ):
        boundary.reserve_candidate_promotion("attempt-2")


# -- the two-phase active release ----------------------------------------
#
# The mirror of promotion, and the same shape for the same reason: the two transitions
# involved have to be ordered and only one of them can be taken back.  What cannot be taken
# back is the execution lease -- ``finalize_if_safe`` is a check-and-commit call on a
# controller this service does not get to change -- so the slot's releasability is proved
# and locked *first*, and the workflow is asked second.  These tests pin the lock, the
# proof and the totality of the commit.


def _releasable(
    service: _FakeService,
) -> tuple[PaperTradingService, PaperActiveReleaseReservation]:
    """An owned session whose broker connection has been closed: the release's precondition."""

    boundary = _owned_disconnected(service)
    return boundary, boundary.reserve_active_release()


def test_reserving_a_release_locks_the_slot_without_dropping_it() -> None:
    """Reserving is a claim on the ending, not the ending itself."""

    service = _FakeService()
    boundary, _reservation = _releasable(service)

    assert boundary.has_order_service() is True


def test_reserving_a_release_is_refused_while_a_promotion_holds_the_slot() -> None:
    """E1's invariant path, refused *before* the caller has released anything.

    This is the whole point of the transaction boundary: a promotion claim that still holds
    the slot cannot be accounted for, so the refusal must arrive here -- while the caller
    can still walk away -- rather than after the execution lease has already been handed
    back, which would leave PAPER released with the ownership still held.
    """

    service = _FakeService()
    boundary, _reservation = _reserved(service)
    boundary.disconnect()

    with pytest.raises(
        PaperTradingLifecycleError, match="holds the promotion reservation"
    ):
        boundary.reserve_active_release()

    assert boundary.has_order_service() is True


def test_reserving_a_release_is_refused_without_an_active_service() -> None:
    boundary = _service()

    with pytest.raises(
        PaperTradingLifecycleError, match="no active Paper order service to release"
    ):
        boundary.reserve_active_release()


def test_reserving_a_release_is_refused_while_the_service_is_still_connected() -> None:
    """Dropping a live socket is the one thing this must never do."""

    service = _FakeService()
    boundary = _owned(service)

    with pytest.raises(
        PaperTradingLifecycleError, match="still reports a live connection"
    ):
        boundary.reserve_active_release()

    assert boundary.has_order_service() is True


def test_a_second_release_reservation_cannot_overlap_the_first() -> None:
    service = _FakeService()
    boundary, _reservation = _releasable(service)

    with pytest.raises(PaperTradingLifecycleError, match="already in flight"):
        boundary.reserve_active_release()


@pytest.mark.parametrize(
    "action,match",
    [
        (
            lambda boundary: boundary.clear_active(),
            "release is already in flight",
        ),
        (
            lambda boundary: boundary.connect_active(),
            "release is in flight",
        ),
    ],
)
def test_a_reserved_release_locks_every_other_slot_transition(
    action, match: str
) -> None:
    """Clearing and re-opening are both refused while the release stands.

    Enumerated rather than sampled because the commit's totality is exactly this list: any
    one transition that could still slip through is enough to invalidate a commit the
    caller has already paid for with an execution lease.  The two refusals are quoted
    separately because they are *different* refusals -- ``clear_active`` goes through the
    reservation itself and is turned away as an overlapping release, while a re-open is
    turned away by the claim it would invalidate.
    """

    service = _FakeService()
    boundary, _reservation = _releasable(service)

    with pytest.raises(PaperTradingLifecycleError, match=match):
        action(boundary)

    assert boundary.has_order_service() is True
    assert boundary.has_candidate_ownership() is False


def test_a_reserved_release_refuses_a_promotion_into_the_slot() -> None:
    """The third transition of the same list, and the one that installs a new owner."""

    first, second = _FakeService(), _FakeService()
    boundary = _owned_disconnected(first, factory=_FakeFactory(first, second))
    boundary.connect_candidate(
        "attempt-2", config=object(), repository=object(), extended_hours_enabled=False
    )
    boundary.reserve_active_release()

    with pytest.raises(PaperTradingLifecycleError, match="release is in flight"):
        boundary.reserve_candidate_promotion("attempt-2")

    assert boundary.has_order_service() is True
    assert boundary.has_candidate("attempt-2") is True


def test_committing_a_release_drops_the_slot_and_is_total() -> None:
    """``commit`` re-checks nothing it locked: that is what makes it total.

    A commit that re-validated the connection would be a commit that can fail *after* the
    caller has already released the execution lease -- which is the single failure the
    reservation exists to make impossible.
    """

    service = _FakeService()
    boundary, reservation = _releasable(service)

    boundary.commit_active_release(reservation)

    assert boundary.has_order_service() is False
    assert boundary.is_connected() is False


def test_cancelling_a_release_gives_the_lock_back_and_drops_nothing() -> None:
    """The workflow refused, so the slot ends up exactly as it was found."""

    service = _FakeService()
    boundary, reservation = _releasable(service)

    assert boundary.cancel_active_release(reservation) is True
    assert boundary.has_order_service() is True
    # And the lock is genuinely gone: the slot can be reserved again.
    boundary.reserve_active_release()
    assert boundary.has_order_service() is True


def test_a_foreign_release_reservation_releases_nothing() -> None:
    """A reservation that is not the outstanding one is told it released nothing."""

    service = _FakeService()
    boundary, _reservation = _releasable(service)

    assert boundary.cancel_active_release(PaperActiveReleaseReservation()) is False
    assert boundary.has_order_service() is True


def test_committing_a_foreign_release_reservation_is_refused() -> None:
    service = _FakeService()
    boundary, _reservation = _releasable(service)

    with pytest.raises(
        PaperTradingLifecycleError, match="stale or foreign Paper active-release"
    ):
        boundary.commit_active_release(PaperActiveReleaseReservation())

    assert boundary.has_order_service() is True


def test_a_release_commit_refuses_an_active_slot_that_was_lost() -> None:
    """The second half of the lock, exercised although nothing can reach it."""

    service = _FakeService()
    boundary, reservation = _releasable(service)
    # Unreachable through the public API; constructed to exercise the guard.
    boundary._order_service = None

    with pytest.raises(
        PaperTradingLifecycleError, match="no longer holds the active slot"
    ):
        boundary.commit_active_release(reservation)


# -- a connect and a release cannot overlap ------------------------------
#
# Both operations start by reading the connection and then act on what they read, so a
# check-then-act pair of them races in **both** directions.  Each test below parks the
# first operation *inside* the call into the broker boundary and drives the second from
# another thread, so the interleaving is driven by Events rather than by timing.


def _in_a_thread(action) -> Thread:
    thread = Thread(target=action, daemon=True)
    thread.start()
    return thread


def test_a_connect_is_refused_while_a_release_holds_the_slot() -> None:
    """The release claims the slot *before* it reads the connection, not after.

    This is the order the whole concurrency argument rests on, and the reason it is not
    "one more check": a release that asked the broker first would report the slot
    releasable while a re-open was still free to make it live again -- and the caller would
    then release the execution lease and commit a slot that had become live underneath it,
    leaving a live broker socket with no owner and no lease.
    """

    service = _FakeService(connected=False, park_connection_reads=True)
    boundary = _owned_disconnected(service)
    failures: list[Exception] = []

    def release() -> None:
        try:
            boundary.reserve_active_release()
        except Exception as error:  # noqa: BLE001 - asserted below
            failures.append(error)

    thread = _in_a_thread(release)
    assert service.entered.wait(10), "the release never reached the connection read"

    with pytest.raises(PaperTradingLifecycleError, match="release is in flight"):
        boundary.connect_active()

    service.released.set()
    thread.join(10)
    assert failures == []


def test_a_release_is_refused_while_a_connect_is_in_flight() -> None:
    """The other direction: a re-open claims the slot for the length of its connect."""

    service = _FakeService(connected=False)
    boundary = _owned_disconnected(service)
    # Armed *after* the setup: building the owned slot runs a real connect, and parking it
    # would park the wrong call.
    service.park_connects = True
    failures: list[Exception] = []

    def reconnect() -> None:
        try:
            boundary.connect_active()
        except Exception as error:  # noqa: BLE001 - asserted below
            failures.append(error)

    thread = _in_a_thread(reconnect)
    assert service.entered.wait(10), "the re-open never reached connect()"

    with pytest.raises(PaperTradingLifecycleError, match="re-open is in flight"):
        boundary.reserve_active_release()

    service.released.set()
    thread.join(10)
    assert failures == []


def test_a_second_release_cannot_overwrite_a_reservation_being_proved() -> None:
    """Overlapping reserves: the claim is visible before the proof, so the second refuses.

    Without that ordering the second caller would install its own token, the first caller's
    token would become stale, and the first caller's commit -- which runs *after*
    ``finalize_if_safe`` has already released the execution lease -- would fail.  That is
    exactly the failure the reservation was supposed to make structurally unreachable.
    """

    service = _FakeService(connected=False, park_connection_reads=True)
    boundary = _owned_disconnected(service)
    held: list[PaperActiveReleaseReservation] = []

    def release() -> None:
        held.append(boundary.reserve_active_release())

    thread = _in_a_thread(release)
    assert service.entered.wait(10)

    with pytest.raises(PaperTradingLifecycleError, match="already in flight"):
        boundary.reserve_active_release()

    service.released.set()
    thread.join(10)
    # And the first caller still owns a reservation its own commit accepts.
    boundary.commit_active_release(held[0])
    assert boundary.has_order_service() is False


def test_a_connect_that_fails_leaves_no_claim() -> None:
    """The claim is released in a ``finally``, so a failed re-open does not wedge the slot."""

    service = _FakeService(connected=False)
    boundary = _owned_disconnected(service)
    service.connect_error = RuntimeError("no route")

    with pytest.raises(RuntimeError, match="no route"):
        boundary.connect_active()

    # The slot is open again: a release may proceed (the service is still disconnected).
    reservation = boundary.reserve_active_release()
    boundary.cancel_active_release(reservation)
    assert boundary.has_order_service() is True


def test_a_release_whose_connection_read_fails_keeps_no_claim() -> None:
    """A read that raised proved nothing, so the slot is unlocked rather than claimed."""

    service = _FakeService(
        connected=False, read_error=RuntimeError("the socket timed out")
    )
    boundary = _owned_disconnected(service)

    with pytest.raises(RuntimeError, match="timed out"):
        boundary.reserve_active_release()

    service.read_error = None
    reservation = boundary.reserve_active_release()
    assert boundary.has_order_service() is True
    boundary.cancel_active_release(reservation)


# -- candidate ownership -------------------------------------------------


def test_candidate_ownership_reports_a_connected_candidate() -> None:
    """The service owns two slots, and this is the query for the second one."""

    service = _FakeService()
    boundary = _service(factory=_FakeFactory(service))
    assert boundary.has_candidate_ownership() is False

    boundary.connect_candidate(
        "attempt-1", config=object(), repository=object(), extended_hours_enabled=False
    )

    assert boundary.has_candidate_ownership() is True
    # Promotion moves it out of the candidate slot into the active one.
    boundary.commit_candidate_promotion(
        boundary.reserve_candidate_promotion("attempt-1")
    )
    assert boundary.has_candidate_ownership() is False
    assert boundary.has_order_service() is True


def test_candidate_ownership_survives_a_failed_disposal() -> None:
    """E1's discard path: the candidate stays tracked when it cannot be disconnected.

    This is how production reaches the state, and it is why "no active service" is not
    "nothing owned": the candidate's broker connection may still be alive, with nothing in
    the workflow that refers to it any more.
    """

    service = _FakeService(error=RuntimeError("the candidate socket would not close"))
    boundary = _service(factory=_FakeFactory(service))
    boundary.connect_candidate(
        "attempt-1", config=object(), repository=object(), extended_hours_enabled=False
    )
    boundary.disconnect()  # a disconnect of the *active* slot: none here, so a no-op

    with pytest.raises(RuntimeError, match="would not close"):
        boundary.discard_candidate("attempt-1")

    assert boundary.has_candidate_ownership() is True
    assert boundary.has_order_service() is False


def test_a_connect_is_refused_while_a_clear_holds_the_slot() -> None:
    """``clear_active`` *is* the reservation, so it locks a re-open out exactly like one.

    That is the point of routing the clear through the claim rather than giving it checks
    of its own: a check of its own would be a fifth check-then-act, and the one it would
    miss is the re-open that makes the slot live again after the clear read a stale
    "disconnected".
    """

    service = _FakeService(connected=False)
    boundary = _owned_disconnected(service)
    service.park_connection_reads = True
    failures: list[Exception] = []

    def clear() -> None:
        try:
            boundary.clear_active()
        except Exception as error:  # noqa: BLE001 - asserted below
            failures.append(error)

    thread = _in_a_thread(clear)
    assert service.entered.wait(10), "the clear never reached the connection read"

    with pytest.raises(PaperTradingLifecycleError, match="release is in flight"):
        boundary.connect_active()

    service.released.set()
    thread.join(10)
    assert failures == []
    assert boundary.has_order_service() is False


def test_a_clear_is_refused_while_a_connect_is_in_flight() -> None:
    """The direction that would race even with a pre-check inside ``clear_active``.

    A check at the top of the clear would read "no re-open yet", unlock, and then drop a
    slot the re-open was about to make live.  Going through the claim removes the window
    rather than narrowing it.
    """

    service = _FakeService(connected=False)
    boundary = _owned_disconnected(service)
    service.park_connects = True
    failures: list[Exception] = []

    def reconnect() -> None:
        try:
            boundary.connect_active()
        except Exception as error:  # noqa: BLE001 - asserted below
            failures.append(error)

    thread = _in_a_thread(reconnect)
    assert service.entered.wait(10), "the re-open never reached connect()"

    with pytest.raises(PaperTradingLifecycleError, match="re-open is in flight"):
        boundary.clear_active()

    service.released.set()
    thread.join(10)
    assert failures == []
    # The re-open finished, so the slot is live again and the owner was never dropped.
    assert boundary.has_order_service() is True
    assert boundary.is_connected() is True


def test_an_expected_service_that_does_not_match_is_refused_before_the_claim() -> None:
    """The identity check is inside the critical section that installs the claim.

    So a refusal leaves no claim standing: the slot is exactly as it was found and the
    legitimate caller can still take it.
    """

    older, newer = _FakeService(connected=False), _FakeService(connected=False)
    boundary = _owned(older, factory=_FakeFactory(older, newer))

    with pytest.raises(PaperTradingLifecycleError, match="different active"):
        boundary.clear_active(expected_service=newer)

    assert boundary.has_order_service() is True

    # No claim was left behind, so the release path is open to the real owner.
    boundary.disconnect()
    boundary.commit_active_release(
        boundary.reserve_active_release(expected_service=older)
    )
    assert boundary.has_order_service() is False


def test_a_completed_clear_leaves_no_claim_behind() -> None:
    """``clear_active`` is reserve + commit, so the slot ends up empty *and* unlocked."""

    boundary = _owned_disconnected(_FakeService())

    boundary.clear_active()

    assert boundary.has_order_service() is False
    # Nothing is claimed: a fresh attempt says "no active service", not "already in flight".
    with pytest.raises(PaperTradingLifecycleError, match="no active Paper"):
        boundary.reserve_active_release()


# -- active lifecycle ----------------------------------------------------


def test_disconnect_does_not_clear_ownership() -> None:
    """A dead socket is not a released session; reconciliation still needs it."""

    service = _FakeService()
    boundary = _owned(service)

    boundary.disconnect()

    assert service.disconnect_calls == 1
    assert boundary.has_order_service() is True
    assert boundary.is_connected() is False


def test_disconnect_failure_is_recorded_keeps_ownership_and_still_raises() -> None:
    service = _FakeService(error=RuntimeError("socket refused"))
    boundary = _owned(service)

    with pytest.raises(RuntimeError, match="socket refused"):
        boundary.disconnect()

    assert service.disconnect_calls == 1
    assert boundary.has_order_service() is True
    assert "socket refused" in (boundary.snapshot().last_error or "")


def test_disconnect_never_reports_silent_success_on_failure() -> None:
    boundary = _owned(_FakeService(error=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        boundary.disconnect()

    assert boundary.snapshot().last_error is not None


def test_a_later_successful_disconnect_clears_the_recorded_error() -> None:
    service = _FakeService(error=RuntimeError("boom"))
    boundary = _owned(service)

    with pytest.raises(RuntimeError):
        boundary.disconnect()

    service.error = None
    boundary.disconnect()

    assert boundary.snapshot().last_error is None


def test_connect_active_reconnects_the_owned_service_once() -> None:
    service = _FakeService()
    boundary = _owned(service)
    assert service.connect_calls == 1  # the candidate connect

    result = boundary.connect_active()

    assert result is service.connection
    assert service.connect_calls == 2
    assert boundary.has_order_service() is True


# -- clear_active --------------------------------------------------------


def test_clear_active_is_refused_while_the_connection_is_alive() -> None:
    service = _FakeService(connected=True)
    boundary = _owned(service)

    with pytest.raises(PaperTradingLifecycleError, match="live connection"):
        boundary.clear_active()

    assert boundary.has_order_service() is True


def test_clear_active_succeeds_once_the_connection_is_gone() -> None:
    boundary = _owned_disconnected(_FakeService())

    boundary.clear_active()

    assert boundary.has_order_service() is False
    assert boundary.is_connected() is False


def test_clear_active_refuses_when_nothing_is_owned() -> None:
    boundary = _service()

    with pytest.raises(PaperTradingLifecycleError, match="no active Paper"):
        boundary.clear_active()


def test_clear_active_refuses_a_different_service_than_the_one_owned() -> None:
    """A late callback must not clear an owner that replaced it."""

    old, new = _FakeService(connected=False), _FakeService(connected=False)
    boundary = _owned(old, factory=_FakeFactory(old, new))

    with pytest.raises(PaperTradingLifecycleError, match="different active"):
        boundary.clear_active(expected_service=new)

    assert boundary.has_order_service() is True
    assert boundary.broker_state() is old.broker_state_value


def test_clear_active_accepts_the_service_it_actually_owns() -> None:
    service = _FakeService()
    boundary = _owned_disconnected(service)

    boundary.clear_active(expected_service=service)

    assert boundary.has_order_service() is False


# -- probe ---------------------------------------------------------------


def test_probe_connects_reads_and_disconnects_without_owning_anything() -> None:
    service = _FakeService()
    boundary = _service(factory=_FakeFactory(service))

    connection, broker_state = boundary.probe_order_channel(
        config=object(), repository=object(), extended_hours_enabled=False
    )

    assert connection is service.connection
    assert broker_state is service.broker_state_value
    assert service.connect_calls == 1
    assert service.disconnect_calls == 1
    assert boundary.has_order_service() is False


def test_probe_disconnects_even_when_the_read_fails() -> None:
    service = _FakeService()

    def _explode() -> object:
        raise RuntimeError("no state")

    service.broker_state = _explode  # type: ignore[method-assign]
    boundary = _service(factory=_FakeFactory(service))

    with pytest.raises(RuntimeError, match="no state"):
        boundary.probe_order_channel(
            config=object(), repository=object(), extended_hours_enabled=False
        )

    assert service.disconnect_calls == 1
    assert boundary.has_order_service() is False


def test_probe_does_not_disturb_an_active_session() -> None:
    active, probe = _FakeService(), _FakeService()
    boundary = _owned(active, factory=_FakeFactory(active, probe))

    boundary.probe_order_channel(
        config=object(), repository=object(), extended_hours_enabled=False
    )

    assert active.disconnect_calls == 0
    assert boundary.has_order_service() is True


# -- construction --------------------------------------------------------


def test_the_order_service_factory_is_required() -> None:
    """The application boundary receives construction from its composition root."""

    signature = inspect.signature(PaperTradingService.__init__)
    parameter = signature.parameters["order_service_factory"]

    assert parameter.default is inspect.Parameter.empty


# -- structural boundary -------------------------------------------------


def _module_source() -> str:
    return inspect.getsource(module)


def _imported_modules() -> list[str]:
    tree = ast.parse(_module_source())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return imported


def test_module_does_not_import_any_gui_toolkit() -> None:
    forbidden = {"PySide6", "PyQt5", "PyQt6", "PySide2"}
    for name in _imported_modules():
        assert name.split(".")[0] not in forbidden, f"{name} pulls a GUI toolkit in"


def test_module_does_not_reference_widgets_or_the_desktop() -> None:
    tree = ast.parse(_module_source())
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            referenced.update(alias.name for alias in node.names)

    for symbol in ("QThread", "QWidget", "MainWindow", "QObject"):
        assert symbol not in referenced, f"{symbol} must not be used here"


def test_module_does_not_import_the_desktop_module() -> None:
    for name in _imported_modules():
        assert not name.startswith("us_quant.desktop")


def test_module_does_not_import_the_launch_plan_or_the_workflow() -> None:
    """The service must not learn the shape of a strategy launch."""

    for name in _imported_modules():
        assert "AutoLaunchPlan" not in name, f"{name} leaks the launch plan in"
        assert not name.startswith("us_quant.paper_workflow"), name


def test_module_exposes_no_real_order_entry_point() -> None:
    """This step must not add or wrap any trading entry point."""

    tree = ast.parse(_module_source())
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for forbidden in (
        "submit",
        "cancel",
        "replace",
        "place",
        "submit_order",
        "cancel_order",
        "replace_order",
        "place_order",
        "reqGlobalCancel",
        "resubmit_pending_intent",
        "arm",
    ):
        assert forbidden not in defined, f"{forbidden} must not exist here"

    source = _module_source()
    for forbidden in ("reqGlobalCancel", "placeOrder", "cancelOrder"):
        assert forbidden not in source, f"{forbidden} must not appear here"


#: Calls that cross into the broker boundary.  A lock held across one of these is a lock
#: held across a network round trip.
BROKER_BOUNDARY_CALLS = (
    "broker_state",
    "connect",
    "connection_snapshot",
    "disconnect",
    "reconciliation_rows_with_latency",
    "reconciliation_summary",
)

#: The modules this file may import.  A closed allowlist rather than a denylist: a new
#: dependency has to be declared here, where a reviewer sees it.
ALLOWED_IMPORTS = (
    "__future__",
    "threading",
    "typing",
    "us_quant.trading.application.paper.active_release",
    "us_quant.trading.application.paper.contracts",
    "us_quant.trading.application.paper.models",
    "us_quant.trading.runtime.workflow_state",
)

#: Attribute names that would mean a service bag, a registry or a manager arrived.
FORBIDDEN_BAG_NAMES = (
    "services",
    "registry",
    "container",
    "context",
    "locator",
    "manager",
)


def _writers_of(source: str, attributes: set[str]) -> set[str]:
    """The methods that assign any of ``attributes`` on ``self``."""

    writers: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            targets = list(getattr(inner, "targets", ()))
            target = getattr(inner, "target", None)
            if target is not None:
                targets.append(target)
            for candidate in targets:
                if (
                    isinstance(candidate, ast.Attribute)
                    and candidate.attr in attributes
                ):
                    writers.add(node.name)
    return writers


def test_module_never_holds_the_lock_across_a_network_call() -> None:
    """``with self._lock`` must not wrap a call into the broker boundary.

    Asserted on the *calls* inside a locked block, not on its text.  The earlier form
    searched the block's AST dump for the substrings "connect" and "disconnect", so it
    fired on a field name (``_active_connect_inflight``) and on an operator message
    containing the word "connection" -- neither of which is a network call, and both of
    which could be silenced by choosing a different noun.  A guard that a rename satisfies
    is not guarding the property, so this one names the calls that cross the boundary.
    """

    offending: list[str] = []
    for node in ast.walk(ast.parse(_module_source())):
        if not isinstance(node, ast.With):
            continue
        if "self._lock" not in ast.unparse(node.items[0].context_expr):
            continue
        for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if not isinstance(inner, ast.Call):
                continue
            name = getattr(inner.func, "attr", None) or getattr(inner.func, "id", None)
            if name in BROKER_BOUNDARY_CALLS:
                offending.append(str(name))
    assert not offending, offending


def test_the_ownership_write_surface_is_the_declared_one() -> None:
    """Every writer of the slots, the reservations and the connect claim is named here.

    Ownership transitions are only meaningful while they all go through a small declared
    set of methods: the two reservations exist precisely so that *every* promotion and
    *every* release is one of these calls, and the connect claim is what makes those two
    mutually exclusive.  A writer appearing anywhere else would be a second, quieter
    ownership rule beside them.
    """

    writers = _writers_of(
        _module_source(),
        {"_order_service", "_promotion_reservation", "_active_connect_inflight"},
    )
    assert writers == {
        "__init__",
        "reserve_candidate_promotion",
        "commit_candidate_promotion",
        "cancel_candidate_promotion",
        "connect_active",
    }, sorted(writers)

    # The release's own half lives in its module, and is pinned there instead.
    release_source = (
        Path(module.__file__ or "").parent / "active_release.py"
    ).read_text(encoding="utf-8")
    release_writers = _writers_of(
        release_source, {"_order_service", "_active_release_reservation"}
    )
    assert release_writers == {
        "reserve_active_release",
        "commit_active_release",
        "cancel_active_release",
    }, sorted(release_writers)


def test_the_import_set_is_closed() -> None:
    """Only downward: ports, shapes and the phase enum -- no adapter, no desktop, no Qt."""

    imported: set[str] = set()
    for node in ast.walk(ast.parse(_module_source())):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    offending = sorted(
        name
        for name in imported
        if not any(
            name == entry or name.startswith(f"{entry}.")
            for entry in ALLOWED_IMPORTS
        )
    )
    assert not offending, offending


def test_the_module_touches_no_gui_and_submits_no_order() -> None:
    """No Qt, no broker order API, no service bag.

    The three failures this pins are the three ways a boundary like this stops being one:
    a widget makes it untestable without a display, an order call moves submission out of
    the execution application, and a bag turns "who owns what?" back into a lookup.
    """

    source = _module_source()
    called = {
        name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        for name in [getattr(node.func, "attr", None) or getattr(node.func, "id", None)]
        if name
    }
    for forbidden in ("placeOrder", "cancelOrder", "reqGlobalCancel", "submit_approved"):
        assert forbidden not in called, forbidden
    for module_name in (
        "PySide6",
        "ibapi",
        "us_quant.desktop",
        "us_quant.trading.adapters",
    ):
        assert module_name not in source, module_name
    for name in FORBIDDEN_BAG_NAMES:
        assert f"self._{name}" not in source, name


def test_service_is_reachable_from_the_package_layout() -> None:
    path = Path(module.__file__ or "")

    assert path.name == "service.py"
    assert path.parent.name == "paper"
    assert path.parent.parent.name == "application"
