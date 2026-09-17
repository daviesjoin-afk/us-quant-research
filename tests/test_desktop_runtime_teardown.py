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
