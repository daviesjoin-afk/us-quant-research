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

from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from us_quant.desktop import MainWindow
from us_quant.desktop_v2.orchestration.paper.models import (
    DUPLICATE_MESSAGE,
    DUPLICATE_TITLE,
)
from us_quant.paper_order_models import PaperBrokerState
from us_quant.trading.application.paper import PaperTradingService
from us_quant.trading.runtime.models import AutoQuantCandidate
from us_quant.trading.runtime.workflow_state import (
    ExecutionLease,
    PaperWorkflowPhase,
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


def test_the_session_reaches_the_window_through_the_publication(
    window: MainWindow,
) -> None:
    """The window adopts the runtime it is handed, and renders the result."""

    _launch(window)

    assert window.trading_runtime is not None
    assert window.auto_quant_snapshot is not None
    assert window.auto_quant_snapshot.session_id is not None


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
