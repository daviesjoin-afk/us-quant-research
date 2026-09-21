"""Real-Qt tests for the Runtime Events page."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.system.runtime_events import (
    RuntimeEventsPage,
)
from us_quant.desktop_v2.pages.system.runtime_events.models import (
    RuntimeEventRowView,
    RuntimeEventsPageView,
    RuntimeEventTone,
)
from us_quant.ui_theme import theme_palette


_APP = QApplication.instance() or QApplication([])


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


def _view(rows=(), **overrides) -> RuntimeEventsPageView:
    kwargs = dict(
        error_count="0",
        warning_count="0",
        active_task_count="0",
        last_export_value="无",
        last_export_note="脱敏 CSV / JSON",
        rows=tuple(rows),
        info_text="info body",
    )
    kwargs.update(overrides)
    return RuntimeEventsPageView(**kwargs)


def _page() -> RuntimeEventsPage:
    return RuntimeEventsPage(palette=theme_palette("dark"))


def test_the_page_owns_four_metric_cards() -> None:
    page = _page()
    assert page.error_card is not None
    assert page.warning_card is not None
    assert page.task_card is not None
    assert page.export_card is not None


def test_refresh_button_emits_the_intent() -> None:
    page = _page()
    seen: list[int] = []
    page.refresh_requested.connect(lambda: seen.append(1))
    page._refresh_button.click()
    assert seen == [1]


def test_export_button_emits_the_intent() -> None:
    page = _page()
    seen: list[int] = []
    page.export_requested.connect(lambda: seen.append(1))
    page._export_button.click()
    assert seen == [1]


def test_resolve_with_a_selection_emits_the_full_event_id() -> None:
    page = _page()
    page.render(_view([_row(10), _row(2)]))
    seen: list[object] = []
    page.resolve_requested.connect(seen.append)

    page.table._select_event(10)
    page._resolve_button.click()

    assert seen == [10]


def test_resolve_without_a_selection_emits_none() -> None:
    page = _page()
    page.render(_view([_row(10)]))
    page.table.clearSelection()
    seen: list[object] = []
    page.resolve_requested.connect(seen.append)

    page._resolve_button.click()

    assert seen == [None]


def test_render_empty_state_and_rows() -> None:
    page = _page()
    page.render(_view([]))
    assert page.empty_label.isHidden() is False
    assert page.table.rowCount() == 0

    page.render(_view([_row(1), _row(2)]))
    assert page.empty_label.isHidden() is True
    assert page.table.rowCount() == 2


def test_render_paints_the_cards_and_info_panel() -> None:
    page = _page()
    page.render(
        _view(
            [_row(1, "error")],
            error_count="1",
            last_export_value="bundle.zip",
            last_export_note="/tmp/bundle.zip",
            info_text="hello",
        )
    )
    assert page.error_card.value_label.text() == "1"
    assert page.export_card.value_label.text() == "bundle.zip"
    assert page.export_card.note_label.text() == "/tmp/bundle.zip"
    assert page.info_text.toPlainText() == "hello"


def test_set_palette_preserves_the_selected_event() -> None:
    page = _page()
    page.render(_view([_row(10), _row(2)]))
    page.table._select_event(10)

    page.set_palette(theme_palette("light"))

    assert page.table.selected_event_id() == 10


def test_set_palette_emits_no_intent() -> None:
    page = _page()
    page.render(_view([_row(10)]))
    page.table._select_event(10)

    seen: list[str] = []
    page.refresh_requested.connect(lambda: seen.append("refresh"))
    page.resolve_requested.connect(lambda _v: seen.append("resolve"))
    page.export_requested.connect(lambda: seen.append("export"))

    page.set_palette(theme_palette("light"))

    assert seen == []
