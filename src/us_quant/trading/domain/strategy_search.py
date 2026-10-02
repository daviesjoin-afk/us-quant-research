"""Versioned bounded search policies and deterministic strategy challengers.

This module describes one narrow operation: perturb exactly one scalar
parameter at a time, validate the complete candidate with the strategy's
existing domain validator, and return candidates in a stable exploration
order. It has no persistence, clock, lifecycle, evidence, or execution access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
from typing import Any, Mapping

from us_quant.trading.domain.common import freeze_parameters
from us_quant.trading.domain.strategy import (
    StrategyVersion,
    parameter_hash_for,
)
from us_quant.trading.domain.strategy_parameters import (
    StrategyParameterError,
    validate_strategy_parameters,
)


SEARCH_POLICY_VERSION = "strategy-search-policy-v1"
STRATEGY_CANDIDATE_GENERATOR_VERSION = "strategy-candidate-generator-v1"
_HASH_LENGTH = 64


class StrategySearchError(ValueError):
    """The requested policy or generation cannot be applied safely."""


class StrategySearchValueKind(StrEnum):
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"


@dataclass(frozen=True, slots=True)
class StrategySearchParameterRule:
    parameter_key: str
    value_kind: StrategySearchValueKind
    minimum: Decimal
    maximum: Decimal
    step: Decimal
    maximum_delta: Decimal

    def __post_init__(self) -> None:
        _require_text(self.parameter_key, "parameter_key")
        if not isinstance(self.value_kind, StrategySearchValueKind):
            raise TypeError("value_kind must be StrategySearchValueKind")
        for name in ("minimum", "maximum", "step", "maximum_delta"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise TypeError(f"{name} must be a finite Decimal")
        if self.minimum > self.maximum:
            raise ValueError("minimum must be no greater than maximum")
        if self.step <= 0:
            raise ValueError("step must be positive")
        if self.maximum_delta <= 0:
            raise ValueError("maximum_delta must be positive")
        if self.value_kind is StrategySearchValueKind.INTEGER:
            for name in ("minimum", "maximum", "step", "maximum_delta"):
                if getattr(self, name) != getattr(self, name).to_integral_value():
                    raise ValueError(f"{name} must be integral for INTEGER rules")


@dataclass(frozen=True, slots=True)
class StrategySearchPolicy:
    policy_id: str
    revision: int
    policy_version: str
    strategy_id: str
    parameter_rules: tuple[StrategySearchParameterRule, ...]
    maximum_candidates_per_generation: int
    maximum_active_candidates: int
    maximum_total_candidates: int
    maximum_generations: int
    generation_cooldown: timedelta
    deterministic_seed: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.policy_id, "policy_id")
        _require_text(self.policy_version, "policy_version")
        _require_text(self.strategy_id, "strategy_id")
        _require_text(self.deterministic_seed, "deterministic_seed")
        if self.policy_version != SEARCH_POLICY_VERSION:
            raise ValueError(f"unsupported search policy version: {self.policy_version}")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("revision must be a positive integer")
        for name in (
            "maximum_candidates_per_generation",
            "maximum_active_candidates",
            "maximum_total_candidates",
            "maximum_generations",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.generation_cooldown, timedelta):
            raise TypeError("generation_cooldown must be timedelta")
        if self.generation_cooldown < timedelta(0):
            raise ValueError("generation_cooldown cannot be negative")
        _require_aware(self.created_at, "created_at")
        if not isinstance(self.parameter_rules, (tuple, list)):
            raise TypeError("parameter_rules must be a sequence of rules")
        rules = tuple(self.parameter_rules)
        if any(not isinstance(rule, StrategySearchParameterRule) for rule in rules):
            raise TypeError("parameter_rules must contain StrategySearchParameterRule")
        ordered = tuple(sorted(rules, key=lambda rule: rule.parameter_key))
        if len({rule.parameter_key for rule in ordered}) != len(ordered):
            raise ValueError("parameter rule keys must be unique")
        object.__setattr__(self, "parameter_rules", ordered)


@dataclass(frozen=True, slots=True)
class StrategyCandidateSpec:
    candidate_version_id: str
    semver: str
    parameters: Mapping[str, Any]
    candidate_parameter_hash: str
    changed_parameter_key: str
    parent_value: str
    candidate_value: str
    ordering_digest: str

    def __post_init__(self) -> None:
        _require_text(self.candidate_version_id, "candidate_version_id")
        _require_text(self.semver, "semver")
        _require_text(self.changed_parameter_key, "changed_parameter_key")
        _require_text(self.parent_value, "parent_value")
        _require_text(self.candidate_value, "candidate_value")
        _require_digest(self.candidate_parameter_hash, "candidate_parameter_hash")
        _require_digest(self.ordering_digest, "ordering_digest")
        object.__setattr__(self, "parameters", freeze_parameters(dict(self.parameters)))
        if parameter_hash_for(self.parameters) != self.candidate_parameter_hash:
            raise ValueError("candidate_parameter_hash does not match parameters")


@dataclass(frozen=True, slots=True)
class StrategyCandidateLineage:
    child_version_id: str
    parent_version_id: str
    strategy_id: str
    generation: int
    ordinal: int
    policy_id: str
    policy_revision: int
    policy_version: str
    parent_parameter_hash: str
    candidate_parameter_hash: str
    changed_parameter_key: str
    parent_value: str
    candidate_value: str
    generation_id: str
    generator_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "child_version_id", "parent_version_id", "strategy_id", "policy_id",
            "policy_version", "changed_parameter_key", "parent_value",
            "candidate_value", "generation_id", "generator_version",
        ):
            _require_text(getattr(self, name), name)
        for name in ("parent_parameter_hash", "candidate_parameter_hash"):
            _require_digest(getattr(self, name), name)
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("generation must be a positive integer")
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise ValueError("ordinal must be a positive integer")
        if type(self.policy_revision) is not int or self.policy_revision < 1:
            raise ValueError("policy_revision must be a positive integer")
        _require_aware(self.created_at, "created_at")
        expected = candidate_version_id_for(
            parent_version_id=self.parent_version_id,
            parent_parameter_hash=self.parent_parameter_hash,
            policy_id=self.policy_id,
            policy_revision=self.policy_revision,
            generation=self.generation,
            candidate_parameter_hash=self.candidate_parameter_hash,
            generator_version=self.generator_version,
        )
        if self.child_version_id != expected:
            raise ValueError("child_version_id does not match candidate identity")


@dataclass(frozen=True, slots=True)
class StrategySearchGeneration:
    generation_id: str
    strategy_id: str
    parent_version_id: str
    parent_parameter_hash: str
    generation: int
    policy_id: str
    policy_revision: int
    policy_version: str
    deterministic_seed: str
    candidate_lineages: tuple[StrategyCandidateLineage, ...]
    generator_version: str
    generated_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "strategy_id", "parent_version_id", "policy_id", "policy_version",
            "deterministic_seed", "generator_version",
        ):
            _require_text(getattr(self, name), name)
        _require_digest(self.parent_parameter_hash, "parent_parameter_hash")
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("generation must be a positive integer")
        if type(self.policy_revision) is not int or self.policy_revision < 1:
            raise ValueError("policy_revision must be a positive integer")
        if not isinstance(self.candidate_lineages, tuple):
            raise TypeError("candidate_lineages must be a tuple")
        _require_aware(self.generated_at, "generated_at")
        if self.generator_version != STRATEGY_CANDIDATE_GENERATOR_VERSION:
            raise ValueError(f"unsupported generator version: {self.generator_version}")
        if self.generation_id != generation_id_for(
            strategy_id=self.strategy_id,
            parent_version_id=self.parent_version_id,
            parent_parameter_hash=self.parent_parameter_hash,
            policy_id=self.policy_id,
            policy_revision=self.policy_revision,
            generation=self.generation,
            deterministic_seed=self.deterministic_seed,
            generator_version=self.generator_version,
        ):
            raise ValueError("generation_id does not match generation identity")
        if tuple(item.ordinal for item in self.candidate_lineages) != tuple(
            range(1, len(self.candidate_lineages) + 1)
        ):
            raise ValueError("candidate ordinals must be contiguous from one")
        if len({item.child_version_id for item in self.candidate_lineages}) != len(
            self.candidate_lineages
        ):
            raise ValueError("candidate child identities must be unique")
        for item in self.candidate_lineages:
            if (
                item.strategy_id != self.strategy_id
                or item.parent_version_id != self.parent_version_id
                or item.parent_parameter_hash != self.parent_parameter_hash
                or item.generation != self.generation
                or item.policy_id != self.policy_id
                or item.policy_revision != self.policy_revision
                or item.policy_version != self.policy_version
                or item.generation_id != self.generation_id
                or item.generator_version != self.generator_version
                or item.created_at != self.generated_at
            ):
                raise ValueError("candidate lineage does not belong to this generation")


def generate_strategy_candidate_specs(
    *,
    parent: StrategyVersion,
    policy: StrategySearchPolicy,
    generation: int,
) -> tuple[StrategyCandidateSpec, ...]:
    """Generate deterministic, validator-approved one-coordinate challengers."""

    if not isinstance(parent, StrategyVersion):
        raise TypeError("parent must be StrategyVersion")
    if not isinstance(policy, StrategySearchPolicy):
        raise TypeError("policy must be StrategySearchPolicy")
    if parent.strategy_id != policy.strategy_id:
        raise StrategySearchError("search policy belongs to another strategy")
    if parameter_hash_for(parent.parameters) != parent.parameter_hash:
        raise StrategySearchError("parent parameter hash does not match its parameters")
    if type(generation) is not int or not 1 <= generation <= policy.maximum_generations:
        raise StrategySearchError("generation is outside the policy bounds")

    parent_parameters = dict(parent.parameters)
    for rule in policy.parameter_rules:
        if rule.parameter_key not in parent_parameters:
            raise StrategySearchError(
                f"policy parameter is absent from parent: {rule.parameter_key}"
            )
        _parent_numeric_value(rule, parent_parameters[rule.parameter_key])

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
    by_parameter_hash: dict[str, StrategyCandidateSpec] = {}

    for rule in policy.parameter_rules:
        parent_raw = parent_parameters[rule.parameter_key]
        parent_number = _parent_numeric_value(rule, parent_raw)
        steps = int(rule.maximum_delta // rule.step)
        for step_number in range(1, steps + 1):
            delta = rule.step * step_number
            for signed_delta in (-delta, delta):
                proposed = parent_number + signed_delta
                if not rule.minimum <= proposed <= rule.maximum:
                    continue
                candidate_parameters = dict(parent_parameters)
                if rule.value_kind is StrategySearchValueKind.INTEGER:
                    candidate_parameters[rule.parameter_key] = int(proposed)
                else:
                    candidate_parameters[rule.parameter_key] = _decimal_text(proposed)
                try:
                    normalized = validate_strategy_parameters(
                        parent.strategy_id, candidate_parameters
                    )
                except (StrategyParameterError, ValueError, TypeError, OverflowError):
                    # Cross-parameter and strategy-specific relations belong to
                    # the existing validator; an inadmissible neighbor is skipped.
                    continue
                candidate_hash = parameter_hash_for(normalized)
                if candidate_hash == parent.parameter_hash or candidate_hash in by_parameter_hash:
                    continue
                candidate_id = candidate_version_id_for(
                    parent_version_id=parent.version_id,
                    parent_parameter_hash=parent.parameter_hash,
                    policy_id=policy.policy_id,
                    policy_revision=policy.revision,
                    generation=generation,
                    candidate_parameter_hash=candidate_hash,
                    generator_version=STRATEGY_CANDIDATE_GENERATOR_VERSION,
                )
                ordering_digest = _digest(
                    {
                        "deterministic_seed": policy.deterministic_seed,
                        "parent_version_id": parent.version_id,
                        "parent_parameter_hash": parent.parameter_hash,
                        "policy_id": policy.policy_id,
                        "policy_revision": policy.revision,
                        "generation": generation,
                        "candidate_parameter_hash": candidate_hash,
                    }
                )
                by_parameter_hash[candidate_hash] = StrategyCandidateSpec(
                    candidate_version_id=candidate_id,
                    semver=(
                        f"{parent.semver}-g{generation}-p{policy.revision}-"
                        f"{candidate_id}"
                    ),
                    parameters=normalized,
                    candidate_parameter_hash=candidate_hash,
                    changed_parameter_key=rule.parameter_key,
                    parent_value=_canonical_numeric(parent_raw, rule.value_kind),
                    candidate_value=_canonical_numeric(
                        normalized[rule.parameter_key], rule.value_kind
                    ),
                    ordering_digest=ordering_digest,
                )

    ordered = sorted(
        by_parameter_hash.values(),
        key=lambda candidate: (
            candidate.ordering_digest, candidate.candidate_parameter_hash
        ),
    )
    return tuple(ordered[: policy.maximum_candidates_per_generation])


def candidate_version_id_for(
    *,
    parent_version_id: str,
    parent_parameter_hash: str,
    policy_id: str,
    policy_revision: int,
    generation: int,
    candidate_parameter_hash: str,
    generator_version: str = STRATEGY_CANDIDATE_GENERATOR_VERSION,
) -> str:
    return "scv-" + _digest(
        {
            "parent_version_id": parent_version_id,
            "parent_parameter_hash": parent_parameter_hash,
            "policy_id": policy_id,
            "policy_revision": policy_revision,
            "generation": generation,
            "candidate_parameter_hash": candidate_parameter_hash,
            "generator_version": generator_version,
        }
    )


def generation_id_for(
    *,
    strategy_id: str,
    parent_version_id: str,
    parent_parameter_hash: str,
    policy_id: str,
    policy_revision: int,
    generation: int,
    deterministic_seed: str,
    generator_version: str = STRATEGY_CANDIDATE_GENERATOR_VERSION,
) -> str:
    return "scg-" + _digest(
        {
            "strategy_id": strategy_id,
            "parent_version_id": parent_version_id,
            "parent_parameter_hash": parent_parameter_hash,
            "policy_id": policy_id,
            "policy_revision": policy_revision,
            "generation": generation,
            "deterministic_seed": deterministic_seed,
            "generator_version": generator_version,
        }
    )


def generation_semantic_payload(
    generation: StrategySearchGeneration,
) -> dict[str, Any]:
    """Payload identity excluding event timestamps for exact retry comparison."""

    return {
        "generation_id": generation.generation_id,
        "strategy_id": generation.strategy_id,
        "parent_version_id": generation.parent_version_id,
        "parent_parameter_hash": generation.parent_parameter_hash,
        "generation": generation.generation,
        "policy_id": generation.policy_id,
        "policy_revision": generation.policy_revision,
        "policy_version": generation.policy_version,
        "deterministic_seed": generation.deterministic_seed,
        "generator_version": generation.generator_version,
        "candidate_lineages": [
            {
                "child_version_id": item.child_version_id,
                "parent_version_id": item.parent_version_id,
                "strategy_id": item.strategy_id,
                "generation": item.generation,
                "ordinal": item.ordinal,
                "policy_id": item.policy_id,
                "policy_revision": item.policy_revision,
                "policy_version": item.policy_version,
                "parent_parameter_hash": item.parent_parameter_hash,
                "candidate_parameter_hash": item.candidate_parameter_hash,
                "changed_parameter_key": item.changed_parameter_key,
                "parent_value": item.parent_value,
                "candidate_value": item.candidate_value,
                "generation_id": item.generation_id,
                "generator_version": item.generator_version,
            }
            for item in generation.candidate_lineages
        ],
    }


def _parent_numeric_value(
    rule: StrategySearchParameterRule,
    value: Any,
) -> Decimal:
    if rule.value_kind is StrategySearchValueKind.INTEGER:
        if type(value) is not int:
            raise StrategySearchError(
                f"{rule.parameter_key} is not an INTEGER scalar"
            )
        return Decimal(value)
    if isinstance(value, bool) or isinstance(value, float):
        raise StrategySearchError(
            f"{rule.parameter_key} is not a DECIMAL scalar"
        )
    if not isinstance(value, (Decimal, str, int)):
        raise StrategySearchError(
            f"{rule.parameter_key} is not a DECIMAL scalar"
        )
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise StrategySearchError(
            f"{rule.parameter_key} is not a DECIMAL scalar"
        ) from error
    if not parsed.is_finite():
        raise StrategySearchError(f"{rule.parameter_key} is not finite")
    return parsed


def _canonical_numeric(value: Any, kind: StrategySearchValueKind) -> str:
    if kind is StrategySearchValueKind.INTEGER:
        return str(value)
    return _decimal_text(Decimal(str(value)))


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _require_text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")


def _require_digest(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != _HASH_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be datetime")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "SEARCH_POLICY_VERSION",
    "STRATEGY_CANDIDATE_GENERATOR_VERSION",
    "StrategyCandidateLineage",
    "StrategyCandidateSpec",
    "StrategySearchError",
    "StrategySearchGeneration",
    "StrategySearchParameterRule",
    "StrategySearchPolicy",
    "StrategySearchValueKind",
    "candidate_version_id_for",
    "generate_strategy_candidate_specs",
    "generation_id_for",
    "generation_semantic_payload",
]
