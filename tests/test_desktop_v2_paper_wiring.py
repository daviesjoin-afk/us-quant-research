"""Real offscreen wiring: the execution page reaches ``PaperOrchestrator.start``.

Unlike ``test_desktop_paper_launch_orchestrator.py``, which drives the capability over
fakes, this file builds a **real** ``MainWindow`` and a real ``ExecutionPage`` and
replaces only the two boundaries a test cannot honestly cross: the broker (a fake
candidate order service) and the worker (a fake task submitter that runs the task
body synchronously on the calling thread).

Everything between them is production code: the page signal, the window's
confirmation gate, the orchestrator's gates and frozen plan, the real
``PaperWorkflowController`` with its real execution lease, the real
``PaperTradingService`` candidate lifecycle, the window's session-build seam, and
``publish_armed`` constructing a real ``PaperSessionCoordinator``.

The two properties this file exists to prove are the ones only real wiring can show:

* the page intent ends at the capability -- no window launch handler participates;
* a duplicate start creates nothing: one candidate, one broker task, one lease, and
  no promotion the second time.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dataclasses
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from us_quant.desktop import MainWindow
from us_quant.desktop_v2.orchestration.paper.models import (
    DUPLICATE_MESSAGE,
    DUPLICATE_TITLE,
    PAPER_LAUNCH_ROLLBACK_CODE,
    PAPER_LAUNCH_ROLLBACK_TITLE,
    PAPER_PROMOTION_INVARIANT_CODE,
    PAPER_STRATEGY_INTEGRITY_CODE,
    PAPER_STRATEGY_INTEGRITY_TITLE,
)
from us_quant.paper_order_models import PaperBrokerState
from us_quant.trading.application.paper import PaperTradingService
from us_quant.trading.application.paper.models import PaperTradingLifecycleError
from us_quant.trading.runtime.models import AutoQuantCandidate
from us_quant.trading.runtime.workflow_state import (
    ExecutionLease,
    PaperWorkflowPhase,
    WorkflowStateError,
)
from us_quant.trading.domain.strategy import StrategyStatus


_APP = QApplication.instance() or QApplication([])


# -- boundary fakes ------------------------------------------------------


class _Connection:
    def __init__(self) -> None:
        self.connected = True
        self.open_broker_orders = 0
        self.account_alias = "DU1234567"


class _FakeCandidateService:
    """A candidate order service: a broker reading, and an ``arm``.

    Satisfies both the launch's borrow protocol and the coordinator's
    ``PaperOrderPort``, because in production they are the same object.
    """

    instances: list["_FakeCandidateService"] = []

    def __init__(self, *, connected: bool = True) -> None:
        self._connection = _Connection()
        self._connection.connected = connected
        self.connected = connected
        self.armed: dict[str, object] | None = None
        self.disconnected = 0
        self._state = PaperBrokerState(
            account_alias="DU1234567",
            net_liquidation=Decimal("25000"),
            cash=Decimal("25000"),
            available_funds=Decimal("25000"),
            buying_power=Decimal("50000"),
            daily_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            realized_pnl=Decimal("0"),
            positions=(),
            observed_at="2026-01-01T00:00:00Z",
        )
        _FakeCandidateService.instances.append(self)

    # -- the launch's borrowed slice --
    def connection_snapshot(self) -> _Connection:
        return self._connection

    def broker_state(self) -> PaperBrokerState:
        return self._state

    def arm(self, **kwargs: object) -> None:
        if not self._connection.connected:
            raise RuntimeError("Paper 订单通道尚未连接")
        self.armed = dict(kwargs)

    # -- the coordinator's order port --
    def connect(self) -> object:
        self._connection.connected = True
        self.connected = True
        return self._connection

    def disconnect(self) -> None:
        self.disconnected += 1
        self._connection.connected = False
        self.connected = False

    def fills(self):
        return ()

    def events(self):
        return ()

    def cancel(self, order_id: str) -> bool:
        return True

    def reconciliation_rows_with_latency(self, *, session_id=None, limit=1000):
        return ()

    def reconciliation_summary(self, session_id: str):
        return type("_Summary", (), {"unreconciled": 0})()

    def refresh_reconciliation_snapshot(self, session_id: str):
        return None

    def reconciliation_snapshot_is_current(self, snapshot: object) -> bool:
        return True

    def armed_account_binding_is_valid(self) -> bool:
        return True

    def armed_account_fingerprint(self) -> str:
        return "fingerprint"


class _SynchronousSubmitter:
    """Runs the task body on the calling thread and delivers its result at once.

    The window's real ``_start_task`` starts a ``TaskThread``; a test cannot wait on a
    Qt worker without an event loop, so this stands in for the *admission* and the
    delivery.  It is deliberately shaped like ``TaskSubmitter`` -- including returning
    ``False`` when it refuses -- so a duplicate start is observable the same way.
    """

    def __init__(self, *, admitted: bool = True) -> None:
        self.admitted = admitted
        self.calls: list[dict[str, object]] = []
        self.connect_count = 0

    def __call__(self, task, *, on_success, start_message, resource_group="research", **_):
        self.calls.append(
            {
                "start_message": start_message,
                "resource_group": resource_group,
            }
        )
        if not self.admitted:
            return False
        self.connect_count += 1
        on_success(task(lambda _message: None))
        return True


class _Preflight:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.checks = ()


def _candidate() -> AutoQuantCandidate:
    return AutoQuantCandidate(
        symbol="AAPL",
        name="Apple",
        sector="Technology",
        leader_tier=1,
        scan_score=Decimal("9"),
        signal="leader",
    )


def _strategy(window: MainWindow):
    """A runnable auto-rotation version from the window's own catalogue.

    Read rather than invented, so the frozen plan names a version the real
    ``build_auto_rotation_config`` can consume.
    """

    return window.strategies.get_version(
        window._selected_auto_strategy_record().version_id
    )


@pytest.fixture
def window(monkeypatch, tmp_path):
    """A real offscreen ``MainWindow`` with the broker and worker boundaries faked."""

    monkeypatch.setenv("US_QUANT_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    _FakeCandidateService.instances.clear()

    window = MainWindow()
    _APP.processEvents()

    # The broker boundary: a fake candidate, built through the service's own factory
    # seam, so the real candidate lifecycle runs on top of it.
    window.paper_trading = PaperTradingService(
        workflow_getter=lambda: window.paper_workflow,
        order_service_factory=lambda config, *, repository, extended_hours_enabled: (
            _FakeCandidateService()
        ),
    )

    # The task boundary.
    submitter = _SynchronousSubmitter()
    window._test_submitter = submitter
    window.paper_orchestrator._submit_task = submitter

    # Every refusal the capability publishes, so a test can tell *which* gate spoke.
    # That distinction matters: a duplicate intent refused by the duplicate gate is a
    # different outcome from one that got through and was refused by the workflow, and
    # only the first proves the gate is doing its job.
    window._test_refusals = []
    window.paper_orchestrator.refused.connect(
        lambda title, message: window._test_refusals.append((title, message))
    )
    # And its runtime events, so a fault's own code can be asserted rather than merely
    # "something was logged".
    window._test_events = []
    window.paper_orchestrator.runtime_event_requested.connect(
        window._test_events.append
    )

    # The facts the launch reads, so the gates pass without a live feed or account.
    window.auto_quant_candidates = (_candidate(),)
    window._test_strategy = _strategy(window)
    window.paper_orchestrator._strategy_provider = lambda: window._test_strategy
    window.paper_orchestrator._preflight_provider = lambda: _Preflight()
    window.paper_orchestrator._shadow_is_active = lambda: False
    window.paper_orchestrator._capital_limit_provider = lambda: Decimal("20000")

    # The launch begins from READY, which the window's preparation step establishes
    # (IDLE -> PREPARING -> READY).  Driven through the controller's own API rather
    # than by poking the phase, so the fixture cannot set up a state production
    # could never reach.
    window.paper_workflow.begin_preparing()
    window.paper_workflow.mark_ready()
    assert window.paper_workflow.phase is PaperWorkflowPhase.READY

    yield window
    window.close()
    window.deleteLater()


def _launch(window: MainWindow) -> None:
    """Emit the page's start intent, as the operator's click would."""

    window.execution_page.start_requested.emit()
    _APP.processEvents()


# -- the golden path -----------------------------------------------------


def test_the_page_start_intent_reaches_the_orchestrator(window: MainWindow) -> None:
    _launch(window)

    assert len(window._test_submitter.calls) == 1
    assert window._test_submitter.calls[0]["resource_group"] == "broker"
    assert window.paper_workflow.phase is PaperWorkflowPhase.RUNNING


def test_a_launch_publishes_exactly_one_session_and_promotes_once(
    window: MainWindow,
) -> None:
    _launch(window)

    assert window.paper_workflow.phase is PaperWorkflowPhase.RUNNING
    # Exactly one candidate was created, and it became the active service.
    assert len(_FakeCandidateService.instances) == 1
    candidate = _FakeCandidateService.instances[0]
    assert window.paper_trading.has_order_service() is True
    assert window.paper_trading.has_candidate("1") is False
    # And the *orchestrator* armed it with the frozen shortlist -- through the real
    # window seam, which composes the runtime but no longer arms it itself.
    assert candidate.armed is not None
    assert candidate.armed["session_id"] is not None
    assert candidate.armed["allowed_symbols"] == ("AAPL",)
    # The notional is the seam's computed sizing, threaded through the build result.
    assert candidate.armed["max_order_notional"] > 0


def test_the_window_build_seam_does_not_arm_the_channel(window: MainWindow) -> None:
    """Arming belongs to the orchestrator, not to the composition root's seam.

    Asserted structurally as well as behaviourally: the seam's *body* must not call
    ``arm``.  Otherwise the ordered trace in the capability's own tests would still
    pass while the real constraint silently stopped being testable.
    """

    import inspect

    seam = inspect.getsource(MainWindow._build_paper_session)
    assert ".arm(" not in seam
    assert "max_order_notional=" in seam  # it returns the sizing instead


def test_an_inconsistent_catalogue_version_does_not_escape_the_qt_slot(
    window: MainWindow,
) -> None:
    """A catalogue fault must be *reported*, not thrown out of a signal handler.

    This is the real-path regression for the escaping-exception defect.  The operator's
    confirmation is set before ``start()`` runs, so an exception leaving the slot would
    leave ``arm_confirmed`` true with no session, no refusal, no log line and no event --
    the UI would look armed and idle while a real catalogue fault went unreported.
    """

    real = window._test_strategy
    window.paper_orchestrator._strategy_provider = lambda: dataclasses.replace(
        real, identity=dataclasses.replace(real.identity, parameter_hash="0" * 64)
    )

    window._test_refusals.clear()
    _launch(window)  # must not raise

    # Nothing started, and the operator is not left looking armed.
    assert window.paper_workflow.phase is PaperWorkflowPhase.READY
    assert window.paper_workflow.lease is ExecutionLease.NONE
    assert window.paper_trading.has_order_service() is False
    assert window.execution_page.arm_confirmed() is False
    assert window._test_submitter.connect_count == 0
    # And they were told, with an error event under its own code.
    assert window._test_refusals[-1][0] == PAPER_STRATEGY_INTEGRITY_TITLE
    assert window._test_events[-1].code == PAPER_STRATEGY_INTEGRITY_CODE
    assert window._test_events[-1].severity == "error"


def test_the_session_reaches_the_window_through_the_result(
    window: MainWindow,
) -> None:
    """The window renders the result it is handed, and keeps no runtime handle.

    v2O-E2 moved the active session's run into the capability, so the window is handed
    a ``PaperSessionResult`` and nothing else: the snapshot it draws is presentation,
    and the canonical session truth is asked for rather than cached.
    """

    _launch(window)

    assert window.paper_orchestrator.result is not None
    assert window.paper_orchestrator.runtime_active is True
    # Presentation: the capability retains the immutable view the route draws, so the
    # route can still show the session it is reporting on.  Since v2O-E4 that fact lives
    # on the capability -- the window keeps no copy, which is what stops it becoming a
    # second owner of a session fact.
    presentation = window.paper_orchestrator.presentation
    assert presentation is not None
    assert presentation.session_id
    # And the window holds no runtime handle of its own -- the second owner is gone.
    assert not hasattr(window, "trading_runtime")


def test_the_lease_is_held_by_the_workflow_after_a_successful_launch(
    window: MainWindow,
) -> None:
    _launch(window)

    assert window.paper_workflow.lease is ExecutionLease.PAPER


def test_no_main_window_launch_handler_participated(window: MainWindow) -> None:
    """The retired handlers are gone, and nothing forwarded to them.

    The check is structural as well as behavioural: if a shim had been left behind,
    ``hasattr`` would find it and this test would fail.
    """

    for retired in (
        "_start_auto_quant",
        "_auto_order_service_connected",
        "_reject_unpublished_auto_candidate",
        "_reject_auto_launch_without_service",
        "_current_auto_launch_matches",
        "_reset_auto_launch_controls",
    ):
        assert not hasattr(window, retired), retired

    _launch(window)

    # And the launch really happened through the capability.
    assert len(window._test_submitter.calls) == 1
    assert window.paper_workflow.phase is PaperWorkflowPhase.RUNNING


# -- duplicate signal ----------------------------------------------------


def test_a_duplicate_start_after_a_running_session_creates_nothing(
    window: MainWindow,
) -> None:
    """Once a session owns the slot, a second intent must launch nothing.

    The first emit runs the whole sequence synchronously, so the phase is ``RUNNING``
    by the time the second arrives.  The second attempt therefore gets past the
    duplicate gate and is refused by the *workflow* -- ``begin_connecting`` from
    ``RUNNING`` is not a legal transition.  What matters is the outcome either way: no
    second candidate, no second task, and the live session untouched.
    """

    _launch(window)
    assert window.paper_workflow.phase is PaperWorkflowPhase.RUNNING
    assert len(_FakeCandidateService.instances) == 1

    window.execution_page.start_requested.emit()
    _APP.processEvents()

    assert len(_FakeCandidateService.instances) == 1
    assert len(window._test_submitter.calls) == 1
    assert window.paper_trading.has_order_service() is True
    # The running session's lease was neither released nor replaced.
    assert window.paper_workflow.lease is ExecutionLease.PAPER
    assert window.paper_workflow.phase is PaperWorkflowPhase.RUNNING


def test_a_duplicate_start_during_connecting_creates_nothing(
    window: MainWindow,
) -> None:
    """The sharper case, and the one the duplicate gate actually guards.

    The submitter *holds* rather than admits, so the first attempt stays genuinely
    ``CONNECTING``.  A second intent must then create no candidate and no task, and
    must touch neither the plan nor the lease.

    Two layers refuse it, and both are asserted because they are different
    guarantees: the window's confirmation gate catches it first (it is the operator's
    entry point and may not show a confirmation for an attempt already in flight), and
    the capability's own gate is the one that holds under any caller -- which the
    direct ``start()`` at the end of this test exercises.
    """

    window._test_submitter.admitted = False
    window.execution_page.start_requested.emit()
    _APP.processEvents()
    # The task was refused, so the attempt was unwound and nothing was left behind.
    assert len(_FakeCandidateService.instances) == 0
    assert window.paper_workflow.phase is PaperWorkflowPhase.READY

    # Now let one attempt be genuinely in flight.
    holder = _HoldingSubmitter()
    window.paper_orchestrator._submit_task = holder
    window.execution_page.start_requested.emit()
    _APP.processEvents()
    assert window.paper_workflow.phase is PaperWorkflowPhase.CONNECTING
    assert len(holder.calls) == 1
    plan_before = window.paper_workflow.active_plan

    # The duplicate page intent: refused at the window's own gate.
    window.execution_page.start_requested.emit()
    _APP.processEvents()

    assert len(holder.calls) == 1
    assert len(_FakeCandidateService.instances) == 0
    assert window.paper_workflow.active_plan is plan_before
    assert window.paper_workflow.lease is ExecutionLease.PAPER

    # And the capability refuses it too, for a caller that skips the page entirely --
    # which is what makes the guarantee the capability's rather than the gate's.
    window.paper_orchestrator.start()

    assert len(holder.calls) == 1
    assert window.paper_workflow.active_plan is plan_before
    assert window.paper_workflow.lease is ExecutionLease.PAPER
    assert window._test_refusals[-1] == (DUPLICATE_TITLE, DUPLICATE_MESSAGE)
    


class _HoldingSubmitter:
    """Admits the task but never delivers its result -- an in-flight connect."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, task, *, on_success, start_message, resource_group="research", **_):
        self.calls.append(
            {
                "start_message": start_message,
                "resource_group": resource_group,
            }
        )
        return True


# -- the publication invariant ------------------------------------------
#
# The regression this round closes.  ``publish_armed`` used to be followed by a
# promotion that could still be refused, and a refusal there left **``RUNNING`` with
# no active owner**: measured on this very wiring (phase RUNNING, ``has_order_service``
# False, lease PAPER held, a published coordinator whose order port was the armed
# broker channel, the watchdog still reaching the workflow, and manual reconciliation
# never entered because it starts at ``has_order_service``).
#
# Ownership is now taken *before* publication, so that state cannot be built.  These
# tests assert the invariant by injection rather than by reading the source: every
# place a launch can be refused is driven on the real thing.

_RUNNING_PHASES = (
    PaperWorkflowPhase.RUNNING,
    PaperWorkflowPhase.PAUSED,
    PaperWorkflowPhase.STOPPING,
)


def _assert_never_ownerless_while_running(window: MainWindow) -> None:
    """``RUNNING`` must imply an active owner -- the whole round in one assertion.

    The market fan-out and the watchdog heartbeat both drive the workflow on the phase
    alone, so an ownerless ``RUNNING`` session is one the capability keeps feeding
    orders through while nothing can adopt it.
    """

    if window.paper_trading.phase() in _RUNNING_PHASES:
        assert window.paper_trading.has_order_service() is True


def _assert_rolled_back(window: MainWindow) -> None:
    """A refused launch leaves the window exactly as it found it."""

    assert window.paper_trading.phase() is PaperWorkflowPhase.READY
    assert window.paper_workflow.lease is ExecutionLease.NONE
    assert window.paper_trading.has_order_service() is False
    assert window.paper_trading.has_candidate("1") is False
    assert window.execution_page.arm_confirmed() is False
    assert window._test_submitter.connect_count == 1
    _assert_never_ownerless_while_running(window)


def _inject_bad_account(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """A broker gate refusal, before anything is built."""
    monkeypatch.setattr(
        _FakeCandidateService,
        "broker_state",
        lambda self: dataclasses.replace(self._state, net_liquidation=Decimal("0")),
    )


def _inject_build_failure(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """The composition seam fails after the candidate exists."""
    window.paper_orchestrator._build_session = _raising("runtime build failed")


def _inject_arm_failure(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """The armed channel refuses, after a successful build."""
    monkeypatch.setattr(
        _FakeCandidateService, "arm", _raising("channel not connected")
    )


def _inject_reserve_failure(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """The order-service owner refuses to take the promotion slot."""
    window.paper_trading.reserve_candidate_promotion = _raising(
        "a Paper order service is already active",
        error=PaperTradingLifecycleError,
    )


def _inject_publish_failure(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """Publication itself fails -- the exact boundary the rollback turns on."""
    window.paper_workflow.publish_armed = _raising(
        "Stale or invalid Paper launch publication.",
        error=WorkflowStateError,
    )


def _raising(message: str, *, error: type[Exception] = RuntimeError):
    def boom(*_args: object, **_kwargs: object) -> object:
        raise error(message)

    return boom


_ROLLBACK_INJECTIONS = [
    pytest.param(_inject_bad_account, id="broker-gate"),
    pytest.param(_inject_build_failure, id="runtime-build"),
    pytest.param(_inject_arm_failure, id="arm"),
    pytest.param(_inject_reserve_failure, id="reserve"),
    pytest.param(_inject_publish_failure, id="publish"),
]


@pytest.mark.parametrize("inject", _ROLLBACK_INJECTIONS)
def test_every_pre_publication_failure_rolls_back_and_leaves_no_owner(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, inject
) -> None:
    """Each refusal must unwind the attempt completely, and never publish.

    The bounded claim is that a launch refused at *any* point before publication ends
    with nothing owned, nothing leased, no candidate and no armed channel -- so no
    ownerless session can exist for the window to keep driving afterwards.
    """

    inject(window, monkeypatch)

    _launch(window)  # must not raise out of the Qt slot

    _assert_rolled_back(window)
    assert window._test_events == [] or all(
        event.code != PAPER_PROMOTION_INVARIANT_CODE for event in window._test_events
    )
    assert window.paper_orchestrator.result is None
    assert window.paper_orchestrator.runtime_active is False
    # And nothing was published *for display* either: a refused launch has no session to
    # retain, and ``presentation`` is built from published results only.
    assert window.paper_orchestrator.presentation is None


def test_a_commit_failure_after_publication_cannot_orphan_the_session(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one injection that is *not* a rollback, and the state it must not leave.

    Ending the claim can only be refused through a bug -- this launch took the
    reservation and nothing else may replace it -- but it is precisely the injection
    that produced the ownerless running session in the retired order, so it is driven
    directly.  The session is live and **owned**; the window can still find it, which
    is what every recovery path starts from.
    """

    window.paper_trading.commit_candidate_promotion = _raising(
        "stale or foreign Paper promotion reservation",
        error=PaperTradingLifecycleError,
    )

    _launch(window)  # must not raise out of the Qt slot

    assert window.paper_workflow.phase is PaperWorkflowPhase.RUNNING
    assert window.paper_workflow.lease is ExecutionLease.PAPER
    # The property the retired order could not hold: RUNNING with an owner.
    assert window.paper_trading.has_order_service() is True
    assert window.paper_trading.is_connected() is True
    _assert_never_ownerless_while_running(window)
    # Reported, with its own code, rather than silently absorbed.
    assert window._test_events[-1].code == PAPER_PROMOTION_INVARIANT_CODE
    assert window._test_events[-1].severity == "error"
    # And the recovery entry point's own first predicate now passes, which is what
    # makes the session adoptable instead of stranded.
    assert window.paper_trading.has_order_service() is True


def test_a_publication_failure_releases_the_promotion_for_the_next_launch(
    window: MainWindow,
) -> None:
    """The rollback's job, checked by *outcome* rather than by inspection.

    A reservation that was not given back would leave the service permanently
    unreservable -- fail-closed, but a dead end for the operator.  The probe is a
    second launch on the same service: it can only reach ``RUNNING`` if the first
    launch left nothing claimed.
    """

    real = window.paper_workflow.publish_armed
    window.paper_workflow.publish_armed = _raising(
        "Stale or invalid Paper launch publication.",
        error=WorkflowStateError,
    )

    _launch(window)
    _assert_rolled_back(window)

    window.paper_workflow.publish_armed = real
    _launch(window)

    assert window.paper_workflow.phase is PaperWorkflowPhase.RUNNING
    assert window.paper_trading.has_order_service() is True
    assert window.paper_workflow.lease is ExecutionLease.PAPER
    _assert_never_ownerless_while_running(window)


# -- the rollback's own invariant ---------------------------------------


def _assert_rollback_refused(window: MainWindow) -> None:
    """A rollback that cannot prove the ownership returned unwinds nothing."""

    assert window.paper_trading.phase() is PaperWorkflowPhase.CONNECTING
    assert window.paper_workflow.active_plan is not None
    assert window.paper_workflow.lease is ExecutionLease.PAPER
    assert window.paper_trading.has_order_service() is True
    assert window.execution_page.arm_confirmed() is True


def test_a_rollback_that_cannot_give_the_slot_back_keeps_the_lease(
    window: MainWindow,
) -> None:
    """Publication refused *and* the promotion not shown to have returned.

    The dangerous completion is the one this forbids: dispose the candidate, reject the
    plan and release PAPER, while an armed channel may still hold the slot and nothing
    excludes Shadow.  Measured on this wiring before the guard existed: ``READY``,
    ``lease NONE``, the active owner still held, the armed channel still alive, and an
    ordinary "launch failed" as the only operator-visible line.

    Since the lease is shared with Shadow, releasing it here would be the one
    unforgivable move -- so the fault is reported and the attempt stays in flight.
    """

    window.paper_workflow.publish_armed = _raising(
        "Stale or invalid Paper launch publication.",
        error=WorkflowStateError,
    )
    window.paper_trading.cancel_candidate_promotion = lambda _reservation: False

    _launch(window)  # must not raise out of the Qt slot

    _assert_rollback_refused(window)
    assert window._test_refusals[-1][0] == PAPER_LAUNCH_ROLLBACK_TITLE
    assert window._test_events[-1].code == PAPER_LAUNCH_ROLLBACK_CODE
    assert window._test_events[-1].severity == "error"
    # And it is its own code: an ownerless *live* session and a stuck launch are
    # different situations for whoever is on call.
    assert window._test_events[-1].code != PAPER_PROMOTION_INVARIANT_CODE
    # The operator's own words are the launch error plus what could not be undone.
    assert "Stale or invalid Paper launch publication." in window._test_refusals[-1][1]


def test_a_refused_clear_cannot_be_reached_between_the_reserve_and_the_commit(
    window: MainWindow,
) -> None:
    """``clear_active`` refuses a reserved slot, so the launch keeps its owner.

    The other public way to empty the active slot, driven at the one instant it would
    matter: after the reserve, before publication.  Without the refusal the launch
    publishes a session whose owner has already been dropped -- the ownerless
    ``RUNNING`` state, re-entered through a different method.
    """

    real_publish = window.paper_workflow.publish_armed

    def steal_then_publish(*args, **kwargs):
        window.paper_trading.disconnect()
        window.paper_trading.clear_active()  # must refuse
        return real_publish(*args, **kwargs)

    window.paper_workflow.publish_armed = steal_then_publish
    window._test_refusals.clear()

    _launch(window)

    # The theft was refused, so publication never ran and the attempt rolled back
    # the ordinary way: nothing owned, nothing published, nothing leased.
    _assert_rolled_back(window)
    assert window.paper_orchestrator.result is None
    assert window.paper_orchestrator.runtime_active is False
    _assert_never_ownerless_while_running(window)


def test_a_stuck_launch_is_not_closed_over_silently(window: MainWindow) -> None:
    """``clear_active``'s refusal must not escape ``closeEvent``.

    The close path calls ``clear_active`` unguarded, so a slot the service refuses to
    release turned into an exception thrown out of the Qt override -- and because
    ``runtime_supervisor.begin_shutdown()`` runs first, everything after the Paper gate
    (shadow shutdown, heartbeats, the market stream, the worker joins) was skipped
    while the window still went away.
    """

    window.paper_workflow.publish_armed = _raising(
        "Stale or invalid Paper launch publication.",
        error=WorkflowStateError,
    )
    window.paper_trading.cancel_candidate_promotion = lambda _reservation: False
    _launch(window)
    _assert_rollback_refused(window)

    torn_down: list[str] = []
    window.shadow_orchestrator.shutdown = lambda *a, **k: torn_down.append("shadow")
    window.runtime_supervisor.shutdown = lambda *a, **k: torn_down.append("supervisor")

    closed = window.close()  # must not raise

    # Refused, so nothing was torn down and the ownership is still on the books.
    assert closed is False
    assert torn_down == []
    assert window.paper_trading.has_order_service() is True
    assert window.paper_workflow.lease is ExecutionLease.PAPER


# -- v2O-E2: the active session's run reaches the capability -------------
#
# The same rule as the launch half above, for the session half: the page's session
# controls and the watchdog heartbeat end at ``PaperOrchestrator``, which owns the
# phase gate, the ingress stamp and the one result path.  Everything between the page
# and the capability here is production code -- a real ``MainWindow``, a real
# ``ExecutionPage``, the real controller with its real lease, and the real
# ``PaperSessionCoordinator`` built by ``publish_armed``.


def test_the_page_pause_control_reaches_the_workflow(window: MainWindow) -> None:
    """A real click, through the real page and the real controller."""

    _launch(window)
    assert window.paper_workflow.phase is PaperWorkflowPhase.RUNNING

    window.execution_page.pause_requested.emit()
    _APP.processEvents()

    assert window.paper_workflow.phase is PaperWorkflowPhase.PAUSED


def test_the_page_resume_control_reaches_the_workflow(window: MainWindow) -> None:
    _launch(window)
    window.execution_page.pause_requested.emit()
    _APP.processEvents()
    assert window.paper_workflow.phase is PaperWorkflowPhase.PAUSED

    window.execution_page.resume_requested.emit()
    _APP.processEvents()

    assert window.paper_workflow.phase is PaperWorkflowPhase.RUNNING


def test_the_page_stop_control_reaches_the_workflow(window: MainWindow) -> None:
    """The stop is judged against the market snapshot, and the phase moves."""

    _launch(window)

    window.execution_page.stop_requested.emit()
    _APP.processEvents()

    assert window.paper_workflow.phase in {
        PaperWorkflowPhase.STOPPING,
        PaperWorkflowPhase.FINALIZED,
    }


def test_the_watchdog_heartbeat_reaches_the_workflow_through_the_capability(
    window: MainWindow,
) -> None:
    """The timer's real wiring: ``timeout`` -> ``PaperOrchestrator.poll`` -> workflow.

    Asserted by emitting the timer's own signal, so a mis-wired heartbeat is observed
    rather than inferred: nothing in this test calls ``poll`` by hand.  The stamp is
    asserted to be unset rather than cleared, so the suppression window cannot mask a
    missing connection.
    """

    _launch(window)
    assert window.paper_orchestrator._last_stream_ingress_monotonic is None

    polled: list[int] = []
    real_poll = window.paper_workflow.poll

    def counting_poll():
        polled.append(1)
        return real_poll()

    window.paper_workflow.poll = counting_poll

    window.paper_order_timer.timeout.emit()
    _APP.processEvents()

    assert polled == [1]


def test_the_holding_gates_ask_the_capability_not_a_runtime_handle(
    window: MainWindow,
) -> None:
    """``_paper_runtime_is_active`` -- the fact Shadow is refused on -- is the session's.

    Before the launch there is no session; after it there is one.  The window answers
    with no runtime handle either way, which is the point of the round: that handle was
    a second owner of a live session, kept beside the workflow's own.
    """

    assert window._paper_runtime_is_active() is False
    assert not hasattr(window, "trading_runtime")
    assert not hasattr(window, "paper_execution_health")

    _launch(window)

    assert window._paper_runtime_is_active() is True


def test_the_channel_probe_is_still_refused_while_a_session_is_live(
    window: MainWindow,
) -> None:
    """The probe gate lost its runtime clause and must not have lost its effect."""

    _launch(window)
    connects = window._test_submitter.connect_count

    window._check_auto_order_channel()
    _APP.processEvents()

    assert window._channel_check_inflight is False
    assert window._test_submitter.connect_count == connects


# -- v2O-E3: the operator's resume confirmation --------------------------
#
# The one recovery intent that legitimately hops through the window.  It is a
# ``QMessageBox``, and the capability may not import one -- so the hop is the point.  The
# failure this guards is not a crash: it is a *question asked and then acted on anyway*,
# or a confirmation counted twice, and both are invisible to a test that only checks the
# handler exists.

class _ResumeWorkflow:
    """The two facts the confirmation reads, and the one call it makes.

    ``result`` is ``None`` deliberately: the fixture closes the window at teardown, and
    the close asks the service whether a session is still awaiting finalization.  A fake
    that raised there would make this test file fail for a reason that has nothing to do
    with the confirmation.
    """

    result = None

    def __init__(self, *, ready: bool, evidence_id: str | None) -> None:
        self.phase = (
            PaperWorkflowPhase.RECONCILING_READY
            if ready
            else PaperWorkflowPhase.HALTED
        )
        self._evidence_id = evidence_id
        self.confirmed: list[str] = []

    @property
    def reconciliation_evidence(self):
        if self._evidence_id is None:
            return None
        return type("_Evidence", (), {"evidence_id": self._evidence_id})()

    def confirm_manual_resume(self, evidence_id: str) -> object:
        # Records the attempt before doing anything, so "the confirmation was never
        # reached" stays distinguishable from "it was reached and refused".
        self.confirmed.append(evidence_id)
        return object()


def _ask_resume(window: MainWindow) -> None:
    """Emit the page's confirmation intent, as the operator's click would."""

    window.execution_page.resume_reconciliation_requested.emit()
    _APP.processEvents()


def test_declining_the_resume_confirmation_never_reaches_the_capability(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``No`` is not a slow yes: nothing is submitted and nothing is confirmed."""

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    workflow = _ResumeWorkflow(ready=True, evidence_id="reconciliation-1")
    window.paper_workflow = workflow  # type: ignore[assignment]
    window._test_submitter.calls.clear()

    _ask_resume(window)

    assert workflow.confirmed == []
    assert window._test_submitter.calls == []
    # And the proof is still where it was, so the operator can change their mind.
    assert workflow.phase is PaperWorkflowPhase.RECONCILING_READY


def test_confirming_the_resume_reaches_the_capability_exactly_once(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``Yes`` hands the current proof's id over once, on the broker group."""

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    workflow = _ResumeWorkflow(ready=True, evidence_id="reconciliation-1")
    window.paper_workflow = workflow  # type: ignore[assignment]
    window._test_submitter.calls.clear()

    _ask_resume(window)

    assert workflow.confirmed == ["reconciliation-1"]
    assert len(window._test_submitter.calls) == 1
    assert window._test_submitter.calls[0]["resource_group"] == "broker"


def test_a_confirmation_with_no_current_proof_submits_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a stray ``Yes`` cannot confirm a proof that is not there.

    The window asks and forwards; the capability re-reads the phase and the evidence and
    refuses.  That division is the point: a handler that decided for itself whether a
    proof was current would be a second reader of the workflow's evidence.
    """

    asked: list[int] = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: asked.append(1) or QMessageBox.Yes
    )
    workflow = _ResumeWorkflow(ready=False, evidence_id=None)
    window.paper_workflow = workflow  # type: ignore[assignment]
    window._test_submitter.calls.clear()

    _ask_resume(window)

    assert asked == [1], "the window's job is to ask"
    assert workflow.confirmed == []
    assert window._test_submitter.calls == []
