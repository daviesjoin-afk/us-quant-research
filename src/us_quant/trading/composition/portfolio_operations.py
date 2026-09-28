"""Composition for the durable portfolio operating plan."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from us_quant.trading.adapters.sqlite.portfolio_operating_plan_repository import (
    SQLitePortfolioOperatingPlanRepository,
)
from us_quant.trading.application.portfolio_operations import (
    PortfolioOperatingPlanApplication,
)


def build_portfolio_operating_plan_application(
    *,
    database_path: str | Path,
    strategies,
    active_session: Callable[[], bool],
) -> PortfolioOperatingPlanApplication:
    """Wire the plan service to its durable store at the application boundary."""

    return PortfolioOperatingPlanApplication(
        repository=SQLitePortfolioOperatingPlanRepository(database_path),
        strategies=strategies,
        active_session=active_session,
    )


__all__ = ["build_portfolio_operating_plan_application"]
