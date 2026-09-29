"""Composition for independent strategy evidence-gate recording."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from us_quant.trading.adapters.sqlite.strategy_gate_repository import (
    SQLiteStrategyGateRepository,
)
from us_quant.trading.application.strategy_gate import StrategyGateEvaluator


@dataclass(frozen=True, slots=True)
class StrategyGateComponents:
    evaluator: StrategyGateEvaluator
    repository: SQLiteStrategyGateRepository


def build_strategy_gate_components(
    *, database_path: str | Path
) -> StrategyGateComponents:
    """Bind only the pure evaluator and its independent durable store."""

    return StrategyGateComponents(
        evaluator=StrategyGateEvaluator(),
        repository=SQLiteStrategyGateRepository(database_path),
    )


__all__ = ["StrategyGateComponents", "build_strategy_gate_components"]
