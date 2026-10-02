"""Governed lifecycle authority: *why* a strategy version may change state.

Before Stage 6-C there were two authorities for promotion.  The structural one
was ``ALLOWED_TRANSITIONS``, enforced by ``StrategyApplication``; the *actual*
one was the legacy ``gate_passed`` boolean, which a version carried around and
which the transition method consulted directly.  That field is a
self-attested flag on a mutable-ish record, so it could not answer the only
question that matters -- who decided, on what evidence.

This module introduces the missing authority.  ``StrategyLifecycleController``
evaluates the full evidence chain (authenticated evidence, gate evaluation,
coverage evaluation, policy revisions, key revocation, evidence age) and mints
a :class:`StrategyLifecycleAuthorization`.  ``StrategyApplication`` still owns
the state machine, the repository write and the audit record -- it just no
longer decides *whether* a promotion is warranted.

Two properties are load-bearing:

**The legacy field is not authority.**  ``gate_passed`` / ``gate_reason``
survive as read-only compatibility columns; nothing here consults them, and
:class:`StrategyLifecycleBlocker` has no way to express "the legacy flag said
yes" as a reason to proceed.

**A decision is durable before it is acted on.**  A decision is persisted
``PREPARED``, then the transition runs, then it is marked ``APPLIED``.  A crash
in between leaves a row that recovery can reconcile against the *actual*
current status, so the transition is never performed twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
import json

from us_quant.trading.domain.strategy import StrategyStatus

#: Policy schema this controller understands.  An unknown value fails closed.
SUPPORTED_LIFECYCLE_POLICY_VERSIONS: tuple[str, ...] = ("strategy-lifecycle-v1",)

LIFECYCLE_CONTROLLER_VERSION = "strategy-lifecycle-v2"


class StrategyLifecycleAction(StrEnum):
    """The evidence-driven mutations the controller may authorise.

    ``STOPPED`` is deliberately absent.  It is terminal, and reaching it stays
    an explicit operator/governance act rather than something an autonomous
    evaluator can decide from evidence.
    """

    PROMOTE_TO_PAPER_SHADOW = "PROMOTE_TO_PAPER_SHADOW"
    PAUSE = "PAUSE"
    RESUME_PAPER_SHADOW = "RESUME_PAPER_SHADOW"


#: The status each action moves a version *to*.
ACTION_TARGET_STATUS: dict[StrategyLifecycleAction, StrategyStatus] = {
    StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW: StrategyStatus.PAPER_SHADOW,
    StrategyLifecycleAction.PAUSE: StrategyStatus.PAUSED,
    StrategyLifecycleAction.RESUME_PAPER_SHADOW: StrategyStatus.PAPER_SHADOW,
}

#: The status each action may be taken *from*.
ACTION_SOURCE_STATUS: dict[StrategyLifecycleAction, StrategyStatus] = {
    StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW: StrategyStatus.RESEARCH,
    StrategyLifecycleAction.PAUSE: StrategyStatus.PAPER_SHADOW,
    StrategyLifecycleAction.RESUME_PAPER_SHADOW: StrategyStatus.PAUSED,
}


class StrategyLifecycleBlocker(StrEnum):
    """Every reason an evidence-driven lifecycle mutation can be refused."""

    POLICY_MISSING = "POLICY_MISSING"
    POLICY_VERSION_UNSUPPORTED = "POLICY_VERSION_UNSUPPORTED"
    ACTION_NOT_PERMITTED = "ACTION_NOT_PERMITTED"
    VERSION_IDENTITY_MISMATCH = "VERSION_IDENTITY_MISMATCH"
    STRATEGY_SEMVER_MISMATCH = "STRATEGY_SEMVER_MISMATCH"
    PARAMETER_HASH_MISMATCH = "PARAMETER_HASH_MISMATCH"
    CODE_HASH_MISMATCH = "CODE_HASH_MISMATCH"
    UNIVERSE_HASH_MISMATCH = "UNIVERSE_HASH_MISMATCH"
    CURRENT_STATUS_NOT_ELIGIBLE = "CURRENT_STATUS_NOT_ELIGIBLE"
    AUTHENTICATION_MISSING = "AUTHENTICATION_MISSING"
    AUTHENTICATION_NOT_PASSED = "AUTHENTICATION_NOT_PASSED"
    GATE_MISSING = "GATE_MISSING"
    GATE_NOT_PASSED = "GATE_NOT_PASSED"
    COVERAGE_MISSING = "COVERAGE_MISSING"
    COVERAGE_NOT_PASSED = "COVERAGE_NOT_PASSED"
    POLICY_REVISION_MISMATCH = "POLICY_REVISION_MISMATCH"
    REVOKED_SIGNING_KEY = "REVOKED_SIGNING_KEY"
    UNKNOWN_SIGNING_KEY = "UNKNOWN_SIGNING_KEY"
    TRUST_ROOT_UNAVAILABLE = "TRUST_ROOT_UNAVAILABLE"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    #: The whole coverage claim is no longer current: some member's record is
    #: gone, no longer passes, no longer matches the item that named it, is signed
    #: by a key that is no longer trusted, or has aged out.  Deliberately one
    #: blocker rather than a copy of the validator's granular list: the lifecycle
    #: decision says *that* the chain is stale, and the validity result it was
    #: built from carries the reasons.
    EVIDENCE_CHAIN_NOT_CURRENT = "EVIDENCE_CHAIN_NOT_CURRENT"
    #: A pause must name the governance fact that justifies it.  Without this
    #: blocker an evaluator could suspend a running strategy for no recorded
    #: reason, which is exactly the kind of unilateral authority 6-C removes.
    PAUSE_NOT_JUSTIFIED = "PAUSE_NOT_JUSTIFIED"
    PAPER_PERFORMANCE_FAILED = "PAPER_PERFORMANCE_FAILED"
    PAPER_PERFORMANCE_NOT_CURRENT = "PAPER_PERFORMANCE_NOT_CURRENT"


class StrategyLifecycleDecisionState(StrEnum):
    """Durable progress of one decision.

    ``PREPARED`` is written *before* the transition so an interruption leaves
    evidence of intent rather than a silently half-applied change.
    ``SUPERSEDED`` means the world moved on before the transition ran, so the
    decision must not be replayed.
    """

    PREPARED = "PREPARED"
    APPLIED = "APPLIED"
    SUPERSEDED = "SUPERSEDED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class StrategyLifecyclePolicy:
    """Which actions are allowed, and against which evidence policy versions.

    All fields are required and there is no default instance: a lifecycle policy
    that nobody wrote must not exist, because "no policy" has to mean "no
    autonomous mutation" rather than "whatever the defaults happen to allow".
    """

    policy_id: str
    revision: int
    policy_version: str
    permitted_actions: tuple[StrategyLifecycleAction, ...]
    required_gate_policy_version: str
    required_coverage_policy_version: str
    maximum_evidence_age: timedelta | None
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.policy_id, "policy_id")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("revision must be a positive integer")
        _require_text(self.policy_version, "policy_version")
        if not isinstance(self.permitted_actions, tuple) or not self.permitted_actions:
            raise ValueError("permitted_actions must be a nonempty tuple")
        if any(
            not isinstance(action, StrategyLifecycleAction)
            for action in self.permitted_actions
        ):
            raise TypeError("permitted_actions must contain StrategyLifecycleAction values")
        if len(set(self.permitted_actions)) != len(self.permitted_actions):
            raise ValueError("permitted_actions must not repeat")
        _require_text(self.required_gate_policy_version, "required_gate_policy_version")
        _require_text(
            self.required_coverage_policy_version,
            "required_coverage_policy_version",
        )
        if self.maximum_evidence_age is not None:
            if not isinstance(self.maximum_evidence_age, timedelta):
                raise TypeError("maximum_evidence_age must be timedelta or None")
            if self.maximum_evidence_age <= timedelta(0):
                raise ValueError("maximum_evidence_age must be positive")
        _require_aware(self.created_at, "created_at")

    @property
    def identity(self) -> tuple[str, int]:
        return (self.policy_id, self.revision)

    def permits(self, action: StrategyLifecycleAction) -> bool:
        return action in self.permitted_actions


@dataclass(frozen=True, slots=True)
class StrategyLifecycleDecision:
    """The durable record of one lifecycle decision, prepared then applied."""

    decision_id: str
    strategy_version_id: str
    strategy_semver: str
    parameter_hash: str
    universe_hash: str
    code_hash: str
    action: StrategyLifecycleAction
    source_status: StrategyStatus
    target_status: StrategyStatus
    state: StrategyLifecycleDecisionState
    blockers: tuple[StrategyLifecycleBlocker, ...]
    triggers: tuple[StrategyLifecycleBlocker, ...]
    policy_id: str | None
    policy_revision: int | None
    policy_version: str | None
    authentication_id: str | None
    gate_evaluation_id: str | None
    coverage_evaluation_id: str | None
    coverage_policy_id: str | None
    coverage_policy_revision: int | None
    controller_version: str
    authorized_at: datetime
    applied_at: datetime | None
    paper_performance_evaluation_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "decision_id", "strategy_version_id", "strategy_semver",
            "parameter_hash", "universe_hash", "code_hash", "controller_version",
        ):
            _require_text(getattr(self, name), name)
        if not isinstance(self.action, StrategyLifecycleAction):
            raise TypeError("action must be StrategyLifecycleAction")
        if not isinstance(self.source_status, StrategyStatus):
            raise TypeError("source_status must be a StrategyStatus")
        if not isinstance(self.target_status, StrategyStatus):
            raise TypeError("target_status must be a StrategyStatus")
        if self.target_status is not ACTION_TARGET_STATUS[self.action]:
            raise ValueError("target_status must be the action's target")
        if not isinstance(self.state, StrategyLifecycleDecisionState):
            raise TypeError("state must be StrategyLifecycleDecisionState")
        if not isinstance(self.blockers, tuple):
            raise TypeError("blockers must be a tuple")
        if any(
            not isinstance(item, StrategyLifecycleBlocker) for item in self.blockers
        ):
            raise TypeError("blockers must contain StrategyLifecycleBlocker values")
        object.__setattr__(
            self, "blockers",
            tuple(sorted(set(self.blockers), key=lambda item: item.value)),
        )
        if not isinstance(self.triggers, tuple):
            raise TypeError("triggers must be a tuple")
        if any(
            not isinstance(item, StrategyLifecycleBlocker) for item in self.triggers
        ):
            raise TypeError("triggers must contain StrategyLifecycleBlocker values")
        object.__setattr__(
            self, "triggers",
            tuple(sorted(set(self.triggers), key=lambda item: item.value)),
        )
        for name in (
            "policy_id", "policy_version", "authentication_id",
            "gate_evaluation_id", "coverage_evaluation_id", "coverage_policy_id",
            "paper_performance_evaluation_id",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_text(value, name)
        for name in ("policy_revision", "coverage_policy_revision"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{name} must be a positive integer or None")
        # A decision that is allowed to proceed must name every reason it was
        # allowed to proceed; a refused one must name why.  SUPERSEDED is the
        # exception: the decision *was* authorised, it simply became moot
        # because the world moved on, so there is no governance failure to name.
        if self.state is StrategyLifecycleDecisionState.SUPERSEDED:
            if self.blockers:
                raise ValueError("a superseded decision must not carry blockers")
        elif self.authorised != (not self.blockers):
            raise ValueError("a decision is authorised exactly when it has no blockers")
        # A PAUSE is authorised *because* something became invalid, so it must
        # record that trigger.  A promotion is authorised because nothing is
        # wrong, so it must not pretend to have one.
        if self.authorised and self.action is StrategyLifecycleAction.PAUSE:
            if not self.triggers:
                raise ValueError("an authorised pause must record its trigger")
        elif self.triggers:
            raise ValueError("only an authorised pause may carry triggers")
        if self.state is StrategyLifecycleDecisionState.APPLIED:
            if self.blockers:
                raise ValueError("an applied decision cannot carry blockers")
            if self.applied_at is None:
                raise ValueError("an applied decision must record when it applied")
            _require_aware(self.applied_at, "applied_at")
        elif self.applied_at is not None:
            raise ValueError("only an applied decision may record applied_at")
        _require_aware(self.authorized_at, "authorized_at")
        if (StrategyLifecycleBlocker.PAPER_PERFORMANCE_FAILED in self.triggers
                and self.paper_performance_evaluation_id is None):
            raise ValueError("performance pause must name its durable evaluation")

    @property
    def authorised(self) -> bool:
        return self.state in {
            StrategyLifecycleDecisionState.PREPARED,
            StrategyLifecycleDecisionState.APPLIED,
        }

    @property
    def policy_identity(self) -> tuple[str, int] | None:
        if self.policy_id is None or self.policy_revision is None:
            return None
        return (self.policy_id, self.policy_revision)

    @property
    def coverage_policy_identity(self) -> tuple[str, int] | None:
        if self.coverage_policy_id is None or self.coverage_policy_revision is None:
            return None
        return (self.coverage_policy_id, self.coverage_policy_revision)


@dataclass(frozen=True, slots=True, init=False)
class StrategyLifecycleAuthorization:
    """Proof that the controller approved one exact transition.

    Construction is gated on a private token, so this is the only bridge from
    "the evidence justified a promotion" to "the state machine may move".  It
    names the exact version and target, which is what stops an authorization
    from being reused for a different transition.
    """

    version_id: str
    action: StrategyLifecycleAction
    target_status: StrategyStatus
    decision_id: str
    authorised_at: datetime

    def __init__(
        self,
        version_id: str,
        action: StrategyLifecycleAction,
        target_status: StrategyStatus,
        decision_id: str,
        authorised_at: datetime,
        *,
        _controller_token: object,
    ) -> None:
        if _controller_token is not _CONTROLLER_TOKEN:
            raise TypeError(
                "StrategyLifecycleAuthorization must come from the lifecycle controller"
            )
        _require_text(version_id, "version_id")
        _require_text(decision_id, "decision_id")
        if not isinstance(action, StrategyLifecycleAction):
            raise TypeError("action must be StrategyLifecycleAction")
        if target_status is not ACTION_TARGET_STATUS[action]:
            raise ValueError("target_status must be the action's target")
        _require_aware(authorised_at, "authorised_at")
        object.__setattr__(self, "version_id", version_id)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "target_status", target_status)
        object.__setattr__(self, "decision_id", decision_id)
        object.__setattr__(self, "authorised_at", authorised_at)


class StrategyLifecyclePolicyMalformed(ValueError):
    """A stored lifecycle policy cannot be read as a policy."""


_CONTROLLER_TOKEN = object()


def policy_to_payload(policy: StrategyLifecyclePolicy) -> dict[str, object]:
    """Serialise a lifecycle policy immutably; it is appended, never edited."""

    if not isinstance(policy, StrategyLifecyclePolicy):
        raise TypeError("policy must be StrategyLifecyclePolicy")
    return {
        "policy_id": policy.policy_id,
        "revision": policy.revision,
        "policy_version": policy.policy_version,
        "permitted_actions": [action.value for action in policy.permitted_actions],
        "required_gate_policy_version": policy.required_gate_policy_version,
        "required_coverage_policy_version": policy.required_coverage_policy_version,
        "maximum_evidence_age_seconds": (
            None
            if policy.maximum_evidence_age is None
            else int(policy.maximum_evidence_age.total_seconds())
        ),
        "created_at": policy.created_at.isoformat(),
    }


def policy_from_payload(payload: object) -> StrategyLifecyclePolicy:
    """Parse a stored lifecycle policy, raising on anything not well formed."""

    if not isinstance(payload, dict):
        raise StrategyLifecyclePolicyMalformed("policy payload must be an object")
    values = dict(payload)
    actions = values.get("permitted_actions")
    if not isinstance(actions, list):
        raise StrategyLifecyclePolicyMalformed("policy permitted_actions must be a list")
    age_seconds = values.get("maximum_evidence_age_seconds")
    if age_seconds is not None and (type(age_seconds) is not int or age_seconds < 1):
        raise StrategyLifecyclePolicyMalformed(
            "policy maximum_evidence_age_seconds is invalid"
        )
    created_at = values.get("created_at")
    if not isinstance(created_at, str) or not created_at.strip():
        raise StrategyLifecyclePolicyMalformed("policy created_at is missing")
    try:
        parsed_created_at = datetime.fromisoformat(created_at)
    except ValueError as error:
        raise StrategyLifecyclePolicyMalformed(
            "policy created_at is not a timestamp"
        ) from error
    try:
        return StrategyLifecyclePolicy(
            policy_id=values["policy_id"],
            revision=values["revision"],
            policy_version=values["policy_version"],
            permitted_actions=tuple(
                StrategyLifecycleAction(action) for action in actions
            ),
            required_gate_policy_version=values["required_gate_policy_version"],
            required_coverage_policy_version=values[
                "required_coverage_policy_version"
            ],
            maximum_evidence_age=(
                None if age_seconds is None else timedelta(seconds=age_seconds)
            ),
            created_at=parsed_created_at,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise StrategyLifecyclePolicyMalformed(f"policy is invalid: {error}") from error


def stable_lifecycle_decision_id(
    *,
    strategy_version_id: str,
    action: StrategyLifecycleAction,
    source_status: StrategyStatus,
    target_status: StrategyStatus,
    policy_id: str | None,
    policy_revision: int | None,
    coverage_evaluation_id: str | None,
    blockers: tuple[StrategyLifecycleBlocker, ...],
    triggers: tuple[StrategyLifecycleBlocker, ...],
    controller_version: str,
    paper_performance_evaluation_id: str | None = None,
) -> str:
    """Deterministic identity of one semantic decision.

    ``authorized_at`` is excluded, so re-evaluating identical inputs is
    idempotent and cannot create a second, competing decision row.

    The evidence identity is ``coverage_evaluation_id`` and nothing else.  A
    coverage evaluation is immutable and names its complete member set, so it
    already identifies the evidence; the representative ``authentication_id`` and
    ``gate_evaluation_id`` that used to contribute here identified *one member of
    that set*, which is exactly the model this repair removes.  Leaving them in
    the hash would let two decisions over the same evidence differ by which member
    happened to be passed as the representative.
    """

    material = {
        "strategy_version_id": strategy_version_id,
        "action": action.value,
        "source_status": source_status.value,
        "target_status": target_status.value,
        "policy_id": policy_id,
        "policy_revision": policy_revision,
        "coverage_evaluation_id": coverage_evaluation_id,
        "blockers": sorted({item.value for item in blockers}),
        "triggers": sorted({item.value for item in triggers}),
        "controller_version": controller_version,
    }
    if paper_performance_evaluation_id is not None:
        material["paper_performance_evaluation_id"] = paper_performance_evaluation_id
    digest = sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return f"sld-{digest}"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank text")


def _require_aware(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "ACTION_SOURCE_STATUS",
    "ACTION_TARGET_STATUS",
    "LIFECYCLE_CONTROLLER_VERSION",
    "SUPPORTED_LIFECYCLE_POLICY_VERSIONS",
    "StrategyLifecycleAction",
    "StrategyLifecycleAuthorization",
    "StrategyLifecycleBlocker",
    "StrategyLifecycleDecision",
    "StrategyLifecycleDecisionState",
    "StrategyLifecyclePolicy",
    "StrategyLifecyclePolicyMalformed",
    "policy_from_payload",
    "policy_to_payload",
    "stable_lifecycle_decision_id",
]
