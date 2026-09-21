"""Tables for the native cross-section research page."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QTableWidget,
    QTableWidgetItem as _QTableWidgetItem,
    QWidget,
)

from us_quant.desktop_v2.pages.research.cross_section.models import (
    CrossSectionCandidateRow,
    CrossSectionFoldRow,
)
from us_quant.desktop_widgets import _sortable_number, configure_table


CANDIDATE_EMPTY_HEADERS = (
    "参数",
    "收益",
    "回撤",
    "Sharpe",
    "交易数",
    "佣金",
)
CANDIDATE_REPORT_HEADERS = (
    "选中参数",
    "OOS",
    "回撤",
    "训练Sharpe",
    "交易数",
    "2×成本",
)
FOLD_EMPTY_HEADERS = (
    "折",
    "测试区间",
    "选中参数",
    "策略",
    "SPY",
    "回撤",
    "交易数",
)
FOLD_REPORT_HEADERS = (
    "折",
    "测试区间",
    "选中参数",
    "OOS",
    "2×成本",
    "最高风险",
    "平均现金",
)


class _NumericTableWidgetItem(_QTableWidgetItem):
    """Keep formatted text while sorting numeric-looking cells numerically."""

    def __lt__(self, other: _QTableWidgetItem) -> bool:
        left = _sortable_number(self.text())
        right = _sortable_number(other.text())
        if left is not None and right is not None:
            return left < right
        return self.text().casefold() < other.text().casefold()


class CrossSectionCandidateTable(QTableWidget):
    """Owns candidate rows, frozen headers and numeric-aware sorting."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(CANDIDATE_REPORT_HEADERS), parent)
        self.setHorizontalHeaderLabels(CANDIDATE_EMPTY_HEADERS)
        configure_table(self)

    def render(
        self,
        rows: tuple[CrossSectionCandidateRow, ...],
        *,
        has_report: bool,
    ) -> None:
        self.setHorizontalHeaderLabels(
            CANDIDATE_REPORT_HEADERS if has_report else CANDIDATE_EMPTY_HEADERS
        )
        self.setSortingEnabled(False)
        self.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (
                row.selected,
                row.oos_return,
                row.oos_drawdown,
                row.training_sharpe,
                row.trade_count,
                row.cost_2x_return,
            )
            for column, value in enumerate(values):
                self.setItem(index, column, _NumericTableWidgetItem(value))
        self.setSortingEnabled(True)


class CrossSectionFoldTable(QTableWidget):
    """Owns fold rows, frozen headers and numeric-aware sorting."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(FOLD_REPORT_HEADERS), parent)
        self.setHorizontalHeaderLabels(FOLD_EMPTY_HEADERS)
        configure_table(self)

    def render(
        self,
        rows: tuple[CrossSectionFoldRow, ...],
        *,
        has_report: bool,
    ) -> None:
        self.setHorizontalHeaderLabels(
            FOLD_REPORT_HEADERS if has_report else FOLD_EMPTY_HEADERS
        )
        self.setSortingEnabled(False)
        self.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (
                row.fold,
                row.test_interval,
                row.selected,
                row.oos_return,
                row.cost_2x_return,
                row.max_risk_exposure,
                row.average_cash,
            )
            for column, value in enumerate(values):
                self.setItem(index, column, _NumericTableWidgetItem(value))
        self.setSortingEnabled(True)


__all__ = [
    "CANDIDATE_EMPTY_HEADERS",
    "CANDIDATE_REPORT_HEADERS",
    "FOLD_EMPTY_HEADERS",
    "FOLD_REPORT_HEADERS",
    "CrossSectionCandidateTable",
    "CrossSectionFoldTable",
]