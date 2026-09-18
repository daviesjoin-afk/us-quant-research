"""The Settings page's widgets, without the window behind them.

``MainWindow._settings_tab()`` used to build this page inline: four framed
sections, seventeen controls, and the signal wiring for nine of them, all in
one 364-line method sitting in a 9,300-line module.  None of that needs the
window.  It is a pure function of the preferences it renders and the
callbacks it fires, so it lives here and the window now composes it.

The panel is a :class:`QScrollArea` and *is* the tab widget, exactly as the
method's return value was.  Its scroll behaviour (no frame, resizable, no
horizontal bar) and the 820px content height are part of the layout contract
and are set here.

What this module deliberately is not:

* it does not read a credential, a file or a database.  The storage section
  prints the four paths it is handed and nothing else -- no ``exists()``, no
  directory probe;
* it does not know what a Paper order is.  The two capability checkboxes are
  widgets whose ``toggled`` signals go to callbacks supplied by the window;
  the semantics stay there;
* it holds no service.  No settings transaction, no credential service, no
  market data service, no supervisor.  Saving is a callback;
* it does not own the general UI helpers.  ``field_label`` and
  ``configure_combo_width`` are injected so the label style and the combo
  sizing rule are defined once, in the window, rather than duplicated here.

Construction fires no application callback.  The combos are populated,
positioned and *then* connected, in that order, so ``setCurrentIndex`` during
setup cannot run the theme preview or the provider sync; the initial
``_api_provider_changed()`` / ``_refresh_credential_status()`` pair is run by
``MainWindow._settings_tab()`` after it has aliased the controls, because
those handlers read ``self.settings_*`` off the window.

Everything here is re-exported from :mod:`us_quant.desktop`, so
``from us_quant.desktop import DesktopSettingsPanel`` yields the *same object*
defined here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from us_quant.paths import ApplicationPaths
from us_quant.user_settings import UserPreferences


@dataclass(frozen=True, slots=True)
class DesktopSettingsCallbacks:
    """The nine things the Settings page can ask the window to do."""

    preview_theme_changed: Callable[..., None]
    settings_provider_selected: Callable[..., None]
    switch_to_settings_provider: Callable[..., None]
    api_provider_changed: Callable[..., None]
    save_api_credentials: Callable[..., None]
    clear_api_credentials: Callable[..., None]
    paper_order_capability_toggled: Callable[..., None]
    extended_hours_paper_toggled: Callable[..., None]
    save_user_preferences: Callable[..., None]


class DesktopSettingsPanel(QScrollArea):
    """The Settings tab: widgets, layout and signal wiring only."""

    def __init__(
        self,
        *,
        preferences: UserPreferences,
        paths: ApplicationPaths,
        callbacks: DesktopSettingsCallbacks,
        field_label: Callable[[str], QLabel],
        configure_combo_width: Callable[..., None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        page = QWidget()
        page.setMinimumHeight(820)
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        appearance = QFrame()
        appearance.setObjectName("panel")
        appearance_layout = QVBoxLayout(appearance)
        appearance_title = QLabel("外观与默认工作区")
        appearance_title.setObjectName("sectionTitle")
        appearance_row = QGridLayout()
        appearance_row.setHorizontalSpacing(10)
        appearance_row.setVerticalSpacing(6)
        self.settings_theme_combo = QComboBox()
        self.settings_theme_combo.addItem("深色", "dark")
        self.settings_theme_combo.addItem("浅色", "light")
        self.settings_theme_combo.setCurrentIndex(
            max(
                0,
                self.settings_theme_combo.findData(
                    preferences.theme
                ),
            )
        )
        self.settings_theme_combo.currentIndexChanged.connect(
            callbacks.preview_theme_changed
        )
        self.settings_provider_combo = QComboBox()
        self.settings_provider_combo.addItem(
            "Finnhub 实时成交", "finnhub_trades"
        )
        self.settings_provider_combo.addItem(
            "Alpaca IEX 免费实时", "alpaca_iex"
        )
        self.settings_provider_combo.addItem(
            "IBKR 实时优先 / 延迟回退", "ibkr"
        )
        self.settings_provider_combo.addItem(
            "IBKR 5×24（盘前 / 盘后 / 隔夜）",
            "ibkr_extended",
        )
        self.settings_provider_combo.setCurrentIndex(
            max(
                0,
                self.settings_provider_combo.findData(
                    preferences.market_provider
                ),
            )
        )
        self.settings_provider_combo.currentIndexChanged.connect(
            callbacks.settings_provider_selected
        )
        configure_combo_width(
            self.settings_provider_combo,
            minimum_width=300,
            minimum_contents=22,
        )
        self.settings_switch_provider_button = QPushButton(
            "切换 / 重连行情"
        )
        self.settings_switch_provider_button.clicked.connect(
            callbacks.switch_to_settings_provider
        )
        appearance_row.addWidget(
            field_label("主题"),
            0,
            0,
        )
        appearance_row.addWidget(
            field_label("默认行情源"),
            0,
            1,
        )
        appearance_row.addWidget(self.settings_theme_combo, 1, 0)
        appearance_row.addWidget(
            self.settings_provider_combo,
            1,
            1,
        )
        appearance_row.addWidget(
            self.settings_switch_provider_button,
            1,
            2,
        )
        appearance_row.setColumnStretch(1, 3)
        appearance_row.setColumnStretch(2, 1)
        appearance_layout.addWidget(appearance_title)
        appearance_layout.addLayout(appearance_row)
        layout.addWidget(appearance)

        credentials = QFrame()
        credentials.setObjectName("panel")
        credential_layout = QVBoxLayout(credentials)
        credential_title = QLabel(
            "行情数据凭据（Windows 当前用户加密保存）"
        )
        credential_title.setObjectName("sectionTitle")
        credential_note = QLabel(
            "先选择数据源，再填写该数据源的凭据。输入框不会回填已保存明文；"
            "留空表示不修改。"
            "保存后立即清空输入框，日志和导出不会包含凭据。"
            "IBKR 不使用 API Key，在下方 Gateway 券商连接中管理。"
        )
        credential_note.setObjectName("subtitle")
        credential_note.setWordWrap(True)
        credential_row = QGridLayout()
        credential_row.setHorizontalSpacing(10)
        credential_row.setVerticalSpacing(8)
        self.settings_api_provider_combo = QComboBox()
        self.settings_api_provider_combo.addItem(
            "Finnhub", "finnhub_trades"
        )
        self.settings_api_provider_combo.addItem(
            "Alpaca IEX", "alpaca_iex"
        )
        self.settings_api_provider_combo.addItem(
            "IBKR Gateway（无需 API Key）", "ibkr"
        )
        self.settings_api_provider_combo.setCurrentIndex(
            max(
                0,
                self.settings_api_provider_combo.findData(
                    (
                        "ibkr"
                        if preferences.market_provider
                        == "ibkr_extended"
                        else preferences.market_provider
                    )
                ),
            )
        )
        self.settings_api_provider_combo.currentIndexChanged.connect(
            callbacks.api_provider_changed
        )
        configure_combo_width(
            self.settings_api_provider_combo,
            minimum_width=180,
            minimum_contents=14,
        )
        self.settings_finnhub_key = QLineEdit()
        self.settings_finnhub_key.setEchoMode(QLineEdit.Password)
        self.settings_finnhub_key.setPlaceholderText(
            "Finnhub API Key（留空不修改）"
        )
        self.settings_alpaca_key = QLineEdit()
        self.settings_alpaca_key.setEchoMode(QLineEdit.Password)
        self.settings_alpaca_key.setPlaceholderText(
            "Alpaca API Key（留空不修改）"
        )
        self.settings_alpaca_secret = QLineEdit()
        self.settings_alpaca_secret.setEchoMode(QLineEdit.Password)
        self.settings_alpaca_secret.setPlaceholderText(
            "Alpaca API Secret（留空不修改）"
        )
        self.settings_save_credentials_button = QPushButton(
            "保存所选数据源凭据"
        )
        self.settings_save_credentials_button.clicked.connect(
            callbacks.save_api_credentials
        )
        self.settings_clear_credentials_button = QPushButton(
            "清除所选数据源凭据"
        )
        self.settings_clear_credentials_button.clicked.connect(
            callbacks.clear_api_credentials
        )
        credential_row.addWidget(
            field_label("数据源"),
            0,
            0,
        )
        credential_row.addWidget(
            field_label("凭据（留空不修改）"),
            0,
            1,
            1,
            2,
        )
        credential_row.addWidget(
            self.settings_api_provider_combo,
            1,
            0,
        )
        credential_row.addWidget(
            self.settings_finnhub_key,
            1,
            1,
            1,
            2,
        )
        credential_row.addWidget(
            self.settings_alpaca_key,
            1,
            1,
        )
        credential_row.addWidget(
            self.settings_alpaca_secret,
            1,
            2,
        )
        credential_row.addWidget(
            self.settings_save_credentials_button,
            2,
            1,
        )
        credential_row.addWidget(
            self.settings_clear_credentials_button,
            2,
            2,
        )
        credential_row.setColumnStretch(1, 3)
        credential_row.setColumnStretch(2, 3)
        self.settings_credential_status = QLabel()
        self.settings_credential_status.setObjectName("subtitle")
        credential_layout.addWidget(credential_title)
        credential_layout.addWidget(credential_note)
        credential_layout.addLayout(credential_row)
        credential_layout.addWidget(
            self.settings_credential_status
        )
        layout.addWidget(credentials)

        gateway = QFrame()
        gateway.setObjectName("panel")
        gateway_layout = QVBoxLayout(gateway)
        gateway_title = QLabel(
            "IBKR Gateway · 券商连接 / 可选行情源"
        )
        gateway_title.setObjectName("sectionTitle")
        gateway_row = QGridLayout()
        gateway_row.setHorizontalSpacing(10)
        gateway_row.setVerticalSpacing(6)
        self.settings_ibkr_host = QLineEdit(
            preferences.ibkr_host
        )
        self.settings_ibkr_port = QSpinBox()
        self.settings_ibkr_port.setRange(4002, 4002)
        self.settings_ibkr_port.setValue(preferences.ibkr_port)
        self.settings_ibkr_client_id = QSpinBox()
        self.settings_ibkr_client_id.setRange(1, 999_999)
        self.settings_ibkr_client_id.setValue(
            preferences.ibkr_client_id
        )
        self.settings_ibkr_timeout = QSpinBox()
        self.settings_ibkr_timeout.setRange(1, 120)
        self.settings_ibkr_timeout.setValue(
            round(preferences.connection_timeout_seconds)
        )
        gateway_row.addWidget(field_label("Host"), 0, 0)
        gateway_row.addWidget(
            field_label("Paper 端口"),
            0,
            1,
        )
        gateway_row.addWidget(
            field_label("Client ID"),
            0,
            2,
        )
        gateway_row.addWidget(
            field_label("超时（秒）"),
            0,
            3,
        )
        gateway_row.addWidget(self.settings_ibkr_host, 1, 0)
        gateway_row.addWidget(self.settings_ibkr_port, 1, 1)
        gateway_row.addWidget(
            self.settings_ibkr_client_id,
            1,
            2,
        )
        gateway_row.addWidget(self.settings_ibkr_timeout, 1, 3)
        self.settings_paper_order_capability = QCheckBox(
            "允许 IBKR Paper 模拟下单能力"
        )
        self.settings_paper_order_capability.setChecked(
            preferences.paper_order_capability_enabled
        )
        self.settings_paper_order_capability.toggled.connect(
            callbacks.paper_order_capability_toggled
        )
        self.settings_extended_hours_paper = QCheckBox(
            "启用 IBKR Paper 5×24 扩展时段（盘前 / 盘后 / 隔夜）"
        )
        self.settings_extended_hours_paper.setChecked(
            preferences.extended_hours_paper_enabled
        )
        self.settings_extended_hours_paper.toggled.connect(
            callbacks.extended_hours_paper_toggled
        )
        gateway_row.addWidget(
            self.settings_paper_order_capability,
            2,
            0,
            1,
            4,
        )
        gateway_row.addWidget(
            self.settings_extended_hours_paper,
            3,
            0,
            1,
            4,
        )
        gateway_row.setColumnStretch(0, 3)
        gateway_row.setColumnStretch(1, 1)
        gateway_row.setColumnStretch(2, 1)
        gateway_row.setColumnStretch(3, 1)
        gateway_note = QLabel(
            "角色分离：IBKR 可同时提供行情，但账户、持仓和订单属于券商链路。"
            "端口硬锁 4002；能力开关默认关闭。开启后仍需唯一 DU 账户、"
            "实时行情、候选与策略门、单笔上限和自动量化页逐会话武装，"
            "不能连接 Live。5×24 仅在 Paper 开关开启时，按盘前/盘后 "
            "SMART 限价、隔夜 OVERNIGHT 限价路由；周末、休市和美东 "
            "03:50–04:00 维护窗口不会提交订单。"
        )
        gateway_note.setObjectName("emptyState")
        gateway_note.setWordWrap(True)
        gateway_layout.addWidget(gateway_title)
        gateway_layout.addLayout(gateway_row)
        gateway_layout.addWidget(gateway_note)
        layout.addWidget(gateway)

        storage = QFrame()
        storage.setObjectName("panel")
        storage.setMaximumHeight(230)
        storage_layout = QVBoxLayout(storage)
        storage_title = QLabel("数据、日志与安全边界")
        storage_title.setObjectName("sectionTitle")
        storage_text = QTextEdit()
        storage_text.setReadOnly(True)
        storage_text.setMaximumHeight(130)
        storage_text.setPlainText(
            f"用户设置：{paths.state_root / 'settings'}\n"
            f"加密凭据：{paths.state_root / 'credentials'}\n"
            f"运行数据库：{paths.runtime_root}\n"
            f"脱敏导出：{paths.exports_root}\n"
            "整股限制与 Paper 环境为锁定边界；模拟下单能力默认关闭，"
            "且不能绕过 DU 账户、演练上限、策略晋级和会话武装。"
        )
        storage_layout.addWidget(storage_title)
        storage_layout.addWidget(storage_text)
        layout.addWidget(storage)

        action_row = QHBoxLayout()
        self.settings_save_button = QPushButton("应用并保存设置")
        self.settings_save_button.clicked.connect(
            callbacks.save_user_preferences
        )
        action_row.addStretch()
        action_row.addWidget(self.settings_save_button)
        layout.addLayout(action_row)
        layout.addStretch()

        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(page)
