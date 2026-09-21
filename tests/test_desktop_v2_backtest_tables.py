"""Real-Qt tests for the native backtest tables."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.research.backtest.models import (
    BacktestComparisonRow,
    BacktestTradeRow,
)
from us_quant.desktop_v2.pages.research.backtest.tables import (
    BacktestComparisonTable,
    BacktestTradesTable,
    COMPARISON_HEADERS,
    TRADES_HEADERS,
)


_APP = QApplication.instance() or QApplication([])


def _comparison(
    run_id: str,
    *,
    total_return: str = "+1.00%",
    sharpe: str = "1.00",
    turnover: str = "1.00x",
    commission: str = "$1.00",
) -> BacktestComparisonRow:
    return BacktestComparisonRow(
        run_id=run_id,
        run_id_display=run_id[:8],
        strategy="策略 · version-",
        symbol="AAPL",
        interval="2025-01-01 → 2025-12-31",
        total_return=total_return,
        annualized_return="+1.00%",
        sharpe=sharpe,
        sortino="1.00",
        calmar="1.00",
        max_drawdown="10.00%",
        turnover=turnover,
        trade_count="1",
        commission=commission,
    )


def _trade(
    *,
    quantity: str = "1",
    raw_price: str = "$1.00",
    fill_price: str = "$1.00",
    commission: str = "$1.00",
    cash_after: str = "$1.00",
) -> BacktestTradeRow:
    return BacktestTradeRow(
        signal_date="2025-01-02",
        fill_date="2025-01-03",
        signal_symbol="AAPL",
        execution_symbol="AAPL",
        side="买入",
        quantity=quantity,
        raw_price=raw_price,
        fill_price=fill_price,
        slippage_cost="$0.10",
        commission=commission,
        position_after=quantity,
        cash_after=cash_after,
        reason="趋势入场",
    )


def _row_for(table, run_id: str) -> int:
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is not None and item.data(Qt.UserRole) == run_id:
            return row
    raise AssertionError(f"run {run_id} not found")


def test_comparison_headers_are_frozen() -> None:
    table = BacktestComparisonTable()
    assert tuple(
        table.horizontalHeaderItem(column).text()
        for column in range(table.columnCount())
    ) == COMPARISON_HEADERS


def test_comparison_uses_full_run_id_selection_key() -> None:
    table = BacktestComparisonTable()
    table.render((_comparison("run-1234567890"),))
    assert table.item(0, 0).text() == "run-1234"
    assert table.item(0, 0).data(Qt.UserRole) == "run-1234567890"
    assert table.selected_run_id() == "run-1234567890"


def test_numeric_comparison_columns_sort_numerically() -> None:
    rows = (
        _comparison(
            "run-A",
            total_return="+2.00%",
            sharpe="2.00",
            turnover="2.00x",
            commission="$2.00",
        ),
        _comparison(
            "run-B",
            total_return="+10.00%",
            sharpe="10.00",
            turnover="10.00x",
            commission="$10.00",
        ),
    )
    for column in (4, 6, 10, 12):
        table = BacktestComparisonTable()
        table.render(rows)
        table.sortItems(column, Qt.DescendingOrder)
        assert table.item(0, 0).data(Qt.UserRole) == "run-B"


def test_manual_selection_emits_full_run_id_after_sorting() -> None:
    table = BacktestComparisonTable()
    table.render(
        (
            _comparison("run-A", total_return="+2.00%"),
            _comparison("run-B", total_return="+10.00%"),
        )
    )
    table.sortItems(4, Qt.AscendingOrder)
    seen: list[str] = []
    table.run_selected.connect(seen.append)
    table.selectRow(_row_for(table, "run-B"))
    assert seen == ["run-B"]


def test_render_restores_selected_run_by_id() -> None:
    table = BacktestComparisonTable()
    table.render(
        (
            _comparison("run-A"),
            _comparison("run-B"),
        ),
        selected_run_id="run-B",
    )
    assert table.selected_run_id() == "run-B"


def test_trade_headers_are_frozen() -> None:
    table = BacktestTradesTable()
    assert tuple(
        table.horizontalHeaderItem(column).text()
        for column in range(table.columnCount())
    ) == TRADES_HEADERS


def test_trade_numeric_columns_sort_numerically() -> None:
    rows = (
        _trade(
            quantity="2",
            raw_price="$2.00",
            fill_price="$2.00",
            commission="$2.00",
            cash_after="$2.00",
        ),
        _trade(
            quantity="10",
            raw_price="$10.00",
            fill_price="$10.00",
            commission="$10.00",
            cash_after="$10.00",
        ),
    )
    for column in (5, 6, 7, 9, 11):
        table = BacktestTradesTable()
        table.render(rows)
        table.sortItems(column, Qt.DescendingOrder)
        assert table.item(0, column).text() == (
            "10" if column == 5 else "$10.00"
        )


def test_trade_display_is_presentational_only() -> None:
    table = BacktestTradesTable()
    table.render((_trade(),))
    assert table.item(0, 4).text() == "买入"
    assert table.item(0, 12).text() == "趋势入场"
