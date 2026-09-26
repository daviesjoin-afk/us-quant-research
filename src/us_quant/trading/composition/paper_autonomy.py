"""Paper autonomy composition root.

The one module allowed to know both the autonomy application and the concrete
store it runs against.  Callers above this file -- the operator CLI now, a
scheduler host later -- name the application and the port only, which is what
lets the SQLite store be replaced without touching a caller.

There is deliberately no builder for a *supervisor* here.  This control plane
owns an operator's intent and nothing else; a component that acts on that intent
has to be given its own, narrower set of collaborators, and inventing its
composition root before it exists would be inventing the boundary first.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from us_quant.trading.adapters.sqlite.paper_autonomy_repository import (
    SQLitePaperAutonomyRepository,
)
from us_quant.trading.application.paper_autonomy import (
    PaperAutonomyApplication,
)


def build_paper_autonomy_application(
    *,
    database_path: str | Path,
    clock: Callable[[], datetime] | None = None,
) -> PaperAutonomyApplication:
    """Bind the autonomy authority to its store.

    ``clock`` is optional and defaulted rather than required: tests inject one to
    make the audit timestamps deterministic, and the operator CLI leaves it
    alone because a command line has no reason to lie about the time.
    """

    return PaperAutonomyApplication(
        SQLitePaperAutonomyRepository(database_path),
        clock=clock,
    )


__all__ = ["build_paper_autonomy_application"]
