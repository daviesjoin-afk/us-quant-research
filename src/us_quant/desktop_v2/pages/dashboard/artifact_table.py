"""Artifact table owned by the Dashboard page."""

from __future__ import annotations

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

from us_quant.desktop_v2.pages.dashboard.models import (
    DashboardArtifactRowView,
    DashboardArtifactTone,
)
from us_quant.desktop_widgets import configure_table
from us_quant.ui_theme import ThemePalette


HEADERS = (
    "产物",
    "状态",
    "数据截至",
    "生成时间",
    "来源",
    "Run ID",
    "限制",
)


class DashboardArtifactTable(QTableWidget):
    """Read-only table that renders immutable artifact-row views."""

    def __init__(self, palette: ThemePalette) -> None:
        super().__init__(0, len(HEADERS))
        self.setHorizontalHeaderLabels(HEADERS)
        configure_table(self)
        self._palette = palette
        self._rows: tuple[DashboardArtifactRowView, ...] = ()

    def render(self, rows: tuple[DashboardArtifactRowView, ...]) -> None:
        self._rows = rows
        self.setSortingEnabled(False)
        self.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (
                row.artifact_type,
                row.status_text,
                row.data_as_of,
                row.generated_at,
                row.source,
                row.run_id,
                row.limitations,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.setItem(row_index, column, item)
        self.setSortingEnabled(True)
        self._recolour_rows()

    def set_palette(self, palette: ThemePalette) -> None:
        self._palette = palette
        self._recolour_rows()

    def _recolour_rows(self) -> None:
        for row_index, row in enumerate(self._rows):
            for column in range(len(HEADERS)):
                item = self.item(row_index, column)
                if item is None:
                    continue
                if row.tone is DashboardArtifactTone.ERROR:
                    item.setForeground(QBrush(QColor(self._palette.error)))
                elif (
                    row.tone is DashboardArtifactTone.WARNING
                    and column in {0, 1}
                ):
                    item.setForeground(QBrush(QColor(self._palette.warning)))
                else:
                    item.setForeground(QBrush())


__all__ = ["DashboardArtifactTable", "HEADERS"]
