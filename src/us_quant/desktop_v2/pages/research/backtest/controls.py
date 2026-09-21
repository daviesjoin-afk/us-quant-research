"""Form controls for the native backtest page."""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.research.backtest.models import (
    BacktestControlView,
    BacktestFormDraft,
    BacktestStrategyOption,
)
from us_quant.desktop_widgets import configure_combo_width


class BacktestControls(QWidget):
    """Owns backtest form widgets and emits immutable form intent."""

    run_selected_requested = Signal(object)
    compare_all_requested = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        per_share_commission: Decimal | float | int,
        minimum_commission: Decimal | float | int,
        slippage_bps: Decimal | float | int,
    ) -> None:
        super().__init__(parent)
        self._build(
            per_share_commission=per_share_commission,
            minimum_commission=minimum_commission,
            slippage_bps=slippage_bps,
        )

    def _build(
        self,
        *,
        per_share_commission: Decimal | float | int,
        minimum_commission: Decimal | float | int,
        slippage_bps: Decimal | float | int,
    ) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(6)

        self.strategy_combo = QComboBox()
        configure_combo_width(
            self.strategy_combo,
            minimum_width=430,
            minimum_contents=26,
        )
        self.symbol_input = QLineEdit("XLF")
        self.symbol_input.setMinimumWidth(90)
        self.symbol_input.setMaximumWidth(140)
        self.symbol_input.setClearButtonEnabled(True)
        self.start_date = QDateEdit(QDate(2018, 1, 1))
        self.start_date.setCalendarPopup(True)
        self.start_date.setMinimumWidth(200)
        self.end_date = QDateEdit(QDate.currentDate())
        self.end_date.setCalendarPopup(True)
        self.end_date.setMinimumWidth(200)
        self.capital_spin = QSpinBox()
        self.capital_spin.setRange(100, 100_000_000)
        self.capital_spin.setValue(1_500)
        self.capital_spin.setPrefix("$")
        self.capital_spin.setMinimumWidth(220)
        self.weight_spin = QSpinBox()
        self.weight_spin.setRange(1, 100)
        self.weight_spin.setValue(100)
        self.weight_spin.setSuffix("% 仓位")
        self.weight_spin.setMinimumWidth(130)
        self.run_selected_button = QPushButton("运行所选版本")
        self.run_selected_button.clicked.connect(
            self._emit_run_selected
        )
        self.compare_all_button = QPushButton("运行全部策略对比")
        self.compare_all_button.clicked.connect(
            self._emit_compare_all
        )

        controls.addWidget(self._field_label("策略版本"), 0, 0)
        controls.addWidget(self._field_label("代码"), 0, 1)
        controls.addWidget(self._field_label("研究资金"), 0, 2)
        controls.addWidget(self._field_label("目标仓位"), 0, 3)
        controls.addWidget(self.strategy_combo, 1, 0)
        controls.addWidget(self.symbol_input, 1, 1)
        controls.addWidget(self.capital_spin, 1, 2)
        controls.addWidget(self.weight_spin, 1, 3)
        controls.addWidget(self._field_label("起始日期"), 2, 0)
        controls.addWidget(self._field_label("结束日期"), 2, 1)
        controls.addWidget(self.start_date, 3, 0)
        controls.addWidget(self.end_date, 3, 1)
        controls.addWidget(self.run_selected_button, 3, 2)
        controls.addWidget(self.compare_all_button, 3, 3)
        controls.setColumnStretch(0, 4)
        controls.setColumnStretch(1, 2)
        controls.setColumnStretch(2, 2)
        controls.setColumnStretch(3, 2)
        layout.addLayout(controls)

        cost_row = QHBoxLayout()
        self.per_share_commission_spin = QDoubleSpinBox()
        self.per_share_commission_spin.setRange(0, 10)
        self.per_share_commission_spin.setDecimals(4)
        self.per_share_commission_spin.setValue(float(per_share_commission))
        self.minimum_commission_spin = QDoubleSpinBox()
        self.minimum_commission_spin.setRange(0, 100)
        self.minimum_commission_spin.setDecimals(2)
        self.minimum_commission_spin.setValue(float(minimum_commission))
        self.slippage_spin = QDoubleSpinBox()
        self.slippage_spin.setRange(0, 500)
        self.slippage_spin.setDecimals(1)
        self.slippage_spin.setValue(float(slippage_bps))
        cost_row.addWidget(QLabel("每股佣金"))
        cost_row.addWidget(self.per_share_commission_spin)
        cost_row.addWidget(QLabel("最低佣金"))
        cost_row.addWidget(self.minimum_commission_spin)
        cost_row.addWidget(QLabel("单边滑点(bps)"))
        cost_row.addWidget(self.slippage_spin)
        layout.addLayout(cost_row)

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def set_strategy_options(
        self,
        options: tuple[BacktestStrategyOption, ...],
    ) -> None:
        previous = self.strategy_combo.currentData()
        self.strategy_combo.blockSignals(True)
        try:
            self.strategy_combo.clear()
            for option in options:
                self.strategy_combo.addItem(option.label, option.version_id)
            index = self.strategy_combo.findData(previous)
            self.strategy_combo.setCurrentIndex(max(0, index))
        finally:
            self.strategy_combo.blockSignals(False)

    def set_controls(self, view: BacktestControlView) -> None:
        self.run_selected_button.setEnabled(view.run_selected_enabled)
        self.compare_all_button.setEnabled(view.compare_all_enabled)

    def draft(self) -> BacktestFormDraft:
        return BacktestFormDraft(
            strategy_version_id=str(self.strategy_combo.currentData() or ""),
            symbol=self.symbol_input.text().strip().upper(),
            start_date=self.start_date.date().toPython(),
            end_date=self.end_date.date().toPython(),
            initial_equity=self.capital_spin.value(),
            target_weight_percent=self.weight_spin.value(),
            per_share_commission=str(
                self.per_share_commission_spin.value()
            ),
            minimum_commission=str(self.minimum_commission_spin.value()),
            slippage_bps=str(self.slippage_spin.value()),
        )

    def _emit_run_selected(self) -> None:
        self.run_selected_requested.emit(self.draft())

    def _emit_compare_all(self) -> None:
        self.compare_all_requested.emit(self.draft())


__all__ = ["BacktestControls"]
