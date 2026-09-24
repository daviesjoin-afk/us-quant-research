"""Wiring tests: the real window's Settings route, end to end."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QMessageBox

from us_quant.desktop import MainWindow
from us_quant.paths import STATE_ROOT_ENV
from us_quant.user_settings import UserPreferences


_APP = QApplication.instance() or QApplication([])


def _window(monkeypatch, tmp_path) -> MainWindow:
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path / "state"))
    window = MainWindow()
    _APP.processEvents()
    return window


class _RunningWorker:
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id

    def isRunning(self) -> bool:  # noqa: N802 - Qt spelling
        return True


def _capture(monkeypatch, kind: str) -> list[tuple]:
    calls: list[tuple] = []

    def recorder(*args, **kwargs):
        calls.append((args, kwargs))
        return None

    monkeypatch.setattr(f"us_quant.desktop.QMessageBox.{kind}", recorder)
    return calls


def test_theme_change_reaches_apply_theme(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        page = window.settings_page
        combo = page.appearance.theme_combo
        combo.setCurrentIndex(combo.findData("light"))
        assert window.current_theme_name == "light"
    finally:
        window.close()
        window.deleteLater()


def test_settings_provider_maps_to_the_api_provider(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        page = window.settings_page
        combo = page.appearance.provider_combo
        combo.setCurrentIndex(combo.findData("ibkr_extended"))
        _APP.processEvents()

        assert window.market_page.selected_provider() == "ibkr_extended"
        assert window.settings_orchestrator.selected_api_provider == "ibkr"
        assert page.current_credentials_draft().provider == "ibkr"
    finally:
        window.close()
        window.deleteLater()


def test_market_provider_sync_is_silent(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        seen: list[str] = []
        window.settings_page.market_provider_selected.connect(seen.append)
        combo = window.market_page.controls.provider_combo
        combo.setCurrentIndex(combo.findData("alpaca_iex"))
        _APP.processEvents()

        assert (
            window.settings_page.current_draft().market_provider
            == "alpaca_iex"
        )
        assert seen == []
    finally:
        window.close()
        window.deleteLater()


def test_saving_preferences_builds_a_user_preferences(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        calls: list[tuple] = []

        def commit(preferences, **kwargs):
            calls.append((preferences, kwargs))
            return SimpleNamespace(
                preferences=preferences, config=window.config
            )

        monkeypatch.setattr(window.settings_service, "commit", commit)
        page = window.settings_page
        page.connection.host_input.setText("localhost")
        page.save_button.click()

        assert len(calls) == 1
        preferences = calls[0][0]
        assert isinstance(preferences, UserPreferences)
        assert preferences.ibkr_host == "localhost"
        assert preferences.theme == "dark"
    finally:
        window.close()
        window.deleteLater()


def test_a_successful_save_updates_state_theme_and_provider(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        page = window.settings_page
        page.set_theme("light", emit_change=True)
        combo = page.appearance.provider_combo
        combo.setCurrentIndex(combo.findData("alpaca_iex"))
        page.save_button.click()

        assert window.preferences.theme == "light"
        assert window.preferences.market_provider == "alpaca_iex"
        assert window.current_theme_name == "light"
        assert window.market_page.selected_provider() == "alpaca_iex"
    finally:
        window.close()
        window.deleteLater()


def test_paper_capability_refusal_resets_without_a_second_intent(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(
            "us_quant.desktop.QMessageBox.warning",
            lambda *args, **kwargs: QMessageBox.No,
        )
        seen: list[bool] = []
        window.settings_page.paper_order_capability_toggled.connect(
            seen.append
        )
        box = window.settings_page.connection.paper_order_capability

        box.click()

        assert box.isChecked() is False
        assert seen == [True]
    finally:
        window.close()
        window.deleteLater()


def test_extended_hours_refusal_resets_without_a_second_intent(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(
            "us_quant.desktop.QMessageBox.warning",
            lambda *args, **kwargs: QMessageBox.No,
        )
        seen: list[bool] = []
        window.settings_page.extended_hours_paper_toggled.connect(seen.append)
        box = window.settings_page.connection.extended_hours_paper

        box.click()

        assert box.isChecked() is False
        assert seen == [True]
    finally:
        window.close()
        window.deleteLater()


def _select_api_provider(window, provider: str) -> None:
    window.settings_page.set_api_provider(provider, emit_change=True)
    _APP.processEvents()


def test_finnhub_saves_with_one_key(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        saved: list[tuple] = []
        monkeypatch.setattr(
            window.credential_service,
            "save_provider",
            lambda *args, **kwargs: saved.append((args, kwargs)),
        )
        _select_api_provider(window, "finnhub_trades")
        window.settings_page.credentials.finnhub_key.setText("key")

        window.settings_page.credentials.save_button.click()

        assert saved == [(("finnhub_trades",), {"api_key": "key", "api_secret": ""})]
        assert window.settings_page.credentials.finnhub_key.text() == ""
    finally:
        window.close()
        window.deleteLater()


def test_alpaca_requires_both_halves(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        saved: list[tuple] = []
        monkeypatch.setattr(
            window.credential_service,
            "save_provider",
            lambda *args, **kwargs: saved.append((args, kwargs)),
        )
        warnings = _capture(monkeypatch, "warning")
        _select_api_provider(window, "alpaca_iex")
        window.settings_page.credentials.alpaca_key.setText("key")

        window.settings_page.credentials.save_button.click()

        assert saved == []
        assert len(warnings) == 1
        assert "凭据不完整" in warnings[0][0][1]
    finally:
        window.close()
        window.deleteLater()


def test_empty_credential_input_is_a_no_op(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        saved: list[tuple] = []
        monkeypatch.setattr(
            window.credential_service,
            "save_provider",
            lambda *args, **kwargs: saved.append((args, kwargs)),
        )
        messages = _capture(monkeypatch, "information")

        window.settings_page.credentials.save_button.click()

        assert saved == []
        assert len(messages) == 1
        assert "没有变化" in messages[0][0][1]
    finally:
        window.close()
        window.deleteLater()


def test_ibkr_needs_no_api_key(monkeypatch, tmp_path) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        saved: list[tuple] = []
        monkeypatch.setattr(
            window.credential_service,
            "save_provider",
            lambda *args, **kwargs: saved.append((args, kwargs)),
        )
        messages = _capture(monkeypatch, "information")
        _select_api_provider(window, "ibkr")

        # IBKR has no API key, so the save button is disabled; the capability's
        # handler must still explain why when it is asked directly.
        window.settings_orchestrator.save_credentials(
            window.settings_page.current_credentials_draft()
        )

        assert saved == []
        assert len(messages) == 1
        assert "无需 API Key" in messages[0][0][1]
    finally:
        window.close()
        window.deleteLater()


def test_active_provider_credentials_cannot_be_cleared(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        cleared: list[str] = []
        monkeypatch.setattr(
            window.credential_service,
            "clear_provider",
            cleared.append,
        )
        window.market_orchestrator._worker = _RunningWorker("finnhub_trades")
        warnings = _capture(monkeypatch, "warning")
        _select_api_provider(window, "finnhub_trades")

        window.settings_page.credentials.clear_button.click()

        assert cleared == []
        assert len(warnings) == 1
        assert "行情运行中" in warnings[0][0][1]
    finally:
        window.market_orchestrator._worker = None
        window.close()
        window.deleteLater()


def test_inactive_provider_credentials_can_be_cleared(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        cleared: list[str] = []
        monkeypatch.setattr(
            window.credential_service,
            "clear_provider",
            cleared.append,
        )
        window.market_orchestrator._worker = _RunningWorker("finnhub_trades")
        _select_api_provider(window, "alpaca_iex")

        window.settings_page.credentials.clear_button.click()

        assert cleared == ["alpaca_iex"]
    finally:
        window.market_orchestrator._worker = None
        window.close()
        window.deleteLater()


# -- v2O-F2: the capability owns the view, the window owns the fan-out ----


def test_the_credential_status_line_is_reprojected_by_the_capability(
    monkeypatch, tmp_path
) -> None:
    """The page's label follows the service on every capability repaint.

    The status service is stubbed *after* the window is built, so the label must
    change without any window-side call: pointing the provider combo is a page
    intent, the capability repaints from the live status, and the new text
    appears.  The provider is moved away and back because the combo's silent
    setter only reports a real change, and the round trip is what makes the
    second repaint happen.
    """

    window = _window(monkeypatch, tmp_path)
    try:
        from us_quant.desktop_credentials import CredentialStatus

        _select_api_provider(window, "finnhub_trades")
        monkeypatch.setattr(
            window.credential_service,
            "status",
            lambda provider: CredentialStatus(
                provider=provider,
                requires_api_key=True,
                api_key_saved=True,
                api_secret_saved=False,
            ),
        )

        # No window call at all: pointing the provider combo is a page intent
        # that reaches the capability, which repaints from the live status.
        _select_api_provider(window, "alpaca_iex")
        _select_api_provider(window, "finnhub_trades")

        assert (
            window.settings_page.credentials.status_label.text()
            == "Finnhub：已加密保存"
        )
    finally:
        window.close()
        window.deleteLater()


def test_the_market_route_closes_the_connection_controls_through_the_capability(
    monkeypatch, tmp_path
) -> None:
    """A published market fact, not a window reach-through."""

    window = _window(monkeypatch, tmp_path)
    try:
        host = window.settings_page.connection.host_input
        assert host.isEnabled() is True

        window.market_orchestrator.connection_settings_enabled_changed.emit(False)

        assert window.settings_orchestrator.connection_settings_enabled is False
        assert host.isEnabled() is False

        window.market_orchestrator.connection_settings_enabled_changed.emit(True)

        assert window.settings_orchestrator.connection_settings_enabled is True
        assert host.isEnabled() is True
    finally:
        window.close()
        window.deleteLater()


def test_a_successful_save_fans_out_through_the_routes_public_api(
    monkeypatch, tmp_path
) -> None:
    """The window restores a saved provider through the market *capability*.

    The route's own renderer is what points the page's combo; the window must
    not reach into the page, which is why the fan-out goes through
    ``market_orchestrator.set_selected_provider`` and is counted here.
    """

    window = _window(monkeypatch, tmp_path)
    try:
        calls: list[str] = []
        real = window.market_orchestrator.set_selected_provider

        def spy(provider: str) -> None:
            calls.append(provider)
            real(provider)

        monkeypatch.setattr(
            window.market_orchestrator, "set_selected_provider", spy
        )
        page = window.settings_page
        combo = page.appearance.provider_combo
        combo.setCurrentIndex(combo.findData("alpaca_iex"))
        before = len(calls)

        page.save_button.click()

        assert len(calls) == before + 1, "the save fanned out exactly once"
        assert calls[-1] == "alpaca_iex"
        assert window.market_page.selected_provider() == "alpaca_iex"
        assert window.preferences.market_provider == "alpaca_iex"
    finally:
        window.close()
        window.deleteLater()


def test_an_accepted_capability_toggle_stays_a_draft(
    monkeypatch, tmp_path
) -> None:
    """Yes keeps the control checked and persists nothing until Save."""

    window = _window(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(
            "us_quant.desktop.QMessageBox.warning",
            lambda *args, **kwargs: QMessageBox.Yes,
        )
        assert window.preferences.paper_order_capability_enabled is False
        box = window.settings_page.connection.paper_order_capability

        box.click()

        assert box.isChecked() is True
        assert window.preferences.paper_order_capability_enabled is False
        assert window.safety_badge.text() == "只读 · 自动下单关闭"
    finally:
        window.close()
        window.deleteLater()


def test_an_accepted_extended_hours_toggle_stays_a_draft(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(
            "us_quant.desktop.QMessageBox.warning",
            lambda *args, **kwargs: QMessageBox.Yes,
        )
        assert window.preferences.extended_hours_paper_enabled is False
        box = window.settings_page.connection.extended_hours_paper

        box.click()

        assert box.isChecked() is True
        assert window.preferences.extended_hours_paper_enabled is False
    finally:
        window.close()
        window.deleteLater()


def test_a_failed_credential_save_keeps_the_operator_input(
    monkeypatch, tmp_path
) -> None:
    window = _window(monkeypatch, tmp_path)
    try:
        warnings = _capture(monkeypatch, "warning")

        def explode(*_args, **_kwargs):
            raise OSError("dpapi refused")

        monkeypatch.setattr(
            window.credential_service, "save_provider", explode
        )
        _select_api_provider(window, "finnhub_trades")
        window.settings_page.credentials.finnhub_key.setText("key")

        window.settings_page.credentials.save_button.click()

        assert len(warnings) == 1
        assert "凭据保存失败" in warnings[0][0][1]
        # Not cleared and not reported as saved: the input is still there to fix.
        assert window.settings_page.credentials.finnhub_key.text() == "key"
    finally:
        window.close()
        window.deleteLater()


def test_the_application_configuration_stays_on_the_composition_root(
    monkeypatch, tmp_path
) -> None:
    """``self.config`` / ``self.preferences`` are not the capability's."""

    window = _window(monkeypatch, tmp_path)
    try:
        orchestrator = window.settings_orchestrator
        assert not hasattr(orchestrator, "config")
        assert not hasattr(orchestrator, "preferences")

        window.settings_page.connection.host_input.setText("localhost")
        window.settings_page.save_button.click()

        assert window.preferences.ibkr_host == "localhost"
        assert window.config.ibkr.host == "localhost"
        # The capability still has no copy of either.
        assert not hasattr(orchestrator, "config")
        assert not hasattr(orchestrator, "preferences")
    finally:
        window.close()
        window.deleteLater()
