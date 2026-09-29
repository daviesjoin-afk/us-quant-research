"""Stage 6-B2: bounded coverage of a strategy universe.

The property under test throughout: one symbol's PASS is never the strategy's
PASS.  Every case below either covers the *whole* required universe with
admissible evidence or fails closed.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from us_quant.research_evidence_sealing import seal_review_artifact, write_signing_key
from us_quant.targeted_review import (
    DependenceDiagnostic,
    EvidenceGate,
    TargetedReviewResult,
    TARGETED_REVIEW_GATE_DEFINITIONS,
    save_targeted_review,
)
from us_quant.trading.adapters.evidence_signature import (
    Ed25519EvidenceSignatureVerifier,
)
from us_quant.trading.adapters.evidence_trust_store import (
    FileEvidenceVerificationKeySource,
)
from us_quant.trading.adapters.research_evidence import TargetedReviewArtifactSource
from us_quant.trading.application.evidence_authentication import (
    EvidenceAuthenticationApplication,
)
from us_quant.trading.application.strategy_coverage import StrategyCoverageEvaluator
from us_quant.trading.application.strategy_gate import StrategyGateEvaluator
from us_quant.trading.domain.evidence_auth import (
    ED25519_ALGORITHM,
    EvidenceAuthenticationPolicy,
    EvidenceKeyTrustStatus,
    EvidenceVerificationKey,
    default_seal_path,
    trust_store_to_payload,
)
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
)
from us_quant.trading.domain.strategy_coverage import (
    StrategyCoverageBlocker as Blocker,
    StrategyCoverageEvidence,
    StrategyCoveragePolicy,
    StrategyCoverageVerdict as Verdict,
)
from us_quant.trading.domain.strategy_gate import (
    StrategyGateBlocker,
    StrategyGatePolicy,
    StrategyGateVerdict,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
KEY_ID = "research-key-1"
UNIVERSE_HASH = "universe-1"
CODE_HASH = "code-1"
VERSION_ID = "version-1"
SEMVER = "1.0.0"
PARAMETER_HASH = "parameter-1"


def _review(*, run_id, symbol, data_hash, strategy_version_id=VERSION_ID):
    gates = tuple(
        EvidenceGate(code, name, True, "yes", required, evidence)
        for code, name, required, evidence in TARGETED_REVIEW_GATE_DEFINITIONS
    )
    return TargetedReviewResult(
        run_id=run_id, robustness_run_id=f"{run_id}-robust",
        validation_run_id=f"{run_id}-validation", overfit_run_id=f"{run_id}-overfit",
        data_quality_run_id=f"{run_id}-quality",
        execution_stress_run_id=f"{run_id}-stress", symbol=symbol,
        strategy_version_id=strategy_version_id, strategy_semver=SEMVER,
        base_parameter_hash=PARAMETER_HASH, data_hash=data_hash,
        provider="provider-1", evidence_origins=("captured_stream",),
        dependence=DependenceDiagnostic(12, None, None, 0, None, None, None, "pass"),
        gates=gates, passed_gates=len(gates), blocking_failures=0, warnings=(),
        decision="ELIGIBLE_FOR_INDEPENDENT_REVIEW",
        eligible_for_independent_review=True, status="PASS",
    )


def _version(**changes):
    values = dict(
        definition=StrategyDefinition("family-1", "Example", "test"),
        identity=StrategyIdentity("family-1", VERSION_ID, PARAMETER_HASH),
        semver=SEMVER, status=StrategyStatus.RESEARCH, mode=StrategyMode.RESEARCH,
        parameters={"period": 5}, universe_hash=UNIVERSE_HASH, code_hash=CODE_HASH,
        risk_budget_pct=Decimal("0.01"), gate_passed=False, gate_reason="legacy",
        created_at=NOW, updated_at=NOW,
    )
    values.update(changes)
    return StrategyVersion(**values)


def _write_trust_store(path, public_key, *, key_id=KEY_ID, status=EvidenceKeyTrustStatus.ACTIVE):
    Path(path).write_text(
        json.dumps(
            trust_store_to_payload(
                (
                    EvidenceVerificationKey(
                        key_id=key_id, algorithm=ED25519_ALGORITHM,
                        public_key=public_key, trust_status=status,
                    ),
                )
            )
        ),
        encoding="utf-8",
    )


class _Fixture:
    """A real sealing key, a real artifact store and the real B1 verifier."""

    def __init__(self, tmp_path):
        self.artifacts = tmp_path / "artifacts"
        self.keys = tmp_path / "keys"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.keys.mkdir(parents=True, exist_ok=True)
        self.private_key = self.keys / "research.key"
        self.public_key = write_signing_key(self.private_key)
        self.trust_store = self.keys / "trust.json"
        _write_trust_store(self.trust_store, self.public_key)
        self.artifact_source = TargetedReviewArtifactSource()
        self.authenticator = EvidenceAuthenticationApplication(
            artifact_source=self.artifact_source,
            key_source=FileEvidenceVerificationKeySource(self.trust_store),
            signature_verifier=Ed25519EvidenceSignatureVerifier(),
        )
        self.gate_evaluator = StrategyGateEvaluator()

    def set_trust_status(self, status):
        _write_trust_store(self.trust_store, self.public_key, status=status)

    def evidence(
        self,
        *,
        run_id,
        symbol,
        data_hash="data-1",
        version=None,
        generated_at=NOW,
        signed_at=NOW,
        sign=True,
    ):
        """Seal, authenticate and gate-evaluate one artifact end to end."""

        version = version if version is not None else _version()
        artifact_path = save_targeted_review(
            _review(run_id=run_id, symbol=symbol, data_hash=data_hash),
            self.artifacts,
        )
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        payload["generated_at"] = generated_at.isoformat()
        artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        if sign:
            seal_review_artifact(
                artifact_path=artifact_path,
                private_key_path=self.private_key,
                trust_store_path=self.trust_store,
                key_id=KEY_ID,
                universe_hash=UNIVERSE_HASH,
                code_hash=CODE_HASH,
                seal_path=default_seal_path(artifact_path),
                signed_at=signed_at,
            )

        loaded = self.artifact_source.load(artifact_path)
        outcome = self.authenticator.authenticate(
            version=version,
            artifact_path=artifact_path,
            policy=EvidenceAuthenticationPolicy(),
            verified_at=NOW,
        )
        assert outcome.authenticated_evidence is not None, outcome.result.blockers
        gate = self.gate_evaluator.evaluate(
            version=version,
            evidence=loaded.evidence,
            policy=StrategyGatePolicy(),
            evaluated_at=NOW,
        )
        return StrategyCoverageEvidence(
            authenticated=outcome.authenticated_evidence, gate=gate
        )

    def cover(self, evidence, *, policy, version=None, evaluated_at=NOW):
        evaluator = StrategyCoverageEvaluator(
            key_source=FileEvidenceVerificationKeySource(self.trust_store)
        )
        return evaluator.evaluate(
            version=version if version is not None else _version(),
            policy=policy,
            evidence=tuple(evidence),
            evaluated_at=evaluated_at,
        )


def _policy(**changes):
    values = dict(
        policy_id="coverage-policy-1", revision=1,
        policy_version="evidence-coverage-v1",
        required_symbols=("AAPL", "MSFT"),
        required_universe_hash=UNIVERSE_HASH, required_code_hash=CODE_HASH,
        min_distinct_review_runs=2, min_distinct_data_hashes=2,
        maximum_evidence_age=timedelta(days=30), created_at=NOW,
    )
    values.update(changes)
    return StrategyCoveragePolicy(**values)


def _full_universe(fixture, **kwargs):
    return [
        fixture.evidence(run_id="review-1", symbol="AAPL", data_hash="data-1", **kwargs),
        fixture.evidence(run_id="review-2", symbol="MSFT", data_hash="data-2", **kwargs),
    ]


# -- the happy path ------------------------------------------------------


def test_two_symbol_universe_with_distinct_evidence_passes(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = _full_universe(fixture)

    evaluation = fixture.cover(evidence, policy=_policy())

    assert evaluation.verdict is Verdict.PASS
    assert evaluation.blockers == ()
    assert evaluation.covered_symbols == ("AAPL", "MSFT")
    assert evaluation.required_symbols == ("AAPL", "MSFT")
    assert evaluation.distinct_review_runs == 2
    assert evaluation.distinct_data_hashes == 2
    assert evaluation.policy_identity == ("coverage-policy-1", 1)


def test_evaluation_records_the_exact_evidence_it_counted(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = _full_universe(fixture)

    evaluation = fixture.cover(evidence, policy=_policy())

    expected_auth = tuple(sorted(item.authenticated.authentication.authentication_id for item in evidence))
    expected_gate = tuple(sorted(item.gate.evaluation_id for item in evidence))
    assert evaluation.authentication_ids == expected_auth
    assert evaluation.gate_evaluation_ids == expected_gate
    assert {item.review_run_id for item in evaluation.items} == {"review-1", "review-2"}


# -- the core refusal ----------------------------------------------------


def test_single_symbol_pass_never_covers_the_whole_universe(tmp_path):
    """The whole point of coverage: one symbol's PASS is not the strategy's."""

    fixture = _Fixture(tmp_path)
    evidence = [fixture.evidence(run_id="review-1", symbol="AAPL", data_hash="data-1")]

    evaluation = fixture.cover(evidence, policy=_policy())

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.REQUIRED_SYMBOLS_UNCOVERED in evaluation.blockers
    assert evaluation.covered_symbols == ("AAPL",)


def test_no_evidence_fails_closed(tmp_path):
    fixture = _Fixture(tmp_path)

    evaluation = fixture.cover([], policy=_policy())

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.EVIDENCE_MISSING in evaluation.blockers


def test_missing_policy_fails_closed(tmp_path):
    """No policy authorises nothing; there is no threshold hidden in code."""

    fixture = _Fixture(tmp_path)
    evaluation = fixture.cover(_full_universe(fixture), policy=None)

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.POLICY_MISSING in evaluation.blockers
    assert evaluation.policy_id is None
    assert evaluation.items == ()


def test_unsupported_policy_version_fails_closed(tmp_path):
    fixture = _Fixture(tmp_path)
    evaluation = fixture.cover(
        _full_universe(fixture), policy=_policy(policy_version="evidence-coverage-v99")
    )

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.POLICY_VERSION_UNSUPPORTED in evaluation.blockers


# -- policy identity -----------------------------------------------------


def test_policy_universe_hash_mismatch_fails_closed(tmp_path):
    fixture = _Fixture(tmp_path)
    evaluation = fixture.cover(
        _full_universe(fixture), policy=_policy(required_universe_hash="universe-other")
    )

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.UNIVERSE_HASH_MISMATCH in evaluation.blockers


def test_policy_code_hash_mismatch_fails_closed(tmp_path):
    fixture = _Fixture(tmp_path)
    evaluation = fixture.cover(
        _full_universe(fixture), policy=_policy(required_code_hash="code-other")
    )

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.CODE_HASH_MISMATCH in evaluation.blockers


def test_version_identity_mismatch_fails_closed(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = _full_universe(fixture)
    other = _version(
        identity=StrategyIdentity("family-1", "version-2", PARAMETER_HASH)
    )

    evaluation = fixture.cover(evidence, policy=_policy(), version=other)

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.VERSION_IDENTITY_MISMATCH in evaluation.blockers


def test_version_semver_mismatch_fails_closed(tmp_path):
    fixture = _Fixture(tmp_path)
    evaluation = fixture.cover(
        _full_universe(fixture), policy=_policy(), version=_version(semver="2.0.0")
    )

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.STRATEGY_SEMVER_MISMATCH in evaluation.blockers


def test_version_parameter_hash_mismatch_fails_closed(tmp_path):
    fixture = _Fixture(tmp_path)
    other = _version(
        identity=StrategyIdentity("family-1", VERSION_ID, "parameter-2")
    )

    evaluation = fixture.cover(_full_universe(fixture), policy=_policy(), version=other)

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.PARAMETER_HASH_MISMATCH in evaluation.blockers


# -- admissibility of one item ------------------------------------------


def test_gate_failure_excludes_that_evidence(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = _full_universe(fixture)
    failed = replace(
        evidence[1],
        gate=replace(
            evidence[1].gate,
            verdict=StrategyGateVerdict.FAIL,
            blockers=(StrategyGateBlocker.STALE_EVIDENCE,),
        ),
    )

    evaluation = fixture.cover([evidence[0], failed], policy=_policy())

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.GATE_NOT_PASSED in evaluation.blockers
    assert evaluation.covered_symbols == ("AAPL",)


def test_gate_for_a_different_review_excludes_that_evidence(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = _full_universe(fixture)
    crossed = replace(
        evidence[1], gate=replace(evidence[1].gate, review_run_id="review-elsewhere")
    )

    evaluation = fixture.cover([evidence[0], crossed], policy=_policy())

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.GATE_IDENTITY_MISMATCH in evaluation.blockers


def test_evidence_for_an_unrequired_symbol_cannot_cover_a_required_one(tmp_path):
    """Out-of-scope evidence is not a failure, but it is not credit either."""

    fixture = _Fixture(tmp_path)
    evidence = [
        fixture.evidence(run_id="review-1", symbol="AAPL", data_hash="data-1"),
        fixture.evidence(run_id="review-2", symbol="TSLA", data_hash="data-2"),
    ]

    evaluation = fixture.cover(evidence, policy=_policy())

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.REQUIRED_SYMBOLS_UNCOVERED in evaluation.blockers
    assert evaluation.covered_symbols == ("AAPL",)


def test_non_coverage_evidence_entries_fail_closed(tmp_path):
    fixture = _Fixture(tmp_path)

    evaluation = fixture.cover(
        [fixture.evidence(run_id="review-1", symbol="AAPL"), "not-evidence"],
        policy=_policy(),
    )

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.EVIDENCE_NOT_AUTHENTICATED in evaluation.blockers


def test_duplicate_review_identity_fails_closed(tmp_path):
    """The same evidence offered twice must not count twice."""

    fixture = _Fixture(tmp_path)
    first = fixture.evidence(run_id="review-1", symbol="AAPL", data_hash="data-1")
    second = fixture.evidence(run_id="review-2", symbol="MSFT", data_hash="data-2")

    evaluation = fixture.cover([first, second, second], policy=_policy())

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.DUPLICATE_EVIDENCE_IDENTITY in evaluation.blockers
    assert evaluation.distinct_review_runs == 2


def test_stale_evidence_fails_closed(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = [
        fixture.evidence(run_id="review-1", symbol="AAPL", data_hash="data-1"),
        fixture.evidence(
            run_id="review-2", symbol="MSFT", data_hash="data-2",
            generated_at=NOW - timedelta(days=90),
        ),
    ]

    evaluation = fixture.cover(evidence, policy=_policy(), evaluated_at=NOW)

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.STALE_EVIDENCE in evaluation.blockers


def test_evidence_within_the_age_bound_is_accepted(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = [
        fixture.evidence(run_id="review-1", symbol="AAPL", data_hash="data-1"),
        fixture.evidence(
            run_id="review-2", symbol="MSFT", data_hash="data-2",
            generated_at=NOW - timedelta(days=1),
        ),
    ]

    assert fixture.cover(evidence, policy=_policy()).verdict is Verdict.PASS


def test_no_age_bound_accepts_old_evidence(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = [
        fixture.evidence(run_id="review-1", symbol="AAPL", data_hash="data-1"),
        fixture.evidence(
            run_id="review-2", symbol="MSFT", data_hash="data-2",
            generated_at=NOW - timedelta(days=900),
        ),
    ]

    assert fixture.cover(
        evidence, policy=_policy(maximum_evidence_age=None)
    ).verdict is Verdict.PASS


# -- revocation re-checked at coverage time -----------------------------


def test_key_revoked_after_authentication_fails_coverage(tmp_path):
    """Revocation must not be a one-time check at seal-verification time."""

    fixture = _Fixture(tmp_path)
    evidence = _full_universe(fixture)
    fixture.set_trust_status(EvidenceKeyTrustStatus.REVOKED)

    evaluation = fixture.cover(evidence, policy=_policy())

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.REVOKED_AUTHENTICATION in evaluation.blockers
    assert evaluation.items == ()


def test_unknown_key_fails_coverage(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = _full_universe(fixture)
    _write_trust_store(
        fixture.trust_store, fixture.public_key, key_id="some-other-key"
    )

    evaluation = fixture.cover(evidence, policy=_policy())

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.UNKNOWN_AUTHENTICATION_KEY in evaluation.blockers


def test_missing_trust_root_fails_coverage(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = _full_universe(fixture)
    fixture.trust_store = fixture.keys / "absent.json"

    evaluation = fixture.cover(evidence, policy=_policy())

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.TRUST_ROOT_UNAVAILABLE in evaluation.blockers


def test_verify_only_key_still_covers_already_issued_evidence(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = _full_universe(fixture)
    fixture.set_trust_status(EvidenceKeyTrustStatus.VERIFY_ONLY)

    assert fixture.cover(evidence, policy=_policy()).verdict is Verdict.PASS


# -- distinct-evidence minimums -----------------------------------------


def test_insufficient_distinct_review_runs_fails_closed(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = _full_universe(fixture)

    evaluation = fixture.cover(
        evidence, policy=_policy(min_distinct_review_runs=3)
    )

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.INSUFFICIENT_DISTINCT_REVIEW_RUNS in evaluation.blockers


def test_insufficient_distinct_data_hashes_fails_closed(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = [
        fixture.evidence(run_id="review-1", symbol="AAPL", data_hash="data-1"),
        fixture.evidence(run_id="review-2", symbol="MSFT", data_hash="data-1"),
    ]

    evaluation = fixture.cover(
        evidence, policy=_policy(min_distinct_data_hashes=2)
    )

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.INSUFFICIENT_DISTINCT_DATA_HASHES in evaluation.blockers


def test_single_review_of_one_symbol_cannot_satisfy_a_two_run_policy(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = [fixture.evidence(run_id="review-1", symbol="AAPL", data_hash="data-1")]

    evaluation = fixture.cover(evidence, policy=_policy())

    assert evaluation.verdict is Verdict.FAIL
    assert Blocker.REQUIRED_SYMBOLS_UNCOVERED in evaluation.blockers
    assert Blocker.INSUFFICIENT_DISTINCT_REVIEW_RUNS in evaluation.blockers


# -- identity, idempotency, lifecycle neutrality ------------------------


def test_reevaluation_is_idempotent_across_clocks(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = _full_universe(fixture)

    first = fixture.cover(evidence, policy=_policy(), evaluated_at=NOW)
    second = fixture.cover(
        evidence, policy=_policy(), evaluated_at=NOW + timedelta(hours=5)
    )

    assert first.evaluation_id == second.evaluation_id
    assert first.verdict is second.verdict is Verdict.PASS


def test_a_changed_evidence_set_produces_a_new_identity(tmp_path):
    """Same verdict, different admitted evidence: not the same claim."""

    fixture = _Fixture(tmp_path)
    first = [fixture.evidence(run_id="review-1", symbol="AAPL", data_hash="data-1")]
    second = [fixture.evidence(run_id="review-2", symbol="MSFT", data_hash="data-2")]

    one = fixture.cover(first, policy=_policy())
    two = fixture.cover(second, policy=_policy())

    assert one.verdict is two.verdict is Verdict.FAIL
    assert one.evaluation_id != two.evaluation_id


def test_failed_coverage_is_still_durably_identifiable(tmp_path):
    fixture = _Fixture(tmp_path)
    evidence = [fixture.evidence(run_id="review-1", symbol="AAPL", data_hash="data-1")]

    evaluation = fixture.cover(evidence, policy=_policy())

    assert evaluation.verdict is Verdict.FAIL
    assert evaluation.evaluation_id.startswith("sce-")
    assert evaluation.policy_identity == ("coverage-policy-1", 1)


def test_coverage_does_not_change_strategy_lifecycle_fields(tmp_path):
    fixture = _Fixture(tmp_path)
    version = _version()
    before = (version.status, version.mode, version.gate_passed, version.gate_reason)

    evaluation = fixture.cover(
        _full_universe(fixture), policy=_policy(), version=version
    )

    assert evaluation.verdict is Verdict.PASS
    assert (version.status, version.mode, version.gate_passed, version.gate_reason) == before
    assert version.status is StrategyStatus.RESEARCH


def test_coverage_failure_does_not_pause_anything(tmp_path):
    fixture = _Fixture(tmp_path)
    version = _version()

    evaluation = fixture.cover([], policy=_policy(), version=version)

    assert evaluation.verdict is Verdict.FAIL
    assert version.status is StrategyStatus.RESEARCH
    assert version.mode is StrategyMode.RESEARCH
