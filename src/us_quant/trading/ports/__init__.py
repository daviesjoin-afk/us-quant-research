"""Ports: the boundaries the trading runtime depends on.

Every protocol here is expressed in ``trading.domain`` types only.  A port may
import the standard library, ``typing`` / ``collections.abc`` and
``us_quant.trading.domain.*`` -- nothing else.  In particular it must not
import a concrete adapter (IBKR, Alpaca, Finnhub), Qt, or SQL.

That restriction is what makes the dependency arrows point inwards: the
runtime will depend on these protocols, and the adapters will depend on the
domain, so neither side needs to know the other exists.
"""

from __future__ import annotations
