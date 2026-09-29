"""Translate TargetedReview artifacts into provider-neutral strategy evidence."""

from __future__ import annotations

from datetime import datetime

from us_quant.targeted_review import TargetedReviewResult
from us_quant.trading.domain.strategy_gate import (
    StrategyEvidenceIdentity,
    StrategyResearchEvidence,
)


class StrategyEvidenceProjectionError(ValueError):
    """The research artifact cannot be represented as trusted gate evidence."""


def project_targeted_review(
    result: TargetedReviewResult,
    *,
    artifact_review_run_id: str,
    generated_at: datetime,
) -> StrategyResearchEvidence:
    """Copy identity and eligibility facts; compute no statistics or thresholds.

    ``generated_at`` and ``artifact_review_run_id`` come from the persisted
    TargetedReview artifact envelope (or its filename). They are explicit
    because ``TargetedReviewResult`` itself intentionally has no timestamp.
    """

    if not isinstance(result, TargetedReviewResult):
        raise StrategyEvidenceProjectionError("result is not TargetedReviewResult")
    try:
        return StrategyResearchEvidence(
            review_run_id=result.run_id,
            artifact_review_run_id=artifact_review_run_id,
            robustness_run_id=result.robustness_run_id,
            validation_run_id=result.validation_run_id,
            overfit_run_id=result.overfit_run_id,
            data_quality_run_id=result.data_quality_run_id,
            execution_stress_run_id=result.execution_stress_run_id,
            identity=StrategyEvidenceIdentity(
                strategy_version_id=result.strategy_version_id,
                strategy_semver=result.strategy_semver,
                parameter_hash=result.base_parameter_hash,
                symbol=result.symbol,
                data_hash=result.data_hash,
                provider=result.provider,
            ),
            evidence_origins=tuple(result.evidence_origins),
            decision=result.decision,
            eligible_for_independent_review=result.eligible_for_independent_review,
            blocking_failures=result.blocking_failures,
            passed_gates=result.passed_gates,
            gate_count=len(result.gates),
            generated_at=generated_at,
        )
    except (TypeError, ValueError) as error:
        raise StrategyEvidenceProjectionError(
            "TargetedReview artifact has incomplete or invalid identity"
        ) from error


__all__ = ["StrategyEvidenceProjectionError", "project_targeted_review"]
