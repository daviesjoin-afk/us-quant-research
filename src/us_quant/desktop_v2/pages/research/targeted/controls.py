"""Targeted validation controls: widgets that emit intent only."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedControlView,
    TargetedStrategyOption,
)
from us_quant.desktop_widgets import configure_combo_width


class TargetedControls(QWidget):
    """The strategy, target, Shadow and evidence controls."""

    strategy_selected = Signal(str)
    target_apply_requested = Signal(str)
    target_subscribe_requested = Signal(str)
    shadow_start_requested = Signal()
    shadow_stop_requested = Signal()
    replay_requested = Signal()
    robustness_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.strategy_combo = QComboBox()
        configure_combo_width(
            self.strategy_combo, minimum_width=300, minimum_contents=18
        )
        self.strategy_combo.setToolTip(
            "选择驱动本次实时影子会话的不可变策略版本"
        )
        self.strategy_combo.currentIndexChanged.connect(
            self._strategy_changed
        )

        self.target_symbol_input = QLineEdit()
        self.target_symbol_input.setPlaceholderText(
            "输入本次做 T 的美股或 ETF 代码"
        )
        self.target_symbol_input.setMaxLength(10)
        self.target_symbol_input.setMinimumWidth(220)
        self.target_symbol_input.setClearButtonEnabled(True)
        self.target_symbol_input.returnPressed.connect(self._apply_target)
        self.target_symbol_apply_button = QPushButton("应用标的")
        self.target_symbol_apply_button.clicked.connect(self._apply_target)
        self.target_symbol_subscribe_button = QPushButton("订阅该标的行情")
        self.target_symbol_subscribe_button.clicked.connect(self._subscribe_target)
        self.target_symbol_status = QLabel("未指定")
        self.target_symbol_status.setObjectName("subtitle")
        self.target_symbol_status.setWordWrap(True)
        self.minute_data_status = QLabel(
            "分钟证据：输入代码后显示本地已录数据；只回放 fresh bid/ask。"
        )
        self.minute_data_status.setObjectName("subtitle")
        self.minute_data_status.setWordWrap(True)

        self.shadow_start_button = QPushButton("启动内部仿真")
        self.shadow_start_button.setDefault(True)
        self.shadow_start_button.clicked.connect(self.shadow_start_requested.emit)
        self.shadow_stop_button = QPushButton("停止内部仿真")
        self.shadow_stop_button.clicked.connect(self.shadow_stop_requested.emit)
        self.shadow_stop_button.setEnabled(False)
        self.replay_button = QPushButton("回放已录分钟数据")
        self.replay_button.clicked.connect(self.replay_requested.emit)
        self.robustness_button = QPushButton("多日稳健性评估")
        self.robustness_button.clicked.connect(self.robustness_requested.emit)

        self._build_layout(layout)

    def _build_layout(self, layout: QVBoxLayout) -> None:
        strategy_label = QLabel("1. 策略版本")
        strategy_label.setObjectName("fieldLabel")
        target_label = QLabel("2. 标的与行情")
        target_label.setObjectName("fieldLabel")
        session_label = QLabel("3. 会话控制")
        session_label.setObjectName("fieldLabel")
        target_actions = QHBoxLayout()
        target_actions.setSpacing(6)
        target_actions.addWidget(self.target_symbol_input, 1)
        target_actions.addWidget(self.target_symbol_apply_button)
        target_actions.addWidget(self.target_symbol_subscribe_button)
        session_actions = QHBoxLayout()
        session_actions.setSpacing(8)
        session_actions.addWidget(session_label)
        session_actions.addWidget(self.shadow_start_button)
        session_actions.addWidget(self.shadow_stop_button)
        session_actions.addStretch()
        session_actions.addWidget(self.replay_button)
        session_actions.addWidget(self.robustness_button)
        layout.addWidget(strategy_label)
        layout.addWidget(self.strategy_combo)
        layout.addWidget(target_label)
        layout.addLayout(target_actions)
        layout.addWidget(self.target_symbol_status)
        layout.addWidget(self.minute_data_status)
        layout.addLayout(session_actions)

    def _strategy_changed(self, _index: int) -> None:
        version_id = self.selected_strategy_version_id()
        if version_id:
            self.strategy_selected.emit(version_id)

    def _apply_target(self) -> None:
        symbol = self.target_symbol()
        if symbol:
            self.target_apply_requested.emit(symbol)

    def _subscribe_target(self) -> None:
        symbol = self.target_symbol()
        if symbol:
            self.target_subscribe_requested.emit(symbol)

    def render(self, controls: TargetedControlView) -> None:
        self.strategy_combo.setEnabled(controls.strategy_enabled)
        self.target_symbol_input.setEnabled(controls.target_enabled)
        self.target_symbol_apply_button.setEnabled(controls.target_enabled)
        self.target_symbol_subscribe_button.setEnabled(controls.subscribe_enabled)
        self.shadow_start_button.setEnabled(controls.shadow_start_enabled)
        self.shadow_stop_button.setEnabled(controls.shadow_stop_enabled)
        self.replay_button.setEnabled(controls.replay_enabled)
        self.robustness_button.setEnabled(controls.robustness_enabled)

    def set_strategy_options(
        self,
        options: tuple[TargetedStrategyOption, ...],
        selected_version_id: str | None = None,
    ) -> None:
        blocked = self.strategy_combo.blockSignals(True)
        try:
            self.strategy_combo.clear()
            for option in options:
                self.strategy_combo.addItem(option.label, option.version_id)
            if selected_version_id is not None:
                index = self.strategy_combo.findData(selected_version_id)
                if index >= 0:
                    self.strategy_combo.setCurrentIndex(index)
        finally:
            self.strategy_combo.blockSignals(blocked)

    def set_selected_strategy_version(self, version_id: str) -> None:
        index = self.strategy_combo.findData(version_id)
        if index < 0 or index == self.strategy_combo.currentIndex():
            return
        blocked = self.strategy_combo.blockSignals(True)
        try:
            self.strategy_combo.setCurrentIndex(index)
        finally:
            self.strategy_combo.blockSignals(blocked)

    def selected_strategy_version_id(self) -> str:
        return str(self.strategy_combo.currentData() or "")

    def target_symbol(self) -> str:
        return self.target_symbol_input.text().strip().upper()

    def set_target_symbol(self, symbol: str) -> None:
        self.target_symbol_input.setText(symbol.strip().upper())

    def set_target_status(self, text: str) -> None:
        self.target_symbol_status.setText(text)

    def set_minute_status(self, text: str) -> None:
        self.minute_data_status.setText(text)


__all__ = ["TargetedControls"]
