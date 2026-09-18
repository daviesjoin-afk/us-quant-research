"""Market data port.

The runtime asks for a subscription and reads snapshots; which vendor answers
is an adapter detail.  Nothing here names ``MarketDataService``,
``IBKRReadOnlyStream``, ``AlpacaIEXStream`` or ``FinnhubTradeStream``, so a
future Market Data v2 application service can sit on this protocol without
the runtime importing any provider module.
"""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.market import (
    MarketDataHealth,
    MarketSnapshot,
    MarketSubscription,
)


class MarketDataPort(Protocol):
    def start(self, subscription: MarketSubscription) -> None: ...

    def stop(self) -> None: ...

    def snapshot(self) -> MarketSnapshot: ...

    def health(self) -> MarketDataHealth: ...
