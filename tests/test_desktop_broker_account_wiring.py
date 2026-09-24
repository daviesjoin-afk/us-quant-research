"""MainWindow broker-account wiring tests.

These pin the Desktop half of the account migration, now mediated by
``AccountOrchestrator`` (v2O-B):

* the ``account`` route is the native ``AccountPage``, and the legacy
  ``_account_tab`` / ``_populate_account_view`` builders are gone;
* the refresh button reaches ``AccountOrchestrator.request_refresh``, which
  delegates to ``BrokerAccountApplication`` and then appends to the ledger,
  renders the page and publishes the portfolio;
* **there is no second account truth.**  ``window`` has no
  ``account_portfolio``; ``AccountOrchestrator.portfolio`` is a read-only
  delegation to ``BrokerAccountApplication.portfolio`` and the two are the
  same object;
* **an account refresh never writes the market badge.**  Market readiness is
  Market Data v2's alone, and the account chain has no opinion about it.
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


class _FakeAdapter:
    """The one call the application makes on an adapter: a portfolio read."""

    def refresh(self, *, timeout_seconds: float = 20):
        return _portfolio()


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


def test_the_refresh_signal_reaches_the_orchestrator(window) -> None:
    """The page asks; the orchestrator performs the read.

    The window's own connection is left in place: the test replaces the
    orchestrator's method, not the signal wiring, so a window that had wired
    the button somewhere else would fail here.
    """

    calls: list[int] = []
    original = window.account_orchestrator.request_refresh
    window.account_orchestrator.request_refresh = lambda: calls.append(1)
    try:
        window.account_page.refresh_button.click()
    finally:
        window.account_orchestrator.request_refresh = original

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
    # The retired window-level account handlers are gone too.
    assert "_refresh_account_snapshot" not in methods
    assert "_account_snapshot_finished" not in methods
    assert "_refresh_account_surfaces" not in methods
    assert "_paper_simulation_capital" not in methods


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
    """The orchestrator must not build an IBKR client itself."""

    calls: list[float] = []

    def fake_refresh(*, timeout_seconds: float = 20):
        calls.append(timeout_seconds)
        return _portfolio()

    monkeypatch.setattr(window.broker_account, "refresh", fake_refresh)
    started: list = []
    monkeypatch.setattr(
        window.account_orchestrator,
        "_submit_task",
        lambda task, **kwargs: started.append((task, kwargs)),
    )

    window.account_orchestrator.request_refresh()

    assert len(started) == 1
    task, kwargs = started[0]
    assert kwargs["resource_group"] == "broker"

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
        window.account_orchestrator,
        "_submit_task",
        lambda task, **kwargs: captured.append((task, kwargs)),
    )
    window.account_orchestrator.request_refresh()
    captured[0][0](lambda _message: None)

    assert set(seen) == {"timeout"}


def test_the_refresh_never_touches_the_market_data_application(
    window, monkeypatch
) -> None:
    """Spec 33: the account read requests no market data and opens no socket.

    The account path used to take a symbol list and run a readiness check on
    the same socket.  Whether quotes are real-time is Market Data v2's answer,
    so this asserts the *market* side is untouched while the account read runs:
    a start request, a snapshot read or a credential lookup from this path
    would all be the coupling the separation removed.
    """

    touched: list[str] = []

    class _Tripwire:
        def __getattr__(self, name: str):
            touched.append(name)
            raise AssertionError(f"account refresh touched market data: {name}")

    monkeypatch.setattr(window, "market_data", _Tripwire())

    captured: list = []
    monkeypatch.setattr(
        window.account_orchestrator,
        "_submit_task",
        lambda task, **kwargs: captured.append((task, kwargs)),
    )
    # The real application refresh, with a fake adapter so no socket opens.
    monkeypatch.setattr(
        window.broker_account,
        "_adapter_factory",
        lambda: _FakeAdapter(),
    )
    window.account_orchestrator.request_refresh()
    result = captured[0][0](lambda _message: None)

    assert touched == []
    assert result.account.account_alias == "DU***67"

# -- single truth ------------------------------------------------------


def test_the_window_has_no_account_portfolio(window) -> None:
    """``account_portfolio`` is deleted, with no compatibility property."""

    assert not hasattr(window, "account_portfolio")
    names = {
        node.name
        for node in ast.walk(ast.parse(_DESKTOP_PATH.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef)
    }
    assert "account_portfolio" not in names


def test_the_orchestrator_delegates_to_the_application_truth(
    window, monkeypatch
) -> None:
    """``orchestrator.portfolio is application.portfolio``, not a copy."""

    portfolio = _portfolio()
    monkeypatch.setattr(window.broker_account, "_portfolio", portfolio)

    assert window.account_orchestrator.portfolio is portfolio
    assert window.account_orchestrator.portfolio is window.broker_account.portfolio


def test_a_successful_refresh_renders_the_page_and_appends_once(
    window, monkeypatch
) -> None:
    appended: list = []
    monkeypatch.setattr(
        window.account_ledger, "append", lambda account: appended.append(account)
    )
    monkeypatch.setattr(
        window.runtime_events_orchestrator, "record", lambda **_: None
    )
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(
        window.targeted_session_orchestrator, "refresh_preflight", lambda: None
    )
    monkeypatch.setattr(window, "_render_auto_quant_snapshot", lambda: None)
    monkeypatch.setattr(window, "_publish_dashboard_view", lambda: None)

    portfolio = _portfolio()
    window.broker_account._portfolio = portfolio
    window.account_orchestrator._refresh_succeeded(portfolio)

    assert appended == [portfolio.account]
    assert window.account_page.portfolio is portfolio
    assert window.account_orchestrator.portfolio is portfolio


def test_a_successful_refresh_sets_the_account_badge(window, monkeypatch) -> None:
    monkeypatch.setattr(
        window.runtime_events_orchestrator, "record", lambda **_: None
    )
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(
        window.targeted_session_orchestrator, "refresh_preflight", lambda: None
    )
    monkeypatch.setattr(window, "_render_auto_quant_snapshot", lambda: None)
    monkeypatch.setattr(window, "_publish_dashboard_view", lambda: None)

    window.account_orchestrator._refresh_succeeded(_portfolio())

    assert "DU***67" in window.account_badge.text()
    assert window.handshake_badge.text() == "协议 · 已握手"
    assert window.handshake_badge.toolTip() == "最近一次账户刷新握手成功"


def test_a_successful_refresh_fans_out_to_the_preflights(
    window, monkeypatch
) -> None:
    monkeypatch.setattr(
        window.runtime_events_orchestrator, "record", lambda **_: None
    )
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_publish_dashboard_view", lambda: None)
    monkeypatch.setattr(window, "_render_auto_quant_snapshot", lambda: None)

    calls: list[str] = []
    monkeypatch.setattr(
        window, "_refresh_auto_quant_preflight", lambda: calls.append("auto")
    )
    monkeypatch.setattr(
        window.targeted_session_orchestrator,
        "refresh_preflight",
        lambda: calls.append("target"),
    )

    window.account_orchestrator._refresh_succeeded(_portfolio())

    assert set(calls) == {"auto", "target"}


def test_a_wrong_result_type_is_refused(window, monkeypatch) -> None:
    monkeypatch.setattr(
        window.runtime_events_orchestrator, "record", lambda **_: None
    )
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(
        window.targeted_session_orchestrator, "refresh_preflight", lambda: None
    )
    monkeypatch.setattr(window, "_render_auto_quant_snapshot", lambda: None)
    monkeypatch.setattr(window, "_publish_dashboard_view", lambda: None)

    with pytest.raises(TypeError):
        window.account_orchestrator._refresh_succeeded(object())


# -- the account chain never writes the market badge -------------------


def test_a_successful_refresh_does_not_touch_the_market_badge(
    window, monkeypatch
) -> None:
    """The separation, asserted on observable state."""

    monkeypatch.setattr(
        window.runtime_events_orchestrator, "record", lambda **_: None
    )
    monkeypatch.setattr(window, "_log", lambda _message: None)
    monkeypatch.setattr(window, "_refresh_auto_quant_preflight", lambda: None)
    monkeypatch.setattr(
        window.targeted_session_orchestrator, "refresh_preflight", lambda: None
    )
    monkeypatch.setattr(window, "_render_auto_quant_snapshot", lambda: None)
    monkeypatch.setattr(window, "_publish_dashboard_view", lambda: None)

    before_text = window.market_badge.text()
    before_state = window.market_badge.property("state")

    window.account_orchestrator._refresh_succeeded(_portfolio())

    assert window.market_badge.text() == before_text
    assert window.market_badge.property("state") == before_state


def test_the_account_shell_health_handler_never_mentions_the_market_badge() -> None:
    """Belt and braces on the source: the account shell-health bridge only
    paints the handshake and account badges, never the market badge."""

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
        and node.name == "_render_account_shell_health"
    )
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


def test_the_account_path_has_no_market_readiness_helper() -> None:
    """``intraday_market_data_reasons`` is deleted, not merely unused."""

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
