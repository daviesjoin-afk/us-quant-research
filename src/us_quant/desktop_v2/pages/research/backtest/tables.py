"""Tables for the native backtest page."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QTableWidget,
    QTableWidgetItem as _QTableWidgetItem,
    QWidget,
)

from us_quant.desktop_v2.pages.research.backtest.models import (
    BacktestComparisonRow,
    BacktestTradeRow,
)
from us_quant.desktop_widgets import _sortable_number, configure_table


COMPARISON_HEADERS = (
    "Run ID",
    "策略版本",
    "代码",
    "区间",
    "总收益",
    "年化",
    "Sharpe",
    "Sortino",
    "Calmar",
    "最大回撤",
    "换手",
    "交易数",
    "成本",
)
TRADES_HEADERS = (
    "信号时间",
    "成交时间",
    "信号代码",
    "执行代码",
    "方向",
    "整股数量",
    "原始开盘",
    "成交价",
    "滑点成本",
    "佣金",
    "成交后持仓",
    "成交后现金",
    "原因",
)


def _backtest_number(value: str) -> float | None:
    """Parse the frozen display formats, including turnover's ``x`` suffix."""

    cleaned = value.strip()
    if cleaned.endswith("x"):
        cleaned = cleaned[:-1]
    return _sortable_number(cleaned)


class _NumericTableWidgetItem(_QTableWidgetItem):
    """Keep formatted text while sorting numeric-looking cells numerically."""

    def __lt__(self, other: _QTableWidgetItem) -> bool:
        left = _backtest_number(self.text())
        right = _backtest_number(other.text())
        if left is not None and right is not None:
            return left < right
        return self.text().casefold() < other.text().casefold()


class BacktestComparisonTable(QTableWidget):
    """Owns comparison rows, sorting and stable full-run-id selection."""

    run_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(COMPARISON_HEADERS), parent)
        self.setHorizontalHeaderLabels(COMPARISON_HEADERS)
        self._emit_selection = False
        configure_table(self)
        self.itemSelectionChanged.connect(self._selection_changed)

    def render(
        self,
        rows: tuple[BacktestComparisonRow, ...],
        *,
        selected_run_id: str | None = None,
    ) -> None:
        self._emit_selection = False
        self.setSortingEnabled(False)
        self.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (
                row.run_id_display,
                row.strategy,
                row.symbol,
                row.interval,
                row.total_return,
                row.annualized_return,
                row.sharpe,
                row.sortino,
                row.calmar,
                row.max_drawdown,
                row.turnover,
                row.trade_count,
                row.commission,
            )
            for column, value in enumerate(values):
                item = _NumericTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, row.run_id)
                self.setItem(index, column, item)
        self.setSortingEnabled(True)
        self._restore_selection(rows, selected_run_id)
        self._emit_selection = True

    def selected_run_id(self) -> str | None:
        selected = self.selectedItems()
        if not selected:
            return None
        item = self.item(selected[0].row(), 0)
        if item is None:
            return None
        key = item.data(Qt.UserRole)
        return str(key) if key is not None else None

    def _restore_selection(
        self,
        rows: tuple[BacktestComparisonRow, ...],
        selected_run_id: str | None,
    ) -> None:
        if not rows:
            return
        available = {row.run_id for row in rows}
        wanted = (
            selected_run_id
            if selected_run_id in available
            else rows[0].run_id
        )
        for row_index in range(self.rowCount()):
            item = self.item(row_index, 0)
            if item is not None and item.data(Qt.UserRole) == wanted:
                self.selectRow(row_index)
                return

    def _selection_changed(self) -> None:
        if not self._emit_selection:
            return
        run_id = self.selected_run_id()
        if run_id:
            self.run_selected.emit(run_id)


class BacktestTradesTable(QTableWidget):
    """Owns frozen trade rows and numeric-aware sorting."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(TRADES_HEADERS), parent)
        self.setHorizontalHeaderLabels(TRADES_HEADERS)
        configure_table(self)

    def render(self, rows: tuple[BacktestTradeRow, ...]) -> None:
        self.setSortingEnabled(False)
        self.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (
                row.signal_date,
                row.fill_date,
                row.signal_symbol,
                row.execution_symbol,
                row.side,
                row.quantity,
                row.raw_price,
                row.fill_price,
                row.slippage_cost,
                row.commission,
                row.position_after,
                row.cash_after,
                row.reason,
            )
            for column, value in enumerate(values):
                self.setItem(index, column, _NumericTableWidgetItem(value))
        self.setSortingEnabled(True)


__all__ = [
    "BacktestComparisonTable",
    "BacktestTradesTable",
    "COMPARISON_HEADERS",
    "TRADES_HEADERS",
]
