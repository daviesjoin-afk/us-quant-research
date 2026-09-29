"""Bounded, versioned coverage of a strategy's whole universe.

Stage 6-A and Stage 6-B1 can each prove something about *one* symbol: that a
review of it was structurally sound, and that it was produced by a trusted
identity.  Neither can prove that the strategy's universe was covered.  A
single symbol's PASS must never be read as "this strategy is covered", so
coverage is a separate authority with its own explicit, versioned policy.

Coverage *composes* two facts it does not compute: an authenticated evidence
PASS and a gate evaluation PASS.  It never re-derives PBO, DSR, walk-forward,
HAC or data-quality thresholds -- those remain exclusively TargetedReview's.

Two properties are load-bearing:

**A policy authorises, and there is no permissive default.**  The thresholds
live in a stored, versioned record rather than in code, and a policy cannot be
constructed with an empty symbol list or a zero minimum, so there is no
instance that passes by accident.  No policy at all fails closed.

**A PASS here is still not lifecycle authority.**  It answers only whether the
strategy's universe was covered by admissible evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
import json

from us_quant.trading.domain.evidence_auth import (
    AuthenticatedStrategyResearchEvidence,
)
from us_quant.trading.domain.strategy_gate import (
    StrategyGateEvaluation,
    StrategyGateVerdict,
)


#: Policy schema this evaluator understands.  Bumped when the meaning of a
#: policy field changes; an unknown value fails closed rather than being
#: interpreted optimistically.
SUPPORTED_COVERAGE_POLICY_VERSIONS: tuple[str, ...] = ("evidence-coverage-v1",)

COVERAGE_EVALUATOR_VERSION = "strategy-coverage-v1"


class StrategyCoverageVerdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class StrategyCoverageBlocker(StrEnum):
    """Every reason coverage can refuse.  Unknown always fails closed."""

    POLICY_MISSING = "POLICY_MISSING"
    POLICY_VERSION_UNSUPPORTED = "POLICY_VERSION_UNSUPPORTED"
    VERSION_IDENTITY_MISMATCH = "VERSION_IDENTITY_MISMATCH"
    STRATEGY_SEMVER_MISMATCH = "STRATEGY_SEMVER_MISMATCH"
    PARAMETER_HASH_MISMATCH = "PARAMETER_HASH_MISMATCH"
    CODE_HASH_MISMATCH = "CODE_HASH_MISMATCH"
    UNIVERSE_HASH_MISMATCH = "UNIVERSE_HASH_MISMATCH"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    EVIDENCE_NOT_AUTHENTICATED = "EVIDENCE_NOT_AUTHENTICATED"
    GATE_NOT_PASSED = "GATE_NOT_PASSED"
    GATE_IDENTITY_MISMATCH = "GATE_IDENTITY_MISMATCH"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    REVOKED_AUTHENTICATION = "REVOKED_AUTHENTICATION"
    UNKNOWN_AUTHENTICATION_KEY = "UNKNOWN_AUTHENTICATION_KEY"
    TRUST_ROOT_UNAVAILABLE = "TRUST_ROOT_UNAVAILABLE"
    DUPLICATE_EVIDENCE_IDENTITY = "DUPLICATE_EVIDENCE_IDENTITY"
    REQUIRED_SYMBOLS_UNCOVERED = "REQUIRED_SYMBOLS_UNCOVERED"
    INSUFFICIENT_DISTINCT_REVIEW_RUNS = "INSUFFICIENT_DISTINCT_REVIEW_RUNS"
    INSUFFICIENT_DISTINCT_DATA_HASHES = "INSUFFICIENT_DISTINCT_DATA_HASHES"


@dataclass(frozen=True, slots=True)
class StrategyCoveragePolicy:
    """The explicit, versioned thresholds a coverage claim is made against.

    Deliberately has no defaults.  Every field that could widen a claim --
    the required symbols, the required identity hashes, both distinct-evidence
    minimums -- must be stated by whoever authors the policy, so there is no
    "forgot to configure it" instance that quietly authorises everything.
    """

    policy_id: str
    revision: int
    policy_version: str
    required_symbols: tuple[str, ...]
    required_universe_hash: str
    required_code_hash: str
    min_distinct_review_runs: int
    min_distinct_data_hashes: int
    maximum_evidence_age: timedelta | None
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.policy_id, "policy_id")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("revision must be a positive integer")
        _require_text(self.policy_version, "policy_version")
        if not isinstance(self.required_symbols, tuple) or not self.required_symbols:
            raise ValueError("required_symbols must be a nonempty tuple")
        for symbol in self.required_symbols:
            _require_text(symbol, "required symbol")
        if len(set(self.required_symbols)) != len(self.required_symbols):
            raise ValueError("required_symbols must not repeat")
        _require_text(self.required_universe_hash, "required_universe_hash")
        _require_text(self.required_code_hash, "required_code_hash")
        for name in ("min_distinct_review_runs", "min_distinct_data_hashes"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.maximum_evidence_age is not None:
            if not isinstance(self.maximum_evidence_age, timedelta):
                raise TypeError("maximum_evidence_age must be timedelta or None")
            if self.maximum_evidence_age <= timedelta(0):
                raise ValueError("maximum_evidence_age must be positive")
        _require_aware(self.created_at, "created_at")

    @property
    def identity(self) -> tuple[str, int]:
        return (self.policy_id, self.revision)


@dataclass(frozen=True, slots=True)
class StrategyCoverageEvidence:
    """One authenticated artifact paired with the gate that admitted it."""

    authenticated: AuthenticatedStrategyResearchEvidence
    gate: StrategyGateEvaluation

    def __post_init__(self) -> None:
        if not isinstance(self.authenticated, AuthenticatedStrategyResearchEvidence):
            raise TypeError("authenticated must be AuthenticatedStrategyResearchEvidence")
        if not isinstance(self.gate, StrategyGateEvaluation):
            raise TypeError("gate must be StrategyGateEvaluation")


@dataclass(frozen=True, slots=True)
class StrategyCoverageItem:
    """One piece of evidence that counted toward the coverage claim."""

    symbol: str
    review_run_id: str
    data_hash: str
    key_id: str
    authentication_id: str
    gate_evaluation_id: str
    signed_at: datetime
    generated_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "symbol", "review_run_id", "data_hash", "key_id",
            "authentication_id", "gate_evaluation_id",
        ):
            _require_text(getattr(self, name), name)
        _require_aware(self.signed_at, "signed_at")
        _require_aware(self.generated_at, "generated_at")

    @property
    def identity(self) -> tuple[str, str]:
        return (self.review_run_id, self.authentication_id)


@dataclass(frozen=True, slots=True)
class StrategyCoverageEvaluation:
    """The durable record of one coverage decision.

    ``items`` names the exact evidence that satisfied the policy, so the
    question "why did we believe this version was covered?" is answerable later
    without re-deriving anything.  The exact policy revision and the exact
    authentication and gate evaluation ids are part of the record, not
    reconstructible from it.
    """

    evaluation_id: str
    strategy_version_id: str
    strategy_semver: str
    parameter_hash: str
    universe_hash: str
    code_hash: str
    policy_id: str | None
    policy_revision: int | None
    policy_version: str | None
    verdict: StrategyCoverageVerdict
    blockers: tuple[StrategyCoverageBlocker, ...]
    items: tuple[StrategyCoverageItem, ...]
    covered_symbols: tuple[str, ...]
    required_symbols: tuple[str, ...]
    distinct_review_runs: int
    distinct_data_hashes: int
    evaluator_version: str
    evaluated_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "evaluation_id", "strategy_version_id", "strategy_semver",
            "parameter_hash", "universe_hash", "code_hash", "evaluator_version",
        ):
            _require_text(getattr(self, name), name)
        for name in ("policy_id", "policy_version"):
            value = getattr(self, name)
            if value is not None:
                _require_text(value, name)
        if self.policy_revision is not None:
            if type(self.policy_revision) is not int or self.policy_revision < 1:
                raise ValueError("policy_revision must be a positive integer or None")
        if not isinstance(self.verdict, StrategyCoverageVerdict):
            raise TypeError("verdict must be StrategyCoverageVerdict")
        if not isinstance(self.blockers, tuple):
            raise TypeError("blockers must be a tuple")
        if any(
            not isinstance(item, StrategyCoverageBlocker) for item in self.blockers
        ):
            raise TypeError("blockers must contain StrategyCoverageBlocker values")
        object.__setattr__(
            self, "blockers",
            tuple(sorted(set(self.blockers), key=lambda item: item.value)),
        )
        if (self.verdict is StrategyCoverageVerdict.PASS) != (not self.blockers):
            raise ValueError("PASS requires no blockers and FAIL requires blockers")
        if not isinstance(self.items, tuple):
            raise TypeError("items must be a tuple")
        if any(not isinstance(item, StrategyCoverageItem) for item in self.items):
            raise TypeError("items must contain StrategyCoverageItem values")
        for name in ("covered_symbols", "required_symbols"):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                raise TypeError(f"{name} must be a tuple")
            for entry in value:
                _require_text(entry, f"{name} entry")
        for name in ("distinct_review_runs", "distinct_data_hashes"):
            _require_count(getattr(self, name), name)
        if self.verdict is StrategyCoverageVerdict.PASS:
            if self.policy_id is None or self.policy_revision is None:
                raise ValueError("PASS requires the policy it was evaluated against")
            if not self.items:
                raise ValueError("PASS requires at least one admitted evidence item")
        _require_aware(self.evaluated_at, "evaluated_at")

    @property
    def policy_identity(self) -> tuple[str, int] | None:
        if self.policy_id is None or self.policy_revision is None:
            return None
        return (self.policy_id, self.policy_revision)

    @property
    def authentication_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.authentication_id for item in self.items}))

    @property
    def gate_evaluation_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.gate_evaluation_id for item in self.items}))


def stable_strategy_coverage_evaluation_id(
    *,
    strategy_version_id: str,
    strategy_semver: str,
    parameter_hash: str,
    universe_hash: str,
    code_hash: str,
    policy_id: str | None,
    policy_revision: int | None,
    policy_version: str | None,
    verdict: StrategyCoverageVerdict,
    blockers: tuple[StrategyCoverageBlocker, ...],
    items: tuple[StrategyCoverageItem, ...],
    evaluator_version: str,
) -> str:
    """Deterministic identity of one semantic coverage outcome.

    ``evaluated_at`` is excluded so re-evaluating unchanged inputs is
    idempotent, while a changed evidence set or verdict produces a new id and a
    new row -- which is what keeps the earlier claim intact for audit.
    """

    material = {
        "strategy_version_id": strategy_version_id,
        "strategy_semver": strategy_semver,
        "parameter_hash": parameter_hash,
        "universe_hash": universe_hash,
        "code_hash": code_hash,
        "policy_id": policy_id,
        "policy_revision": policy_revision,
        "policy_version": policy_version,
        "verdict": verdict.value,
        "blockers": sorted({item.value for item in blockers}),
        "items": sorted(
            [
                {
                    "symbol": item.symbol,
                    "review_run_id": item.review_run_id,
                    "data_hash": item.data_hash,
                    "key_id": item.key_id,
                    "authentication_id": item.authentication_id,
                    "gate_evaluation_id": item.gate_evaluation_id,
                }
                for item in items
            ],
            key=lambda entry: (
                entry["review_run_id"], entry["authentication_id"],
            ),
        ),
        "evaluator_version": evaluator_version,
    }
    digest = sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return f"sce-{digest}"


def policy_to_payload(policy: StrategyCoveragePolicy) -> dict[str, object]:
    """Serialise a policy immutably; it is stored, never edited in place."""

    if not isinstance(policy, StrategyCoveragePolicy):
        raise TypeError("policy must be StrategyCoveragePolicy")
    return {
        "policy_id": policy.policy_id,
        "revision": policy.revision,
        "policy_version": policy.policy_version,
        "required_symbols": list(policy.required_symbols),
        "required_universe_hash": policy.required_universe_hash,
        "required_code_hash": policy.required_code_hash,
        "min_distinct_review_runs": policy.min_distinct_review_runs,
        "min_distinct_data_hashes": policy.min_distinct_data_hashes,
        "maximum_evidence_age_seconds": (
            None
            if policy.maximum_evidence_age is None
            else int(policy.maximum_evidence_age.total_seconds())
        ),
        "created_at": policy.created_at.isoformat(),
    }


def policy_from_payload(payload: object) -> StrategyCoveragePolicy:
    """Parse a stored policy, raising :class:`CoveragePolicyMalformed` on refuse."""

    if not isinstance(payload, dict):
        raise CoveragePolicyMalformed("policy payload must be an object")
    values = dict(payload)
    symbols = values.get("required_symbols")
    if not isinstance(symbols, list):
        raise CoveragePolicyMalformed("policy required_symbols must be a list")
    age_seconds = values.get("maximum_evidence_age_seconds")
    if age_seconds is not None and (type(age_seconds) is not int or age_seconds < 1):
        raise CoveragePolicyMalformed("policy maximum_evidence_age_seconds is invalid")
    created_at = values.get("created_at")
    if not isinstance(created_at, str) or not created_at.strip():
        raise CoveragePolicyMalformed("policy created_at is missing")
    try:
        parsed_created_at = datetime.fromisoformat(created_at)
    except ValueError as error:
        raise CoveragePolicyMalformed("policy created_at is not a timestamp") from error
    try:
        return StrategyCoveragePolicy(
            policy_id=values["policy_id"],
            revision=values["revision"],
            policy_version=values["policy_version"],
            required_symbols=tuple(symbols),
            required_universe_hash=values["required_universe_hash"],
            required_code_hash=values["required_code_hash"],
            min_distinct_review_runs=values["min_distinct_review_runs"],
            min_distinct_data_hashes=values["min_distinct_data_hashes"],
            maximum_evidence_age=(
                None if age_seconds is None else timedelta(seconds=age_seconds)
            ),
            created_at=parsed_created_at,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CoveragePolicyMalformed(f"policy is invalid: {error}") from error


class CoveragePolicyMalformed(ValueError):
    """A stored coverage policy cannot be read as a policy."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank text")


def _require_count(value: object, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _require_aware(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "COVERAGE_EVALUATOR_VERSION",
    "CoveragePolicyMalformed",
    "SUPPORTED_COVERAGE_POLICY_VERSIONS",
    "StrategyCoverageBlocker",
    "StrategyCoverageEvaluation",
    "StrategyCoverageEvidence",
    "StrategyCoverageItem",
    "StrategyCoveragePolicy",
    "StrategyCoverageVerdict",
    "policy_from_payload",
    "policy_to_payload",
    "stable_strategy_coverage_evaluation_id",
]
