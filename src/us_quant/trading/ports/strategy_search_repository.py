"""Persistence port for versioned search policies and immutable generations."""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.strategy_search import StrategySearchGeneration, StrategySearchPolicy


class StrategySearchRepositoryError(RuntimeError):
    """Base error for unreadable or unwriteable search records."""


class StrategySearchRepositoryConflict(StrategySearchRepositoryError):
    """A policy revision or generation identity already has other semantics."""


class StrategySearchRepositoryNotFound(StrategySearchRepositoryError):
    """A requested policy revision or generation is absent."""


class StrategySearchRepositoryPort(Protocol):
    def append_policy_revision(
        self,
        policy: StrategySearchPolicy,
        *,
        expected_current_revision: int | None,
    ) -> None: ...

    def get_policy(self, policy_id: str, revision: int) -> StrategySearchPolicy: ...

    def active_policy(self, policy_id: str) -> StrategySearchPolicy | None: ...

    def record_generation(
        self, generation: StrategySearchGeneration
    ) -> StrategySearchGeneration: ...

    def get_generation(self, generation_id: str) -> StrategySearchGeneration: ...

    def generations_for_policy(
        self, strategy_id: str, policy_id: str
    ) -> tuple[StrategySearchGeneration, ...]: ...

    def generations_for_strategy(
        self, strategy_id: str
    ) -> tuple[StrategySearchGeneration, ...]: ...


__all__ = [
    "StrategySearchRepositoryConflict",
    "StrategySearchRepositoryError",
    "StrategySearchRepositoryNotFound",
    "StrategySearchRepositoryPort",
]
