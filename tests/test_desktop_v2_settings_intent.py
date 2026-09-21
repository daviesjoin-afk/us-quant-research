"""Every Settings control must report intent as a stable value, not a widget."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.system.settings import SettingsPage
from us_quant.desktop_v2.pages.system.settings.models import (
    CredentialDraft,
    SettingsDraft,
    SettingsStorageView,
)


_APP = QApplication.instance() or QApplication([])

_DRAFT = SettingsDraft(
    theme="dark",
    market_provider="finnhub_trades",
    ibkr_host="127.0.0.1",
    ibkr_port=4002,
    ibkr_client_id=71,
    connection_timeout_seconds=15.0,
    paper_order_capability_enabled=False,
    extended_hours_paper_enabled=False,
)
_STORAGE = SettingsStorageView("S", "C", "R", "E")


def _page() -> SettingsPage:
    return SettingsPage(_DRAFT, _STORAGE)


def test_theme_change_emits_the_theme_string() -> None:
    page = _page()
    seen: list[str] = []
    page.theme_preview_requested.connect(seen.append)
    combo = page.appearance.theme_combo
    combo.setCurrentIndex(combo.findData("light"))
    assert seen == ["light"]


def test_market_provider_change_emits_the_provider_string() -> None:
    page = _page()
    seen: list[str] = []
    page.market_provider_selected.connect(seen.append)
    combo = page.appearance.provider_combo
    combo.setCurrentIndex(combo.findData("alpaca_iex"))
    assert seen == ["alpaca_iex"]


def test_switch_button_emits_a_settings_draft() -> None:
    page = _page()
    seen: list[object] = []
    page.switch_provider_requested.connect(seen.append)

    page.appearance.switch_button.click()

    assert len(seen) == 1
    assert seen[0] == _DRAFT


def test_api_provider_change_emits_the_provider_string() -> None:
    page = _page()
    seen: list[str] = []
    page.api_provider_selected.connect(seen.append)
    combo = page.credentials.api_provider_combo
    combo.setCurrentIndex(combo.findData("alpaca_iex"))
    assert seen == ["alpaca_iex"]


def test_save_credentials_emits_a_credential_draft() -> None:
    page = _page()
    page.credentials.api_provider_combo.setCurrentIndex(
        page.credentials.api_provider_combo.findData("alpaca_iex")
    )
    page.credentials.alpaca_key.setText("key")
    page.credentials.alpaca_secret.setText("secret")
    seen: list[object] = []
    page.save_credentials_requested.connect(seen.append)

    page.credentials.save_button.click()

    assert seen == [
        CredentialDraft(
            provider="alpaca_iex",
            api_key="key",
            api_secret="secret",
        )
    ]


def test_clear_credentials_emits_the_provider_string() -> None:
    page = _page()
    page.credentials.api_provider_combo.setCurrentIndex(
        page.credentials.api_provider_combo.findData("alpaca_iex")
    )
    seen: list[str] = []
    page.clear_credentials_requested.connect(seen.append)

    page.credentials.clear_button.click()

    assert seen == ["alpaca_iex"]


def test_capability_toggles_emit_booleans() -> None:
    page = _page()
    paper: list[bool] = []
    extended: list[bool] = []
    page.paper_order_capability_toggled.connect(paper.append)
    page.extended_hours_paper_toggled.connect(extended.append)

    page.connection.paper_order_capability.click()
    page.connection.extended_hours_paper.click()

    assert paper == [True]
    assert extended == [True]


def test_save_button_emits_a_settings_draft() -> None:
    page = _page()
    page.connection.host_input.setText("localhost")
    seen: list[object] = []
    page.save_preferences_requested.connect(seen.append)

    page.save_button.click()

    assert len(seen) == 1
    draft = seen[0]
    assert isinstance(draft, SettingsDraft)
    assert draft.ibkr_host == "localhost"


def test_no_intent_carries_a_widget() -> None:
    page = _page()
    seen: list[object] = []
    for name in (
        "theme_preview_requested",
        "market_provider_selected",
        "switch_provider_requested",
        "api_provider_selected",
        "save_credentials_requested",
        "clear_credentials_requested",
        "paper_order_capability_toggled",
        "extended_hours_paper_toggled",
        "save_preferences_requested",
    ):
        getattr(page, name).connect(seen.append)

    page.appearance.switch_button.click()
    page.credentials.save_button.click()
    page.credentials.clear_button.click()
    page.save_button.click()

    for payload in seen:
        assert not hasattr(payload, "metaObject")
