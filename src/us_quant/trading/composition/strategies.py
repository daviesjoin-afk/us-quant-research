"""Strategy composition root.

This is the one module allowed to know both the strategy application service
and the concrete SQLite adapter.  It chooses the store, builds the adapter,
wraps it in the application service, and brings the default catalogue up to
date -- in that order, so the very first ``StrategyApplication`` call already
sees a seeded store.

Keeping the wiring here is what lets the application stay storage-blind: a
test can build a ``StrategyApplication`` over an in-memory fake without a file
on disk, and the dependency arrow keeps pointing inward -- composition ->
application -> ports -> domain.

The window used to construct ``StrategyRegistry`` and call ``seed_defaults``
itself.  That made the UI the composition root for strategy, which is exactly
the arrangement each migration is removing.
"""

from __future__ import annotations

from pathlib import Path

from us_quant.trading.adapters.sqlite.strategy_repository import (
    SQLiteStrategyRepository,
)
from us_quant.trading.application.strategies import StrategyApplication


def build_strategy_application(path: str | Path) -> StrategyApplication:
    """Assemble the strategy application over a SQLite store, seeded.

    ``bootstrap`` is idempotent and also performs the legacy
    embedded-symbol retirement, so calling this on an existing store is safe
    and is the intended upgrade path: no database is ever deleted or rebuilt.
    """

    application = StrategyApplication(SQLiteStrategyRepository(path))
    application.bootstrap()
    return application


__all__ = ["build_strategy_application"]
