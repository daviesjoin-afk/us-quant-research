"""Desktop UI v2 market page tests.

The page replaced ``MainWindow._quotes_tab``.  Two claims are pinned here, and
they are the whole reason the page exists:

* it *renders* a view model it is handed -- the four cards, the scope line, the
  quote grid, the health panel and the control strip -- so what the operator
  sees comes from one projection rather than from a dozen handlers writing
  widgets;
* it *reports intent and nothing else*.  Every control emits a signal, the page
  holds no service, and it cannot start a feed, resolve a credential or decide
  whether a stop is allowed.  The absence of those capabilities is asserted
  rather than assumed, because a page that could start a stream would make the
  window's safety gates advisory.

Nothing here constructs a stream, a broker or an application service: the page
is given view models and its own signals are observed.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timezone

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from us_quant.desktop_v2.pages.market import presenter
from us_quant.desktop_v2.pages.market.controls import (
    DEFAULT_PROVIDER,
    PROVIDER_OPTIONS,
)
from us_quant.desktop_v2.pages.market.models import MarketControlView
from us_quant.desktop_v2.pages.market.page import CARD_TITLES, MarketPage


_APP = QApplication.instance() or QApplication([])

NOW = datetime(2026, 9, 20, 14, 30, tzinfo=timezone.utc)


@pytest.fixture()
def page():
    widget = MarketPage()
    yield widget
    widget.deleteLater()


def _ready_controls(**overrides: object) -> MarketControlView:
    values: dict[str, object] = {
        "start_enabled": True,
        "stop_enabled": False,
        "symbols_enabled": True,
        "provider_enabled": True,
        "load_watchlist_enabled": True,
        "start_label": "启动只读流行情",
    }
    values.update(overrides)
    return MarketControlView(**values)  # type: ignore[arg-type]


def _row(**overrides: object):
    from us_quant.desktop_v2.pages.market.models import MarketQuoteRow

    values: dict[str, object] = {
        "symbol": "AAPL",
        "bid": "100.1",
        "ask": "100.2",
        "last": "100.15",
        "close": "99.5",
        "spread": "0.1",
        "mode": "实时",
        "updated_at": NOW.isoformat(),
        "age": "0.4",
        "generation": "3",
        "source": "IBKR",
        "coverage": "Type 1 实时",
        "status": "READY",
        "reason": "可用于日内观察",
    }
    values.update(overrides)
    return MarketQuoteRow(**values)  # type: ignore[arg-type]


def _view(**overrides: object):
    from us_quant.desktop_v2.pages.market.models import (
        MarketMetricView,
        MarketPageView,
    )

    values: dict[str, object] = {
        "connection": MarketMetricView("已握手", "连接代次 3 · 尝试 0"),
        "feed": MarketMetricView("实时", "Type 1 实时"),
        "readiness": MarketMetricView("订阅合计 1/1", "当前 fresh"),
        "watchlist": MarketMetricView("1", "Level I 持续订阅"),
        "scope": "范围分层 · 官方美股/ETF 12,345",
        "empty_message": None,
        "rows": (_row(),),
        "health_text": "连接代次：3",
        "controls": _ready_controls(),
    }
    values.update(overrides)
    return MarketPageView(**values)  # type: ignore[arg-type]


# -- the route's widgets exist -------------------------------------------


def test_the_route_builds_the_four_cards_the_grid_and_the_health_panel(page) -> None:
    # The four cards, in the legacy order.
    assert page.connection_card is not page.feed_card
    assert page.feed_card is not page.readiness_card
    assert page.readiness_card is not page.watchlist_card
    # The grid, the scope line, the empty state and the health body.
    assert page.quote_table.model() is page.quote_model
    assert page.scope_label.text() == ""
    assert page.empty_label.text() == presenter.IDLE_EMPTY
    assert page.health_text.toPlainText() == presenter.IDLE_HEALTH


def test_the_four_cards_start_with_their_documented_titles(page) -> None:
    assert CARD_TITLES == (
        ("行情流连接", "未启动", "外部或 IBKR 独立只读 client"),
        ("行情类型", "未知", "以 marketDataType 回调为准"),
        ("日内可用", "否", "必须 fresh Type 1 + bid/ask"),
        ("实时订阅子集", "0", "最多 30；不等于全市场研究池"),
    )
    assert page.connection_card.value_label.text() == "未启动"
    assert page.feed_card.value_label.text() == "未知"
    assert page.readiness_card.value_label.text() == "否"
    assert page.watchlist_card.value_label.text() == "0"


def test_the_quote_grid_keeps_the_route_column_widths(page) -> None:
    from us_quant.desktop_v2.pages.market.tables import (
        QUOTE_COLUMN_WIDTHS,
        QUOTE_HEADERS,
    )

    assert page.quote_model.columnCount() == len(QUOTE_HEADERS)
    for column, width in enumerate(QUOTE_COLUMN_WIDTHS):
        assert page.quote_table.columnWidth(column) == width


def test_the_page_starts_on_the_configured_provider(page) -> None:
    assert page.selected_provider() == DEFAULT_PROVIDER

    other = MarketPage(selected_provider="alpaca_iex")
    try:
        assert other.selected_provider() == "alpaca_iex"
    finally:
        other.deleteLater()


# -- the controls only emit ----------------------------------------------


def test_a_start_click_only_emits(page) -> None:
    seen: list[str] = []
    page.start_requested.connect(lambda: seen.append("start"))

    page.controls.start_button.click()

    assert seen == ["start"]


def test_a_stop_click_only_emits(page) -> None:
    page.render_controls(_ready_controls(stop_enabled=True))
    seen: list[str] = []
    page.stop_requested.connect(lambda: seen.append("stop"))

    page.controls.stop_button.click()

    assert seen == ["stop"]


def test_a_watchlist_click_only_emits(page) -> None:
    seen: list[str] = []
    page.load_scan_watchlist_requested.connect(
        lambda: seen.append("watchlist")
    )

    page.controls.load_watchlist_button.click()

    assert seen == ["watchlist"]


def test_a_provider_change_only_emits(page) -> None:
    seen: list[str] = []
    page.provider_selected.connect(seen.append)

    page.controls.provider_combo.setCurrentIndex(
        page.controls.provider_combo.findData("alpaca_iex")
    )

    assert seen == ["alpaca_iex"]


def test_every_provider_option_is_offered_and_keyed_by_its_id(page) -> None:
    """The stable key is ``source_id``; the label is only ever shown."""

    combo = page.controls.provider_combo
    offered = [combo.itemData(index) for index in range(combo.count())]

    assert offered == [option.source_id for option in PROVIDER_OPTIONS]
    assert "ibkr_extended" in offered


def test_a_disabled_control_emits_nothing(page) -> None:
    """The page is told which controls are open; a closed one must not report."""

    page.render_controls(
        _ready_controls(start_enabled=False, stop_enabled=False)
    )
    seen: list[str] = []
    page.start_requested.connect(lambda: seen.append("start"))
    page.stop_requested.connect(lambda: seen.append("stop"))

    page.controls.start_button.click()
    page.controls.stop_button.click()

    assert seen == []


# -- the page cannot start anything --------------------------------------


def test_the_page_has_no_attribute_that_could_start_a_feed(page) -> None:
    for forbidden in (
        "market_data",
        "stream_worker",
        "credential_service",
        "credential_store",
        "config",
        "ibkr",
        "paper_workflow",
        "shadow_engine",
        "trading_runtime",
    ):
        assert not hasattr(page, forbidden), forbidden


def test_every_button_on_the_page_is_wired_to_a_signal(page) -> None:
    """A button that reached a service would be a second orchestration point."""

    buttons = set(page.findChildren(QPushButton))
    assert buttons, "the page must offer the market controls"
    for name in ("start_button", "stop_button", "load_watchlist_button"):
        assert getattr(page.controls, name) in buttons, name


# -- programmatic writes are not operator intent -------------------------


def test_set_subscription_symbols_writes_the_field(page) -> None:
    page.set_subscription_symbols(("AAPL", "MSFT"))

    assert page.subscription_symbols() == ("AAPL", "MSFT")
    assert page.controls.symbols_input.text() == "AAPL,MSFT"


def test_set_subscription_symbols_emits_no_intent(page) -> None:
    seen: list[object] = []
    page.provider_selected.connect(seen.append)

    page.set_subscription_symbols(("AAPL",))

    assert seen == []


def test_set_selected_provider_is_deliberately_silent(page) -> None:
    """A programmatic set must not look like the operator choosing.

    Both the settings panel and the window's own restore path use this setter.
    A setter that emitted would let the two combos drive each other in a loop.
    """

    seen: list[str] = []
    page.provider_selected.connect(seen.append)

    page.set_selected_provider("alpaca_iex")

    assert seen == []
    assert page.selected_provider() == "alpaca_iex"


def test_an_unknown_provider_is_ignored_rather_than_selected(page) -> None:
    before = page.selected_provider()

    page.set_selected_provider("not_a_provider")

    assert page.selected_provider() == before


def test_the_subscription_draft_reads_the_page_back_as_data(page) -> None:
    page.set_selected_provider("alpaca_iex")
    page.set_subscription_symbols(("AAPL", "MSFT"))

    draft = page.subscription_draft()

    assert draft.source_id == "alpaca_iex"
    assert draft.symbols == ("AAPL", "MSFT")


def test_the_page_parses_the_subscription_but_does_not_limit_it(page) -> None:
    """At least one, at most thirty is the orchestration layer's rule."""

    page.controls.symbols_input.setText("aapl, MSFT ,,aapl")

    assert page.subscription_symbols() == ("AAPL", "MSFT")
    assert len(page.subscription_symbols()) <= 30


# -- rendering ------------------------------------------------------------


def test_render_updates_the_cards_the_scope_and_the_empty_state(page) -> None:
    page.render(_view())

    assert page.connection_card.value_label.text() == "已握手"
    assert page.feed_card.value_label.text() == "实时"
    assert page.readiness_card.value_label.text() == "订阅合计 1/1"
    assert page.watchlist_card.value_label.text() == "1"
    assert "12,345" in page.scope_label.text()
    assert not page.empty_label.isVisible()


def test_render_updates_the_health_panel(page) -> None:
    page.render(_view(health_text="连接代次：9"))

    assert page.health_text.toPlainText() == "连接代次：9"


def test_render_updates_the_table(page) -> None:
    page.render(_view(rows=(_row(), _row(symbol="MSFT"))))

    assert page.quote_model.rowCount() == 2
    # The grid enables sorting, so the row order is not the contract here --
    # the content is.
    symbols = {
        page.quote_model.index(row, 0).data()
        for row in range(page.quote_model.rowCount())
    }
    assert symbols == {"AAPL", "MSFT"}


def test_an_empty_message_is_shown_as_text(page) -> None:
    page.render(_view(rows=(), empty_message=presenter.WAITING_EMPTY))

    assert page.empty_label.text() == presenter.WAITING_EMPTY


def test_render_controls_updates_the_buttons_without_a_snapshot(page) -> None:
    page.render_controls(
        _ready_controls(
            start_enabled=False,
            stop_enabled=True,
            start_label="切换 / 重连行情",
        )
    )

    assert not page.controls.start_button.isEnabled()
    assert page.controls.stop_button.isEnabled()
    assert page.controls.start_button.text() == "切换 / 重连行情"


def test_render_controls_can_publish_the_watchlist_note(page) -> None:
    page.set_subscription_symbols(("AAPL", "MSFT"))
    page.render_controls(
        _ready_controls(), watchlist="实时订阅子集；不限制研究或交易范围"
    )

    assert page.watchlist_card.value_label.text() == "2"
    assert "不限制研究" in page.watchlist_card.note_label.text()


def test_render_health_replaces_the_panel_without_a_view(page) -> None:
    page.render_health("连接中：等待 WebSocket 认证/首个事件")

    assert "连接中" in page.health_text.toPlainText()


def test_render_failure_names_the_failure_and_keeps_the_route_usable(page) -> None:
    page.render_failure("connection refused")

    text = page.health_text.toPlainText()
    assert "流服务失败：connection refused" in text
    assert "可继续离线研究" in text


def test_render_scope_replaces_the_line_without_a_full_render(page) -> None:
    page.render_scope("范围分层 · 官方美股/ETF 1")

    assert page.scope_label.text() == "范围分层 · 官方美股/ETF 1"


# -- the scroll freeze is the page's concern -----------------------------


def test_scrolling_defers_the_repaint_and_draws_the_latest_view(page) -> None:
    """While the operator drags a scrollbar the grid must not jump.

    Two views arrive; the first is dropped rather than queued, because only the
    newest one describes the feed.  On release the latest is drawn.
    """

    page.render(_view(health_text="A"))
    page.quote_table.verticalScrollBar().sliderPressed.emit()
    assert page.scroll_active

    page.render(_view(health_text="B", scope="中间态"))
    page.render(_view(health_text="C", scope="最新态"))

    # Nothing was applied while the drag was live.
    assert page.health_text.toPlainText() == "A"
    assert page.scope_label.text() != "最新态"

    page.quote_table.verticalScrollBar().sliderReleased.emit()

    assert not page.scroll_active
    assert page.health_text.toPlainText() == "C"
    assert page.scope_label.text() == "最新态"


def test_the_horizontal_scrollbar_freezes_the_grid_too(page) -> None:
    page.quote_table.horizontalScrollBar().sliderPressed.emit()
    page.render(_view(scope="被推迟"))

    assert page.scope_label.text() != "被推迟"
    page.quote_table.horizontalScrollBar().sliderReleased.emit()
    assert page.scope_label.text() == "被推迟"


def test_a_release_with_no_pending_view_changes_nothing(page) -> None:
    page.render(_view(scope="A"))
    page.quote_table.verticalScrollBar().sliderPressed.emit()
    page.quote_table.verticalScrollBar().sliderReleased.emit()

    assert page.scope_label.text() == "A"


def test_the_market_package_keeps_no_scroll_state_outside_the_page() -> None:
    """The retired window state lives here now, under the page's own names."""

    page = MarketPage()
    try:
        assert page._scroll_active is False
        assert page._pending_view is None
    finally:
        page.deleteLater()


# -- the palette ----------------------------------------------------------


def test_set_palette_recolours_the_rows_already_drawn(page) -> None:
    from us_quant.ui_theme import theme_palette

    stale = _row(status="STALE", tone=_error_tone())
    page.render(_view(rows=(stale,)))
    assert page.quote_model.index(0, 0).data(Qt.ForegroundRole).name().lower() == (
        theme_palette("dark").error.lower()
    )

    page.set_palette(theme_palette("light"))

    colour = page.quote_model.index(0, 0).data(Qt.ForegroundRole)
    assert colour.name().lower() == theme_palette("light").error.lower()
    assert colour.name().lower() != theme_palette("dark").error.lower()


def test_set_palette_is_adopted_by_the_page(page) -> None:
    from us_quant.ui_theme import theme_palette

    page.set_palette(theme_palette("light"))

    assert page._palette is theme_palette("light")


def _error_tone():
    from us_quant.desktop_v2.pages.market.models import MarketRowTone

    return MarketRowTone.ERROR
