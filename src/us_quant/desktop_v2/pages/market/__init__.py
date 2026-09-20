"""Desktop UI v2 market route.

The market route is the observability and subscription entry point: it shows what
the feed is doing and lets the operator choose a source and a subscription
subset.  It is not a trading surface and holds no order path.

``MarketPage``
    the page itself: widgets, layout, render and intent signals.
``presenter`` / ``rows``
    the Qt-free projection from already-computed facts into display values.
``tables``
    the incremental quote grid.
``controls``
    the subscription and provider strip.
"""

from __future__ import annotations

from us_quant.desktop_v2.pages.market.page import MarketPage

__all__ = ["MarketPage"]
