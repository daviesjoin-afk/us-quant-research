"""Real-Qt tests for the Settings page: construction, API and render."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace

from PySide6.QtWidgets import QApplication, QLineEdit

from us_quant.desktop_v2.pages.system.settings import SettingsPage
from us_quant.desktop_v2.pages.system.settings.appearance import (
    AppearanceSection,
    MARKET_PROVIDER_OPTIONS,
    THEME_OPTIONS,
)
from us_quant.desktop_v2.pages.system.settings.connection import (
    PAPER_GATEWAY_PORT,
)
from us_quant.desktop_v2.pages.system.settings.credentials import (
    API_PROVIDER_OPTIONS,
    CredentialsSection,
)
from us_quant.desktop_v2.pages.system.settings.models import (
    SettingsDraft,
    SettingsPageView,
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
_STORAGE = SettingsStorageView(
    settings_path="S",
    credentials_path="C",
    runtime_path="R",
    exports_path="E",
)


def _page(draft: SettingsDraft = _DRAFT) -> SettingsPage:
    return SettingsPage(draft, _STORAGE)


def _combo_ids(combo) -> list[str]:
    return [combo.itemData(index) for index in range(combo.count())]


# -- construction ------------------------------------------------------


def test_construction_fires_no_signals(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        AppearanceSection,
        "_theme_changed",
        lambda self, _index: seen.append("theme"),
    )
    monkeypatch.setattr(
        AppearanceSection,
        "_provider_changed",
        lambda self, _index: seen.append("market"),
    )
    monkeypatch.setattr(
        CredentialsSection,
        "_provider_changed",
        lambda self, _index: seen.append("api"),
    )
    _page(replace(_DRAFT, theme="light", market_provider="ibkr_extended"))
    assert seen == []


def test_initial_values_come_from_the_draft() -> None:
    page = _page(
        replace(
            _DRAFT,
            theme="light",
            market_provider="alpaca_iex",
            ibkr_host="::1",
            ibkr_client_id=99,
            connection_timeout_seconds=30.0,
            paper_order_capability_enabled=True,
            extended_hours_paper_enabled=True,
        )
    )
    draft = page.current_draft()
    assert draft.theme == "light"
    assert draft.market_provider == "alpaca_iex"
    assert draft.ibkr_host == "::1"
    assert draft.ibkr_client_id == 99
    assert draft.connection_timeout_seconds == 30.0
    assert draft.paper_order_capability_enabled is True
    assert draft.extended_hours_paper_enabled is True


def test_theme_order_is_frozen() -> None:
    page = _page()
    assert _combo_ids(page.appearance.theme_combo) == [
        key for _label, key in THEME_OPTIONS
    ]
    assert _combo_ids(page.appearance.theme_combo) == ["dark", "light"]


def test_market_provider_order_is_frozen() -> None:
    page = _page()
    assert _combo_ids(page.appearance.provider_combo) == [
        "finnhub_trades",
        "alpaca_iex",
        "ibkr",
        "ibkr_extended",
    ]


def test_api_provider_order_is_frozen() -> None:
    page = _page()
    assert _combo_ids(page.credentials.api_provider_combo) == [
        key for _label, key in API_PROVIDER_OPTIONS
    ]
    assert _combo_ids(page.credentials.api_provider_combo) == [
        "finnhub_trades",
        "alpaca_iex",
        "ibkr",
    ]


def test_ibkr_port_is_hard_locked_to_4002() -> None:
    page = _page()
    port = page.connection.port_input
    assert port.minimum() == PAPER_GATEWAY_PORT
    assert port.maximum() == PAPER_GATEWAY_PORT
    assert port.value() == PAPER_GATEWAY_PORT


def test_timeout_rounding_is_preserved() -> None:
    page = _page(replace(_DRAFT, connection_timeout_seconds=15.6))
    assert page.current_draft().connection_timeout_seconds == 16.0


def test_secret_inputs_keep_password_echo_mode() -> None:
    page = _page()
    for field in (
        page.credentials.finnhub_key,
        page.credentials.alpaca_key,
        page.credentials.alpaca_secret,
    ):
        assert field.echoMode() == QLineEdit.EchoMode.Password


# -- public API --------------------------------------------------------


def test_current_draft_is_exact() -> None:
    page = _page()
    assert page.current_draft() == _DRAFT


def test_current_credentials_draft_is_exact() -> None:
    page = _page()
    page.credentials.finnhub_key.setText("key")
    draft = page.current_credentials_draft()
    assert draft.provider == "finnhub_trades"
    assert draft.api_key == "key"
    assert draft.api_secret == ""


def test_set_market_provider_is_silent_by_default() -> None:
    page = _page()
    seen: list[str] = []
    page.market_provider_selected.connect(seen.append)

    page.set_market_provider("alpaca_iex")

    assert page.current_draft().market_provider == "alpaca_iex"
    assert seen == []


def test_set_market_provider_emits_when_asked() -> None:
    page = _page()
    seen: list[str] = []
    page.market_provider_selected.connect(seen.append)

    page.set_market_provider("alpaca_iex", emit_change=True)

    assert seen == ["alpaca_iex"]


def test_set_api_provider_is_silent_by_default() -> None:
    page = _page()
    seen: list[str] = []
    page.api_provider_selected.connect(seen.append)

    page.set_api_provider("alpaca_iex")

    assert page.current_credentials_draft().provider == "alpaca_iex"
    assert seen == []


def test_set_api_provider_emits_when_asked() -> None:
    page = _page()
    seen: list[str] = []
    page.api_provider_selected.connect(seen.append)

    page.set_api_provider("alpaca_iex", emit_change=True)

    assert seen == ["alpaca_iex"]


def test_capability_setters_are_silent_and_can_emit() -> None:
    page = _page()
    seen: list[bool] = []
    page.paper_order_capability_toggled.connect(seen.append)

    page.set_paper_order_capability(True)
    assert page.current_draft().paper_order_capability_enabled is True
    assert seen == []

    page.set_paper_order_capability(False, emit_change=True)
    assert seen == [False]


def test_extended_hours_setter_is_silent_and_can_emit() -> None:
    page = _page()
    seen: list[bool] = []
    page.extended_hours_paper_toggled.connect(seen.append)

    page.set_extended_hours_paper(True)
    assert page.current_draft().extended_hours_paper_enabled is True
    assert seen == []

    page.set_extended_hours_paper(False, emit_change=True)
    assert seen == [False]


def test_clear_credential_inputs_clears_only_the_secrets() -> None:
    page = _page()
    page.credentials.finnhub_key.setText("a")
    page.credentials.alpaca_key.setText("b")
    page.credentials.alpaca_secret.setText("c")

    page.clear_credential_inputs()

    assert page.credentials.finnhub_key.text() == ""
    assert page.credentials.alpaca_key.text() == ""
    assert page.credentials.alpaca_secret.text() == ""
    # The non-secret preferences are untouched.
    assert page.current_draft() == _DRAFT


# -- render ------------------------------------------------------------


def _view(**overrides) -> SettingsPageView:
    kwargs = dict(
        credential_status_text="status",
        credential_save_enabled=True,
        credential_clear_enabled=True,
        connection_settings_enabled=True,
    )
    kwargs.update(overrides)
    return SettingsPageView(**kwargs)


def test_render_paints_the_credential_status_and_buttons() -> None:
    page = _page()
    page.render(
        _view(
            credential_status_text="Finnhub：已加密保存",
            credential_save_enabled=False,
            credential_clear_enabled=True,
        )
    )
    assert page.credentials.status_label.text() == "Finnhub：已加密保存"
    assert page.credentials.save_button.isEnabled() is False
    assert page.credentials.clear_button.isEnabled() is True


def test_render_toggles_only_the_connection_controls() -> None:
    page = _page()
    page.render(_view(connection_settings_enabled=False))

    for control in (
        page.connection.host_input,
        page.connection.port_input,
        page.connection.client_id_input,
        page.connection.timeout_input,
        page.connection.paper_order_capability,
        page.connection.extended_hours_paper,
    ):
        assert control.isEnabled() is False

    for control in (
        page.appearance.provider_combo,
        page.appearance.switch_button,
        page.credentials.api_provider_combo,
        page.credentials.finnhub_key,
    ):
        assert control.isEnabled() is True

    page.render(_view(connection_settings_enabled=True))
    assert page.connection.host_input.isEnabled() is True


def test_render_does_not_overwrite_operator_input() -> None:
    page = _page()
    page.connection.host_input.setText("localhost")
    page.credentials.finnhub_key.setText("typed")

    page.render(_view())

    assert page.connection.host_input.text() == "localhost"
    assert page.credentials.finnhub_key.text() == "typed"


# -- credential visibility ---------------------------------------------


def test_only_the_selected_provider_fields_are_visible() -> None:
    page = _page()

    page.set_api_provider("finnhub_trades")
    assert page.credentials.finnhub_key.isHidden() is False
    assert page.credentials.alpaca_key.isHidden() is True
    assert page.credentials.alpaca_secret.isHidden() is True

    page.set_api_provider("alpaca_iex")
    assert page.credentials.finnhub_key.isHidden() is True
    assert page.credentials.alpaca_key.isHidden() is False
    assert page.credentials.alpaca_secret.isHidden() is False

    page.set_api_provider("ibkr")
    assert page.credentials.finnhub_key.isHidden() is True
    assert page.credentials.alpaca_key.isHidden() is True
    assert page.credentials.alpaca_secret.isHidden() is True
