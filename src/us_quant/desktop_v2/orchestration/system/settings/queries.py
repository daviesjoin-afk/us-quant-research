"""Pure rules and projections for the Settings capability.

Qt-free, service-free, I/O-free: everything here is a function of its arguments.
That is what makes the four decisions the Settings workspace actually makes
testable without a window -- which credential action a click means, what the
status line says, which API provider a market provider maps to, and what the
page is built from.

What is deliberately **not** here: reading the credential store, committing
preferences, or deciding whether a live stream may change.  Those belong to
``DesktopCredentialService``, ``DesktopSettingsService`` and the transaction
order they already define; this module only answers questions about values it
was handed.
"""

from __future__ import annotations

from pathlib import Path

from us_quant.desktop_credentials import (
    API_KEY_PROVIDERS,
    CredentialStatus,
)
from us_quant.desktop_v2.pages.system.settings.models import (
    SettingsDraft,
    SettingsStorageView,
)
from us_quant.user_settings import UserPreferences

from .models import (
    CredentialSaveOutcome,
    CredentialSavePlan,
)

#: The two providers that store an API key here, and the one that does not.
PROVIDER_FINNHUB = "finnhub_trades"
PROVIDER_ALPACA = "alpaca_iex"

#: Operator-facing credential provider labels, frozen with the status line.
CREDENTIAL_PROVIDER_LABELS: dict[str, str] = {
    PROVIDER_FINNHUB: "Finnhub",
    PROVIDER_ALPACA: "Alpaca",
}


def provider_requires_api_key(provider: str) -> bool:
    """Whether this provider keeps an API credential in the local store.

    The set itself lives in ``DesktopCredentialService`` (it is the canonical
    owner of which credential belongs to which provider); this is the
    capability's read of it, so the page's "save enabled" fact and the clear
    rules cannot grow a second copy of the list.
    """

    return provider in API_KEY_PROVIDERS


def provider_label(provider: str) -> str:
    """The label the clear-success log line names."""

    return CREDENTIAL_PROVIDER_LABELS.get(provider, provider)


def credential_save_plan(
    provider: str,
    *,
    api_key: str,
    api_secret: str,
) -> CredentialSavePlan:
    """Resolve one Save-credentials click into an outcome and its payload.

    Semantics are the retired handler's, verbatim: blank input is "no change"
    rather than an error, a half-filled Alpaca pair is refused outright, and a
    provider that stores no key here is explained rather than written.  Trimming
    happens here, once, so the value that is written is the value that was
    judged complete.
    """

    if not provider_requires_api_key(provider):
        return CredentialSavePlan(
            provider=provider, outcome=CredentialSaveOutcome.NOT_REQUIRED
        )

    trimmed_key = api_key.strip()
    trimmed_secret = api_secret.strip()
    if provider == PROVIDER_ALPACA:
        if not trimmed_key and not trimmed_secret:
            return CredentialSavePlan(
                provider=provider, outcome=CredentialSaveOutcome.NO_CHANGE
            )
        if not trimmed_key or not trimmed_secret:
            return CredentialSavePlan(
                provider=provider, outcome=CredentialSaveOutcome.INCOMPLETE
            )
        return CredentialSavePlan(
            provider=provider,
            outcome=CredentialSaveOutcome.SAVE,
            api_key=trimmed_key,
            api_secret=trimmed_secret,
        )

    if not trimmed_key:
        return CredentialSavePlan(
            provider=provider, outcome=CredentialSaveOutcome.NO_CHANGE
        )
    return CredentialSavePlan(
        provider=provider,
        outcome=CredentialSaveOutcome.SAVE,
        api_key=trimmed_key,
    )


def credential_status_text(status: CredentialStatus) -> str:
    """The frozen status line for one credential status fact.

    It reads a :class:`CredentialStatus`, never the store: asking "is a secret
    saved?" is the service's job and it answers with presence only, so the
    projection cannot decrypt anything even by accident.
    """

    if status.provider == PROVIDER_FINNHUB:
        return (
            "Finnhub：已加密保存"
            if status.api_key_saved
            else "Finnhub：未保存"
        )
    if status.provider == PROVIDER_ALPACA:
        return (
            "Alpaca Key："
            f"{'已加密保存' if status.api_key_saved else '未保存'}"
            " · Alpaca Secret："
            f"{'已加密保存' if status.api_secret_saved else '未保存'}"
        )
    return (
        "IBKR Gateway：使用本机 Host / 端口 / Client ID，"
        "无需 API Key"
    )


def preferences_from_draft(draft: SettingsDraft) -> UserPreferences:
    """The ``UserPreferences`` one settings draft describes.

    The only mapping from the page's draft to the persisted type, so the
    transaction owner is handed a finished value rather than being taught the
    widget layout.  Validation is deliberately *not* repeated here:
    ``UserPreferences.validated()`` is the one validator, and it runs inside
    ``DesktopSettingsService.commit``.
    """

    return UserPreferences(
        theme=draft.theme,
        market_provider=draft.market_provider,
        ibkr_host=draft.ibkr_host,
        ibkr_port=draft.ibkr_port,
        ibkr_client_id=draft.ibkr_client_id,
        connection_timeout_seconds=draft.connection_timeout_seconds,
        paper_order_capability_enabled=(
            draft.paper_order_capability_enabled
        ),
        extended_hours_paper_enabled=draft.extended_hours_paper_enabled,
    )


def settings_draft_from_preferences(
    preferences: UserPreferences,
) -> SettingsDraft:
    """The draft the Settings page is built from, field for field."""

    return SettingsDraft(
        theme=preferences.theme,
        market_provider=preferences.market_provider,
        ibkr_host=preferences.ibkr_host,
        ibkr_port=preferences.ibkr_port,
        ibkr_client_id=preferences.ibkr_client_id,
        connection_timeout_seconds=(
            preferences.connection_timeout_seconds
        ),
        paper_order_capability_enabled=(
            preferences.paper_order_capability_enabled
        ),
        extended_hours_paper_enabled=(
            preferences.extended_hours_paper_enabled
        ),
    )


def settings_storage_view(
    *,
    state_root: Path,
    runtime_root: Path,
    exports_root: Path,
) -> SettingsStorageView:
    """The four paths the storage section prints, already formatted.

    A projection of the paths the composition root owns -- the capability never
    reaches for ``ApplicationPaths``, and the window no longer formats strings.
    """

    return SettingsStorageView(
        settings_path=str(state_root / "settings"),
        credentials_path=str(state_root / "credentials"),
        runtime_path=str(runtime_root),
        exports_path=str(exports_root),
    )


__all__ = [
    "CREDENTIAL_PROVIDER_LABELS",
    "PROVIDER_ALPACA",
    "PROVIDER_FINNHUB",
    "credential_save_plan",
    "credential_status_text",
    "preferences_from_draft",
    "provider_label",
    "provider_requires_api_key",
    "settings_draft_from_preferences",
    "settings_storage_view",
]
