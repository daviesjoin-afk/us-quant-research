"""The market route's quote table.

``QuoteTableModel`` moved here from ``desktop_widgets``: it was only ever the
market page's table, and leaving it in the shared widget module would have kept
two table models alive for one route.

It was also re-pointed at presentation rows rather than at a ``MarketSnapshot``.
The model now receives finished strings and a tone, so it no longer interprets
``MarketDataMode``, ``realtime_ready`` or ``stale`` -- that work happens in the
Qt-free ``rows`` module.  What it keeps, deliberately, is the behaviour an
earlier round established and the operator relies on:

* a changed symbol set resets the model, while same-symbols updates emit
  ``dataChanged`` for the rows that actually moved.  A model that reset every
  tick would lose the selection and the scroll position of a live grid;
* the user's sort survives those updates, and numeric columns sort numerically
  rather than lexically.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    Qt,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableView,
)

from us_quant.desktop_v2.pages.market.models import (
    MarketQuoteRow,
    MarketRowTone,
)
from us_quant.desktop_v2.pages.market.rows import (
    READY_TONE_COLUMNS,
    STALE_TONE_COLUMNS,
    display_values,
)
# The numeric sort key is already defined once, in ``desktop_widgets``, and the
# window's own table items sort with it.  A second copy here would be free to
# drift, and a grid that sorted differently from its neighbours is the bug the
# shared helper exists to prevent -- the same reason the execution page imports
# ``configure_table`` from the same module.
from us_quant.desktop_widgets import _sortable_number
from us_quant.ui_theme import ThemePalette, theme_palette


QUOTE_HEADERS = (
    "代码",
    "Bid",
    "Ask",
    "Last",
    "Close",
    "点差",
    "有效类型",
    "更新时间",
    "Age(s)",
    "代次",
    "来源",
    "覆盖",
    "状态",
    "原因",
)

#: The column widths the legacy route shipped, kept so the grid looks the same.
QUOTE_COLUMN_WIDTHS = (
    82, 96, 96, 96, 96, 96, 112,
    168, 78, 70, 96, 420, 92, 360,
)


class QuoteTableModel(QAbstractTableModel):
    """Small incremental model for the live quote grid."""

    HEADERS = QUOTE_HEADERS

    def __init__(self, theme_name: str = "dark") -> None:
        super().__init__()
        self._rows: list[tuple[str, ...]] = []
        self._tones: list[MarketRowTone] = []
        self._palette: ThemePalette = theme_palette(theme_name)
        self._sort_column = -1
        self._sort_order = Qt.AscendingOrder
        self.reset_count = 0
        self.changed_row_count = 0

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.DisplayRole,
    ):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        value = self._rows[index.row()][index.column()]
        if role in {Qt.DisplayRole, Qt.ToolTipRole}:
            return value
        if role == Qt.ForegroundRole:
            tone = self._tones[index.row()]
            column = index.column()
            if tone is MarketRowTone.ERROR and column in STALE_TONE_COLUMNS:
                return QColor(self._palette.error)
            if tone is MarketRowTone.SUCCESS and column in READY_TONE_COLUMNS:
                return QColor(self._palette.success)
        return None

    def sort(
        self, column: int, order: Qt.SortOrder = Qt.AscendingOrder
    ) -> None:
        self._sort_column = column
        self._sort_order = order
        self._resort()

    def set_palette(self, palette: ThemePalette) -> None:
        """Adopt the window's palette and recolour the rows already drawn."""

        self._palette = palette
        if self._rows:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._rows) - 1, len(self.HEADERS) - 1),
                [Qt.ForegroundRole],
            )

    def set_theme(self, theme_name: str) -> None:
        """Kept for callers that still name a theme rather than a palette."""

        self.set_palette(theme_palette(theme_name))

    def update_rows(self, rows: tuple[MarketQuoteRow, ...]) -> None:
        """Replace or patch the grid from presentation rows.

        A changed symbol set is a reset; the same symbols updating in place are
        patched row by row, so the table does not lose the operator's selection,
        sort or scroll position on every tick.
        """

        values = display_values(rows)
        tones = tuple(row.tone for row in rows)
        current_symbols = tuple(row[0] for row in self._rows)
        incoming_symbols = tuple(row[0] for row in values)
        if set(current_symbols) != set(incoming_symbols):
            self.beginResetModel()
            self._rows = list(values)
            self._tones = list(tones)
            self.endResetModel()
            self.reset_count += 1
            if self._sort_column >= 0:
                self._resort()
            return
        incoming_by_symbol = {
            row[0]: (row, tone) for row, tone in zip(values, tones)
        }
        ordered = [incoming_by_symbol[symbol] for symbol in current_symbols]

        changed: list[int] = []
        for index, (row, tone) in enumerate(ordered):
            if row != self._rows[index] or tone != self._tones[index]:
                self._rows[index] = row
                self._tones[index] = tone
                changed.append(index)
        self.changed_row_count += len(changed)
        for row_index in changed:
            self.dataChanged.emit(
                self.index(row_index, 0),
                self.index(row_index, len(self.HEADERS) - 1),
                [
                    Qt.DisplayRole,
                    Qt.ToolTipRole,
                    Qt.ForegroundRole,
                ],
            )
        if changed and self._sort_column >= 0:
            self._resort()

    def _resort(self) -> None:
        if self._sort_column < 0 or len(self._rows) < 2:
            return
        combined = list(zip(self._rows, self._tones))
        column = self._sort_column

        def key(item):  # type: ignore[no-untyped-def]
            value = item[0][column]
            numeric = _sortable_number(value)
            return (
                numeric is None,
                numeric if numeric is not None else value.casefold(),
            )

        self.layoutAboutToBeChanged.emit()
        combined.sort(
            key=key,
            reverse=self._sort_order == Qt.DescendingOrder,
        )
        self._rows = [row for row, _ in combined]
        self._tones = [tone for _, tone in combined]
        self.layoutChanged.emit()


class QuoteTable(QTableView):
    """The configured quote grid, with the route's column widths applied."""

    def __init__(
        self,
        model: QuoteTableModel,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("marketQuoteTable")
        self.setModel(model)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalHeader().setVisible(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        for column, width in enumerate(QUOTE_COLUMN_WIDTHS):
            self.setColumnWidth(column, width)


__all__ = [
    "QUOTE_COLUMN_WIDTHS",
    "QUOTE_HEADERS",
    "QuoteTable",
    "QuoteTableModel",
]
