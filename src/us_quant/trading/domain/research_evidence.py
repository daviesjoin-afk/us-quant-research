"""Provider-neutral projection of one symbol-specific research review.

These types describe *what a review found* and nothing about what any policy
decides to do with it.  They live apart from the gate evaluator on purpose:
authentication (Stage 6-B1) and coverage (Stage 6-B2) both consume research
evidence, and neither should have to depend on the gate's module to do it.

Nothing here computes statistics.  Every field is a fact read from a persisted
TargetedReview artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


#: The only review decision that may be treated as admissible evidence.
ELIGIBLE_REVIEW_DECISION = "ELIGIBLE_FOR_INDEPENDENT_REVIEW"


@dataclass(frozen=True, slots=True)
class StrategyEvidenceIdentity:
    """The five hashes plus the symbol that name one piece of evidence."""

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
        for name in (
            "review_run_id", "artifact_review_run_id", "robustness_run_id",
            "overfit_run_id",
        ):
            _require_text(getattr(self, name), name)
        for name in (
            "validation_run_id", "data_quality_run_id", "execution_stress_run_id",
        ):
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
class LoadedResearchEvidence:
    """One validated review artifact: the payload read plus its projection.

    Carrying both is deliberate.  A detached seal is checked against the
    canonical digest of the payload the evidence was projected from, so the two
    must come from a single read -- re-reading the file would reopen a window in
    which the bytes that were digested and the bytes that were parsed could
    differ.
    """

    payload: Mapping[str, Any]
    evidence: StrategyResearchEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        if not isinstance(self.evidence, StrategyResearchEvidence):
            raise TypeError("evidence must be StrategyResearchEvidence")


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
    "LoadedResearchEvidence",
    "StrategyEvidenceIdentity",
    "StrategyResearchEvidence",
]
