"""Composition root for deterministic strategy candidate generation."""

from __future__ import annotations

from pathlib import Path

from us_quant.trading.adapters.sqlite.strategy_repository import SQLiteStrategyRepository
from us_quant.trading.adapters.sqlite.strategy_search_repository import (
    SQLiteStrategySearchRepository,
)
from us_quant.trading.application.strategies import StrategyApplication
from us_quant.trading.application.strategy_candidate_generation import (
    StrategyCandidateGenerationApplication,
)


def build_strategy_candidate_generation_application(
    strategy_path: str | Path,
    search_path: str | Path,
) -> StrategyCandidateGenerationApplication:
    """Assemble the three allowed parts of the 6-E application path."""

    strategies = StrategyApplication(SQLiteStrategyRepository(strategy_path))
    search = SQLiteStrategySearchRepository(search_path)
    return StrategyCandidateGenerationApplication(strategies, search)


__all__ = ["build_strategy_candidate_generation_application"]
