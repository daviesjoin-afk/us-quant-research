"""Desktop UI v2 Settings workspace.

This page replaced ``DesktopSettingsPanel`` plus the seventeen ``settings_*``
widget aliases the window exposed.  The page now owns every Settings widget --
the appearance, credential and connection sections, the storage text and the
save button -- and the window owns the transaction: a signal carries an
immutable :class:`SettingsDraft` or :class:`CredentialDraft`, and the window
decides whether it may be committed.

It holds no ``DesktopSettingsService``, no credential service, no market or
broker application and no ``UserPreferences``: turning a draft into persisted
settings is the window's job.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .appearance import AppearanceSection
from .connection import ConnectionSection
from .credentials import CredentialsSection
from .models import (
    CredentialDraft,
    SettingsDraft,
    SettingsPageView,
    SettingsStorageView,
)

STORAGE_BOUNDARY_NOTE = (
    "整股限制与 Paper 环境为锁定边界；模拟下单能力默认关闭，"
    "且不能绕过 DU 账户、演练上限、策略晋级和会话武装。"
)


def _api_provider_for(market_provider: str) -> str:
    """Map a market provider to the API provider shown."""

    return "ibkr" if market_provider == "ibkr_extended" else market_provider


class SettingsPage(QScrollArea):
    """Owns the Settings widgets and reports what the operator asked for."""

    theme_preview_requested = Signal(str)
    market_provider_selected = Signal(str)
    switch_provider_requested = Signal(object)
    api_provider_selected = Signal(str)
    save_credentials_requested = Signal(object)
    clear_credentials_requested = Signal(str)
    paper_order_capability_toggled = Signal(bool)
    extended_hours_paper_toggled = Signal(bool)
    save_preferences_requested = Signal(object)

    def __init__(
        self,
        draft: SettingsDraft,
        storage: SettingsStorageView,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPage")
        page = QWidget()
        page.setMinimumHeight(820)
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        self.appearance = AppearanceSection(draft)
        self.credentials = CredentialsSection(
            CredentialDraft(
                provider=_api_provider_for(draft.market_provider),
                api_key="",
                api_secret="",
            )
        )
        self.connection = ConnectionSection(draft)
        for section in (self.appearance, self.credentials, self.connection):
            layout.addWidget(section)

        layout.addWidget(self._build_storage(storage))

        action_row = QHBoxLayout()
        self.save_button = QPushButton("应用并保存设置")
        action_row.addStretch()
        action_row.addWidget(self.save_button)
        layout.addLayout(action_row)
        layout.addStretch()

        self._connect_sections()

        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(page)

    # -- construction ---------------------------------------------------

    def _build_storage(self, storage: SettingsStorageView) -> QFrame:
        frame = QFrame()
        frame.setObjectName("panel")
        frame.setMaximumHeight(230)
        layout = QVBoxLayout(frame)
        title = QLabel("数据、日志与安全边界")
        title.setObjectName("sectionTitle")
        text = QTextEdit()
        text.setReadOnly(True)
        text.setMaximumHeight(130)
        text.setPlainText(
            f"用户设置：{storage.settings_path}\n"
            f"加密凭据：{storage.credentials_path}\n"
            f"运行数据库：{storage.runtime_path}\n"
            f"脱敏导出：{storage.exports_path}\n"
            f"{STORAGE_BOUNDARY_NOTE}"
        )
        layout.addWidget(title)
        layout.addWidget(text)
        return frame

    def _connect_sections(self) -> None:
        appearance = self.appearance
        appearance.theme_preview_requested.connect(
            self.theme_preview_requested.emit
        )
        appearance.market_provider_selected.connect(
            self.market_provider_selected.emit
        )
        appearance.switch_requested.connect(self._switch_clicked)

        credentials = self.credentials
        credentials.api_provider_selected.connect(
            self.api_provider_selected.emit
        )
        credentials.save_requested.connect(self._save_credentials_clicked)
        credentials.clear_requested.connect(self._clear_credentials_clicked)

        connection = self.connection
        connection.paper_order_capability_toggled.connect(
            self.paper_order_capability_toggled.emit
        )
        connection.extended_hours_paper_toggled.connect(
            self.extended_hours_paper_toggled.emit
        )

        self.save_button.clicked.connect(self._save_preferences_clicked)

    # -- public API -----------------------------------------------------

    def current_draft(self) -> SettingsDraft:
        """Read every control back as an immutable draft; pure, no signal."""

        return SettingsDraft(
            theme=self.appearance.theme(),
            market_provider=self.appearance.market_provider(),
            ibkr_host=self.connection.host(),
            ibkr_port=self.connection.port(),
            ibkr_client_id=self.connection.client_id(),
            connection_timeout_seconds=(
                self.connection.timeout_seconds()
            ),
            paper_order_capability_enabled=(
                self.connection.paper_order_capability_enabled()
            ),
            extended_hours_paper_enabled=(
                self.connection.extended_hours_paper_enabled()
            ),
        )

    def current_credentials_draft(self) -> CredentialDraft:
        """Read the credential inputs; never saves, clears or calls a service."""

        return self.credentials.current_draft()

    def set_theme(
        self,
        theme: str,
        *,
        emit_change: bool = False,
    ) -> None:
        self.appearance.set_theme(theme, emit_change=emit_change)

    def set_market_provider(
        self,
        provider: str,
        *,
        emit_change: bool = False,
    ) -> None:
        self.appearance.set_market_provider(
            provider, emit_change=emit_change
        )

    def set_api_provider(
        self,
        provider: str,
        *,
        emit_change: bool = False,
    ) -> None:
        self.credentials.set_api_provider(provider, emit_change=emit_change)

    def set_paper_order_capability(
        self,
        enabled: bool,
        *,
        emit_change: bool = False,
    ) -> None:
        self.connection.set_paper_order_capability(
            enabled, emit_change=emit_change
        )

    def set_extended_hours_paper(
        self,
        enabled: bool,
        *,
        emit_change: bool = False,
    ) -> None:
        self.connection.set_extended_hours_paper(
            enabled, emit_change=emit_change
        )

    def clear_credential_inputs(self) -> None:
        self.credentials.clear_inputs()

    def render(self, view: SettingsPageView) -> None:
        """Draw only the external facts; operator input is never overwritten."""

        self.credentials.render_status(view.credential_status_text)
        self.credentials.render_actions(
            save_enabled=view.credential_save_enabled,
            clear_enabled=view.credential_clear_enabled,
        )
        self.connection.render(view.connection_settings_enabled)

    # -- intents --------------------------------------------------------

    def _switch_clicked(self) -> None:
        self.switch_provider_requested.emit(self.current_draft())

    def _save_credentials_clicked(self) -> None:
        self.save_credentials_requested.emit(
            self.current_credentials_draft()
        )

    def _clear_credentials_clicked(self) -> None:
        self.clear_credentials_requested.emit(
            self.credentials.provider()
        )

    def _save_preferences_clicked(self) -> None:
        self.save_preferences_requested.emit(self.current_draft())


__all__ = ["SettingsPage"]
