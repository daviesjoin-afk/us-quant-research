"""The real coalescing seam, exercised as the shipped object.

The orchestrator's behaviour suite injects a hand-fired ``ManualTimer``, which is
what makes the 1-second window deterministic -- and which is also why none of
those tests would notice a broken ``QtFlushTimer``.  This file closes that gap:
it drives the real single-shot Qt timer, and then drives a real ``MainWindow``
burst through the shipped timer until the coalesced repaint arrives.

Two properties, and the second is the one the operator would feel:

* a failed arm (a callback stored but a timer never started) makes the coalesced
  repaint never arrive -- the page would keep showing a stale task count and the
  rows from before the burst until some later arrival or an explicit refresh;
* ``disarm`` must both stop the timer and forget the callback, and the timer must
  stay single-shot, or a superseded burst would fire again.

The window is the only place with a real event loop, so the waiting is real; the
deadlines are generous on purpose and a miss is a failure, not a skip.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.desktop_v2.orchestration.system.runtime_events.orchestrator import (
    QtFlushTimer,
)
from us_quant.paths import STATE_ROOT_ENV


_APP = QApplication.instance() or QApplication([])

#: How long a real timer may take before the seam counts as broken.  The
#: orchestrator's window is 1 second, so this is a six-fold margin: a loaded
#: runner may be slow, but it is not allowed to be six seconds slow.
_TIMEOUT_MS = 6_000


def _wait_until(predicate, what: str) -> None:
    """Pump the real event loop until ``predicate`` holds, or fail loudly."""

    waited = 0
    while not predicate():
        assert waited < _TIMEOUT_MS, f"{what} never happened"
        QTest.qWait(20)
        waited += 20


def _window(monkeypatch, tmp_path) -> MainWindow:
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path / "state"))
    window = MainWindow()
    _APP.processEvents()
    return window


# -- the seam itself ----------------------------------------------------


def test_the_real_timer_delivers_once_and_forgets_a_disarmed_callback() -> None:
    owner = QObject()
    fired: list[int] = []
    timer = QtFlushTimer(owner)

    timer.arm(0.01, lambda: fired.append(1))
    _wait_until(lambda: fired == [1], "the armed callback")
    assert fired == [1]

    # Single-shot: an armed timer delivers exactly one callback.
    QTest.qWait(200)
    assert fired == [1]

    # A disarmed timer never delivers -- neither because it was stopped nor
    # because a timeout that was already queued was allowed through.
    timer.arm(0.01, lambda: fired.append(2))
    timer.disarm()
    QTest.qWait(200)
    assert fired == [1]


# -- the window's coalesced repaint -------------------------------------


def test_a_coalesced_burst_reaches_the_page_through_the_real_timer(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        orchestrator = window.runtime_events_orchestrator
        # Deterministic setup: the first arrival has no previous paint to wait
        # for, so it paints at once and starts the coalescing window; the second
        # is then coalesced onto the real, shipped QTimer.
        orchestrator._last_refresh_at = None
        orchestrator.record(
            severity="info", component="test", code="FIRST", message="first"
        )
        painted = window.runtime_events_page.table.rowCount()

        orchestrator.record(
            severity="info", component="test", code="SECOND", message="second"
        )
        assert window.runtime_events_page.table.rowCount() == painted

        _wait_until(
            lambda: window.runtime_events_page.table.rowCount() == painted + 1,
            "the coalesced repaint",
        )
        assert window.runtime_events_page.table.rowCount() == painted + 1
    finally:
        window.close()
        window.deleteLater()


def test_a_task_count_change_is_repainted_by_the_real_timer(
    monkeypatch, tmp_path
) -> None:
    """The same seam, for the other coalesced arrival the page draws."""

    window = _window(monkeypatch, tmp_path)
    try:
        orchestrator = window.runtime_events_orchestrator
        orchestrator._last_refresh_at = None
        orchestrator.notify_task_count_changed()
        assert window.runtime_events_page.task_card.value_label.text() == "0"

        window.workers.append("unit-test-worker")  # type: ignore[arg-type]
        orchestrator.notify_task_count_changed()
        assert window.runtime_events_page.task_card.value_label.text() == "0"

        _wait_until(
            lambda: (
                window.runtime_events_page.task_card.value_label.text() == "1"
            ),
            "the coalesced task-count repaint",
        )
        assert window.runtime_events_page.task_card.value_label.text() == "1"
    finally:
        window.workers.clear()
        window.close()
        window.deleteLater()
