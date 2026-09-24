"""The provider-switch sequencing defect v2O-F2 found and fixed.

Before this round ``MainWindow._switch_to_settings_provider`` was:

    provider = draft.market_provider
    self._save_user_preferences(draft)          # returns None on every path
    if not self.market_orchestrator.subscription_symbols():
        self.market_orchestrator.set_selected_provider(provider)
        self._log("默认行情源已切换；当前没有订阅代码。…")
        return
    self._request_market_switch(provider)

``_save_user_preferences`` returns nothing, so a *refused* commit still fell
through into the market path.  Reproduced on the pre-fix tree with a commit that
raises ``UserSettingsError`` (evidence captured before any F2 edit):

    warnings  : [('设置未保存', '凭据/设置被拒绝')]
    switched  : ['alpaca_iex']                    # with a subscription
    preference: finnhub_trades                    # still the saved value
    selected  : ['alpaca_iex']                    # without a subscription
    log       : ['默认行情源已切换；当前没有订阅代码。…']

So a live feed was reconfigured to a provider whose preference had never been
written, and the operator was told the default provider had changed while the
file on disk still said otherwise.

The fix is in the canonical sequencing owner, not the window:
``SettingsOrchestrator.request_provider_switch`` publishes ``settings_committed``
and then ``market_switch_requested`` *only* after the commit returned, so the
composition root has adopted the new preferences before the market route reacts.

These tests drive the real window and stay red if that ordering regresses.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dataclasses

from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.paths import STATE_ROOT_ENV
from us_quant.user_settings import UserSettingsError


_APP = QApplication.instance() or QApplication([])


def _window(monkeypatch, tmp_path) -> MainWindow:
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path / "state"))
    window = MainWindow()
    _APP.processEvents()
    return window


def _capture_warnings(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.warning",
        lambda *args, **kwargs: calls.append(args),
    )
    return calls


def _refusing_commit(monkeypatch, window) -> None:
    def commit(*_args, **_kwargs):
        raise UserSettingsError("设置被拒绝")

    monkeypatch.setattr(window.settings_service, "commit", commit)


def _wanted_draft(window) -> object:
    return dataclasses.replace(
        window.settings_page.current_draft(),
        market_provider="alpaca_iex",
    )


def test_a_refused_save_does_not_switch_a_live_provider(
    monkeypatch, tmp_path
) -> None:
    """With a subscription the refused save must not reach request_switch."""

    window = _window(monkeypatch, tmp_path)
    try:
        warnings = _capture_warnings(monkeypatch)
        _refusing_commit(monkeypatch, window)
        switched: list[str] = []
        monkeypatch.setattr(
            window.market_orchestrator, "request_switch", switched.append
        )
        window.market_orchestrator.set_subscription_symbols(("SPY",))

        window.settings_orchestrator.request_provider_switch(_wanted_draft(window))

        assert len(warnings) == 1
        assert warnings[0][1] == "设置未保存"
        assert switched == [], "a refused save reconfigured the live feed"
        assert window.preferences.market_provider == "finnhub_trades"
    finally:
        window.close()
        window.deleteLater()


def test_a_refused_save_does_not_move_the_default_provider(
    monkeypatch, tmp_path
) -> None:
    """Without a subscription the refused save must not point the route."""

    window = _window(monkeypatch, tmp_path)
    try:
        warnings = _capture_warnings(monkeypatch)
        _refusing_commit(monkeypatch, window)
        selected: list[str] = []
        monkeypatch.setattr(
            window.market_orchestrator,
            "set_selected_provider",
            selected.append,
        )
        logs: list[str] = []
        monkeypatch.setattr(window, "_log", logs.append)

        window.settings_orchestrator.request_provider_switch(_wanted_draft(window))

        assert len(warnings) == 1
        assert selected == [], "a refused save moved the default provider"
        assert logs == [], "a refused save claimed the default provider changed"
        assert window.preferences.market_provider == "finnhub_trades"
    finally:
        window.close()
        window.deleteLater()


def test_an_accepted_save_still_switches(monkeypatch, tmp_path) -> None:
    """The control case: the refusal above is not achieved by removing the path."""

    window = _window(monkeypatch, tmp_path)
    try:
        warnings = _capture_warnings(monkeypatch)
        switched: list[str] = []
        monkeypatch.setattr(
            window.market_orchestrator, "request_switch", switched.append
        )
        window.market_orchestrator.set_subscription_symbols(("SPY",))

        window.settings_orchestrator.request_provider_switch(_wanted_draft(window))

        assert warnings == []
        assert switched == ["alpaca_iex"]
        # The commit was adopted before the route reacted, so a switch that
        # rebuilds the feed reads the new preferences.
        assert window.preferences.market_provider == "alpaca_iex"
    finally:
        window.close()
        window.deleteLater()


def test_the_switch_request_is_published_after_the_commit_fact(
    monkeypatch, tmp_path
) -> None:
    """Order, on the real composition: adopt first, then ask for the switch.

    The assertion samples the *adopted* preference from inside the switch, so it
    measures the interleaving rather than only the two facts: a switch emitted
    before the commit fact would arrive while ``self.preferences`` still held the
    old provider, and a feed rebuilt at that moment would read stale settings.
    """

    window = _window(monkeypatch, tmp_path)
    try:
        _capture_warnings(monkeypatch)
        adopted: list[str] = []
        switched: list[tuple[str, str]] = []

        window.settings_orchestrator.settings_committed.connect(
            lambda _commit: adopted.append(
                window.preferences.market_provider
            )
        )

        def record_switch(provider: str) -> None:
            switched.append((provider, window.preferences.market_provider))

        monkeypatch.setattr(
            window.market_orchestrator, "request_switch", record_switch
        )
        window.market_orchestrator.set_subscription_symbols(("SPY",))

        window.settings_orchestrator.request_provider_switch(_wanted_draft(window))

        assert adopted == ["alpaca_iex"]
        assert switched == [("alpaca_iex", "alpaca_iex")], (
            "the route was asked to switch before the commit was adopted"
        )
    finally:
        window.close()
        window.deleteLater()
