"""MainWindow broker-account wiring tests.

These pin the Desktop half of the migration:

* the ``account`` route is the native ``AccountPage``, and the legacy
  ``_account_tab`` / ``_populate_account_view`` builders are gone;
* an account refresh delegates to ``BrokerAccountApplication``, stores the
  domain ``BrokerAccountPortfolio``, appends to the ledger and refreshes the
  preflights;
* **an account refresh never writes the market badge.**  That is the whole
  point of separating the two chains: the v1 code derived "行情 · 实时 Type 1"
  from the account snapshot's own quote checks, so an account refresh could
  report on market readiness it did not own.  Market Data v2 owns that now.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from us_quant.desktop import MainWindow  # noqa: E402
from us_quant.desktop_v2.pages.account import AccountPage  # noqa: E402
from us_quant.paths import STATE_ROOT_ENV  # noqa: E402
from us_quant.trading.application.accounts import (  # noqa: E402
    BrokerAccountApplication,
)
from us_quant.trading.domain.account import (  # noqa: E402
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
)
from us_quant.trading.domain.common import Environment  # noqa: E402

from datetime import datetime, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402


_APP = None
_DESKTOP_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "desktop.py"
)
NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _window(monkeypatch, tmp_path) -> MainWindow:
    _qapp()
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    window = MainWindow()
    _APP.processEvents()
    return window


def _portfolio() -> BrokerAccountPortfolio:
    return BrokerAccountPortfolio(
        account=BrokerAccountSnapshot(
            environment=Environment.PAPER,
            account_alias="DU***67",
            net_liquidation=Decimal("10000"),
            cash=Decimal("5000"),
            available_funds=Decimal("4000"),
            buying_power=Decimal("8000"),
            gross_position_value=Decimal("2000"),
            excess_liquidity=Decimal("3000"),
            maintenance_margin=Decimal("1000"),
            cushion=Decimal("0.75"),
            daily_pnl=Decimal("12.5"),
            unrealized_pnl=Decimal("30.25"),
            realized_pnl=Decimal("-4"),
            observed_at=NOW,
            pnl_source="IBKR reqPnL",
        ),
        positions=(),
    )


@pytest.fixture()
def window(monkeypatch, tmp_path):
    widget = _window(monkeypatch, tmp_path)
    yield widget
    widget.deleteLater()


# -- the route is a native v2 page -------------------------------------


def test_the_account_route_is_the_native_account_page(window) -> None:
    assert isinstance(window.account_page, AccountPage)
    assert window.shell.page("account") is window.account_page


def test_the_refresh_signal_is_wired_to_the_window(window) -> None:
    """The page asks; the window performs the read.

    Asserted by driving the signal and observing the window's own refresh
    path, not by inspecting Qt's connection table.
    """

    calls: list[int] = []
    original = window._refresh_account_snapshot
    window._refresh_account_snapshot = lambda: calls.append(1)
    try:
        window.account_page.refresh_requested.disconnect()
        window.account_page.refresh_requested.connect(
            window._refresh_account_snapshot
        )
        window.account_page.refresh_button.click()
    finally:
        window._refresh_account_snapshot = original

    assert calls == [1]


def test_the_legacy_account_builder_is_gone() -> None:
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_account_tab" not in methods
    assert "_populate_account_view" not in methods
    assert "_refresh_account_surfaces" in methods


def test_the_legacy_account_view_type_is_gone_from_the_window() -> None:
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "portfolio_view",
        "PortfolioView",
        "AccountView",
        "PositionView",
        "IBKRReadOnlySnapshot",
        "build_portfolio_view",
        "collect_readonly_snapshot",
        "intraday_market_data_reasons",
    ):
        assert forbidden not in source, forbidden


# -- the refresh delegates ---------------------------------------------


def test_the_window_owns_a_broker_account_application(window) -> None:
    assert isinstance(window.broker_account, BrokerAccountApplication)


def test_the_refresh_delegates_to_the_account_application(
    window, monkeypatch
) -> None:
    """The window must not build an IBKR client itself."""

    calls: list[float] = []

    def fake_refresh(*, timeout_seconds: float = 20):
        calls.append(timeout_seconds)
        return _portfolio()

    monkeypatch.setattr(window.broker_account, "refresh", fake_refresh)
    started: list = []
    monkeypatch.setattr(
        window,
        "_start_task",
        lambda task, **kwargs: started.append((task, kwargs)),
    )

    window._refresh_account_snapshot()

    assert len(started) == 1
    task, kwargs = started[0]
    assert kwargs["on_success"] == window._account_snapshot_finished
    assert kwargs["resource_group"] == "broker"

    # Running the task must reach the application, with no symbol list.
    result = task(lambda _message: None)
    assert calls == [20]
    assert result.account.account_alias == "DU***67"


def test_the_refresh_passes_no_market_data_symbols(window, monkeypatch) -> None:
    """The account chain must not request quotes any more."""

    seen: dict = {}

    def fake_refresh(*, timeout_seconds: float = 20):
        seen["timeout"] = timeout_seconds
        return _portfolio()

    monkeypatch.setattr(window.broker_account, "refresh", fake_refresh)
    captured: list = []
    monkeypatch.setattr(
        window,
        "_start_task",
        lambda task, **kwargs: captured.append((task, kwargs)),
    )
    window._refresh_account_snapshot()
    captured[0][0](lambda _message: None)

    assert set(seen) == {"timeout"}


# -- success stores domain truth ---------------------------------------


def test_a_successful_refresh_stores_the_domain_portfolio(
    window, monkeypatch
) -> None:
    monkeypatch.setattr(window, "_repolish_health_badges", lambda: None)
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_record_runtime_event", lambda **_: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(window, "_refresh_target_preflight", lambda: None)
    monkeypatch.setattr(window, "_populate_auto_quant_snapshot", lambda _s: None)

    portfolio = _portfolio()
    window._account_snapshot_finished(portfolio)

    assert window.account_portfolio is portfolio
    assert window.account_page.portfolio is portfolio


def test_a_successful_refresh_appends_to_the_ledger(
    window, monkeypatch
) -> None:
    monkeypatch.setattr(window, "_repolish_health_badges", lambda: None)
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_record_runtime_event", lambda **_: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(window, "_refresh_target_preflight", lambda: None)
    monkeypatch.setattr(window, "_populate_auto_quant_snapshot", lambda _s: None)

    appended: list = []
    monkeypatch.setattr(
        window.account_ledger, "append", lambda account: appended.append(account)
    )

    portfolio = _portfolio()
    window._account_snapshot_finished(portfolio)

    assert appended == [portfolio.account]


def test_a_successful_refresh_sets_the_account_badge(
    window, monkeypatch
) -> None:
    monkeypatch.setattr(window, "_repolish_health_badges", lambda: None)
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_record_runtime_event", lambda **_: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(window, "_refresh_target_preflight", lambda: None)
    monkeypatch.setattr(window, "_populate_auto_quant_snapshot", lambda _s: None)

    window._account_snapshot_finished(_portfolio())

    assert "DU***67" in window.account_badge.text()


def test_a_successful_refresh_refreshes_the_preflights(
    window, monkeypatch
) -> None:
    monkeypatch.setattr(window, "_repolish_health_badges", lambda: None)
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_record_runtime_event", lambda **_: None)
    monkeypatch.setattr(window, "_populate_auto_quant_snapshot", lambda _s: None)

    calls: list[str] = []
    monkeypatch.setattr(
        window, "_refresh_auto_quant_preflight", lambda: calls.append("auto")
    )
    monkeypatch.setattr(
        window, "_refresh_target_preflight", lambda: calls.append("target")
    )

    window._account_snapshot_finished(_portfolio())

    # Both preflights run; the order between them is not a contract, only
    # that the account refresh drives both.
    assert set(calls) == {"auto", "target"}


def test_a_wrong_result_type_is_refused(window) -> None:
    with pytest.raises(TypeError):
        window._account_snapshot_finished(object())


# -- the account chain never writes the market badge -------------------


def test_a_successful_refresh_does_not_touch_the_market_badge(
    window, monkeypatch
) -> None:
    """The separation, asserted on observable state.

    The v1 code set the market badge from the account snapshot's quote
    checks.  Market readiness is Market Data v2's alone.
    """

    monkeypatch.setattr(window, "_repolish_health_badges", lambda: None)
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_record_runtime_event", lambda **_: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(window, "_refresh_target_preflight", lambda: None)
    monkeypatch.setattr(window, "_populate_auto_quant_snapshot", lambda _s: None)

    before_text = window.market_badge.text()
    before_state = window.market_badge.property("state")

    window._account_snapshot_finished(_portfolio())

    assert window.market_badge.text() == before_text
    assert window.market_badge.property("state") == before_state


def test_the_account_finished_handler_never_mentions_the_market_badge() -> None:
    """Belt and braces on the source, so the state assertion cannot be the
    only guard."""

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    window = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    method = next(
        node
        for node in window.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_account_snapshot_finished"
    )
    # Strip the docstring before checking: it *explains* the removal by
    # naming the badge, which is documentation rather than a write.
    body = method.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        body = body[1:]
    code = "\n".join(ast.unparse(node) for node in body)
    assert "market_badge" not in code
    assert "intraday_market_data_reasons" not in code
    assert "行情" not in code


def test_the_account_path_has_no_market_readiness_helper() -> None:
    """``intraday_market_data_reasons`` is deleted, not merely unused.

    It used to live in the account read and answer a market-readiness
    question.  Both halves are gone: the function and its caller.
    """

    for path in (
        pathlib.Path(__file__).resolve().parents[1]
        / "src"
        / "us_quant"
        / "trading"
        / "adapters"
        / "ibkr"
        / "account.py",
        _DESKTOP_PATH,
    ):
        assert "intraday_market_data_reasons" not in path.read_text(
            encoding="utf-8"
        )
