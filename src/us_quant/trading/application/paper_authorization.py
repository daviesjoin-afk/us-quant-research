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

The chain is fixed, and every step reads a *specific* record rather than a
"latest" one:

1. the newest ``APPLIED`` decision that entered Paper for this version;
2. that decision's own ``policy_id`` / ``policy_revision`` -- the policy that
   justified the promotion, not whatever policy is active now;
3. the exact coverage evaluation the decision named;
4. the coverage claim's identity against the version being launched;
5. the shared ``StrategyCoverageCurrentValidator`` over every member of that
   claim.

Step 5 is shared rather than re-implemented, and that is the repair.  An earlier
version of this class re-checked *one* representative authentication and its
signing key, then read ``coverage.verdict is PASS``.  A claim formed from several
members could therefore have one member's key revoked and still authorise a
launch, because the one key it looked at was still fine.

Every failure path returns ``False`` rather than raising, and that is a
*structural* property rather than a promise: ``authorises`` is a total wrapper
around ``_authorises``, so an exception from anything it calls -- a repository, a
key source, the current-validity validator, or a caller-supplied clock -- becomes
a refusal.  A launch gate that can throw is a launch gate a caller will wrap in a
broad ``except``, and the whole point of the boundary is that the answer is
always "may this launch", never a traceback.

The distinction matters because the failure modes are not all enumerable.  A
clock returning a naive instant, a port handing back a record of the wrong type,
a store raising something its own port type does not name -- each one previously
escaped as ``ValueError`` or ``AttributeError`` and reached the desktop as an
unhandled error.  Enumerating them would mean re-auditing this method every time
a collaborator changed; wrapping it does not.

Because the caller renders both a refusal and a failure identically, the wrapper
logs before refusing.  A genuine programming error must not be indistinguishable
from a policy decision, or nobody would ever investigate it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from us_quant.trading.domain.strategy_lifecycle import (
    StrategyLifecycleAction,
    StrategyLifecycleDecisionState,
)
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

_LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PaperLaunchAuthorizer:
    """Decide whether one version may be launched into Paper right now."""

    def __init__(
        self,
        *,
        decisions,
        coverages,
        lifecycle_policies,
        coverage_validity,
        clock=None,
    ) -> None:
        if decisions is None or coverages is None:
            raise TypeError("decisions and coverages are required")
        if lifecycle_policies is None:
            raise TypeError("lifecycle_policies is required")
        if coverage_validity is None:
            raise TypeError("coverage_validity is required")
        self._decisions = decisions
        self._coverages = coverages
        self._lifecycle_policies = lifecycle_policies
        self._coverage_validity = coverage_validity
        self._clock = clock or _utc_now

    def authorises(self, version_id: str) -> bool:
        """Whether ``version_id`` may be launched into Paper right now.

        Total by construction: any exception raised below this line is a refusal.
        See the module docstring for why this is a wrapper rather than a set of
        ``except`` clauses around each collaborator.
        """

        try:
            return self._authorises(version_id)
        except Exception:
            # Deliberately broad, and the only place in this module that is.  The
            # caller is the Paper launch boundary and it needs a boolean, not a
            # traceback: an unreadable store, a malformed record, a clock that is
            # not timezone-aware and a bug in a collaborator are all "we cannot
            # establish that this may launch", which fails closed.
            #
            # Logged, because the refusal is otherwise indistinguishable from a
            # legitimate one: the caller renders both as ``paper=NOT_AUTHORISED``,
            # so without this a programming error would look like a policy
            # decision and nobody would ever investigate it.  The logging itself
            # cannot break the contract -- a handler that raises would defeat the
            # whole point of this wrapper.
            try:
                _LOGGER.exception(
                    "Paper launch authorisation failed for %s; refusing",
                    version_id,
                )
            except Exception:
                pass
            return False

    def _authorises(self, version_id: str) -> bool:
        decision = self._entering_decision(version_id)
        if decision is None:
            return False
        # A decision that named no claim cannot have been justified.  There is no
        # representative-id check beside it any more: the claim is the evidence.
        if decision.coverage_evaluation_id is None:
            return False

        # The policy the decision was made under, by exact revision.  Substituting
        # whatever is active now would apply today's gate policy and today's age
        # bound to a promotion that was justified under different ones.
        if decision.policy_id is None or decision.policy_revision is None:
            return False
        try:
            policy = self._lifecycle_policies.get_policy(
                decision.policy_id, decision.policy_revision
            )
        except StrategyLifecycleRepositoryError:
            return False
        if policy is None:
            return False

        try:
            coverage = self._coverages.get_evaluation(
                decision.coverage_evaluation_id
            )
        except StrategyCoverageRepositoryError:
            return False
        if coverage is None:
            return False
        # The claim has to be about the version being launched, or a decision that
        # referenced another strategy's passing claim would authorise this one.
        if coverage.strategy_version_id != version_id:
            return False

        validity = self._coverage_validity.validate(
            coverage,
            now=self._clock(),
            required_gate_policy_version=policy.required_gate_policy_version,
            maximum_evidence_age=policy.maximum_evidence_age,
        )
        return validity.valid

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
