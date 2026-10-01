"""Current validity of an already formed coverage claim.

The property under test is narrow and worth stating plainly: a coverage claim
that passed when it was formed does **not** stay true by itself.  A signing key
can be revoked afterwards, a gate record can disappear, an identity can drift,
evidence can age out -- and every one of those has to stop authorising new
mutations *now*, not at the moment the claim was written.

The tests are organised by the question each one answers, because the failure
modes are different repairs:

* a member whose *record* is gone or no longer passes;
* a member whose *identity* no longer matches the item that named it;
* a member whose *key* is no longer trusted;
* a member whose *evidence* has aged out;
* the whole claim being unusable (not PASS, or naming no members at all).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from us_quant.trading.application.strategy_coverage_validity import (
    StrategyCoverageCurrentValidator,
    StrategyCoverageValidityBlocker,
    StrategyCoverageValidityResult,
    StrategyCoverageValidityVerdict,
)
from us_quant.trading.domain.evidence_auth import (
    EvidenceAuthenticationBlocker,
    EvidenceAuthenticationResult,
    EvidenceAuthenticationVerdict,
    EvidenceKeyTrustStatus,
    EvidenceVerificationKey,
)
from us_quant.trading.domain.strategy_coverage import (
    StrategyCoverageBlocker,
    StrategyCoverageEvaluation,
    StrategyCoverageItem,
    StrategyCoverageVerdict,
)
from us_quant.trading.domain.strategy_gate import (
    StrategyGateBlocker,
    StrategyGateEvaluation,
    StrategyGateVerdict,
)
from us_quant.trading.ports.evidence_authentication_repository import (
    EvidenceAuthenticationRepositoryError,
)
from us_quant.trading.ports.evidence_verification import (
    EvidenceTrustRootUnavailable,
)

_NOW = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
_VERSION = "version-1"
_PARAMETER_HASH = "params-1"
_KEY_A = "key-a"
_KEY_B = "key-b"
_RUN_A = "review-a"
_RUN_B = "review-b"


# =====================================================================
# Builders
# =====================================================================


def _authentication(
    *,
    authentication_id: str,
    key_id: str,
    review_run_id: str,
    version_id: str = _VERSION,
    verdict: EvidenceAuthenticationVerdict = EvidenceAuthenticationVerdict.PASS,
    verified_at: datetime | None = None,
) -> EvidenceAuthenticationResult:
    return EvidenceAuthenticationResult(
        authentication_id=authentication_id,
        strategy_version_id=version_id,
        review_run_id=review_run_id,
        artifact_digest="digest",
        key_id=key_id,
        algorithm="ed25519",
        signature_digest="sig",
        verdict=verdict,
        blockers=(
            ()
            if verdict is EvidenceAuthenticationVerdict.PASS
            else (EvidenceAuthenticationBlocker.SIGNATURE_INVALID,)
        ),
        authenticator_version="auth-1",
        policy_version="auth-policy-1",
        verified_at=verified_at or _NOW - timedelta(hours=1),
    )


def _gate(
    *,
    evaluation_id: str,
    review_run_id: str,
    symbol: str,
    data_hash: str,
    version_id: str = _VERSION,
    parameter_hash: str = _PARAMETER_HASH,
    verdict: StrategyGateVerdict = StrategyGateVerdict.PASS,
    policy_version: str = "gate-policy-1",
    evaluated_at: datetime | None = None,
) -> StrategyGateEvaluation:
    return StrategyGateEvaluation(
        evaluation_id=evaluation_id,
        strategy_version_id=version_id,
        review_run_id=review_run_id,
        parameter_hash=parameter_hash,
        data_hash=data_hash,
        symbol=symbol,
        provider="provider",
        verdict=verdict,
        blockers=()
        if verdict is StrategyGateVerdict.PASS
        else (StrategyGateBlocker.STALE_EVIDENCE,),
        review_decision="approved",
        review_blocking_failures=0,
        review_passed_gates=1,
        review_gate_count=1,
        evaluator_version="gate-1",
        policy_version=policy_version,
        evaluated_at=evaluated_at or _NOW - timedelta(hours=1),
    )


def _item(
    *,
    symbol: str,
    authentication_id: str,
    key_id: str,
    review_run_id: str,
    gate_evaluation_id: str,
    data_hash: str,
    generated_at: datetime | None = None,
) -> StrategyCoverageItem:
    return StrategyCoverageItem(
        symbol=symbol,
        review_run_id=review_run_id,
        data_hash=data_hash,
        key_id=key_id,
        authentication_id=authentication_id,
        gate_evaluation_id=gate_evaluation_id,
        signed_at=_NOW - timedelta(hours=2),
        generated_at=generated_at or _NOW - timedelta(hours=1),
    )


def _coverage(
    items: tuple[StrategyCoverageItem, ...],
    *,
    verdict: StrategyCoverageVerdict = StrategyCoverageVerdict.PASS,
    version_id: str = _VERSION,
    parameter_hash: str = _PARAMETER_HASH,
) -> StrategyCoverageEvaluation:
    return StrategyCoverageEvaluation(
        evaluation_id="coverage-1",
        strategy_version_id=version_id,
        strategy_semver="1.0.0",
        parameter_hash=parameter_hash,
        universe_hash="universe-1",
        code_hash="code-1",
        policy_id="coverage-policy",
        policy_revision=1,
        policy_version="coverage-policy-1",
        verdict=verdict,
        blockers=()
        if verdict is StrategyCoverageVerdict.PASS
        else (StrategyCoverageBlocker.GATE_NOT_PASSED,),
        items=items,
        covered_symbols=tuple(item.symbol for item in items),
        required_symbols=tuple(item.symbol for item in items),
        distinct_review_runs=len({item.review_run_id for item in items}),
        distinct_data_hashes=len({item.data_hash for item in items}),
        evaluator_version="coverage-1",
        evaluated_at=_NOW - timedelta(minutes=30),
    )


def _pair(
    *,
    symbol: str,
    suffix: str,
    key_id: str,
    review_run_id: str,
    generated_at: datetime | None = None,
    auth_verdict: EvidenceAuthenticationVerdict = (
        EvidenceAuthenticationVerdict.PASS
    ),
    gate_verdict: StrategyGateVerdict = StrategyGateVerdict.PASS,
    gate_policy_version: str = "gate-policy-1",
) -> tuple[StrategyCoverageItem, EvidenceAuthenticationResult, StrategyGateEvaluation]:
    """One coverage member with the two records it names."""

    authentication_id = f"auth-{suffix}"
    gate_id = f"gate-{suffix}"
    data_hash = f"data-{suffix}"
    item = _item(
        symbol=symbol,
        authentication_id=authentication_id,
        key_id=key_id,
        review_run_id=review_run_id,
        gate_evaluation_id=gate_id,
        data_hash=data_hash,
        generated_at=generated_at,
    )
    return (
        item,
        _authentication(
            authentication_id=authentication_id,
            key_id=key_id,
            review_run_id=review_run_id,
            verdict=auth_verdict,
        ),
        _gate(
            evaluation_id=gate_id,
            review_run_id=review_run_id,
            symbol=symbol,
            data_hash=data_hash,
            verdict=gate_verdict,
            policy_version=gate_policy_version,
        ),
    )


class _Authentications:
    def __init__(self, records) -> None:
        self._records = {row.authentication_id: row for row in records}
        self.reads: list[str] = []

    def get(self, authentication_id: str):
        self.reads.append(authentication_id)
        return self._records.get(authentication_id)


class _Gates:
    def __init__(self, records) -> None:
        self._records = {row.evaluation_id: row for row in records}
        self.reads: list[str] = []

    def get(self, evaluation_id: str):
        self.reads.append(evaluation_id)
        return self._records.get(evaluation_id)


class _Keys:
    """A trust root whose statuses can be changed between reads."""

    def __init__(self, statuses: dict[str, EvidenceKeyTrustStatus]) -> None:
        self.statuses = statuses
        self.unavailable = False
        self.reads: list[str] = []

    def verification_key(self, key_id: str):
        self.reads.append(key_id)
        if self.unavailable:
            raise EvidenceTrustRootUnavailable("the trust root is unreadable")
        status = self.statuses.get(key_id)
        if status is None:
            return None
        return EvidenceVerificationKey(
            key_id=key_id,
            algorithm="ed25519",
            public_key=b"public-bytes",
            trust_status=status,
        )


class _Fixture:
    def __init__(self, members, *, key_statuses=None) -> None:
        self.items = tuple(member[0] for member in members)
        self.authentications = _Authentications([m[1] for m in members])
        self.gates = _Gates([m[2] for m in members])
        self.keys = _Keys(
            key_statuses
            if key_statuses is not None
            else {_KEY_A: EvidenceKeyTrustStatus.ACTIVE, _KEY_B: EvidenceKeyTrustStatus.ACTIVE}
        )
        self.validator = StrategyCoverageCurrentValidator(
            authentications=self.authentications,
            gates=self.gates,
            key_source=self.keys,
        )

    def validate(self, coverage=None, **kwargs):
        kwargs.setdefault("required_gate_policy_version", "gate-policy-1")
        # Explicit, because the validator now requires the bound to be passed:
        # ``None`` means "this policy sets no age bound" and must not be
        # reachable by omission.  The default here is the fixture's own choice of
        # a bound, and a case that wants no bound passes it explicitly.
        kwargs.setdefault("maximum_evidence_age", timedelta(days=30))
        return self.validator.validate(
            coverage if coverage is not None else _coverage(self.items),
            now=kwargs.pop("now", _NOW),
            **kwargs,
        )


def _two_members(**overrides) -> _Fixture:
    """The canonical multi-member claim: A signed by key-a, B by key-b."""

    first = _pair(
        symbol="AAA", suffix="a", key_id=_KEY_A, review_run_id=_RUN_A
    )
    second = _pair(
        symbol="BBB", suffix="b", key_id=_KEY_B, review_run_id=_RUN_B
    )
    return _Fixture((first, second), **overrides)


# =====================================================================
# The happy path
# =====================================================================


def test_a_fully_current_claim_is_valid() -> None:
    result = _two_members().validate()
    assert result.verdict is StrategyCoverageValidityVerdict.VALID
    assert result.blockers == ()
    assert result.valid is True


def test_every_member_is_read_by_its_exact_identity() -> None:
    """No "latest" lookup anywhere: the item names the record it depends on.

    A validator that resolved the item to the newest record for the version would
    answer "is there some passing evidence" while appearing to answer "is *this*
    evidence still good" -- and the difference is the whole repair.
    """

    fixture = _two_members()
    fixture.validate()
    assert sorted(fixture.authentications.reads) == ["auth-a", "auth-b"]
    assert sorted(fixture.gates.reads) == ["gate-a", "gate-b"]
    assert sorted(fixture.keys.reads) == [_KEY_A, _KEY_B]


def test_a_verify_only_key_still_authorises() -> None:
    """Key rotation is not revocation.

    A retired-but-still-trusted key must keep verifying historical evidence, or
    every rotation would retroactively invalidate the record it signed.
    """

    fixture = _two_members(
        key_statuses={
            _KEY_A: EvidenceKeyTrustStatus.VERIFY_ONLY,
            _KEY_B: EvidenceKeyTrustStatus.ACTIVE,
        }
    )
    assert fixture.validate().valid is True


# =====================================================================
# The whole claim
# =====================================================================


def test_a_claim_that_is_not_pass_is_invalid() -> None:
    fixture = _two_members()
    result = fixture.validate(_coverage(fixture.items, verdict=StrategyCoverageVerdict.FAIL))
    assert StrategyCoverageValidityBlocker.COVERAGE_NOT_PASSED in result.blockers


def test_a_claim_naming_no_members_cannot_even_be_built() -> None:
    """The empty-member case is refused upstream, and this records where.

    ``StrategyCoverageEvaluation`` already requires a PASS to name at least one
    admitted item, so a passing claim with no members is not constructible.  The
    validator's own check for it is defence in depth -- a hand-built or
    deserialised record could still arrive that way -- and this test pins the
    division of labour so a future reader does not delete either half.
    """

    with pytest.raises(ValueError):
        _coverage(())

    # The validator's half: a record that reached it anyway -- hand-built, or read
    # back from a store that lost its items -- is still refused rather than being
    # treated as vacuously current.  Built with the dataclass directly, because
    # the constructor above is precisely what refuses the honest path.
    empty = StrategyCoverageEvaluation(
        evaluation_id="coverage-1",
        strategy_version_id=_VERSION,
        strategy_semver="1.0.0",
        parameter_hash=_PARAMETER_HASH,
        universe_hash="universe-1",
        code_hash="code-1",
        policy_id="coverage-policy",
        policy_revision=1,
        policy_version="coverage-policy-1",
        verdict=StrategyCoverageVerdict.FAIL,
        blockers=(StrategyCoverageBlocker.EVIDENCE_MISSING,),
        items=(),
        covered_symbols=(),
        required_symbols=("AAA",),
        distinct_review_runs=0,
        distinct_data_hashes=0,
        evaluator_version="coverage-1",
        evaluated_at=_NOW - timedelta(minutes=30),
    )
    fixture = _Fixture(())
    result = fixture.validator.validate(
        empty, now=_NOW, required_gate_policy_version='gate-policy-1',
        maximum_evidence_age=None,
    )
    assert result.verdict is StrategyCoverageValidityVerdict.INVALID
    assert StrategyCoverageValidityBlocker.COVERAGE_NOT_PASSED in result.blockers


def test_a_missing_claim_is_invalid() -> None:
    fixture = _two_members()
    result = fixture.validator.validate(
        None, now=_NOW, required_gate_policy_version='gate-policy-1',
        maximum_evidence_age=None,
    )
    assert result.verdict is StrategyCoverageValidityVerdict.INVALID


def test_a_duplicate_member_identity_is_invalid() -> None:
    fixture = _two_members()
    duplicated = _coverage((fixture.items[0], fixture.items[0]))
    result = fixture.validate(duplicated)
    assert (
        StrategyCoverageValidityBlocker.DUPLICATE_MEMBER_IDENTITY
        in result.blockers
    )


def test_a_naive_now_is_refused() -> None:
    """A naive instant cannot be compared with a stored one, so it is not guessed."""

    fixture = _two_members()
    with pytest.raises(ValueError):
        fixture.validator.validate(
            _coverage(fixture.items),
            now=datetime(2026, 9, 28, 15, 0),
            required_gate_policy_version='gate-policy-1',
            maximum_evidence_age=None,
        )


# =====================================================================
# Revocation -- the gap this repair closes
# =====================================================================


def test_one_revoked_member_invalidates_the_whole_claim() -> None:
    """The reported hole, at its smallest.

    ``coverage = A(key-a) + B(key-b)``; key-a is revoked afterwards.  B is still
    active, and the old model would have let B's representative authentication
    carry the claim.  Every member is checked, so one revoked key is enough.
    """

    fixture = _two_members(
        key_statuses={
            _KEY_A: EvidenceKeyTrustStatus.REVOKED,
            _KEY_B: EvidenceKeyTrustStatus.ACTIVE,
        }
    )
    result = fixture.validate()
    assert result.verdict is StrategyCoverageValidityVerdict.INVALID
    assert StrategyCoverageValidityBlocker.REVOKED_SIGNING_KEY in result.blockers


def test_an_unknown_member_key_invalidates_the_claim() -> None:
    fixture = _two_members(
        key_statuses={_KEY_B: EvidenceKeyTrustStatus.ACTIVE}
    )
    result = fixture.validate()
    assert StrategyCoverageValidityBlocker.UNKNOWN_SIGNING_KEY in result.blockers


def test_an_unreadable_trust_root_invalidates_the_claim() -> None:
    fixture = _two_members()
    fixture.keys.unavailable = True
    result = fixture.validate()
    assert (
        StrategyCoverageValidityBlocker.TRUST_ROOT_UNAVAILABLE in result.blockers
    )


# =====================================================================
# Records that vanished or stopped passing
# =====================================================================


def test_a_missing_authentication_record_invalidates_the_claim() -> None:
    fixture = _two_members()
    fixture.authentications._records.pop("auth-a")
    result = fixture.validate()
    assert (
        StrategyCoverageValidityBlocker.AUTHENTICATION_RECORD_MISSING
        in result.blockers
    )


def test_a_missing_gate_record_invalidates_the_claim() -> None:
    """And it is not substituted by another passing gate for the version.

    The item names the gate it counted on; a different PASS gate is a different
    piece of evidence, and accepting it would silently rewrite the coverage claim.
    """

    fixture = _two_members()
    fixture.gates._records.pop("gate-a")
    result = fixture.validate()
    assert StrategyCoverageValidityBlocker.GATE_RECORD_MISSING in result.blockers


def test_an_authentication_that_no_longer_passes_invalidates_the_claim() -> None:
    member = _pair(
        symbol="AAA",
        suffix="a",
        key_id=_KEY_A,
        review_run_id=_RUN_A,
        auth_verdict=EvidenceAuthenticationVerdict.FAIL,
    )
    fixture = _Fixture((member,))
    result = fixture.validate()
    assert (
        StrategyCoverageValidityBlocker.AUTHENTICATION_NOT_PASSED
        in result.blockers
    )


def test_a_gate_that_no_longer_passes_invalidates_the_claim() -> None:
    member = _pair(
        symbol="AAA",
        suffix="a",
        key_id=_KEY_A,
        review_run_id=_RUN_A,
        gate_verdict=StrategyGateVerdict.FAIL,
    )
    fixture = _Fixture((member,))
    result = fixture.validate()
    assert StrategyCoverageValidityBlocker.GATE_NOT_PASSED in result.blockers


def test_an_unreadable_authentication_store_invalidates_the_claim() -> None:
    """A port failure is a refusal; a programming bug is not swallowed."""

    fixture = _two_members()

    def explode(_authentication_id: str):
        raise EvidenceAuthenticationRepositoryError("the store is corrupt")

    fixture.authentications.get = explode
    result = fixture.validate()
    assert (
        StrategyCoverageValidityBlocker.AUTHENTICATION_RECORD_MISSING
        in result.blockers
    )


def test_a_programming_error_is_not_disguised_as_a_missing_record() -> None:
    """The reason the handlers catch the port's errors and not ``Exception``.

    A bare ``except Exception`` also swallows an ``AttributeError`` from a typo,
    reports it as a missing record, and leaves a green suite over a real defect.
    The failure has to reach the test rather than be turned into a safe-looking
    refusal.
    """

    fixture = _two_members()

    def broken(_authentication_id: str):
        raise AttributeError("a typo, not a storage failure")

    fixture.authentications.get = broken
    with pytest.raises(AttributeError):
        fixture.validate()


def test_a_gate_programming_error_is_not_disguised_either() -> None:
    fixture = _two_members()

    def broken(_evaluation_id: str):
        raise TypeError("a typo, not a storage failure")

    fixture.gates.get = broken
    with pytest.raises(TypeError):
        fixture.validate()


# =====================================================================
# Identity drift
# =====================================================================


@pytest.mark.parametrize(
    "field, value",
    (
        ("key_id", "other-key"),
        ("review_run_id", "other-run"),
        ("version_id", "other-version"),
    ),
)
def test_an_authentication_whose_identity_drifted_invalidates_the_claim(
    field: str, value: str,
) -> None:
    """Exact comparison, with no grandfathering for a missing field.

    The store is keyed by the id the item looks up, so a record that still
    *exists* under that id but describes something else is what this catches --
    and it must be caught, because the item's identity is what the coverage claim
    rests on.
    """

    member = _pair(
        symbol="AAA", suffix="a", key_id=_KEY_A, review_run_id=_RUN_A
    )
    item, _record, gate = member
    kwargs = {
        "authentication_id": "auth-a",
        "key_id": _KEY_A,
        "review_run_id": _RUN_A,
        "version_id": _VERSION,
    }
    kwargs[field] = value
    drifted = _authentication(**kwargs)
    fixture = _Fixture(((item, drifted, gate),))
    result = fixture.validate()
    assert (
        StrategyCoverageValidityBlocker.MEMBER_IDENTITY_MISMATCH
        in result.blockers
    )


def test_an_authentication_looked_up_by_another_id_is_missing() -> None:
    """The identity check starts with the lookup, which is the point.

    A record filed under a different ``authentication_id`` is not "a record whose
    id drifted" -- it is *not the record the item named*, and the exact lookup
    says so before any field comparison happens.  An earlier version of this test
    expected a mismatch here and was wrong about which guard fires first.
    """

    member = _pair(
        symbol="AAA", suffix="a", key_id=_KEY_A, review_run_id=_RUN_A
    )
    item, _record, gate = member
    elsewhere = _authentication(
        authentication_id="other-auth", key_id=_KEY_A, review_run_id=_RUN_A
    )
    fixture = _Fixture(((item, elsewhere, gate),))
    result = fixture.validate()
    assert (
        StrategyCoverageValidityBlocker.AUTHENTICATION_RECORD_MISSING
        in result.blockers
    )


def test_an_authentication_with_a_null_key_id_is_a_mismatch_not_an_unknown() -> None:
    """The compatibility cost of strict comparison, asserted on purpose.

    Evidence predating the identity columns fails closed and has to be
    re-established through the current pipeline.  That is a deliberate trade: a
    record that cannot prove which key signed it must not be read as probably
    fine.
    """

    member = _pair(
        symbol="AAA", suffix="a", key_id=_KEY_A, review_run_id=_RUN_A
    )
    item, _authentication_record, gate = member
    # A legacy row cannot even claim PASS: the record type requires a
    # ``review_run_id`` for a passing verdict, which is the same fail-closed
    # choice this validator makes, made one layer earlier.  So the legacy shape is
    # expressed as it would actually arrive -- a record that never got far enough
    # to identify itself, carrying a non-passing verdict.
    legacy = EvidenceAuthenticationResult(
        authentication_id="auth-a",
        strategy_version_id=_VERSION,
        review_run_id=None,
        artifact_digest=None,
        key_id=None,
        algorithm=None,
        signature_digest=None,
        verdict=EvidenceAuthenticationVerdict.FAIL,
        blockers=(EvidenceAuthenticationBlocker.AUTHENTICATION_MISSING,),
        authenticator_version="auth-1",
        policy_version="auth-policy-1",
        verified_at=_NOW - timedelta(hours=1),
    )
    fixture = _Fixture(((item, legacy, gate),))
    result = fixture.validate()
    assert (
        StrategyCoverageValidityBlocker.MEMBER_IDENTITY_MISMATCH
        in result.blockers
    )


@pytest.mark.parametrize(
    "field, value",
    (
        ("review_run_id", "other-run"),
        ("parameter_hash", "other-params"),
        ("data_hash", "other-data"),
        ("symbol", "OTHER"),
    ),
)
def test_a_gate_whose_identity_drifted_invalidates_the_claim(
    field: str, value: str,
) -> None:
    """The gate is looked up by the item's exact id and must describe the item."""

    member = _pair(
        symbol="AAA", suffix="a", key_id=_KEY_A, review_run_id=_RUN_A
    )
    item, authentication, _gate_record = member
    kwargs = {
        "evaluation_id": "gate-a",
        "review_run_id": _RUN_A,
        "symbol": "AAA",
        "data_hash": "data-a",
        "parameter_hash": _PARAMETER_HASH,
    }
    kwargs[field] = value
    drifted = _gate(**kwargs)
    fixture = _Fixture(((item, authentication, drifted),))
    result = fixture.validate()
    assert (
        StrategyCoverageValidityBlocker.MEMBER_IDENTITY_MISMATCH
        in result.blockers
    )


def test_a_gate_looked_up_by_another_id_is_missing() -> None:
    """Same as the authentication case: the exact lookup is the first guard."""

    member = _pair(
        symbol="AAA", suffix="a", key_id=_KEY_A, review_run_id=_RUN_A
    )
    item, authentication, _gate_record = member
    elsewhere = _gate(
        evaluation_id="other-gate",
        review_run_id=_RUN_A,
        symbol="AAA",
        data_hash="data-a",
    )
    fixture = _Fixture(((item, authentication, elsewhere),))
    result = fixture.validate()
    assert StrategyCoverageValidityBlocker.GATE_RECORD_MISSING in result.blockers


def test_a_gate_from_another_policy_revision_invalidates_the_claim() -> None:
    member = _pair(
        symbol="AAA",
        suffix="a",
        key_id=_KEY_A,
        review_run_id=_RUN_A,
        gate_policy_version="gate-policy-OLD",
    )
    fixture = _Fixture((member,))
    result = fixture.validate(required_gate_policy_version="gate-policy-1")
    assert StrategyCoverageValidityBlocker.GATE_POLICY_MISMATCH in result.blockers


def test_the_required_gate_policy_version_is_mandatory() -> None:
    """Required, not optional, so a caller cannot skip the check by omission.

    An optional argument with a default is how the previous model let one
    representative gate stand in for the claim; the same shape here would let a
    future launch path forget the policy and silently pass every gate.
    """

    fixture = _two_members()
    with pytest.raises(TypeError):
        fixture.validator.validate(_coverage(fixture.items), now=_NOW)
    with pytest.raises(ValueError):
        fixture.validate(required_gate_policy_version="")
    with pytest.raises(ValueError):
        fixture.validate(required_gate_policy_version="   ")
    with pytest.raises(ValueError):
        fixture.validate(required_gate_policy_version=None)


# =====================================================================
# Freshness, judged per member
# =====================================================================


def test_one_stale_member_invalidates_the_whole_claim() -> None:
    """A claim is as fresh as its oldest member.

    Checking one representative would let a stale member ride along behind a
    fresh one, which is the same shape of hole as the revocation gap.
    """

    fresh = _pair(
        symbol="AAA", suffix="a", key_id=_KEY_A, review_run_id=_RUN_A
    )
    stale = _pair(
        symbol="BBB",
        suffix="b",
        key_id=_KEY_B,
        review_run_id=_RUN_B,
        generated_at=_NOW - timedelta(days=40),
    )
    fixture = _Fixture((fresh, stale))
    result = fixture.validate(maximum_evidence_age=timedelta(days=30))
    assert StrategyCoverageValidityBlocker.STALE_MEMBER in result.blockers


def test_freshness_is_not_checked_without_a_bound() -> None:
    """``None`` means "the policy sets no age bound", and must be passed on.

    It is *not* the same as omitting the argument -- see
    ``test_the_age_bound_cannot_be_dropped_by_omission`` -- so a caller who wants
    no bound says so explicitly, as this case does.
    """

    stale = _pair(
        symbol="AAA",
        suffix="a",
        key_id=_KEY_A,
        review_run_id=_RUN_A,
        generated_at=_NOW - timedelta(days=400),
    )
    fixture = _Fixture((stale,))
    assert fixture.validate(maximum_evidence_age=None).valid is True


def test_the_age_bound_cannot_be_dropped_by_omission() -> None:
    """The freshness half of the check cannot be disabled by forgetting it.

    The validator requires ``maximum_evidence_age`` to be passed, so "no bound"
    is only reachable by a caller that deliberately passes ``None`` -- which the
    policy supplies.  Omitting it used to default to ``None`` and silently accept
    arbitrarily old evidence, the same shape of fail-open the mandatory
    ``required_gate_policy_version`` exists to prevent.
    """

    fixture = _two_members()
    with pytest.raises(TypeError):
        fixture.validator.validate(
            _coverage(fixture.items),
            now=_NOW,
            required_gate_policy_version="gate-policy-1",
        )


def test_a_wrong_typed_age_bound_is_refused_rather_than_read_as_unbounded() -> None:
    """The type check is what stops a wrong value from disabling the bound.

    This case is deliberately built so that the *comparison* cannot catch the
    error.  ``timedelta > int`` raises ``TypeError`` on its own, so asserting
    that ``30`` and ``"30d"`` are refused would pass even with the explicit
    ``isinstance`` check deleted -- the test would be pinning Python's operator
    behaviour, not this validator's guard.

    ``_NeverStale`` is duck-typed so its comparisons simply answer "not too old".
    Without the ``isinstance`` check a 999-day-old member would therefore be
    reported VALID, which is a fail-open: the policy's age bound silently stops
    applying.  The refusal has to come from the check, and this asserts that.
    """

    class _NeverStale:
        def __gt__(self, _other):
            return False

        def __lt__(self, _other):
            return False

    stale = _pair(
        symbol="AAA",
        suffix="a",
        key_id=_KEY_A,
        review_run_id=_RUN_A,
        generated_at=_NOW - timedelta(days=999),
    )
    fixture = _Fixture((stale,))

    with pytest.raises(TypeError):
        fixture.validate(maximum_evidence_age=_NeverStale())

    # The plain wrong types, kept as the obvious half.
    with pytest.raises(TypeError):
        fixture.validate(maximum_evidence_age=30)
    with pytest.raises(TypeError):
        fixture.validate(maximum_evidence_age="30d")


def test_evidence_from_the_future_is_refused() -> None:
    """A timestamp ahead of now is a clock or a record problem, not freshness."""

    ahead = _pair(
        symbol="AAA",
        suffix="a",
        key_id=_KEY_A,
        review_run_id=_RUN_A,
        generated_at=_NOW + timedelta(hours=1),
    )
    fixture = _Fixture((ahead,))
    result = fixture.validate(maximum_evidence_age=timedelta(days=30))
    assert (
        StrategyCoverageValidityBlocker.FUTURE_MEMBER_TIMESTAMP
        in result.blockers
    )


# =====================================================================
# The result type
# =====================================================================


def test_blockers_are_sorted_and_deduplicated() -> None:
    """Deterministic output, so two runs over the same stores compare equal."""

    fixture = _two_members(
        key_statuses={_KEY_A: EvidenceKeyTrustStatus.REVOKED}
    )
    fixture.authentications._records.pop("auth-b")
    fixture.gates._records.pop("gate-a")
    first = fixture.validate()
    second = fixture.validate()
    assert first == second
    values = [row.value for row in first.blockers]
    assert values == sorted(values)
    assert len(values) == len(set(values))


def test_a_result_cannot_contradict_itself() -> None:
    """The verdict and its reasons are locked together at construction."""

    with pytest.raises(ValueError):
        StrategyCoverageValidityResult(
            verdict=StrategyCoverageValidityVerdict.VALID,
            blockers=(StrategyCoverageValidityBlocker.REVOKED_SIGNING_KEY,),
        )
    with pytest.raises(ValueError):
        StrategyCoverageValidityResult(
            verdict=StrategyCoverageValidityVerdict.INVALID,
            blockers=(),
        )


def test_the_result_is_immutable() -> None:
    result = _two_members().validate()
    with pytest.raises(Exception):
        result.verdict = StrategyCoverageValidityVerdict.INVALID


def test_the_validator_requires_all_three_reads() -> None:
    with pytest.raises(TypeError):
        StrategyCoverageCurrentValidator(
            authentications=None, gates=_Gates(()), key_source=_Keys({})
        )
    with pytest.raises(TypeError):
        StrategyCoverageCurrentValidator(
            authentications=_Authentications(()), gates=None, key_source=_Keys({})
        )
    with pytest.raises(TypeError):
        StrategyCoverageCurrentValidator(
            authentications=_Authentications(()),
            gates=_Gates(()),
            key_source=None,
        )


def test_the_validator_writes_nothing() -> None:
    """A read-only validator cannot change a decision or a coverage record.

    Asserted as "no attribute is rebound" rather than by inspecting the stores:
    the guarantee is that this object has no write surface at all.
    """

    fixture = _two_members()
    before = {
        name: getattr(fixture.validator, name)
        for name in ("_authentications", "_gates", "_key_source")
    }
    fixture.validate()
    after = {
        name: getattr(fixture.validator, name)
        for name in ("_authentications", "_gates", "_key_source")
    }
    assert before == after


# =====================================================================
# The validator's own boundaries
# =====================================================================


def test_the_validator_reaches_no_adapter_and_no_authority() -> None:
    """It reads three ports and writes nothing -- asserted as imports.

    A validator that could name a SQLite adapter, a lifecycle authority or a
    broker would be a place where a re-check turns into a decision, which is the
    second authority this whole repair exists to avoid.
    """

    import ast
    import pathlib

    source = pathlib.Path(
        "src/us_quant/trading/application/strategy_coverage_validity.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    for forbidden in (
        "sqlite3",
        "us_quant.trading.adapters",
        "us_quant.trading.composition",
        "us_quant.trading.application.strategy_lifecycle",
        "us_quant.trading.application.paper_authorization",
        "us_quant.trading.application.strategy_coverage",
        "us_quant.trading.runtime",
        "us_quant.desktop",
        "us_quant.desktop_v2",
        "PySide6",
        "ibapi",
    ):
        assert not any(
            name == forbidden or name.startswith(forbidden + ".")
            for name in imported
        ), forbidden