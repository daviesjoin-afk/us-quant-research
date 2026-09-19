"""Adapters.

Adapters implement the ``ports`` protocols against real systems.  Each module
lands with the migration that needs it:

    ibkr/market_data.py            <- ibkr_stream.py       ✅
    alpaca/market_data.py          <- alpaca_stream.py     ✅
    finnhub/market_data.py         <- finnhub_stream.py    ✅
    ibkr/account.py                <- ibkr_readonly.py     ✅
    sqlite/strategy_repository.py  <- strategy_registry.py ✅
    ibkr/execution.py              <- ibkr_paper_orders.py ⏭
    sqlite/order_repository.py     <- paper_order_journal.py ⏭

The existing modules keep working unchanged until their migration lands.
Adapters depend on the domain and on their vendor library; they must never be
imported by the domain.
"""

from __future__ import annotations
