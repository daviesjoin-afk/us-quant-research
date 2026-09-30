"""Stage 6-C: the Paper launch gate.

``PaperLaunchAuthorizer`` is the only thing standing between a stored lifecycle
decision and a running Paper session, so each way it can be satisfied is
exercised separately, along with each way it refuses.

The gate no longer inspects one representative authentication.  It resolves the
decision's *own* policy revision, loads the exact coverage evaluation that
decision named, and hands the whole claim to the shared current-validity
validator -- so the member-level refusals below run through that real validator
over fake stores.  Testing them through a stub validator would prove only that
the stub says what the stub says.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from us_quant.trading.application.paper_authorization import (
    PAPER_ENTRY_ACTIONS,
    PaperLaunchAuthorizer,
)
from us_quant.trading.application.strategy_coverage_validity import (
    StrategyCoverageCurrentValidator,
)
from us_quant.trading.domain.evidence_auth import (
    ED25519_ALGORITHM,
    EvidenceAuthenticationVerdict,
    EvidenceKeyTrustStatus,
    EvidenceVerificationKey,
)
from us_quant.trading.domain.strategy import StrategyStatus
from us_quant.trading.domain.strategy_coverage import (
    StrategyCoverageBlocker,
    StrategyCoverageEvaluation,
    StrategyCoverageItem,
    StrategyCoverageVerdict,
)
from us_quant.trading.domain.strategy_gate import (
    StrategyGateEvaluation,
    StrategyGateVerdict,
)
from us_quant.trading.domain.strategy_lifecycle import (
    StrategyLifecycleAction,
    StrategyLifecycleBlocker as Blocker,
    StrategyLifecycleDecision,
    StrategyLifecycleDecisionState,
    StrategyLifecyclePolicy,
)
from us_quant.trading.ports.evidence_authentication_repository import (
    EvidenceAuthenticationRepositoryNotFound,
)
from us_quant.trading.ports.evidence_verification import EvidenceTrustRootUnavailable
from us_quant.trading.ports.strategy_coverage_repository import (
    StrategyCoverageRepositoryNotFound,
)
from us_quant.trading.ports.strategy_lifecycle_repository import (
    StrategyLifecycleRepositoryNotFound,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
VERSION_ID = "version-1"
KEY_ID = "research-key-1"
AUTHENTICATION_ID = "sea-1"
GATE_ID = "sge-1"
COVERAGE_ID = "sce-1"
REVIEW_RUN = "review-1"
DATA_HASH = "data-1"
PARAMETER_HASH = "parameter-1"
SYMBOL = "AAPL"
GATE_POLICY = "independent-review-v1"


# -- the stored records ---------------------------------------------------


def _decision(**changes):
    values = dict(
        decision_id="sld-1", strategy_version_id=VERSION_ID, strategy_semver="1.0.0",
        parameter_hash=PARAMETER_HASH, universe_hash="u", code_hash="c",
        action=StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
        source_status=StrategyStatus.RESEARCH, target_status=StrategyStatus.PAPER_SHADOW,
        state=StrategyLifecycleDecisionState.APPLIED, blockers=(), triggers=(),
        policy_id="lifecycle-policy-1", policy_revision=1,
        policy_version="strategy-lifecycle-v1",
        # The representative columns are NULL on a decision this system writes;
        # the evidence identity is the claim.
        authentication_id=None, gate_evaluation_id=None,
        coverage_evaluation_id=COVERAGE_ID, coverage_policy_id="coverage-policy-1",
        coverage_policy_revision=1, controller_version="strategy-lifecycle-v1",
        authorized_at=NOW, applied_at=NOW,
    )
    values.update(changes)
    return StrategyLifecycleDecision(**values)


def _policy(**changes):
    values = dict(
        policy_id="lifecycle-policy-1", revision=1,
        policy_version="strategy-lifecycle-v1",
        permitted_actions=(
            StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
            StrategyLifecycleAction.RESUME_PAPER_SHADOW,
            StrategyLifecycleAction.PAUSE,
        ),
        required_gate_policy_version=GATE_POLICY,
        required_coverage_policy_version="evidence-coverage-v1",
        maximum_evidence_age=timedelta(days=30), created_at=NOW,
    )
    values.update(changes)
    return StrategyLifecyclePolicy(**values)


def _item(**changes):
    """A real coverage item, because the validator compares its whole identity."""

    values = dict(
        symbol=SYMBOL, review_run_id=REVIEW_RUN, data_hash=DATA_HASH,
        key_id=KEY_ID, authentication_id=AUTHENTICATION_ID,
        gate_evaluation_id=GATE_ID,
        signed_at=NOW - timedelta(hours=2), generated_at=NOW - timedelta(hours=1),
    )
    values.update(changes)
    return StrategyCoverageItem(**values)


def _coverage(**changes):
    """A real coverage evaluation, for the same reason as the item."""

    values = dict(
        evaluation_id=COVERAGE_ID, strategy_version_id=VERSION_ID,
        strategy_semver="1.0.0", parameter_hash=PARAMETER_HASH,
        universe_hash="u", code_hash="c",
        policy_id="coverage-policy-1", policy_revision=1,
        policy_version="evidence-coverage-v1",
        verdict=StrategyCoverageVerdict.PASS, blockers=(),
        items=(_item(),), covered_symbols=(SYMBOL,), required_symbols=(SYMBOL,),
        distinct_review_runs=1, distinct_data_hashes=1,
        evaluator_version="coverage-1", evaluated_at=NOW - timedelta(minutes=30),
    )
    values.update(changes)
    return StrategyCoverageEvaluation(**values)


def _authentication(**changes):
    values = dict(
        authentication_id=AUTHENTICATION_ID, strategy_version_id=VERSION_ID,
        review_run_id=REVIEW_RUN, key_id=KEY_ID,
        verdict=EvidenceAuthenticationVerdict.PASS,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def _gate(**changes):
    """A real gate evaluation: the validator compares its verdict by identity."""

    values = dict(
        evaluation_id=GATE_ID, strategy_version_id=VERSION_ID,
        review_run_id=REVIEW_RUN, parameter_hash=PARAMETER_HASH,
        data_hash=DATA_HASH, symbol=SYMBOL, provider="provider-1",
        policy_version=GATE_POLICY, verdict=StrategyGateVerdict.PASS,
        blockers=(), review_decision="approved", review_blocking_failures=0,
        review_passed_gates=1, review_gate_count=1,
        evaluator_version="gate-1", evaluated_at=NOW - timedelta(hours=1),
    )
    values.update(changes)
    return StrategyGateEvaluation(**values)


def _key(status=EvidenceKeyTrustStatus.ACTIVE, key_id=KEY_ID):
    return EvidenceVerificationKey(
        key_id=key_id, algorithm=ED25519_ALGORITHM,
        public_key=b"c" * 32, trust_status=status,
    )


# -- the stores -----------------------------------------------------------


class _Decisions:
    def __init__(self, decisions=(), *, error=None):
        self._decisions = tuple(decisions)
        self._error = error

    def decisions_for_version(self, version_id):
        if self._error is not None:
            raise self._error
        return self._decisions


class _Coverages:
    def __init__(self, evaluation=None, *, error=None):
        self._evaluation = evaluation
        self._error = error

    def get_evaluation(self, evaluation_id):
        if self._error is not None:
            raise self._error
        if self._evaluation is None:
            raise StrategyCoverageRepositoryNotFound(evaluation_id)
        return self._evaluation


class _Policies:
    def __init__(self, policy=None, *, error=None):
        self._policy = policy
        self._error = error

    def get_policy(self, policy_id, revision):
        if self._error is not None:
            raise self._error
        return self._policy


class _Records:
    """A store keyed by exact id, raising the port's own not-found."""

    def __init__(self, records, *, error=None, missing=None):
        self._records = {row.authentication_id: row for row in records}
        self._error = error
        self._missing = missing

    def get(self, record_id):
        if self._error is not None:
            raise self._error
        if self._missing:
            raise self._missing(record_id)
        return self._records.get(record_id)


class _Gates:
    def __init__(self, records, *, error=None):
        self._records = {row.evaluation_id: row for row in records}
        self._error = error

    def get(self, evaluation_id):
        if self._error is not None:
            raise self._error
        return self._records.get(evaluation_id)


class _Keys:
    """A trust root holding exactly one key, or none."""

    def __init__(self, key=None, *, error=None):
        self._key = key
        self._error = error

    def verification_key(self, key_id):
        if self._error is not None:
            raise self._error
        if self._key is not None and self._key.key_id == key_id:
            return self._key
        return None


def _authorizer(
    *,
    decisions=(),
    coverages=None,
    policies=None,
    authentications=None,
    gates=None,
    keys=None,
):
    """The real authorizer over the real shared validator and fake stores."""

    return PaperLaunchAuthorizer(
        decisions=(
            decisions if isinstance(decisions, _Decisions) else _Decisions(decisions)
        ),
        coverages=coverages if coverages is not None else _Coverages(_coverage()),
        lifecycle_policies=(
            policies if policies is not None else _Policies(_policy())
        ),
        coverage_validity=StrategyCoverageCurrentValidator(
            authentications=(
                authentications
                if authentications is not None
                else _Records((_authentication(),))
            ),
            gates=gates if gates is not None else _Gates((_gate(),)),
            key_source=keys if keys is not None else _Keys(_key()),
        ),
        clock=lambda: NOW,
    )


# -- the happy paths ------------------------------------------------------


def test_an_applied_promotion_with_live_evidence_authorises():
    assert _authorizer(decisions=(_decision(),)).authorises(VERSION_ID) is True


def test_an_applied_resume_authorises():
    resume = _decision(
        action=StrategyLifecycleAction.RESUME_PAPER_SHADOW,
        source_status=StrategyStatus.PAUSED,
    )

    assert _authorizer(decisions=(resume,)).authorises(VERSION_ID) is True


def test_a_verify_only_key_still_authorises():
    """VERIFY_ONLY means "may verify, may not sign" -- not "distrusted"."""

    authorizer = _authorizer(
        decisions=(_decision(),), keys=_Keys(_key(EvidenceKeyTrustStatus.VERIFY_ONLY))
    )

    assert authorizer.authorises(VERSION_ID) is True


def test_a_pause_on_top_does_not_hide_the_entry_decision():
    """The gate consults the newest *entry* decision, skipping later non-entries.

    A paused version cannot reach this gate anyway -- the plan boundary checks
    status and mode first -- so this documents which decision is read, not a
    hole: the pause is skipped precisely because it never authorised a launch.
    """

    pause = _decision(
        decision_id="sld-pause", action=StrategyLifecycleAction.PAUSE,
        source_status=StrategyStatus.PAPER_SHADOW, target_status=StrategyStatus.PAUSED,
        triggers=(Blocker.EVIDENCE_CHAIN_NOT_CURRENT,),
    )
    authorizer = _authorizer(decisions=(pause, _decision()))  # newest first

    assert authorizer.authorises(VERSION_ID) is True


# -- refusals -------------------------------------------------------------


def test_no_decision_history_refuses():
    assert _authorizer(decisions=()).authorises(VERSION_ID) is False


def test_a_prepared_decision_is_not_yet_authority():
    prepared = _decision(state=StrategyLifecycleDecisionState.PREPARED, applied_at=None)

    assert _authorizer(decisions=(prepared,)).authorises(VERSION_ID) is False


def test_a_superseded_decision_is_not_authority():
    superseded = _decision(
        state=StrategyLifecycleDecisionState.SUPERSEDED, applied_at=None
    )

    assert _authorizer(decisions=(superseded,)).authorises(VERSION_ID) is False


def test_a_blocked_decision_is_not_authority():
    blocked = _decision(
        state=StrategyLifecycleDecisionState.BLOCKED, blockers=(Blocker.POLICY_MISSING,),
        applied_at=None,
    )

    assert _authorizer(decisions=(blocked,)).authorises(VERSION_ID) is False


def test_a_pause_alone_never_authorises_a_launch():
    """PAUSE is what takes a version out of Paper; it cannot put one in."""

    pause = _decision(
        action=StrategyLifecycleAction.PAUSE,
        source_status=StrategyStatus.PAPER_SHADOW, target_status=StrategyStatus.PAUSED,
        triggers=(Blocker.EVIDENCE_CHAIN_NOT_CURRENT,),
    )

    assert _authorizer(decisions=(pause,)).authorises(VERSION_ID) is False


def test_pause_is_not_an_entry_action():
    assert StrategyLifecycleAction.PAUSE not in PAPER_ENTRY_ACTIONS


def test_a_decision_that_named_no_claim_refuses():
    """The claim is the evidence identity, so a decision without one is unjustified.

    There is no "named no authentication" case beside this: a decision this
    system writes never names a representative member, and the claim is what
    carries the evidence.
    """

    uncovered = _decision(coverage_evaluation_id=None)

    assert _authorizer(decisions=(uncovered,)).authorises(VERSION_ID) is False


def test_a_decision_without_its_policy_revision_refuses():
    anonymous = _decision(policy_revision=None)

    assert _authorizer(decisions=(anonymous,)).authorises(VERSION_ID) is False


def test_a_missing_policy_revision_refuses():
    """The policy is read by exact revision, never "whatever is active"."""

    authorizer = _authorizer(decisions=(_decision(),), policies=_Policies(None))

    assert authorizer.authorises(VERSION_ID) is False


def test_a_claim_for_another_version_refuses():
    """A decision pointing at another strategy's passing claim must not launch this."""

    elsewhere = _coverage(strategy_version_id="version-2")

    authorizer = _authorizer(
        decisions=(_decision(),), coverages=_Coverages(elsewhere)
    )

    assert authorizer.authorises(VERSION_ID) is False


def test_a_failed_claim_refuses():
    authorizer = _authorizer(
        decisions=(_decision(),),
        coverages=_Coverages(
            _coverage(
                verdict=StrategyCoverageVerdict.FAIL,
                blockers=(StrategyCoverageBlocker.EVIDENCE_MISSING,),
            )
        ),
    )

    assert authorizer.authorises(VERSION_ID) is False


def test_a_missing_coverage_record_refuses():
    authorizer = _authorizer(decisions=(_decision(),), coverages=_Coverages(None))

    assert authorizer.authorises(VERSION_ID) is False


# -- the member-level refusals, through the real validator ----------------


def test_a_revoked_member_key_refuses():
    """Revocation after the launch decision must stop the launch.

    This is the case the single-representative model could not see when the
    revoked key belonged to a member other than the one it inspected.
    """

    authorizer = _authorizer(
        decisions=(_decision(),), keys=_Keys(_key(EvidenceKeyTrustStatus.REVOKED))
    )

    assert authorizer.authorises(VERSION_ID) is False


def test_an_absent_key_refuses():
    authorizer = _authorizer(decisions=(_decision(),), keys=_Keys(None))

    assert authorizer.authorises(VERSION_ID) is False


def test_a_key_for_another_id_refuses():
    other = _key(key_id="other-key")

    authorizer = _authorizer(decisions=(_decision(),), keys=_Keys(other))

    assert authorizer.authorises(VERSION_ID) is False


def test_a_missing_authentication_record_refuses():
    authorizer = _authorizer(decisions=(_decision(),), authentications=_Records(()))

    assert authorizer.authorises(VERSION_ID) is False


def test_a_failed_authentication_record_refuses():
    authorizer = _authorizer(
        decisions=(_decision(),),
        authentications=_Records(
            (_authentication(verdict=EvidenceAuthenticationVerdict.FAIL),)
        ),
    )

    assert authorizer.authorises(VERSION_ID) is False


def test_a_missing_gate_record_refuses():
    authorizer = _authorizer(decisions=(_decision(),), gates=_Gates(()))

    assert authorizer.authorises(VERSION_ID) is False


def test_a_stale_member_refuses():
    """The decision's own policy supplies the age bound, per member."""

    authorizer = _authorizer(
        decisions=(_decision(),),
        coverages=_Coverages(
            _coverage(items=(_item(generated_at=NOW - timedelta(days=90)),))
        ),
    )

    assert authorizer.authorises(VERSION_ID) is False


def test_a_policy_with_no_age_bound_accepts_old_evidence():
    authorizer = _authorizer(
        decisions=(_decision(),),
        policies=_Policies(_policy(maximum_evidence_age=None)),
        coverages=_Coverages(
            _coverage(items=(_item(generated_at=NOW - timedelta(days=900)),))
        ),
    )

    assert authorizer.authorises(VERSION_ID) is True


def test_a_gate_from_another_policy_revision_refuses():
    """The required gate revision comes from the decision's own policy."""

    authorizer = _authorizer(
        decisions=(_decision(),),
        gates=_Gates((_gate(policy_version="independent-review-v9"),)),
    )

    assert authorizer.authorises(VERSION_ID) is False


# -- fail closed on infrastructure faults ---------------------------------


@pytest.mark.parametrize("fault", ["decisions", "coverages", "policies", "records", "keys"])
def test_an_unreadable_store_refuses_rather_than_raising(fault):
    """A launch gate that can throw is one a caller wraps in a broad except."""

    errors = {
        "decisions": StrategyLifecycleRepositoryNotFound("version-1"),
        "coverages": StrategyCoverageRepositoryNotFound(COVERAGE_ID),
        "policies": StrategyLifecycleRepositoryNotFound("lifecycle-policy-1"),
        "records": EvidenceAuthenticationRepositoryNotFound(AUTHENTICATION_ID),
        "keys": EvidenceTrustRootUnavailable("trust root is unavailable"),
    }
    replacements = {
        "decisions": {"decisions": _Decisions(error=errors["decisions"])},
        "coverages": {"coverages": _Coverages(error=errors["coverages"])},
        "policies": {"policies": _Policies(error=errors["policies"])},
        "records": {"authentications": _Records((), error=errors["records"])},
        "keys": {"keys": _Keys(error=errors["keys"])},
    }
    arguments = {"decisions": (_decision(),), **replacements[fault]}

    assert _authorizer(**arguments).authorises(VERSION_ID) is False


def test_the_authorizer_requires_all_four_dependencies():
    with pytest.raises(TypeError):
        PaperLaunchAuthorizer(
            decisions=_Decisions(), coverages=_Coverages(),
            lifecycle_policies=_Policies(_policy()), coverage_validity=None,
        )
    with pytest.raises(TypeError):
        PaperLaunchAuthorizer(
            decisions=_Decisions(), coverages=_Coverages(),
            lifecycle_policies=None,
            coverage_validity=StrategyCoverageCurrentValidator(
                authentications=_Records(()), gates=_Gates(()), key_source=_Keys()
            ),
        )
    with pytest.raises(TypeError):
        PaperLaunchAuthorizer(
            decisions=_Decisions(), coverages=None,
            lifecycle_policies=_Policies(_policy()),
            coverage_validity=StrategyCoverageCurrentValidator(
                authentications=_Records(()), gates=_Gates(()), key_source=_Keys()
            ),
        )


def test_the_authorizer_holds_no_representative_store_of_its_own():
    """The dependency direction, asserted on the object.

    An authorizer that could read one authentication and one key inline is an
    authorizer that can drift back to the representative model, so the absence of
    those collaborators is the guard.
    """

    authorizer = _authorizer(decisions=(_decision(),))
    assert not hasattr(authorizer, "_authentications")
    assert not hasattr(authorizer, "_key_source")
    assert hasattr(authorizer, "_coverage_validity")
    assert hasattr(authorizer, "_lifecycle_policies")
