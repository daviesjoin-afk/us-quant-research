"""Desktop UI v2 orchestration.

The pages in ``desktop_v2/pages`` render and report intent.  Something has to
own the runtime behind them, and during the transition that something was
``MainWindow``: a 5k-line object holding the market feed, the account refresh,
the research workers, the shadow book and the Paper session all at once.

This package is where those owners are extracted, one capability at a time.
Each one takes injected dependencies and exposes a small read-only surface, so
the window's remaining job is composition: build the object, wire its signals,
and route its published facts to whoever still consumes them.

Only ``market`` exists today.  The remaining extractions are tracked in
``docs/TRADING_ARCHITECTURE_V2.md``.
"""

from __future__ import annotations

__all__: list[str] = []
