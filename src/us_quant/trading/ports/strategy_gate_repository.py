"""Persistence port for immutable strategy gate evaluations."""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.strategy_gate import StrategyGateEvaluation


class StrategyGateRepositoryError(RuntimeError):
    """Stored strategy gate evidence is unreadable or cannot be persisted."""


class StrategyGateRepositoryConflict(StrategyGateRepositoryError):
    """An evaluation ID already exists with a different immutable payload."""


class StrategyGateRepositoryNotFound(StrategyGateRepositoryError):
    """No evaluation exists for the requested deterministic identity."""


class StrategyGateRepositoryPort(Protocol):
    """Store evaluation facts; make no lifecycle or eligibility decisions."""

    def record(self, evaluation: StrategyGateEvaluation) -> None:
        """Persist once; identical retries are idempotent, conflicts are refused."""

    def get(self, evaluation_id: str) -> StrategyGateEvaluation:
        """Read one evaluation, raising a typed error for missing/corrupt data."""

    def latest_for_version(
        self, version_id: str
    ) -> StrategyGateEvaluation | None:
        """Return newest by evaluated_at, then evaluation_id, or None."""

    def evaluations_for_version(
        self, version_id: str
    ) -> tuple[StrategyGateEvaluation, ...]:
        """Return evaluations ordered newest first with a stable tie-breaker."""


__all__ = [
    "StrategyGateRepositoryConflict",
    "StrategyGateRepositoryError",
    "StrategyGateRepositoryNotFound",
    "StrategyGateRepositoryPort",
]
