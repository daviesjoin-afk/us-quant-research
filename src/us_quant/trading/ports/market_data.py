"""Market data port: the boundary the runtime depends on.

The runtime asks a provider to run and reads snapshots; which vendor answers
is an adapter detail.  Nothing here names ``IBKRReadOnlyStream``,
``AlpacaIEXStream`` or ``FinnhubTradeStream``, and no credential lives here --
credentials belong to the application/composition/adapters side.

``run()`` is the provider transport's blocking loop.  Whether it executes on a
Qt thread, a plain thread or the caller's thread is decided *outside* this
port, which is why the port exposes no threading concept at all.

``MarketDataCredentialsError`` is the provider-neutral credential failure.
Each adapter converts its vendor error into it (or subclasses it), so the UI
never imports a provider-specific exception name.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from us_quant.trading.domain.market import (
    MarketDataHealth,
    MarketSnapshot,
)


class MarketDataCredentialsError(RuntimeError):
    """A provider cannot start because its credentials are missing or bad.

    Provider-neutral: the UI catches this instead of naming which vendor
    failed, because *which* provider is configured is not presentation
    knowledge.
    """


class MarketDataActiveError(RuntimeError):
    """A live stream would be disturbed by the operation.

    A ``RuntimeError`` subclass on purpose: refusing is a runtime state
    conflict, not a programming error in the caller's arguments.  It is a
    distinct class so the UI can catch *this* refusal without also swallowing
    the ``RuntimeError``s an adapter raises for a real connection failure.
    """


#: How an adapter pushes snapshots to an interested caller.  Alpaca and
#: Finnhub use it; IBKR deliberately does not (the desktop polls it), because
#: giving IBKR a listener as well would publish every quote twice.
SnapshotListener = Callable[[MarketSnapshot], None]


class MarketDataPort(Protocol):
    def run(self) -> None: ...

    def stop(self) -> None: ...

    def snapshot(self) -> MarketSnapshot: ...

    def health(self) -> MarketDataHealth: ...
