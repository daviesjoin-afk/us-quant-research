"""The market route's control strip: subscription, provider and the three buttons.

The strip owns the widgets the window used to reach into by name and reports
only what the operator asked for.  It cannot start anything: a click emits, and
the window decides whether the request is one it should honour.  That is why the
provider combo's *programmatic* setter is deliberately silent -- syncing the
combo from the settings panel must not look like the operator changing it, or
the two would drive each other in a loop.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from us_quant.desktop_v2.pages.market.models import (
    MarketControlView,
    MarketProviderOption,
)
from us_quant.desktop_widgets import configure_combo_width


#: The four providers this route offers, in the order the legacy route showed
#: them.  The id is the stable key; the label is only ever displayed.
PROVIDER_OPTIONS = (
    MarketProviderOption(
        "alpaca_iex", "Alpaca IEX 免费实时（单交易所）"
    ),
    MarketProviderOption(
        "finnhub_trades", "Finnhub 实时成交（模拟执行带）"
    ),
    MarketProviderOption("ibkr", "IBKR 实时优先 / 延迟回退"),
    MarketProviderOption(
        "ibkr_extended", "IBKR 5×24（盘前 / 盘后 / 隔夜）"
    ),
)

DEFAULT_PROVIDER = "finnhub_trades"

#: The source ids this route can select.  The combo is built from the options
#: above, so this set and that tuple cannot disagree; the window uses it to
#: reject a provider it was asked to switch to but does not offer.
VALID_MARKET_SOURCES = frozenset(
    option.source_id for option in PROVIDER_OPTIONS
)

SUBSCRIPTION_PLACEHOLDER = (
    "实时订阅子集（最多 30；不是研究池或交易白名单）"
)

SCOPE_NOTE = (
    "研究池、历史扫描和实时订阅是三层范围：上方代码只控制本次"
    " Level I 行情连接，不会限制广域研究或自动候选生成。"
    "API 凭据与默认连接参数已移至“系统·设置”；"
    "Alpaca=IEX盘口，Finnhub=实时成交+明确模拟带，均非SIP/NBBO。"
)


class MarketControls(QWidget):
    """The subscription, provider and session buttons for the market route."""

    provider_selected = Signal(str)
    start_requested = Signal()
    stop_requested = Signal()
    load_scan_watchlist_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        options: tuple[MarketProviderOption, ...] = PROVIDER_OPTIONS,
        selected_provider: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("marketControls")
        self._options = options
        self._build(selected_provider or DEFAULT_PROVIDER)

    # -- construction ---------------------------------------------------

    def _build(self, selected_provider: str) -> None:
        layout = QGridLayout(self)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)

        self.symbols_input = QLineEdit()
        self.symbols_input.setPlaceholderText(SUBSCRIPTION_PLACEHOLDER)
        self.symbols_input.setClearButtonEnabled(True)
        self.symbols_input.textChanged.connect(self._symbols_changed)

        self.provider_combo = QComboBox()
        for option in self._options:
            self.provider_combo.addItem(option.label, option.source_id)
        self.set_selected_provider(selected_provider)
        self.provider_combo.currentIndexChanged.connect(
            self._provider_changed
        )
        configure_combo_width(
            self.provider_combo,
            minimum_width=320,
            minimum_contents=24,
        )

        self.load_watchlist_button = QPushButton("载入扫描候选（最多 30）")
        self.load_watchlist_button.clicked.connect(
            self.load_scan_watchlist_requested.emit
        )
        self.start_button = QPushButton("启动只读流行情")
        self.start_button.clicked.connect(self.start_requested.emit)
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.stop_button.setEnabled(False)

        subscription_label = QLabel("实时行情订阅子集")
        subscription_label.setObjectName("fieldLabel")
        provider_label = QLabel("行情数据源")
        provider_label.setObjectName("fieldLabel")

        layout.addWidget(subscription_label, 0, 0, 1, 4)
        layout.addWidget(self.symbols_input, 1, 0, 1, 4)
        layout.addWidget(provider_label, 2, 0)
        layout.addWidget(self.provider_combo, 3, 0)
        layout.addWidget(self.load_watchlist_button, 3, 1)
        layout.addWidget(self.start_button, 3, 2)
        layout.addWidget(self.stop_button, 3, 3)
        layout.setRowMinimumHeight(3, 40)
        layout.setColumnStretch(0, 4)
        layout.setColumnStretch(1, 2)
        layout.setColumnStretch(2, 2)
        layout.setColumnStretch(3, 1)

    # -- signals --------------------------------------------------------

    def _symbols_changed(self, _text: str) -> None:
        """Reserved: the window reads the draft when it needs it."""

    def _provider_changed(self, _index: int) -> None:
        self.provider_selected.emit(self.selected_provider())

    # -- presentation ---------------------------------------------------

    def render(self, controls: MarketControlView) -> None:
        self.start_button.setEnabled(controls.start_enabled)
        self.start_button.setText(controls.start_label)
        self.stop_button.setEnabled(controls.stop_enabled)
        self.symbols_input.setEnabled(controls.symbols_enabled)
        self.provider_combo.setEnabled(controls.provider_enabled)
        self.load_watchlist_button.setEnabled(controls.load_watchlist_enabled)

    # -- reads / writes -------------------------------------------------

    def symbols_text(self) -> str:
        """The raw text, for the caller's own parsing boundary."""

        return self.symbols_input.text()

    def set_symbols_text(self, text: str) -> None:
        """Write the subscription field without emitting an operator intent."""

        self.symbols_input.setText(text)

    def selected_provider(self) -> str:
        return str(
            self.provider_combo.currentData() or DEFAULT_PROVIDER
        )

    def set_selected_provider(self, source_id: str) -> None:
        """Point the combo at a provider **without** emitting an intent.

        This is the programmatic setter: the settings panel and the window's own
        restore path use it, and neither is the operator choosing a provider.  A
        setter that emitted would let the two combos drive each other.
        """

        index = self.provider_combo.findData(source_id)
        if index < 0 or index == self.provider_combo.currentIndex():
            return
        blocked = self.provider_combo.blockSignals(True)
        self.provider_combo.setCurrentIndex(index)
        self.provider_combo.blockSignals(blocked)


__all__ = [
    "DEFAULT_PROVIDER",
    "PROVIDER_OPTIONS",
    "SCOPE_NOTE",
    "SUBSCRIPTION_PLACEHOLDER",
    "VALID_MARKET_SOURCES",
    "MarketControls",
]
