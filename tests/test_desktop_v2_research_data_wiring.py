"""Wiring tests for the Universe and History native pages.

These originally drove the window's own handlers (``_refresh_universe``,
``_history_finished`` and friends).  v2O-C1 moved those handlers into
``desktop_v2/orchestration/research``, so the tests now drive the capability and
read the same real widgets the window shows.  The intent is unchanged -- prove
the page is painted from the right state -- and asserting on the real
``QPushButton``/``QTableWidget`` rather than on the orchestrator's return value
is what keeps that true.

The button-click wiring lives in
``tests/test_desktop_research_foundations_wiring.py``; what stays here is the
page state each intent produces.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from us_quant.desktop import MainWindow
from us_quant.desktop_history_service import HistoryQueueSnapshot
from us_quant.history_queue import HistoryJob
from us_quant.paths import STATE_ROOT_ENV
from us_quant.universe import UniverseRecord, UniverseSnapshot


_APP = QApplication.instance() or QApplication([])


class _IdleWorker:
    def isRunning(self) -> bool:
        return False


@pytest.fixture()
def window(monkeypatch, tmp_path):
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    widget = MainWindow()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


def _universe() -> UniverseSnapshot:
    return UniverseSnapshot(
        generated_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        source_timestamps={"test": "now"},
        records=(
            UniverseRecord(
                symbol="AAPL",
                name="Apple",
                exchange="NASDAQ",
                security_type="STK",
                eligible_for_research=True,
            ),
        ),
    )


def _history_snapshot() -> HistoryQueueSnapshot:
    job = HistoryJob(
        symbol="AAPL",
        duration="1 Y",
        priority=1,
        status="completed",
        attempts=1,
        row_count=200,
        last_error="",
        updated_at="2026-09-20T00:00:00+00:00",
    )
    return HistoryQueueSnapshot((job,), 0, 0, 1, 0)


def _stub_refresh(window: MainWindow, monkeypatch) -> None:
    """Admit a refresh without running it, so the controls can be observed.

    Only the *domain* call is stubbed.  The page render must run for real: this
    test is about the painted control state, and stubbing the render would leave
    the buttons in whatever state the fixture built them in.
    """

    monkeypatch.setattr(
        window.universe_orchestrator._service,
        "refresh",
        lambda *, should_stop, progress=None: _universe(),
    )


def test_universe_refresh_lifecycle_publishes_control_state(
    window: MainWindow, monkeypatch
) -> None:
    """The three control states: refreshing, cancelling, idle."""

    _stub_refresh(window, monkeypatch)

    window.universe_orchestrator.request_refresh()
    assert window.universe_page.refresh_button.isEnabled() is False
    assert window.universe_page.cancel_button.isEnabled() is True
    assert window.universe_page.refresh_button.text() == "官方标的刷新中…"

    window.universe_orchestrator.request_cancel()
    assert window.universe_page.cancel_button.isEnabled() is False
    assert window.universe_page.cancel_button.text() == "正在取消…"

    # The task's completion path is what returns the controls to idle; it is
    # reached through ``on_finished`` rather than by reading a worker.
    window.universe_orchestrator._refresh_finished()
    assert window.universe_page.refresh_button.isEnabled() is True
    assert window.universe_page.cancel_button.isEnabled() is False


def test_adopting_a_snapshot_renders_the_new_rows(window: MainWindow) -> None:
    """Startup adoption paints: the page shows what the loader read."""

    window.universe_orchestrator.restore_snapshot(_universe())

    assert window.universe_page.table.rowCount() == 1
    assert window.universe_page.table.item(0, 0).text() == "AAPL"


def test_history_intents_reach_the_orchestrator_with_the_batch_size(
    window: MainWindow, monkeypatch
) -> None:
    """Each history intent arrives with the page's batch size, not a default."""

    page = window.history_page
    seen: list[tuple[str, int | None]] = []
    for handler in (
        "request_schedule",
        "request_run_ibkr",
        "request_run_public",
        "retry_failed",
    ):
        monkeypatch.setattr(
            window.history_orchestrator,
            handler,
            lambda value=None, handler=handler: seen.append(
                (handler, value if isinstance(value, int) else None)
            ),
        )
    page.batch_size.setValue(37)
    page.schedule_button.click()
    page.run_ibkr_button.click()
    page.run_public_button.click()
    page.retry_button.click()

    assert seen == [
        ("request_schedule", None),
        ("request_run_ibkr", 37),
        ("request_run_public", 37),
        ("retry_failed", None),
    ]


def test_history_completion_publishes_progress_and_rows(
    window: MainWindow, monkeypatch
) -> None:
    monkeypatch.setattr(window.history_service, "snapshot", _history_snapshot)

    window.history_orchestrator._finished({"completed": 1, "failed": 1})

    assert window.history_page.progress.value() == 50
    assert window.history_page.table.rowCount() == 1
    assert window.history_page.table.item(0, 0).text() == "AAPL"


def test_history_failure_resets_only_history_progress(
    window: MainWindow, monkeypatch
) -> None:
    monkeypatch.setattr(window.history_service, "snapshot", _history_snapshot)
    window.history_orchestrator._progress_percent = 77
    window.history_orchestrator.render_current()

    window.history_orchestrator._failed("failed")

    assert window.history_page.progress.value() == 0


def test_unrelated_task_failure_does_not_touch_history_page(
    window: MainWindow, monkeypatch
) -> None:
    """A failure elsewhere must not clear the history bar.

    The progress bar belongs to the history capability now, so a generic task
    failure has no route to it at all -- which is exactly what this asserts.
    """

    monkeypatch.setattr(window.history_service, "snapshot", _history_snapshot)
    window.history_orchestrator._progress_percent = 77
    window.history_orchestrator.render_current()

    window._task_failed("unrelated")

    assert window.history_page.progress.value() == 77


def test_dashboard_no_longer_owns_universe_actions(window: MainWindow) -> None:
    assert not hasattr(window, "universe_refresh_button")
    assert not hasattr(window, "universe_cancel_button")
