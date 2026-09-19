"""IBKR market data adapters.

The public surface is the read-only stream and its stable source ids.  The
transport reducer, the transport DTOs and the read-only guard live in
``trading.adapters.market_data_state`` because Alpaca and Finnhub share them.
"""

from __future__ import annotations

from us_quant.trading.adapters.ibkr.market_data import (
    IBKR_COVERAGE,
    IBKR_EXTENDED_COVERAGE,
    IBKR_EXTENDED_SOURCE_LABEL,
    IBKR_SOURCE_LABEL,
    SOURCE_IBKR,
    SOURCE_IBKR_EXTENDED,
    IBKRReadOnlyStream,
)

__all__ = [
    "IBKR_COVERAGE",
    "IBKR_EXTENDED_COVERAGE",
    "IBKR_EXTENDED_SOURCE_LABEL",
    "IBKR_SOURCE_LABEL",
    "IBKRReadOnlyStream",
    "SOURCE_IBKR",
    "SOURCE_IBKR_EXTENDED",
]
