"""Stage 6-B2 domain: coverage policy, items, evaluation identity and codec."""

from dataclasses import MISSING, fields
from datetime import datetime, timedelta, timezone
import json

import pytest

from us_quant.trading.domain.strategy_coverage import (
    COVERAGE_EVALUATOR_VERSION,
    CoveragePolicyMalformed,
    StrategyCoverageBlocker as Blocker,
    StrategyCoverageEvaluation,
    StrategyCoverageItem,
    StrategyCoveragePolicy,
    StrategyCoverageVerdict as Verdict,
    policy_from_payload,
    policy_to_payload,
    stable_strategy_coverage_evaluation_id,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _policy(**changes):
    values = dict(
        policy_id="coverage-policy-1", revision=1,
        policy_version="evidence-coverage-v1", required_symbols=("AAPL", "MSFT"),
        required_universe_hash="universe-1", required_code_hash="code-1",
        min_distinct_review_runs=2, min_distinct_data_hashes=2,
        maximum_evidence_age=timedelta(days=30), created_at=NOW,
    )
    values.update(changes)
    return StrategyCoveragePolicy(**values)


def _item(**changes):
    values = dict(
        symbol="AAPL", review_run_id="review-1", data_hash="data-1", key_id="key-1",
        authentication_id="sea-1", gate_evaluation_id="sge-1",
        signed_at=NOW, generated_at=NOW,
    )
    values.update(changes)
    return StrategyCoverageItem(**values)


def _evaluation(**changes):
    values = dict(
        evaluation_id="sce-1", strategy_version_id="version-1",
        strategy_semver="1.0.0", parameter_hash="parameter-1",
        universe_hash="universe-1", code_hash="code-1",
        policy_id="coverage-policy-1", policy_revision=1,
        policy_version="evidence-coverage-v1", verdict=Verdict.PASS, blockers=(),
        items=(_item(),), covered_symbols=("AAPL",), required_symbols=("AAPL", "MSFT"),
        distinct_review_runs=1, distinct_data_hashes=1,
        evaluator_version=COVERAGE_EVALUATOR_VERSION, evaluated_at=NOW,
    )
    values.update(changes)
    return StrategyCoverageEvaluation(**values)


# -- no permissive default ----------------------------------------------


def test_every_policy_field_is_required(tmp_path=None):
    """No field may carry a default: a default is a threshold nobody chose.

    This is the structural half of "a default policy authorises nothing" -- if
    a field could be omitted, omitting it would silently widen the claim.
    """

    defaults = {
        field.name: field.default
        for field in fields(StrategyCoveragePolicy)
        if field.default is not MISSING
    }

    assert defaults == {}


def test_policy_module_defines_no_authorising_default_instance():
    """There is no ready-made policy object anyone could reach for instead.

    Checked by type rather than by name: a constant called ``DEFAULT_POLICY`` is
    the obvious way to reintroduce a threshold nobody chose, but any spelling of
    it is caught here.
    """

    import us_quant.trading.domain.strategy_coverage as module

    instances = [
        name
        for name in dir(module)
        if isinstance(getattr(module, name), StrategyCoveragePolicy)
    ]

    assert instances == []


# -- policy validation ---------------------------------------------------


def test_policy_rejects_an_empty_required_symbol_universe():
    with pytest.raises(ValueError):
        _policy(required_symbols=())


def test_policy_rejects_repeated_required_symbols():
    with pytest.raises(ValueError):
        _policy(required_symbols=("AAPL", "AAPL"))


def test_policy_rejects_a_nonpositive_revision():
    for revision in (0, -1):
        with pytest.raises(ValueError):
            _policy(revision=revision)


@pytest.mark.parametrize(
    "name", ["min_distinct_review_runs", "min_distinct_data_hashes"]
)
def test_policy_rejects_a_zero_minimum(name):
    """A zero minimum would let a single artifact satisfy it vacuously."""

    for value in (0, -1):
        with pytest.raises(ValueError):
            _policy(**{name: value})


def test_policy_rejects_a_nonpositive_age_bound():
    with pytest.raises(ValueError):
        _policy(maximum_evidence_age=timedelta(0))


def test_policy_accepts_no_age_bound():
    assert _policy(maximum_evidence_age=None).maximum_evidence_age is None


def test_policy_requires_timezone_aware_creation():
    with pytest.raises(ValueError):
        _policy(created_at=datetime(2026, 1, 1))


def test_policy_identity_is_id_and_revision():
    assert _policy().identity == ("coverage-policy-1", 1)
    assert _policy(revision=4).identity == ("coverage-policy-1", 4)


# -- policy codec --------------------------------------------------------


def test_policy_payload_roundtrip():
    policy = _policy(maximum_evidence_age=timedelta(hours=6))

    restored = policy_from_payload(json.loads(json.dumps(policy_to_payload(policy))))

    assert restored == policy
    assert restored.maximum_evidence_age == timedelta(hours=6)


def test_policy_payload_roundtrip_without_age_bound():
    policy = _policy(maximum_evidence_age=None)

    assert policy_from_payload(policy_to_payload(policy)) == policy


def test_policy_payload_records_the_bound_in_seconds():
    payload = policy_to_payload(_policy(maximum_evidence_age=timedelta(days=2)))

    assert payload["maximum_evidence_age_seconds"] == 172800


@pytest.mark.parametrize(
    "payload",
    [
        "not-an-object",
        [],
        {},
        {"required_symbols": "AAPL"},
        {"required_symbols": [], "maximum_evidence_age_seconds": 0},
    ],
)
def test_policy_codec_refuses_malformed_payloads(payload):
    with pytest.raises(CoveragePolicyMalformed):
        policy_from_payload(payload)


def test_policy_codec_refuses_a_missing_field():
    payload = policy_to_payload(_policy())
    payload.pop("required_code_hash")

    with pytest.raises(CoveragePolicyMalformed):
        policy_from_payload(payload)


def test_policy_codec_refuses_an_unparseable_created_at():
    payload = policy_to_payload(_policy())
    payload["created_at"] = "yesterday"

    with pytest.raises(CoveragePolicyMalformed):
        policy_from_payload(payload)


# -- item and evaluation invariants -------------------------------------


def test_item_requires_aware_timestamps():
    with pytest.raises(ValueError):
        _item(signed_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        _item(generated_at=datetime(2026, 1, 1))


def test_item_identity_is_review_and_authentication():
    assert _item().identity == ("review-1", "sea-1")


def test_pass_requires_a_policy_and_items():
    with pytest.raises(ValueError):
        _evaluation(policy_id=None, policy_revision=None, policy_version=None)
    with pytest.raises(ValueError):
        _evaluation(items=())


def test_fail_requires_a_blocker():
    with pytest.raises(ValueError):
        _evaluation(verdict=Verdict.FAIL, blockers=())


def test_blockers_are_canonicalised_and_deduplicated():
    evaluation = _evaluation(
        verdict=Verdict.FAIL,
        blockers=(Blocker.STALE_EVIDENCE, Blocker.GATE_NOT_PASSED, Blocker.STALE_EVIDENCE),
    )

    assert evaluation.blockers == (Blocker.GATE_NOT_PASSED, Blocker.STALE_EVIDENCE)


def test_evaluation_exposes_the_exact_evidence_ids():
    evaluation = _evaluation(
        items=(
            _item(review_run_id="review-1", authentication_id="sea-1", gate_evaluation_id="sge-1"),
            _item(review_run_id="review-2", authentication_id="sea-2", gate_evaluation_id="sge-2"),
        )
    )

    assert evaluation.authentication_ids == ("sea-1", "sea-2")
    assert evaluation.gate_evaluation_ids == ("sge-1", "sge-2")


def test_evaluation_requires_timezone_aware_evaluation():
    with pytest.raises(ValueError):
        _evaluation(evaluated_at=datetime(2026, 1, 1))


# -- evaluation identity -------------------------------------------------


def _identity_material(**changes):
    values = dict(
        strategy_version_id="version-1", strategy_semver="1.0.0",
        parameter_hash="parameter-1", universe_hash="universe-1", code_hash="code-1",
        policy_id="coverage-policy-1", policy_revision=1,
        policy_version="evidence-coverage-v1", verdict=Verdict.PASS, blockers=(),
        items=(_item(),), evaluator_version=COVERAGE_EVALUATOR_VERSION,
    )
    values.update(changes)
    return values


def test_evaluation_identity_is_stable_and_excludes_the_clock():
    assert stable_strategy_coverage_evaluation_id(
        **_identity_material()
    ) == stable_strategy_coverage_evaluation_id(**_identity_material())


@pytest.mark.parametrize(
    "field, value",
    [
        ("strategy_version_id", "version-2"),
        ("strategy_semver", "2.0.0"),
        ("parameter_hash", "parameter-2"),
        ("universe_hash", "universe-2"),
        ("code_hash", "code-2"),
        ("policy_id", "coverage-policy-2"),
        ("policy_revision", 2),
        ("policy_version", "evidence-coverage-v2"),
        ("evaluator_version", "strategy-coverage-v2"),
    ],
)
def test_evaluation_identity_moves_with_every_identity_field(field, value):
    assert stable_strategy_coverage_evaluation_id(
        **_identity_material()
    ) != stable_strategy_coverage_evaluation_id(**_identity_material(**{field: value}))


def test_evaluation_identity_moves_with_the_admitted_evidence():
    baseline = stable_strategy_coverage_evaluation_id(**_identity_material())

    assert baseline != stable_strategy_coverage_evaluation_id(
        **_identity_material(items=(_item(review_run_id="review-2"),))
    )
    assert baseline != stable_strategy_coverage_evaluation_id(
        **_identity_material(items=(_item(authentication_id="sea-2"),))
    )


def test_evaluation_identity_moves_with_the_verdict_and_blockers():
    baseline = stable_strategy_coverage_evaluation_id(**_identity_material())

    assert baseline != stable_strategy_coverage_evaluation_id(
        **_identity_material(verdict=Verdict.FAIL, blockers=(Blocker.EVIDENCE_MISSING,))
    )


def test_two_failures_with_different_reasons_are_different_claims():
    """Same verdict, different reason: the audit trail must tell them apart."""

    evidence_missing = stable_strategy_coverage_evaluation_id(
        **_identity_material(
            verdict=Verdict.FAIL, blockers=(Blocker.EVIDENCE_MISSING,)
        )
    )
    gate_not_passed = stable_strategy_coverage_evaluation_id(
        **_identity_material(
            verdict=Verdict.FAIL, blockers=(Blocker.GATE_NOT_PASSED,)
        )
    )

    assert evidence_missing != gate_not_passed


def test_evidence_order_does_not_change_the_identity():
    """A set of evidence is a set: submission order is not part of the claim."""

    first = _item(review_run_id="review-1", authentication_id="sea-1")
    second = _item(review_run_id="review-2", authentication_id="sea-2")

    assert stable_strategy_coverage_evaluation_id(
        **_identity_material(items=(first, second))
    ) == stable_strategy_coverage_evaluation_id(
        **_identity_material(items=(second, first))
    )
