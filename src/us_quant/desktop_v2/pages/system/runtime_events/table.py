"""The Runtime Events table: rows, tones and stable selection identity.

The table owns its widgets and nothing else.  It cannot read the event store,
resolve an event or export anything -- the page reads :meth:`selected_event_id`
and emits an intent, and the window decides what to do with it.

Two details are load-bearing:

* the ID column keeps the *full integer* event id as its ordering key, so the
  grid sorts ``2`` before ``10`` instead of lexically;
* a row's tone is stored next to its cells, so a palette change can recolour the
  rows already drawn without a fresh view.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from us_quant.desktop_widgets import configure_table
from us_quant.ui_theme import ThemePalette

from .models import (
    RuntimeEventRowView,
    RuntimeEventTone,
)

RUNTIME_EVENT_HEADERS = (
    "ID",
    "时间",
    "级别",
    "组件",
    "代码",
    "消息",
    "状态",
)

_ID_ROLE = Qt.ItemDataRole.UserRole
_TONE_ROLE = Qt.ItemDataRole.UserRole + 1


class _EventIdItem(QTableWidgetItem):
    """ID cell whose ordering key is the full integer event id."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(_ID_ROLE)
        right = other.data(_ID_ROLE)
        if isinstance(left, int) and isinstance(right, int):
            return left < right
        return super().__lt__(other)


class RuntimeEventTable(QTableWidget):
    """Read-only table of recent runtime events."""

    def __init__(
        self,
        palette: ThemePalette,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(0, len(RUNTIME_EVENT_HEADERS), parent)
        self._palette = palette
        self.setHorizontalHeaderLabels(list(RUNTIME_EVENT_HEADERS))
        configure_table(self)

    # -- rendering ------------------------------------------------------

    def render(self, rows: tuple[RuntimeEventRowView, ...]) -> None:
        """Replace every row, keeping the column order and the row tones."""

        sorting = self.isSortingEnabled()
        self.setSortingEnabled(False)
        self.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (
                row.event_id_text,
                row.occurred_at,
                row.severity_text,
                row.component,
                row.code,
                row.message,
                row.status_text,
            )
            for column, value in enumerate(values):
                if column == 0:
                    item: QTableWidgetItem = _EventIdItem(value)
                    item.setData(_ID_ROLE, row.event_id)
                else:
                    item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setData(_TONE_ROLE, row.tone.value)
                self.setItem(index, column, item)
        self.setSortingEnabled(sorting)
        self._apply_tones()

    def set_palette(self, palette: ThemePalette) -> None:
        """Adopt a new palette and recolour the rows already drawn."""

        selected = self.selected_event_id()
        self._palette = palette
        self._apply_tones()
        if selected is not None:
            self._select_event(selected)

    def _apply_tones(self) -> None:
        for row in range(self.rowCount()):
            severity = self._row_tone(row)
            color = self._color_for(severity)
            for column in range(self.columnCount()):
                item = self.item(row, column)
                if item is None:
                    continue
                if color is None:
                    item.setData(Qt.ItemDataRole.ForegroundRole, None)
                else:
                    item.setForeground(QColor(color))

    def _row_tone(self, row: int) -> RuntimeEventTone:
        anchor = self.item(row, 0)
        if anchor is None:
            return RuntimeEventTone.NEUTRAL
        stored = anchor.data(_TONE_ROLE)
        try:
            return RuntimeEventTone(stored)
        except (TypeError, ValueError):
            return RuntimeEventTone.NEUTRAL

    def _color_for(self, tone: RuntimeEventTone) -> str | None:
        if tone is RuntimeEventTone.ERROR:
            return self._palette.error
        if tone is RuntimeEventTone.WARNING:
            return self._palette.warning
        return None

    # -- selection identity ---------------------------------------------

    def selected_event_id(self) -> int | None:
        """Return the full event id of the selected row, or ``None``."""

        for item in self.selectedItems():
            anchor = self.item(item.row(), 0)
            if anchor is None:
                continue
            value = anchor.data(_ID_ROLE)
            if isinstance(value, int):
                return value
        return None

    def _select_event(self, event_id: int) -> None:
        for row in range(self.rowCount()):
            anchor = self.item(row, 0)
            if anchor is not None and anchor.data(_ID_ROLE) == event_id:
                self.selectRow(row)
                return


__all__ = ["RUNTIME_EVENT_HEADERS", "RuntimeEventTable"]

