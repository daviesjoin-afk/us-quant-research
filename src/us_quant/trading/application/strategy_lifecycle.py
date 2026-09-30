"""The one authority that decides *why* a strategy version may change state.

``StrategyApplication`` owns the state machine, the repository write and the
audit row; this controller owns the justification.  Splitting them is the point
of Stage 6-C: before it, the justification was a ``gate_passed`` boolean stored
on the version, which meant the field that described a decision also was the
decision.

The controller evaluates the whole chain for one exact action:

``authenticated evidence PASS`` + ``gate PASS`` + ``coverage PASS``, each bound
to the current version identity, against policy revisions and an evidence age
bound, with signing-key revocation re-checked *now* rather than whenever the
seal happened to be verified.

For a promotion a full PASS is required.  For a pause the opposite is required:
the pause is authorised *because* a named governance fact became invalid, and a
pause with nothing wrong is refused.  Both directions are fail-closed, so the
controller cannot promote on hope or suspend on a whim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from us_quant.trading.domain import strategy_lifecycle as _strategy_lifecycle
from us_quant.trading.domain.strategy import StrategyVersion
from us_quant.trading.domain.strategy_coverage import (
    StrategyCoverageEvaluation,
    StrategyCoverageVerdict,
)
from us_quant.trading.domain.strategy_lifecycle import (
    ACTION_SOURCE_STATUS,
    ACTION_TARGET_STATUS,
    LIFECYCLE_CONTROLLER_VERSION,
    SUPPORTED_LIFECYCLE_POLICY_VERSIONS,
    StrategyLifecycleAction,
    StrategyLifecycleAuthorization,
    StrategyLifecycleBlocker,
    StrategyLifecycleDecision,
    StrategyLifecycleDecisionState,
    StrategyLifecyclePolicy,
    stable_lifecycle_decision_id,
)


@dataclass(frozen=True, slots=True)
class StrategyLifecycleDecisionResult:
    """One decision, plus the authorization it grants if it was allowed.

    The authorization is present only for an authorised decision, so a caller
    cannot accidentally treat a refusal as permission by ignoring the state.
    """

    decision: StrategyLifecycleDecision
    authorization: StrategyLifecycleAuthorization | None

    @property
    def authorised(self) -> bool:
        return self.authorization is not None


class StrategyLifecycleController:
    """Decide whether one evidence-driven lifecycle mutation is justified.

    The evidence authority is the coverage claim and its current validity, not a
    pair of representative records.  A coverage evaluation names its complete
    member set, so it is the whole of what has to still hold; the controller
    therefore takes no ``authenticated`` / ``gate`` argument at all.  Accepting
    one of each would re-introduce the model where a single passing member could
    stand in for the claim.
    """

    def __init__(self, *, coverage_validity) -> None:
        if coverage_validity is None:
            raise TypeError("coverage_validity is required")
        self._coverage_validity = coverage_validity

    def decide(
        self,
        *,
        version: StrategyVersion,
        action: StrategyLifecycleAction,
        policy: StrategyLifecyclePolicy | None,
        coverage: StrategyCoverageEvaluation | None,
        decided_at: datetime,
    ) -> StrategyLifecycleDecisionResult:
        if not isinstance(version, StrategyVersion):
            raise TypeError("version must be StrategyVersion")
        if not isinstance(action, StrategyLifecycleAction):
            raise TypeError("action must be StrategyLifecycleAction")
        if not isinstance(decided_at, datetime):
            raise TypeError("decided_at must be a datetime")
        if decided_at.tzinfo is None or decided_at.utcoffset() is None:
            raise ValueError("decided_at must be timezone-aware")

        # Without a policy there is no authority to mutate anything.  This is
        # the fail-closed default, not an oversight.
        if not isinstance(policy, StrategyLifecyclePolicy):
            return self._blocked(
                version=version,
                action=action,
                policy=None,
                blockers={StrategyLifecycleBlocker.POLICY_MISSING},
                coverage=coverage,
                decided_at=decided_at,
            )

        structural: set[StrategyLifecycleBlocker] = set()
        if policy.policy_version not in SUPPORTED_LIFECYCLE_POLICY_VERSIONS:
            structural.add(StrategyLifecycleBlocker.POLICY_VERSION_UNSUPPORTED)
        if not policy.permits(action):
            structural.add(StrategyLifecycleBlocker.ACTION_NOT_PERMITTED)
        if version.status is not ACTION_SOURCE_STATUS[action]:
            structural.add(StrategyLifecycleBlocker.CURRENT_STATUS_NOT_ELIGIBLE)

        failures = self._chain_failures(
            version=version,
            policy=policy,
            coverage=coverage,
            decided_at=decided_at,
        )

        if action is StrategyLifecycleAction.PAUSE:
            # A pause is authorised *because* something became invalid, so it
            # needs a trigger.  Nothing wrong means nothing to pause for.
            if structural:
                blockers, triggers = structural, set()
            elif failures:
                blockers, triggers = set(), failures
            else:
                blockers = {StrategyLifecycleBlocker.PAUSE_NOT_JUSTIFIED}
                triggers = set()
        else:
            blockers, triggers = structural | failures, set()

        if blockers:
            return self._blocked(
                version=version,
                action=action,
                policy=policy,
                blockers=blockers,
                coverage=coverage,
                decided_at=decided_at,
            )
        return self._authorise(
            version=version,
            action=action,
            policy=policy,
            triggers=triggers,
            coverage=coverage,
            decided_at=decided_at,
        )

    # -- the evidence chain ----------------------------------------------

    def _chain_failures(
        self,
        *,
        version: StrategyVersion,
        policy: StrategyLifecyclePolicy,
        coverage: StrategyCoverageEvaluation | None,
        decided_at: datetime,
    ) -> set[StrategyLifecycleBlocker]:
        """Whether the evidence chain still justifies a mutation, right now.

        Two halves, and the split is the repair.  The coverage claim is checked
        for *structural* agreement with the version and the policy -- identity,
        semver, hashes, policy revision -- and its whole member set is then handed
        to the shared current-validity validator, which re-checks every member's
        authentication, gate, key trust and freshness.

        What is gone: the representative ``authenticated`` and ``gate`` arguments.
        They let one passing member stand in for a claim that named several, so a
        key revoked on a member nobody passed went unnoticed.  The claim is the
        authority now, and ``coverage.items`` is the evidence set.
        """

        failures: set[StrategyLifecycleBlocker] = set()

        if coverage is None:
            failures.add(StrategyLifecycleBlocker.COVERAGE_MISSING)
            return failures

        if coverage.strategy_version_id != version.version_id:
            failures.add(StrategyLifecycleBlocker.VERSION_IDENTITY_MISMATCH)
        if coverage.verdict is not StrategyCoverageVerdict.PASS:
            failures.add(StrategyLifecycleBlocker.COVERAGE_NOT_PASSED)
        if coverage.policy_version != policy.required_coverage_policy_version:
            failures.add(StrategyLifecycleBlocker.POLICY_REVISION_MISMATCH)
        if coverage.strategy_semver != version.semver:
            failures.add(StrategyLifecycleBlocker.STRATEGY_SEMVER_MISMATCH)
        if coverage.parameter_hash != version.parameter_hash:
            failures.add(StrategyLifecycleBlocker.PARAMETER_HASH_MISMATCH)
        if coverage.universe_hash != version.universe_hash:
            failures.add(StrategyLifecycleBlocker.UNIVERSE_HASH_MISMATCH)
        if coverage.code_hash != version.code_hash:
            failures.add(StrategyLifecycleBlocker.CODE_HASH_MISMATCH)

        validity = self._coverage_validity.validate(
            coverage,
            now=decided_at,
            required_gate_policy_version=policy.required_gate_policy_version,
            maximum_evidence_age=policy.maximum_evidence_age,
        )
        if not validity.valid:
            failures.add(
                StrategyLifecycleBlocker.EVIDENCE_CHAIN_NOT_CURRENT
            )
        return failures

    # -- construction ----------------------------------------------------

    def _blocked(
        self,
        *,
        version: StrategyVersion,
        action: StrategyLifecycleAction,
        policy: StrategyLifecyclePolicy | None,
        blockers: set[StrategyLifecycleBlocker],
        coverage,
        decided_at: datetime,
    ) -> StrategyLifecycleDecisionResult:
        resolved = set(blockers)
        if not resolved:
            # Only reachable if a future branch forgets to name a reason; a
            # refusal must always be explicable.
            resolved.add(StrategyLifecycleBlocker.POLICY_MISSING)
        decision = self._build(
            version=version,
            action=action,
            policy=policy,
            state=StrategyLifecycleDecisionState.BLOCKED,
            blockers=resolved,
            triggers=(),
            coverage=coverage,
            decided_at=decided_at,
            applied_at=None,
        )
        return StrategyLifecycleDecisionResult(decision=decision, authorization=None)

    def _authorise(
        self,
        *,
        version: StrategyVersion,
        action: StrategyLifecycleAction,
        policy: StrategyLifecyclePolicy,
        triggers: set[StrategyLifecycleBlocker],
        coverage,
        decided_at: datetime,
    ) -> StrategyLifecycleDecisionResult:
        decision = self._build(
            version=version,
            action=action,
            policy=policy,
            state=StrategyLifecycleDecisionState.PREPARED,
            blockers=(),
            triggers=tuple(sorted(triggers, key=lambda item: item.value)),
            coverage=coverage,
            decided_at=decided_at,
            applied_at=None,
        )
        authorization = StrategyLifecycleAuthorization(
            version.version_id,
            action,
            ACTION_TARGET_STATUS[action],
            decision.decision_id,
            decided_at,
            _controller_token=_strategy_lifecycle._CONTROLLER_TOKEN,
        )
        return StrategyLifecycleDecisionResult(
            decision=decision, authorization=authorization
        )

    @staticmethod
    def _build(
        *,
        version: StrategyVersion,
        action: StrategyLifecycleAction,
        policy: StrategyLifecyclePolicy | None,
        state: StrategyLifecycleDecisionState,
        blockers,
        triggers,
        coverage,
        decided_at: datetime,
        applied_at: datetime | None,
    ) -> StrategyLifecycleDecision:
        policy_id = policy.policy_id if policy is not None else None
        policy_revision = policy.revision if policy is not None else None
        policy_version = policy.policy_version if policy is not None else None
        # The representative ids are written as NULL from here on.  The columns
        # stay for the databases that already hold them, but nothing new fills
        # them and nothing reads them: the evidence identity is
        # ``coverage_evaluation_id``, which names the complete member set.
        authentication_id = None
        gate_evaluation_id = None
        coverage_evaluation_id = coverage.evaluation_id if coverage is not None else None
        coverage_policy_id = coverage.policy_id if coverage is not None else None
        coverage_policy_revision = (
            coverage.policy_revision if coverage is not None else None
        )
        blockers_tuple = tuple(sorted(set(blockers), key=lambda item: item.value))
        triggers_tuple = tuple(sorted(set(triggers), key=lambda item: item.value))
        return StrategyLifecycleDecision(
            decision_id=stable_lifecycle_decision_id(
                strategy_version_id=version.version_id,
                action=action,
                source_status=ACTION_SOURCE_STATUS[action],
                target_status=ACTION_TARGET_STATUS[action],
                policy_id=policy_id,
                policy_revision=policy_revision,
                coverage_evaluation_id=coverage_evaluation_id,
                blockers=blockers_tuple,
                triggers=triggers_tuple,
                controller_version=LIFECYCLE_CONTROLLER_VERSION,
            ),
            strategy_version_id=version.version_id,
            strategy_semver=version.semver,
            parameter_hash=version.parameter_hash,
            universe_hash=version.universe_hash,
            code_hash=version.code_hash,
            action=action,
            source_status=ACTION_SOURCE_STATUS[action],
            target_status=ACTION_TARGET_STATUS[action],
            state=state,
            blockers=blockers_tuple,
            triggers=triggers_tuple,
            policy_id=policy_id,
            policy_revision=policy_revision,
            policy_version=policy_version,
            authentication_id=authentication_id,
            gate_evaluation_id=gate_evaluation_id,
            coverage_evaluation_id=coverage_evaluation_id,
            coverage_policy_id=coverage_policy_id,
            coverage_policy_revision=coverage_policy_revision,
            controller_version=LIFECYCLE_CONTROLLER_VERSION,
            authorized_at=decided_at,
            applied_at=applied_at,
        )


class StrategyLifecycleService:
    """Drive one lifecycle mutation durably, and reconcile it after a crash.

    The protocol is deliberately three steps rather than one, because a
    transition and its audit record must not be able to disagree:

    1. evaluate and persist the decision (``PREPARED``, or ``BLOCKED``);
    2. perform the state-machine transition, which now *requires* the
       authorization this service just received;
    3. mark the decision ``APPLIED``.

    An interruption between 2 and 3 leaves a ``PREPARED`` row whose target
    already matches the live status -- :meth:`reconcile` finishes it.  An
    interruption before 2 leaves the status untouched, so the same call can be
    safely retried.  Nothing replays a transition that already happened, because
    the decision id is derived from the semantic inputs and the state machine
    refuses a transition that is no longer legal.
    """

    def __init__(self, *, controller, decisions, strategies) -> None:
        if controller is None or decisions is None or strategies is None:
            raise TypeError("controller, decisions and strategies are required")
        self._controller = controller
        self._decisions = decisions
        self._strategies = strategies

    def apply(
        self,
        *,
        version: StrategyVersion,
        action: StrategyLifecycleAction,
        policy: StrategyLifecyclePolicy | None,
        coverage: StrategyCoverageEvaluation | None,
        applied_at: datetime,
    ) -> StrategyLifecycleDecisionResult:
        outcome = self._controller.decide(
            version=version,
            action=action,
            policy=policy,
            coverage=coverage,
            decided_at=applied_at,
        )
        # A refusal is recorded too: "we looked and declined" is exactly the
        # audit fact a later reviewer needs.
        self._decisions.record_decision(outcome.decision)
        if outcome.authorization is None:
            return outcome

        self._strategies.transition(
            version.version_id,
            outcome.decision.target_status,
            reason=(
                f"lifecycle {action.value} "
                f"[{outcome.decision.decision_id}]"
            ),
            authorization=outcome.authorization,
        )
        applied = self._decisions.mark_applied(
            outcome.decision.decision_id, applied_at=applied_at
        )
        return StrategyLifecycleDecisionResult(
            decision=applied, authorization=outcome.authorization
        )

    def reconcile(
        self, *, version_id: str, now: datetime
    ) -> tuple[StrategyLifecycleDecision, ...]:
        """Resolve every ``PREPARED`` decision against the live status.

        - target already reached -> the transition did happen: mark ``APPLIED``;
        - source unchanged -> nothing happened yet: leave it ``PREPARED`` so a
          retry can still apply it;
        - anything else -> the world moved on: mark ``SUPERSEDED`` so it can
          never be replayed.
        """

        if not isinstance(now, datetime):
            raise TypeError("now must be a datetime")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        resolved: list[StrategyLifecycleDecision] = []
        for decision in self._decisions.prepared_decisions(version_id):
            current = self._strategies.get_version(version_id).status
            if current is decision.target_status:
                resolved.append(
                    self._decisions.mark_applied(
                        decision.decision_id, applied_at=now
                    )
                )
            elif current is decision.source_status:
                resolved.append(decision)
            else:
                resolved.append(
                    self._decisions.mark_superseded(decision.decision_id)
                )
        return tuple(resolved)


__all__ = [
    "StrategyLifecycleController",
    "StrategyLifecycleDecisionResult",
    "StrategyLifecycleService",
]
