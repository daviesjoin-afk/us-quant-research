"""Only two stores: explicit policy revisions and immutable evaluations."""
from typing import Protocol
from us_quant.trading.domain.strategy_paper_performance import (
    StrategyPaperPerformancePolicy, StrategyPaperPerformanceEvaluation,
)


class StrategyPaperPerformanceRepositoryError(RuntimeError):
    """Stored performance evidence is unreadable or cannot be persisted."""


class StrategyPaperPerformanceRepositoryConflict(StrategyPaperPerformanceRepositoryError):
    """An immutable identity conflicts or an append loses compare-and-set."""


class StrategyPaperPerformanceRepositoryNotFound(StrategyPaperPerformanceRepositoryError):
    """Requested immutable record does not exist."""


class StrategyPaperPerformancePolicyStorePort(Protocol):
    def append_policy_revision(self, policy: StrategyPaperPerformancePolicy, *, expected_current_revision: int | None) -> None: ...
    def get_policy(self, policy_id: str, revision: int) -> StrategyPaperPerformancePolicy: ...
    def active_policy(self, policy_id: str) -> StrategyPaperPerformancePolicy | None: ...


class StrategyPaperPerformanceRepositoryPort(Protocol):
    def record_evaluation(self, evaluation: StrategyPaperPerformanceEvaluation) -> None: ...
    def get_evaluation(self, evaluation_id: str) -> StrategyPaperPerformanceEvaluation: ...
    def evaluations_for_version(self, version_id: str) -> tuple[StrategyPaperPerformanceEvaluation, ...]: ...
    def latest_for_version(self, version_id: str) -> StrategyPaperPerformanceEvaluation | None: ...
    def active_policy(self, policy_id: str) -> StrategyPaperPerformancePolicy | None: ...
