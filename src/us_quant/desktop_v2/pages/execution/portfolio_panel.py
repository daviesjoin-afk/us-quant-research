"""Portfolio read-only summary and operator plan controls."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.execution.portfolio_models import PortfolioOperationsView


class PortfolioOperationsPanel(QWidget):
    """Render portfolio facts and emit the raw plan-save intent."""

    plan_save_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._build_summary(layout)
        self._build_plan_editor(layout)

    def _build_summary(self, layout: QVBoxLayout) -> None:
        group = QGroupBox("Portfolio 运行状态")
        form = QFormLayout(group)
        self.summary = QLabel("尚无 Portfolio 状态")
        self.summary.setWordWrap(True)
        form.addRow("账户 / 状态", self.summary)
        self.strategy_table = QTableWidget(0, 11)
        self.strategy_table.setHorizontalHeaderLabels((
            "策略版本", "启用", "权重", "资金上限", "总敞口上限",
            "归属敞口", "股数", "待处理敞口", "已实现盈亏", "费用", "最近决策",
        ))
        self.strategy_table.setEditTriggers(QTableWidget.NoEditTriggers)
        form.addRow("策略分配", self.strategy_table)
        self.symbol_table = QTableWidget(0, 5)
        self.symbol_table.setHorizontalHeaderLabels((
            "代码", "股数", "名义金额", "集中度", "贡献策略",
        ))
        self.symbol_table.setEditTriggers(QTableWidget.NoEditTriggers)
        form.addRow("资产敞口", self.symbol_table)
        layout.addWidget(group)

    def _build_plan_editor(self, layout: QVBoxLayout) -> None:
        group = QGroupBox("Paper Portfolio Operating Plan")
        form = QFormLayout(group)
        self.options = QLabel("可用 Paper Shadow 策略：尚未加载")
        self.options.setWordWrap(True)
        form.addRow("策略目录", self.options)
        self.revision = QLineEdit("0")
        self.revision.setReadOnly(True)
        form.addRow("当前版本", self.revision)
        self.selected = QLineEdit()
        self.selected.setPlaceholderText("strategy-version-a, strategy-version-b")
        form.addRow("选择版本 ID", self.selected)
        self.limits = QLineEdit()
        self.limits.setPlaceholderText("capital|gross|net|single|symbol_ratio|strategy_ratio|max_positions|max_orders")
        form.addRow("硬限额", self.limits)
        self.allocations = QPlainTextEdit()
        self.allocations.setPlaceholderText("每行：version_id|weight|capital|gross|enabled")
        form.addRow("策略分配", self.allocations)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("记录本次计划修改原因")
        form.addRow("修改原因", self.reason)
        self.save = QPushButton("保存计划")
        self.save.clicked.connect(self._emit_plan_save)
        form.addRow(self.save)
        layout.addWidget(group)

    def render_operations(self, view: PortfolioOperationsView) -> None:
        self.summary.setText(
            f"模式 {view.mode}｜运行 {view.runtime_state}｜资本上限 {view.total_capital_limit}｜"
            f"现金 {view.cash}｜权益 {view.equity}｜总敞口 {view.gross_exposure}｜"
            f"净敞口 {view.net_exposure}\n对账：{view.reconciliation_state}｜最近周期：{view.last_cycle}\n"
            f"开放订单：{'；'.join(view.open_orders) or '无'}\n"
            f"待处理组合动作：{'；'.join(view.pending_actions) or '无'}"
        )
        self.strategy_table.setRowCount(len(view.strategy_allocations))
        for row_index, row in enumerate(view.strategy_allocations):
            values = (
                row.strategy_version_id, str(row.enabled), row.capital_weight,
                row.capital_ceiling, row.gross_ceiling, row.exposure,
                str(row.shares), row.pending_exposure, row.realized_pnl,
                row.fees, row.last_decision,
            )
            for column, value in enumerate(values):
                self.strategy_table.setItem(row_index, column, QTableWidgetItem(value))
        self.symbol_table.setRowCount(len(view.positions))
        for row_index, row in enumerate(view.positions):
            values = (
                row.symbol, str(row.shares), row.notional,
                row.concentration, row.contributing_strategies,
            )
            for column, value in enumerate(values):
                self.symbol_table.setItem(row_index, column, QTableWidgetItem(value))

    def render_plan(
        self,
        *,
        options: tuple[str, ...],
        plan: dict[str, object] | None,
        error: str | None = None,
    ) -> None:
        self.options.setText(
            error or ("\n".join(options) if options else "没有可用的 Paper Shadow 策略")
        )
        self.revision.setText(str(plan.get("revision", 0) if plan else 0))
        if plan is None:
            self.selected.clear()
            self.limits.clear()
            self.allocations.clear()
            return
        self.selected.setText(", ".join(plan["selected_version_ids"]))
        self.limits.setText("|".join(str(value) for value in plan["limits"]))
        self.allocations.setPlainText(
            "\n".join("|".join(map(str, row)) for row in plan["allocations"])
        )

    def set_plan_editable(self, editable: bool) -> None:
        for control in (self.selected, self.limits, self.allocations, self.reason, self.save):
            control.setEnabled(editable)

    def _emit_plan_save(self) -> None:
        self.plan_save_requested.emit({
            "expected_revision": self.revision.text(),
            "selected_version_ids": self.selected.text(),
            "limits": self.limits.text(),
            "allocations": self.allocations.toPlainText(),
            "operator_reason": self.reason.text(),
        })


__all__ = ["PortfolioOperationsPanel"]
