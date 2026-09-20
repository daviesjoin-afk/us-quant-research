"""Small item-backed tables for the targeted validation workspace."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QWidget

from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedRowTone,
    TargetedTableRow,
)
from us_quant.desktop_widgets import configure_table
from us_quant.ui_theme import ThemePalette, theme_palette


class TargetedTable(QTableWidget):
    """A configured read-only table with stable run-id selection."""

    run_selected = Signal(str)

    def __init__(
        self,
        headers: tuple[str, ...],
        parent: QWidget | None = None,
        *,
        column_widths: tuple[int, ...] | None = None,
        sort_column: int | None = None,
        sorting_enabled: bool = True,
        tone_columns: tuple[int, ...] = (),
        palette: ThemePalette | None = None,
    ) -> None:
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(list(headers))
        self._palette = palette or theme_palette("dark")
        self._sort_column = sort_column
        self._sorting_enabled = sorting_enabled
        self._tone_columns = tone_columns
        self._rows: tuple[TargetedTableRow, ...] = ()
        configure_table(self)
        if not sorting_enabled:
            self.setSortingEnabled(False)
        if column_widths:
            for column, width in enumerate(column_widths):
                self.setColumnWidth(column, width)
        self.itemSelectionChanged.connect(self._emit_selection)

    def set_palette(self, palette: ThemePalette) -> None:
        self._palette = palette
        if self._rows:
            self.render_rows(self._rows)

    def render_rows(
        self,
        rows: tuple[TargetedTableRow, ...],
        *,
        selected_key: str | None = None,
    ) -> None:
        wanted = selected_key or self.selected_key()
        self.blockSignals(True)
        try:
            self._rows = rows
            self.setSortingEnabled(False)
            self.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                for column, value in enumerate(row.values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(row.tooltip or value)
                    if column == 0 and row.key is not None:
                        item.setData(Qt.UserRole, row.key)
                    colour = self._tone_colour(row.tone)
                    if colour is not None and column in self._tone_columns:
                        item.setForeground(colour)
                    self.setItem(row_index, column, item)
            if self._sorting_enabled:
                self.setSortingEnabled(True)
                if self._sort_column is not None:
                    self.sortItems(self._sort_column, Qt.AscendingOrder)
            else:
                self.setSortingEnabled(False)
            self._restore_selection(wanted)
        finally:
            self.blockSignals(False)

    def selected_key(self) -> str | None:
        selected = self.selectedItems()
        if not selected:
            return None
        item = self.item(selected[0].row(), 0)
        return str(item.data(Qt.UserRole)) if item is not None else None

    def _emit_selection(self) -> None:
        key = self.selected_key()
        if key:
            self.run_selected.emit(key)

    def _restore_selection(self, wanted: str | None) -> None:
        if not self._rows:
            return
        available = {row.key for row in self._rows if row.key is not None}
        if wanted not in available:
            wanted = self._rows[0].key
        if wanted is None:
            return
        for row_index in range(self.rowCount()):
            item = self.item(row_index, 0)
            if item is not None and item.data(Qt.UserRole) == wanted:
                self.selectRow(row_index)
                return

    def _tone_colour(self, tone: TargetedRowTone) -> QColor | None:
        if tone is TargetedRowTone.SUCCESS:
            return QColor(self._palette.success)
        if tone is TargetedRowTone.WARNING:
            return QColor(self._palette.warning)
        if tone is TargetedRowTone.ERROR:
            return QColor(self._palette.error)
        return None


__all__ = ["TargetedTable"]
