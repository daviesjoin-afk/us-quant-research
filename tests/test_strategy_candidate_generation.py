from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from us_quant.trading.adapters.sqlite.strategy_repository import SQLiteStrategyRepository
from us_quant.trading.adapters.sqlite.strategy_search_repository import (
    SQLiteStrategySearchRepository,
)
from us_quant.trading.application.strategies import (
    StrategyApplication,
    StrategyApplicationError,
)
from us_quant.trading.application.strategy_candidate_generation import (
    StrategyCandidateGenerationApplication,
    StrategyCandidateGenerationError,
)
from us_quant.trading.domain.strategy import StrategyStatus
from us_quant.trading.domain.strategy_search import (
    SEARCH_POLICY_VERSION,
    StrategySearchParameterRule,
    StrategySearchPolicy,
    StrategySearchValueKind,
    generate_strategy_candidate_specs,
)


UTC = timezone.utc
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _policy(**changes):
    values = dict(
        policy_id="search-dual-ma",
        revision=1,
        policy_version=SEARCH_POLICY_VERSION,
        strategy_id="dual-ma-trend",
        parameter_rules=(
            StrategySearchParameterRule(
                "short_window", StrategySearchValueKind.INTEGER,
                Decimal(2), Decimal(250), Decimal(5), Decimal(10),
            ),
        ),
        maximum_candidates_per_generation=4,
        maximum_active_candidates=10,
        maximum_total_candidates=20,
        maximum_generations=5,
        generation_cooldown=timedelta(seconds=60),
        deterministic_seed="stable-seed",
        created_at=NOW,
    )
    values.update(changes)
    return StrategySearchPolicy(**values)


@pytest.fixture
def setup(tmp_path):
    strategy_repo = SQLiteStrategyRepository(tmp_path / "strategies.sqlite3")
    strategies = StrategyApplication(strategy_repo)
    parent = strategies.register(
        strategy_id="dual-ma-trend", name="Dual MA", description="test",
        semver="1.0.0", parameters={
            "short_window": 20, "long_window": 100, "whole_shares": True,
        }, universe_hash="u", code_hash="c", risk_budget_pct=Decimal("0.01"),
    )
    search_repo = SQLiteStrategySearchRepository(tmp_path / "search.sqlite3")
    search_repo.append_policy_revision(_policy(), expected_current_revision=None)
    app = StrategyCandidateGenerationApplication(strategies, search_repo)
    return strategies, search_repo, app, parent


def test_generate_persists_ordered_unproven_candidates(setup):
    strategies, search_repo, app, parent = setup
    original = parent
    result = app.generate(
        parent_version_id=parent.version_id, policy_id="search-dual-ma",
        policy_revision=1, generation=1, generated_at=NOW,
    )
    assert result.candidate_lineages
    assert tuple(lineage.ordinal for lineage in result.candidate_lineages) == tuple(
        range(1, len(result.candidate_lineages) + 1)
    )
    for lineage in result.candidate_lineages:
        child = strategies.get_version(lineage.child_version_id)
        assert child.status is StrategyStatus.RESEARCH
        assert child.mode.value == "research"
        assert child.gate_passed is False
        assert child.parameter_hash != parent.parameter_hash
        assert child.universe_hash == parent.universe_hash
        assert child.code_hash == parent.code_hash
        assert child.risk_budget_pct == parent.risk_budget_pct
        assert sum(
            child.parameters[key] != parent.parameters[key]
            for key in parent.parameters
        ) == 1
    assert strategies.get_version(parent.version_id) == original
    assert search_repo.get_generation(result.generation_id) == result


def test_exact_retry_uses_stored_generation_before_cooldown(setup):
    _, search_repo, app, parent = setup
    first = app.generate(
        parent_version_id=parent.version_id, policy_id="search-dual-ma",
        policy_revision=1, generation=1, generated_at=NOW,
    )
    replay = app.generate(
        parent_version_id=parent.version_id, policy_id="search-dual-ma",
        policy_revision=1, generation=1, generated_at=NOW + timedelta(seconds=1),
    )
    assert replay == first
    assert len(search_repo.generations_for_policy("dual-ma-trend", "search-dual-ma")) == 1


def test_cooldown_and_generation_bounds_are_enforced(setup):
    _, search_repo, app, parent = setup
    app.generate(
        parent_version_id=parent.version_id, policy_id="search-dual-ma",
        policy_revision=1, generation=1, generated_at=NOW,
    )
    with pytest.raises(StrategyCandidateGenerationError, match="cooldown"):
        app.generate(
            parent_version_id=parent.version_id, policy_id="search-dual-ma",
            policy_revision=1, generation=2, generated_at=NOW + timedelta(seconds=30),
        )
    with pytest.raises(StrategyCandidateGenerationError, match="generation"):
        app.generate(
            parent_version_id=parent.version_id, policy_id="search-dual-ma",
            policy_revision=1, generation=6, generated_at=NOW + timedelta(minutes=2),
        )
    assert len(search_repo.generations_for_policy("dual-ma-trend", "search-dual-ma")) == 1


@pytest.mark.parametrize(
    "policy_changes, message",
    [
        ({"maximum_active_candidates": 1}, "maximum_active"),
        ({"maximum_total_candidates": 1}, "maximum_total"),
    ],
)
def test_resource_bounds_refuse_before_any_child_is_created(
    setup, policy_changes, message
):
    strategies, repo, app, parent = setup
    repo.append_policy_revision(
        _policy(revision=2, **policy_changes), expected_current_revision=1
    )
    with pytest.raises(StrategyCandidateGenerationError, match=message):
        app.generate(
            parent_version_id=parent.version_id, policy_id="search-dual-ma",
            policy_revision=2, generation=1, generated_at=NOW,
        )
    assert strategies.list_versions() == (parent,)


def test_crash_after_first_child_recovers_without_duplicate_versions(setup, monkeypatch):
    strategies, repo, app, parent = setup
    original = strategies.clone_research_candidate
    calls = 0

    def fail_on_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise StrategyApplicationError("simulated process crash")
        return original(*args, **kwargs)

    monkeypatch.setattr(strategies, "clone_research_candidate", fail_on_second)
    with pytest.raises(StrategyCandidateGenerationError, match="materialization failed"):
        app.generate(
            parent_version_id=parent.version_id, policy_id="search-dual-ma",
            policy_revision=1, generation=1, generated_at=NOW,
        )
    assert len(strategies.list_versions()) == 2
    monkeypatch.setattr(strategies, "clone_research_candidate", original)
    result = app.generate(
        parent_version_id=parent.version_id, policy_id="search-dual-ma",
        policy_revision=1, generation=1, generated_at=NOW + timedelta(seconds=1),
    )
    assert len(strategies.list_versions()) == 1 + len(result.candidate_lineages)
    assert repo.get_generation(result.generation_id) == result


def test_completed_retry_ignores_child_lifecycle_status(setup):
    strategies, _, app, parent = setup
    first = app.generate(
        parent_version_id=parent.version_id, policy_id="search-dual-ma",
        policy_revision=1, generation=1, generated_at=NOW,
    )
    child_id = first.candidate_lineages[0].child_version_id
    strategies.transition(child_id, StrategyStatus.STOPPED, reason="test")
    assert app.generate(
        parent_version_id=parent.version_id, policy_id="search-dual-ma",
        policy_revision=1, generation=1, generated_at=NOW + timedelta(days=1),
    ) == first


def test_naive_generated_at_is_rejected(setup):
    _, _, app, parent = setup
    with pytest.raises(StrategyCandidateGenerationError, match="timezone-aware"):
        app.generate(
            parent_version_id=parent.version_id, policy_id="search-dual-ma",
            policy_revision=1, generation=1, generated_at=datetime(2026, 1, 1),
        )


def test_zero_candidate_generation_is_durable(setup):
    _, repo, app, parent = setup
    repo.append_policy_revision(
        _policy(
            revision=2,
            parameter_rules=(StrategySearchParameterRule(
                "short_window", StrategySearchValueKind.INTEGER,
                Decimal(20), Decimal(20), Decimal(5), Decimal(5),
            ),),
        ),
        expected_current_revision=1,
    )
    result = app.generate(
        parent_version_id=parent.version_id, policy_id="search-dual-ma",
        policy_revision=2, generation=1, generated_at=NOW,
    )
    assert result.candidate_lineages == ()
    assert repo.get_generation(result.generation_id) == result


def test_explicit_policy_revision_is_used_even_after_a_new_revision_is_active(setup):
    _, repo, app, parent = setup
    repo.append_policy_revision(
        _policy(
            revision=2,
            deterministic_seed="new-seed",
            maximum_candidates_per_generation=1,
        ),
        expected_current_revision=1,
    )
    old_revision = app.generate(
        parent_version_id=parent.version_id, policy_id="search-dual-ma",
        policy_revision=1, generation=1, generated_at=NOW,
    )
    new_revision = app.generate(
        parent_version_id=parent.version_id, policy_id="search-dual-ma",
        policy_revision=2, generation=1, generated_at=NOW + timedelta(minutes=2),
    )
    assert old_revision.policy_revision == 1
    assert len(old_revision.candidate_lineages) == 4
    assert new_revision.policy_revision == 2
    assert len(new_revision.candidate_lineages) == 1


def test_existing_deterministic_identity_with_mismatched_child_fails_closed(setup):
    strategies, _, app, parent = setup
    spec = generate_strategy_candidate_specs(
        parent=parent, policy=_policy(), generation=1
    )[0]
    strategies.clone_research_candidate(
        parent.version_id,
        candidate_version_id=spec.candidate_version_id,
        semver=spec.semver,
        parameters={"short_window": 21, "long_window": 100, "whole_shares": True},
    )
    with pytest.raises(StrategyCandidateGenerationError, match="different or proven"):
        app.generate(
            parent_version_id=parent.version_id, policy_id="search-dual-ma",
            policy_revision=1, generation=1, generated_at=NOW,
        )
