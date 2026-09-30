"""Stage 6-C: the Paper launch gate.

``PaperLaunchAuthorizer`` is the only thing standing between a stored lifecycle
decision and a running Paper session, so each way it can be satisfied is
exercised separately, along with each way it refuses.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from us_quant.trading.application.paper_authorization import (
    PAPER_ENTRY_ACTIONS,
    PaperLaunchAuthorizer,
)
from us_quant.trading.domain.evidence_auth import (
    ED25519_ALGORITHM,
    EvidenceAuthenticationVerdict,
    EvidenceAuthenticationBlocker,
    EvidenceKeyTrustStatus,
    EvidenceVerificationKey,
)
from us_quant.trading.domain.strategy import StrategyStatus
from us_quant.trading.domain.strategy_coverage import (
    StrategyCoverageBlocker,
    StrategyCoverageVerdict,
)
from us_quant.trading.domain.strategy_lifecycle import (
    StrategyLifecycleAction,
    StrategyLifecycleBlocker as Blocker,
    StrategyLifecycleDecision,
    StrategyLifecycleDecisionState,
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
COVERAGE_ID = "sce-1"


def _decision(**changes):
    values = dict(
        decision_id="sld-1", strategy_version_id=VERSION_ID, strategy_semver="1.0.0",
        parameter_hash="parameter-1", universe_hash="u", code_hash="c",
        action=StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
        source_status=StrategyStatus.RESEARCH, target_status=StrategyStatus.PAPER_SHADOW,
        state=StrategyLifecycleDecisionState.APPLIED, blockers=(), triggers=(),
        policy_id="lifecycle-policy-1", policy_revision=1,
        policy_version="strategy-lifecycle-v1",
        authentication_id=AUTHENTICATION_ID, gate_evaluation_id="sge-1",
        coverage_evaluation_id=COVERAGE_ID, coverage_policy_id="coverage-policy-1",
        coverage_policy_revision=1, controller_version="strategy-lifecycle-v1",
        authorized_at=NOW, applied_at=NOW,
    )
    values.update(changes)
    return StrategyLifecycleDecision(**values)


class _Decisions:
    def __init__(self, decisions=(), *, error=None):
        self._decisions = tuple(decisions)
        self._error = error

    def decisions_for_version(self, version_id):
        if self._error is not None:
            raise self._error
        return self._decisions


class _Authentications:
    def __init__(self, result=None, *, error=None):
        self._result = result
        self._error = error

    def get(self, authentication_id):
        if self._error is not None:
            raise self._error
        if self._result is None:
            raise EvidenceAuthenticationRepositoryNotFound(authentication_id)
        return self._result


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


def _authentication(verdict=EvidenceAuthenticationVerdict.PASS, key_id=KEY_ID):
    # The authorizer inspects only the verdict and the key identity; a stand-in
    # keeps this test about the gate rather than about B1's record validation.
    return SimpleNamespace(verdict=verdict, key_id=key_id)


def _coverage(verdict=StrategyCoverageVerdict.PASS):
    return SimpleNamespace(verdict=verdict)


def _key(status=EvidenceKeyTrustStatus.ACTIVE):
    return EvidenceVerificationKey(
        key_id=KEY_ID, algorithm=ED25519_ALGORITHM,
        public_key=b"c" * 32, trust_status=status,
    )


def _authorizer(
    *, decisions=(), authentications=None, coverages=None, keys=None
):
    return PaperLaunchAuthorizer(
        decisions=_Decisions(decisions) if not isinstance(decisions, _Decisions) else decisions,
        authentications=(
            authentications if authentications is not None else _Authentications(_authentication())
        ),
        coverages=coverages if coverages is not None else _Coverages(_coverage()),
        key_source=keys if keys is not None else _Keys(_key()),
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
        triggers=(Blocker.REVOKED_SIGNING_KEY,),
    )
    authorizer = _authorizer(
        decisions=(pause, _decision()),  # newest first
        authentications=_Authentications(_authentication()),
        coverages=_Coverages(_coverage()),
        keys=_Keys(_key()),
    )

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
        triggers=(Blocker.REVOKED_SIGNING_KEY,),
    )

    assert _authorizer(decisions=(pause,)).authorises(VERSION_ID) is False


def test_pause_is_not_an_entry_action():
    assert StrategyLifecycleAction.PAUSE not in PAPER_ENTRY_ACTIONS


def test_a_decision_that_named_no_evidence_refuses():
    anonymous = _decision(authentication_id=None)

    assert _authorizer(decisions=(anonymous,)).authorises(VERSION_ID) is False


def test_a_decision_that_named_no_coverage_refuses():
    uncovered = _decision(coverage_evaluation_id=None)

    assert _authorizer(decisions=(uncovered,)).authorises(VERSION_ID) is False


def test_a_failed_authentication_refuses():
    authorizer = _authorizer(
        decisions=(_decision(),),
        authentications=_Authentications(
            _authentication(EvidenceAuthenticationVerdict.FAIL)
        ),
    )

    assert authorizer.authorises(VERSION_ID) is False


def test_a_missing_authentication_record_refuses():
    authorizer = _authorizer(
        decisions=(_decision(),), authentications=_Authentications(None)
    )

    assert authorizer.authorises(VERSION_ID) is False


def test_a_revoked_key_refuses():
    """Revocation after the launch decision must stop the launch."""

    authorizer = _authorizer(
        decisions=(_decision(),), keys=_Keys(_key(EvidenceKeyTrustStatus.REVOKED))
    )

    assert authorizer.authorises(VERSION_ID) is False


def test_an_absent_key_refuses():
    authorizer = _authorizer(decisions=(_decision(),), keys=_Keys(None))

    assert authorizer.authorises(VERSION_ID) is False


def test_a_key_for_another_id_refuses():
    other = EvidenceVerificationKey(
        key_id="other-key", algorithm=ED25519_ALGORITHM,
        public_key=b"d" * 32, trust_status=EvidenceKeyTrustStatus.ACTIVE,
    )

    authorizer = _authorizer(decisions=(_decision(),), keys=_Keys(other))

    assert authorizer.authorises(VERSION_ID) is False


def test_a_failed_coverage_refuses():
    authorizer = _authorizer(
        decisions=(_decision(),),
        coverages=_Coverages(_coverage(StrategyCoverageVerdict.FAIL)),
    )

    assert authorizer.authorises(VERSION_ID) is False


def test_a_missing_coverage_record_refuses():
    authorizer = _authorizer(decisions=(_decision(),), coverages=_Coverages(None))

    assert authorizer.authorises(VERSION_ID) is False


# -- fail closed on infrastructure faults ---------------------------------


@pytest.mark.parametrize("fault", ["decisions", "authentications", "coverages", "keys"])
def test_an_unreadable_store_refuses_rather_than_raising(fault):
    """A launch gate that can throw is one a caller wraps in a broad except."""

    errors = {
        "decisions": StrategyLifecycleRepositoryNotFound("version-1"),
        "authentications": EvidenceAuthenticationRepositoryNotFound(AUTHENTICATION_ID),
        "coverages": StrategyCoverageRepositoryNotFound(COVERAGE_ID),
        "keys": EvidenceTrustRootUnavailable("trust root is unavailable"),
    }
    replacements = {
        "decisions": {"decisions": _Decisions(error=errors["decisions"])},
        "authentications": {"authentications": _Authentications(error=errors["authentications"])},
        "coverages": {"coverages": _Coverages(error=errors["coverages"])},
        "keys": {"keys": _Keys(error=errors["keys"])},
    }
    arguments = {"decisions": (_decision(),), **replacements[fault]}

    assert _authorizer(**arguments).authorises(VERSION_ID) is False


def test_the_authorizer_requires_all_four_dependencies():
    with pytest.raises(TypeError):
        PaperLaunchAuthorizer(
            decisions=_Decisions(), authentications=None, coverages=_Coverages(),
            key_source=_Keys(_key()),
        )
    with pytest.raises(TypeError):
        PaperLaunchAuthorizer(
            decisions=_Decisions(), authentications=_Authentications(),
            coverages=_Coverages(), key_source=None,
        )
