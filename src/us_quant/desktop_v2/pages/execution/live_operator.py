"""Render-only Live operator controls for the Execution workspace."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.execution.live_operator_models import (
    LiveOperatorControlView,
)


_FACTS = (
    ("environment", "部署环境"),
    ("feature_flag", "Live feature flag"),
    ("bound_account", "绑定账户"),
    ("fingerprint", "账户指纹"),
    ("authorization", "持久授权"),
    ("session_arm", "本次进程授权"),
    ("kill_latch", "Kill latch"),
    ("startup_proof", "启动证明"),
    ("reconciliation", "对账状态"),
    ("capital_limit", "资金上限"),
    ("order_limit", "单笔上限"),
    ("daily_loss_limit", "日亏损上限"),
    ("position_limit", "持仓上限"),
    ("open_order_limit", "在途订单上限"),
    ("allowed_strategies", "授权策略"),
    ("allowed_symbols", "授权标的"),
    ("broker_connection", "Live 券商连接"),
)


class LiveOperatorControls(QWidget):
    """Displays Live safety state and emits arm/kill/refresh intents only."""

    arm_requested = Signal()
    kill_requested = Signal()
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("liveOperatorControls")
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        title = QLabel("Live 小资金操作台")
        title.setObjectName("sectionTitle")
        self.status_label = QLabel("Live safety 状态尚未读取")
        self.status_label.setWordWrap(True)
        self.fact_labels: dict[str, QLabel] = {}
        facts = QFormLayout()
        for key, label in _FACTS:
            value = QLabel("—")
            value.setWordWrap(True)
            self.fact_labels[key] = value
            facts.addRow(label, value)
        actions = QHBoxLayout()
        self.arm_button = QPushButton("确认并武装 Live 会话")
        self.arm_button.setEnabled(False)
        self.arm_button.clicked.connect(self.arm_requested.emit)
        self.kill_button = QPushButton("紧急 Kill：阻止新增风险")
        self.kill_button.clicked.connect(self.kill_requested.emit)
        self.refresh_button = QPushButton("刷新 Live 状态")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        actions.addWidget(self.arm_button)
        actions.addWidget(self.kill_button)
        actions.addWidget(self.refresh_button)
        layout.addWidget(title)
        layout.addWidget(self.status_label)
        layout.addLayout(facts)
        layout.addLayout(actions)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

    def render(self, view: LiveOperatorControlView) -> None:
        for key, _label in _FACTS:
            self.fact_labels[key].setText(getattr(view, key))
        self.arm_button.setEnabled(view.arm_enabled)
        self.arm_button.setToolTip(view.arm_block_reason)
        self.kill_button.setEnabled(view.kill_latch != "已触发")
        if view.kill_latch == "已触发":
            self.status_label.setText(
                "LIVE HALTED · NEW EXPOSURE BLOCKED · RECONCILIATION REQUIRED"
            )
        elif view.arm_enabled:
            self.status_label.setText(
                "Live canary prerequisites are ready; explicit operator confirmation is required."
            )
        else:
            self.status_label.setText(view.arm_block_reason)


__all__ = ["LiveOperatorControls"]
