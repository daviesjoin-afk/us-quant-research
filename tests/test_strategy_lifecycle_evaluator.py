"""Stage 6-C: the lifecycle authority's decision matrix.

The controller is the only thing that can turn evidence into a state change, so
every case here either assembles the whole chain or refuses with a named
blocker.  The two directions are deliberately asymmetric: a promotion needs a
full PASS, while a pause needs a named *failure*, so neither can be taken
without evidence pointing the right way.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import sqlite3
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
from us_quant.trading.adapters.sqlite.evidence_authentication_repository import (
    SQLiteEvidenceAuthenticationRepository,
)
from us_quant.trading.adapters.sqlite.strategy_gate_repository import (
    SQLiteStrategyGateRepository,
)
from us_quant.trading.application.strategy_coverage_validity import (
    StrategyCoverageCurrentValidator,
)
from us_quant.trading.adapters.research_evidence import TargetedReviewArtifactSource
from us_quant.trading.application.evidence_authentication import (
    EvidenceAuthenticationApplication,
)
from us_quant.trading.application.strategy_coverage import StrategyCoverageEvaluator
from us_quant.trading.application.strategy_gate import StrategyGateEvaluator
from us_quant.trading.application.strategy_lifecycle import (
    StrategyLifecycleController,
    StrategyLifecycleDecisionResult,
)
from us_quant.trading.domain import strategy_lifecycle as _lifecycle
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
    StrategyCoverageBlocker,
    StrategyCoverageEvidence,
    StrategyCoveragePolicy,
    StrategyCoverageVerdict,
)
from us_quant.trading.domain.strategy_gate import (
    StrategyGateBlocker,
    StrategyGatePolicy,
    StrategyGateVerdict,
)
from us_quant.trading.domain.strategy_lifecycle import (
    StrategyLifecycleAction,
    StrategyLifecycleAuthorization,
    StrategyLifecycleBlocker as Blocker,
    StrategyLifecycleDecisionState,
    StrategyLifecyclePolicy,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
KEY_ID = "research-key-1"
UNIVERSE_HASH = "universe-1"
CODE_HASH = "code-1"
VERSION_ID = "version-1"
SEMVER = "1.0.0"
PARAMETER_HASH = "parameter-1"


def _review(*, run_id, symbol, data_hash):
    gates = tuple(
        EvidenceGate(code, name, True, "yes", required, evidence)
        for code, name, required, evidence in TARGETED_REVIEW_GATE_DEFINITIONS
    )
    return TargetedReviewResult(
        run_id=run_id, robustness_run_id=f"{run_id}-robust",
        validation_run_id=f"{run_id}-validation", overfit_run_id=f"{run_id}-overfit",
        data_quality_run_id=f"{run_id}-quality",
        execution_stress_run_id=f"{run_id}-stress", symbol=symbol,
        strategy_version_id=VERSION_ID, strategy_semver=SEMVER,
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


def _policy(**changes):
    values = dict(
        policy_id="lifecycle-policy-1", revision=1,
        policy_version="strategy-lifecycle-v1",
        permitted_actions=(
            StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
            StrategyLifecycleAction.PAUSE,
            StrategyLifecycleAction.RESUME_PAPER_SHADOW,
        ),
        required_gate_policy_version="independent-review-v1",
        required_coverage_policy_version="evidence-coverage-v1",
        maximum_evidence_age=timedelta(days=30), created_at=NOW,
    )
    values.update(changes)
    return StrategyLifecyclePolicy(**values)


class _Chain:
    """A real sealed artifact, authenticated, gate-evaluated and covered."""

    def __init__(self, tmp_path):
        self.artifacts = tmp_path / "artifacts"
        self.keys = tmp_path / "keys"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.keys.mkdir(parents=True, exist_ok=True)
        self.private_key = self.keys / "research.key"
        self.public_key = write_signing_key(self.private_key)
        self.trust_store = self.keys / "trust.json"
        self.set_trust_status(EvidenceKeyTrustStatus.ACTIVE)
        self.artifact_source = TargetedReviewArtifactSource()
        self.authenticator = EvidenceAuthenticationApplication(
            artifact_source=self.artifact_source,
            key_source=FileEvidenceVerificationKeySource(self.trust_store),
            signature_verifier=Ed25519EvidenceSignatureVerifier(),
        )
        self.gate_evaluator = StrategyGateEvaluator()
        self.coverage_evaluator = StrategyCoverageEvaluator(
            key_source=FileEvidenceVerificationKeySource(self.trust_store)
        )
        # The current-validity validator reads the *records*, by exact id, so the
        # chain has to store them.  Real SQLite stores rather than stubs: what is
        # being tested is that the records the coverage claim names can still be
        # found and still say what they said, and a stub would only prove the stub
        # agrees with itself.
        self.authentication_store = SQLiteEvidenceAuthenticationRepository(
            tmp_path / "authentications.sqlite3"
        )
        self.gate_store = SQLiteStrategyGateRepository(tmp_path / "gates.sqlite3")
        self._authenticated = None
        self._gate = None
        self._coverage = None

    def set_trust_status(self, status):
        Path(self.trust_store).write_text(
            json.dumps(
                trust_store_to_payload(
                    (
                        EvidenceVerificationKey(
                            key_id=KEY_ID, algorithm=ED25519_ALGORITHM,
                            public_key=self.public_key, trust_status=status,
                        ),
                    )
                )
            ),
            encoding="utf-8",
        )

    def build(self, *, version=None, generated_at=NOW, coverage_policy=None):
        version = version if version is not None else _version()
        artifact_path = save_targeted_review(
            _review(run_id="review-1", symbol="AAPL", data_hash="data-1"),
            self.artifacts,
        )
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        payload["generated_at"] = generated_at.isoformat()
        artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        seal_review_artifact(
            artifact_path=artifact_path,
            private_key_path=self.private_key,
            trust_store_path=self.trust_store,
            key_id=KEY_ID,
            universe_hash=UNIVERSE_HASH,
            code_hash=CODE_HASH,
            seal_path=default_seal_path(artifact_path),
            signed_at=NOW,
        )
        loaded = self.artifact_source.load(artifact_path)
        outcome = self.authenticator.authenticate(
            version=version, artifact_path=artifact_path,
            policy=EvidenceAuthenticationPolicy(), verified_at=NOW,
        )
        assert outcome.authenticated_evidence is not None
        self._authenticated = outcome.authenticated_evidence
        self._gate = self.gate_evaluator.evaluate(
            version=version, evidence=loaded.evidence,
            policy=StrategyGatePolicy(), evaluated_at=NOW,
        )
        # Recorded before the coverage claim is formed, so the claim names records
        # that actually exist -- which is the precondition the validator re-checks.
        self.authentication_store.record(self._authenticated.authentication)
        self.gate_store.record(self._gate)
        self._coverage = self.coverage_evaluator.evaluate(
            version=version,
            policy=coverage_policy if coverage_policy is not None else _coverage_policy(),
            evidence=(
                StrategyCoverageEvidence(
                    authenticated=self._authenticated, gate=self._gate
                ),
            ),
            evaluated_at=NOW,
        )
        return self

    def remove_authentication_record(self):
        """Delete the member record the claim names, as a store loss would."""

        connection = sqlite3.connect(self.authentication_store.path)
        try:
            connection.execute("DELETE FROM strategy_evidence_authentication")
            connection.commit()
        finally:
            connection.close()

    def corrupt_gate_verdict(self, verdict):
        """Change the stored gate row in place.

        Written through SQL rather than re-recorded: ``record`` refuses an id
        whose payload changed, which is the store's own immutability guard and a
        property worth keeping.  A store that *has* been tampered with is exactly
        what the validator has to notice.
        """

        connection = sqlite3.connect(self.gate_store.path)
        try:
            connection.execute(
                "UPDATE strategy_gate_evaluation SET verdict = ? "
                "WHERE evaluation_id = ?",
                (verdict.value, self._gate.evaluation_id),
            )
            connection.commit()
        finally:
            connection.close()

    def corrupt_gate_version(self, version_id):
        connection = sqlite3.connect(self.gate_store.path)
        try:
            connection.execute(
                "UPDATE strategy_gate_evaluation SET strategy_version_id = ? "
                "WHERE evaluation_id = ?",
                (version_id, self._gate.evaluation_id),
            )
            connection.commit()
        finally:
            connection.close()

    def remove_gate_record(self):
        connection = sqlite3.connect(self.gate_store.path)
        try:
            connection.execute("DELETE FROM strategy_gate_evaluation")
            connection.commit()
        finally:
            connection.close()

    @property
    def authenticated(self):
        return self._authenticated

    @property
    def gate(self):
        return self._gate

    @property
    def coverage(self):
        return self._coverage


def _coverage_policy(**changes):
    values = dict(
        policy_id="coverage-policy-1", revision=1,
        policy_version="evidence-coverage-v1", required_symbols=("AAPL",),
        required_universe_hash=UNIVERSE_HASH, required_code_hash=CODE_HASH,
        min_distinct_review_runs=1, min_distinct_data_hashes=1,
        maximum_evidence_age=timedelta(days=30), created_at=NOW,
    )
    values.update(changes)
    return StrategyCoveragePolicy(**values)


def _controller(chain):
    """The controller over the shared current-validity validator.

    The controller no longer takes a key source: it holds no representative
    record and therefore no way to read one key inline.  Current validity is one
    service, and the controller reaches evidence only through it.
    """

    return StrategyLifecycleController(
        coverage_validity=StrategyCoverageCurrentValidator(
            authentications=chain.authentication_store,
            gates=chain.gate_store,
            key_source=FileEvidenceVerificationKeySource(chain.trust_store),
        )
    )


#: Distinguishes "the caller did not override this" from "the caller passed
#: None", which the fail-closed tests depend on.
_UNSET = object()


def _decide(chain, *, action=StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
            version=None, policy=_UNSET, coverage=_UNSET, decided_at=NOW):
    """One decision over the whole chain.

    There is no ``authenticated`` or ``gate`` argument, and that absence is the
    repair: the evidence authority is the coverage claim, which names its complete
    member set.  A caller that could pass one representative member could pass one
    that is still valid while another has been revoked.
    """

    return _controller(chain).decide(
        version=version if version is not None else _version(),
        action=action,
        policy=_policy() if policy is _UNSET else policy,
        coverage=chain.coverage if coverage is _UNSET else coverage,
        decided_at=decided_at,
    )


# -- the happy path ------------------------------------------------------


def test_a_full_chain_authorises_promotion(tmp_path):
    chain = _Chain(tmp_path).build()

    result = _decide(chain)

    assert result.authorised is True
    assert result.decision.state is StrategyLifecycleDecisionState.PREPARED
    assert result.decision.blockers == ()
    assert result.decision.triggers == ()
    assert result.decision.policy_identity == ("lifecycle-policy-1", 1)
    # The evidence identity is the coverage claim alone.  The representative
    # columns are written NULL from here on: they identified one member of a set
    # the claim already names in full.
    assert result.decision.authentication_id is None
    assert result.decision.gate_evaluation_id is None
    assert result.decision.coverage_evaluation_id is not None
    assert result.authorization.target_status is StrategyStatus.PAPER_SHADOW


def test_resume_is_authorised_from_paused(tmp_path):
    chain = _Chain(tmp_path).build()
    version = _version(status=StrategyStatus.PAUSED, mode=StrategyMode.PAPER_SHADOW)

    result = _decide(
        chain, action=StrategyLifecycleAction.RESUME_PAPER_SHADOW, version=version
    )

    assert result.authorised is True
    assert result.authorization.target_status is StrategyStatus.PAPER_SHADOW


# -- policy authority ----------------------------------------------------


def test_no_policy_authorises_nothing(tmp_path):
    chain = _Chain(tmp_path).build()

    result = _decide(chain, policy=None)

    assert result.authorised is False
    assert result.authorization is None
    assert result.decision.state is StrategyLifecycleDecisionState.BLOCKED
    assert Blocker.POLICY_MISSING in result.decision.blockers


def test_unsupported_policy_version_fails_closed(tmp_path):
    chain = _Chain(tmp_path).build()

    result = _decide(chain, policy=_policy(policy_version="strategy-lifecycle-v99"))

    assert Blocker.POLICY_VERSION_UNSUPPORTED in result.decision.blockers


def test_an_action_the_policy_does_not_permit_is_refused(tmp_path):
    chain = _Chain(tmp_path).build()

    result = _decide(
        chain,
        policy=_policy(permitted_actions=(StrategyLifecycleAction.PAUSE,)),
    )

    assert Blocker.ACTION_NOT_PERMITTED in result.decision.blockers


def test_the_current_status_must_match_the_action(tmp_path):
    chain = _Chain(tmp_path).build()

    result = _decide(
        chain,
        action=StrategyLifecycleAction.PAUSE,
        version=_version(),  # RESEARCH, not PAPER_SHADOW
    )

    assert Blocker.CURRENT_STATUS_NOT_ELIGIBLE in result.decision.blockers


# -- the evidence chain --------------------------------------------------


def test_a_missing_claim_blocks_promotion(tmp_path):
    """The claim is the evidence link now, so it is the one that can be absent.

    There is no "authenticated missing" or "gate missing" case any more: a caller
    cannot pass a representative member at all, and a claim whose member records
    have gone missing is caught by the current-validity check instead.
    """

    chain = _Chain(tmp_path).build()

    result = _decide(chain, coverage=None)

    assert result.authorised is False
    assert Blocker.COVERAGE_MISSING in result.decision.blockers


def test_a_claim_whose_authentication_record_vanished_blocks(tmp_path):
    """The member-record case, at the store the validator reads."""

    chain = _Chain(tmp_path).build()
    chain.remove_authentication_record()

    result = _decide(chain)

    assert result.authorised is False
    assert Blocker.EVIDENCE_CHAIN_NOT_CURRENT in result.decision.blockers
    assert Blocker.AUTHENTICATION_MISSING in result.decision.blockers


def test_a_claim_whose_gate_record_vanished_blocks(tmp_path):
    chain = _Chain(tmp_path).build()
    chain.remove_gate_record()

    result = _decide(chain)

    assert result.authorised is False
    assert Blocker.EVIDENCE_CHAIN_NOT_CURRENT in result.decision.blockers
    assert Blocker.GATE_MISSING in result.decision.blockers


def test_a_failed_gate_blocks_promotion(tmp_path):
    """A gate row tampered with underneath the store is refused.

    ``corrupt_gate_verdict`` rewrites the indexed ``verdict`` column by SQL,
    leaving the stored payload disagreeing with it.  The store's own integrity
    check refuses to read the row back, so the member's gate is *unreadable*
    rather than readable-and-failing -- which is why the specific reason is
    ``GATE_MISSING`` and not ``GATE_NOT_PASSED``.  Both are a refusal; the
    difference is what the operator is told to repair.
    """

    chain = _Chain(tmp_path).build()
    chain.corrupt_gate_verdict(StrategyGateVerdict.FAIL)

    result = _decide(chain)

    assert Blocker.EVIDENCE_CHAIN_NOT_CURRENT in result.decision.blockers
    assert Blocker.GATE_MISSING in result.decision.blockers


def test_a_failed_coverage_blocks_promotion(tmp_path):
    from dataclasses import replace

    chain = _Chain(tmp_path).build()
    failed = replace(
        chain.coverage,
        verdict=StrategyCoverageVerdict.FAIL,
        blockers=(StrategyCoverageBlocker.EVIDENCE_MISSING,),
        items=(),
    )

    result = _decide(chain, coverage=failed)

    assert Blocker.COVERAGE_NOT_PASSED in result.decision.blockers


def test_a_gate_for_another_version_blocks_promotion(tmp_path):
    """The stored gate no longer describes the item that named it."""

    chain = _Chain(tmp_path).build()
    chain.corrupt_gate_version("version-2")

    result = _decide(chain)

    assert Blocker.EVIDENCE_CHAIN_NOT_CURRENT in result.decision.blockers


def test_gate_policy_revision_mismatch_blocks(tmp_path):
    """The lifecycle policy's required gate revision reaches the validator.

    This is the path the launch boundary will use too: the policy revision is read
    from the decision's own policy, never from an "active" one.
    """

    chain = _Chain(tmp_path).build()

    result = _decide(
        chain, policy=_policy(required_gate_policy_version="independent-review-v9")
    )

    assert Blocker.EVIDENCE_CHAIN_NOT_CURRENT in result.decision.blockers


def test_coverage_policy_revision_mismatch_blocks(tmp_path):
    chain = _Chain(tmp_path).build()

    result = _decide(
        chain,
        policy=_policy(required_coverage_policy_version="evidence-coverage-v9"),
    )

    assert Blocker.POLICY_REVISION_MISMATCH in result.decision.blockers


def test_a_revoked_member_key_blocks_promotion(tmp_path):
    """A key revoked after the claim was formed stops authorising mutations."""

    chain = _Chain(tmp_path).build()
    chain.set_trust_status(EvidenceKeyTrustStatus.REVOKED)

    result = _decide(chain)

    assert Blocker.EVIDENCE_CHAIN_NOT_CURRENT in result.decision.blockers
    assert Blocker.REVOKED_SIGNING_KEY in result.decision.blockers


def test_an_unknown_key_blocks_promotion(tmp_path):
    chain = _Chain(tmp_path).build()
    Path(chain.trust_store).write_text(
        json.dumps(
            trust_store_to_payload(
                (
                    EvidenceVerificationKey(
                        key_id="other-key", algorithm=ED25519_ALGORITHM,
                        public_key=chain.public_key,
                        trust_status=EvidenceKeyTrustStatus.ACTIVE,
                    ),
                )
            )
        ),
        encoding="utf-8",
    )

    result = _decide(chain)

    assert Blocker.EVIDENCE_CHAIN_NOT_CURRENT in result.decision.blockers
    assert Blocker.UNKNOWN_SIGNING_KEY in result.decision.blockers


def test_stale_member_evidence_blocks_promotion(tmp_path):
    """Evidence aged past the policy bound is refused.

    The evidence is generated 90 days before the claim is formed, so the coverage
    *evaluation* already refuses it (``COVERAGE_NOT_PASSED``) and the claim never
    passes.  The decision is therefore refused on the coverage verdict; the
    decision-time freshness re-check is exercised by
    ``test_a_claim_fresh_at_formation_is_stale_at_decision_time`` in the 6-C
    regression file, where the claim passes at formation and the promotion
    happens later.
    """

    chain = _Chain(tmp_path).build(generated_at=NOW - timedelta(days=90))

    result = _decide(chain)

    assert Blocker.EVIDENCE_CHAIN_NOT_CURRENT in result.decision.blockers
    assert Blocker.COVERAGE_NOT_PASSED in result.decision.blockers


def test_evidence_within_the_age_bound_is_accepted(tmp_path):
    chain = _Chain(tmp_path).build(generated_at=NOW - timedelta(days=1))

    assert _decide(chain).authorised is True


def test_version_identity_mismatch_blocks_promotion(tmp_path):
    chain = _Chain(tmp_path).build()
    other = _version(
        identity=StrategyIdentity("family-1", "version-2", PARAMETER_HASH)
    )

    result = _decide(chain, version=other)

    assert Blocker.VERSION_IDENTITY_MISMATCH in result.decision.blockers


def test_a_cross_wired_chain_is_refused(tmp_path):
    """Evidence from one version with a gate and coverage from another.

    The gate and coverage here have both been re-pointed at the version under
    test, so only the authenticated-evidence binding can catch the mismatch.
    Without it the chain would look complete and the promotion would proceed.
    """

    from dataclasses import replace

    chain = _Chain(tmp_path).build()
    other = _version(
        identity=StrategyIdentity("family-1", "version-2", PARAMETER_HASH)
    )

    result = _decide(
        chain,
        version=other,
        coverage=replace(chain.coverage, strategy_version_id="version-2"),
    )

    assert result.authorised is False
    # The claim agrees with the version it is presented against, so the
    # structural half passes -- and the *stored member records* are what catch it:
    # they still name the original version, which is the cross-wire this case is
    # about.
    assert Blocker.EVIDENCE_CHAIN_NOT_CURRENT in result.decision.blockers


def test_parameter_hash_mismatch_blocks_promotion(tmp_path):
    chain = _Chain(tmp_path).build()
    other = _version(
        identity=StrategyIdentity("family-1", VERSION_ID, "parameter-2")
    )

    result = _decide(chain, version=other)

    assert Blocker.PARAMETER_HASH_MISMATCH in result.decision.blockers


def test_a_parameter_hash_cross_wire_is_refused(tmp_path):
    """Only the evidence binding can catch this: coverage is re-pointed too."""

    from dataclasses import replace

    chain = _Chain(tmp_path).build()
    other = _version(
        identity=StrategyIdentity("family-1", VERSION_ID, "parameter-2")
    )

    result = _decide(
        chain,
        version=other,
        coverage=replace(chain.coverage, parameter_hash="parameter-2"),
    )

    assert result.authorised is False
    # The claim now agrees with the version's parameters, so only the *stored*
    # member records can catch that they were signed for other parameters.
    assert Blocker.EVIDENCE_CHAIN_NOT_CURRENT in result.decision.blockers


# -- pause semantics -----------------------------------------------------


def test_a_pause_with_nothing_wrong_is_refused(tmp_path):
    """An evaluator must not be able to suspend a healthy running strategy."""

    chain = _Chain(tmp_path).build()
    version = _version(status=StrategyStatus.PAPER_SHADOW, mode=StrategyMode.PAPER_SHADOW)

    result = _decide(chain, action=StrategyLifecycleAction.PAUSE, version=version)

    assert result.authorised is False
    assert Blocker.PAUSE_NOT_JUSTIFIED in result.decision.blockers


def test_a_pause_is_authorised_by_a_named_governance_failure(tmp_path):
    """A revoked key is exactly the kind of fact a pause must name.

    The trigger names the *specific* reason, not only the umbrella.  Collapsing
    every chain failure into ``EVIDENCE_CHAIN_NOT_CURRENT`` was a diagnostic
    regression: an operator pausing a version needs to know whether a key was
    revoked, a record vanished, an identity drifted or the evidence aged out,
    because those are four different repairs.  The umbrella is still present --
    it is what refuses a promotion -- so both are asserted.
    """

    chain = _Chain(tmp_path).build()
    version = _version(status=StrategyStatus.PAPER_SHADOW, mode=StrategyMode.PAPER_SHADOW)
    chain.set_trust_status(EvidenceKeyTrustStatus.REVOKED)

    result = _decide(chain, action=StrategyLifecycleAction.PAUSE, version=version)

    assert result.authorised is True
    assert result.decision.blockers == ()
    assert result.decision.triggers == (
        Blocker.EVIDENCE_CHAIN_NOT_CURRENT,
        Blocker.REVOKED_SIGNING_KEY,
    )
    assert result.authorization.target_status is StrategyStatus.PAUSED


def test_invalid_coverage_also_justifies_a_pause(tmp_path):
    from dataclasses import replace

    chain = _Chain(tmp_path).build()
    version = _version(status=StrategyStatus.PAPER_SHADOW, mode=StrategyMode.PAPER_SHADOW)
    stale = replace(
        chain.coverage,
        verdict=StrategyCoverageVerdict.FAIL,
        blockers=(StrategyCoverageBlocker.EVIDENCE_MISSING,),
        items=(),
    )

    result = _decide(
        chain, action=StrategyLifecycleAction.PAUSE, version=version, coverage=stale
    )

    assert result.authorised is True
    assert Blocker.COVERAGE_NOT_PASSED in result.decision.triggers


def test_a_structurally_blocked_pause_records_no_triggers(tmp_path):
    """Triggers only describe an *authorised* pause; a refusal has blockers."""

    chain = _Chain(tmp_path).build()

    result = _decide(
        chain,
        action=StrategyLifecycleAction.PAUSE,
        version=_version(),  # RESEARCH is not a legal pause source
    )

    assert result.authorised is False
    assert Blocker.CURRENT_STATUS_NOT_ELIGIBLE in result.decision.blockers
    assert result.decision.triggers == ()


# -- identity, authorization and neutrality ------------------------------


def test_repeating_the_decision_is_idempotent(tmp_path):
    chain = _Chain(tmp_path).build()

    first = _decide(chain, decided_at=NOW)
    second = _decide(chain, decided_at=NOW + timedelta(hours=3))

    assert first.decision.decision_id == second.decision.decision_id


def test_a_changed_action_produces_a_new_decision_identity(tmp_path):
    chain = _Chain(tmp_path).build()
    version = _version(status=StrategyStatus.PAPER_SHADOW, mode=StrategyMode.PAPER_SHADOW)

    promote = _decide(chain, version=version)
    pause = _decide(
        chain, action=StrategyLifecycleAction.PAUSE, version=version,
    )

    assert promote.decision.decision_id != pause.decision.decision_id


def test_authorization_cannot_be_minted_without_the_controller_token(tmp_path):
    chain = _Chain(tmp_path).build()
    result = _decide(chain)

    with pytest.raises(TypeError):
        StrategyLifecycleAuthorization(
            VERSION_ID,
            StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
            StrategyStatus.PAPER_SHADOW,
            "sld-forged",
            NOW,
        )

    with pytest.raises(TypeError):
        StrategyLifecycleAuthorization(
            VERSION_ID,
            StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
            StrategyStatus.PAPER_SHADOW,
            "sld-forged",
            NOW,
            _controller_token=object(),
        )

    assert result.authorised is True


def test_the_controller_does_not_change_lifecycle_fields(tmp_path):
    chain = _Chain(tmp_path).build()
    version = _version()
    before = (version.status, version.mode, version.gate_passed, version.gate_reason)

    result = _decide(chain, version=version)

    assert result.authorised is True
    assert (version.status, version.mode, version.gate_passed, version.gate_reason) == before
    assert version.status is StrategyStatus.RESEARCH


def test_a_blocked_decision_grants_no_authorization(tmp_path):
    chain = _Chain(tmp_path).build()

    result = _decide(chain, policy=None)

    assert isinstance(result, StrategyLifecycleDecisionResult)
    assert result.authorization is None


def test_the_legacy_gate_flag_cannot_appear_in_a_decision(tmp_path):
    """Nothing in a decision can express "the legacy flag said yes"."""

    chain = _Chain(tmp_path).build()
    version = _version(gate_passed=True, gate_reason="legacy review")

    result = _decide(chain, version=version)
    rendered = json.dumps(
        {
            "blockers": [item.value for item in result.decision.blockers],
            "triggers": [item.value for item in result.decision.triggers],
        }
    ).lower()

    assert "gate_passed" not in rendered
    assert "legacy" not in rendered
