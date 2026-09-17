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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.runtime_supervisor import STATE_STOPPED, RuntimeSupervisor


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
    assert not window.stream_timer.isActive()

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

    def __init__(self, window: MainWindow) -> None:
        from threading import Event as ThreadingEvent

        from us_quant.desktop import TaskThread

        self.release = ThreadingEvent()
        self.started = ThreadingEvent()

        def task(progress: object) -> str:
            self.started.set()
            self.release.wait(30)
            return "done"

        self.thread = TaskThread(task, resource_group="universe")
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
    """Requirement 2: a cancellable network task is asked to stop."""

    from threading import Event as ThreadingEvent

    _silence_dialogs(monkeypatch)
    window = _window()
    try:
        with _BlockingWorker(window):
            cancel_event = ThreadingEvent()
            window.universe_refresh_cancel_event = cancel_event
            window.universe_refresh_worker = window.workers[-1]

            assert not cancel_event.is_set()
            _close_verdict(window)

            assert cancel_event.is_set()
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
                def disconnect(self) -> None:  # pragma: no cover - must not run
                    disconnected.append("disconnect")

            window.paper_order_service = _ServiceSpy()  # type: ignore[assignment]
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
            assert window.paper_order_service is not None
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
        assert not window.stream_timer.isActive()
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

    from us_quant.paper_workflow import PaperWorkflowPhase
    from us_quant.workflow_state import WorkflowStateError

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
