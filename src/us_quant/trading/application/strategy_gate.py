"""Pure governance evidence evaluator for immutable strategy versions."""

from __future__ import annotations

from datetime import datetime, timezone

from us_quant.trading.domain.strategy import StrategyVersion
from us_quant.trading.domain.strategy_gate import (
    ELIGIBLE_REVIEW_DECISION,
    StrategyGateBlocker,
    StrategyGateEvaluation,
    StrategyGatePolicy,
    StrategyGateVerdict,
    StrategyResearchEvidence,
    stable_strategy_gate_evaluation_id,
)


class StrategyGateEvaluator:
    """Decide whether one review artifact is admissible for one version.

    This class deliberately owns no repository and performs no lifecycle
    writes. Statistical eligibility remains exclusively in TargetedReview.
    """

    def evaluate(
        self,
        *,
        version: StrategyVersion,
        evidence: StrategyResearchEvidence | None,
        policy: StrategyGatePolicy,
        evaluated_at: datetime,
    ) -> StrategyGateEvaluation:
        if not isinstance(version, StrategyVersion):
            raise TypeError("version must be StrategyVersion")
        if not isinstance(policy, StrategyGatePolicy):
            raise TypeError("policy must be StrategyGatePolicy")
        if not isinstance(evaluated_at, datetime):
            raise TypeError("evaluated_at must be datetime")
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")

        blockers: set[StrategyGateBlocker] = set()
        if evidence is None:
            blockers.add(StrategyGateBlocker.EVIDENCE_MISSING)
        elif not isinstance(evidence, StrategyResearchEvidence):
            blockers.add(StrategyGateBlocker.EVIDENCE_UNREADABLE)
            evidence = None
        else:
            self._evaluate_identity(version, evidence, blockers)
            self._evaluate_review(evidence, blockers)
            self._evaluate_evidence_quality(evidence, policy, evaluated_at, blockers)

        identity = evidence.identity if evidence is not None else None
        review_run_id = evidence.review_run_id if evidence is not None else None
        blockers_tuple = tuple(sorted(blockers, key=lambda item: item.value))
        verdict = StrategyGateVerdict.PASS if not blockers_tuple else StrategyGateVerdict.FAIL
        evaluation_id = stable_strategy_gate_evaluation_id(
            strategy_version_id=version.version_id,
            review_run_id=review_run_id,
            parameter_hash=identity.parameter_hash if identity else version.parameter_hash,
            data_hash=identity.data_hash if identity else None,
            symbol=identity.symbol if identity else None,
            policy_version=policy.policy_version,
            evaluator_version=policy.evaluator_version,
            evaluated_at=evaluated_at,
        )
        return StrategyGateEvaluation(
            evaluation_id=evaluation_id,
            strategy_version_id=version.version_id,
            review_run_id=review_run_id,
            parameter_hash=identity.parameter_hash if identity else None,
            data_hash=identity.data_hash if identity else None,
            symbol=identity.symbol if identity else None,
            provider=identity.provider if identity else None,
            verdict=verdict,
            blockers=blockers_tuple,
            review_decision=evidence.decision if evidence else None,
            review_blocking_failures=evidence.blocking_failures if evidence else None,
            review_passed_gates=evidence.passed_gates if evidence else None,
            review_gate_count=evidence.gate_count if evidence else None,
            evaluator_version=policy.evaluator_version,
            policy_version=policy.policy_version,
            evaluated_at=evaluated_at,
        )

    @staticmethod
    def _evaluate_identity(version, evidence, blockers) -> None:
        identity = evidence.identity
        if evidence.artifact_review_run_id != evidence.review_run_id:
            blockers.add(StrategyGateBlocker.REVIEW_IDENTITY_MISMATCH)
        if identity.strategy_version_id != version.version_id:
            blockers.add(StrategyGateBlocker.VERSION_ID_MISMATCH)
        if identity.strategy_semver != version.semver:
            blockers.add(StrategyGateBlocker.STRATEGY_SEMVER_MISMATCH)
        if identity.parameter_hash != version.parameter_hash:
            blockers.add(StrategyGateBlocker.PARAMETER_HASH_MISMATCH)

    @staticmethod
    def _evaluate_review(evidence, blockers) -> None:
        if (
            not evidence.eligible_for_independent_review
            or evidence.decision != ELIGIBLE_REVIEW_DECISION
        ):
            blockers.add(StrategyGateBlocker.EVIDENCE_NOT_ELIGIBLE)
        if evidence.blocking_failures > 0:
            blockers.add(StrategyGateBlocker.REVIEW_BLOCKING_FAILURES)
        if evidence.gate_count == 0:
            blockers.add(StrategyGateBlocker.EVIDENCE_UNREADABLE)

    @staticmethod
    def _evaluate_evidence_quality(evidence, policy, evaluated_at, blockers) -> None:
        if evidence.evidence_origins != ("captured_stream",):
            blockers.add(StrategyGateBlocker.EVIDENCE_ORIGIN_INVALID)

        run_ids = (evidence.review_run_id, *evidence.component_run_ids)
        if len(run_ids) != len(set(run_ids)):
            blockers.add(StrategyGateBlocker.DUPLICATE_EVIDENCE)

        generated_at = evidence.generated_at.astimezone(timezone.utc)
        evaluation_time = evaluated_at.astimezone(timezone.utc)
        if generated_at > evaluation_time:
            blockers.add(StrategyGateBlocker.EVIDENCE_UNREADABLE)
        elif (
            policy.maximum_evidence_age is not None
            and evaluation_time - generated_at > policy.maximum_evidence_age
        ):
            blockers.add(StrategyGateBlocker.STALE_EVIDENCE)


__all__ = ["StrategyGateEvaluator"]
