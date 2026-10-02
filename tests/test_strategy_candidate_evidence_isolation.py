from datetime import timedelta
from decimal import Decimal
import sqlite3

import test_strategy_lifecycle_evaluator as lifecycle_fixtures
from test_strategy_lifecycle_evaluator import (
    NOW,
    _Chain,
    _controller,
    _policy as lifecycle_policy,
    _version,
)
from test_strategy_paper_performance_lifecycle import _fact
from paper_performance_support import policy as performance_policy
from us_quant.trading.adapters.sqlite.strategy_coverage_repository import (
    SQLiteStrategyCoverageRepository,
)
from us_quant.trading.adapters.sqlite.strategy_search_repository import (
    SQLiteStrategySearchRepository,
)
from us_quant.trading.application.strategy_candidate_generation import (
    StrategyCandidateGenerationApplication,
)
from us_quant.trading.application.strategy_lifecycle import StrategyLifecycleService
from us_quant.trading.domain.strategy_lifecycle import StrategyLifecycleAction
from us_quant.trading.domain.strategy_lifecycle import StrategyLifecycleDecisionState
from us_quant.trading.domain.strategy import parameter_hash_for
from us_quant.trading.domain.strategy_search import (
    SEARCH_POLICY_VERSION,
    StrategySearchParameterRule,
    StrategySearchPolicy,
    StrategySearchValueKind,
)


def test_candidate_receives_none_of_parent_sqlite_proofs(tmp_path, monkeypatch):
    # The shared lifecycle fixture predates canonical parameter hashes and uses
    # a readable placeholder. Use the real hash here so the lineage invariant
    # is exercised without weakening the production identity contract.
    monkeypatch.setattr(
        lifecycle_fixtures, "PARAMETER_HASH", parameter_hash_for({"period": 5})
    )
    chain = _Chain(tmp_path).build(version=_version())
    service, strategies, decisions, performance = _performance_service(tmp_path, chain)
    parent = strategies.get_version("version-1")

    coverage = SQLiteStrategyCoverageRepository(tmp_path / "coverage.sqlite3")
    coverage.append_policy_revision(
        _coverage_policy_from_chain(chain), expected_current_revision=None
    )
    coverage.record_evaluation(chain.coverage)

    performance.append_policy_revision(
        performance_policy(), expected_current_revision=None
    )
    performance_fact = _fact(tmp_path / "pass-fact", kind=_pass_verdict())
    performance.record_evaluation(performance_fact)

    applied = service.apply(
        version=parent,
        action=StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
        policy=lifecycle_policy(paper_performance_policy_id="paper-policy"),
        coverage=chain.coverage,
        applied_at=NOW,
    )
    assert applied.decision.state is StrategyLifecycleDecisionState.APPLIED
    assert strategies.get_version(parent.version_id).status.value == "paper_shadow"
    assert decisions.get_decision(applied.decision.decision_id) == applied.decision
    assert performance.evaluations_for_version(parent.version_id) == (performance_fact,)

    search = SQLiteStrategySearchRepository(tmp_path / "search.sqlite3")
    rule = StrategySearchParameterRule(
        "period", StrategySearchValueKind.INTEGER,
        Decimal(1), Decimal(10), Decimal(1), Decimal(1),
    )
    policy = StrategySearchPolicy(
        policy_id="search-policy", revision=1,
        policy_version=SEARCH_POLICY_VERSION, strategy_id=parent.strategy_id,
        parameter_rules=(rule,), maximum_candidates_per_generation=2,
        maximum_active_candidates=4, maximum_total_candidates=10,
        maximum_generations=3, generation_cooldown=timedelta(0),
        deterministic_seed="isolation-seed", created_at=NOW,
    )
    search.append_policy_revision(policy, expected_current_revision=None)
    generation = StrategyCandidateGenerationApplication(strategies, search).generate(
        parent_version_id=parent.version_id,
        policy_id=policy.policy_id,
        policy_revision=1,
        generation=1,
        generated_at=NOW,
    )
    child_id = generation.candidate_lineages[0].child_version_id
    child = strategies.get_version(child_id)
    assert child.status.value == "research"
    assert child.mode.value == "research"
    assert child.gate_passed is False

    databases = (
        (chain.authentication_store.path, "strategy_evidence_authentication"),
        (chain.gate_store.path, "strategy_gate_evaluation"),
        (coverage.path, "strategy_coverage_evaluation"),
        (decisions.path, "strategy_lifecycle_decision"),
        (performance.path, "strategy_paper_performance_evaluation"),
    )
    for path, table in databases:
        with sqlite3.connect(path) as connection:
            parent_count = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE strategy_version_id = ?",
                (parent.version_id,),
            ).fetchone()[0]
            child_count = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE strategy_version_id = ?",
                (child_id,),
            ).fetchone()[0]
        assert parent_count > 0, table
        assert child_count == 0, table


def _performance_service(tmp_path, chain):
    from test_strategy_paper_performance_lifecycle import _service

    return _service(tmp_path, chain, version=_version())


def _coverage_policy_from_chain(chain):
    from test_strategy_lifecycle_evaluator import _coverage_policy

    return _coverage_policy()


def _pass_verdict():
    from us_quant.trading.domain.strategy_paper_performance import (
        StrategyPaperPerformanceVerdict,
    )

    return StrategyPaperPerformanceVerdict.PASS
