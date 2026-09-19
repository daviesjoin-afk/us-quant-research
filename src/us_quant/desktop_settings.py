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

*The preflight is deliberately before the write.*  While a stream is live the
market data application refuses to be reconfigured, and while an account
refresh is running the account application refuses to change its endpoint;
saving first would leave the settings file and the live runtime disagreeing
about the port and the client id -- the operator would be told the change was
saved while the next stream still used the old values.

*The write is the commit point.*  The runtime only moves once the file is
on disk, so a failed write cannot leave ``self.config`` and the services
adopted to values the operator was just told were not saved.  A refused
change and a failed write therefore leave nothing behind, and neither one
can leave the two views disagreeing.

*The runtime guards only run when the connection actually changed.*  An
unchanged IBKR config is a no-op, so re-saving an unrelated setting such as
the theme still works while a stream is live.  Checking unconditionally
would make the theme depend on market-data state, which is exactly the
coupling this architecture removes.

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
* no concrete application import.  The two Protocols below describe what is
  asked of the account owner and of any runtime that must be quiescent
  before a connection change.  A test can satisfy them with a recorder, and
  neither application can grow a method without this file noticing;
* no credential store, no Paper stack and no provider adapter.  It knows
  about *connection parameters*, not about how a stream is built or how
  an order is placed;
* exceptions propagate unchanged.  ``UserSettingsError``,
  ``MarketDataActiveError`` and ``BrokerAccountActiveError`` are the
  caller's to report, and translating one into another here would hide
  which of the failure modes actually happened.
"""

from __future__ import annotations

from collections.abc import Sequence
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


class BrokerConfigOwnerPort(Protocol):
    """The slice of :class:`BrokerAccountApplication` this module depends on.

    This is the runtime owner of the IBKR connection settings, so it is the
    thing that must be asked before the settings file is rewritten.
    Structural on purpose: the real application satisfies it without
    inheriting anything, and a test can satisfy it with a config attribute
    and a counter.
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


class RuntimeReconfigurationGuard(Protocol):
    """A runtime that must be idle before the connection may change.

    Deliberately narrower than :class:`BrokerConfigOwnerPort`: a guard does
    not own the config and does not need to know what the new one is.  It
    only answers "may anything about the connection change right now?".
    ``MarketDataApplication`` satisfies it with
    ``ensure_reconfiguration_allowed``.
    """

    def ensure_reconfiguration_allowed(self) -> None: ...


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
    account or market-data lifecycle -- it is handed the current runtime
    participants per call, and only when there are any (``broker_config``
    and ``runtime_guards`` default to "nothing to check", which is the
    start-up case where saving must still validate and persist).
    """

    def __init__(self, store: UserPreferencesStore) -> None:
        self.store = store

    def commit(
        self,
        preferences: UserPreferences,
        *,
        current_config: AppConfig,
        broker_config: BrokerConfigOwnerPort | None,
        runtime_guards: Sequence[RuntimeReconfigurationGuard] = (),
    ) -> DesktopSettingsCommit:
        """Persist ``preferences`` and return the config to adopt.

        Raises ``UserSettingsError`` for invalid preferences or a failed
        write, ``BrokerAccountActiveError`` if an account refresh is running,
        and ``MarketDataActiveError`` if a live stream refuses the change.
        All of them leave the disk, the runtime and ``current_config``
        untouched.
        """

        safe = preferences.validated()
        ibkr = ibkr_config_from_preferences(safe)

        # The comparison is made against the account application, which owns
        # the connection settings.  An unchanged config makes the apply a
        # no-op, so saving an unrelated setting (the theme) still works while
        # a stream is live.
        changed = broker_config is None or broker_config.config != ibkr

        # Both preflights run *before* persisting, and deliberately so -- see
        # the module docstring.  The account check runs first because it is
        # the config owner: if it refuses, the config cannot be applied at all
        # and there is no point asking the stream guards.
        #
        # The account check is made unconditionally.  It is the owner's own
        # check and returns immediately for an identical config, so re-saving
        # unchanged settings is never blocked.  The stream guards are called
        # only for a *real* change: a guard can only answer "may anything
        # change right now?", so asking it about an unchanged config would
        # block an unrelated theme save for no reason.  Every check only
        # checks: no state change, no I/O.
        if broker_config is not None:
            broker_config.ensure_config_update_allowed(ibkr)
        if changed:
            for guard in runtime_guards:
                guard.ensure_reconfiguration_allowed()

        saved = self.store.save(safe)

        # Only once the file is on disk does the runtime move.
        if changed and broker_config is not None:
            broker_config.update_config(ibkr)

        return DesktopSettingsCommit(
            preferences=saved,
            config=replace(current_config, ibkr=ibkr),
        )
