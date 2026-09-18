"""Qt worker threads used by the desktop shell.

These two adapters are the only place where a background job becomes a
``QThread``.  They are kept apart from ``desktop.py`` so that the window
module owns layout and wiring, and the threading plumbing can be read --
and tested -- without importing a 10k-line GUI module.

Both classes are re-exported from :mod:`us_quant.desktop`, so
``from us_quant.desktop import TaskThread`` keeps working and yields the
*same object* defined here.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThread, Signal

from us_quant.trading.application.market_data import (
    MarketDataApplication,
    MarketDataStartRequest,
)
from us_quant.universe import UniverseRefreshCancelled


class TaskThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(str)

    def __init__(
        self,
        task: Callable[[Callable[[str], None]], object],
        resource_group: str = "research",
    ) -> None:
        super().__init__()
        self.task = task
        self.resource_group = resource_group

    def run(self) -> None:
        try:
            result = self.task(self.progress.emit)
        except UniverseRefreshCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(" ".join(str(error).split()))
        else:
            self.succeeded.emit(result)


class StreamWorker(QThread):
    """Qt thread adapter over the market data application.

    This class owns *threading only*: it runs the feed, carries its snapshots
    across the thread boundary and forwards the stop request.  Which provider
    to construct, with which credentials, timeouts, venue and labels is decided
    by :class:`MarketDataApplication`; the worker never inspects the source id
    to build anything, and it never holds the adapter.

    The worker asks the application to prepare the feed (rather than being
    handed an adapter) so that the push listener is ``snapshot_ready.emit``: a
    signal emitted from this thread is delivered *queued* to the GUI thread,
    and a listener that called the desktop directly would touch widgets from
    the stream thread.
    """

    snapshot_ready = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        market_data: MarketDataApplication,
        request: MarketDataStartRequest,
    ) -> None:
        super().__init__()
        self.source_id = request.source_id
        # The application that owns the feed, so the worker can hand the stop
        # request and any runtime failure back through it instead of reaching
        # for the adapter directly.
        self.market_data = market_data
        market_data.prepare(request, listener=self.snapshot_ready.emit)
        # Mirrors the venue the feed was built for; the desktop compares it
        # against the application's current session venue to decide whether an
        # extended-hours stream has to be rotated.
        self.market_exchange = market_data.market_exchange_for(request)

    def run(self) -> None:
        # ``market_data.run()`` owns the lifecycle half -- it marks the feed
        # finished and records the failure itself, so this only has to carry
        # the message to the GUI thread.
        try:
            self.market_data.run()
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")

    def request_stop(self) -> None:
        self.market_data.stop()
