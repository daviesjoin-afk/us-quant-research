"""Immutable DTOs for the Settings workspace.

These are presentation contracts, not application state:

* ``SettingsDraft`` and ``CredentialDraft`` are the operator's *input* read back
  off the controls.  The page never turns them into a ``UserPreferences`` or
  saves a credential -- the window owns that transaction;
* ``SettingsStorageView`` and ``SettingsPageView`` are display facts the page
  renders.  Neither can read a directory, resolve a credential or call a
  service.

This module is Qt-free and importable without a widget toolkit.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SettingsDraft:
    """The operator's current preference input; not the persisted truth."""

    theme: str
    market_provider: str

    ibkr_host: str
    ibkr_port: int
    ibkr_client_id: int
    connection_timeout_seconds: float

    paper_order_capability_enabled: bool
    extended_hours_paper_enabled: bool


@dataclass(frozen=True, slots=True)
class CredentialDraft:
    """The operator's current credential input for one provider.

    Finnhub fills only ``api_key``; Alpaca fills both; IBKR fills neither and
    the window decides that no API key is required.
    """

    provider: str
    api_key: str
    api_secret: str


@dataclass(frozen=True, slots=True)
class SettingsStorageView:
    """The four paths the storage section prints, already formatted."""

    settings_path: str
    credentials_path: str
    runtime_path: str
    exports_path: str


@dataclass(frozen=True, slots=True)
class SettingsPageView:
    """The external facts one render of the Settings page draws."""

    credential_status_text: str

    credential_save_enabled: bool
    credential_clear_enabled: bool

    connection_settings_enabled: bool


def api_provider_for_market_provider(market_provider: str) -> str:
    """The API provider a market provider's credentials belong to.

    Defined *once* because two callers need the same answer for different
    reasons: the page uses it to point its credential inputs when it is built,
    and ``SettingsOrchestrator`` uses it to keep its own selected-API-provider
    fact in step when the operator picks a market provider.  Two copies would be
    two rules, and the first one edited would be the one that is wrong.

    ``ibkr_extended`` is the 5x24 Paper route over the same IBKR Gateway, so it
    shares IBKR's (empty) API credential set rather than inventing a provider.
    """

    return "ibkr" if market_provider == "ibkr_extended" else market_provider


__all__ = [
    "CredentialDraft",
    "SettingsDraft",
    "SettingsPageView",
    "SettingsStorageView",
    "api_provider_for_market_provider",
]
