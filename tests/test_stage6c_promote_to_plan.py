"""Stage 6-C repair: promote to Paper, then build a plan, end to end.

``#82`` recorded this as its own known gap: every unit was green while nothing
proved the *Paper path* actually opened.  This file is that proof, and it is the
one that demonstrates the legacy architecture is dead rather than merely unused.

The assertion that carries it is ``gate_passed is False``.  A version promoted
through the evidence chain deliberately never sets the legacy flag, so if the plan
boundary still consulted it the plan would be refused -- and this test would fail.
That it succeeds is the statement that authorisation now comes from the coverage
chain, the lifecycle controller and the launch gate, and from nothing else.

The reverse direction is asserted in the same file: revoke one member key and the
same plan is refused, without re-running the promotion.
"""

from __future__ import annotations

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
from us_quant.trading.adapters.sqlite.evidence_authentication_repository import (
    SQLiteEvidenceAuthenticationRepository,
)
from us_quant.trading.adapters.sqlite.portfolio_operating_plan_repository import (
    SQLitePortfolioOperatingPlanRepository,
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
from us_quant.trading.application.portfolio_operations import (
    PortfolioOperatingPlanApplication,
    PortfolioPlanRefused,
)
from us_quant.trading.application.strategies import StrategyApplication
from us_quant.trading.application.strategy_coverage import StrategyCoverageEvaluator
from us_quant.trading.application.strategy_coverage_validity import (
    StrategyCoverageCurrentValidator,
)
from us_quant.trading.application.strategy_gate import StrategyGateEvaluator
from us_quant.trading.application.strategy_defaults import (
    DEFAULT_STRATEGY_SEEDS,
)
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
from us_quant.trading.domain.portfolio import (
    PortfolioCapitalPolicy,
    PortfolioStrategyAllocation,
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
)
from us_quant.trading.domain.strategy_gate import StrategyGatePolicy
from us_quant.trading.domain.strategy_lifecycle import (
    StrategyLifecycleAction,
    StrategyLifecyclePolicy,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
LAUNCH_AT = NOW + timedelta(hours=1)
STRATEGY_ID = "intraday-auto-rotation"
UNIVERSE_HASH = "universe-1"
CODE_HASH = "code-1"
KEY_A = "research-key-a"
KEY_B = "research-key-b"
GATE_POLICY = "independent-review-v1"
COVERAGE_POLICY = "evidence-coverage-v1"


def _seed():
    """The strategy's real seed, so registration validates as production does."""

    for seed in DEFAULT_STRATEGY_SEEDS:
        if seed.strategy_id == STRATEGY_ID:
            return seed
    raise AssertionError(f"no seed is defined for {STRATEGY_ID}")


def _seed_parameters() -> dict:
    return dict(_seed().parameters)


def _seed_semver() -> str:
    return _seed().semver


def _review(*, run_id: str, symbol: str, data_hash: str, version: StrategyVersion):
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
        strategy_version_id=version.version_id,
        strategy_semver=version.semver,
        base_parameter_hash=version.parameter_hash,
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


def _lifecycle_policy() -> StrategyLifecyclePolicy:
    return StrategyLifecyclePolicy(
        policy_id="lifecycle-policy-1",
        revision=1,
        policy_version="strategy-lifecycle-v1",
        permitted_actions=(
            StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
            StrategyLifecycleAction.RESUME_PAPER_SHADOW,
            StrategyLifecycleAction.PAUSE,
        ),
        required_gate_policy_version=GATE_POLICY,
        required_coverage_policy_version=COVERAGE_POLICY,
        maximum_evidence_age=timedelta(days=30),
        created_at=NOW,
    )


def _coverage_policy() -> StrategyCoveragePolicy:
    return StrategyCoveragePolicy(
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


def _plan_policy(version_id: str) -> PortfolioCapitalPolicy:
    """The real hard-ceiling shape, with one fully enabled allocation."""

    return PortfolioCapitalPolicy(
        total_capital_limit=Decimal("10000"),
        max_gross_exposure=Decimal("10000"),
        max_net_exposure=Decimal("10000"),
        max_single_position_notional=Decimal("10000"),
        max_symbol_concentration=Decimal("1"),
        max_strategy_concentration=Decimal("1"),
        max_positions=5,
        max_open_orders=5,
        allocations=(
            PortfolioStrategyAllocation(
                strategy_version_id=version_id,
                capital_weight=Decimal("1"),
                max_capital=Decimal("10000"),
                max_gross_exposure=Decimal("10000"),
                enabled=True,
            ),
        ),
    )


class _Paper:
    """The full production chain, from sealed evidence to an accepted plan."""

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
        self.set_statuses(
            a=EvidenceKeyTrustStatus.ACTIVE, b=EvidenceKeyTrustStatus.ACTIVE
        )

        self.key_source = FileEvidenceVerificationKeySource(self.trust_store)
        self.artifact_source = TargetedReviewArtifactSource()
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
        self.service = StrategyLifecycleService(
            controller=StrategyLifecycleController(coverage_validity=self.validity),
            decisions=self.decisions,
            strategies=self.strategies,
        )
        self.authorizer = PaperLaunchAuthorizer(
            decisions=self.decisions,
            coverages=self.coverages,
            lifecycle_policies=self.decisions,
            coverage_validity=self.validity,
            clock=lambda: LAUNCH_AT,
        )
        self.plan = PortfolioOperatingPlanApplication(
            repository=SQLitePortfolioOperatingPlanRepository(
                tmp_path / "plans.sqlite3"
            ),
            strategies=self.strategies,
            paper_authorization=self.authorizer.authorises,
        )
        self.coverage = None
        self.version: StrategyVersion | None = None

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
        self.set_statuses(
            a=EvidenceKeyTrustStatus.REVOKED, b=EvidenceKeyTrustStatus.ACTIVE
        )

    # -- the chain -------------------------------------------------------

    def promote(self):
        """Register, seal two members, form the claim, and promote for real."""

        self.version = self.strategies.register(
            strategy_id=STRATEGY_ID,
            name="Auto rotation",
            description="test",
            semver=_seed_semver(),
            parameters=_seed_parameters(),
            universe_hash=UNIVERSE_HASH,
            code_hash=CODE_HASH,
            risk_budget_pct=Decimal("0.01"),
        )
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
                    version=self.version,
                ),
                self.artifacts,
            )
            # ``save_targeted_review`` stamps the wall clock, and the gate refuses
            # an artifact whose ``generated_at`` is *after* the evaluation instant.
            # The whole chain here is pinned to NOW, so the stamp is rewritten to
            # match -- otherwise the gate would be rejecting a future timestamp
            # rather than the thing each case is about.
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            payload["generated_at"] = NOW.isoformat()
            artifact_path.write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
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
                version=self.version,
                artifact_path=artifact_path,
                policy=EvidenceAuthenticationPolicy(),
                verified_at=NOW,
            )
            assert outcome.authenticated_evidence is not None, (
                outcome.result.blockers
            )
            authenticated = outcome.authenticated_evidence
            gate = self.gate_evaluator.evaluate(
                version=self.version,
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
            version=self.version,
            policy=_coverage_policy(),
            evidence=tuple(evidence),
            evaluated_at=NOW,
        )
        self.coverages.record_evaluation(self.coverage)

        policy = _lifecycle_policy()
        self.decisions.append_policy_revision(
            policy, expected_current_revision=None
        )
        return self.service.apply(
            version=self.strategies.get_version(self.version.version_id),
            action=StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
            policy=policy,
            coverage=self.coverage,
            applied_at=NOW,
        )

    def build_plan(self, *, expected_revision: int = 0):
        assert self.version is not None
        return self.plan.save(
            selected_version_ids=(self.version.version_id,),
            policy=_plan_policy(self.version.version_id),
            expected_revision=expected_revision,
            operator_reason="Initial Paper allocation",
        )


# =====================================================================
# The end-to-end path
# =====================================================================


def test_promote_to_plan_succeeds_with_the_legacy_gate_flag_unset(
    tmp_path: Path,
) -> None:
    """The gap #82 recorded, closed.

    Every step is the production one: sealed evidence, real authentication, real
    gate, real coverage, the real lifecycle service, the real launch gate, the
    real plan store.  The version's ``gate_passed`` is **False** throughout --
    nothing in the new path writes it -- and the plan is still accepted.
    """

    paper = _Paper(tmp_path)
    outcome = paper.promote()

    assert outcome.authorised is True, outcome.decision.blockers

    version = paper.strategies.get_version(paper.version.version_id)
    assert version.status is StrategyStatus.PAPER_SHADOW
    assert version.mode is StrategyMode.PAPER_SHADOW
    # The assertion that proves the old architecture is dead: the legacy flag was
    # never set, and nothing needed it.
    assert version.gate_passed is False

    assert paper.authorizer.authorises(version.version_id) is True

    plan = paper.build_plan()

    assert plan.selected_version_ids == (version.version_id,)


def test_the_same_plan_is_refused_after_one_member_key_is_revoked(
    tmp_path: Path,
) -> None:
    """The other direction, on the same chain, without re-running the promotion.

    A plan built while both keys were active must stop being accepted when one of
    them is revoked.  Nothing about the version, the plan or the promotion
    changes -- only the trust root -- which is exactly the fact the repair is
    about.
    """

    paper = _Paper(tmp_path)
    assert paper.promote().authorised is True
    version_id = paper.version.version_id

    assert paper.build_plan() is not None

    paper.revoke_a()

    assert paper.authorizer.authorises(version_id) is False
    with pytest.raises(PortfolioPlanRefused):
        paper.build_plan()


def test_a_version_never_promoted_cannot_build_a_plan(tmp_path: Path) -> None:
    """Fail closed: no lifecycle decision means no authorisation, not a default."""

    paper = _Paper(tmp_path)
    paper.promote()
    # A second version of the same family, registered but never promoted.  The
    # store keys a version on ``(strategy_id, semver)``, so it needs its own
    # semver -- the point is that it was never through the lifecycle, not that it
    # is a different strategy.
    other = paper.strategies.register(
        strategy_id=STRATEGY_ID,
        name="Auto rotation",
        description="test",
        semver="1.0.1-research",
        parameters=_seed_parameters(),
        universe_hash=UNIVERSE_HASH,
        code_hash=CODE_HASH,
        risk_budget_pct=Decimal("0.01"),
    )

    assert paper.authorizer.authorises(other.version_id) is False
    with pytest.raises(PortfolioPlanRefused):
        paper.plan.save(
            selected_version_ids=(other.version_id,),
            policy=_plan_policy(other.version_id),
            expected_revision=0,
            operator_reason="Never promoted",
        )
