"""Materialize deterministic, bounded RESEARCH strategy challengers."""

from __future__ import annotations

from datetime import datetime, timezone

from us_quant.trading.application.strategies import (
    StrategyApplication,
    StrategyApplicationError,
    StrategyNotFoundError,
)
from us_quant.trading.domain.strategy import (
    StrategyStatus,
    StrategyVersion,
    canonical_parameters_json,
)
from us_quant.trading.domain.strategy_search import (
    STRATEGY_CANDIDATE_GENERATOR_VERSION,
    StrategyCandidateLineage,
    StrategySearchError,
    StrategySearchGeneration,
    StrategySearchPolicy,
    generate_strategy_candidate_specs,
    generation_id_for,
    generation_semantic_payload,
)
from us_quant.trading.ports.strategy_search_repository import (
    StrategySearchRepositoryConflict,
    StrategySearchRepositoryNotFound,
    StrategySearchRepositoryPort,
)


_ACTIVE_STATUSES = frozenset(
    {StrategyStatus.RESEARCH, StrategyStatus.PAPER_SHADOW, StrategyStatus.PAUSED}
)


class StrategyCandidateGenerationError(RuntimeError):
    """The requested generation is unsafe or exceeds its policy bounds."""


class StrategyCandidateGenerationApplication:
    """The single application surface for generation and candidate creation."""

    def __init__(
        self,
        strategies: StrategyApplication,
        repository: StrategySearchRepositoryPort,
    ) -> None:
        self._strategies = strategies
        self._repository = repository

    def generate(
        self,
        *,
        parent_version_id: str,
        policy_id: str,
        policy_revision: int,
        generation: int,
        generated_at: datetime,
    ) -> StrategySearchGeneration:
        _require_aware(generated_at)
        if type(policy_revision) is not int or policy_revision < 1:
            raise StrategyCandidateGenerationError("policy_revision must be positive")
        try:
            parent = self._strategies.get_version(parent_version_id)
            policy = self._repository.get_policy(policy_id, policy_revision)
        except (StrategyNotFoundError, StrategySearchRepositoryNotFound) as error:
            raise StrategyCandidateGenerationError(str(error)) from error
        if parent.status is StrategyStatus.LEGACY_INVALIDATED:
            raise StrategyCandidateGenerationError("legacy-invalidated parent is ineligible")
        if policy.policy_id != policy_id or policy.revision != policy_revision:
            raise StrategyCandidateGenerationError("repository returned a different policy revision")
        if policy.strategy_id != parent.strategy_id:
            raise StrategyCandidateGenerationError("policy belongs to another strategy")
        try:
            specs = generate_strategy_candidate_specs(
                parent=parent, policy=policy, generation=generation
            )
        except (StrategySearchError, ValueError, TypeError) as error:
            raise StrategyCandidateGenerationError(str(error)) from error

        generation_id = generation_id_for(
            strategy_id=parent.strategy_id,
            parent_version_id=parent.version_id,
            parent_parameter_hash=parent.parameter_hash,
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            generation=generation,
            deterministic_seed=policy.deterministic_seed,
            generator_version=STRATEGY_CANDIDATE_GENERATOR_VERSION,
        )
        expected = self._generation(parent, policy, generation, generation_id, specs, generated_at)

        # Exact-generation replay is checked before cooldown and current child
        # status: its immutable record describes generation-time truth.
        try:
            stored = self._repository.get_generation(generation_id)
        except StrategySearchRepositoryNotFound:
            stored = None
        if stored is not None:
            if generation_semantic_payload(stored) != generation_semantic_payload(expected):
                raise StrategyCandidateGenerationError(
                    "generation identity already exists with different semantics"
                )
            return stored

        versions = self._strategies.list_versions()
        self._enforce_cooldown(parent, policy, generated_at)
        self._enforce_resource_bounds(parent, policy, specs, versions)

        existing: dict[str, StrategyVersion] = {}
        # Preflight all deterministic identities before writing any missing one.
        for spec in specs:
            try:
                child = self._strategies.get_version(spec.candidate_version_id)
            except StrategyNotFoundError:
                continue
            self._verify_existing_child(parent, spec, child)
            existing[spec.candidate_version_id] = child

        lineages: list[StrategyCandidateLineage] = []
        for ordinal, spec in enumerate(specs, start=1):
            if spec.candidate_version_id not in existing:
                try:
                    self._strategies.clone_research_candidate(
                        parent.version_id,
                        candidate_version_id=spec.candidate_version_id,
                        semver=spec.semver,
                        parameters=spec.parameters,
                    )
                except StrategyApplicationError as error:
                    # A concurrent writer or a conflicting semver must never
                    # be guessed into lineage.
                    raise StrategyCandidateGenerationError(
                        f"candidate materialization failed: {error}"
                    ) from error
            lineages.append(
                StrategyCandidateLineage(
                    child_version_id=spec.candidate_version_id,
                    parent_version_id=parent.version_id,
                    strategy_id=parent.strategy_id,
                    generation=generation,
                    ordinal=ordinal,
                    policy_id=policy.policy_id,
                    policy_revision=policy.revision,
                    policy_version=policy.policy_version,
                    parent_parameter_hash=parent.parameter_hash,
                    candidate_parameter_hash=spec.candidate_parameter_hash,
                    changed_parameter_key=spec.changed_parameter_key,
                    parent_value=spec.parent_value,
                    candidate_value=spec.candidate_value,
                    generation_id=generation_id,
                    generator_version=STRATEGY_CANDIDATE_GENERATOR_VERSION,
                    created_at=generated_at,
                )
            )
        completed = self._generation(
            parent, policy, generation, generation_id, specs, generated_at,
            candidate_lineages=tuple(lineages),
        )
        try:
            return self._repository.record_generation(completed)
        except StrategySearchRepositoryConflict as error:
            raise StrategyCandidateGenerationError(str(error)) from error

    @staticmethod
    def _generation(parent, policy, generation, generation_id, specs, generated_at,
                    candidate_lineages=()):
        lineages = tuple(candidate_lineages)
        if not lineages and specs:
            lineages = tuple(
                StrategyCandidateLineage(
                    child_version_id=spec.candidate_version_id,
                    parent_version_id=parent.version_id,
                    strategy_id=parent.strategy_id,
                    generation=generation,
                    ordinal=ordinal,
                    policy_id=policy.policy_id,
                    policy_revision=policy.revision,
                    policy_version=policy.policy_version,
                    parent_parameter_hash=parent.parameter_hash,
                    candidate_parameter_hash=spec.candidate_parameter_hash,
                    changed_parameter_key=spec.changed_parameter_key,
                    parent_value=spec.parent_value,
                    candidate_value=spec.candidate_value,
                    generation_id=generation_id,
                    generator_version=STRATEGY_CANDIDATE_GENERATOR_VERSION,
                    created_at=generated_at,
                )
                for ordinal, spec in enumerate(specs, start=1)
            )
        return StrategySearchGeneration(
            generation_id=generation_id,
            strategy_id=parent.strategy_id,
            parent_version_id=parent.version_id,
            parent_parameter_hash=parent.parameter_hash,
            generation=generation,
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            policy_version=policy.policy_version,
            deterministic_seed=policy.deterministic_seed,
            candidate_lineages=lineages,
            generator_version=STRATEGY_CANDIDATE_GENERATOR_VERSION,
            generated_at=generated_at,
        )

    def _enforce_cooldown(self, parent, policy, generated_at) -> None:
        previous = self._repository.generations_for_policy(
            parent.strategy_id, policy.policy_id
        )
        if not previous:
            return
        latest = max(item.generated_at.astimezone(timezone.utc) for item in previous)
        current = generated_at.astimezone(timezone.utc)
        if current < latest:
            raise StrategyCandidateGenerationError("generated_at precedes the latest generation")
        if current - latest < policy.generation_cooldown:
            raise StrategyCandidateGenerationError("generation cooldown has not elapsed")

    def _enforce_resource_bounds(self, parent, policy, specs, versions) -> None:
        generations = self._repository.generations_for_policy(
            parent.strategy_id, policy.policy_id
        )
        lineage_ids = {
            lineage.child_version_id
            for item in generations
            for lineage in item.candidate_lineages
        }
        by_id = {version.version_id: version for version in versions}
        missing = lineage_ids - by_id.keys()
        if missing:
            raise StrategyCandidateGenerationError(
                "a prior candidate lineage references a missing strategy version"
            )
        active_ids = {
            version_id for version_id in lineage_ids
            if by_id[version_id].status in _ACTIVE_STATUSES
        }
        intended_ids = {spec.candidate_version_id for spec in specs}
        if len(lineage_ids) + len(intended_ids) > policy.maximum_total_candidates:
            raise StrategyCandidateGenerationError("maximum_total_candidates exceeded")
        if len(active_ids | intended_ids) > policy.maximum_active_candidates:
            raise StrategyCandidateGenerationError("maximum_active_candidates exceeded")

    @staticmethod
    def _verify_existing_child(parent, spec, child) -> None:
        equal = (
            child.version_id == spec.candidate_version_id
            and child.strategy_id == parent.strategy_id
            and child.semver == spec.semver
            and child.parameter_hash == spec.candidate_parameter_hash
            and canonical_parameters_json(child.parameters)
            == canonical_parameters_json(spec.parameters)
            and child.universe_hash == parent.universe_hash
            and child.code_hash == parent.code_hash
            and child.risk_budget_pct == parent.risk_budget_pct
            and child.status is StrategyStatus.RESEARCH
            and child.mode.value == "research"
            and child.gate_passed is False
        )
        if not equal:
            raise StrategyCandidateGenerationError(
                "deterministic candidate identity is occupied by a different or proven version"
            )


def _require_aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StrategyCandidateGenerationError("generated_at must be timezone-aware")


__all__ = [
    "StrategyCandidateGenerationApplication",
    "StrategyCandidateGenerationError",
]
