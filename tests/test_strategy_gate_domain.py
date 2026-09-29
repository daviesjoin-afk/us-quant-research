from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from us_quant.trading.domain.strategy_gate import (
    StrategyEvidenceIdentity,
    StrategyGateBlocker,
    StrategyGateEvaluation,
    StrategyGatePolicy,
    StrategyGateVerdict,
    StrategyResearchEvidence,
    stable_strategy_gate_evaluation_id,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _evidence(**changes):
    values = dict(
        review_run_id="review-1", artifact_review_run_id="review-1",
        robustness_run_id="robust-1", validation_run_id="validation-1",
        overfit_run_id="overfit-1", data_quality_run_id="quality-1",
        execution_stress_run_id="stress-1",
        identity=StrategyEvidenceIdentity("version-1", "1.0.0", "param-1", "AAPL", "data-1", "provider-1"),
        evidence_origins=("captured_stream",), decision="ELIGIBLE_FOR_INDEPENDENT_REVIEW",
        eligible_for_independent_review=True, blocking_failures=0, passed_gates=4,
        gate_count=4, generated_at=NOW,
    )
    values.update(changes)
    return StrategyResearchEvidence(**values)


def _evaluation(blockers=()):
    blockers = tuple(sorted(set(blockers), key=lambda item: item.value))
    return StrategyGateEvaluation(
        evaluation_id="evaluation-1", strategy_version_id="version-1",
        review_run_id="review-1", parameter_hash="param-1", data_hash="data-1",
        symbol="AAPL", provider="provider-1",
        verdict=StrategyGateVerdict.FAIL if blockers else StrategyGateVerdict.PASS,
        blockers=blockers, review_decision="ELIGIBLE_FOR_INDEPENDENT_REVIEW",
        review_blocking_failures=0, review_passed_gates=4, review_gate_count=4,
        evaluator_version="strategy-gate-v1", policy_version="independent-review-v1",
        evaluated_at=NOW,
    )


def test_verdict_and_blocker_are_typed_enums():
    assert StrategyGateVerdict.PASS.value == "PASS"
    assert StrategyGateBlocker.VERSION_ID_MISMATCH.value == "VERSION_ID_MISMATCH"
    with pytest.raises(ValueError):
        StrategyGateVerdict("UNKNOWN")


def test_evaluation_is_immutable_and_requires_aware_time():
    evaluation = _evaluation()
    with pytest.raises(FrozenInstanceError):
        evaluation.verdict = StrategyGateVerdict.FAIL
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(evaluation, evaluated_at=datetime(2026, 1, 1))


def test_identity_rejects_blank_fields():
    with pytest.raises(ValueError, match="symbol"):
        StrategyEvidenceIdentity("v1", "1.0", "p", " ", "d", "provider")


def test_stable_id_is_deterministic_and_uses_required_identities():
    arguments = dict(
        strategy_version_id="v1", review_run_id="r1", parameter_hash="p1",
        data_hash="d1", policy_version="independent-review-v1",
        evaluator_version="strategy-gate-v1", symbol="AAPL",
    )
    assert stable_strategy_gate_evaluation_id(**arguments) == stable_strategy_gate_evaluation_id(**arguments)
    for key in (
        "strategy_version_id", "review_run_id", "parameter_hash", "data_hash",
        "policy_version", "evaluator_version", "symbol",
    ):
        changed = {**arguments, key: f"different-{key}"}
        assert stable_strategy_gate_evaluation_id(**arguments) != stable_strategy_gate_evaluation_id(**changed)


def test_blocker_order_is_canonical_for_different_input_orderings():
    canonical = _evaluation((StrategyGateBlocker.VERSION_ID_MISMATCH, StrategyGateBlocker.STALE_EVIDENCE))
    reversed_input = replace(
        canonical,
        blockers=(StrategyGateBlocker.VERSION_ID_MISMATCH, StrategyGateBlocker.STALE_EVIDENCE),
    )
    assert reversed_input.blockers == canonical.blockers
    assert reversed_input == canonical


def test_policy_versions_and_optional_freshness_are_validated():
    assert StrategyGatePolicy().maximum_evidence_age is None
    with pytest.raises(ValueError):
        StrategyGatePolicy(maximum_evidence_age=timedelta(0))


def test_evidence_is_immutable_and_rejects_bad_counts():
    evidence = _evidence()
    with pytest.raises(FrozenInstanceError):
        evidence.decision = "BLOCKED"
    with pytest.raises(ValueError, match="nonnegative"):
        _evidence(blocking_failures=-1)
