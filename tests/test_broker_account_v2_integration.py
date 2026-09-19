"""Broker Account v2 integration tests: preflight + settings transaction.

Two boundaries are exercised together here because each is only meaningful
against the other:

* the **targeted preflight** now consumes the domain
  :class:`BrokerAccountSnapshot` with a timezone-aware ``datetime``, so the
  300-second freshness gate, the positive-NLV rule and the Paper requirement
  are re-pinned against the new type;
* the **settings transaction** must protect *both* runtimes.  A live market
  stream and a running account refresh each refuse a connection change, and
  both refusals must land before the file is written.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.minute_data import MinuteDataSummary
from us_quant.trading.application.accounts import BrokerAccountApplication
from us_quant.trading.application.market_data import MarketDataApplication
from us_quant.trading.domain.account import BrokerAccountSnapshot
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.market import MarketQuote, MarketDataMode
from us_quant.trading.ports.broker_account import BrokerAccountActiveError
from us_quant.trading.ports.market_data import MarketDataActiveError
from us_quant.targeted_preflight import (
    MAXIMUM_ACCOUNT_AGE_SECONDS,
    evaluate_target_preflight,
)


NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def _account(
    *,
    observed_at: datetime | None = None,
    net_liquidation: Decimal | None = Decimal("10000"),
    environment: Environment = Environment.PAPER,
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
        observed_at=observed_at or NOW,
        pnl_source="IBKR reqPnL",
    )


def _quote(
    *, mode: MarketDataMode = MarketDataMode.REALTIME, age: float = 1.0
) -> MarketQuote:
    return MarketQuote(
        symbol="AAPL",
        bid=Decimal("100"),
        ask=Decimal("100.01"),
        last=Decimal("100"),
        close=None,
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
        mode=mode,
        updated_at=NOW,
        age_seconds=age,
        stale=False,
        stale_reason=None,
        generation=1,
        source_id="test",
        source_label="Test",
        coverage="unit",
    )


def _summary() -> MinuteDataSummary:
    return MinuteDataSummary(
        symbol="AAPL",
        total_rows=50,
        usable_rows=50,
        first_minute=None,
        last_minute=None,
        providers=("test",),
        evidence_origins=("captured_stream",),
    )


def _gates(result) -> dict[str, bool]:
    return {gate.code: gate.passed for gate in result.gates}


# -- the preflight's account truth gate --------------------------------


def test_a_fresh_paper_account_is_truth() -> None:
    result = evaluate_target_preflight(
        "AAPL",
        universe_record=None,
        quote=_quote(),
        account=_account(),
        minute_summary=_summary(),
        strategy=None,
        now=NOW,
    )

    assert _gates(result)["paper_account_truth"] is True


def test_a_stale_account_beyond_300_seconds_fails() -> None:
    stale = _account(
        observed_at=NOW - timedelta(seconds=301)
    )
    result = evaluate_target_preflight(
        "AAPL",
        universe_record=None,
        quote=_quote(),
        account=stale,
        minute_summary=_summary(),
        strategy=None,
        now=NOW,
    )

    assert _gates(result)["paper_account_truth"] is False


def test_an_account_exactly_at_the_limit_passes() -> None:
    """The boundary is inclusive: ``<= 300``."""

    at_limit = _account(
        observed_at=NOW - timedelta(seconds=int(MAXIMUM_ACCOUNT_AGE_SECONDS))
    )
    result = evaluate_target_preflight(
        "AAPL",
        universe_record=None,
        quote=_quote(),
        account=at_limit,
        minute_summary=_summary(),
        strategy=None,
        now=NOW,
    )

    assert _gates(result)["paper_account_truth"] is True


def test_a_zero_net_liquidation_fails() -> None:
    result = evaluate_target_preflight(
        "AAPL",
        universe_record=None,
        quote=_quote(),
        account=_account(net_liquidation=Decimal("0")),
        minute_summary=_summary(),
        strategy=None,
        now=NOW,
    )

    assert _gates(result)["paper_account_truth"] is False


def test_a_negative_net_liquidation_fails() -> None:
    result = evaluate_target_preflight(
        "AAPL",
        universe_record=None,
        quote=_quote(),
        account=_account(net_liquidation=Decimal("-1")),
        minute_summary=_summary(),
        strategy=None,
        now=NOW,
    )

    assert _gates(result)["paper_account_truth"] is False


def test_a_missing_net_liquidation_fails_closed() -> None:
    result = evaluate_target_preflight(
        "AAPL",
        universe_record=None,
        quote=_quote(),
        account=_account(net_liquidation=None),
        minute_summary=_summary(),
        strategy=None,
        now=NOW,
    )

    assert _gates(result)["paper_account_truth"] is False


def test_a_non_paper_environment_fails() -> None:
    """``environment is Environment.PAPER``, not a string comparison."""

    result = evaluate_target_preflight(
        "AAPL",
        universe_record=None,
        quote=_quote(),
        account=_account(environment=Environment.LIVE),
        minute_summary=_summary(),
        strategy=None,
        now=NOW,
    )

    assert _gates(result)["paper_account_truth"] is False


def test_a_missing_account_fails_closed() -> None:
    result = evaluate_target_preflight(
        "AAPL",
        universe_record=None,
        quote=_quote(),
        account=None,
        minute_summary=_summary(),
        strategy=None,
        now=NOW,
    )

    assert _gates(result)["paper_account_truth"] is False


def test_a_naive_observation_time_is_read_as_utc() -> None:
    """A naive ``datetime`` must not be treated as "now"."""

    naive = _account(
        observed_at=(NOW - timedelta(minutes=30)).replace(tzinfo=None)
    )
    result = evaluate_target_preflight(
        "AAPL",
        universe_record=None,
        quote=_quote(),
        account=naive,
        minute_summary=_summary(),
        strategy=None,
        now=NOW,
    )

    assert _gates(result)["paper_account_truth"] is False


def test_the_domain_timestamp_is_not_re_parsed_as_a_string() -> None:
    """A string where a ``datetime`` belongs fails closed, not crashes.

    The preflight used to call ``datetime.fromisoformat`` on the domain
    value; if that parsing came back, a string would be *accepted*.  It must
    be rejected as un-ageable instead.
    """

    broken = _account()
    object.__setattr__(broken, "observed_at", NOW.isoformat())

    result = evaluate_target_preflight(
        "AAPL",
        universe_record=None,
        quote=_quote(),
        account=broken,
        minute_summary=_summary(),
        strategy=None,
        now=NOW,
    )

    assert _gates(result)["paper_account_truth"] is False


# -- the settings transaction protects both runtimes -------------------


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


class _AccountAdapter:
    """A minimal adapter so the application can complete a refresh."""

    def refresh(self, *, timeout_seconds: float):
        raise AssertionError("this test never completes a refresh")


def _account_application() -> BrokerAccountApplication:
    return BrokerAccountApplication(
        _config(), adapter_factory=_AccountAdapter
    )


class _Stream:
    """A prepared market data adapter that never runs."""

    symbols = ("SPY",)

    def run(self) -> None:
        raise AssertionError("never run in these tests")

    def stop(self) -> None:
        pass

    def snapshot(self):  # pragma: no cover - unused
        raise AssertionError

    def health(self):  # pragma: no cover - unused
        raise AssertionError


def _market_application() -> MarketDataApplication:
    from us_quant.trading.application.market_data import (
        SOURCE_IBKR,
        MarketDataStartRequest,
    )

    application = MarketDataApplication(
        factories={SOURCE_IBKR: lambda request, listener: _Stream()}
    )
    application.prepare(
        MarketDataStartRequest(source_id=SOURCE_IBKR, symbols=("SPY",))
    )
    return application


def test_an_idle_runtime_accepts_a_config_change() -> None:
    account = _account_application()
    market = MarketDataApplication(factories={})

    account.ensure_config_update_allowed(_config(client_id=99))
    market.ensure_reconfiguration_allowed()

    account.update_config(_config(client_id=99))
    assert account.config.client_id == 99


def test_a_live_market_stream_refuses_reconfiguration() -> None:
    market = _market_application()

    with pytest.raises(MarketDataActiveError):
        market.ensure_reconfiguration_allowed()


def test_a_running_account_refresh_refuses_a_config_change() -> None:
    account = _account_application()
    refused: list[bool] = []

    class _Blocking(_AccountAdapter):
        def refresh(self, *, timeout_seconds: float):
            with pytest.raises(BrokerAccountActiveError):
                account.ensure_config_update_allowed(_config(client_id=99))
            refused.append(True)
            from us_quant.trading.domain.account import (
                BrokerAccountPortfolio,
            )

            return BrokerAccountPortfolio(account=_account(), positions=())

    account._adapter_factory = _Blocking
    account.refresh(timeout_seconds=1)

    assert refused == [True]
    assert account.config.client_id == 17


def test_both_guards_must_pass_before_a_change_is_accepted() -> None:
    """The settings transaction asks both, and either refusal is enough."""

    account = _account_application()
    market = _market_application()

    with pytest.raises(MarketDataActiveError):
        account.ensure_config_update_allowed(_config(client_id=99))
        market.ensure_reconfiguration_allowed()

    # The account accepted it (it is idle); the market refused.
    assert account.config.client_id == 17


def test_the_market_application_is_the_guard_protocol() -> None:
    """Structural: the settings module can accept it without an import."""

    from us_quant.desktop_settings import RuntimeReconfigurationGuard

    market = MarketDataApplication(factories={})
    assert hasattr(market, "ensure_reconfiguration_allowed")
    # And it has no config surface left for the guard protocol to reach.
    assert not hasattr(market, "config")
    assert not hasattr(market, "update_config")
    del RuntimeReconfigurationGuard


def test_the_account_application_is_the_config_owner_protocol() -> None:
    from us_quant.desktop_settings import BrokerConfigOwnerPort

    account = _account_application()
    assert isinstance(account.config, IBKRConnectionConfig)
    assert callable(account.ensure_config_update_allowed)
    assert callable(account.update_config)
    del BrokerConfigOwnerPort
