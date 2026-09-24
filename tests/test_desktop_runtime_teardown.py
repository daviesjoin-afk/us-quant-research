"""Integration coverage for the desktop's supervisor-backed teardown.

The unit tests in ``tests/test_runtime_supervisor.py`` pin the supervisor's
contract in isolation.  These tests pin the *wiring*: that ``MainWindow``
actually registers the resources it owns and that a real close releases them.

The regression this exists for is concrete: ``paper_order_timer`` and
``extended_session_timer`` are started in ``_build_ui`` and, before the
supervisor, had no ``stop()`` call anywhere in the module.  Asserting they are
active after construction is what makes the teardown assertions meaningful --
otherwise a timer that never started would also read as "not running".
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.runtime_supervisor import STATE_STOPPED, RuntimeSupervisor
from us_quant.trading.runtime.workflow_state import ExecutionLease


_APP = QApplication.instance() or QApplication([])


def _window() -> MainWindow:
    window = MainWindow()
    _APP.processEvents()
    return window


def test_main_window_registers_its_owned_runtime_resources() -> None:
    window = _window()
    try:
        names = [component.name for component in window.runtime_supervisor.snapshot().components]
        assert set(names) == {
            "closing_gate",
            "paper_order_heartbeat",
            "extended_session_heartbeat",
            "stream_snapshot_timer",
            "market_data_stream",
            "background_workers",
        }
    finally:
        window.deleteLater()


def test_heartbeat_timers_are_running_before_close() -> None:
    """Guards the premise of the teardown test below."""

    window = _window()
    try:
        assert window.paper_order_timer.isActive()
        assert window.extended_session_timer.isActive()
    finally:
        window.deleteLater()


def test_close_stops_every_registered_heartbeat_timer() -> None:
    window = _window()
    window.close()
    _APP.processEvents()

    assert not window.paper_order_timer.isActive()
    assert not window.extended_session_timer.isActive()
    # The market poll timer is the orchestrator's, so its state is read there.
    assert not window.market_orchestrator.polling_active

    states = {
        component.name: component.state
        for component in window.runtime_supervisor.snapshot().components
    }
    assert states["paper_order_heartbeat"] == STATE_STOPPED
    assert states["extended_session_heartbeat"] == STATE_STOPPED
    assert states["closing_gate"] == STATE_STOPPED


def test_close_raises_the_admission_gate() -> None:
    window = _window()
    window.close()
    _APP.processEvents()

    assert window._closing is True


def test_admission_gate_refuses_new_tasks_after_close() -> None:
    """A task requested during/after teardown must be refused, not started."""

    window = _window()
    window.close()
    _APP.processEvents()

    started: list[str] = []

    def task(report: object) -> None:  # pragma: no cover - must not run
        started.append("ran")

    accepted = window._start_task(
        task,
        on_success=lambda result: None,
        start_message="closed-out task",
    )

    assert accepted is False
    assert started == []


def test_closing_twice_is_safe() -> None:
    window = _window()
    window.close()
    _APP.processEvents()
    window.close()
    _APP.processEvents()

    assert window.runtime_supervisor.errors() == ()


def test_supervisor_module_imports_no_gui_toolkit() -> None:
    """The supervisor must stay importable without a GUI toolkit.

    Checked structurally over the AST rather than by grepping the source, so
    a comment mentioning PySide6 cannot make this pass or fail spuriously.
    """

    import ast
    import pathlib

    import us_quant.runtime_supervisor as module

    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert not [name for name in imported if name.startswith("PySide6")]
    assert not [name for name in imported if name.startswith("us_quant")]


# -- close ordering: the admission gate runs before the task check -------


class _BlockingWorker:
    """A real ``TaskThread`` parked on an ``Event``, released in a ``finally``."""

    def __init__(self, window: MainWindow, resource_group: str = "universe") -> None:
        from threading import Event as ThreadingEvent

        from us_quant.desktop import TaskThread

        self.release = ThreadingEvent()
        self.started = ThreadingEvent()

        def task(progress: object) -> str:
            self.started.set()
            self.release.wait(30)
            return "done"

        self.thread = TaskThread(task, resource_group=resource_group)
        window.task_controller.register(self.thread)
        self.window = window

    def __enter__(self):
        self.thread.start()
        assert self.started.wait(10), "blocking task never started"
        _APP.processEvents()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release.set()
        self.thread.wait(10_000)
        _APP.processEvents()
        return None


def _close_verdict(window: MainWindow) -> bool:
    """Run the real ``closeEvent`` and report whether it accepted the close."""

    from PySide6.QtGui import QCloseEvent

    event = QCloseEvent()
    window.closeEvent(event)
    return bool(event.isAccepted())


def _silence_dialogs(monkeypatch) -> None:
    """The close path reports to the operator; offscreen must not block on it."""

    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.information",
        staticmethod(lambda *args, **kwargs: None),
    )
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.warning",
        staticmethod(lambda *args, **kwargs: None),
    )


def test_close_with_a_running_task_is_ignored_but_raises_the_gate(
    monkeypatch,
) -> None:
    """Requirement 1: closing while a task runs must gate first, then defer."""

    _silence_dialogs(monkeypatch)
    window = _window()
    try:
        with _BlockingWorker(window):
            assert window._closing is False

            accepted = _close_verdict(window)

            assert accepted is False
            assert window._closing is True
    finally:
        window.deleteLater()


def test_close_with_a_running_task_refuses_new_tasks(monkeypatch) -> None:
    """Requirement 1 (continued): the gate must actually reject work."""

    _silence_dialogs(monkeypatch)
    window = _window()
    try:
        with _BlockingWorker(window):
            _close_verdict(window)

            ran: list[str] = []
            accepted = window._start_task(
                lambda report: ran.append("ran"),
                on_success=lambda result: None,
                start_message="post-close task",
                resource_group="research",
            )

            assert accepted is False
            assert ran == []
    finally:
        window.deleteLater()


def test_close_sets_the_universe_refresh_cancel_event(monkeypatch) -> None:
    """Requirement 2: a cancellable network task is asked to stop.

    The event is no longer a window attribute -- the capability owns it -- so
    this drives the real :meth:`request_refresh` path and captures the event the
    domain call was handed.  That is a stronger test than the old one: it proves
    the event *the running refresh actually polls* is the one close sets, rather
    than an object the test parked on the window itself.
    """

    _silence_dialogs(monkeypatch)
    window = _window()
    try:
        # A different resource group: the refresh this test admits needs the
        # ``universe`` group free, and the blocking worker only has to be *a*
        # running task for the close to be deferred.
        with _BlockingWorker(window, resource_group="research"):
            from threading import Event as ThreadingEvent

            from us_quant.universe import UniverseRecord, UniverseSnapshot

            entered = ThreadingEvent()
            seen: dict = {}

            def refresh(*, should_stop, progress=None):
                seen["should_stop"] = should_stop
                entered.set()
                return UniverseSnapshot(
                    generated_at=datetime.now(timezone.utc),
                    source_timestamps={},
                    records=(
                        UniverseRecord(
                            symbol="AAPL",
                            name="Apple",
                            exchange="NASDAQ",
                            security_type="STK",
                        ),
                    ),
                )

            monkeypatch.setattr(
                window.universe_orchestrator._service, "refresh", refresh
            )
            monkeypatch.setattr(
                window.universe_orchestrator._page, "render", lambda view: None
            )

            window.universe_orchestrator.request_refresh()

            # The task runs on a worker thread, so wait for the domain call to
            # actually start rather than racing it: asserting on an event that
            # was never handed out would pass for the wrong reason.
            assert entered.wait(10), "the refresh never reached the service"
            assert seen["should_stop"]() is False

            _close_verdict(window)

            assert seen["should_stop"]() is True
    finally:
        window.deleteLater()


def test_close_with_a_running_task_does_not_tear_down_paper_or_kill_threads(
    monkeypatch,
) -> None:
    """Requirement 3: no dangerous teardown and no thread termination."""

    _silence_dialogs(monkeypatch)
    window = _window()
    try:
        with _BlockingWorker(window) as worker:
            disconnected: list[str] = []

            class _ServiceSpy:
                def connect(self) -> object:
                    return self

                def connection_snapshot(self):
                    class _Connection:
                        connected = False

                    return _Connection()

                def disconnect(self) -> None:  # pragma: no cover - must not run
                    disconnected.append("disconnect")

            _install_order_service_spy(window, _ServiceSpy())
            shutdowns: list[str] = []
            real_shutdown = window.runtime_supervisor.shutdown

            def spy_shutdown():
                shutdowns.append("shutdown")
                return real_shutdown()

            window.runtime_supervisor.shutdown = spy_shutdown  # type: ignore[method-assign]

            accepted = _close_verdict(window)

            assert accepted is False
            # No Paper/broker teardown, no supervisor release.
            assert disconnected == []
            assert shutdowns == []
            assert window.paper_trading.has_order_service() is True
            # The heartbeat timers were not released either: phase one only
            # signals, it does not release.
            assert window.paper_order_timer.isActive()
            # The worker was asked to stop, never killed.
            assert worker.thread.isRunning()
            assert worker.thread.isFinished() is False
    finally:
        window.deleteLater()


def test_close_after_the_task_finished_completes_the_teardown(
    monkeypatch,
) -> None:
    """Requirement 4: the second close proceeds once the task has exited."""

    _silence_dialogs(monkeypatch)
    window = _window()
    try:
        with _BlockingWorker(window):
            assert _close_verdict(window) is False
            assert window._closing is True

        # The task has now exited; a second close must finish the job.
        assert window._running_workers() == []
        assert _close_verdict(window) is True

        assert not window.paper_order_timer.isActive()
        assert not window.extended_session_timer.isActive()
        assert not window.market_orchestrator.polling_active
        states = {
            component.name: component.state
            for component in window.runtime_supervisor.snapshot().components
        }
        assert states["background_workers"] == STATE_STOPPED
        assert states["closing_gate"] == STATE_STOPPED
        assert window.runtime_supervisor.errors() == ()
    finally:
        window.deleteLater()


def test_close_defers_the_paper_session_without_raising_the_gate(
    monkeypatch,
) -> None:
    """The Paper-finalized branch must stay reachable after the gate moved.

    Gating first must not make an unfinalized Paper session unclosable: the
    session's own zero-state proof is still admitted, and the branch still
    refuses to exit.
    """

    from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase
    from us_quant.trading.runtime.workflow_state import WorkflowStateError

    _silence_dialogs(monkeypatch)
    window = _window()
    real_workflow = window.paper_workflow

    class _State:
        finalized = False

    class _Result:
        state = _State()

    class _UnfinalizedWorkflow:
        """Just enough to drive the close path's Paper branch."""

        phase = PaperWorkflowPhase.RUNNING
        result = _Result()

        def request_stop(self, _snapshot: object) -> object:
            raise WorkflowStateError("no runtime in this test")

    try:
        window.paper_workflow = _UnfinalizedWorkflow()  # type: ignore[assignment]

        accepted = _close_verdict(window)

        assert accepted is False
        assert window._closing is True

        # The proof that finalizes the session is admitted even though the
        # gate is down, or the window could never close.
        admitted = window._start_task(
            lambda report: None,
            on_success=lambda result: None,
            start_message="finalization proof",
            resource_group="broker",
            suppress_busy_message=True,
            shutdown_essential=True,
        )
        assert admitted is True
    finally:
        window.paper_workflow = real_workflow
        for worker in window._running_workers():
            worker.wait(5_000)
        _APP.processEvents()
        window.deleteLater()


def test_a_non_essential_task_is_still_refused_once_closing(monkeypatch) -> None:
    """The shutdown-essential exemption must stay narrow.

    Everything except the close path's own proof has to be refused, including
    other broker-group tasks that would otherwise share its resource group.
    """

    _silence_dialogs(monkeypatch)
    window = _window()
    try:
        with _BlockingWorker(window):
            _close_verdict(window)

            for group in ("broker", "research", "universe"):
                assert (
                    window._start_task(
                        lambda report: None,
                        on_success=lambda result: None,
                        start_message="ordinary task",
                        resource_group=group,
                        suppress_busy_message=True,
                    )
                    is False
                )
    finally:
        window.deleteLater()


# -- a refused close must not lock the Paper recovery path ---------------


class _HaltedPaperWorkflow:
    """A controller stub in a phase only the operator can leave.

    ``HALTED``/``RECONCILING``/``RECONCILING_READY`` have no automatic exit:
    each is left by an explicit reconciliation step, and every one of those
    steps is a task.  This is the shape the deadlock came from.
    """

    def __init__(self, phase, *, finalized: bool = False) -> None:
        from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase  # noqa: F401

        self.phase = phase
        self._finalized = finalized
        self._halted = False
        self.stop_requests = 0
        # The execution lease is a canonical read the shutdown gate makes.  Nothing in this
        # fake ever takes it, so ``NONE`` is the honest answer -- and a missing attribute
        # would fail open only if the gate defaulted it, which it does not.
        self.lease = ExecutionLease.NONE

    @property
    def result(self):
        """A ``PaperSessionResult``-shaped object the render path accepts."""

        finalized = self._finalized

        class _State:
            pass

        state = _State()
        state.finalized = finalized  # type: ignore[attr-defined]
        state.halted = self._halted  # type: ignore[attr-defined]

        class _Result:
            pass

        result = _Result()
        result.state = state  # type: ignore[attr-defined]
        result.engine_snapshot = None  # type: ignore[attr-defined]
        result.health = None  # type: ignore[attr-defined]
        result.events = ()  # type: ignore[attr-defined]
        return result

    def halt(self) -> object:
        """Model ``poll()`` discovering unsafe health: any phase -> HALTED."""

        from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase

        self.phase = PaperWorkflowPhase.HALTED
        self._halted = True
        return self.result

    def fail_finalization_refresh(self) -> bool:
        from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase

        if self.phase is not PaperWorkflowPhase.STOPPING:
            return False
        self.phase = PaperWorkflowPhase.HALTED
        return True

    def request_stop(self, _snapshot: object) -> object:
        """Model the real automatic route: RUNNING/PAUSED -> STOPPING.

        An operator-only phase has no automatic stop, so asking for one there
        is a test bug and must be loud rather than silently accepted.
        """

        from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase

        if self.phase not in {
            PaperWorkflowPhase.RUNNING,
            PaperWorkflowPhase.PAUSED,
        }:
            raise AssertionError(f"no automatic stop from {self.phase}")
        self.stop_requests += 1
        self.phase = PaperWorkflowPhase.STOPPING
        return self.result


def _install_order_service_spy(window: MainWindow, spy: object) -> None:
    """Give the window's Paper service one *owned* order service.

    Ownership lives in ``PaperTradingService`` now, so a test can no longer
    assign ``window.paper_order_service``.  It rebuilds the service with an
    injected factory and promotes one candidate through the real two-phase
    promotion -- the production path -- so the window really holds an order service
    when the close path runs.
    """

    from us_quant.trading.application.paper import PaperTradingService

    window.paper_trading = PaperTradingService(
        workflow_getter=lambda: window.paper_workflow,
        order_service_factory=lambda config, *, repository, extended_hours_enabled: spy,
    )
    window.paper_trading.connect_candidate(
        "teardown-spy",
        config=object(),
        repository=object(),
        extended_hours_enabled=False,
    )
    window.paper_trading.commit_candidate_promotion(
        window.paper_trading.reserve_candidate_promotion("teardown-spy")
    )


def _install_paper_workflow(monkeypatch, window: MainWindow, workflow: object):
    """Swap the controller and restore it, waiting out any worker it started."""

    real = window.paper_workflow
    window.paper_workflow = workflow  # type: ignore[assignment]
    return real


def _restore_paper_workflow(window: MainWindow, real: object) -> None:
    window.paper_workflow = real  # type: ignore[assignment]
    for worker in window._running_workers():
        worker.wait(5_000)
    _APP.processEvents()


def test_close_on_a_halted_session_refuses_without_tearing_paper_down(
    monkeypatch,
) -> None:
    """Requirement 1: HALTED + not finalized -> refused, nothing disconnected."""

    from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase

    _silence_dialogs(monkeypatch)
    window = _window()
    real = _install_paper_workflow(
        monkeypatch, window, _HaltedPaperWorkflow(PaperWorkflowPhase.HALTED)
    )
    try:
        disconnected: list[str] = []

        class _ServiceSpy:
            def connect(self) -> object:
                return self

            def connection_snapshot(self):
                class _Connection:
                    connected = False

                return _Connection()

            def disconnect(self) -> None:  # pragma: no cover - must not run
                disconnected.append("disconnect")

        _install_order_service_spy(window, _ServiceSpy())
        shutdowns: list[str] = []
        real_shutdown = window.runtime_supervisor.shutdown

        def spy_shutdown():
            shutdowns.append("shutdown")
            return real_shutdown()

        window.runtime_supervisor.shutdown = spy_shutdown  # type: ignore[method-assign]

        assert _close_verdict(window) is False

        # No Paper teardown, no full runtime release, service still held.
        assert disconnected == []
        assert shutdowns == []
        assert window.paper_trading.has_order_service() is True
    finally:
        _restore_paper_workflow(window, real)
        window.deleteLater()


def test_halted_close_does_not_lock_out_manual_reconciliation(
    monkeypatch,
) -> None:
    """Requirement 2: the recovery task must be admitted after a refused close."""

    from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase

    _silence_dialogs(monkeypatch)
    window = _window()
    real = _install_paper_workflow(
        monkeypatch, window, _HaltedPaperWorkflow(PaperWorkflowPhase.HALTED)
    )
    try:
        assert _close_verdict(window) is False
        # The refused close handed the client back.
        assert window._closing is False
        assert window.runtime_supervisor.shutting_down is False

        # And the real recovery entry point is admitted again.
        admitted = window._start_task(
            lambda report: None,
            on_success=lambda result: None,
            start_message="manual reconciliation",
            resource_group="broker",
            suppress_busy_message=True,
        )
        assert admitted is True
    finally:
        _restore_paper_workflow(window, real)
        window.deleteLater()


def test_reconciling_phases_also_release_the_close_drain(monkeypatch) -> None:
    """RECONCILING / RECONCILING_READY are operator-only too."""

    from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase

    for phase in (
        PaperWorkflowPhase.RECONCILING,
        PaperWorkflowPhase.RECONCILING_READY,
    ):
        _silence_dialogs(monkeypatch)
        window = _window()
        real = _install_paper_workflow(monkeypatch, window, _HaltedPaperWorkflow(phase))
        try:
            assert _close_verdict(window) is False
            assert window._closing is False, phase
            assert window.runtime_supervisor.shutting_down is False, phase
        finally:
            _restore_paper_workflow(window, real)
            window.deleteLater()


def test_finalization_failure_during_close_reopens_manual_recovery(
    monkeypatch,
) -> None:
    """Requirement 3: RUNNING -> close -> STOPPING -> failure -> HALTED.

    The automatic failure route is the one a phase check at close time cannot
    see: the gate is raised while the phase is still ``RUNNING``, and only
    later does the finalization failure land the session in ``HALTED``.
    """

    from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase

    _silence_dialogs(monkeypatch)
    window = _window()
    running = _HaltedPaperWorkflow(PaperWorkflowPhase.RUNNING)
    real = _install_paper_workflow(monkeypatch, window, running)
    try:
        # Hold the zero-state proof in flight so the close path stops at
        # STOPPING with the gate still down -- the real state while the proof
        # runs.  The flag is the capability's since v2O-E3; the test sets it where
        # it now lives.
        window.paper_orchestrator._finalization_inflight = True

        assert _close_verdict(window) is False
        # RUNNING has an automatic route, so the gate stays down for it.
        assert window._closing is True
        assert running.phase is PaperWorkflowPhase.STOPPING

        # The proof then fails: STOPPING -> HALTED, the automatic route.
        window.paper_orchestrator._finalization_failed("zero-state proof failed")

        assert window.paper_workflow.phase is PaperWorkflowPhase.HALTED
        assert window._closing is False
        assert window.runtime_supervisor.shutting_down is False

        admitted = window._start_task(
            lambda report: None,
            on_success=lambda result: None,
            start_message="manual reconciliation",
            resource_group="broker",
            suppress_busy_message=True,
        )
        assert admitted is True
    finally:
        _restore_paper_workflow(window, real)
        window.deleteLater()


def test_ordinary_tasks_stay_refused_while_an_automatic_stop_drains(
    monkeypatch,
) -> None:
    """Requirement 4: the automatic path must keep the gate down."""

    from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase

    _silence_dialogs(monkeypatch)
    window = _window()
    running = _HaltedPaperWorkflow(PaperWorkflowPhase.RUNNING)
    real = _install_paper_workflow(monkeypatch, window, running)
    try:
        with _BlockingWorker(window):
            assert _close_verdict(window) is False

        assert window._closing is True
        assert window.runtime_supervisor.shutting_down is True
        assert (
            window._start_task(
                lambda report: None,
                on_success=lambda result: None,
                start_message="ordinary task",
                resource_group="research",
                suppress_busy_message=True,
            )
            is False
        )
    finally:
        _restore_paper_workflow(window, real)
        window.deleteLater()


def test_close_after_finalization_still_completes_the_teardown(
    monkeypatch,
) -> None:
    """Requirement 5: a genuinely finalized session closes normally."""

    from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase

    _silence_dialogs(monkeypatch)
    window = _window()
    real = _install_paper_workflow(
        monkeypatch,
        window,
        _HaltedPaperWorkflow(PaperWorkflowPhase.FINALIZED, finalized=True),
    )
    try:
        assert _close_verdict(window) is True

        assert not window.paper_order_timer.isActive()
        assert not window.extended_session_timer.isActive()
        assert not window.market_orchestrator.polling_active
        assert window.runtime_supervisor.errors() == ()
    finally:
        _restore_paper_workflow(window, real)
        window.deleteLater()


def test_a_halt_reported_by_a_running_proof_reopens_manual_recovery(
    monkeypatch,
) -> None:
    """Requirement 3 (second route): a result that lands ``HALTED`` mid-drain.

    The automatic failure route is ``_finalization_failed``; this is the other one, and
    it is a different call site: the proof was *in flight* and came back reporting a
    halted session instead of failing.  Until v2O-E3 the analogue was the render path,
    because the window's result handler was where a result that had moved the phase was
    noticed; the decision is the capability's now, so the result goes in where a result
    actually arrives -- the proof's completion callback -- and the drain must still be
    released.  A HALTED session with the admission gate still down cannot be reconciled,
    so this is the difference between a recoverable client and a stuck one.
    """

    from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase

    _silence_dialogs(monkeypatch)
    window = _window()
    running = _HaltedPaperWorkflow(PaperWorkflowPhase.RUNNING)
    real = _install_paper_workflow(monkeypatch, window, running)
    try:
        window.paper_orchestrator._finalization_inflight = True
        assert _close_verdict(window) is False
        assert window._closing is True

        # The in-flight proof reports unsafe health: STOPPING -> HALTED, and the
        # result reaches the capability's own completion path.
        window.paper_orchestrator._finalization_completed(running.halt())

        assert window.paper_workflow.phase is PaperWorkflowPhase.HALTED
        assert window._closing is False
        assert window.runtime_supervisor.shutting_down is False
        # And the flag was cleared even though the result halted the session.
        assert window.paper_orchestrator._finalization_inflight is False

        admitted = window._start_task(
            lambda report: None,
            on_success=lambda result: None,
            start_message="manual reconciliation",
            resource_group="broker",
            suppress_busy_message=True,
        )
        assert admitted is True
    finally:
        _restore_paper_workflow(window, real)
        window.deleteLater()


def test_the_recovery_publication_is_what_releases_the_close_drain() -> None:
    """Requirement 2 (the mechanism): the *signal* undoes the refused close.

    The rounds above reach the release through a route that happens to end in this
    publication; this one invokes the publication directly, so the claim under test is
    the wiring itself rather than any single route's arithmetic.  It is the whole of what
    v2O-E3 has to keep: the window no longer decides *whether* Paper needs a human -- the
    capability says so, and the window only undoes its own teardown.
    """

    window = _window()
    try:
        window.runtime_supervisor.begin_shutdown()
        assert window._closing is True
        assert window.runtime_supervisor.shutting_down is True

        window.paper_orchestrator.manual_recovery_required.emit()

        assert window._closing is False
        assert window.runtime_supervisor.shutting_down is False

        # And it is idempotent, because the capability re-announces the condition rather
        # than diffing phases -- a listener that assumed a transition would double-count.
        window.paper_orchestrator.manual_recovery_required.emit()
        assert window._closing is False
    finally:
        window.deleteLater()
