"""Translate TargetedReview artifacts into provider-neutral strategy evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from us_quant.targeted_review import (
    TargetedReviewResult,
    targeted_review_from_payload,
)

from us_quant.trading.domain.strategy_gate import (
    StrategyEvidenceIdentity,
    StrategyResearchEvidence,
)


class StrategyEvidenceProjectionError(ValueError):
    """The research artifact cannot be represented as trusted gate evidence."""


_LOADER_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class LoadedTargetedReviewArtifact:
    """A TargetedReview result paired with metadata read from its stored file."""

    result: TargetedReviewResult
    artifact_run_id: str
    generated_at: datetime
    source_path: Path

    def __init__(
        self,
        result: TargetedReviewResult,
        artifact_run_id: str,
        generated_at: datetime,
        source_path: Path,
        *,
        _loader_token: object,
    ) -> None:
        if _loader_token is not _LOADER_TOKEN:
            raise TypeError("LoadedTargetedReviewArtifact must come from its loader")
        if result.run_id != artifact_run_id or source_path.stem != artifact_run_id:
            raise ValueError("TargetedReview artifact identity does not match its file")
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            raise ValueError("TargetedReview generated_at must be timezone-aware")
        object.__setattr__(self, "result", result)
        object.__setattr__(self, "artifact_run_id", artifact_run_id)
        object.__setattr__(self, "generated_at", generated_at)
        object.__setattr__(self, "source_path", source_path)


def load_targeted_review_artifact(path: str | Path) -> LoadedTargetedReviewArtifact:
    """Load a review and provenance metadata from its persisted JSON artifact."""

    artifact_path = Path(path).resolve()
    try:
        row = json.loads(artifact_path.read_text(encoding="utf-8"))
        if not isinstance(row, dict):
            raise ValueError("artifact root must be an object")
        for key in (
            "run_id", "robustness_run_id", "overfit_run_id", "symbol",
            "strategy_version_id", "strategy_semver", "base_parameter_hash",
            "data_hash", "provider", "decision", "status",
        ):
            value = row.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"artifact {key} is missing or invalid")
        for key in ("validation_run_id", "data_quality_run_id", "execution_stress_run_id"):
            value = row.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"artifact {key} is invalid")
        origins = row.get("evidence_origins")
        if not isinstance(origins, list) or any(
            not isinstance(item, str) or not item.strip() for item in origins
        ):
            raise ValueError("artifact evidence_origins is invalid")
        artifact_run_id = row.get("run_id")
        if not isinstance(artifact_run_id, str) or not artifact_run_id.strip():
            raise ValueError("artifact run_id is missing")
        if artifact_path.suffix.lower() != ".json" or artifact_path.stem != artifact_run_id:
            raise ValueError("artifact filename does not match its run_id")
        generated_at_text = row.get("generated_at")
        if not isinstance(generated_at_text, str) or not generated_at_text.strip():
            raise ValueError("artifact generated_at is missing")
        generated_at = datetime.fromisoformat(generated_at_text)
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            raise ValueError("artifact generated_at must be timezone-aware")
        result = targeted_review_from_payload(row)
        if result.run_id != artifact_run_id:
            raise ValueError("parsed TargetedReview run_id does not match its file")
        return LoadedTargetedReviewArtifact(
            result,
            artifact_run_id,
            generated_at,
            artifact_path,
            _loader_token=_LOADER_TOKEN,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise StrategyEvidenceProjectionError(
            "TargetedReview artifact is missing, malformed, or inconsistent"
        ) from error


def project_targeted_review(
    artifact: LoadedTargetedReviewArtifact,
) -> StrategyResearchEvidence:
    """Project facts loaded from the persisted artifact; compute no statistics."""

    if not isinstance(artifact, LoadedTargetedReviewArtifact):
        raise StrategyEvidenceProjectionError("artifact must come from the artifact loader")
    result = artifact.result
    try:
        return StrategyResearchEvidence(
            review_run_id=result.run_id,
            artifact_review_run_id=artifact.artifact_run_id,
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
            generated_at=artifact.generated_at,
        )
    except (TypeError, ValueError) as error:
        raise StrategyEvidenceProjectionError(
            "TargetedReview artifact has incomplete or invalid identity"
        ) from error


__all__ = [
    "LoadedTargetedReviewArtifact",
    "StrategyEvidenceProjectionError",
    "load_targeted_review_artifact",
    "project_targeted_review",
]
