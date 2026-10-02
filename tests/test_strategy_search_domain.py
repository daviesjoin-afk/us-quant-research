from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
    parameter_hash_for,
)
from us_quant.trading.domain.strategy_parameters import validate_strategy_parameters
from us_quant.trading.domain.strategy_search import (
    SEARCH_POLICY_VERSION,
    StrategySearchError,
    StrategySearchParameterRule as Rule,
    StrategySearchPolicy as Policy,
    StrategySearchValueKind as Kind,
    candidate_version_id_for,
    generate_strategy_candidate_specs,
    generation_id_for,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _parent(strategy_id="dual-ma-trend", parameters=None):
    raw = parameters or {
        "short_window": 20,
        "long_window": 100,
        "whole_shares": True,
    }
    normalized = validate_strategy_parameters(strategy_id, raw)
    return StrategyVersion(
        definition=StrategyDefinition(strategy_id, "Example", "search test"),
        identity=StrategyIdentity(
            strategy_id, "parent-version", parameter_hash_for(normalized)
        ),
        semver="1.2.3-research",
        status=StrategyStatus.RESEARCH,
        mode=StrategyMode.RESEARCH,
        parameters=normalized,
        universe_hash="u" * 64,
        code_hash="c" * 64,
        risk_budget_pct=Decimal("0.01"),
        gate_passed=False,
        gate_reason="unverified",
        created_at=NOW,
        updated_at=NOW,
    )


def _rule(key="short_window", kind=Kind.INTEGER, **changes):
    values = dict(
        parameter_key=key,
        value_kind=kind,
        minimum=Decimal("2"),
        maximum=Decimal("250"),
        step=Decimal("5"),
        maximum_delta=Decimal("10"),
    )
    values.update(changes)
    return Rule(**values)


def _policy(*rules, **changes):
    values = dict(
        policy_id="search-policy",
        revision=1,
        policy_version=SEARCH_POLICY_VERSION,
        strategy_id="dual-ma-trend",
        parameter_rules=tuple(rules) if rules else (_rule(),),
        maximum_candidates_per_generation=20,
        maximum_active_candidates=20,
        maximum_total_candidates=100,
        maximum_generations=10,
        generation_cooldown=timedelta(0),
        deterministic_seed="seed-1",
        created_at=NOW,
    )
    values.update(changes)
    return Policy(**values)


def test_rules_require_exact_decimal_bounds_and_positive_movement():
    with pytest.raises(TypeError, match="Decimal"):
        _rule(step=0.5)
    with pytest.raises(ValueError, match="step"):
        _rule(step=Decimal("0"))
    with pytest.raises(ValueError, match="maximum_delta"):
        _rule(maximum_delta=Decimal("0"))
    with pytest.raises(ValueError, match="minimum"):
        _rule(minimum=Decimal("9"), maximum=Decimal("8"))
    with pytest.raises(ValueError, match="integral"):
        _rule(step=Decimal("0.5"))


def test_policy_requires_explicit_limits_and_canonicalizes_rule_order():
    short = _rule("short_window")
    long = _rule(
        "long_window", minimum=Decimal("3"), maximum=Decimal("500"),
        step=Decimal("20"), maximum_delta=Decimal("40"),
    )
    policy = _policy(long, short)
    assert tuple(rule.parameter_key for rule in policy.parameter_rules) == (
        "long_window", "short_window"
    )
    with pytest.raises(ValueError, match="unique"):
        _policy(short, short)
    with pytest.raises(ValueError, match="maximum_generations"):
        _policy(maximum_generations=0)


def test_generator_is_deterministic_and_mutates_one_coordinate_only():
    parent = _parent()
    policy = _policy(_rule(), _rule(
        "long_window", minimum=Decimal("3"), maximum=Decimal("500"),
        step=Decimal("20"), maximum_delta=Decimal("40"),
    ))
    first = generate_strategy_candidate_specs(parent=parent, policy=policy, generation=1)
    second = generate_strategy_candidate_specs(parent=parent, policy=policy, generation=1)
    assert first == second
    assert first
    for candidate in first:
        changed = {
            key for key in parent.parameters
            if candidate.parameters[key] != parent.parameters[key]
        }
        assert len(changed) == 1
        assert changed == {candidate.changed_parameter_key}
        assert candidate.candidate_version_id.startswith("scv-")
        assert candidate.semver.startswith("1.2.3-research-g1-p1-")
        assert candidate.parameters["whole_shares"] is True


def test_parameter_insertion_and_rule_input_order_do_not_change_output():
    parent = _parent(parameters={
        "whole_shares": True,
        "long_window": 100,
        "short_window": 20,
    })
    reordered_parent = _parent(parameters={
        "short_window": 20,
        "whole_shares": True,
        "long_window": 100,
    })
    short = _rule()
    long = _rule("long_window", minimum=Decimal("3"), maximum=Decimal("500"),
                 step=Decimal("20"), maximum_delta=Decimal("40"))
    left = generate_strategy_candidate_specs(
        parent=parent, policy=_policy(short, long), generation=2
    )
    right = generate_strategy_candidate_specs(
        parent=reordered_parent, policy=_policy(long, short), generation=2
    )
    assert left == right


def test_seed_changes_order_and_generation_identity_but_not_candidate_set():
    parent = _parent()
    one = generate_strategy_candidate_specs(
        parent=parent,
        policy=_policy(deterministic_seed="alpha"),
        generation=1,
    )
    two = generate_strategy_candidate_specs(
        parent=parent,
        policy=_policy(deterministic_seed="beta"),
        generation=1,
    )
    assert {item.candidate_version_id for item in one} == {
        item.candidate_version_id for item in two
    }
    assert tuple(item.ordering_digest for item in one) != tuple(
        item.ordering_digest for item in two
    )
    assert generation_id_for(
        strategy_id=parent.strategy_id, parent_version_id=parent.version_id,
        parent_parameter_hash=parent.parameter_hash, policy_id="search-policy",
        policy_revision=1, generation=1, deterministic_seed="alpha",
    ) != generation_id_for(
        strategy_id=parent.strategy_id, parent_version_id=parent.version_id,
        parent_parameter_hash=parent.parameter_hash, policy_id="search-policy",
        policy_revision=1, generation=1, deterministic_seed="beta",
    )


def test_policy_minimum_maximum_delta_and_generation_cap_bound_candidates():
    parent = _parent()
    policy = _policy(_rule(
        minimum=Decimal("15"), maximum=Decimal("25"),
        step=Decimal("5"), maximum_delta=Decimal("5"),
    ), maximum_candidates_per_generation=1)
    specs = generate_strategy_candidate_specs(parent=parent, policy=policy, generation=1)
    assert len(specs) == 1
    assert specs[0].parameters["short_window"] in {15, 25}
    assert abs(specs[0].parameters["short_window"] - 20) <= 5


def test_delta_has_an_independent_absolute_movement_bound():
    parent = _parent()
    policy = _policy(_rule(
        minimum=Decimal("10"), maximum=Decimal("30"),
        step=Decimal("5"), maximum_delta=Decimal("5"),
    ))
    specs = generate_strategy_candidate_specs(parent=parent, policy=policy, generation=1)
    assert {item.parameters["short_window"] for item in specs} == {15, 25}


def test_minimum_and_maximum_each_bound_candidates():
    parent = _parent()
    minimum_policy = _policy(_rule(
        minimum=Decimal("20"), maximum=Decimal("25"),
        step=Decimal("5"), maximum_delta=Decimal("10"),
    ))
    maximum_policy = _policy(_rule(
        minimum=Decimal("15"), maximum=Decimal("20"),
        step=Decimal("5"), maximum_delta=Decimal("10"),
    ))
    assert {item.parameters["short_window"] for item in
            generate_strategy_candidate_specs(parent=parent, policy=minimum_policy, generation=1)} == {25}
    assert {item.parameters["short_window"] for item in
            generate_strategy_candidate_specs(parent=parent, policy=maximum_policy, generation=1)} == {15}


def test_existing_strategy_validator_discards_relationally_invalid_neighbors():
    parent = _parent(parameters={
        "short_window": 20,
        "long_window": 25,
        "whole_shares": True,
    })
    policy = _policy(_rule(
        minimum=Decimal("10"), maximum=Decimal("40"),
        step=Decimal("10"), maximum_delta=Decimal("10"),
    ))
    specs = generate_strategy_candidate_specs(parent=parent, policy=policy, generation=1)
    assert {item.parameters["short_window"] for item in specs} == {10}


def test_rsi_threshold_relation_discards_entry_at_or_above_exit():
    parent = _parent("rsi-mean-reversion", {
        "window": 14,
        "entry_threshold": "20.0",
        "exit_threshold": "21.0",
        "whole_shares": True,
    })
    policy = _policy(
        _rule(
            "entry_threshold", Kind.DECIMAL,
            minimum=Decimal("19"), maximum=Decimal("23"),
            step=Decimal("1"), maximum_delta=Decimal("2"),
        ),
        strategy_id="rsi-mean-reversion",
    )
    candidates = generate_strategy_candidate_specs(
        parent=parent, policy=policy, generation=1
    )
    assert all(
        Decimal(item.parameters["entry_threshold"])
        < Decimal(item.parameters["exit_threshold"])
        for item in candidates
    )
    assert Decimal("22.0") not in {
        Decimal(item.parameters["entry_threshold"]) for item in candidates
    }


def test_decimal_search_uses_decimal_steps_and_existing_normalization():
    parent = _parent("rsi-mean-reversion", {
        "window": 14,
        "entry_threshold": "20.0",
        "exit_threshold": "40.0",
        "whole_shares": True,
    })
    rule = _rule(
        "entry_threshold", Kind.DECIMAL,
        minimum=Decimal("10"), maximum=Decimal("30"),
        step=Decimal("0.1"), maximum_delta=Decimal("0.2"),
    )
    policy = _policy(rule, strategy_id="rsi-mean-reversion")
    specs = generate_strategy_candidate_specs(parent=parent, policy=policy, generation=1)
    assert {item.parameters["entry_threshold"] for item in specs} == {"19.8", "19.9", "20.1", "20.2"}
    assert all(isinstance(item.parameters["entry_threshold"], str) for item in specs)


def test_policy_cannot_silently_ignore_missing_or_non_scalar_parent_parameter():
    parent = _parent("buy-hold", {"whole_shares": True})
    list_policy = _policy(_rule(
        "whole_shares", Kind.INTEGER, minimum=Decimal("0"), maximum=Decimal("2"),
        step=Decimal("1"), maximum_delta=Decimal("1"),
    ), strategy_id="buy-hold")
    with pytest.raises(StrategySearchError, match="INTEGER scalar"):
        generate_strategy_candidate_specs(parent=parent, policy=list_policy, generation=1)
    with pytest.raises(StrategySearchError, match="absent"):
        generate_strategy_candidate_specs(
            parent=_parent(), policy=_policy(_rule("unknown")), generation=1
        )


def test_wrong_strategy_and_generation_bounds_refuse():
    with pytest.raises(StrategySearchError, match="another strategy"):
        generate_strategy_candidate_specs(
            parent=_parent(), policy=_policy(strategy_id="buy-hold"), generation=1
        )
    with pytest.raises(StrategySearchError, match="generation"):
        generate_strategy_candidate_specs(
            parent=_parent(), policy=_policy(maximum_generations=2), generation=3
        )


def test_parent_parameter_hash_must_match_its_immutable_parameters():
    parent = _parent()
    bad_parent = StrategyVersion(
        definition=parent.definition,
        identity=StrategyIdentity(parent.strategy_id, parent.version_id, "f" * 64),
        semver=parent.semver,
        status=parent.status,
        mode=parent.mode,
        parameters=parent.parameters,
        universe_hash=parent.universe_hash,
        code_hash=parent.code_hash,
        risk_budget_pct=parent.risk_budget_pct,
        gate_passed=parent.gate_passed,
        gate_reason=parent.gate_reason,
        created_at=parent.created_at,
        updated_at=parent.updated_at,
    )
    with pytest.raises(StrategySearchError, match="hash does not match"):
        generate_strategy_candidate_specs(
            parent=bad_parent, policy=_policy(), generation=1
        )


def test_zero_legal_neighbors_is_a_valid_empty_result():
    parent = _parent()
    policy = _policy(_rule(
        minimum=Decimal("20"), maximum=Decimal("20"),
        step=Decimal("5"), maximum_delta=Decimal("5"),
    ))
    assert generate_strategy_candidate_specs(parent=parent, policy=policy, generation=1) == ()


def test_duplicate_normalized_parameter_hash_is_emitted_once(monkeypatch):
    parent = _parent()
    policy = _policy(
        _rule(maximum_delta=Decimal("10")),
        _rule(
            "long_window", minimum=Decimal("3"), maximum=Decimal("500"),
            step=Decimal("20"), maximum_delta=Decimal("40"),
        ),
    )
    normalized = dict(parent.parameters, short_window=21)
    monkeypatch.setattr(
        "us_quant.trading.domain.strategy_search.validate_strategy_parameters",
        lambda _strategy_id, _parameters: normalized,
    )
    candidates = generate_strategy_candidate_specs(
        parent=parent, policy=policy, generation=1
    )
    assert len(candidates) == 1


def test_parent_equal_normalized_candidate_is_discarded(monkeypatch):
    parent = _parent()
    policy = _policy()
    monkeypatch.setattr(
        "us_quant.trading.domain.strategy_search.validate_strategy_parameters",
        lambda _strategy_id, _parameters: parent.parameters,
    )
    assert generate_strategy_candidate_specs(
        parent=parent, policy=policy, generation=1
    ) == ()


@pytest.mark.parametrize("field", [
    "parent_version_id", "parent_parameter_hash", "policy_id",
    "policy_revision", "generation", "candidate_parameter_hash",
])
def test_candidate_identity_changes_when_any_bound_field_changes(field):
    values = dict(
        parent_version_id="parent-version", parent_parameter_hash="a" * 64,
        policy_id="search-policy", policy_revision=1, generation=1,
        candidate_parameter_hash="b" * 64,
    )
    changed = dict(values)
    changed[field] = (
        values[field] + "-changed" if isinstance(values[field], str)
        else values[field] + 1
    )
    assert candidate_version_id_for(**values) != candidate_version_id_for(**changed)


def test_candidate_identity_source_does_not_bind_ordinal_or_clock():
    import inspect

    source = inspect.getsource(candidate_version_id_for)
    assert "ordinal" not in source
    assert "datetime" not in source


def test_generation_identity_changes_for_parent_policy_or_generation():
    values = dict(
        strategy_id="dual-ma-trend", parent_version_id="parent-version",
        parent_parameter_hash="a" * 64, policy_id="search-policy",
        policy_revision=1, generation=1, deterministic_seed="seed-1",
    )
    original = generation_id_for(**values)
    for key, replacement in (
        ("parent_version_id", "another-parent"),
        ("parent_parameter_hash", "c" * 64),
        ("policy_revision", 2),
        ("generation", 2),
    ):
        changed = dict(values, **{key: replacement})
        assert generation_id_for(**changed) != original


def test_candidate_id_does_not_take_ordinal_or_clock():
    values = dict(
        parent_version_id="parent-version", parent_parameter_hash="a" * 64,
        policy_id="search-policy", policy_revision=1, generation=1,
        candidate_parameter_hash="b" * 64,
    )
    first = candidate_version_id_for(**values)
    assert candidate_version_id_for(**values) == first
    assert len(first) == 68
