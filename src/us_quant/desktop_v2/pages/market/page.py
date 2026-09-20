"""Desktop UI v2 market page: the native market route.

This page replaced ``MainWindow._quotes_tab``, a builder whose widgets the
window then went on mutating from a dozen handlers.  The page now owns those
widgets outright, and the window owns the orchestration: the controls emit
intents, and nothing here calls a service, resolves a credential, starts a feed
or touches a trading runtime.

Three boundaries are load-bearing:

* it **renders and reports intent**.  It holds no ``MarketDataApplication``, no
  ``StreamWorker``, no credential store, no adapter and no runtime, so it draws
  a view model and emits a signal while everything else is decided elsewhere;
* it **cannot decide whether a start or stop is allowed**.  It is told which
  controls are open (``MarketControlView``), so a page that cannot name the
  Paper session cannot admit a stop the session should refuse;
* it **owns the quote table's repaint state**.  Deferring a repaint while the
  operator drags a scrollbar is a UI concern, so the pending view lives here
  rather than in the window.

The page is a thin composition: the metric cards, the control strip
(``controls``) and the quote table (``tables``) are separate modules, and this
one wires their signals to its own and forwards the read/write surface the
window uses.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.market import presenter
from us_quant.desktop_v2.pages.market.controls import (
    MarketControls,
    SCOPE_NOTE,
)
from us_quant.desktop_v2.pages.market.models import (
    MarketControlView,
    MarketPageView,
    MarketSubscriptionDraft,
)
from us_quant.desktop_v2.pages.market.rows import symbol_text
from us_quant.desktop_v2.pages.market.tables import (
    QuoteTable,
    QuoteTableModel,
)
from us_quant.desktop_widgets import MetricCard
from us_quant.ui_theme import ThemePalette, theme_palette


#: The card titles, migrated verbatim so the route looks the same.
CARD_TITLES = (
    ("行情流连接", "未启动", "外部或 IBKR 独立只读 client"),
    ("行情类型", "未知", "以 marketDataType 回调为准"),
    ("日内可用", "否", "必须 fresh Type 1 + bid/ask"),
    ("实时订阅子集", "0", "最多 30；不等于全市场研究池"),
)


class MarketPage(QWidget):
    """Renders the market route and reports what the operator asked for."""

    provider_selected = Signal(str)
    start_requested = Signal()
    stop_requested = Signal()
    load_scan_watchlist_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        palette: ThemePalette | None = None,
        theme_name: str = "dark",
        selected_provider: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("marketPage")
        self._palette = palette or theme_palette(theme_name)
        # Pure UI repaint state: while the operator is dragging a scrollbar the
        # newest view is held here and drawn once the drag ends.
        self._scroll_active = False
        self._pending_view: MarketPageView | None = None
        self._build(selected_provider)

    # -- construction ---------------------------------------------------

    def _build(self, selected_provider: str | None) -> None:
        layout = QVBoxLayout(self)
        layout.addLayout(self._build_cards())
        self.controls = MarketControls(selected_provider=selected_provider)
        self.controls.provider_selected.connect(self.provider_selected.emit)
        self.controls.start_requested.connect(self.start_requested.emit)
        self.controls.stop_requested.connect(self.stop_requested.emit)
        self.controls.load_scan_watchlist_requested.connect(
            self.load_scan_watchlist_requested.emit
        )
        layout.addWidget(self.controls)

        self.scope_label = QLabel()
        self.scope_label.setObjectName("emptyState")
        self.scope_label.setWordWrap(True)
        self.scope_label.setMinimumHeight(40)
        layout.addWidget(self.scope_label)

        note = QLabel(SCOPE_NOTE)
        note.setObjectName("subtitle")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.empty_label = QLabel(presenter.IDLE_EMPTY)
        self.empty_label.setObjectName("emptyState")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setMinimumHeight(48)
        layout.addWidget(self.empty_label)

        splitter = QSplitter(Qt.Vertical)
        self.quote_model = QuoteTableModel(self._palette.name)
        self.quote_table = QuoteTable(self.quote_model)
        for scrollbar in (
            self.quote_table.horizontalScrollBar(),
            self.quote_table.verticalScrollBar(),
        ):
            scrollbar.sliderPressed.connect(self._scroll_started)
            scrollbar.sliderReleased.connect(self._scroll_finished)
        splitter.addWidget(self.quote_table)

        health_panel = QFrame()
        health_panel.setObjectName("panel")
        health_layout = QVBoxLayout(health_panel)
        health_title = QLabel("运行健康与安全门")
        health_title.setObjectName("sectionTitle")
        self.health_text = QTextEdit()
        self.health_text.setReadOnly(True)
        self.health_text.setPlainText(presenter.IDLE_HEALTH)
        health_layout.addWidget(health_title)
        health_layout.addWidget(self.health_text)
        splitter.addWidget(health_panel)
        splitter.setSizes([480, 180])
        layout.addWidget(splitter)

    def _build_cards(self) -> QHBoxLayout:
        cards = QHBoxLayout()
        self.connection_card = MetricCard(*CARD_TITLES[0])
        self.feed_card = MetricCard(*CARD_TITLES[1])
        self.readiness_card = MetricCard(*CARD_TITLES[2])
        self.watchlist_card = MetricCard(*CARD_TITLES[3])
        for card in (
            self.connection_card,
            self.feed_card,
            self.readiness_card,
            self.watchlist_card,
        ):
            cards.addWidget(card)
        return cards

    # -- rendering ------------------------------------------------------

    def render(self, view: MarketPageView) -> None:
        """Draw one view, deferring it while the operator drags a scrollbar."""

        if self._scroll_active:
            self._pending_view = view
            return
        self._apply(view)

    def _apply(self, view: MarketPageView) -> None:
        self.connection_card.set_value(view.connection.value, view.connection.note)
        self.feed_card.set_value(view.feed.value, view.feed.note)
        self.readiness_card.set_value(view.readiness.value, view.readiness.note)
        self.watchlist_card.set_value(view.watchlist.value, view.watchlist.note)
        self.scope_label.setText(view.scope)
        self.empty_label.setText(view.empty_message or "")
        self.empty_label.setVisible(view.empty_message is not None)
        self.health_text.setPlainText(view.health_text)
        self.quote_model.update_rows(view.rows)
        self.controls.render(view.controls)

    def render_health(self, text: str) -> None:
        """Replace the health panel's body without a full view."""

        self.health_text.setPlainText(text)

    def render_controls(
        self,
        controls: MarketControlView,
        *,
        watchlist: str | None = None,
    ) -> None:
        """Publish just the control state, and the watchlist note if given.

        The start/stop and subscription paths change what the route may offer
        without having a snapshot to render, so they need this narrower entry
        point rather than a full ``render``.
        """

        self.controls.render(controls)
        if watchlist is not None:
            self.watchlist_card.set_value(
                str(len(self.subscription_symbols())), watchlist
            )

    def render_failure(self, message: str) -> None:
        """Show a feed failure; the window owns the badge and the log."""

        self.health_text.setPlainText(presenter.failure_text(message))

    def render_scope(self, scope: str) -> None:
        """Replace the scope line written outside a full render."""

        self.scope_label.setText(scope)

    # -- UI-only repaint state ------------------------------------------

    def _scroll_started(self) -> None:
        self._scroll_active = True

    def _scroll_finished(self) -> None:
        self._scroll_active = False
        pending = self._pending_view
        self._pending_view = None
        if pending is not None:
            self._apply(pending)

    @property
    def scroll_active(self) -> bool:
        """Whether a repaint is currently deferred, for callers that must know."""

        return self._scroll_active

    # -- programmatic UI commands ---------------------------------------

    def set_subscription_symbols(self, symbols: tuple[str, ...]) -> None:
        """Write the subscription field; not an operator intent."""

        self.controls.set_symbols_text(",".join(symbols))

    def set_selected_provider(self, source_id: str) -> None:
        """Point the provider combo; deliberately emits nothing."""

        self.controls.set_selected_provider(source_id)

    def set_palette(self, palette: ThemePalette) -> None:
        """Adopt the window's palette and recolour the rows already drawn."""

        self._palette = palette
        self.quote_model.set_palette(palette)

    # -- queries --------------------------------------------------------

    def subscription_draft(self) -> MarketSubscriptionDraft:
        """The operator's current input, as data; not the truth."""

        return MarketSubscriptionDraft(
            source_id=self.controls.selected_provider(),
            symbols=symbol_text(self.controls.symbols_text()),
        )

    def subscription_symbols(self) -> tuple[str, ...]:
        return symbol_text(self.controls.symbols_text())

    def selected_provider(self) -> str:
        return self.controls.selected_provider()


__all__ = ["CARD_TITLES", "MarketPage"]
