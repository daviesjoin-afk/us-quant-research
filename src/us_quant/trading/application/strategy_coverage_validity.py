"""Whether an *already formed* coverage claim is still trustworthy right now.

Two questions, and keeping them apart is the reason this module exists rather
than another method on the evaluator:

* ``StrategyCoverageEvaluator`` asks whether a version's universe *satisfied the
  coverage policy* at the moment it was evaluated.  That is a computation, and
  its answer is frozen into a :class:`StrategyCoverageEvaluation`.
* this validator asks whether the exact evidence that evaluation recorded is
  *still* what it was: every member's authentication record still exists and
  still passes, every member's gate record still exists and still passes, every
  member's identity still matches the item that named it, and every member's
  signing key is still trusted.

The distinction matters because the two can disagree in one direction only.  A
coverage claim that passed at T does not stay true by itself -- a key can be
revoked at T+1 -- and the whole point of re-checking at decision time is that the
person who signed yesterday may not be trusted today.

What this deliberately does **not** do:

* it does not re-evaluate coverage.  Recomputing required symbols, distinct
  counts or the statistics gate would create a second coverage authority, and two
  authorities is how "covered" starts meaning two different things;
* it does not verify signatures.  Signature verification belongs to the
  authentication authority (6-B1), which produced the durable verdict this reads.
  Re-verifying here would be a second authentication authority;
* it does not write anything.  It is a pure read over the stores, so it cannot
  change a lifecycle decision or a coverage record.

Everything it reads is addressed by **exact identity**, never by a "latest"
lookup: the item names its own ``authentication_id`` and ``gate_evaluation_id``,
and a validator that resolved those to the newest record for the version would be
answering a different question -- "is there some passing evidence" -- while
appearing to answer this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from us_quant.trading.domain.evidence_auth import (
    EvidenceAuthenticationVerdict,
    EvidenceKeyTrustStatus,
)
from us_quant.trading.domain.strategy_coverage import (
    StrategyCoverageEvaluation,
    StrategyCoverageItem,
    StrategyCoverageVerdict,
)
from us_quant.trading.domain.strategy_gate import StrategyGateVerdict
from us_quant.trading.ports.evidence_authentication_repository import (
    EvidenceAuthenticationRepositoryError,
)
from us_quant.trading.ports.evidence_verification import (
    EvidenceTrustRootUnavailable,
)
from us_quant.trading.ports.strategy_gate_repository import (
    StrategyGateRepositoryError,
)


class StrategyCoverageValidityBlocker(StrEnum):
    """Every reason a formed coverage claim can stop being current.

    Unknown always fails closed, and the granularity is deliberate: an operator
    looking at a refused promotion needs to know whether a record vanished, a
    key was revoked, an identity drifted, or the evidence aged out, because those
    are four different repairs.
    """

    COVERAGE_NOT_PASSED = "COVERAGE_NOT_PASSED"

    AUTHENTICATION_RECORD_MISSING = "AUTHENTICATION_RECORD_MISSING"
    AUTHENTICATION_NOT_PASSED = "AUTHENTICATION_NOT_PASSED"

    GATE_RECORD_MISSING = "GATE_RECORD_MISSING"
    GATE_NOT_PASSED = "GATE_NOT_PASSED"

    MEMBER_IDENTITY_MISMATCH = "MEMBER_IDENTITY_MISMATCH"
    DUPLICATE_MEMBER_IDENTITY = "DUPLICATE_MEMBER_IDENTITY"

    UNKNOWN_SIGNING_KEY = "UNKNOWN_SIGNING_KEY"
    REVOKED_SIGNING_KEY = "REVOKED_SIGNING_KEY"
    TRUST_ROOT_UNAVAILABLE = "TRUST_ROOT_UNAVAILABLE"

    GATE_POLICY_MISMATCH = "GATE_POLICY_MISMATCH"

    STALE_MEMBER = "STALE_MEMBER"
    FUTURE_MEMBER_TIMESTAMP = "FUTURE_MEMBER_TIMESTAMP"


class StrategyCoverageValidityVerdict(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class StrategyCoverageValidityResult:
    """The verdict, and every reason that produced it.

    ``blockers`` is a tuple rather than a set so the result is deterministic to
    compare and to log; it is sorted and de-duplicated at construction so two
    runs over the same stores produce byte-identical output.
    """

    verdict: StrategyCoverageValidityVerdict
    blockers: tuple[StrategyCoverageValidityBlocker, ...]

    def __post_init__(self) -> None:
        # The verdict and its reasons have to agree.  Without this a caller could
        # hold a result that says VALID while carrying a blocker, and the verdict
        # is the first thing anybody reads.
        if self.verdict is StrategyCoverageValidityVerdict.VALID:
            if self.blockers:
                raise ValueError(
                    "a VALID coverage validity result cannot carry blockers"
                )
        elif not self.blockers:
            raise ValueError(
                "an INVALID coverage validity result must name at least one blocker"
            )

    @property
    def valid(self) -> bool:
        return self.verdict is StrategyCoverageValidityVerdict.VALID


class StrategyCoverageCurrentValidator:
    """Re-checks the exact evidence set one coverage claim recorded.

    Holds the three reads that a current-validity check needs and nothing else,
    so both the lifecycle authority and the Paper launch boundary can share one
    instance and therefore one answer.  Two instances built from different stores
    or different trust roots would be two answers to one question.
    """

    def __init__(
        self,
        *,
        authentications,
        gates,
        key_source,
    ) -> None:
        if authentications is None:
            raise TypeError("authentications is required")
        if gates is None:
            raise TypeError("gates is required")
        if key_source is None:
            raise TypeError("key_source is required")
        self._authentications = authentications
        self._gates = gates
        self._key_source = key_source

    def validate(
        self,
        coverage: StrategyCoverageEvaluation | None,
        *,
        now: datetime,
        required_gate_policy_version: str,
        maximum_evidence_age: timedelta | None = None,
    ) -> StrategyCoverageValidityResult:
        """Whether every member of ``coverage`` is still current.

        ``now`` must be timezone-aware; a naive instant cannot be compared with a
        stored one, and guessing a zone here would be inventing the comparison
        this method exists to make.
        """

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        # Required, not optional.  The policy revision is what says which gate
        # policy the claim was formed under, and an optional argument would let a
        # future caller skip the check by omission -- the one shape of fail-open
        # this repair exists to remove.
        if (
            not isinstance(required_gate_policy_version, str)
            or not required_gate_policy_version.strip()
        ):
            raise ValueError(
                "required_gate_policy_version must be a non-blank string"
            )
        if coverage is None:
            return _invalid(
                {StrategyCoverageValidityBlocker.COVERAGE_NOT_PASSED}
            )

        blockers: set[StrategyCoverageValidityBlocker] = set()
        if coverage.verdict is not StrategyCoverageVerdict.PASS:
            blockers.add(StrategyCoverageValidityBlocker.COVERAGE_NOT_PASSED)

        # A coverage claim that recorded no member cannot be current: it names no
        # evidence to re-check, and treating an empty set as vacuously valid is
        # the fail-open reading of the same facts.
        if not coverage.items:
            blockers.add(StrategyCoverageValidityBlocker.COVERAGE_NOT_PASSED)

        seen: set[tuple[str, str]] = set()
        for item in coverage.items:
            if item.identity in seen:
                blockers.add(
                    StrategyCoverageValidityBlocker.DUPLICATE_MEMBER_IDENTITY
                )
            seen.add(item.identity)
            blockers |= self._member_blockers(
                item,
                coverage=coverage,
                now=now,
                required_gate_policy_version=required_gate_policy_version,
                maximum_evidence_age=maximum_evidence_age,
            )

        return _verdict(blockers)

    # -- one member ------------------------------------------------------

    def _member_blockers(
        self,
        item: StrategyCoverageItem,
        *,
        coverage: StrategyCoverageEvaluation,
        now: datetime,
        required_gate_policy_version: str,
        maximum_evidence_age: timedelta | None,
    ) -> set[StrategyCoverageValidityBlocker]:
        blockers: set[StrategyCoverageValidityBlocker] = set()

        blockers |= self._authentication_blockers(item, coverage=coverage)
        blockers |= self._gate_blockers(
            item,
            coverage=coverage,
            required_gate_policy_version=required_gate_policy_version,
        )
        blockers |= _freshness_blockers(
            item, now=now, maximum_evidence_age=maximum_evidence_age
        )
        return blockers

    def _authentication_blockers(
        self,
        item: StrategyCoverageItem,
        *,
        coverage: StrategyCoverageEvaluation,
    ) -> set[StrategyCoverageValidityBlocker]:
        blockers: set[StrategyCoverageValidityBlocker] = set()
        try:
            record = self._authentications.get(item.authentication_id)
        except EvidenceAuthenticationRepositoryError:
            # Only the port's own failures.  A bare ``except Exception`` would also
            # swallow an AttributeError from a programming bug and report it as a
            # missing record -- safe, but diagnosed as the wrong problem, which is
            # how a real defect survives a green suite.
            return {StrategyCoverageValidityBlocker.AUTHENTICATION_RECORD_MISSING}
        if record is None:
            return {StrategyCoverageValidityBlocker.AUTHENTICATION_RECORD_MISSING}

        if record.verdict is not EvidenceAuthenticationVerdict.PASS:
            blockers.add(StrategyCoverageValidityBlocker.AUTHENTICATION_NOT_PASSED)

        # Exact identity, and every field compared as a whole value.  ``None`` on
        # the record where the item names a value is a mismatch and not an
        # "unknown": these fields are the identity the lifecycle decision rests
        # on, and a record that cannot prove them cannot be interpreted as
        # possibly-matching.  There is deliberately no grandfathering -- evidence
        # that predates these columns has to be re-established through the
        # current pipeline before it may authorise anything.
        if (
            record.authentication_id != item.authentication_id
            or record.strategy_version_id != coverage.strategy_version_id
            or record.review_run_id != item.review_run_id
            or record.key_id != item.key_id
        ):
            blockers.add(StrategyCoverageValidityBlocker.MEMBER_IDENTITY_MISMATCH)

        blockers |= self._key_blockers(item.key_id)
        return blockers

    def _key_blockers(self, key_id: str) -> set[StrategyCoverageValidityBlocker]:
        try:
            key = self._key_source.verification_key(key_id)
        except EvidenceTrustRootUnavailable:
            return {StrategyCoverageValidityBlocker.TRUST_ROOT_UNAVAILABLE}
        if key is None:
            return {StrategyCoverageValidityBlocker.UNKNOWN_SIGNING_KEY}
        if key.trust_status is EvidenceKeyTrustStatus.REVOKED:
            return {StrategyCoverageValidityBlocker.REVOKED_SIGNING_KEY}
        return set()

    def _gate_blockers(
        self,
        item: StrategyCoverageItem,
        *,
        coverage: StrategyCoverageEvaluation,
        required_gate_policy_version: str,
    ) -> set[StrategyCoverageValidityBlocker]:
        blockers: set[StrategyCoverageValidityBlocker] = set()
        try:
            gate = self._gates.get(item.gate_evaluation_id)
        except StrategyGateRepositoryError:
            return {StrategyCoverageValidityBlocker.GATE_RECORD_MISSING}
        if gate is None:
            return {StrategyCoverageValidityBlocker.GATE_RECORD_MISSING}

        if gate.verdict is not StrategyGateVerdict.PASS:
            blockers.add(StrategyCoverageValidityBlocker.GATE_NOT_PASSED)

        if (
            gate.evaluation_id != item.gate_evaluation_id
            or gate.strategy_version_id != coverage.strategy_version_id
            or gate.review_run_id != item.review_run_id
            or gate.parameter_hash != coverage.parameter_hash
            or gate.data_hash != item.data_hash
            or gate.symbol != item.symbol
        ):
            blockers.add(StrategyCoverageValidityBlocker.MEMBER_IDENTITY_MISMATCH)

        if gate.policy_version != required_gate_policy_version:
            blockers.add(StrategyCoverageValidityBlocker.GATE_POLICY_MISMATCH)
        return blockers


def _freshness_blockers(
    item: StrategyCoverageItem,
    *,
    now: datetime,
    maximum_evidence_age: timedelta | None,
) -> set[StrategyCoverageValidityBlocker]:
    """Freshness, judged per member rather than on one representative.

    A coverage claim is as fresh as its *oldest* member, and checking one
    representative would let a stale member ride along behind a fresh one -- which
    is exactly the shape of hole this whole repair is about.
    """

    blockers: set[StrategyCoverageValidityBlocker] = set()
    moment = now.astimezone(timezone.utc)
    generated_at = item.generated_at.astimezone(timezone.utc)
    if generated_at > moment:
        blockers.add(StrategyCoverageValidityBlocker.FUTURE_MEMBER_TIMESTAMP)
    elif (
        maximum_evidence_age is not None
        and moment - generated_at > maximum_evidence_age
    ):
        blockers.add(StrategyCoverageValidityBlocker.STALE_MEMBER)
    return blockers


def _invalid(
    blockers: set[StrategyCoverageValidityBlocker],
) -> StrategyCoverageValidityResult:
    return _verdict(blockers or {StrategyCoverageValidityBlocker.COVERAGE_NOT_PASSED})


def _verdict(
    blockers: set[StrategyCoverageValidityBlocker],
) -> StrategyCoverageValidityResult:
    ordered = tuple(sorted(blockers, key=lambda row: row.value))
    return StrategyCoverageValidityResult(
        verdict=(
            StrategyCoverageValidityVerdict.VALID
            if not ordered
            else StrategyCoverageValidityVerdict.INVALID
        ),
        blockers=ordered,
    )


__all__ = [
    "StrategyCoverageCurrentValidator",
    "StrategyCoverageValidityBlocker",
    "StrategyCoverageValidityResult",
    "StrategyCoverageValidityVerdict",
]
