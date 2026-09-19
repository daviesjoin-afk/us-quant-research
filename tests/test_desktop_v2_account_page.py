"""Desktop UI v2 account page tests.

The page is the first genuinely native v2 route, so these tests pin both
halves of that claim: it *renders* the domain portfolio correctly, and it
stays a pure renderer -- no application service, no adapter, no ``ibapi``,
and no market-data state.

The empty-state distinction is asserted explicitly: ``portfolio=None`` means
"never read" and must not look like "the account holds nothing", and a
broker value that was not reported renders as ``—`` rather than ``$0``.
"""

from __future__ import annotations

import ast
import pathlib
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from us_quant.account_ledger import EquityPoint  # noqa: E402
from us_quant.desktop_v2.pages.account import (  # noqa: E402
    LEDGER_COLUMNS,
    MISSING,
    POSITION_COLUMNS,
    AccountPage,
)
from us_quant.trading.domain.account import (  # noqa: E402
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
    BrokerPositionSnapshot,
)
from us_quant.trading.domain.common import Environment  # noqa: E402


_APP = None
_PAGE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "desktop_v2"
    / "pages"
    / "account.py"
)
NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


@pytest.fixture()
def page():
    _qapp()
    widget = AccountPage()
    yield widget
    widget.deleteLater()


def _account(**overrides) -> BrokerAccountSnapshot:
    values = {
        "environment": Environment.PAPER,
        "account_alias": "DU***67",
        "net_liquidation": Decimal("10000"),
        "cash": Decimal("5000"),
        "available_funds": Decimal("4000"),
        "buying_power": Decimal("8000"),
        "gross_position_value": Decimal("2000"),
        "excess_liquidity": Decimal("3000"),
        "maintenance_margin": Decimal("1000"),
        "cushion": Decimal("0.75"),
        "daily_pnl": Decimal("12.5"),
        "unrealized_pnl": Decimal("30.25"),
        "realized_pnl": Decimal("-4"),
        "observed_at": NOW,
        "pnl_source": "IBKR reqPnL",
    }
    values.update(overrides)
    return BrokerAccountSnapshot(**values)


def _position(**overrides) -> BrokerPositionSnapshot:
    values = {
        "account_alias": "DU***67",
        "con_id": 1,
        "symbol": "AAPL",
        "local_symbol": "AAPL",
        "security_type": "STK",
        "exchange": "SMART",
        "currency": "USD",
        "quantity": Decimal("2"),
        "average_cost": Decimal("36"),
        "market_value": Decimal("72"),
        "daily_pnl": Decimal("1.5"),
        "unrealized_pnl": Decimal("0.5"),
        "realized_pnl": Decimal("0"),
        "observed_at": NOW,
    }
    values.update(overrides)
    return BrokerPositionSnapshot(**values)


def _portfolio(*positions, account=None) -> BrokerAccountPortfolio:
    return BrokerAccountPortfolio(
        account=account or _account(),
        positions=positions,
    )


def _cell(page: AccountPage, row: int, column: int) -> str:
    item = page.positions_table.item(row, column)
    return item.text() if item is not None else ""


# -- refresh signal ----------------------------------------------------


def test_the_refresh_button_emits_the_signal(page) -> None:
    fired: list[int] = []
    page.refresh_requested.connect(lambda: fired.append(1))

    page.refresh_button.click()

    assert fired == [1]


def test_the_page_owns_no_refresh_logic(page) -> None:
    """It asks; it does not read.  A page that fetched would be an
    orchestrator again."""

    assert not hasattr(page, "refresh")
    assert not hasattr(page, "broker_account")
    assert not hasattr(page, "_refresh_account_snapshot")


# -- empty state -------------------------------------------------------


def test_the_initial_state_is_unread_not_empty(page) -> None:
    assert page.portfolio is None
    assert page.positions_table.rowCount() == 0
    assert page.net_liquidation_card.value_label.text() == MISSING


def test_rendering_none_keeps_the_unread_placeholder(page) -> None:
    page.render(_portfolio(_position()))
    page.render(None)

    assert page.portfolio is None
    assert page.net_liquidation_card.value_label.text() == MISSING
    assert "尚未读取持仓" in page.positions_hint_label.text()


def test_an_empty_portfolio_says_the_account_holds_nothing(page) -> None:
    """Distinct from "never read": an empty account is a real answer."""

    page.render(_portfolio())

    assert page.portfolio is not None
    assert page.positions_table.rowCount() == 0
    assert page.positions_hint_label.text() == "当前账户无持仓"


def test_an_error_is_shown_on_the_status_label(page) -> None:
    page.render(None, error="gateway down")

    assert "gateway down" in page.status_label.text()


# -- account metrics ---------------------------------------------------


def test_account_metrics_render_into_the_cards(page) -> None:
    page.render(_portfolio())

    assert page.net_liquidation_card.value_label.text() == "$10,000.00"
    assert page.cash_card.value_label.text() == "$5,000.00"
    assert page.daily_pnl_card.value_label.text() == "+$12.50"
    assert page.unrealized_pnl_card.value_label.text() == "+$30.25"


def test_the_net_liquidation_caption_names_the_alias(page) -> None:
    page.render(_portfolio())

    caption = page.net_liquidation_card.note_label.text()
    assert "DU***67" in caption
    assert "Paper" in caption


def test_the_status_label_shows_the_alias_and_observation_time(page) -> None:
    page.render(_portfolio())

    text = page.status_label.text()
    assert "DU***67" in text
    assert "PAPER" in text
    assert NOW.isoformat() in text


def test_the_detail_label_shows_buying_power_and_margin(page) -> None:
    page.render(_portfolio())

    text = page.detail_label.text()
    assert "$8,000.00" in text
    assert "$2,000.00" in text
    assert "$1,000.00" in text


def test_the_pnl_caption_keeps_the_broker_source(page) -> None:
    page.render(_portfolio())

    assert "IBKR reqPnL" in page.daily_pnl_card.note_label.text()


def test_a_missing_account_metric_renders_as_missing(page) -> None:
    """Not ``$0``: an unreported balance is not a zero balance."""

    page.render(_portfolio(account=_account(cash=None)))

    assert page.cash_card.value_label.text() == MISSING


# -- positions ---------------------------------------------------------


def test_the_position_columns_are_the_new_broker_truth_set(page) -> None:
    """The old 13-column design mixed account and market-data state."""

    headers = [
        page.positions_table.horizontalHeaderItem(index).text()
        for index in range(page.positions_table.columnCount())
    ]
    assert tuple(headers) == POSITION_COLUMNS
    for removed in ("行情类型", "Mark来源", "状态"):
        assert removed not in headers


def test_a_position_row_renders_the_broker_fields(page) -> None:
    page.render(_portfolio(_position()))

    assert page.positions_table.rowCount() == 1
    assert _cell(page, 0, 0) == "AAPL"
    assert _cell(page, 0, 1) == "2"
    assert _cell(page, 0, 2) == "$36.00"
    assert _cell(page, 0, 3) == "$36.00"  # broker mark = 72 / 2
    assert _cell(page, 0, 4) == "$72.00"
    assert _cell(page, 0, 9) == "USD"
    assert _cell(page, 0, 10) == "DU***67"


def test_a_fractional_quantity_is_displayed_honestly(page) -> None:
    """Whole shares are an execution policy, not a display policy."""

    page.render(
        _portfolio(
            _position(quantity=Decimal("0.5"), market_value=Decimal("18"))
        )
    )

    assert _cell(page, 0, 1) == "0.5"
    assert _cell(page, 0, 3) == "$36.00"


def test_the_broker_mark_is_derived_from_market_value(page) -> None:
    """Not from a quote feed: the account path has no market data."""

    page.render(
        _portfolio(
            _position(quantity=Decimal("4"), market_value=Decimal("100"))
        )
    )

    assert _cell(page, 0, 3) == "$25.00"


def test_a_missing_market_value_renders_as_missing(page) -> None:
    page.render(_portfolio(_position(market_value=None)))

    assert _cell(page, 0, 3) == MISSING
    assert _cell(page, 0, 4) == MISSING
    assert _cell(page, 0, 5) == MISSING
    assert "$0" not in _cell(page, 0, 4)


def test_a_missing_position_pnl_renders_as_missing(page) -> None:
    page.render(
        _portfolio(
            _position(daily_pnl=None, unrealized_pnl=None, realized_pnl=None)
        )
    )

    assert _cell(page, 0, 6) == MISSING
    assert _cell(page, 0, 7) == MISSING
    assert _cell(page, 0, 8) == MISSING


def test_a_missing_market_value_is_coloured(page) -> None:
    """An absent number must not read as a zero at a glance."""

    page.render(_portfolio(_position(market_value=None)))

    item = page.positions_table.item(0, 4)
    assert item.foreground().color().isValid()


def test_several_positions_render_one_row_each(page) -> None:
    page.render(
        _portfolio(
            _position(symbol="AAPL"),
            _position(con_id=2, symbol="MSFT"),
            _position(con_id=3, symbol="NVDA"),
        )
    )

    assert page.positions_table.rowCount() == 3
    assert page.positions_hint_label.text() == "共 3 个券商持仓"


# -- risk exposure is a UI projection ----------------------------------


def test_exposure_uses_the_configured_multiplier(page) -> None:
    page.render(
        _portfolio(_position(symbol="AAPL", market_value=Decimal("100"))),
        exposure_multipliers={"AAPL": Decimal("2")},
    )

    assert _cell(page, 0, 5) == "$200.00"


def test_exposure_defaults_to_one(page) -> None:
    page.render(
        _portfolio(_position(symbol="AAPL", market_value=Decimal("100")))
    )

    assert _cell(page, 0, 5) == "$100.00"


def test_exposure_is_missing_without_a_market_value(page) -> None:
    page.render(
        _portfolio(_position(market_value=None)),
        exposure_multipliers={"AAPL": Decimal("3")},
    )

    assert _cell(page, 0, 5) == MISSING


def test_the_projection_does_not_mutate_the_domain(page) -> None:
    """The multiplier is presentation; it must not be written back."""

    position = _position(market_value=Decimal("100"))
    page.render(
        _portfolio(position),
        exposure_multipliers={"AAPL": Decimal("5")},
    )

    assert position.market_value == Decimal("100")
    assert not hasattr(position, "risk_exposure")
    assert not hasattr(position, "risk_multiplier")


# -- ledger ------------------------------------------------------------


def test_the_ledger_columns_are_unchanged(page) -> None:
    headers = [
        page.ledger_table.horizontalHeaderItem(index).text()
        for index in range(page.ledger_table.columnCount())
    ]
    assert tuple(headers) == LEDGER_COLUMNS


def test_ledger_points_render(page) -> None:
    points = (
        EquityPoint(
            observed_at="2026-07-26T12:00:00+00:00",
            environment="paper",
            account_alias="DU***67",
            net_liquidation=Decimal("10000"),
            cash=Decimal("5000"),
            daily_pnl=Decimal("12.5"),
            unrealized_pnl=Decimal("30.25"),
            realized_pnl=Decimal("-4"),
        ),
    )

    page.render(_portfolio(), ledger_points=points)

    assert page.ledger_table.rowCount() == 1
    assert page.ledger_table.item(0, 2).text() == "DU***67"
    assert page.ledger_table.item(0, 3).text() == "$10,000.00"


def test_the_ledger_renders_even_without_a_portfolio(page) -> None:
    points = (
        EquityPoint(
            observed_at="2026-07-26T12:00:00+00:00",
            environment="paper",
            account_alias="DU***67",
            net_liquidation=Decimal("10000"),
            cash=None,
            daily_pnl=None,
            unrealized_pnl=None,
            realized_pnl=None,
        ),
    )

    page.render(None, ledger_points=points)

    assert page.ledger_table.rowCount() == 1


# -- the notice strip --------------------------------------------------


def test_the_notice_strip_is_hidden_until_set(page) -> None:
    assert page.notice_label.isVisible() is False


def test_the_notice_strip_shows_caller_supplied_text(page) -> None:
    """The page does not interpret it, which keeps strategy state out."""

    page.set_notice("策略证据门：硬阻断。")

    assert "策略证据门" in page.notice_label.text()


def test_the_notice_strip_can_be_cleared(page) -> None:
    page.set_notice("something")
    page.set_notice("")

    assert page.notice_label.text() == ""


# -- the page is a pure renderer ---------------------------------------


def _imported(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_page_imports_no_application_adapter_or_transport() -> None:
    imported = _imported(_PAGE_PATH)

    for forbidden in (
        "ibapi",
        "us_quant.trading.adapters",
        "us_quant.trading.application",
        "us_quant.trading.composition",
        "us_quant.ibkr",
        "us_quant.paper_trading_service",
        "us_quant.paper_session",
        "us_quant.paper_workflow",
        "us_quant.ibkr_paper_orders",
        "us_quant.ibkr_paper_gateway",
        "us_quant.auto_quant",
        "us_quant.risk",
        "us_quant.sqlite_support",
        "us_quant.portfolio_view",
        "us_quant.ibkr_readonly",
    ):
        assert forbidden not in imported, forbidden
        assert not any(
            name.startswith(f"{forbidden}.") for name in imported
        ), forbidden


def test_the_page_imports_the_domain_and_nothing_business_shaped() -> None:
    imported = {
        name for name in _imported(_PAGE_PATH) if name.startswith("us_quant")
    }

    assert imported == {
        "us_quant.account_ledger",
        "us_quant.desktop_widgets",
        "us_quant.trading.domain.account",
    }


def test_the_page_shows_no_market_data_state() -> None:
    """No quote type, no mark source, no stale flag: Market Data v2 owns
    all of it, and the account path never requests quotes."""

    source = _PAGE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    for forbidden in (
        "market_data_type",
        "mark_source",
        "market_data_mode",
        "realtime_ready",
    ):
        assert forbidden not in attributes

    for header in POSITION_COLUMNS:
        assert header not in {"行情类型", "Mark来源", "状态", "STALE", "FRESH"}
