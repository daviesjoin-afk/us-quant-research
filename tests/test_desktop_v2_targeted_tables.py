"""Regression tests for the targeted table component."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedRowTone,
    TargetedTableRow,
)
from us_quant.desktop_v2.pages.research.targeted.tables import TargetedTable
from us_quant.ui_theme import theme_palette


_APP = QApplication.instance() or QApplication([])


def _select_key(table: TargetedTable, key: str) -> None:
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is not None and item.data(Qt.UserRole) == key:
            table.selectRow(row)
            return
    raise AssertionError(f"row {key!r} not found")


def test_the_table_can_keep_the_legacy_sort_column() -> None:
    table = TargetedTable(("id", "value"), sort_column=0)
    table.render_rows(
        (
            TargetedTableRow("b", ("bbb", "2")),
            TargetedTableRow("a", ("aaa", "1")),
        )
    )
    assert table.item(0, 0).text() == "aaa"


def test_tone_is_applied_only_to_the_declared_columns() -> None:
    palette = theme_palette("dark")
    table = TargetedTable(("id", "state", "value"), tone_columns=(1,), palette=palette)
    table.render_rows(
        (TargetedTableRow("a", ("aaa", "阻断", "bad"), tone=TargetedRowTone.WARNING),)
    )
    assert table.item(0, 1).foreground().color().name() == QColor(palette.warning).name()
    assert table.item(0, 2).foreground().color().name() != QColor(palette.warning).name()


def test_selection_key_survives_a_rerender() -> None:
    table = TargetedTable(("id", "value"))
    rows = (
        TargetedTableRow("a", ("aaa", "1")),
        TargetedTableRow("b", ("bbb", "2")),
    )
    table.render_rows(rows)
    _select_key(table, "b")
    table.render_rows(rows)
    assert table.selected_key() == "b"


def test_selection_emits_the_stable_key() -> None:
    table = TargetedTable(("id", "value"))
    table.render_rows(
        (
            TargetedTableRow("a", ("aaa", "1")),
            TargetedTableRow("b", ("bbb", "2")),
        )
    )
    seen: list[str] = []
    table.run_selected.connect(seen.append)
    _select_key(table, "b")
    assert seen == ["b"]


def test_sorting_can_be_disabled_for_a_read_only_detail_table() -> None:
    table = TargetedTable(("id", "value"), sorting_enabled=False)
    assert not table.isSortingEnabled()
