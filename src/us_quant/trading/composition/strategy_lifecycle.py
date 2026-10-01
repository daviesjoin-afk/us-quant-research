"""Composition for governed strategy lifecycle authority.

Binds the controller, its trust root, the two durable stores and the state
machine it authorises.  The state machine is passed in rather than rebuilt
here, so exactly one ``StrategyApplication`` exists per process and this module
only wires its authority -- it does not create a second one that could drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from us_quant.trading.adapters.evidence_trust_store import (
    FileEvidenceVerificationKeySource,
)
from us_quant.trading.adapters.sqlite.evidence_authentication_repository import (
    SQLiteEvidenceAuthenticationRepository,
)
from us_quant.trading.adapters.sqlite.strategy_gate_repository import (
    SQLiteStrategyGateRepository,
)
from us_quant.trading.adapters.sqlite.strategy_lifecycle_repository import (
    SQLiteStrategyLifecycleRepository,
)
from us_quant.trading.adapters.sqlite.strategy_paper_performance_repository import (
    SQLiteStrategyPaperPerformanceRepository,
)
from us_quant.trading.application.strategies import StrategyApplication
from us_quant.trading.application.strategy_coverage_validity import (
    StrategyCoverageCurrentValidator,
)
from us_quant.trading.application.strategy_lifecycle import (
    StrategyLifecycleController,
    StrategyLifecycleService,
)


@dataclass(frozen=True, slots=True)
class StrategyLifecycleComponents:
    controller: StrategyLifecycleController
    service: StrategyLifecycleService
    repository: SQLiteStrategyLifecycleRepository
    key_source: FileEvidenceVerificationKeySource
    coverage_validity: StrategyCoverageCurrentValidator


def build_strategy_lifecycle_components(
    *,
    database_path: str | Path,
    trust_store_path: str | Path,
    strategies: StrategyApplication,
) -> StrategyLifecycleComponents:
    """Bind the lifecycle authority to the one state machine it governs.

    The controller is given the shared current-validity validator rather than a
    key source: current validity is one service over the member records, and the
    same instance is what the Paper launch boundary consumes.
    """

    if not isinstance(strategies, StrategyApplication):
        raise TypeError("strategies must be StrategyApplication")
    key_source = FileEvidenceVerificationKeySource(trust_store_path)
    repository = SQLiteStrategyLifecycleRepository(database_path)
    coverage_validity = StrategyCoverageCurrentValidator(
        authentications=SQLiteEvidenceAuthenticationRepository(database_path),
        gates=SQLiteStrategyGateRepository(database_path),
        key_source=key_source,
    )
    controller = StrategyLifecycleController(coverage_validity=coverage_validity)
    return StrategyLifecycleComponents(
        controller=controller,
        service=StrategyLifecycleService(
            controller=controller, decisions=repository, strategies=strategies,
            paper_performance_repository=SQLiteStrategyPaperPerformanceRepository(
                database_path
            ),
        ),
        repository=repository,
        key_source=key_source,
        coverage_validity=coverage_validity,
    )


__all__ = [
    "StrategyLifecycleComponents",
    "build_strategy_lifecycle_components",
]
