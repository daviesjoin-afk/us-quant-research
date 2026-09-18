"""The desktop settings transaction, without a widget in sight.

Saving settings used to be one method on ``MainWindow`` that did four
unrelated things in a row: read the widgets, validate the values, write
the preferences file, and move the running market data service onto the
new connection config.  Three of those four are not presentation, and the
order they happen in is a safety property rather than a style choice, so
they live here now.

The order is the whole point::

    validate  ->  derive the IBKR config  ->  preflight  ->  persist
                                                          ->  apply to runtime

*The preflight is deliberately before the write.*  While a stream is live
:class:`MarketDataService` refuses to change its connection config, and
saving first would leave the settings file and the live service
disagreeing about the port and the client id -- the operator would be
told the change was saved while the next stream still used the old
values.

*The write is the commit point.*  The runtime only moves once the file is
on disk, so a failed write cannot leave ``self.config`` and the service
adopted to values the operator was just told were not saved.  A refused
change and a failed write therefore leave nothing behind, and neither one
can leave the two views disagreeing.

Nothing here is transactional in a stronger sense.  There is no rollback,
no backup of the previous file, no lock and no two-phase commit -- the
existing order already makes both failure modes inert, and inventing a
framework for the remaining theoretical race between the check and the
apply would be a separate change with its own tests.

Design constraints (deliberate, kept small on purpose):

* no GUI toolkit: no ``PySide6``, no ``QMessageBox``, no widget.  The
  caller reads its own controls and shows its own dialog; this module
  returns a value or raises;
* no ``desktop`` import, so the direction of the dependency stays
  ``desktop -> desktop_settings`` and this module can be unit tested
  without constructing a ``MainWindow``;
* no credential store, no Paper stack and no provider adapter.  It knows
  about *connection parameters*, not about how a stream is built or how
  an order is placed;
* :class:`MarketDataConfigPort` describes the three things the service is
  asked for -- its ``config``, the check and the apply -- so this module
  never imports :class:`MarketDataService` itself.  The service can grow
  a method without this file noticing, and a test can substitute a
  recorder without a live stream;
* exceptions propagate unchanged.  ``UserSettingsError`` and
  ``MarketDataStreamActive`` are the caller's to report, and translating
  one into the other here would hide which of the two failure modes
  actually happened.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from us_quant.config import AppConfig
from us_quant.ibkr import IBKRConnectionConfig
from us_quant.user_settings import UserPreferences, UserPreferencesStore


@dataclass(frozen=True, slots=True)
class DesktopSettingsCommit:
    """What a successful settings save produced.

    ``preferences`` is what the store wrote -- its own validated copy, not
    the caller's argument, because the store normalises the host and pins
    the port.  ``config`` is the ``AppConfig`` the caller should adopt.
    """

    preferences: UserPreferences
    config: AppConfig


class MarketDataConfigPort(Protocol):
    """The slice of :class:`MarketDataService` this module depends on.

    Structural on purpose: a real service satisfies it without inheriting
    anything, and a test can satisfy it with three attributes and a
    counter.
    """

    config: IBKRConnectionConfig

    def ensure_config_update_allowed(
        self,
        config: IBKRConnectionConfig,
    ) -> None: ...

    def update_config(
        self,
        config: IBKRConnectionConfig,
    ) -> None: ...


def ibkr_config_from_preferences(
    preferences: UserPreferences,
) -> IBKRConnectionConfig:
    """The IBKR connection config ``preferences`` describe.

    The only mapping from saved preferences to a connection config, so
    start-up and a later save cannot drift into two sets of rules.

    ``api_read_only`` and ``paper_order_submission_enabled`` are hard-coded
    rather than derived from ``preferences``.  They are a different safety
    layer from ``paper_order_capability_enabled``, which says the operator
    *may* be offered Paper order controls; it must never turn broker
    submission on.  The Paper order service is built by its own dedicated
    path from its own config, and this one stays read-only.
    """

    return IBKRConnectionConfig(
        host=preferences.ibkr_host,
        port=preferences.ibkr_port,
        client_id=preferences.ibkr_client_id,
        api_read_only=True,
        paper_order_submission_enabled=False,
        connection_timeout_seconds=(
            preferences.connection_timeout_seconds
        ),
    )


class DesktopSettingsService:
    """Validate, persist and apply the operator's settings.

    Holds the preferences store and nothing else.  It does not own the
    market data service's lifecycle -- it is handed one per call, and only
    when there is one to hand (``market_data=None`` is the start-up case,
    where saving must still validate and persist).
    """

    def __init__(self, store: UserPreferencesStore) -> None:
        self.store = store

    def commit(
        self,
        preferences: UserPreferences,
        *,
        current_config: AppConfig,
        market_data: MarketDataConfigPort | None,
    ) -> DesktopSettingsCommit:
        """Persist ``preferences`` and return the config to adopt.

        Raises ``UserSettingsError`` for invalid preferences or a failed
        write, and ``MarketDataStreamActive`` if a live stream refuses the
        connection change.  Both leave the disk, the runtime and
        ``current_config`` untouched.
        """

        safe = preferences.validated()
        ibkr = ibkr_config_from_preferences(safe)

        # Checked *before* persisting, and deliberately so -- see the
        # module docstring.  ``ensure_config_update_allowed`` only checks:
        # it changes no state and performs no I/O.
        if market_data is not None:
            market_data.ensure_config_update_allowed(ibkr)

        saved = self.store.save(safe)

        # Only once the file is on disk does the runtime move.  An
        # unchanged config is a no-op rather than a spurious refusal, so
        # re-saving while a stream is live still works for unrelated
        # settings such as the theme.
        if market_data is not None and market_data.config != ibkr:
            market_data.update_config(ibkr)

        return DesktopSettingsCommit(
            preferences=saved,
            config=replace(current_config, ibkr=ibkr),
        )
