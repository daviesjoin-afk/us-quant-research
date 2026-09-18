"""API credential management, without a widget in sight.

``MainWindow`` used to own three jobs at once: it knew the provider to
secret-name mapping, it knew the ``.dpapi`` filenames, and it knew that
environment variables win over the store.  The first two are storage
detail; the third is a rule about *which* credential is used.

They move here.  What stays in ``MainWindow`` is everything that needs a
widget: which field is visible, which button is enabled, what the status
line says, and whether a live stream forbids clearing a credential.

The provider ids are spelled out as literals rather than imported from
``market_data_service``: this module needs three strings, not the whole
adapter dependency graph.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from typing import Protocol

from us_quant.credential_store import CredentialStoreError


PROVIDER_FINNHUB_TRADES = "finnhub_trades"
PROVIDER_ALPACA_IEX = "alpaca_iex"
PROVIDER_IBKR = "ibkr"
PROVIDER_IBKR_EXTENDED = "ibkr_extended"

# provider -> (secret name, environment variable name).  This mapping is
# defined once: ``MainWindow`` no longer keeps its own secret-name list.
STREAM_CREDENTIAL_SOURCES: dict[str, tuple[tuple[str, str], ...]] = {
    PROVIDER_FINNHUB_TRADES: (("finnhub_api_key", "FINNHUB_API_KEY"),),
    PROVIDER_ALPACA_IEX: (
        ("alpaca_api_key", "APCA_API_KEY_ID"),
        ("alpaca_api_secret", "APCA_API_SECRET_KEY"),
    ),
    PROVIDER_IBKR: (),
    PROVIDER_IBKR_EXTENDED: (),
}

# The same table read the other way round, so resolving a credential is a
# lookup rather than a search that has to invent a "cannot happen" error.
ENVIRONMENT_NAMES: dict[str, str] = {
    name: environment_name
    for sources in STREAM_CREDENTIAL_SOURCES.values()
    for name, environment_name in sources
}

# Providers that store an API key at all.  IBKR uses host/port/client id.
API_KEY_PROVIDERS = frozenset(
    provider
    for provider, sources in STREAM_CREDENTIAL_SOURCES.items()
    if sources
)


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    """Whether the operator has credentials saved.  Never the values."""

    provider: str
    requires_api_key: bool
    api_key_saved: bool
    api_secret_saved: bool


@dataclass(frozen=True, slots=True)
class StreamCredentials:
    """The three credentials a market-data request may carry.

    Empty string means "not saved", which is the contract
    ``MarketDataRequest`` and the provider adapters already rely on.
    """

    finnhub_api_key: str
    alpaca_api_key: str
    alpaca_api_secret: str


class CredentialStorePort(Protocol):
    def save_secret(self, name: str, secret: str) -> object: ...

    def load_secret(self, name: str) -> str | None: ...

    def delete_secret(self, name: str) -> None: ...

    def has_secret(self, name: str) -> bool: ...


class DesktopCredentialService:
    """The credential half of the Settings tab, minus the widgets."""

    def __init__(self, store: CredentialStorePort) -> None:
        self.store = store

    # -- writing ---------------------------------------------------------

    def save_provider(
        self,
        provider: str,
        *,
        api_key: str = "",
        api_secret: str = "",
    ) -> None:
        """Save the credentials for one provider, or refuse.

        Finnhub saves its key.  Alpaca saves both halves or neither --
        a half-saved Alpaca pair would look configured while failing at
        stream start.  IBKR uses no API key, so it writes nothing.
        """

        try:
            sources = STREAM_CREDENTIAL_SOURCES[provider]
        except KeyError:
            raise ValueError(
                f"unsupported credential provider: {provider!r}"
            ) from None
        if not sources:
            raise ValueError("provider does not use API credentials")
        if len(sources) == 1:
            if not api_key:
                raise ValueError("API Key 不能为空")
            self.store.save_secret(sources[0][0], api_key)
            return
        if len(sources) == 2:
            if not api_key or not api_secret:
                raise ValueError(
                    "Alpaca 需要同时提供 API Key 和 API Secret"
                )
            self.store.save_secret(sources[0][0], api_key)
            self.store.save_secret(sources[1][0], api_secret)
            return
        raise ValueError(
            f"unsupported credential shape for provider: {provider!r}"
        )

    def clear_provider(self, provider: str) -> None:
        """Delete the credentials for one provider.

        IBKR fails closed rather than guessing: it has nothing stored
        here, so there is nothing to delete.
        """

        try:
            sources = STREAM_CREDENTIAL_SOURCES[provider]
        except KeyError:
            raise ValueError(
                f"unsupported credential provider: {provider!r}"
            ) from None
        if not sources:
            raise ValueError("provider does not use API credentials")
        for name, _environment_name in sources:
            self.store.delete_secret(name)

    # -- reading ---------------------------------------------------------

    def status(self, provider: str) -> CredentialStatus:
        """Report presence only -- never decrypt a blob to answer this.

        ``load_secret`` is deliberately not called: a blob written by
        another Windows user, or a corrupt one, would turn a simple
        "saved / not saved" line into an error.
        """

        try:
            sources = STREAM_CREDENTIAL_SOURCES[provider]
        except KeyError:
            raise ValueError(
                f"unsupported credential provider: {provider!r}"
            ) from None
        if not sources:
            return CredentialStatus(
                provider=provider,
                requires_api_key=False,
                api_key_saved=False,
                api_secret_saved=False,
            )
        return CredentialStatus(
            provider=provider,
            requires_api_key=True,
            api_key_saved=self.store.has_secret(sources[0][0]),
            api_secret_saved=(
                self.store.has_secret(sources[1][0])
                if len(sources) > 1
                else False
            ),
        )

    def resolve_stream_credentials(
        self,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> StreamCredentials:
        """The credentials a stream start should use, each resolved alone.

        Environment first, store second, per credential: an environment
        variable with a non-empty value means the corresponding blob is
        never read.  A whitespace-only variable counts as unset.
        """

        source = os.environ if environment is None else environment
        finnhub_sources = STREAM_CREDENTIAL_SOURCES[
            PROVIDER_FINNHUB_TRADES
        ]
        alpaca_sources = STREAM_CREDENTIAL_SOURCES[PROVIDER_ALPACA_IEX]
        return StreamCredentials(
            finnhub_api_key=self._resolve(
                source, finnhub_sources[0][0]
            ),
            alpaca_api_key=self._resolve(source, alpaca_sources[0][0]),
            alpaca_api_secret=self._resolve(
                source, alpaca_sources[1][0]
            ),
        )

    def _resolve(self, source: Mapping[str, str], name: str) -> str:
        environment_value = source.get(
            ENVIRONMENT_NAMES[name], ""
        ).strip()
        if environment_value:
            return environment_value
        try:
            return self.store.load_secret(name) or ""
        except CredentialStoreError as error:
            # ``_start_stream`` catches ``ValueError``, not
            # ``CredentialStoreError``: the conversion is part of the
            # contract this service has to keep.
            raise ValueError(str(error)) from error
