"""The Settings connection section: IBKR Gateway and the Paper capabilities.

The section owns the four Gateway fields, the two capability checkboxes and the
explanatory note.  It cannot connect a broker, arm a strategy or decide whether
a capability may be enabled -- a click emits and the window runs the safety
confirmation, then tells the section the resulting state.

Two boundaries are frozen and must not be widened:

* the Paper port is hard-locked to 4002;
* the capability setters are silent by default, so a refusal from the window can
  reset the checkbox without firing a second intent.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .models import SettingsDraft

PAPER_GATEWAY_PORT = 4002
CONNECTION_TIMEOUT_RANGE = (1, 120)
CLIENT_ID_RANGE = (1, 999_999)

GATEWAY_NOTE = (
    "角色分离：IBKR 可同时提供行情，但账户、持仓和订单属于券商链路。"
    "端口硬锁 4002；能力开关默认关闭。开启后仍需唯一 DU 账户、"
    "实时行情、候选与策略门、单笔上限和自动量化页逐会话武装，"
    "不能连接 Live。5×24 仅在 Paper 开关开启时，按盘前/盘后 "
    "SMART 限价、隔夜 OVERNIGHT 限价路由；周末、休市和美东 "
    "03:50–04:00 维护窗口不会提交订单。"
)


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("fieldLabel")
    return label


class ConnectionSection(QFrame):
    """IBKR Gateway fields plus the two Paper capability toggles."""

    paper_order_capability_toggled = Signal(bool)
    extended_hours_paper_toggled = Signal(bool)

    def __init__(
        self,
        draft: SettingsDraft,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        layout = QVBoxLayout(self)
        title = QLabel("IBKR Gateway · 券商连接 / 可选行情源")
        title.setObjectName("sectionTitle")
        row = QGridLayout()
        row.setHorizontalSpacing(10)
        row.setVerticalSpacing(6)

        self.host_input = QLineEdit(draft.ibkr_host)
        self.port_input = QSpinBox()
        self.port_input.setRange(PAPER_GATEWAY_PORT, PAPER_GATEWAY_PORT)
        self.port_input.setValue(draft.ibkr_port)
        self.client_id_input = QSpinBox()
        self.client_id_input.setRange(*CLIENT_ID_RANGE)
        self.client_id_input.setValue(draft.ibkr_client_id)
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(*CONNECTION_TIMEOUT_RANGE)
        self.timeout_input.setValue(
            round(draft.connection_timeout_seconds)
        )

        self.paper_order_capability = QCheckBox(
            "允许 IBKR Paper 模拟下单能力"
        )
        self.paper_order_capability.setChecked(
            draft.paper_order_capability_enabled
        )
        self.extended_hours_paper = QCheckBox(
            "启用 IBKR Paper 5×24 扩展时段（盘前 / 盘后 / 隔夜）"
        )
        self.extended_hours_paper.setChecked(
            draft.extended_hours_paper_enabled
        )
        # Checked first, connected after: construction must fire no signal.
        self.paper_order_capability.toggled.connect(
            self.paper_order_capability_toggled.emit
        )
        self.extended_hours_paper.toggled.connect(
            self.extended_hours_paper_toggled.emit
        )

        row.addWidget(_field_label("Host"), 0, 0)
        row.addWidget(_field_label("Paper 端口"), 0, 1)
        row.addWidget(_field_label("Client ID"), 0, 2)
        row.addWidget(_field_label("超时（秒）"), 0, 3)
        row.addWidget(self.host_input, 1, 0)
        row.addWidget(self.port_input, 1, 1)
        row.addWidget(self.client_id_input, 1, 2)
        row.addWidget(self.timeout_input, 1, 3)
        row.addWidget(self.paper_order_capability, 2, 0, 1, 4)
        row.addWidget(self.extended_hours_paper, 3, 0, 1, 4)
        row.setColumnStretch(0, 3)
        row.setColumnStretch(1, 1)
        row.setColumnStretch(2, 1)
        row.setColumnStretch(3, 1)

        note = QLabel(GATEWAY_NOTE)
        note.setObjectName("emptyState")
        note.setWordWrap(True)
        layout.addWidget(title)
        layout.addLayout(row)
        layout.addWidget(note)

    # -- reads ----------------------------------------------------------

    @property
    def connection_settings_enabled(self) -> bool:
        return self.host_input.isEnabled()

    def host(self) -> str:
        return self.host_input.text()

    def port(self) -> int:
        return int(self.port_input.value())

    def client_id(self) -> int:
        return int(self.client_id_input.value())

    def timeout_seconds(self) -> float:
        return float(self.timeout_input.value())

    def paper_order_capability_enabled(self) -> bool:
        return self.paper_order_capability.isChecked()

    def extended_hours_paper_enabled(self) -> bool:
        return self.extended_hours_paper.isChecked()

    # -- programmatic writes --------------------------------------------

    def set_paper_order_capability(
        self,
        enabled: bool,
        *,
        emit_change: bool = False,
    ) -> None:
        """Set the Paper capability checkbox; silent unless asked to emit."""

        field = self.paper_order_capability
        if field.isChecked() == enabled:
            return
        blocked = field.blockSignals(True)
        field.setChecked(enabled)
        field.blockSignals(blocked)
        if emit_change:
            self.paper_order_capability_toggled.emit(enabled)

    def set_extended_hours_paper(
        self,
        enabled: bool,
        *,
        emit_change: bool = False,
    ) -> None:
        """Set the extended-hours checkbox; silent unless asked to emit."""

        field = self.extended_hours_paper
        if field.isChecked() == enabled:
            return
        blocked = field.blockSignals(True)
        field.setChecked(enabled)
        field.blockSignals(blocked)
        if emit_change:
            self.extended_hours_paper_toggled.emit(enabled)

    # -- rendering ------------------------------------------------------

    def render(self, connection_settings_enabled: bool) -> None:
        """Apply the window's connection-control state.

        Only the Gateway and capability controls are affected; the provider,
        credential and switch controls elsewhere stay open.
        """

        for control in (
            self.host_input,
            self.port_input,
            self.client_id_input,
            self.timeout_input,
            self.paper_order_capability,
            self.extended_hours_paper,
        ):
            control.setEnabled(connection_settings_enabled)


__all__ = [
    "CONNECTION_TIMEOUT_RANGE",
    "CLIENT_ID_RANGE",
    "GATEWAY_NOTE",
    "PAPER_GATEWAY_PORT",
    "ConnectionSection",
]
