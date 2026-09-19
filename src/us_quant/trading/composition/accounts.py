"""Broker account composition root.

This is the one place allowed to know both the application service and the
concrete IBKR account adapter.  It maps the connection config onto a factory
and hands that to :class:`BrokerAccountApplication`, which never imports an
adapter itself.

Keeping the wiring here is what lets the application stay provider-blind.  A
test can substitute a fake factory without a gateway, and the dependency
arrow keeps pointing inward: composition -> application -> ports -> domain.

The factory closes over the *config object passed in at build time* on
purpose.  The application is the config owner, and it hands the factory the
current config on every refresh, so this closure is rebuilt from the
application's own value rather than caching an endpoint of its own.
"""

from __future__ import annotations

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.adapters.ibkr.account import IBKRAccountAdapter
from us_quant.trading.application.accounts import (
    BrokerAccountApplication,
)


def build_broker_account_application(
    config: IBKRConnectionConfig,
) -> BrokerAccountApplication:
    """Assemble the account application with the IBKR adapter wired."""

    application: BrokerAccountApplication

    def ibkr_adapter_factory() -> IBKRAccountAdapter:
        # Read the config from the application at call time.  Capturing the
        # ``config`` argument directly would reintroduce the stale-config
        # bug: after a settings change the next refresh would still read the
        # endpoint the application started with.
        return IBKRAccountAdapter(application.config)

    application = BrokerAccountApplication(
        config,
        adapter_factory=ibkr_adapter_factory,
    )
    return application


__all__ = ["build_broker_account_application"]
