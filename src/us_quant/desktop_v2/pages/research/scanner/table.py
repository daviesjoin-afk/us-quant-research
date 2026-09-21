"""Table widget for the native market scanner page."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QTableWidget,
    QTableWidgetItem as _QTableWidgetItem,
    QWidget,
)

from us_quant.desktop_v2.pages.research.scanner.models import ScannerRowView
from us_quant.desktop_widgets import _sortable_number, configure_table
from us_quant.ui_theme import ThemePalette, theme_palette


SCANNER_HEADERS = (
    "代码",
    "执行",
    "板块",
    "层级",
    "信号",
    "评分",
    "收盘",
    "整股容量",
    "20日",
    "63日",
    "年化波动",
    "RSI14",
    "原因",
)
SCORE_COLUMN = 5
NUMERIC_COLUMNS = frozenset({3, 5, 6, 7, 8, 9, 10, 11})
TREND_COLUMNS = frozenset({0, 4, 5})


class _NumericTableWidgetItem(_QTableWidgetItem):
    """Sort displayed prices, percentages and counts numerically."""

    def __lt__(self, other: _QTableWidgetItem) -> bool:
        left = _sortable_number(self.text())
        right = _sortable_number(other.text())
        if left is not None and right is not None:
            return left < right
        return self.text().casefold() < other.text().casefold()


class ScannerTable(QTableWidget):
    """Owns scanner row rendering, selection and column sorting."""

    symbol_selected = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        palette: ThemePalette | None = None,
    ) -> None:
        super().__init__(0, len(SCANNER_HEADERS), parent)
        self._palette = palette or theme_palette("dark")
        self._emit_selection = True
        self.setHorizontalHeaderLabels(SCANNER_HEADERS)
        configure_table(self)
        self.itemSelectionChanged.connect(self._selection_changed)

    def render(
        self,
        rows: tuple[ScannerRowView, ...],
        *,
        select_first: bool = True,
        emit_selection: bool = True,
    ) -> None:
        self._emit_selection = False
        self.setSortingEnabled(False)
        self.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (
                row.symbol,
                row.execution_symbol,
                row.sector,
                row.leader_tier,
                row.signal,
                row.score,
                row.close,
                row.whole_share_capacity,
                row.return_20d,
                row.return_63d,
                row.volatility_20d,
                row.rsi_14d,
                row.reason,
            )
            for column, value in enumerate(values):
                item = _NumericTableWidgetItem(value)
                if row.trend_candidate and column in TREND_COLUMNS:
                    item.setForeground(QColor(self._palette.success))
                self.setItem(index, column, item)
        self.setSortingEnabled(True)
        self.sortItems(SCORE_COLUMN, Qt.DescendingOrder)
        if select_first and rows:
            self.selectRow(0)
        else:
            self.clearSelection()
        self._emit_selection = True
        if emit_selection and rows:
            self.symbol_selected.emit(self.symbol_at(0))

    def symbol_at(self, row: int) -> str:
        item = self.item(row, 0)
        return item.text() if item is not None else ""

    def _selection_changed(self) -> None:
        if not self._emit_selection:
            return
        selected = self.selectedItems()
        if not selected:
            return
        symbol = self.symbol_at(selected[0].row())
        if symbol:
            self.symbol_selected.emit(symbol)

    def set_palette(self, palette: ThemePalette) -> None:
        self._palette = palette
        for row in range(self.rowCount()):
            signal = self.item(row, 4)
            trend = signal is not None and signal.text() == "趋势候选"
            for column in TREND_COLUMNS:
                item = self.item(row, column)
                if item is None:
                    continue
                if trend:
                    item.setForeground(QColor(palette.success))
                else:
                    item.setData(Qt.ForegroundRole, None)
        self.viewport().update()


__all__ = [
    "NUMERIC_COLUMNS",
    "SCANNER_HEADERS",
    "ScannerTable",
]
