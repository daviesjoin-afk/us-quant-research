"""Pure rules and projections of the Settings capability (v2O-F2).

Everything here is a function of its arguments: no window, no Qt, no store, no
filesystem.  That is the point of keeping these rules out of the orchestrator --
the four decisions the Settings workspace makes (what a Save-credentials click
means, what the status line says, which API provider a market provider maps to,
and what the page is built from) are testable without constructing anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from us_quant.desktop_credentials import CredentialStatus
from us_quant.desktop_v2.orchestration.system.settings import queries
from us_quant.desktop_v2.orchestration.system.settings.models import (
    CredentialSaveOutcome,
)
from us_quant.desktop_v2.pages.system.settings.models import (
    SettingsDraft,
    api_provider_for_market_provider,
)
from us_quant.user_settings import UserPreferences


# -- A. the initial projections -----------------------------------------


def _preferences() -> UserPreferences:
    return UserPreferences(
        theme="light",
        market_provider="ibkr_extended",
        ibkr_host="localhost",
        ibkr_port=4002,
        ibkr_client_id=12345,
        connection_timeout_seconds=42.0,
        paper_order_capability_enabled=True,
        extended_hours_paper_enabled=True,
    )


def test_the_draft_is_the_preferences_field_for_field() -> None:
    draft = queries.settings_draft_from_preferences(_preferences())

    assert draft == SettingsDraft(
        theme="light",
        market_provider="ibkr_extended",
        ibkr_host="localhost",
        ibkr_port=4002,
        ibkr_client_id=12345,
        connection_timeout_seconds=42.0,
        paper_order_capability_enabled=True,
        extended_hours_paper_enabled=True,
    )


def test_the_draft_and_the_preferences_round_trip() -> None:
    """The two mappings are inverses, so a page built from a draft is a save."""
    preferences = _preferences()
    assert (
        queries.preferences_from_draft(
            queries.settings_draft_from_preferences(preferences)
        )
        == preferences
    )


@pytest.mark.parametrize(
    "market_provider,expected",
    [
        ("finnhub_trades", "finnhub_trades"),
        ("alpaca_iex", "alpaca_iex"),
        ("ibkr", "ibkr"),
        # The 5x24 Paper route is the same Gateway, so it shares IBKR's
        # credential set rather than inventing a provider.
        ("ibkr_extended", "ibkr"),
    ],
)
def test_the_api_provider_mapping_is_frozen(
    market_provider: str, expected: str
) -> None:
    assert (
        api_provider_for_market_provider(market_provider) == expected
    )


def test_the_storage_view_names_the_four_paths() -> None:
    view = queries.settings_storage_view(
        state_root=Path("S:/state"),
        runtime_root=Path("S:/state/runtime"),
        exports_root=Path("S:/state/exports"),
    )

    assert view.settings_path == str(Path("S:/state/settings"))
    assert view.credentials_path == str(Path("S:/state/credentials"))
    assert view.runtime_path == str(Path("S:/state/runtime"))
    assert view.exports_path == str(Path("S:/state/exports"))


# -- G. the credential status projection --------------------------------


def _status(
    provider: str, *, key: bool = False, secret: bool = False
) -> CredentialStatus:
    return CredentialStatus(
        provider=provider,
        requires_api_key=provider in {"finnhub_trades", "alpaca_iex"},
        api_key_saved=key,
        api_secret_saved=secret,
    )


@pytest.mark.parametrize(
    "saved,expected", [(True, "Finnhub：已加密保存"), (False, "Finnhub：未保存")]
)
def test_the_finnhub_status_line_is_either_saved_or_not(
    saved: bool, expected: str
) -> None:
    assert (
        queries.credential_status_text(
            _status("finnhub_trades", key=saved)
        )
        == expected
    )


@pytest.mark.parametrize("key", [True, False])
@pytest.mark.parametrize("secret", [True, False])
def test_the_alpaca_status_line_reports_each_half(key: bool, secret: bool) -> None:
    text = queries.credential_status_text(
        _status("alpaca_iex", key=key, secret=secret)
    )

    assert text == (
        "Alpaca Key："
        f"{'已加密保存' if key else '未保存'}"
        " · Alpaca Secret："
        f"{'已加密保存' if secret else '未保存'}"
    )


@pytest.mark.parametrize("provider", ["ibkr", "ibkr_extended"])
def test_the_ibkr_status_line_says_no_key_is_needed(provider: str) -> None:
    assert queries.credential_status_text(_status(provider)) == (
        "IBKR Gateway：使用本机 Host / 端口 / Client ID，"
        "无需 API Key"
    )


def test_the_status_projection_takes_a_status_and_not_a_service() -> None:
    """The projection cannot read the store even by accident."""

    source = Path(queries.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "has_secret(",
        "load_secret(",
        "save_secret(",
        "delete_secret(",
        "WindowsCredentialStore",
    ):
        assert forbidden not in source, forbidden


# -- F / G / H. the credential save decision ----------------------------


def test_a_blank_finnhub_key_is_no_change() -> None:
    plan = queries.credential_save_plan(
        "finnhub_trades", api_key="   ", api_secret=""
    )
    assert plan.outcome is CredentialSaveOutcome.NO_CHANGE
    assert plan.api_key == ""


def test_a_finnhub_key_is_trimmed_and_saved() -> None:
    plan = queries.credential_save_plan(
        "finnhub_trades", api_key="  key-1  ", api_secret="ignored"
    )
    assert plan.outcome is CredentialSaveOutcome.SAVE
    assert plan.api_key == "key-1"
    # Finnhub has one half, so the secret is never carried.
    assert plan.api_secret == ""


def test_a_blank_alpaca_pair_is_no_change() -> None:
    plan = queries.credential_save_plan(
        "alpaca_iex", api_key="  ", api_secret=""
    )
    assert plan.outcome is CredentialSaveOutcome.NO_CHANGE


@pytest.mark.parametrize(
    "key,secret",
    [("key", "  "), ("  ", "secret"), ("key", ""), ("", "secret")],
)
def test_a_half_filled_alpaca_pair_is_incomplete(key: str, secret: str) -> None:
    plan = queries.credential_save_plan(
        "alpaca_iex", api_key=key, api_secret=secret
    )
    assert plan.outcome is CredentialSaveOutcome.INCOMPLETE
    # Refused means nothing to write, not a partial write.
    assert plan.api_key == ""
    assert plan.api_secret == ""


def test_a_complete_alpaca_pair_is_trimmed_and_saved() -> None:
    plan = queries.credential_save_plan(
        "alpaca_iex", api_key=" key ", api_secret=" secret "
    )
    assert plan.outcome is CredentialSaveOutcome.SAVE
    assert (plan.api_key, plan.api_secret) == ("key", "secret")


@pytest.mark.parametrize("provider", ["ibkr", "ibkr_extended", "unknown"])
def test_a_provider_without_an_api_key_is_never_written(provider: str) -> None:
    """Including an unknown id: the retired handler explained, never wrote."""

    plan = queries.credential_save_plan(
        provider, api_key="key", api_secret="secret"
    )
    assert plan.outcome is CredentialSaveOutcome.NOT_REQUIRED
    assert (plan.api_key, plan.api_secret) == ("", "")


@pytest.mark.parametrize(
    "provider,expected",
    [
        ("finnhub_trades", True),
        ("alpaca_iex", True),
        ("ibkr", False),
        ("ibkr_extended", False),
        ("unknown", False),
    ],
)
def test_the_api_key_providers_come_from_the_credential_service(
    provider: str, expected: bool
) -> None:
    assert queries.provider_requires_api_key(provider) is expected


@pytest.mark.parametrize(
    "provider,expected",
    [("finnhub_trades", "Finnhub"), ("alpaca_iex", "Alpaca")],
)
def test_the_log_label_names_the_provider(provider: str, expected: str) -> None:
    assert queries.provider_label(provider) == expected


def test_the_queries_module_is_qt_free_and_service_free() -> None:
    source = Path(queries.__file__).read_text(encoding="utf-8")
    for forbidden in ("PySide6", "QMessageBox", "MainWindow"):
        assert forbidden not in source, forbidden
