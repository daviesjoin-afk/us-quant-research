"""Real-Qt tests for the native ScannerPage."""

from __future__ import annotations

import os
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.research.scanner.models import (
    ScannerChartView,
    ScannerCoverageFacts,
    ScannerFilterMode,
    ScannerPageView,
    ScannerRowView,
)
from us_quant.desktop_v2.pages.research.scanner.page import ScannerPage
from us_quant.desktop_v2.pages.research.scanner.table import (
    SCANNER_HEADERS,
    ScannerTable,
)
from us_quant.ui_theme import DARK_THEME, LIGHT_THEME


_APP = QApplication.instance() or QApplication([])


def _row(
    symbol: str,
    *,
    score: str = "50.0",
    capacity: str = "2",
    return_20d: str = "+1.0%",
    signal: str = "观察",
    trend: bool = False,
    leader: bool = False,
    trade_eligible: bool = False,
    name: str = "Example",
    sector: str = "Technology",
) -> ScannerRowView:
    return ScannerRowView(
        symbol=symbol,
        execution_symbol=symbol,
        name=name,
        sector=sector,
        leader_tier="1" if leader else "2",
        signal=signal,
        score=score,
        close="$10.00",
        whole_share_capacity=capacity,
        return_20d=return_20d,
        return_63d="+0.0%",
        volatility_20d="10.0%",
        rsi_14d="50.0",
        reason="test",
        trade_eligible=trade_eligible,
        trend_candidate=trend,
        leader=leader,
    )


def _view(*rows: ScannerRowView, has_scan: bool = True) -> ScannerPageView:
    return ScannerPageView(
        rows=rows,
        coverage=ScannerCoverageFacts(
            scanned_count=len(rows),
            skipped_count=0,
            research_count=20,
        ),
        has_scan=has_scan,
    )


def test_scan_button_only_emits_scan_intent() -> None:
    page = ScannerPage()
    requested: list[bool] = []
    symbols: list[str] = []
    page.scan_requested.connect(lambda: requested.append(True))
    page.symbol_selected.connect(symbols.append)
    page.scan_button.click()
    assert requested == [True]
    assert symbols == []


def test_filter_combo_uses_stable_ids_and_local_filtering() -> None:
    page = ScannerPage()
    page.render(
        _view(
            _row("AAPL", leader=True),
            _row("MSFT", leader=False),
        )
    )
    assert [
        page.filter_combo.itemData(index)
        for index in range(page.filter_combo.count())
    ] == [mode.value for mode in ScannerFilterMode]

    emitted: list[str] = []
    page.symbol_selected.connect(emitted.append)
    page.filter_combo.setCurrentIndex(3)
    assert page.table.rowCount() == 1
    assert page.table.item(0, 0).text() == "AAPL"
    assert emitted[-1] == "AAPL"
    assert "当前显示 1" in page.coverage_label.text()
    page.search_input.setText("missing")
    assert page.table.rowCount() == 0
    assert page.coverage_label.text() == (
        "当前显示 0 · 最近实际扫描 2 · 缺少/不足 200 根日 K 0 · "
        "非中概研究池 20。筛选器只改变显示，不改变扫描范围。"
    )


def test_headers_are_frozen() -> None:
    page = ScannerPage()
    assert tuple(
        page.table.horizontalHeaderItem(column).text()
        for column in range(page.table.columnCount())
    ) == SCANNER_HEADERS


def test_score_sorting_is_numeric_and_descending() -> None:
    page = ScannerPage()
    page.render(
        _view(
            _row("AAPL", score="100.0"),
            _row("MSFT", score="20.0"),
        )
    )
    assert page.table.item(0, 0).text() == "AAPL"
    assert page.table.item(1, 0).text() == "MSFT"


@pytest.mark.parametrize(
    ("column", "left", "right", "expected_left"),
    (
        (7, "2", "10", "10"),
        (8, "+2.0%", "+10.0%", "+10.0%"),
    ),
)
def test_numeric_columns_sort_numerically(
    column: int, left: str, right: str, expected_left: str
) -> None:
    page = ScannerPage()
    page.render(
        _view(
            _row("AAPL", capacity=left, return_20d=left),
            _row("MSFT", capacity=right, return_20d=right),
        )
    )
    page.table.sortItems(column, Qt.DescendingOrder)
    assert page.table.item(0, 0).text() == ("MSFT" if expected_left == right else "AAPL")


def test_trend_tone_is_limited_to_legacy_columns() -> None:
    page = ScannerPage()
    page.render(_view(_row("AAPL", signal="趋势候选", trend=True)))
    assert page.table.item(0, 0).data(Qt.ForegroundRole) is not None
    assert page.table.item(0, 4).data(Qt.ForegroundRole) is not None
    assert page.table.item(0, 5).data(Qt.ForegroundRole) is not None
    assert page.table.item(0, 1).data(Qt.ForegroundRole) is None


def test_empty_view_does_not_emit_symbol() -> None:
    page = ScannerPage()
    emitted: list[str] = []
    page.symbol_selected.connect(emitted.append)
    page.render(_view(has_scan=False))
    assert page.table.rowCount() == 0
    assert emitted == []


def test_render_selects_first_row_and_emits_symbol() -> None:
    page = ScannerPage()
    emitted: list[str] = []
    page.symbol_selected.connect(emitted.append)
    page.render(
        _view(
            _row("AAPL", score="50.0"),
            _row("MSFT", score="80.0"),
        )
    )
    assert page.table.currentRow() == 0
    assert page.table.item(0, 0).text() == "MSFT"
    assert emitted == ["MSFT"]


def test_manual_selection_emits_the_selected_symbol() -> None:
    page = ScannerPage()
    page.render(_view(_row("AAPL"), _row("MSFT")))
    expected = page.table.symbol_at(1)
    emitted: list[str] = []
    page.symbol_selected.connect(emitted.append)
    page.table.selectRow(1)
    assert emitted == [expected]


def test_render_chart_delegates_to_price_chart() -> None:
    page = ScannerPage()
    points = ((date(2026, 9, 18), 123.4), (date(2026, 9, 19), 125.6))
    page.render_chart(ScannerChartView("AAPL", points, "Apple"))
    assert page.chart.symbol == "AAPL"
    assert page.chart.points == points
    assert page.chart.display_title == "Apple"


def test_set_palette_preserves_selection_without_selection_side_effect() -> None:
    page = ScannerPage(palette=DARK_THEME)
    page.render(_view(_row("AAPL"), _row("MSFT")))
    page.table.selectRow(1)
    selected = page.table.item(page.table.currentRow(), 0).text()
    emitted: list[str] = []
    page.symbol_selected.connect(emitted.append)
    page.set_palette(LIGHT_THEME)
    assert page.table.item(page.table.currentRow(), 0).text() == selected
    assert emitted == []


def test_table_type_is_public_and_selectable() -> None:
    assert isinstance(ScannerPage().table, ScannerTable)
