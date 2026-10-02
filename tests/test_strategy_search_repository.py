from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

import pytest

from us_quant.trading.adapters.sqlite.strategy_search_repository import (
    SQLiteStrategySearchRepository,
)
from us_quant.trading.domain.strategy_search import (
    SEARCH_POLICY_VERSION,
    STRATEGY_CANDIDATE_GENERATOR_VERSION,
    StrategyCandidateLineage,
    StrategySearchGeneration,
    StrategySearchParameterRule,
    StrategySearchPolicy,
    StrategySearchValueKind,
    candidate_version_id_for,
    generation_id_for,
)
from us_quant.trading.ports.strategy_search_repository import (
    StrategySearchRepositoryConflict,
    StrategySearchRepositoryError,
)


UTC = timezone.utc
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _policy(revision=1, **changes):
    values = dict(
        policy_id="policy-a",
        revision=revision,
        policy_version=SEARCH_POLICY_VERSION,
        strategy_id="family-a",
        parameter_rules=(
            StrategySearchParameterRule(
                "window", StrategySearchValueKind.INTEGER,
                Decimal(2), Decimal(20), Decimal(2), Decimal(4),
            ),
        ),
        maximum_candidates_per_generation=4,
        maximum_active_candidates=8,
        maximum_total_candidates=20,
        maximum_generations=5,
        generation_cooldown=timedelta(seconds=30),
        deterministic_seed="seed-a",
        created_at=NOW,
    )
    values.update(changes)
    return StrategySearchPolicy(**values)


def _generation(*, generated_at=NOW, lineages=True):
    generation_id = generation_id_for(
        strategy_id="family-a", parent_version_id="parent-a",
        parent_parameter_hash="a" * 64, policy_id="policy-a",
        policy_revision=1, generation=1, deterministic_seed="seed-a",
    )
    candidates = ()
    if lineages:
        child_id = candidate_version_id_for(
            parent_version_id="parent-a", parent_parameter_hash="a" * 64,
            policy_id="policy-a", policy_revision=1, generation=1,
            candidate_parameter_hash="b" * 64,
        )
        candidates = (
            StrategyCandidateLineage(
                child_version_id=child_id,
                parent_version_id="parent-a", strategy_id="family-a",
                generation=1, ordinal=1, policy_id="policy-a", policy_revision=1,
                policy_version=SEARCH_POLICY_VERSION,
                parent_parameter_hash="a" * 64,
                candidate_parameter_hash="b" * 64,
                changed_parameter_key="window", parent_value="10",
                candidate_value="12", generation_id=generation_id,
                generator_version=STRATEGY_CANDIDATE_GENERATOR_VERSION,
                created_at=generated_at,
            ),
        )
    return StrategySearchGeneration(
        generation_id=generation_id, strategy_id="family-a",
        parent_version_id="parent-a", parent_parameter_hash="a" * 64,
        generation=1, policy_id="policy-a", policy_revision=1,
        policy_version=SEARCH_POLICY_VERSION, deterministic_seed="seed-a",
        candidate_lineages=candidates,
        generator_version=STRATEGY_CANDIDATE_GENERATOR_VERSION,
        generated_at=generated_at,
    )


def test_policy_append_is_compare_and_set_and_revisions_are_immutable(tmp_path):
    repo = SQLiteStrategySearchRepository(tmp_path / "search.sqlite3")
    repo.append_policy_revision(_policy(), expected_current_revision=None)
    assert repo.active_policy("policy-a") == _policy()
    with pytest.raises(StrategySearchRepositoryConflict):
        repo.append_policy_revision(_policy(2), expected_current_revision=None)
    changed = _policy(2, deterministic_seed="new-seed")
    repo.append_policy_revision(changed, expected_current_revision=1)
    assert repo.get_policy("policy-a", 1) == _policy()
    assert repo.active_policy("policy-a") == changed
    with pytest.raises(StrategySearchRepositoryConflict):
        repo.append_policy_revision(_policy(3), expected_current_revision=1)


def test_generation_is_durable_idempotent_and_timestamp_independent(tmp_path):
    path = tmp_path / "search.sqlite3"
    repo = SQLiteStrategySearchRepository(path)
    first = _generation()
    assert repo.record_generation(first) == first
    restarted = SQLiteStrategySearchRepository(path)
    same_semantics_later = _generation(
        generated_at=NOW + timedelta(hours=2)
    )
    stored = restarted.record_generation(same_semantics_later)
    assert stored == first
    assert restarted.get_generation(first.generation_id) == first
    assert restarted.generations_for_policy("family-a", "policy-a") == (first,)


def test_same_generation_id_with_other_lineage_semantics_conflicts(tmp_path):
    repo = SQLiteStrategySearchRepository(tmp_path / "search.sqlite3")
    first = _generation()
    repo.record_generation(first)
    changed = StrategySearchGeneration(
        generation_id=first.generation_id,
        strategy_id=first.strategy_id,
        parent_version_id=first.parent_version_id,
        parent_parameter_hash=first.parent_parameter_hash,
        generation=first.generation,
        policy_id=first.policy_id,
        policy_revision=first.policy_revision,
        policy_version=first.policy_version,
        deterministic_seed=first.deterministic_seed,
        candidate_lineages=(),
        generator_version=first.generator_version,
        generated_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(StrategySearchRepositoryConflict, match="different semantics"):
        repo.record_generation(changed)


def test_empty_generation_is_a_durable_fact(tmp_path):
    repo = SQLiteStrategySearchRepository(tmp_path / "search.sqlite3")
    empty = _generation(lineages=False)
    repo.record_generation(empty)
    assert repo.get_generation(empty.generation_id).candidate_lineages == ()


def test_policy_history_gap_fails_closed(tmp_path):
    repo = SQLiteStrategySearchRepository(tmp_path / "search.sqlite3")
    repo.append_policy_revision(_policy(), expected_current_revision=None)
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            "INSERT INTO strategy_search_policy VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("policy-a", 3, SEARCH_POLICY_VERSION, "family-a", NOW.isoformat(), "{}", "x"),
        )
    with pytest.raises(StrategySearchRepositoryError, match="gap"):
        repo.active_policy("policy-a")


def test_payload_hash_and_indexed_identity_corruption_fail_closed(tmp_path):
    repo = SQLiteStrategySearchRepository(tmp_path / "search.sqlite3")
    repo.append_policy_revision(_policy(), expected_current_revision=None)
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            "UPDATE strategy_search_policy SET strategy_id = 'tampered'"
        )
    with pytest.raises(StrategySearchRepositoryError, match="indexed policy"):
        repo.get_policy("policy-a", 1)

    generation = _generation()
    repo.record_generation(generation)
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            "UPDATE strategy_search_generation SET payload_hash = 'bad'"
        )
    with pytest.raises(StrategySearchRepositoryError, match="hash mismatch"):
        repo.get_generation(generation.generation_id)
