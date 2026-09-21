"""Unit tests for the Runtime Events presenter."""

from __future__ import annotations

from us_quant.desktop_v2.pages.system.runtime_events import presenter
from us_quant.desktop_v2.pages.system.runtime_events.models import (
    RuntimeEventTone,
)
from us_quant.runtime_events import RuntimeEvent


def _event(
    event_id: int,
    severity: str = "info",
    resolved: bool = False,
    component: str = "market_data",
    code: str = "STREAM_START",
    message: str = "started",
    occurred_at: str = "2026-01-01T00:00:00+00:00",
) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=event_id,
        occurred_at=occurred_at,
        severity=severity,
        component=component,
        code=code,
        message=message,
        resolved=resolved,
    )


def _view(events, **overrides):
    kwargs = dict(
        events=tuple(events),
        active_task_count=0,
        last_export=None,
        info_text="info",
    )
    kwargs.update(overrides)
    return presenter.build_runtime_events_view(**kwargs)


def test_empty_events_render_zeroed_cards_and_no_rows() -> None:
    view = _view([])
    assert view.error_count == "0"
    assert view.warning_count == "0"
    assert view.rows == ()
    assert view.info_text == "info"


def test_unresolved_errors_are_counted() -> None:
    view = _view([_event(1, severity="error")])
    assert view.error_count == "1"


def test_unresolved_warnings_are_counted() -> None:
    view = _view([_event(1, severity="warning")])
    assert view.warning_count == "1"


def test_resolved_events_do_not_reach_the_cards() -> None:
    view = _view(
        [
            _event(1, severity="error", resolved=True),
            _event(2, severity="warning", resolved=True),
        ]
    )
    assert view.error_count == "0"
    assert view.warning_count == "0"
    # ...but they are still rows the operator can see.
    assert len(view.rows) == 2


def test_severity_and_status_are_translated_in_the_frozen_way() -> None:
    view = _view(
        [
            _event(1, severity="info", resolved=False),
            _event(2, severity="warning", resolved=True),
            _event(3, severity="error", resolved=False),
        ]
    )
    assert [row.severity_text for row in view.rows] == ["信息", "警告", "错误"]
    assert [row.status_text for row in view.rows] == [
        "待确认",
        "已确认",
        "待确认",
    ]


def test_row_tone_follows_severity() -> None:
    view = _view(
        [
            _event(1, severity="error"),
            _event(2, severity="warning"),
            _event(3, severity="info"),
        ]
    )
    assert [row.tone for row in view.rows] == [
        RuntimeEventTone.ERROR,
        RuntimeEventTone.WARNING,
        RuntimeEventTone.NEUTRAL,
    ]


def test_active_task_count_is_carried_through() -> None:
    view = _view([], active_task_count=3)
    assert view.active_task_count == "3"


def test_initial_export_state_is_the_frozen_empty_value() -> None:
    view = _view([])
    assert view.last_export_value == "无"
    assert view.last_export_note == "脱敏 CSV / JSON"


def test_successful_export_state_carries_the_name_and_path() -> None:
    view = _view(
        [],
        last_export=("terminal-1.zip", "/tmp/exports/terminal-1.zip"),
    )
    assert view.last_export_value == "terminal-1.zip"
    assert view.last_export_note == "/tmp/exports/terminal-1.zip"


def test_row_identity_preserves_the_full_event_id() -> None:
    view = _view([_event(10), _event(2)])
    assert [row.event_id for row in view.rows] == [10, 2]
    assert [row.event_id_text for row in view.rows] == ["10", "2"]


def test_severity_helpers_are_stable() -> None:
    assert presenter.severity_text("error") == "错误"
    assert presenter.severity_text("warning") == "警告"
    assert presenter.severity_text("info") == "信息"
    assert presenter.severity_text("weird") == "weird"
    assert presenter.status_text(True) == "已确认"
    assert presenter.status_text(False) == "待确认"
