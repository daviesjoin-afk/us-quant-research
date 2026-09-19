"""Broker account application service.

This is the single runtime owner of the IBKR read-only connection settings
and of the account refresh lifecycle.  The desktop, the CLI and the preflight
all read account truth through it, so there is exactly one place that decides
when a refresh may run and exactly one place that holds the last good
snapshot.

What it owns:

* the current :class:`IBKRConnectionConfig` -- moved here from
  ``MarketDataApplication``, which held it only transitionally;
* the refresh lifecycle, including the concurrent-refresh guard;
* the last successful :class:`BrokerAccountPortfolio` and the last error.

What it deliberately does not own: Qt, market data, Paper orders, strategy,
risk calculations.  It imports the domain and the ports and nothing else --
no concrete adapter, so construction is injected as a factory by
``trading.composition.accounts``.

Three safety properties are load-bearing:

* **a failed refresh never erases good truth.**  The previous snapshot stays
  available for display, but ``last_error`` is set, so the operator can see
  the data is not fresh.
* **a failed refresh never restamps the timestamp.**  Re-dating an old
  snapshot would disguise a stale account as a fresh one and walk straight
  through the preflight's 300-second freshness gate.  The stored snapshot is
  immutable, so its ``observed_at`` is the moment it was actually read.
* **a refresh in progress blocks a config change.**  The adapter is
  connected with the config it was built from; swapping the endpoint
  mid-flight would leave the running read and the saved settings describing
  different gateways.
"""

from __future__ import annotations

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.domain.account import BrokerAccountPortfolio
from us_quant.trading.ports.broker_account import (
    BrokerAccountActiveError,
    BrokerAccountAdapterFactory,
)


class BrokerAccountApplication:
    """Owns the account connection config and the refresh lifecycle."""

    def __init__(
        self,
        config: IBKRConnectionConfig,
        *,
        adapter_factory: BrokerAccountAdapterFactory,
    ) -> None:
        self._config = config
        self._adapter_factory = adapter_factory
        self._portfolio: BrokerAccountPortfolio | None = None
        self._last_error: str | None = None
        self._refresh_active = False

    # -- connection config ----------------------------------------------

    @property
    def config(self) -> IBKRConnectionConfig:
        """The config future refreshes will use.

        Read by the market-data composition so its IBKR adapter is built with
        the current endpoint instead of a captured start-up copy.
        """

        return self._config

    def ensure_config_update_allowed(
        self,
        config: IBKRConnectionConfig,
    ) -> None:
        """Raise if ``config`` could not be applied right now.

        Split out of :meth:`update_config` so a caller can check *before* it
        commits to anything irreversible -- saving settings must know the
        change will be accepted before it writes the file.

        Checks only: no state change, no I/O.  An identical config is always
        allowed, so re-saving unchanged settings is not blocked by an
        unrelated in-flight refresh.
        """

        if config == self._config:
            return
        if self._refresh_active:
            raise BrokerAccountActiveError(
                "cannot change the IBKR connection config while an account "
                "refresh is running: wait for it to finish"
            )

    def update_config(self, config: IBKRConnectionConfig) -> None:
        """Replace the config used by future refreshes.

        Fails closed while a refresh is running.  Deliberately inert
        otherwise: no network call, no reconnect, no refresh.
        """

        self.ensure_config_update_allowed(config)
        self._config = config

    # -- refresh --------------------------------------------------------

    def refresh(
        self,
        *,
        timeout_seconds: float = 20,
    ) -> BrokerAccountPortfolio:
        """Read account truth once and commit it on success.

        Raises ``BrokerAccountActiveError`` if a refresh is already running:
        a second concurrent read would open another client-id socket and race
        the first to publish account truth.

        On failure the exception propagates unchanged and the previous
        snapshot is left exactly as it was -- same object, same
        ``observed_at`` -- while ``last_error`` records what went wrong.
        """

        if self._refresh_active:
            raise BrokerAccountActiveError(
                "an account refresh is already running"
            )
        self._refresh_active = True
        try:
            adapter = self._adapter_factory()
            portfolio = adapter.refresh(timeout_seconds=timeout_seconds)
        except Exception as error:
            self._last_error = f"{type(error).__name__}: {error}"
            raise
        else:
            # Commit only after a complete read.  The snapshot is stored as
            # returned, timestamp included: nothing here re-dates it.
            self._portfolio = portfolio
            self._last_error = None
            return portfolio
        finally:
            self._refresh_active = False

    # -- queries --------------------------------------------------------

    @property
    def portfolio(self) -> BrokerAccountPortfolio | None:
        """The last successful snapshot, or ``None`` if there has never been one."""

        return self._portfolio

    @property
    def last_error(self) -> str | None:
        """Why the most recent refresh failed, cleared by the next success."""

        return self._last_error

    @property
    def refresh_active(self) -> bool:
        """Whether a refresh is running right now."""

        return self._refresh_active


__all__ = ["BrokerAccountApplication"]
