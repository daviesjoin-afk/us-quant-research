"""CLI ``ibkr-readonly`` tests.

The command name is unchanged (§106) but what it does is not: it now reads
through ``BrokerAccountApplication`` and serialises the *domain* portfolio,
which already holds only a masked alias.

Two consequences are pinned here:

* the output has no second masking pass, because the domain cannot carry a
  raw account id in the first place -- so a raw id cannot leak even if the
  CLI's own serialiser is wrong;
* the output carries no market-readiness keys.  ``quotes``,
  ``intraday_market_data_ready`` and ``intraday_market_data_reasons`` are
  Market Data v2's business and are gone from this command (§105).
"""

from __future__ import annotations

import ast
import json
import pathlib
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.cli import ibkr_readonly, portfolio_to_redacted_dict
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
    BrokerDiagnostic,
    BrokerPositionSnapshot,
)
from us_quant.trading.domain.common import Environment


_RAW = "DU1234567"
_CLI_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "cli.py"
)
NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def _portfolio() -> BrokerAccountPortfolio:
    return BrokerAccountPortfolio(
        account=BrokerAccountSnapshot(
            environment=Environment.PAPER,
            account_alias="DU***67",
            net_liquidation=Decimal("10000.50"),
            cash=Decimal("5000.25"),
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
        positions=(
            BrokerPositionSnapshot(
                account_alias="DU***67",
                con_id=1,
                symbol="AAPL",
                local_symbol="AAPL",
                security_type="STK",
                exchange="SMART",
                currency="USD",
                quantity=Decimal("0.5"),
                average_cost=Decimal("100"),
                market_value=Decimal("50"),
                daily_pnl=Decimal("1"),
                unrealized_pnl=Decimal("2"),
                realized_pnl=Decimal("3"),
                observed_at=NOW,
            ),
        ),
        diagnostics=(
            BrokerDiagnostic(
                code=2104, message="farm OK", informational=True
            ),
        ),
    )


# -- the serialised shape ----------------------------------------------


def test_the_output_is_json_serialisable() -> None:
    payload = portfolio_to_redacted_dict(_portfolio())

    # A round trip proves every value is JSON-safe, not merely str()-able.
    restored = json.loads(json.dumps(payload, ensure_ascii=False))
    assert restored["account"]["account_alias"] == "DU***67"


def test_the_output_has_no_raw_account_id() -> None:
    payload = portfolio_to_redacted_dict(_portfolio())
    text = json.dumps(payload, ensure_ascii=False)

    assert _RAW not in text
    assert "DU***67" in text


def test_the_output_has_no_market_readiness_keys() -> None:
    """§105: those belong to Market Data v2."""

    payload = portfolio_to_redacted_dict(_portfolio())

    assert "quotes" not in payload
    assert "intraday_market_data_ready" not in payload
    assert "intraday_market_data_reasons" not in payload
    assert "market_data_type" not in json.dumps(payload)


def test_the_output_reports_the_refresh_and_the_account() -> None:
    payload = portfolio_to_redacted_dict(_portfolio())

    assert payload["connected"] is True
    assert payload["refreshed"] is True
    assert payload["account"]["environment"] == "paper"
    assert payload["account"]["net_liquidation"] == "10000.50"
    assert payload["account"]["pnl_source"] == "IBKR reqPnL"


def test_the_output_keeps_the_fractional_quantity() -> None:
    payload = portfolio_to_redacted_dict(_portfolio())

    assert payload["positions"][0]["quantity"] == "0.5"
    assert payload["positions"][0]["market_value"] == "50"


def test_the_output_carries_the_diagnostics() -> None:
    payload = portfolio_to_redacted_dict(_portfolio())

    assert payload["diagnostics"] == [
        {"code": 2104, "message": "farm OK", "informational": True}
    ]


def test_a_missing_value_serialises_as_null_not_zero() -> None:
    portfolio = BrokerAccountPortfolio(
        account=BrokerAccountSnapshot(
            environment=Environment.PAPER,
            account_alias="DU***67",
            net_liquidation=None,
            cash=None,
            available_funds=None,
            buying_power=None,
            gross_position_value=None,
            excess_liquidity=None,
            maintenance_margin=None,
            cushion=None,
            daily_pnl=None,
            unrealized_pnl=None,
            realized_pnl=None,
            observed_at=NOW,
            pnl_source="unavailable",
        ),
        positions=(),
    )
    payload = portfolio_to_redacted_dict(portfolio)

    assert payload["account"]["net_liquidation"] is None
    assert payload["account"]["cash"] is None
    assert payload["positions"] == []


# -- the command -------------------------------------------------------


def test_the_command_reads_through_the_account_application(
    monkeypatch, tmp_path, capsys
) -> None:
    """It must not build an IBKR client itself any more."""

    from us_quant.config import load_config

    config_path = pathlib.Path(__file__).resolve().parents[1] / "configs" / "paper.toml"
    config = load_config(config_path)

    built: list = []
    refreshed: list = []

    class _App:
        def __init__(self, config) -> None:
            self.config = config

        def refresh(self, *, timeout_seconds: float = 20):
            refreshed.append(timeout_seconds)
            return _portfolio()

    monkeypatch.setattr(
        "us_quant.cli.load_config", lambda _path: config
    )
    monkeypatch.setattr(
        "us_quant.cli.build_broker_account_application",
        lambda cfg: built.append(cfg) or _App(cfg),
    )

    exit_code = ibkr_readonly(config_path)

    assert exit_code == 0
    assert built == [config.ibkr]
    assert refreshed == [20]
    payload = json.loads(capsys.readouterr().out)
    assert payload["account"]["account_alias"] == "DU***67"


def test_the_command_reports_an_unavailable_gateway_as_four(
    monkeypatch, capsys
) -> None:
    from us_quant.config import load_config
    from us_quant.trading.ports.broker_account import (
        BrokerAccountUnavailable,
    )

    config_path = pathlib.Path(__file__).resolve().parents[1] / "configs" / "paper.toml"
    config = load_config(config_path)

    class _App:
        def refresh(self, *, timeout_seconds: float = 20):
            raise BrokerAccountUnavailable("no gateway")

    monkeypatch.setattr("us_quant.cli.load_config", lambda _p: config)
    monkeypatch.setattr(
        "us_quant.cli.build_broker_account_application", lambda _c: _App()
    )

    assert ibkr_readonly(config_path) == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["connected"] is False
    assert "TWS API" in payload["next_step"]


def test_the_command_reports_a_validation_error_as_five(
    monkeypatch, capsys
) -> None:
    from us_quant.config import load_config
    from us_quant.trading.ports.broker_account import (
        BrokerAccountValidationError,
    )

    config_path = pathlib.Path(__file__).resolve().parents[1] / "configs" / "paper.toml"
    config = load_config(config_path)

    class _App:
        def refresh(self, *, timeout_seconds: float = 20):
            raise BrokerAccountValidationError("not a DU account")

    monkeypatch.setattr("us_quant.cli.load_config", lambda _p: config)
    monkeypatch.setattr(
        "us_quant.cli.build_broker_account_application", lambda _c: _App()
    )

    assert ibkr_readonly(config_path) == 5
    payload = json.loads(capsys.readouterr().out)
    assert payload["connected"] is False
    assert "DU" in payload["error"]


def test_the_command_still_requires_the_paper_environment(
    monkeypatch, capsys
) -> None:
    import dataclasses

    from us_quant.config import load_config

    config_path = pathlib.Path(__file__).resolve().parents[1] / "configs" / "paper.toml"
    config = load_config(config_path)
    live = dataclasses.replace(config, live_trading_enabled=True)

    monkeypatch.setattr("us_quant.cli.load_config", lambda _p: live)

    assert ibkr_readonly(config_path) == 2


# -- the command no longer imports the retired modules -----------------


def test_the_cli_does_not_import_the_retired_account_modules() -> None:
    tree = ast.parse(_CLI_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert "us_quant.ibkr_readonly" not in imported
    assert "us_quant.portfolio_view" not in imported
    assert "us_quant.trading.composition.accounts" in imported


def test_the_cli_no_longer_defines_the_removed_helpers() -> None:
    source = _CLI_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "ibkr_readonly" in functions  # the command itself is kept
    assert "snapshot_to_redacted_dict" not in functions
    assert "portfolio_to_redacted_dict" in functions


def test_the_command_name_is_preserved() -> None:
    """§106: this change must not also churn the CLI UX."""

    source = _CLI_PATH.read_text(encoding="utf-8")
    assert '"ibkr-readonly"' in source
    assert 'args.command == "ibkr-readonly"' in source
