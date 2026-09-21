"""The Settings credentials section: API provider, secret inputs and actions.

The section owns the provider combo, the three password fields, the two action
buttons and the status line.  It reports only what the operator asked for and
never touches a credential store: saving and clearing are window decisions.

Provider visibility is local presentation behaviour -- choosing a provider shows
only that provider's fields -- so the window never has to call ``setVisible``.

The inputs keep ``QLineEdit.Password`` echo mode and are never back-filled with a
saved secret.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_widgets import configure_combo_width

from .models import CredentialDraft

#: Frozen display order; the id is the stable key, the label is only shown.
API_PROVIDER_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Finnhub", "finnhub_trades"),
    ("Alpaca IEX", "alpaca_iex"),
    ("IBKR Gateway（无需 API Key）", "ibkr"),
)

CREDENTIAL_NOTE = (
    "先选择数据源，再填写该数据源的凭据。输入框不会回填已保存明文；"
    "留空表示不修改。保存后立即清空输入框，日志和导出不会包含凭据。"
    "IBKR 不使用 API Key，在下方 Gateway 券商连接中管理。"
)


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("fieldLabel")
    return label


class CredentialsSection(QFrame):
    """API provider selection, secret inputs and the save/clear actions."""

    api_provider_selected = Signal(str)
    save_requested = Signal()
    clear_requested = Signal()

    def __init__(
        self,
        draft: CredentialDraft,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        layout = QVBoxLayout(self)
        title = QLabel("行情数据凭据（Windows 当前用户加密保存）")
        title.setObjectName("sectionTitle")
        note = QLabel(CREDENTIAL_NOTE)
        note.setObjectName("subtitle")
        note.setWordWrap(True)

        row = QGridLayout()
        row.setHorizontalSpacing(10)
        row.setVerticalSpacing(8)
        self.api_provider_combo = QComboBox()
        for label, key in API_PROVIDER_OPTIONS:
            self.api_provider_combo.addItem(label, key)
        self.api_provider_combo.setCurrentIndex(
            max(0, self.api_provider_combo.findData(draft.provider))
        )
        configure_combo_width(
            self.api_provider_combo,
            minimum_width=180,
            minimum_contents=14,
        )

        self.finnhub_key = self._secret_input("Finnhub API Key（留空不修改）")
        self.alpaca_key = self._secret_input("Alpaca API Key（留空不修改）")
        self.alpaca_secret = self._secret_input(
            "Alpaca API Secret（留空不修改）"
        )

        self.save_button = QPushButton("保存所选数据源凭据")
        self.clear_button = QPushButton("清除所选数据源凭据")
        self.status_label = QLabel()
        self.status_label.setObjectName("subtitle")

        # Position first, connect after: construction must fire no signal.
        self.api_provider_combo.currentIndexChanged.connect(
            self._provider_changed
        )
        self.save_button.clicked.connect(self.save_requested.emit)
        self.clear_button.clicked.connect(self.clear_requested.emit)

        row.addWidget(_field_label("数据源"), 0, 0)
        row.addWidget(_field_label("凭据（留空不修改）"), 0, 1, 1, 2)
        row.addWidget(self.api_provider_combo, 1, 0)
        row.addWidget(self.finnhub_key, 1, 1, 1, 2)
        row.addWidget(self.alpaca_key, 1, 1)
        row.addWidget(self.alpaca_secret, 1, 2)
        row.addWidget(self.save_button, 2, 1)
        row.addWidget(self.clear_button, 2, 2)
        row.setColumnStretch(1, 3)
        row.setColumnStretch(2, 3)

        layout.addWidget(title)
        layout.addWidget(note)
        layout.addLayout(row)
        layout.addWidget(self.status_label)

        self._apply_visibility()

    # -- construction helpers -------------------------------------------

    def _secret_input(self, placeholder: str) -> QLineEdit:
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText(placeholder)
        return field

    # -- reads ----------------------------------------------------------

    def provider(self) -> str:
        return str(
            self.api_provider_combo.currentData() or "finnhub_trades"
        )

    def current_draft(self) -> CredentialDraft:
        """Read the inputs as data; never saves, clears or calls a service."""

        return CredentialDraft(
            provider=self.provider(),
            api_key=self._value_for_provider(),
            api_secret=self._secret_for_provider(),
        )

    def _value_for_provider(self) -> str:
        provider = self.provider()
        if provider == "finnhub_trades":
            return self.finnhub_key.text()
        if provider == "alpaca_iex":
            return self.alpaca_key.text()
        return ""

    def _secret_for_provider(self) -> str:
        if self.provider() == "alpaca_iex":
            return self.alpaca_secret.text()
        return ""

    # -- programmatic writes --------------------------------------------

    def set_api_provider(
        self,
        provider: str,
        *,
        emit_change: bool = False,
    ) -> None:
        """Point the provider combo; silent unless ``emit_change`` is set."""

        index = self.api_provider_combo.findData(provider)
        if index < 0 or index == self.api_provider_combo.currentIndex():
            return
        blocked = self.api_provider_combo.blockSignals(True)
        self.api_provider_combo.setCurrentIndex(index)
        self.api_provider_combo.blockSignals(blocked)
        self._apply_visibility()
        if emit_change:
            self.api_provider_selected.emit(provider)

    def clear_inputs(self) -> None:
        """Clear only the secret inputs; the status line is left alone."""

        self.finnhub_key.clear()
        self.alpaca_key.clear()
        self.alpaca_secret.clear()

    # -- rendering ------------------------------------------------------

    def render_status(self, text: str) -> None:
        self.status_label.setText(text)

    def render_actions(
        self,
        *,
        save_enabled: bool,
        clear_enabled: bool,
    ) -> None:
        self.save_button.setEnabled(save_enabled)
        self.clear_button.setEnabled(clear_enabled)

    # -- signals --------------------------------------------------------

    def _provider_changed(self, _index: int) -> None:
        self._apply_visibility()
        self.api_provider_selected.emit(self.provider())

    def _apply_visibility(self) -> None:
        provider = self.provider()
        self.finnhub_key.setVisible(provider == "finnhub_trades")
        self.alpaca_key.setVisible(provider == "alpaca_iex")
        self.alpaca_secret.setVisible(provider == "alpaca_iex")


__all__ = [
    "API_PROVIDER_OPTIONS",
    "CREDENTIAL_NOTE",
    "CredentialsSection",
]
