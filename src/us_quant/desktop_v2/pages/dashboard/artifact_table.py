"""Artifact table owned by the Dashboard page."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

from us_quant.desktop_v2.pages.dashboard.models import (
    DashboardArtifactRowView,
    DashboardArtifactTone,
)
from us_quant.desktop_widgets import configure_table
from us_quant.ui_theme import ThemePalette


#: The tone travels with each visual item, so sorting cannot detach it from
#: the artifact it describes.
_TONE_ROLE = Qt.ItemDataRole.UserRole + 1

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

    def render(self, rows: tuple[DashboardArtifactRowView, ...]) -> None:
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
                item.setData(_TONE_ROLE, row.tone.value)
                self.setItem(row_index, column, item)
        self.setSortingEnabled(True)
        self._recolour_rows()

    def set_palette(self, palette: ThemePalette) -> None:
        self._palette = palette
        self._recolour_rows()

    def _recolour_rows(self) -> None:
        for row_index in range(self.rowCount()):
            for column in range(len(HEADERS)):
                item = self.item(row_index, column)
                if item is None:
                    continue
                tone = DashboardArtifactTone(item.data(_TONE_ROLE))
                if tone is DashboardArtifactTone.ERROR:
                    item.setForeground(QBrush(QColor(self._palette.error)))
                elif (
                    tone is DashboardArtifactTone.WARNING
                    and column in {0, 1}
                ):
                    item.setForeground(QBrush(QColor(self._palette.warning)))
                else:
                    item.setForeground(QBrush())


__all__ = ["DashboardArtifactTable", "HEADERS"]
