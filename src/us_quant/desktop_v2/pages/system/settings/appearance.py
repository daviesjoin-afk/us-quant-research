"""The Settings appearance section: theme and default market provider.

The section owns its widgets and reports intent only.  The combos are
populated, positioned and *then* connected, so building the section cannot run
a theme preview or a provider sync; the programmatic setters are silent by
default for the same reason.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_widgets import configure_combo_width

from .models import SettingsDraft

#: Frozen display order; the key is the stable id and the label is only shown.
THEME_OPTIONS: tuple[tuple[str, str], ...] = (
    ("深色", "dark"),
    ("浅色", "light"),
)
MARKET_PROVIDER_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Finnhub 实时成交", "finnhub_trades"),
    ("Alpaca IEX 免费实时", "alpaca_iex"),
    ("IBKR 实时优先 / 延迟回退", "ibkr"),
    ("IBKR 5×24（盘前 / 盘后 / 隔夜）", "ibkr_extended"),
)


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("fieldLabel")
    return label


class AppearanceSection(QFrame):
    """Theme selection, default market provider and the switch button."""

    theme_preview_requested = Signal(str)
    market_provider_selected = Signal(str)
    switch_requested = Signal()

    def __init__(
        self,
        draft: SettingsDraft,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        layout = QVBoxLayout(self)
        title = QLabel("外观与默认工作区")
        title.setObjectName("sectionTitle")
        row = QGridLayout()
        row.setHorizontalSpacing(10)
        row.setVerticalSpacing(6)

        self.theme_combo = QComboBox()
        for label, key in THEME_OPTIONS:
            self.theme_combo.addItem(label, key)
        self.theme_combo.setCurrentIndex(
            max(0, self.theme_combo.findData(draft.theme))
        )

        self.provider_combo = QComboBox()
        for label, key in MARKET_PROVIDER_OPTIONS:
            self.provider_combo.addItem(label, key)
        self.provider_combo.setCurrentIndex(
            max(0, self.provider_combo.findData(draft.market_provider))
        )
        configure_combo_width(
            self.provider_combo,
            minimum_width=300,
            minimum_contents=22,
        )

        self.switch_button = QPushButton("切换 / 重连行情")

        # Position first, connect after: construction must fire no signal.
        self.theme_combo.currentIndexChanged.connect(
            self._theme_changed
        )
        self.provider_combo.currentIndexChanged.connect(
            self._provider_changed
        )
        self.switch_button.clicked.connect(self.switch_requested.emit)

        row.addWidget(_field_label("主题"), 0, 0)
        row.addWidget(_field_label("默认行情源"), 0, 1)
        row.addWidget(self.theme_combo, 1, 0)
        row.addWidget(self.provider_combo, 1, 1)
        row.addWidget(self.switch_button, 1, 2)
        row.setColumnStretch(1, 3)
        row.setColumnStretch(2, 1)
        layout.addWidget(title)
        layout.addLayout(row)

    # -- reads / writes -------------------------------------------------

    def theme(self) -> str:
        return str(self.theme_combo.currentData() or "dark")

    def market_provider(self) -> str:
        return str(
            self.provider_combo.currentData() or "finnhub_trades"
        )

    def set_theme(
        self,
        theme: str,
        *,
        emit_change: bool = False,
    ) -> None:
        """Point the theme combo; silent unless ``emit_change`` is set."""

        index = self.theme_combo.findData(theme)
        if index < 0 or index == self.theme_combo.currentIndex():
            return
        blocked = self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(index)
        self.theme_combo.blockSignals(blocked)
        if emit_change:
            self.theme_preview_requested.emit(theme)

    def set_market_provider(
        self,
        provider: str,
        *,
        emit_change: bool = False,
    ) -> None:
        """Point the provider combo; silent unless ``emit_change`` is set."""

        index = self.provider_combo.findData(provider)
        if index < 0 or index == self.provider_combo.currentIndex():
            return
        blocked = self.provider_combo.blockSignals(True)
        self.provider_combo.setCurrentIndex(index)
        self.provider_combo.blockSignals(blocked)
        if emit_change:
            self.market_provider_selected.emit(provider)

    # -- signals --------------------------------------------------------

    def _theme_changed(self, _index: int) -> None:
        self.theme_preview_requested.emit(self.theme())

    def _provider_changed(self, _index: int) -> None:
        self.market_provider_selected.emit(self.market_provider())


__all__ = [
    "AppearanceSection",
    "MARKET_PROVIDER_OPTIONS",
    "THEME_OPTIONS",
]
