"""Composition for bounded strategy evidence coverage.

Binds the coverage evaluator, the external trust root it re-checks revocation
against, and the two durable stores.  There is no lifecycle wiring here:
coverage PASS does not promote and coverage FAIL does not pause, so this
composition cannot mutate strategy state even by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from us_quant.trading.adapters.evidence_trust_store import (
    FileEvidenceVerificationKeySource,
)
from us_quant.trading.adapters.sqlite.strategy_coverage_repository import (
    SQLiteStrategyCoverageRepository,
)
from us_quant.trading.application.strategy_coverage import StrategyCoverageEvaluator


@dataclass(frozen=True, slots=True)
class StrategyCoverageComponents:
    evaluator: StrategyCoverageEvaluator
    repository: SQLiteStrategyCoverageRepository
    key_source: FileEvidenceVerificationKeySource


def build_strategy_coverage_components(
    *,
    database_path: str | Path,
    trust_store_path: str | Path,
) -> StrategyCoverageComponents:
    """Bind only the coverage evaluator, its trust root and its stores."""

    key_source = FileEvidenceVerificationKeySource(trust_store_path)
    return StrategyCoverageComponents(
        evaluator=StrategyCoverageEvaluator(key_source=key_source),
        repository=SQLiteStrategyCoverageRepository(database_path),
        key_source=key_source,
    )


__all__ = ["StrategyCoverageComponents", "build_strategy_coverage_components"]
