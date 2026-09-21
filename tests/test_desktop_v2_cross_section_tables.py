"""Real-Qt tests for the native cross-section tables."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.research.cross_section.models import (
    CrossSectionCandidateRow,
    CrossSectionFoldRow,
)
from us_quant.desktop_v2.pages.research.cross_section.tables import (
    CANDIDATE_EMPTY_HEADERS,
    CANDIDATE_REPORT_HEADERS,
    FOLD_EMPTY_HEADERS,
    FOLD_REPORT_HEADERS,
    CrossSectionCandidateTable,
    CrossSectionFoldTable,
)


_APP = QApplication.instance() or QApplication([])


def _candidate(
    *,
    oos_return: str = "+1.0%",
    drawdown: str = "1.0%",
    sharpe: str = "1.00",
    trades: str = "1",
    cost: str = "+1.0%",
) -> CrossSectionCandidateRow:
    return CrossSectionCandidateRow(
        selected="momentum_63_weekly_top3",
        oos_return=oos_return,
        oos_drawdown=drawdown,
        training_sharpe=sharpe,
        trade_count=trades,
        cost_2x_return=cost,
    )


def _fold(
    *,
    fold: str = "1",
    oos_return: str = "+1.0%",
    cost: str = "+1.0%",
    risk: str = "10.0%",
    cash: str = "10.0%",
) -> CrossSectionFoldRow:
    return CrossSectionFoldRow(
        fold=fold,
        test_interval="2024-01-02 → 2024-06-28",
        selected="momentum_63_weekly_top3",
        oos_return=oos_return,
        cost_2x_return=cost,
        max_risk_exposure=risk,
        average_cash=cash,
    )


def _headers(table) -> tuple[str, ...]:
    return tuple(
        table.horizontalHeaderItem(column).text()
        for column in range(table.columnCount())
    )


def test_candidate_headers_switch_between_empty_and_report() -> None:
    table = CrossSectionCandidateTable()
    assert _headers(table) == CANDIDATE_EMPTY_HEADERS
    table.render((_candidate(),), has_report=True)
    assert _headers(table) == CANDIDATE_REPORT_HEADERS
    table.render((), has_report=False)
    assert _headers(table) == CANDIDATE_EMPTY_HEADERS


def test_fold_headers_switch_between_empty_and_report() -> None:
    table = CrossSectionFoldTable()
    assert _headers(table) == FOLD_EMPTY_HEADERS
    table.render((_fold(),), has_report=True)
    assert _headers(table) == FOLD_REPORT_HEADERS
    table.render((), has_report=False)
    assert _headers(table) == FOLD_EMPTY_HEADERS


def test_candidate_numeric_columns_sort_numerically() -> None:
    rows = (
        _candidate(
            oos_return="+2.0%",
            drawdown="1.0%",
            sharpe="2.00",
            trades="2",
            cost="+2.0%",
        ),
        _candidate(
            oos_return="+10.0%",
            drawdown="10.0%",
            sharpe="10.00",
            trades="10",
            cost="+10.0%",
        ),
    )
    expected = {
        1: "+10.0%",
        2: "10.0%",
        3: "10.00",
        4: "10",
        5: "+10.0%",
    }
    for column, wanted in expected.items():
        table = CrossSectionCandidateTable()
        table.render(rows, has_report=True)
        table.sortItems(column, Qt.DescendingOrder)
        assert table.item(0, column).text() == wanted


def test_fold_numeric_columns_sort_numerically() -> None:
    rows = (
        _fold(fold="2", oos_return="+2.0%", cost="+2.0%", risk="2.0%", cash="2.0%"),
        _fold(
            fold="10",
            oos_return="+10.0%",
            cost="+10.0%",
            risk="10.0%",
            cash="10.0%",
        ),
    )
    expected = {
        0: "10",
        3: "+10.0%",
        4: "+10.0%",
        5: "10.0%",
        6: "10.0%",
    }
    for column, wanted in expected.items():
        table = CrossSectionFoldTable()
        table.render(rows, has_report=True)
        table.sortItems(column, Qt.DescendingOrder)
        assert table.item(0, column).text() == wanted


def test_empty_render_has_no_rows() -> None:
    candidate = CrossSectionCandidateTable()
    fold = CrossSectionFoldTable()
    candidate.render((), has_report=False)
    fold.render((), has_report=False)
    assert candidate.rowCount() == 0
    assert fold.rowCount() == 0