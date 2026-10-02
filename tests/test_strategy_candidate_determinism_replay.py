from datetime import datetime, timedelta, timezone
from decimal import Decimal

from us_quant.trading.adapters.sqlite.strategy_repository import SQLiteStrategyRepository
from us_quant.trading.adapters.sqlite.strategy_search_repository import (
    SQLiteStrategySearchRepository,
)
from us_quant.trading.application.strategies import StrategyApplication
from us_quant.trading.application.strategy_candidate_generation import (
    StrategyCandidateGenerationApplication,
)
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
    parameter_hash_for,
)
from us_quant.trading.domain.strategy_search import (
    SEARCH_POLICY_VERSION,
    StrategySearchParameterRule,
    StrategySearchPolicy,
    StrategySearchValueKind,
    generation_semantic_payload,
)
from us_quant.trading.ports.strategy_repository import StrategyAuditEvent


UTC = timezone.utc
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _parent():
    parameters = {
        "short_window": 20,
        "long_window": 100,
        "whole_shares": True,
    }
    return StrategyVersion(
        definition=StrategyDefinition("dual-ma-trend", "Dual MA", "replay"),
        identity=StrategyIdentity(
            "dual-ma-trend", "explicit-parent-id", parameter_hash_for(parameters)
        ),
        semver="1.0.0", status=StrategyStatus.RESEARCH,
        mode=StrategyMode.RESEARCH, parameters=parameters,
        universe_hash="universe", code_hash="code",
        risk_budget_pct=Decimal("0.01"), gate_passed=False,
        gate_reason="unproven", created_at=NOW, updated_at=NOW,
    )


def _policy():
    return StrategySearchPolicy(
        policy_id="replay-policy", revision=1,
        policy_version=SEARCH_POLICY_VERSION, strategy_id="dual-ma-trend",
        parameter_rules=(StrategySearchParameterRule(
            "short_window", StrategySearchValueKind.INTEGER,
            Decimal(2), Decimal(250), Decimal(5), Decimal(10),
        ),),
        maximum_candidates_per_generation=4,
        maximum_active_candidates=10, maximum_total_candidates=20,
        maximum_generations=5, generation_cooldown=timedelta(0),
        deterministic_seed="replay-seed", created_at=NOW,
    )


def _replay(root, generated_at):
    parent = _parent()
    strategy_repo = SQLiteStrategyRepository(root / "strategies.sqlite3")
    strategy_repo.insert_version(
        parent,
        audit=StrategyAuditEvent(
            strategy_id=parent.strategy_id, version_id=parent.version_id,
            event="registered", detail="replay parent", occurred_at=NOW,
        ),
    )
    strategies = StrategyApplication(strategy_repo)
    search_repo = SQLiteStrategySearchRepository(root / "search.sqlite3")
    search_repo.append_policy_revision(_policy(), expected_current_revision=None)
    generation = StrategyCandidateGenerationApplication(
        strategies, search_repo
    ).generate(
        parent_version_id=parent.version_id, policy_id="replay-policy",
        policy_revision=1, generation=2, generated_at=generated_at,
    )
    children = tuple(
        strategies.get_version(item.child_version_id)
        for item in generation.candidate_lineages
    )
    return generation, children


def test_fresh_sqlite_stores_replay_identical_semantic_generation(tmp_path):
    first, first_children = _replay(tmp_path / "db-a", NOW)
    second, second_children = _replay(tmp_path / "db-b", NOW + timedelta(days=2))

    assert first.generation_id == second.generation_id
    assert tuple(item.version_id for item in first_children) == tuple(
        item.version_id for item in second_children
    )
    assert tuple(item.semver for item in first_children) == tuple(
        item.semver for item in second_children
    )
    assert tuple(item.parameter_hash for item in first_children) == tuple(
        item.parameter_hash for item in second_children
    )
    assert generation_semantic_payload(first) == generation_semantic_payload(second)
