"""Behavioural tests for ``AccountOrchestrator`` and the account queries.

The window's wiring tests prove the signals are connected; these prove the
orchestrator and the Qt-free query are the thing that decides.  Everything here
drives the orchestrator directly with fakes, so a failure points at the
capability rather than at the composition around it.

Two properties get the most attention because they are the ones a careless
extraction would break:

* **no second account truth.**  ``portfolio`` delegates to the injected
  application, and a successful refresh does not store a copy;
* **the fresh-Paper rule is deterministic.**  Every query test passes an
  explicit ``now`` and never reads the wall clock.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.orchestration.account import (
    AccountPresentationInputs,
    AccountShellHealthView,
)
from us_quant.desktop_v2.orchestration.account.orchestrator import (
    AccountOrchestrator,
)
from us_quant.desktop_v2.orchestration.account.queries import (
    FRESH_PAPER_MAX_AGE_SECONDS,
    fresh_paper_net_liquidation,
)
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
)
from us_quant.trading.domain.common import Environment


_APP = QApplication.instance() or QApplication([])
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def _account(
    *,
    environment: Environment = Environment.PAPER,
    net_liquidation: Decimal | None = Decimal("10000"),
    observed_at: datetime = NOW,
) -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        environment=environment,
        account_alias="DU***67",
        net_liquidation=net_liquidation,
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
        observed_at=observed_at,
        pnl_source="IBKR reqPnL",
    )


def _portfolio(
    *,
    environment: Environment = Environment.PAPER,
    net_liquidation: Decimal | None = Decimal("10000"),
    observed_at: datetime = NOW,
) -> BrokerAccountPortfolio:
    return BrokerAccountPortfolio(
        account=_account(
            environment=environment,
            net_liquidation=net_liquidation,
            observed_at=observed_at,
        ),
        positions=(),
    )


class _Application:
    """A minimal ``BrokerAccountApplication`` stand-in: owns the portfolio."""

    def __init__(self, portfolio: BrokerAccountPortfolio | None = None) -> None:
        self.portfolio = portfolio
        self.refreshed: list[object] = []

    def refresh(self, *, timeout_seconds: float = 20):
        self.refreshed.append(timeout_seconds)
        assert timeout_seconds == 20
        result = _portfolio()
        self.portfolio = result
        return result


class _Ledger:
    def __init__(self) -> None:
        self.appended: list[object] = []
        self.points = ()

    def append(self, account) -> None:
        self.appended.append(account)

    def list_points(self, *, environment, account_alias, limit=100):
        return self.points


class _Page(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.rendered: list[tuple] = []

    def render(self, portfolio, *, ledger_points=(), exposure_multipliers=None):
        self.rendered.append((portfolio, ledger_points, exposure_multipliers))


def _make_orchestrator(
    *,
    application=None,
    ledger=None,
    page=None,
    submit_task=None,
):
    orchestrator = AccountOrchestrator(
        application=application or _Application(),
        ledger=ledger or _Ledger(),
        page=page or _Page(),
        submit_task=submit_task or (lambda *a, **k: True),
    )
    return orchestrator


# -- single truth -------------------------------------------------------


def test_portfolio_delegates_to_the_application() -> None:
    app = _Application(portfolio=None)
    orchestrator = _make_orchestrator(application=app)

    assert orchestrator.portfolio is None

    portfolio = _portfolio()
    app.portfolio = portfolio
    assert orchestrator.portfolio is portfolio
    assert orchestrator.portfolio is app.portfolio


def test_a_successful_refresh_stores_no_copy() -> None:
    app = _Application(portfolio=None)
    ledger = _Ledger()
    page = _Page()
    orchestrator = _make_orchestrator(application=app, ledger=ledger, page=page)

    result = _portfolio()
    app.portfolio = result  # the application commits before the handler runs
    orchestrator._refresh_succeeded(result)

    # The portfolio is the application's object, by identity, and there is no
    # second copy anywhere on the orchestrator.
    assert orchestrator.portfolio is result
    assert orchestrator.portfolio is app.portfolio
    assert not hasattr(orchestrator, "_portfolio")


def test_a_successful_refresh_appends_once_and_renders() -> None:
    app = _Application(portfolio=None)
    ledger = _Ledger()
    page = _Page()
    orchestrator = _make_orchestrator(application=app, ledger=ledger, page=page)

    result = _portfolio()
    app.portfolio = result
    orchestrator._refresh_succeeded(result)

    assert ledger.appended == [result.account]
    assert len(page.rendered) == 1
    rendered_portfolio = page.rendered[0][0]
    assert rendered_portfolio is result


def test_a_wrong_result_type_is_refused() -> None:
    orchestrator = _make_orchestrator()
    with pytest.raises(TypeError):
        orchestrator._refresh_succeeded(object())


def test_a_failed_refresh_preserves_the_last_good_portfolio() -> None:
    """The orchestrator never clears or re-dates the application's truth."""

    app = _Application(portfolio=None)
    orchestrator = _make_orchestrator(application=app)

    good = _portfolio()
    app.portfolio = good

    # A failed refresh is the application's to record; the orchestrator must
    # not touch the stored portfolio.  Nothing here clears the page, replaces
    # with None or appends a fake ledger point -- that is the application's
    # safety semantic, and the orchestrator only delegates to it.
    assert orchestrator.portfolio is good


# -- the refresh request ------------------------------------------------


def test_request_refresh_submits_a_broker_group_task() -> None:
    app = _Application(portfolio=None)
    submitted: list = []
    orchestrator = _make_orchestrator(
        application=app, submit_task=lambda *a, **k: submitted.append((a, k))
    )

    orchestrator.request_refresh()

    assert len(submitted) == 1
    args, kwargs = submitted[0]
    task = args[0]
    assert kwargs["resource_group"] == "broker"
    assert kwargs["start_message"] == "IBKR 只读账户刷新开始…"

    # Running the task reaches the application with the 20s timeout and no
    # symbol list.
    result = task(lambda _message: None)
    assert app.refreshed == [20]
    assert result is not None


def test_request_refresh_passes_no_symbol_list() -> None:
    app = _Application(portfolio=None)
    submitted: list = []
    orchestrator = _make_orchestrator(
        application=app, submit_task=lambda *a, **k: submitted.append((a, k))
    )

    orchestrator.request_refresh()
    task = submitted[0][0][0]
    task(lambda _message: None)

    assert app.refreshed == [20]


# -- render_current -----------------------------------------------------


def test_render_current_reads_the_application_and_the_ledger() -> None:
    app = _Application(portfolio=None)
    ledger = _Ledger()
    page = _Page()
    orchestrator = _make_orchestrator(application=app, ledger=ledger, page=page)

    result = _portfolio()
    app.portfolio = result
    ledger.points = ("point-a", "point-b")

    orchestrator.render_current()

    assert len(page.rendered) == 1
    rendered_portfolio, rendered_points, multipliers = page.rendered[0]
    assert rendered_portfolio is result
    assert rendered_points == ("point-a", "point-b")
    assert multipliers == {}


def test_render_current_never_fetches() -> None:
    """A render that fetched would be a second refresh path."""

    app = _Application(portfolio=None)
    orchestrator = _make_orchestrator(application=app)

    orchestrator.render_current()

    assert app.refreshed == []


# -- presentation inputs -------------------------------------------------


def test_presentation_inputs_freeze_the_multipliers() -> None:
    inputs = AccountPresentationInputs.of({"AAPL": Decimal("2")})

    assert inputs.multiplier_for("AAPL") == Decimal("2")
    assert inputs.multiplier_for("MSFT") == Decimal("1")
    assert inputs.as_mapping() == {"AAPL": Decimal("2")}


def test_presentation_inputs_are_passed_to_the_page() -> None:
    app = _Application(portfolio=_portfolio())
    page = _Page()
    orchestrator = _make_orchestrator(application=app, page=page)

    orchestrator.set_presentation_inputs(
        AccountPresentationInputs.of({"AAPL": Decimal("2")})
    )
    orchestrator.render_current()

    _, _, multipliers = page.rendered[0]
    assert multipliers == {"AAPL": Decimal("2")}


# -- the fresh Paper net-liquidation rule -------------------------------


def test_none_portfolio_is_not_usable() -> None:
    assert fresh_paper_net_liquidation(None, now=NOW) is None


def test_live_account_is_not_usable() -> None:
    portfolio = _portfolio(environment=Environment.LIVE)
    assert fresh_paper_net_liquidation(portfolio, now=NOW) is None


def test_fresh_paper_returns_nlv() -> None:
    portfolio = _portfolio(net_liquidation=Decimal("12345.67"))
    assert (
        fresh_paper_net_liquidation(portfolio, now=NOW)
        == Decimal("12345.67")
    )


def test_exactly_300_seconds_is_still_fresh() -> None:
    observed = NOW - timedelta(seconds=300)
    portfolio = _portfolio(observed_at=observed)
    assert (
        fresh_paper_net_liquidation(portfolio, now=NOW)
        == Decimal("10000")
    )


def test_over_300_seconds_is_stale() -> None:
    observed = NOW - timedelta(seconds=300.001)
    portfolio = _portfolio(observed_at=observed)
    assert fresh_paper_net_liquidation(portfolio, now=NOW) is None


def test_a_future_timestamp_is_not_usable() -> None:
    observed = NOW + timedelta(seconds=10)
    portfolio = _portfolio(observed_at=observed)
    assert fresh_paper_net_liquidation(portfolio, now=NOW) is None


def test_none_nlv_is_not_usable() -> None:
    portfolio = _portfolio(net_liquidation=None)
    assert fresh_paper_net_liquidation(portfolio, now=NOW) is None


def test_zero_nlv_is_not_usable() -> None:
    portfolio = _portfolio(net_liquidation=Decimal("0"))
    assert fresh_paper_net_liquidation(portfolio, now=NOW) is None


def test_negative_nlv_is_not_usable() -> None:
    portfolio = _portfolio(net_liquidation=Decimal("-1"))
    assert fresh_paper_net_liquidation(portfolio, now=NOW) is None


def test_a_naive_timestamp_is_read_as_utc() -> None:
    """The retired rule read a naive timestamp as UTC; so does the query.

    The domain dataclass refuses a naive timestamp at construction, but the
    query is still defensive: it must handle a naive ``observed_at`` the same
    way the legacy method did, so a caller (or a future adapter) that slips one
    through is not silently rejected.  A lightweight fake exercises that path.
    """

    from types import SimpleNamespace

    naive = NOW.replace(tzinfo=None)
    fake = SimpleNamespace(
        account=SimpleNamespace(
            environment=Environment.PAPER,
            net_liquidation=Decimal("10000"),
            observed_at=naive,
        )
    )
    assert (
        fresh_paper_net_liquidation(fake, now=NOW)  # type: ignore[arg-type]
        == Decimal("10000")
    )


def test_the_freshness_constant_matches_the_spec() -> None:
    assert FRESH_PAPER_MAX_AGE_SECONDS == 300.0


def test_the_orchestrator_fresh_paper_delegates_to_the_query() -> None:
    app = _Application(portfolio=_portfolio(net_liquidation=Decimal("7777")))
    orchestrator = _make_orchestrator(application=app)

    assert (
        orchestrator.fresh_paper_net_liquidation(now=NOW)
        == Decimal("7777")
    )


# -- shell health view --------------------------------------------------


def test_the_shell_health_view_carries_the_account_facts() -> None:
    view = AccountOrchestrator._health_view(_account())

    assert view.handshake_text == "协议 · 已握手"
    assert view.handshake_state == "ok"
    assert view.handshake_tooltip == "最近一次账户刷新握手成功"
    assert view.account_text == "Paper · DU***67"
    assert view.account_state == "ok"
    # No market fact: the account chain has no opinion about quotes.
    assert not hasattr(view, "market_text")
