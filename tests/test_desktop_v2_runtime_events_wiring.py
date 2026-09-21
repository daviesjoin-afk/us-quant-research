"""Wiring tests: the real window's System route, end to end.

The Runtime Events page's own tests prove it emits; these prove the *window* is
listening and doing the orchestration.  The store is replaced with recorders so
nothing here touches a socket; the export payload is asserted so the migration
cannot quietly drop a field.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.desktop_v2.pages.system.runtime_events.models import (
    RuntimeEventRowView,
    RuntimeEventsPageView,
    RuntimeEventTone,
)
from us_quant.paths import STATE_ROOT_ENV
from us_quant.runtime_events import RuntimeEvent


_APP = QApplication.instance() or QApplication([])


def _window(monkeypatch, tmp_path) -> MainWindow:
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path / "state"))
    window = MainWindow()
    _APP.processEvents()
    return window


def _event(event_id: int, severity: str = "info") -> RuntimeEvent:
    return RuntimeEvent(
        event_id=event_id,
        occurred_at="2026-01-01T00:00:00+00:00",
        severity=severity,
        component="market_data",
        code="STREAM_START",
        message=f"event {event_id}",
        resolved=False,
    )


def _row(event_id: int, severity: str = "info") -> RuntimeEventRowView:
    tone = {
        "error": RuntimeEventTone.ERROR,
        "warning": RuntimeEventTone.WARNING,
    }.get(severity, RuntimeEventTone.NEUTRAL)
    return RuntimeEventRowView(
        event_id=event_id,
        event_id_text=str(event_id),
        occurred_at="2026-01-01T00:00:00+00:00",
        severity=severity,
        severity_text={"info": "信息", "warning": "警告", "error": "错误"}[
            severity
        ],
        component="market_data",
        code="STREAM_START",
        message=f"event {event_id}",
        status_text="待确认",
        tone=tone,
    )


def test_refresh_queries_the_latest_five_hundred(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        limits: list[int] = []

        def list_recent(limit=500):
            limits.append(limit)
            return (_event(1),)

        monkeypatch.setattr(window.runtime_events, "list_recent", list_recent)
        window._refresh_runtime_events()
        assert limits == [500]
    finally:
        window.close()
        window.deleteLater()


def test_recording_an_event_refreshes_the_page(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        before = window.runtime_events_page.table.rowCount()
        window._record_runtime_event(
            severity="info",
            component="test",
            code="UNIT",
            message="unit test event",
        )
        assert window.runtime_events_page.table.rowCount() == before + 1
    finally:
        window.close()
        window.deleteLater()


def test_resolve_targets_the_exact_event_id(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        resolved: list[int] = []
        monkeypatch.setattr(window.runtime_events, "resolve", resolved.append)
        monkeypatch.setattr(window, "_refresh_runtime_events", lambda: None)

        window._resolve_runtime_event(2)

        assert resolved == [2]
    finally:
        window.close()
        window.deleteLater()


def test_resolve_without_a_selection_shows_the_existing_message(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        calls: list[tuple] = []
        monkeypatch.setattr(
            "us_quant.desktop.QMessageBox.information",
            lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        window._resolve_runtime_event(None)
        assert len(calls) == 1
        args, _kwargs = calls[0]
        assert "未选择事件" in args[1]
    finally:
        window.close()
        window.deleteLater()


def test_successful_export_records_the_fact_and_refreshes(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        target = tmp_path / "terminal-1.zip"
        monkeypatch.setattr(
            "us_quant.desktop.export_terminal_bundle",
            lambda *args, **kwargs: target,
        )
        monkeypatch.setattr(
            "us_quant.desktop.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        window._export_terminal_state()

        assert window._last_runtime_export == (
            target.name,
            str(target),
        )
        assert (
            window.runtime_events_page.export_card.value_label.text()
            == target.name
        )
    finally:
        window.close()
        window.deleteLater()


def test_export_business_payload_is_unchanged(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        seen: dict = {}

        def recorder(*args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs
            return tmp_path / "terminal-2.zip"

        monkeypatch.setattr(
            "us_quant.desktop.export_terminal_bundle", recorder
        )
        monkeypatch.setattr(
            "us_quant.desktop.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        window._export_terminal_state()

        kwargs = seen["kwargs"]
        expected = {
            "portfolio",
            "stream",
            "strategies",
            "events",
            "shadow_fills",
            "targeted_replays",
            "targeted_robustness",
            "targeted_walk_forward",
            "targeted_overfit",
            "targeted_data_quality",
            "targeted_execution_stress",
            "targeted_review",
            "paper_order_audit",
            "paper_execution_audit",
        }
        assert expected <= set(kwargs)
        assert seen["args"] == (window.paths.exports_root,)
    finally:
        window.close()
        window.deleteLater()


def test_a_real_click_resolves_by_event_id_not_row_index(
    monkeypatch, tmp_path
) -> None:
    """The non-vacuity check: sorting must not turn ``2`` into a row index."""

    window = _window(monkeypatch, tmp_path)
    try:
        window.runtime_events_page.render(
            RuntimeEventsPageView(
                error_count="0",
                warning_count="0",
                active_task_count="0",
                last_export_value="无",
                last_export_note="脱敏 CSV / JSON",
                rows=(_row(10), _row(2)),
                info_text="info",
            )
        )
        resolved: list[int] = []
        monkeypatch.setattr(window.runtime_events, "resolve", resolved.append)
        monkeypatch.setattr(window, "_refresh_runtime_events", lambda: None)

        # After a descending sort the id-2 row is second, not first.
        window.runtime_events_page.table._select_event(2)
        window.runtime_events_page._resolve_button.click()

        assert resolved == [2]
    finally:
        window.close()
        window.deleteLater()
