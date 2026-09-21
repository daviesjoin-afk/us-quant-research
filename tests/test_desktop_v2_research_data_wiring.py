"""Wiring tests for the Universe and History native pages."""

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



def test_universe_refresh_and_cancel_intents_reach_handlers(
    window: MainWindow, monkeypatch
) -> None:
    page = window.universe_page
    seen: list[str] = []
    for signal, handler in (
        (page.refresh_requested, "_refresh_universe"),
        (page.cancel_refresh_requested, "_cancel_universe_refresh"),
    ):
        monkeypatch.setattr(window, handler, lambda handler=handler: seen.append(handler))
        signal.disconnect()
    window._connect_universe_page()
    page.refresh_button.click()
    page.cancel_button.setEnabled(True)
    page.cancel_button.click()
    assert seen == ["_refresh_universe", "_cancel_universe_refresh"]


def test_universe_refresh_lifecycle_publishes_control_state(
    window: MainWindow, monkeypatch
) -> None:
    monkeypatch.setattr(window, "_start_task", lambda *args, **kwargs: True)
    monkeypatch.setattr(window, "workers", [_IdleWorker()])
    window._refresh_universe()
    assert window.universe_page.refresh_button.isEnabled() is False
    assert window.universe_page.cancel_button.isEnabled() is True
    assert window.universe_page.refresh_button.text() == "官方标的刷新中…"
    window._cancel_universe_refresh()
    assert window.universe_page.cancel_button.isEnabled() is False
    assert window.universe_page.cancel_button.text() == "正在取消…"
    window._reset_universe_refresh_controls()
    assert window.universe_page.refresh_button.isEnabled() is True
    assert window.universe_page.cancel_button.isEnabled() is False


def test_universe_refresh_success_renders_new_snapshot(window: MainWindow) -> None:
    window._universe_refreshed(_universe())
    assert window.universe_page.table.rowCount() == 1
    assert window.universe_page.table.item(0, 0).text() == "AAPL"


def test_history_intents_reach_handlers_with_batch_size(
    window: MainWindow, monkeypatch
) -> None:
    page = window.history_page
    seen: list[tuple[str, int | None]] = []
    for signal, handler in (
        (page.schedule_requested, "_schedule_history"),
        (page.run_ibkr_requested, "_run_history"),
        (page.run_public_requested, "_run_public_history"),
        (page.retry_failed_requested, "_retry_failed"),
    ):
        monkeypatch.setattr(
            window,
            handler,
            lambda value=None, handler=handler: seen.append(
                (handler, value if isinstance(value, int) else None)
            ),
        )
        signal.disconnect()
    window._connect_history_page()
    page.batch_size.setValue(37)
    page.schedule_button.click()
    page.run_ibkr_button.click()
    page.run_public_button.click()
    page.retry_button.click()
    assert seen == [
        ("_schedule_history", None),
        ("_run_history", 37),
        ("_run_public_history", 37),
        ("_retry_failed", None),
    ]


def test_history_finished_publishes_progress_and_rows(
    window: MainWindow, monkeypatch
) -> None:
    monkeypatch.setattr(window.history_service, "snapshot", _history_snapshot)
    window._history_finished({"completed": 1, "failed": 1})
    assert window.history_page.progress.value() == 50
    assert window.history_page.table.rowCount() == 1
    assert window.history_page.table.item(0, 0).text() == "AAPL"


def test_history_failure_resets_only_history_progress(
    window: MainWindow, monkeypatch
) -> None:
    monkeypatch.setattr(window.history_service, "snapshot", _history_snapshot)
    window._history_progress_percent = 77
    window._publish_history_view()
    window._history_task_failed("failed")
    assert window.history_page.progress.value() == 0


def test_unrelated_task_failure_does_not_touch_history_page(
    window: MainWindow, monkeypatch
) -> None:
    monkeypatch.setattr(window.history_service, "snapshot", _history_snapshot)
    window._history_progress_percent = 77
    window._publish_history_view()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    window._task_failed("unrelated")
    assert window.history_page.progress.value() == 77


def test_dashboard_no_longer_owns_universe_actions(window: MainWindow) -> None:
    assert not hasattr(window, "universe_refresh_button")
    assert not hasattr(window, "universe_cancel_button")
