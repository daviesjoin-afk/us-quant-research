"""Reusable immutable-row models for frequently refreshed desktop tables."""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt


class ImmutableRowsTableModel(QAbstractTableModel):
    """Reset atomically from immutable display rows and support stable sorting."""

    def __init__(self, headers: Sequence[str]) -> None:
        super().__init__()
        self._headers = tuple(headers)
        self._rows: tuple[tuple[str, ...], ...] = ()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._headers)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None
        value = self._rows[index.row()][index.column()]
        return value if role in {Qt.DisplayRole, Qt.ToolTipRole} else None

    def headerData(self, section: int, orientation, role: int = Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            if 0 <= section < len(self._headers):
                return self._headers[section]
        return None

    def set_rows(self, rows: Iterable[Sequence[object]]) -> None:
        normalized = tuple(tuple(str(value) for value in row) for row in rows)
        if any(len(row) != len(self._headers) for row in normalized):
            raise ValueError("table row width does not match headers")
        self.beginResetModel()
        self._rows = normalized
        self.endResetModel()

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:  # type: ignore[override]
        if not 0 <= column < len(self._headers):
            return
        self.layoutAboutToBeChanged.emit()
        self._rows = tuple(
            sorted(
                self._rows,
                key=lambda row: _sort_key(row[column]),
                reverse=order == Qt.DescendingOrder,
            )
        )
        self.layoutChanged.emit()


def _sort_key(value: str) -> tuple[int, object]:
    cleaned = value.strip().replace(",", "").replace("$", "")
    percent = cleaned.endswith("%")
    if percent:
        cleaned = cleaned[:-1]
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", cleaned):
        number = float(cleaned)
        return (0, number / 100 if percent else number)
    return (1, value.casefold())
