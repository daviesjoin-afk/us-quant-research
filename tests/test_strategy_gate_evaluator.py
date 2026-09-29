from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from us_quant.targeted_review import DependenceDiagnostic, EvidenceGate, TargetedReviewResult
from us_quant.trading.adapters.research_evidence import project_targeted_review
from us_quant.trading.application.strategy_gate import StrategyGateEvaluator
from us_quant.trading.domain.strategy import (
    StrategyDefinition, StrategyIdentity, StrategyMode, StrategyStatus, StrategyVersion,
)
from us_quant.trading.domain.strategy_gate import (
    StrategyEvidenceIdentity, StrategyGateBlocker, StrategyGatePolicy,
    StrategyGateVerdict, StrategyResearchEvidence,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _version(**changes):
    values = dict(
        definition=StrategyDefinition("family-1", "Example", "test"),
        identity=StrategyIdentity("family-1", "version-1", "parameter-1"),
        semver="1.0.0", status=StrategyStatus.RESEARCH, mode=StrategyMode.RESEARCH,
        parameters={"period": 5}, universe_hash="universe", code_hash="code",
        risk_budget_pct=Decimal("0.01"), gate_passed=False, gate_reason="legacy",
        created_at=NOW, updated_at=NOW,
    )
    values.update(changes)
    return StrategyVersion(**values)


def _evidence(**changes):
    values = dict(
        review_run_id="review-1", artifact_review_run_id="review-1",
        robustness_run_id="robust-1", validation_run_id="validation-1",
        overfit_run_id="overfit-1", data_quality_run_id="quality-1",
        execution_stress_run_id="stress-1",
        identity=StrategyEvidenceIdentity("version-1", "1.0.0", "parameter-1", "AAPL", "data-1", "provider-1"),
        evidence_origins=("captured_stream",), decision="ELIGIBLE_FOR_INDEPENDENT_REVIEW",
        eligible_for_independent_review=True, blocking_failures=0, passed_gates=5,
        gate_count=5, generated_at=NOW,
    )
    values.update(changes)
    return StrategyResearchEvidence(**values)


def _review(**changes):
    values = dict(
        run_id="review-1", robustness_run_id="robust-1", validation_run_id="validation-1",
        overfit_run_id="overfit-1", data_quality_run_id="quality-1",
        execution_stress_run_id="stress-1", symbol="AAPL", strategy_version_id="version-1",
        strategy_semver="1.0.0", base_parameter_hash="parameter-1", data_hash="data-1",
        provider="provider-1", evidence_origins=("captured_stream",),
        dependence=DependenceDiagnostic(12, None, None, 0, None, None, None, "pass"),
        gates=(EvidenceGate("g1", "gate", True, "yes", "yes", "evidence"),),
        passed_gates=1, blocking_failures=0, warnings=(),
        decision="ELIGIBLE_FOR_INDEPENDENT_REVIEW", eligible_for_independent_review=True,
        status="PASS",
    )
    values.update(changes)
    return TargetedReviewResult(**values)


def test_valid_evidence_passes_and_projection_uses_targeted_review_fields():
    projected = project_targeted_review(_review(), artifact_review_run_id="review-1", generated_at=NOW)
    result = StrategyGateEvaluator().evaluate(version=_version(), evidence=projected, policy=StrategyGatePolicy(), evaluated_at=NOW)
    assert result.verdict is StrategyGateVerdict.PASS
    assert result.review_run_id == "review-1"
    assert result.review_gate_count == 1


def test_targeted_review_ineligible_flag_is_preserved_by_projection():
    projected = project_targeted_review(
        _review(eligible_for_independent_review=False, decision="BLOCKED"),
        artifact_review_run_id="review-1", generated_at=NOW,
    )
    result = StrategyGateEvaluator().evaluate(
        version=_version(), evidence=projected, policy=StrategyGatePolicy(), evaluated_at=NOW,
    )
    assert not projected.eligible_for_independent_review
    assert StrategyGateBlocker.EVIDENCE_NOT_ELIGIBLE in result.blockers


@pytest.mark.parametrize(("identity_changes", "blocker"), [
    ({"strategy_version_id": "other"}, StrategyGateBlocker.VERSION_ID_MISMATCH),
    ({"strategy_semver": "2.0.0"}, StrategyGateBlocker.STRATEGY_SEMVER_MISMATCH),
    ({"parameter_hash": "other"}, StrategyGateBlocker.PARAMETER_HASH_MISMATCH),
])
def test_version_identity_mismatch_fails(identity_changes, blocker):
    result = StrategyGateEvaluator().evaluate(version=_version(), evidence=_evidence(identity=replace(_evidence().identity, **identity_changes)), policy=StrategyGatePolicy(), evaluated_at=NOW)
    assert result.verdict is StrategyGateVerdict.FAIL
    assert blocker in result.blockers


def test_review_decision_eligibility_blockers_and_origin_fail_closed():
    evaluator = StrategyGateEvaluator()
    cases = (
        (_evidence(decision="BLOCKED"), StrategyGateBlocker.EVIDENCE_NOT_ELIGIBLE),
        (_evidence(eligible_for_independent_review=False), StrategyGateBlocker.EVIDENCE_NOT_ELIGIBLE),
        (_evidence(blocking_failures=1), StrategyGateBlocker.REVIEW_BLOCKING_FAILURES),
        (_evidence(evidence_origins=("synthetic",)), StrategyGateBlocker.EVIDENCE_ORIGIN_INVALID),
    )
    for evidence, expected in cases:
        evaluation = evaluator.evaluate(version=_version(), evidence=evidence, policy=StrategyGatePolicy(), evaluated_at=NOW)
        assert evaluation.verdict is StrategyGateVerdict.FAIL
        assert expected in evaluation.blockers


def test_missing_unreadable_duplicate_stale_and_identity_mismatch_fail():
    evaluator = StrategyGateEvaluator()
    policy = StrategyGatePolicy(maximum_evidence_age=timedelta(days=1))
    assert StrategyGateBlocker.EVIDENCE_MISSING in evaluator.evaluate(version=_version(), evidence=None, policy=policy, evaluated_at=NOW).blockers
    assert StrategyGateBlocker.EVIDENCE_UNREADABLE in evaluator.evaluate(version=_version(), evidence=object(), policy=policy, evaluated_at=NOW).blockers
    assert StrategyGateBlocker.REVIEW_IDENTITY_MISMATCH in evaluator.evaluate(version=_version(), evidence=_evidence(artifact_review_run_id="other"), policy=policy, evaluated_at=NOW).blockers
    duplicate = _evidence(validation_run_id="robust-1")
    assert StrategyGateBlocker.DUPLICATE_EVIDENCE in evaluator.evaluate(version=_version(), evidence=duplicate, policy=policy, evaluated_at=NOW).blockers
    stale = _evidence(generated_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert StrategyGateBlocker.STALE_EVIDENCE in evaluator.evaluate(version=_version(), evidence=stale, policy=policy, evaluated_at=NOW).blockers


def test_anti_reuse_blocks_versions_clones_semver_and_new_parameters():
    evaluator = StrategyGateEvaluator()
    evidence = _evidence()
    cases = (
        (_version(definition=StrategyDefinition("family-2", "Example", "test"), identity=StrategyIdentity("family-2", "version-2", "parameter-1")), StrategyGateBlocker.VERSION_ID_MISMATCH),
        (_version(identity=StrategyIdentity("family-1", "version-2", "parameter-1")), StrategyGateBlocker.VERSION_ID_MISMATCH),
        (_version(semver="2.0.0"), StrategyGateBlocker.STRATEGY_SEMVER_MISMATCH),
        (_version(identity=StrategyIdentity("family-1", "version-1", "new-parameter")), StrategyGateBlocker.PARAMETER_HASH_MISMATCH),
    )
    for version, expected in cases:
        assert expected in evaluator.evaluate(version=version, evidence=evidence, policy=StrategyGatePolicy(), evaluated_at=NOW).blockers


def test_evaluation_does_not_change_strategy_lifecycle_fields():
    evaluator = StrategyGateEvaluator()
    for evidence in (_evidence(), _evidence(decision="BLOCKED")):
        version = _version()
        before = (version.status, version.mode, version.gate_passed)
        evaluator.evaluate(version=version, evidence=evidence, policy=StrategyGatePolicy(), evaluated_at=NOW)
        assert (version.status, version.mode, version.gate_passed) == before


def test_time_dependent_evaluations_have_distinct_stable_ids():
    evaluator = StrategyGateEvaluator()
    first = evaluator.evaluate(version=_version(), evidence=_evidence(), policy=StrategyGatePolicy(), evaluated_at=NOW)
    second = evaluator.evaluate(version=_version(), evidence=_evidence(), policy=StrategyGatePolicy(), evaluated_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert first.evaluation_id != second.evaluation_id
    repeated = evaluator.evaluate(version=_version(), evidence=_evidence(), policy=StrategyGatePolicy(), evaluated_at=NOW)
    assert first.evaluation_id == repeated.evaluation_id


def test_expiration_transition_is_a_distinct_persistable_failure(tmp_path):
    from us_quant.trading.adapters.sqlite.strategy_gate_repository import SQLiteStrategyGateRepository

    evaluator = StrategyGateEvaluator()
    policy = StrategyGatePolicy(maximum_evidence_age=timedelta(days=1))
    version = _version()
    evidence = _evidence()
    before_expiration = evaluator.evaluate(
        version=version, evidence=evidence, policy=policy, evaluated_at=NOW,
    )
    after_expiration = evaluator.evaluate(
        version=version, evidence=evidence, policy=policy,
        evaluated_at=NOW + timedelta(days=2),
    )
    repository = SQLiteStrategyGateRepository(tmp_path / "gate.sqlite3")
    repository.record(before_expiration)
    repository.record(after_expiration)
    latest = repository.latest_for_version(version.version_id)
    assert before_expiration.evaluation_id != after_expiration.evaluation_id
    assert after_expiration.verdict is StrategyGateVerdict.FAIL
    assert StrategyGateBlocker.STALE_EVIDENCE in after_expiration.blockers
    assert latest == after_expiration
