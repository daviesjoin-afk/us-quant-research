"""Broker account application tests.

The application is the runtime owner of the connection config and of the
account refresh lifecycle, so these tests are about *state transitions*
rather than about IBKR: a fake adapter is injected through the factory, which
is the same seam ``trading.composition.accounts`` uses.

Four safety properties are pinned here, because each one is a way an account
snapshot could lie to the preflight:

* a failed refresh keeps the previous snapshot and its original timestamp --
  re-dating old data would disguise a stale account as a fresh one and walk
  straight through the 300-second freshness gate;
* a failed refresh still records ``last_error``, so the operator can tell;
* a concurrent refresh is refused rather than opening a second socket;
* a config change is refused while a refresh is running, and accepted when
  idle -- and the *next* refresh sees the new config.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.application.accounts import BrokerAccountApplication
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
    BrokerPositionSnapshot,
)
from us_quant.trading.domain.common import Environment
from us_quant.trading.ports.broker_account import (
    BrokerAccountActiveError,
    BrokerAccountUnavailable,
)


def _config(**overrides) -> IBKRConnectionConfig:
    values = {
        "host": "127.0.0.1",
        "port": 4002,
        "client_id": 17,
        "api_read_only": True,
        "paper_order_submission_enabled": False,
        "connection_timeout_seconds": 2.0,
    }
    values.update(overrides)
    return IBKRConnectionConfig(**values)


def _portfolio(
    *,
    alias: str = "DU***67",
    observed_at: datetime | None = None,
    net_liquidation: str = "10000",
) -> BrokerAccountPortfolio:
    observed = observed_at or datetime.now(timezone.utc)
    return BrokerAccountPortfolio(
        account=BrokerAccountSnapshot(
            environment=Environment.PAPER,
            account_alias=alias,
            net_liquidation=Decimal(net_liquidation),
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
            observed_at=observed,
            pnl_source="IBKR reqPnL",
        ),
        positions=(
            BrokerPositionSnapshot(
                account_alias=alias,
                con_id=1,
                symbol="AAPL",
                local_symbol="AAPL",
                security_type="STK",
                exchange="SMART",
                currency="USD",
                quantity=Decimal("2"),
                average_cost=Decimal("36"),
                market_value=Decimal("72"),
                daily_pnl=None,
                unrealized_pnl=None,
                realized_pnl=None,
                observed_at=observed,
            ),
        ),
    )


class _FakeAdapter:
    """An adapter that returns a scripted result or raises a scripted error."""

    def __init__(
        self,
        *,
        portfolio: BrokerAccountPortfolio | None = None,
        error: Exception | None = None,
        log: list | None = None,
    ) -> None:
        self._portfolio = portfolio
        self._error = error
        self._log = log if log is not None else []
        self.calls: list[float] = []

    def refresh(self, *, timeout_seconds: float) -> BrokerAccountPortfolio:
        self._log.append("refresh")
        self.calls.append(timeout_seconds)
        if self._error is not None:
            raise self._error
        assert self._portfolio is not None
        return self._portfolio


class _Factory:
    """Records the config in force when each adapter was built."""

    def __init__(
        self,
        application: BrokerAccountApplication,
        *,
        portfolio: BrokerAccountPortfolio | None = None,
        error: Exception | None = None,
    ) -> None:
        self._application = application
        self._portfolio = portfolio
        self._error = error
        self.configs: list[IBKRConnectionConfig] = []
        self.adapters: list[_FakeAdapter] = []

    def __call__(self) -> _FakeAdapter:
        self.configs.append(self._application.config)
        adapter = _FakeAdapter(
            portfolio=self._portfolio, error=self._error
        )
        self.adapters.append(adapter)
        return adapter


def _application(
    *,
    config: IBKRConnectionConfig | None = None,
    portfolio: BrokerAccountPortfolio | None = None,
    error: Exception | None = None,
) -> tuple[BrokerAccountApplication, _Factory]:
    """An application over a recording factory.

    The factory is bound after construction because it needs the
    application to read ``config`` from -- the same circular-but-late
    binding ``trading.composition.accounts`` uses.
    """

    application = BrokerAccountApplication(
        config or _config(),
        adapter_factory=lambda: None,  # replaced below
    )
    factory = _Factory(application, portfolio=portfolio, error=error)
    application._adapter_factory = factory
    return application, factory


# -- the factory sees the current config -------------------------------


def test_the_factory_reads_the_config_at_call_time() -> None:
    """Not captured at construction: the stale-config bug in miniature."""

    application, factory = _application(portfolio=_portfolio())
    application.refresh(timeout_seconds=5)

    assert factory.configs == [_config()]

    updated = _config(client_id=99)
    application.update_config(updated)
    application.refresh(timeout_seconds=5)

    assert factory.configs[-1] == updated
    assert factory.configs[-1].client_id == 99


def test_the_timeout_reaches_the_adapter() -> None:
    application, factory = _application(portfolio=_portfolio())
    application.refresh(timeout_seconds=7.5)

    assert factory.adapters[0].calls == [7.5]


def test_the_default_timeout_is_twenty_seconds() -> None:
    application, factory = _application(portfolio=_portfolio())
    application.refresh()

    assert factory.adapters[0].calls == [20]


# -- success -----------------------------------------------------------


def test_a_successful_refresh_returns_the_domain_snapshot() -> None:
    portfolio = _portfolio()
    application, _ = _application(portfolio=portfolio)

    result = application.refresh(timeout_seconds=5)

    assert result is portfolio
    assert result.account.account_alias == "DU***67"
    assert result.account.environment is Environment.PAPER


def test_a_successful_refresh_is_stored() -> None:
    portfolio = _portfolio()
    application, _ = _application(portfolio=portfolio)

    application.refresh(timeout_seconds=5)

    assert application.portfolio is portfolio


def test_a_successful_refresh_clears_a_previous_error() -> None:
    application, factory = _application(
        portfolio=_portfolio(), error=BrokerAccountUnavailable("down")
    )
    with pytest.raises(BrokerAccountUnavailable):
        application.refresh(timeout_seconds=5)
    assert application.last_error is not None

    factory._error = None
    application.refresh(timeout_seconds=5)

    assert application.last_error is None


def test_no_refresh_means_no_portfolio() -> None:
    """An unread account must not look like an empty one."""

    application, _ = _application(portfolio=_portfolio())

    assert application.portfolio is None
    assert application.last_error is None
    assert application.refresh_active is False


def test_the_application_holds_no_adapter_between_refreshes() -> None:
    """It builds one per refresh; a persistent socket is not this design."""

    application, factory = _application(portfolio=_portfolio())
    application.refresh(timeout_seconds=5)
    application.refresh(timeout_seconds=5)

    assert len(factory.adapters) == 2
    assert factory.adapters[0] is not factory.adapters[1]
    assert not hasattr(application, "adapter")


# -- failure keeps the previous truth ----------------------------------


def test_a_failed_refresh_keeps_the_previous_snapshot() -> None:
    portfolio = _portfolio()
    application, factory = _application(portfolio=portfolio)
    application.refresh(timeout_seconds=5)

    factory._error = BrokerAccountUnavailable("gateway down")
    with pytest.raises(BrokerAccountUnavailable):
        application.refresh(timeout_seconds=5)

    assert application.portfolio is portfolio


def test_a_failed_refresh_does_not_restamp_the_timestamp() -> None:
    """The single most important property here.

    Re-dating old data would make a stale account look fresh and pass the
    preflight's 300-second gate.
    """

    observed = datetime.now(timezone.utc) - timedelta(minutes=30)
    portfolio = _portfolio(observed_at=observed)
    application, factory = _application(portfolio=portfolio)
    application.refresh(timeout_seconds=5)

    factory._error = BrokerAccountUnavailable("gateway down")
    with pytest.raises(BrokerAccountUnavailable):
        application.refresh(timeout_seconds=5)

    assert application.portfolio is portfolio
    assert application.portfolio.account.observed_at == observed
    # And the age is still large, so the preflight fails closed.
    age = (
        datetime.now(timezone.utc) - application.portfolio.account.observed_at
    ).total_seconds()
    assert age > 300


def test_a_failed_refresh_records_the_error() -> None:
    application, _ = _application(
        error=BrokerAccountUnavailable("gateway down")
    )

    with pytest.raises(BrokerAccountUnavailable):
        application.refresh(timeout_seconds=5)

    assert application.last_error is not None
    assert "BrokerAccountUnavailable" in application.last_error
    assert "gateway down" in application.last_error


def test_a_failed_refresh_does_not_leave_the_guard_armed() -> None:
    """A failed refresh must release the concurrent-refresh guard."""

    application, _ = _application(
        error=BrokerAccountUnavailable("gateway down")
    )
    with pytest.raises(BrokerAccountUnavailable):
        application.refresh(timeout_seconds=5)

    assert application.refresh_active is False
    # And a later refresh is allowed.
    application._adapter_factory._error = None
    application._adapter_factory._portfolio = _portfolio()
    application.refresh(timeout_seconds=5)
    assert application.portfolio is not None


def test_an_unexpected_error_is_recorded_and_propagated() -> None:
    application, _ = _application(error=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        application.refresh(timeout_seconds=5)

    assert application.last_error is not None
    assert "boom" in application.last_error


# -- concurrent refresh ------------------------------------------------


def test_a_concurrent_refresh_is_refused() -> None:
    """Fail closed rather than opening a second client-id socket."""

    application, factory = _application(portfolio=_portfolio())

    class _Reentrant(_FakeAdapter):
        def refresh(self, *, timeout_seconds: float):
            # Re-enter while the guard is held.
            with pytest.raises(BrokerAccountActiveError):
                application.refresh(timeout_seconds=1)
            return super().refresh(timeout_seconds=timeout_seconds)

    application._adapter_factory = lambda: _Reentrant(
        portfolio=_portfolio()
    )
    application.refresh(timeout_seconds=5)


def test_the_guard_is_held_during_the_refresh() -> None:
    application, _ = _application(portfolio=_portfolio())
    seen: list[bool] = []

    class _Observing(_FakeAdapter):
        def refresh(self, *, timeout_seconds: float):
            seen.append(application.refresh_active)
            return super().refresh(timeout_seconds=timeout_seconds)

    application._adapter_factory = lambda: _Observing(
        portfolio=_portfolio()
    )
    application.refresh(timeout_seconds=5)

    assert seen == [True]
    assert application.refresh_active is False


# -- config ownership --------------------------------------------------


def test_the_config_property_exposes_the_current_config() -> None:
    application, _ = _application()

    assert application.config == _config()


def test_an_idle_config_update_is_accepted() -> None:
    application, _ = _application()
    updated = _config(client_id=99)

    application.update_config(updated)

    assert application.config == updated


def test_an_idle_config_update_is_visible_to_the_next_refresh() -> None:
    application, factory = _application(portfolio=_portfolio())

    application.update_config(_config(client_id=99))
    application.refresh(timeout_seconds=5)

    assert factory.configs[-1].client_id == 99


def test_a_config_change_is_refused_while_a_refresh_runs() -> None:
    """The adapter is connected with the config it was built from."""

    application, _ = _application(portfolio=_portfolio())
    refused: list[bool] = []

    class _Blocking(_FakeAdapter):
        def refresh(self, *, timeout_seconds: float):
            with pytest.raises(BrokerAccountActiveError):
                application.update_config(_config(client_id=99))
            refused.append(True)
            return super().refresh(timeout_seconds=timeout_seconds)

    application._adapter_factory = lambda: _Blocking(
        portfolio=_portfolio()
    )
    application.refresh(timeout_seconds=5)

    assert refused == [True]
    # Refused means unchanged, not half-applied.
    assert application.config.client_id == 17


def test_ensure_config_update_allowed_changes_nothing() -> None:
    """It is a check, not an application."""

    application, _ = _application()
    before = application.config

    application.ensure_config_update_allowed(_config(client_id=99))

    assert application.config == before


def test_ensure_config_update_allowed_accepts_an_unchanged_config() -> None:
    """Re-saving identical settings must not be blocked by a live refresh."""

    application, _ = _application(portfolio=_portfolio())

    class _Blocking(_FakeAdapter):
        def refresh(self, *, timeout_seconds: float):
            application.ensure_config_update_allowed(application.config)
            return super().refresh(timeout_seconds=timeout_seconds)

    application._adapter_factory = lambda: _Blocking(
        portfolio=_portfolio()
    )
    application.refresh(timeout_seconds=5)  # must not raise


def test_ensure_config_update_allowed_is_refused_while_refreshing() -> None:
    application, _ = _application(portfolio=_portfolio())
    refused: list[bool] = []

    class _Blocking(_FakeAdapter):
        def refresh(self, *, timeout_seconds: float):
            with pytest.raises(BrokerAccountActiveError):
                application.ensure_config_update_allowed(
                    _config(client_id=99)
                )
            refused.append(True)
            return super().refresh(timeout_seconds=timeout_seconds)

    application._adapter_factory = lambda: _Blocking(
        portfolio=_portfolio()
    )
    application.refresh(timeout_seconds=5)

    assert refused == [True]


def test_a_config_update_after_a_refresh_is_accepted() -> None:
    application, _ = _application(portfolio=_portfolio())
    application.refresh(timeout_seconds=5)

    application.update_config(_config(client_id=99))

    assert application.config.client_id == 99


# -- boundary ----------------------------------------------------------


def test_the_application_imports_no_adapter_or_qt() -> None:
    """Provider-blind and UI-free, checked on the real import graph."""

    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src"
        / "us_quant"
        / "trading"
        / "application"
        / "accounts.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    for forbidden in (
        "PySide6",
        "ibapi",
        "us_quant.trading.adapters",
        "us_quant.desktop",
        "us_quant.paper_trading_service",
        "us_quant.trading.application.market_data",
    ):
        assert forbidden not in imported
    assert "us_quant.trading.domain.account" in imported
    assert "us_quant.trading.ports.broker_account" in imported
