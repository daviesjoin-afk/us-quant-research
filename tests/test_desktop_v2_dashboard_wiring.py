"""Real MainWindow wiring regressions for Dashboard v2."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

import us_quant.desktop as desktop_module
from us_quant.desktop import MainWindow
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
    BrokerPositionSnapshot,
)
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)
from us_quant.ui_theme import theme_palette


_APP = QApplication.instance() or QApplication([])
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
RETIRED_WINDOW_WIDGETS = (
    "universe_card",
    "verified_card",
    "history_card",
    "signal_card",
    "artifact_table",
    "dashboard_chart",
    "dashboard_notes",
)


@pytest.fixture()
def window():
    widget = MainWindow()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


def _account() -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        environment=Environment.PAPER,
        account_alias="DU***67",
        net_liquidation=Decimal("12345.67"),
        cash=Decimal("5000"),
        available_funds=Decimal("4000"),
        buying_power=Decimal("8000"),
        gross_position_value=Decimal("1234"),
        excess_liquidity=Decimal("3000"),
        maintenance_margin=Decimal("1000"),
        cushion=Decimal("0.75"),
        daily_pnl=Decimal("-12.5"),
        unrealized_pnl=Decimal("3"),
        realized_pnl=Decimal("-1"),
        observed_at=NOW,
        pnl_source="IBKR reqPnL",
    )


def _position() -> BrokerPositionSnapshot:
    return BrokerPositionSnapshot(
        account_alias="DU***67",
        con_id=1,
        symbol="AAPL",
        local_symbol="AAPL",
        security_type="STK",
        exchange="SMART",
        currency="USD",
        quantity=Decimal("2"),
        average_cost=Decimal("36"),
        market_value=Decimal("72"),
        daily_pnl=Decimal("1.5"),
        unrealized_pnl=Decimal("0.5"),
        realized_pnl=Decimal("0"),
        observed_at=NOW,
    )


def _portfolio() -> BrokerAccountPortfolio:
    return BrokerAccountPortfolio(_account(), (_position(),))


def _quote(**overrides) -> MarketQuote:
    values = dict(
        symbol="SPY",
        bid=Decimal("100"),
        ask=Decimal("100.1"),
        last=Decimal("100.05"),
        close=Decimal("99"),
        bid_size=None,
        ask_size=None,
        mode=MarketDataMode.REALTIME,
        updated_at=NOW,
        age_seconds=0.2,
        stale=False,
        stale_reason=None,
        generation=1,
        source_id="alpaca_iex",
        source_label="Alpaca",
        coverage="IEX",
    )
    values.update(overrides)
    return MarketQuote(**values)  # type: ignore[arg-type]


def _snapshot(*, quotes=(), message="") -> MarketSnapshot:
    return MarketSnapshot(
        generation=1,
        connected=True,
        ready=True,
        reconnect_attempt=0,
        quotes=tuple(quotes),
        error_code=None,
        message=message,
        observed_at=NOW,
        source_id="alpaca_iex",
        source_label="Alpaca",
        coverage="IEX",
    )


def test_dashboard_route_is_native_and_owns_no_legacy_widget_alias(window) -> None:
    assert window.shell.page("dashboard") is window.dashboard_page
    for name in RETIRED_WINDOW_WIDGETS:
        assert not hasattr(window, name), name


def test_account_publish_is_visible_on_the_real_dashboard_cards(window) -> None:
    window.account_portfolio = None
    window._publish_dashboard_view()
    assert window.dashboard_page._net_liquidation_card.value_label.text() == "未读取"
    assert window.dashboard_page._daily_pnl_card.value_label.text() == "不可用"
    assert window.dashboard_page._positions_card.value_label.text() == "未读取"

    window.account_portfolio = _portfolio()
    window._refresh_account_surfaces()
    assert window.dashboard_page._net_liquidation_card.value_label.text() == "$12,345.67"
    assert window.dashboard_page._daily_pnl_card.value_label.text() == "$-12.50"
    assert window.dashboard_page._positions_card.value_label.text() == "1"


def test_market_snapshot_states_are_visible_on_the_real_dashboard(window) -> None:
    window.market_orchestrator._snapshot = None
    window._publish_dashboard_view()
    card = window.dashboard_page._intraday_market_card
    assert (card.value_label.text(), card.note_label.text()) == (
        "不可用",
        "尚未启动流行情",
    )

    window.market_orchestrator._snapshot = _snapshot(quotes=(_quote(),))
    window._publish_dashboard_view()
    assert (card.value_label.text(), card.note_label.text()) == (
        "可用",
        "Alpaca IEX 单交易所实时",
    )

    window.market_orchestrator._snapshot = _snapshot(message="Type 1 尚未就绪")
    window._publish_dashboard_view()
    assert (card.value_label.text(), card.note_label.text()) == (
        "不可用",
        "Type 1 尚未就绪",
    )


def test_gateway_button_reaches_the_real_main_window_handler(
    window, monkeypatch
) -> None:
    calls: list[object] = []

    def probe(config):
        calls.append(config)
        return SimpleNamespace(reachable=True)

    monkeypatch.setattr(desktop_module, "probe_ibkr_socket", probe)
    window.dashboard_page._gateway_button.click()
    assert calls == [window.config.ibkr]
    assert window.gateway_badge.text() == "端口 · 4002 可达"


def test_stream_receive_updates_dashboard_through_the_window(window, monkeypatch) -> None:
    """A market snapshot must reach the dashboard card through the fan-out."""

    monkeypatch.setattr(window, "_record_minute_snapshot", lambda snapshot: None)
    monkeypatch.setattr(window, "_populate_auto_quant_candidates", lambda: None)
    monkeypatch.setattr(window, "_refresh_target_preflight", lambda: None)
    window.market_orchestrator._on_snapshot(_snapshot(quotes=(_quote(),)))
    assert window.dashboard_page._intraday_market_card.value_label.text() == "可用"


def test_stream_stop_updates_dashboard_through_the_window(window) -> None:
    """An invalidated feed must repaint the card as stopped."""

    window.market_orchestrator._snapshot = None
    window.market_orchestrator._invalidate("行情流正在停止")
    card = window.dashboard_page._intraday_market_card
    assert (card.value_label.text(), card.note_label.text()) == (
        "不可用",
        "行情流正在停止",
    )


def test_theme_switch_repaints_dashboard_without_business_intent(window) -> None:
    window.artifact_catalog = SimpleNamespace(
        artifacts=(
            SimpleNamespace(
                artifact_type="old",
                status="legacy_invalidated",
                data_as_of=None,
                generated_at=None,
                source="local",
                run_id="1234567890abcdef",
                limitations=("legacy",),
            ),
        )
    )
    fired: list[int] = []
    window.dashboard_page.gateway_probe_requested.connect(
        lambda: fired.append(1)
    )
    window._publish_dashboard_view()
    window._apply_theme("light")
    assert window.dashboard_page._palette.name == "light"
    assert fired == []
    assert (
        window.dashboard_page._artifact_table.item(0, 0)
        .foreground()
        .color()
        .name()
        == QColor(theme_palette("light").warning).name()
    )
