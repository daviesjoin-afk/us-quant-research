"""Immutable, provider-neutral evidence and evaluation records for strategy governance.

This module records whether one symbol-specific TargetedReview artifact is
bound to one immutable StrategyVersion. It does not change strategy lifecycle
state and does not duplicate any statistical research thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
import json


ELIGIBLE_REVIEW_DECISION = "ELIGIBLE_FOR_INDEPENDENT_REVIEW"


class StrategyGateVerdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class StrategyGateBlocker(StrEnum):
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    EVIDENCE_UNREADABLE = "EVIDENCE_UNREADABLE"
    EVIDENCE_NOT_ELIGIBLE = "EVIDENCE_NOT_ELIGIBLE"
    VERSION_ID_MISMATCH = "VERSION_ID_MISMATCH"
    STRATEGY_SEMVER_MISMATCH = "STRATEGY_SEMVER_MISMATCH"
    PARAMETER_HASH_MISMATCH = "PARAMETER_HASH_MISMATCH"
    EVIDENCE_ORIGIN_INVALID = "EVIDENCE_ORIGIN_INVALID"
    REVIEW_IDENTITY_MISMATCH = "REVIEW_IDENTITY_MISMATCH"
    REVIEW_BLOCKING_FAILURES = "REVIEW_BLOCKING_FAILURES"
    DUPLICATE_EVIDENCE = "DUPLICATE_EVIDENCE"
    STALE_EVIDENCE = "STALE_EVIDENCE"


@dataclass(frozen=True, slots=True)
class StrategyEvidenceIdentity:
    strategy_version_id: str
    strategy_semver: str
    parameter_hash: str
    symbol: str
    data_hash: str
    provider: str

    def __post_init__(self) -> None:
        for name in (
            "strategy_version_id", "strategy_semver", "parameter_hash",
            "symbol", "data_hash", "provider",
        ):
            _require_text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class StrategyResearchEvidence:
    """A typed projection of a single symbol-specific research review."""

    review_run_id: str
    artifact_review_run_id: str
    robustness_run_id: str
    validation_run_id: str | None
    overfit_run_id: str
    data_quality_run_id: str | None
    execution_stress_run_id: str | None
    identity: StrategyEvidenceIdentity
    evidence_origins: tuple[str, ...]
    decision: str
    eligible_for_independent_review: bool
    blocking_failures: int
    passed_gates: int
    gate_count: int
    generated_at: datetime

    def __post_init__(self) -> None:
        for name in ("review_run_id", "artifact_review_run_id", "robustness_run_id", "overfit_run_id"):
            _require_text(getattr(self, name), name)
        for name in ("validation_run_id", "data_quality_run_id", "execution_stress_run_id"):
            value = getattr(self, name)
            if value is not None:
                _require_text(value, name)
        if not isinstance(self.identity, StrategyEvidenceIdentity):
            raise TypeError("identity must be StrategyEvidenceIdentity")
        if not isinstance(self.evidence_origins, tuple):
            raise TypeError("evidence_origins must be a tuple")
        for origin in self.evidence_origins:
            _require_text(origin, "evidence origin")
        _require_text(self.decision, "decision")
        if type(self.eligible_for_independent_review) is not bool:
            raise TypeError("eligible_for_independent_review must be bool")
        _require_count(self.blocking_failures, "blocking_failures")
        _require_count(self.passed_gates, "passed_gates")
        _require_count(self.gate_count, "gate_count")
        if self.passed_gates > self.gate_count:
            raise ValueError("passed_gates cannot exceed gate_count")
        _require_aware(self.generated_at, "generated_at")

    @property
    def component_run_ids(self) -> tuple[str, ...]:
        return tuple(
            value for value in (
                self.robustness_run_id,
                self.validation_run_id,
                self.overfit_run_id,
                self.data_quality_run_id,
                self.execution_stress_run_id,
            ) if value is not None
        )


@dataclass(frozen=True, slots=True)
class StrategyGatePolicy:
    evaluator_version: str = "strategy-gate-v1"
    policy_version: str = "independent-review-v1"
    maximum_evidence_age: timedelta | None = None

    def __post_init__(self) -> None:
        _require_text(self.evaluator_version, "evaluator_version")
        _require_text(self.policy_version, "policy_version")
        if self.maximum_evidence_age is not None:
            if not isinstance(self.maximum_evidence_age, timedelta):
                raise TypeError("maximum_evidence_age must be timedelta or None")
            if self.maximum_evidence_age <= timedelta(0):
                raise ValueError("maximum_evidence_age must be positive")


@dataclass(frozen=True, slots=True)
class StrategyGateEvaluation:
    evaluation_id: str
    strategy_version_id: str
    review_run_id: str | None
    parameter_hash: str | None
    data_hash: str | None
    symbol: str | None
    provider: str | None
    verdict: StrategyGateVerdict
    blockers: tuple[StrategyGateBlocker, ...]
    review_decision: str | None
    review_blocking_failures: int | None
    review_passed_gates: int | None
    review_gate_count: int | None
    evaluator_version: str
    policy_version: str
    evaluated_at: datetime

    def __post_init__(self) -> None:
        for name in ("evaluation_id", "strategy_version_id", "evaluator_version", "policy_version"):
            _require_text(getattr(self, name), name)
        for name in ("review_run_id", "parameter_hash", "data_hash", "symbol", "provider", "review_decision"):
            value = getattr(self, name)
            if value is not None:
                _require_text(value, name)
        if not isinstance(self.verdict, StrategyGateVerdict):
            raise TypeError("verdict must be StrategyGateVerdict")
        if not isinstance(self.blockers, tuple):
            raise TypeError("blockers must be a tuple")
        if any(not isinstance(item, StrategyGateBlocker) for item in self.blockers):
            raise TypeError("blockers must contain StrategyGateBlocker values")
        canonical = tuple(sorted(set(self.blockers), key=lambda item: item.value))
        object.__setattr__(self, "blockers", canonical)
        if (self.verdict is StrategyGateVerdict.PASS) != (not self.blockers):
            raise ValueError("PASS requires no blockers and FAIL requires blockers")
        if self.verdict is StrategyGateVerdict.PASS and self.review_run_id is None:
            raise ValueError("PASS requires a review run id")
        if self.review_run_id is None and not {
            StrategyGateBlocker.EVIDENCE_MISSING,
            StrategyGateBlocker.EVIDENCE_UNREADABLE,
        }.intersection(self.blockers):
            raise ValueError("missing review run id requires missing or unreadable evidence")
        for name in ("review_blocking_failures", "review_passed_gates", "review_gate_count"):
            value = getattr(self, name)
            if value is not None:
                _require_count(value, name)
        if self.review_passed_gates is not None and self.review_gate_count is not None:
            if self.review_passed_gates > self.review_gate_count:
                raise ValueError("review_passed_gates cannot exceed review_gate_count")
        _require_aware(self.evaluated_at, "evaluated_at")


def stable_strategy_gate_evaluation_id(
    *,
    strategy_version_id: str,
    review_run_id: str | None,
    parameter_hash: str | None,
    data_hash: str | None,
    policy_version: str,
    evaluator_version: str,
    verdict: StrategyGateVerdict,
    blockers: tuple[StrategyGateBlocker, ...],
    symbol: str | None = None,
    provider: str | None = None,
    review_decision: str | None = None,
    review_blocking_failures: int | None = None,
    review_passed_gates: int | None = None,
    review_gate_count: int | None = None,
) -> str:
    """Return a deterministic identity for one semantic evaluation state."""

    material = {
        "strategy_version_id": strategy_version_id,
        "review_run_id": review_run_id,
        "parameter_hash": parameter_hash,
        "data_hash": data_hash,
        "symbol": symbol,
        "provider": provider,
        "policy_version": policy_version,
        "evaluator_version": evaluator_version,
        "verdict": verdict.value,
        "blockers": sorted({item.value for item in blockers}),
        "review_decision": review_decision,
        "review_blocking_failures": review_blocking_failures,
        "review_passed_gates": review_passed_gates,
        "review_gate_count": review_gate_count,
    }
    digest = sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return f"sge-{digest}"


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
    "ELIGIBLE_REVIEW_DECISION",
    "StrategyEvidenceIdentity",
    "StrategyGateBlocker",
    "StrategyGateEvaluation",
    "StrategyGatePolicy",
    "StrategyGateVerdict",
    "StrategyResearchEvidence",
    "stable_strategy_gate_evaluation_id",
]
