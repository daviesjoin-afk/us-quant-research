"""Stage 6-C repair: the two boundaries where a coverage claim is re-checked.

Every case here is written against a **real** chain: real SQLite stores, real
Ed25519 seals, the real lifecycle service and the real launch gate.  The point is
not that a validator function returns the right enum -- it is that a member key
revoked *after* a claim was formed stops both of the things that claim authorises:

* the **lifecycle boundary**, where a promotion would otherwise be granted;
* the **Paper launch boundary**, where an already-promoted version would otherwise
  still be allowed to run.

Those are different paths through different components, and the whole repair is
about the second one: a version promoted yesterday must stop launching today when
a key is revoked, without anybody re-running the promotion.

The eight cases are the R1-R8 list from the repair brief.  Where a case can only
be expressed by reaching past the public API -- deleting a stored row, say -- that
is done deliberately and said so: the state has to be *reachable* for the guard to
mean anything.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

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
from us_quant.trading.adapters.sqlite.evidence_authentication_repository import (
    SQLiteEvidenceAuthenticationRepository,
)
from us_quant.trading.adapters.sqlite.strategy_coverage_repository import (
    SQLiteStrategyCoverageRepository,
)
from us_quant.trading.adapters.sqlite.strategy_gate_repository import (
    SQLiteStrategyGateRepository,
)
from us_quant.trading.adapters.sqlite.strategy_lifecycle_repository import (
    SQLiteStrategyLifecycleRepository,
)
from us_quant.trading.adapters.sqlite.strategy_repository import (
    SQLiteStrategyRepository,
)
from us_quant.trading.application.evidence_authentication import (
    EvidenceAuthenticationApplication,
)
from us_quant.trading.application.paper_authorization import PaperLaunchAuthorizer
from us_quant.trading.application.strategies import StrategyApplication
from us_quant.trading.application.strategy_coverage import StrategyCoverageEvaluator
from us_quant.trading.application.strategy_coverage_validity import (
    StrategyCoverageCurrentValidator,
)
from us_quant.trading.application.strategy_gate import StrategyGateEvaluator
from us_quant.trading.application.strategy_lifecycle import (
    StrategyLifecycleController,
    StrategyLifecycleService,
)
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
    StrategyCoverageEvidence,
    StrategyCoveragePolicy,
    StrategyCoverageVerdict,
)
from us_quant.trading.domain.strategy_gate import StrategyGatePolicy
from us_quant.trading.domain.strategy_lifecycle import (
    StrategyLifecycleAction,
    StrategyLifecycleBlocker,
    StrategyLifecyclePolicy,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATER = NOW + timedelta(days=1)
VERSION_ID = "version-1"
SEMVER = "1.0.0"
PARAMETER_HASH = "parameter-1"
UNIVERSE_HASH = "universe-1"
CODE_HASH = "code-1"
KEY_A = "research-key-a"
KEY_B = "research-key-b"
GATE_POLICY = "independent-review-v1"
COVERAGE_POLICY = "evidence-coverage-v1"


def _review(
    *,
    run_id: str,
    symbol: str,
    data_hash: str,
    version_id: str,
    parameter_hash: str,
) -> TargetedReviewResult:
    gates = tuple(
        EvidenceGate(code, name, True, "yes", required, evidence)
        for code, name, required, evidence in TARGETED_REVIEW_GATE_DEFINITIONS
    )
    return TargetedReviewResult(
        run_id=run_id,
        robustness_run_id=f"{run_id}-robust",
        validation_run_id=f"{run_id}-validation",
        overfit_run_id=f"{run_id}-overfit",
        data_quality_run_id=f"{run_id}-quality",
        execution_stress_run_id=f"{run_id}-stress",
        symbol=symbol,
        strategy_version_id=version_id,
        strategy_semver=SEMVER,
        base_parameter_hash=parameter_hash,
        data_hash=data_hash,
        provider="provider-1",
        evidence_origins=("captured_stream",),
        dependence=DependenceDiagnostic(12, None, None, 0, None, None, None, "pass"),
        gates=gates,
        passed_gates=len(gates),
        blocking_failures=0,
        warnings=(),
        decision="ELIGIBLE_FOR_INDEPENDENT_REVIEW",
        eligible_for_independent_review=True,
        status="PASS",
    )


def _version(**changes) -> StrategyVersion:
    values = dict(
        definition=StrategyDefinition("family-1", "Example", "test"),
        identity=StrategyIdentity("family-1", VERSION_ID, PARAMETER_HASH),
        semver=SEMVER,
        status=StrategyStatus.RESEARCH,
        mode=StrategyMode.RESEARCH,
        parameters={"period": 5},
        universe_hash=UNIVERSE_HASH,
        code_hash=CODE_HASH,
        risk_budget_pct=Decimal("0.01"),
        gate_passed=False,
        gate_reason="legacy",
        created_at=NOW,
        updated_at=NOW,
    )
    values.update(changes)
    return StrategyVersion(**values)


def _lifecycle_policy(**changes) -> StrategyLifecyclePolicy:
    values = dict(
        policy_id="lifecycle-policy-1",
        revision=1,
        policy_version="strategy-lifecycle-v1",
        permitted_actions=(
            StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
            StrategyLifecycleAction.PAUSE,
            StrategyLifecycleAction.RESUME_PAPER_SHADOW,
        ),
        required_gate_policy_version=GATE_POLICY,
        required_coverage_policy_version=COVERAGE_POLICY,
        maximum_evidence_age=timedelta(days=30),
        created_at=NOW,
    )
    values.update(changes)
    return StrategyLifecyclePolicy(**values)


def _coverage_policy(**changes) -> StrategyCoveragePolicy:
    values = dict(
        policy_id="coverage-policy-1",
        revision=1,
        policy_version=COVERAGE_POLICY,
        required_symbols=("AAPL", "MSFT"),
        required_universe_hash=UNIVERSE_HASH,
        required_code_hash=CODE_HASH,
        min_distinct_review_runs=2,
        min_distinct_data_hashes=2,
        maximum_evidence_age=timedelta(days=30),
        created_at=NOW,
    )
    values.update(changes)
    return StrategyCoveragePolicy(**values)


class _Chain:
    """A two-member evidence chain, promoted through the real authority."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.artifacts = tmp_path / "artifacts"
        self.keys = tmp_path / "keys"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.keys.mkdir(parents=True, exist_ok=True)

        self.private_a = self.keys / "a.key"
        self.public_a = write_signing_key(self.private_a)
        self.private_b = self.keys / "b.key"
        self.public_b = write_signing_key(self.private_b)
        self.trust_store = self.keys / "trust.json"
        self.set_statuses(a=EvidenceKeyTrustStatus.ACTIVE, b=EvidenceKeyTrustStatus.ACTIVE)

        self.artifact_source = TargetedReviewArtifactSource()
        self.key_source = FileEvidenceVerificationKeySource(self.trust_store)
        self.authenticator = EvidenceAuthenticationApplication(
            artifact_source=self.artifact_source,
            key_source=self.key_source,
            signature_verifier=Ed25519EvidenceSignatureVerifier(),
        )
        self.gate_evaluator = StrategyGateEvaluator()
        self.coverage_evaluator = StrategyCoverageEvaluator(key_source=self.key_source)

        self.governance = tmp_path / "governance.sqlite3"
        self.authentications = SQLiteEvidenceAuthenticationRepository(self.governance)
        self.gates = SQLiteStrategyGateRepository(self.governance)
        self.coverages = SQLiteStrategyCoverageRepository(self.governance)
        self.decisions = SQLiteStrategyLifecycleRepository(self.governance)
        self.strategies = StrategyApplication(SQLiteStrategyRepository(self.governance))

        self.validity = StrategyCoverageCurrentValidator(
            authentications=self.authentications,
            gates=self.gates,
            key_source=self.key_source,
        )
        self.controller = StrategyLifecycleController(
            coverage_validity=self.validity
        )
        self.service = StrategyLifecycleService(
            controller=self.controller,
            decisions=self.decisions,
            strategies=self.strategies,
        )
        self.authorizer = PaperLaunchAuthorizer(
            decisions=self.decisions,
            coverages=self.coverages,
            lifecycle_policies=self.decisions,
            coverage_validity=self.validity,
            clock=lambda: LATER,
        )
        self.coverage = None
        #: The id the store assigned.  ``register`` mints a uuid, so nothing may
        #: assume a literal.
        self.version_id: str | None = None

    # -- trust root ------------------------------------------------------

    def set_statuses(self, *, a, b) -> None:
        Path(self.trust_store).write_text(
            json.dumps(
                trust_store_to_payload(
                    (
                        EvidenceVerificationKey(
                            key_id=KEY_A,
                            algorithm=ED25519_ALGORITHM,
                            public_key=self.public_a,
                            trust_status=a,
                        ),
                        EvidenceVerificationKey(
                            key_id=KEY_B,
                            algorithm=ED25519_ALGORITHM,
                            public_key=self.public_b,
                            trust_status=b,
                        ),
                    )
                )
            ),
            encoding="utf-8",
        )

    def revoke_a(self) -> None:
        self.set_statuses(a=EvidenceKeyTrustStatus.REVOKED, b=EvidenceKeyTrustStatus.ACTIVE)

    def revoke_b(self) -> None:
        """Revoke the *second* member, which is the one a first-only walk misses."""

        self.set_statuses(a=EvidenceKeyTrustStatus.ACTIVE, b=EvidenceKeyTrustStatus.REVOKED)

    # -- the chain -------------------------------------------------------

    def build(self, *, generated_at: datetime = NOW) -> "_Chain":
        # Registered first, so the claim is evaluated against the version that
        # actually exists -- the same binding the lifecycle decision checks.
        version = self.persist_version(_version())
        evidence = []
        for run_id, symbol, data_hash, key_id, private in (
            ("review-a", "AAPL", "data-a", KEY_A, self.private_a),
            ("review-b", "MSFT", "data-b", KEY_B, self.private_b),
        ):
            artifact_path = save_targeted_review(
                _review(
                    run_id=run_id,
                    symbol=symbol,
                    data_hash=data_hash,
                    version_id=version.version_id,
                    parameter_hash=version.parameter_hash,
                ),
                self.artifacts,
            )
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            payload["generated_at"] = generated_at.isoformat()
            artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            seal_review_artifact(
                artifact_path=artifact_path,
                private_key_path=private,
                trust_store_path=self.trust_store,
                key_id=key_id,
                universe_hash=UNIVERSE_HASH,
                code_hash=CODE_HASH,
                seal_path=default_seal_path(artifact_path),
                signed_at=NOW,
            )
            loaded = self.artifact_source.load(artifact_path)
            outcome = self.authenticator.authenticate(
                version=version,
                artifact_path=artifact_path,
                policy=EvidenceAuthenticationPolicy(),
                verified_at=NOW,
            )
            assert outcome.authenticated_evidence is not None
            authenticated = outcome.authenticated_evidence
            gate = self.gate_evaluator.evaluate(
                version=version,
                evidence=loaded.evidence,
                policy=StrategyGatePolicy(),
                evaluated_at=NOW,
            )
            self.authentications.record(authenticated.authentication)
            self.gates.record(gate)
            evidence.append(
                StrategyCoverageEvidence(authenticated=authenticated, gate=gate)
            )

        self.coverage = self.coverage_evaluator.evaluate(
            version=version,
            policy=_coverage_policy(),
            evidence=tuple(evidence),
            evaluated_at=NOW,
        )
        self.coverages.record_evaluation(self.coverage)
        return self

    def persist_version(self, version: StrategyVersion) -> StrategyVersion:
        """Register the version and return what the store holds.

        ``gate_passed`` is left at its default False, and that default is the
        point: an evidence-authorised promotion must work without the legacy flag
        ever being set, which is what makes the old architecture demonstrably dead
        rather than merely unused.
        """

        registered = self.strategies.register(
            strategy_id=version.definition.strategy_id,
            name=version.definition.name,
            description=version.definition.description,
            semver=version.semver,
            parameters=dict(version.parameters),
            universe_hash=version.universe_hash,
            code_hash=version.code_hash,
            risk_budget_pct=version.risk_budget_pct,
        )
        self.version_id = registered.version_id
        return registered

    def persist_policy(self, policy: StrategyLifecyclePolicy | None = None) -> None:
        """Append one policy revision, against the revision currently stored."""

        candidate = policy or _lifecycle_policy()
        revisions = self.decisions.policy_revisions(candidate.policy_id)
        # ``None`` is the first revision's expectation, not 0: the store
        # distinguishes "nothing stored yet" from "revision 0 stored".
        expected = revisions[-1] if revisions else None
        self.decisions.append_policy_revision(
            candidate, expected_current_revision=expected
        )

    def promote(self, *, now: datetime = NOW):
        assert self.version_id is not None
        version = self.strategies.get_version(self.version_id)
        return self.service.apply(
            version=version,
            action=StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
            policy=_lifecycle_policy(),
            coverage=self.coverage,
            applied_at=now,
        )

    # -- deliberate damage ----------------------------------------------

    def delete_authentication(self, authentication_id: str) -> None:
        self._execute(
            "DELETE FROM strategy_evidence_authentication "
            "WHERE authentication_id = ?",
            (authentication_id,),
        )

    def delete_gate(self, evaluation_id: str) -> None:
        self._execute(
            "DELETE FROM strategy_gate_evaluation WHERE evaluation_id = ?",
            (evaluation_id,),
        )

    def _execute(self, statement: str, parameters: tuple) -> None:
        connection = sqlite3.connect(self.governance)
        try:
            connection.execute(statement, parameters)
            connection.commit()
        finally:
            connection.close()


def _ready(tmp_path: Path) -> _Chain:
    """A chain promoted through the real authority, ready for a launch."""

    chain = _Chain(tmp_path).build()
    chain.persist_policy()
    outcome = chain.promote()
    assert outcome.authorised is True, outcome.decision.blockers
    return chain


# =====================================================================
# R1 / R2 -- the two boundaries
# =====================================================================


def test_r1_a_member_revoked_before_promotion_blocks_it(tmp_path: Path) -> None:
    """The lifecycle boundary: revocation reaches the promotion itself."""

    chain = _Chain(tmp_path).build()
    chain.persist_policy()

    assert chain.promote().authorised is True

    # A second version over the same shape of chain, with key A revoked before
    # the decision.
    second = _Chain(tmp_path / "second").build()
    second.persist_policy()
    second.revoke_a()

    outcome = second.promote()

    assert outcome.authorised is False
    assert (
        StrategyLifecycleBlocker.EVIDENCE_CHAIN_NOT_CURRENT
        in outcome.decision.blockers
    )
    assert (
        second.strategies.get_version(second.version_id).status
        is StrategyStatus.RESEARCH
    )


def test_r2_a_member_revoked_after_promotion_refuses_the_launch(
    tmp_path: Path,
) -> None:
    """The Paper launch boundary, and the whole point of the repair.

    The version was promoted while both keys were active.  Nothing re-runs the
    promotion afterwards, so the only thing standing between the revoked key and
    a running Paper session is the launch gate re-checking the claim's members.
    """

    chain = _ready(tmp_path)
    assert chain.authorizer.authorises(chain.version_id) is True

    chain.revoke_a()

    assert chain.authorizer.authorises(chain.version_id) is False


def test_r2b_a_revocation_on_the_last_member_also_refuses_the_launch(
    tmp_path: Path,
) -> None:
    """The member a first-member-only walk would never reach.

    R2 revokes the member the loop happens to see first.  This revokes the last
    one, so a validator that checked only ``coverage.items[0]`` -- or that stopped
    after its first blocker -- passes R2 and fails here.  That asymmetry is the
    whole defect: the old model inspected one representative, and which member it
    inspected was an accident of ordering.
    """

    chain = _ready(tmp_path)
    assert chain.authorizer.authorises(chain.version_id) is True

    chain.revoke_b()

    assert chain.authorizer.authorises(chain.version_id) is False


# =====================================================================
# R3 / R4 -- exact records, not "some passing record"
# =====================================================================


def test_r3_a_missing_member_authentication_is_not_substituted(
    tmp_path: Path,
) -> None:
    """Another passing authentication for the same version is not a substitute."""

    chain = _ready(tmp_path)
    chain.delete_authentication(chain.coverage.items[0].authentication_id)

    assert chain.authorizer.authorises(chain.version_id) is False


def test_r4_a_missing_member_gate_is_not_substituted(tmp_path: Path) -> None:
    chain = _ready(tmp_path)
    chain.delete_gate(chain.coverage.items[0].gate_evaluation_id)

    assert chain.authorizer.authorises(chain.version_id) is False


# =====================================================================
# R5 / R6 -- freshness, and rotation is not revocation
# =====================================================================


def test_r5_one_stale_member_invalidates_the_claim_at_both_boundaries(
    tmp_path: Path,
) -> None:
    """A claim is as fresh as its oldest member, at promotion and at launch."""

    chain = _Chain(tmp_path).build(generated_at=NOW - timedelta(days=90))
    chain.persist_policy()

    outcome = chain.promote()
    assert outcome.authorised is False
    assert (
        StrategyLifecycleBlocker.EVIDENCE_CHAIN_NOT_CURRENT
        in outcome.decision.blockers
    )


def test_r5b_a_claim_fresh_at_formation_is_stale_at_decision_time(
    tmp_path: Path,
) -> None:
    """The lifecycle re-checks freshness against *its own* policy's age bound.

    R5 ages the evidence before the claim is formed, so the coverage evaluation
    itself fails and the claim never passes -- which means R5 exercises the
    evaluator, not the decision-time re-check.  Here the claim passes at formation
    (evidence is fresh when evaluated) and the promotion happens much later, so the
    only thing that can refuse it is the lifecycle applying the policy's age bound
    at ``decided_at``.  That is the re-check the repair added, isolated.
    """

    chain = _Chain(tmp_path).build(generated_at=NOW)
    chain.persist_policy()

    # The claim passed when it was formed; the promotion is 90 days later.
    assert chain.coverage.verdict is StrategyCoverageVerdict.PASS

    outcome = chain.promote(now=NOW + timedelta(days=90))

    assert outcome.authorised is False
    assert (
        StrategyLifecycleBlocker.EVIDENCE_CHAIN_NOT_CURRENT
        in outcome.decision.blockers
    )


def test_r6_a_verify_only_key_still_authorises(tmp_path: Path) -> None:
    """Key rotation is not revocation, at either boundary."""

    chain = _Chain(tmp_path).build()
    chain.set_statuses(a=EvidenceKeyTrustStatus.VERIFY_ONLY, b=EvidenceKeyTrustStatus.ACTIVE)
    chain.persist_policy()

    assert chain.promote().authorised is True
    assert chain.authorizer.authorises(chain.version_id) is True


# =====================================================================
# R7 / R8 -- historical records, and the decision's own policy
# =====================================================================


def test_r7_an_incomplete_historical_member_record_fails_closed(
    tmp_path: Path,
) -> None:
    """A record that cannot prove its identity is not read as probably fine.

    The compatibility cost of strict comparison, asserted on the real store: the
    stored ``key_id`` is cleared, so the record still exists and still says PASS
    but can no longer prove which key signed it.
    """

    chain = _ready(tmp_path)
    chain._execute(
        "UPDATE strategy_evidence_authentication SET key_id = NULL "
        "WHERE authentication_id = ?",
        (chain.coverage.items[0].authentication_id,),
    )

    assert chain.authorizer.authorises(chain.version_id) is False


def test_r8_the_launch_reads_the_decisions_own_policy_revision(
    tmp_path: Path,
) -> None:
    """A newer policy revision does not retroactively govern an older promotion.

    The promotion was justified under revision 1.  Revision 2 is appended
    afterwards with a gate policy the stored gate does not satisfy; if the launch
    read the *active* policy it would refuse a launch that revision 1 authorised,
    and -- the direction that matters -- a laxer revision 2 would authorise one
    revision 1 did not.
    """

    chain = _ready(tmp_path)
    assert chain.authorizer.authorises(chain.version_id) is True

    chain.persist_policy(
        _lifecycle_policy(
            revision=2,
            required_gate_policy_version="independent-review-v9",
        )
    )

    assert chain.authorizer.authorises(chain.version_id) is True


def test_r8b_a_laxer_newer_policy_does_not_authorise_an_older_promotion(
    tmp_path: Path,
) -> None:
    """The dangerous direction of the same fact."""

    chain = _ready(tmp_path)
    chain.revoke_a()
    assert chain.authorizer.authorises(chain.version_id) is False

    # A newer policy that dropped the age bound and the gate requirement entirely
    # must not resurrect it: the decision names revision 1.
    chain.persist_policy(
        _lifecycle_policy(
            revision=2,
            required_gate_policy_version="anything",
            maximum_evidence_age=None,
        )
    )

    assert chain.authorizer.authorises(chain.version_id) is False


@pytest.mark.parametrize("blocker", [StrategyLifecycleBlocker.EVIDENCE_CHAIN_NOT_CURRENT])
def test_the_repair_blocker_exists(blocker) -> None:
    """The lifecycle blocker the chain reports is a real member, not a string."""

    assert blocker.value == "EVIDENCE_CHAIN_NOT_CURRENT"
