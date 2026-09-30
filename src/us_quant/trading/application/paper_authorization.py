"""The Paper launch gate: is this version still authorised to be in Paper?

Stage 6-C moved the *why* of a promotion into the lifecycle controller and
retired ``gate_passed``.  That leaves an obvious question at the point a Paper
session is prepared: the version is ``PAPER_SHADOW`` -- but is the decision that
put it there still valid, right now?

This service answers exactly that, and only that.  It is deliberately narrow
because instruction 25 puts the check at the launch / operating-plan boundary
and forbids it from living inside ``PortfolioRuntime``, ``RiskApplication`` or
``ExecutionApplication``: those keep Stage 5 ownership of a runtime that has
already been composed.

Three things must hold, and any one of them failing refuses the launch:

1. a lifecycle decision that entered Paper for this version reached ``APPLIED``;
2. the signing key behind its evidence is still trusted -- a key revoked after
   the launch decision must stop the launch, not just the next promotion;
3. the coverage evaluation it relied on still passes.

Every failure path returns ``False`` rather than raising.  A launch gate that
can throw is a launch gate a caller will wrap in a broad ``except``.
"""

from __future__ import annotations

from us_quant.trading.domain.evidence_auth import (
    EvidenceAuthenticationVerdict,
    EvidenceKeyTrustStatus,
)
from us_quant.trading.domain.strategy_coverage import StrategyCoverageVerdict
from us_quant.trading.domain.strategy_lifecycle import (
    StrategyLifecycleAction,
    StrategyLifecycleDecisionState,
)
from us_quant.trading.ports.evidence_authentication_repository import (
    EvidenceAuthenticationRepositoryError,
)
from us_quant.trading.ports.evidence_verification import EvidenceTrustRootUnavailable
from us_quant.trading.ports.strategy_coverage_repository import (
    StrategyCoverageRepositoryError,
)
from us_quant.trading.ports.strategy_lifecycle_repository import (
    StrategyLifecycleRepositoryError,
)


#: The decisions that authorise a version to be running in Paper.  PAUSE is
#: absent on purpose: it is what takes a version *out*.
PAPER_ENTRY_ACTIONS = frozenset(
    {
        StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
        StrategyLifecycleAction.RESUME_PAPER_SHADOW,
    }
)


class PaperLaunchAuthorizer:
    """Decide whether one version may be launched into Paper right now."""

    def __init__(
        self, *, decisions, authentications, coverages, key_source
    ) -> None:
        if decisions is None or authentications is None or coverages is None:
            raise TypeError("decisions, authentications and coverages are required")
        if key_source is None:
            raise TypeError("key_source is required")
        self._decisions = decisions
        self._authentications = authentications
        self._coverages = coverages
        self._key_source = key_source

    def authorises(self, version_id: str) -> bool:
        decision = self._entering_decision(version_id)
        if decision is None:
            return False
        # A decision that named no evidence cannot have been justified.
        if decision.authentication_id is None or decision.coverage_evaluation_id is None:
            return False

        try:
            authentication = self._authentications.get(decision.authentication_id)
        except EvidenceAuthenticationRepositoryError:
            return False
        if authentication.verdict is not EvidenceAuthenticationVerdict.PASS:
            return False

        # Revocation is re-checked here, not merely where the seal was verified:
        # a key revoked after the launch decision must stop the launch.
        try:
            key = self._key_source.verification_key(authentication.key_id)
        except EvidenceTrustRootUnavailable:
            return False
        if key is None or key.trust_status is EvidenceKeyTrustStatus.REVOKED:
            return False

        try:
            coverage = self._coverages.get_evaluation(decision.coverage_evaluation_id)
        except StrategyCoverageRepositoryError:
            return False
        return coverage.verdict is StrategyCoverageVerdict.PASS

    def _entering_decision(self, version_id: str):
        try:
            decisions = self._decisions.decisions_for_version(version_id)
        except StrategyLifecycleRepositoryError:
            return None
        # ``decisions_for_version`` is newest first, so the first APPLIED entry
        # decision is the current authority for this version.
        for decision in decisions:
            if (
                decision.state is StrategyLifecycleDecisionState.APPLIED
                and decision.action in PAPER_ENTRY_ACTIONS
            ):
                return decision
        return None


__all__ = ["PAPER_ENTRY_ACTIONS", "PaperLaunchAuthorizer"]
