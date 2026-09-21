"""Real-Qt tests for the Runtime Events table."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.system.runtime_events.models import (
    RuntimeEventRowView,
    RuntimeEventTone,
)
from us_quant.desktop_v2.pages.system.runtime_events.table import (
    RUNTIME_EVENT_HEADERS,
    RuntimeEventTable,
)
from us_quant.ui_theme import theme_palette


_APP = QApplication.instance() or QApplication([])
_PALETTE = theme_palette("dark")


def _row(
    event_id: int,
    severity: str = "info",
    tone: RuntimeEventTone = RuntimeEventTone.NEUTRAL,
) -> RuntimeEventRowView:
    return RuntimeEventRowView(
        event_id=event_id,
        event_id_text=str(event_id),
        occurred_at=f"2026-01-01T00:00:0{event_id % 10}+00:00",
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


def _table(*rows) -> RuntimeEventTable:
    table = RuntimeEventTable(_PALETTE)
    table.render(tuple(rows))
    return table


def test_headers_are_exact_and_ordered() -> None:
    table = RuntimeEventTable(_PALETTE)
    headers = tuple(
        table.horizontalHeaderItem(index).text()
        for index in range(table.columnCount())
    )
    assert headers == RUNTIME_EVENT_HEADERS
    assert headers == ("ID", "时间", "级别", "组件", "代码", "消息", "状态")


def test_the_id_column_sorts_numerically() -> None:
    table = _table(_row(10), _row(2))
    table.sortItems(0, Qt.AscendingOrder)
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == [
        "2",
        "10",
    ]


def test_error_rows_carry_the_error_tone() -> None:
    table = _table(_row(1, "error", RuntimeEventTone.ERROR))
    item = table.item(0, 0)
    assert item.foreground().color().name() == QColor(_PALETTE.error).name()


def test_warning_rows_carry_the_warning_tone() -> None:
    table = _table(_row(1, "warning", RuntimeEventTone.WARNING))
    item = table.item(0, 0)
    assert item.foreground().color().name() == QColor(_PALETTE.warning).name()


def test_selected_event_id_is_the_full_integer_identity() -> None:
    table = _table(_row(10), _row(2))
    table.selectRow(0)
    assert table.selected_event_id() == 10


def test_no_selection_reads_as_none() -> None:
    table = _table(_row(10))
    table.clearSelection()
    assert table.selected_event_id() is None


def test_sorting_does_not_break_the_selection_identity() -> None:
    table = _table(_row(10), _row(2))
    table.selectRow(0)
    assert table.selected_event_id() == 10

    table.sortItems(0, Qt.AscendingOrder)

    # The selected *event* is still 10 even though its row moved.
    assert table.selected_event_id() == 10


def test_set_palette_preserves_the_selected_event() -> None:
    table = _table(_row(1, "error", RuntimeEventTone.ERROR), _row(2))
    table._select_event(1)
    light = theme_palette("light")

    table.set_palette(light)

    assert table.selected_event_id() == 1
    selected_row = [
        item.row() for item in table.selectedItems()
    ][0]
    item = table.item(selected_row, 0)
    assert item.foreground().color().name() == QColor(light.error).name()
