"""Compose authenticated evidence and gate PASSes into a bounded coverage decision.

This service answers exactly one question: was this strategy version's required
universe covered by evidence that is both authenticated and gate-passed?

It deliberately does not recompute anything statistical.  PBO, DSR,
walk-forward, HAC and data-quality thresholds stay in TargetedReview, and this
module never names them.  It also owns no repository and writes no lifecycle
state -- coverage PASS does not promote and coverage FAIL does not pause.
"""

from __future__ import annotations

from datetime import datetime, timezone

from us_quant.trading.domain.evidence_auth import (
    EvidenceAuthenticationVerdict,
    EvidenceKeyTrustStatus,
)
from us_quant.trading.domain.strategy import StrategyVersion
from us_quant.trading.domain.strategy_coverage import (
    COVERAGE_EVALUATOR_VERSION,
    SUPPORTED_COVERAGE_POLICY_VERSIONS,
    StrategyCoverageBlocker,
    StrategyCoverageEvaluation,
    StrategyCoverageEvidence,
    StrategyCoverageItem,
    StrategyCoveragePolicy,
    StrategyCoverageVerdict,
    stable_strategy_coverage_evaluation_id,
)
from us_quant.trading.domain.strategy_gate import StrategyGateVerdict
from us_quant.trading.ports.evidence_verification import EvidenceTrustRootUnavailable


class StrategyCoverageEvaluator:
    """Decide whether one governed version's universe is covered."""

    def __init__(self, *, key_source) -> None:
        if key_source is None:
            raise TypeError("key_source is required")
        self._key_source = key_source

    def evaluate(
        self,
        *,
        version: StrategyVersion,
        policy: StrategyCoveragePolicy | None,
        evidence: tuple[StrategyCoverageEvidence, ...],
        evaluated_at: datetime,
    ) -> StrategyCoverageEvaluation:
        if not isinstance(version, StrategyVersion):
            raise TypeError("version must be StrategyVersion")
        if not isinstance(evaluated_at, datetime):
            raise TypeError("evaluated_at must be a datetime")
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        if not isinstance(evidence, tuple):
            raise TypeError("evidence must be a tuple")

        # No policy, or an unreadable one, authorises nothing.  There is no
        # fallback threshold hiding in this module.
        if not isinstance(policy, StrategyCoveragePolicy):
            return self._build(
                version=version,
                policy=None,
                blockers={StrategyCoverageBlocker.POLICY_MISSING},
                items=(),
                evaluated_at=evaluated_at,
            )

        blockers: set[StrategyCoverageBlocker] = set()
        if policy.policy_version not in SUPPORTED_COVERAGE_POLICY_VERSIONS:
            blockers.add(StrategyCoverageBlocker.POLICY_VERSION_UNSUPPORTED)

        # The policy, not this code, states which universe and code revision
        # the evidence had to be produced under.
        if policy.required_universe_hash != version.universe_hash:
            blockers.add(StrategyCoverageBlocker.UNIVERSE_HASH_MISMATCH)
        if policy.required_code_hash != version.code_hash:
            blockers.add(StrategyCoverageBlocker.CODE_HASH_MISMATCH)

        required = set(policy.required_symbols)
        if not evidence:
            blockers.add(StrategyCoverageBlocker.EVIDENCE_MISSING)

        items: list[StrategyCoverageItem] = []
        seen_review_runs: set[str] = set()
        for entry in evidence:
            item = self._admit(
                entry,
                version=version,
                policy=policy,
                required=required,
                seen_review_runs=seen_review_runs,
                evaluated_at=evaluated_at,
                blockers=blockers,
            )
            if item is not None:
                items.append(item)

        items.sort(key=lambda item: (item.review_run_id, item.authentication_id))
        admitted = tuple(items)
        covered = tuple(sorted({item.symbol for item in admitted}))

        # The core refusal: one symbol's PASS never stands in for the universe.
        if not required <= set(covered):
            blockers.add(StrategyCoverageBlocker.REQUIRED_SYMBOLS_UNCOVERED)
        if len({item.review_run_id for item in admitted}) < policy.min_distinct_review_runs:
            blockers.add(StrategyCoverageBlocker.INSUFFICIENT_DISTINCT_REVIEW_RUNS)
        if len({item.data_hash for item in admitted}) < policy.min_distinct_data_hashes:
            blockers.add(StrategyCoverageBlocker.INSUFFICIENT_DISTINCT_DATA_HASHES)

        return self._build(
            version=version,
            policy=policy,
            blockers=blockers,
            items=admitted,
            evaluated_at=evaluated_at,
            covered_symbols=covered,
        )

    # -- per-item admission ----------------------------------------------

    def _admit(
        self,
        entry: StrategyCoverageEvidence,
        *,
        version: StrategyVersion,
        policy: StrategyCoveragePolicy,
        required: set[str],
        seen_review_runs: set[str],
        evaluated_at: datetime,
        blockers: set[StrategyCoverageBlocker],
    ) -> StrategyCoverageItem | None:
        if not isinstance(entry, StrategyCoverageEvidence):
            blockers.add(StrategyCoverageBlocker.EVIDENCE_NOT_AUTHENTICATED)
            return None
        authenticated = entry.authenticated
        gate = entry.gate

        # Authenticated evidence is PASS by construction, but re-asserting it
        # means a future construction path cannot weaken coverage by accident.
        if authenticated.authentication.verdict is not EvidenceAuthenticationVerdict.PASS:
            blockers.add(StrategyCoverageBlocker.EVIDENCE_NOT_AUTHENTICATED)
            return None

        # Bind the evidence to the governed version first: this is the primary
        # claim, and it is what the identity blockers are named after.
        if authenticated.strategy_version_id != version.version_id:
            blockers.add(StrategyCoverageBlocker.VERSION_IDENTITY_MISMATCH)
            return None
        if authenticated.strategy_semver != version.semver:
            blockers.add(StrategyCoverageBlocker.STRATEGY_SEMVER_MISMATCH)
            return None
        if authenticated.parameter_hash != version.parameter_hash:
            blockers.add(StrategyCoverageBlocker.PARAMETER_HASH_MISMATCH)
            return None
        if authenticated.universe_hash != version.universe_hash:
            blockers.add(StrategyCoverageBlocker.UNIVERSE_HASH_MISMATCH)
            return None
        if authenticated.code_hash != version.code_hash:
            blockers.add(StrategyCoverageBlocker.CODE_HASH_MISMATCH)
            return None

        # Then the gate evaluation that admitted it must be about this very
        # evidence and this very version, and it must have passed.
        if gate.strategy_version_id != version.version_id:
            blockers.add(StrategyCoverageBlocker.VERSION_IDENTITY_MISMATCH)
            return None
        if gate.review_run_id != authenticated.review_run_id:
            blockers.add(StrategyCoverageBlocker.GATE_IDENTITY_MISMATCH)
            return None
        if gate.parameter_hash != version.parameter_hash:
            blockers.add(StrategyCoverageBlocker.GATE_IDENTITY_MISMATCH)
            return None
        if gate.verdict is not StrategyGateVerdict.PASS:
            blockers.add(StrategyCoverageBlocker.GATE_NOT_PASSED)
            return None

        # Evidence outside the policy's scope cannot count toward its claim.
        # It is not a failure -- it is simply not this policy's business.
        if authenticated.symbol not in required:
            return None

        if authenticated.review_run_id in seen_review_runs:
            blockers.add(StrategyCoverageBlocker.DUPLICATE_EVIDENCE_IDENTITY)
            return None

        # Revocation is re-checked *here*, not only at authentication time: a
        # key revoked after the seal was verified must not keep propping up a
        # coverage claim.
        try:
            key = self._key_source.verification_key(authenticated.key_id)
        except EvidenceTrustRootUnavailable:
            blockers.add(StrategyCoverageBlocker.TRUST_ROOT_UNAVAILABLE)
            return None
        if key is None:
            blockers.add(StrategyCoverageBlocker.UNKNOWN_AUTHENTICATION_KEY)
            return None
        if key.trust_status is EvidenceKeyTrustStatus.REVOKED:
            blockers.add(StrategyCoverageBlocker.REVOKED_AUTHENTICATION)
            return None

        generated_at = authenticated.evidence.generated_at.astimezone(timezone.utc)
        moment = evaluated_at.astimezone(timezone.utc)
        if (
            policy.maximum_evidence_age is not None
            and moment - generated_at > policy.maximum_evidence_age
        ):
            blockers.add(StrategyCoverageBlocker.STALE_EVIDENCE)
            return None

        seen_review_runs.add(authenticated.review_run_id)
        return StrategyCoverageItem(
            symbol=authenticated.symbol,
            review_run_id=authenticated.review_run_id,
            data_hash=authenticated.data_hash,
            key_id=authenticated.key_id,
            authentication_id=authenticated.authentication.authentication_id,
            gate_evaluation_id=gate.evaluation_id,
            signed_at=authenticated.signed_at,
            generated_at=authenticated.evidence.generated_at,
        )

    # -- result construction ---------------------------------------------

    @staticmethod
    def _build(
        *,
        version: StrategyVersion,
        policy: StrategyCoveragePolicy | None,
        blockers: set[StrategyCoverageBlocker],
        items: tuple[StrategyCoverageItem, ...],
        evaluated_at: datetime,
        covered_symbols: tuple[str, ...] = (),
    ) -> StrategyCoverageEvaluation:
        # The verdict is derived, never asserted: PASS means the blocker set is
        # empty, and FAIL means it is not.  There is no defensive "add a reason"
        # step here on purpose -- injecting one would silently turn every PASS
        # into a FAIL.
        blockers_tuple = tuple(sorted(blockers, key=lambda item: item.value))
        verdict = (
            StrategyCoverageVerdict.FAIL
            if blockers_tuple
            else StrategyCoverageVerdict.PASS
        )
        required_symbols = (
            tuple(sorted(policy.required_symbols)) if policy is not None else ()
        )
        policy_id = policy.policy_id if policy is not None else None
        policy_revision = policy.revision if policy is not None else None
        policy_version = policy.policy_version if policy is not None else None
        evaluation_id = stable_strategy_coverage_evaluation_id(
            strategy_version_id=version.version_id,
            strategy_semver=version.semver,
            parameter_hash=version.parameter_hash,
            universe_hash=version.universe_hash,
            code_hash=version.code_hash,
            policy_id=policy_id,
            policy_revision=policy_revision,
            policy_version=policy_version,
            verdict=verdict,
            blockers=blockers_tuple,
            items=items,
            evaluator_version=COVERAGE_EVALUATOR_VERSION,
        )
        return StrategyCoverageEvaluation(
            evaluation_id=evaluation_id,
            strategy_version_id=version.version_id,
            strategy_semver=version.semver,
            parameter_hash=version.parameter_hash,
            universe_hash=version.universe_hash,
            code_hash=version.code_hash,
            policy_id=policy_id,
            policy_revision=policy_revision,
            policy_version=policy_version,
            verdict=verdict,
            blockers=blockers_tuple,
            items=items,
            covered_symbols=covered_symbols,
            required_symbols=required_symbols,
            distinct_review_runs=len({item.review_run_id for item in items}),
            distinct_data_hashes=len({item.data_hash for item in items}),
            evaluator_version=COVERAGE_EVALUATOR_VERSION,
            evaluated_at=evaluated_at,
        )


__all__ = ["StrategyCoverageEvaluator"]
