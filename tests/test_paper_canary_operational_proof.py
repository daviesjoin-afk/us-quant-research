from dataclasses import fields, replace
from datetime import timedelta, timezone
from decimal import Decimal
import ast
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from paper_performance_support import NOW, ACCOUNT_ALIAS, broker, history
from support.paper_proof_process import broker_source, persist_facts, spec_for, proof_for
from us_quant import paper_canary_operational_proof as edge
from us_quant.trading.application.paper_canary_operational_proof import (
    PaperCanaryOperationalProofApplication as Application, PaperCanaryOperationalProofSpec as Spec,
    ProofStatus as S, PerformanceComparison as Mode, compare_restart, performance_semantics,
)
from us_quant.trading.composition.paper_canary_operational_proof import build_paper_canary_operational_proof_application as build
from us_quant.trading.adapters.sqlite.portfolio_repository import SQLitePortfolioRepository
from us_quant.trading.adapters.sqlite.order_repository import SQLiteOrderRepository
from us_quant.trading.domain.portfolio_reconciliation import PortfolioOrderTruth
from us_quant.trading.domain.strategy_paper_performance import canonical_json, canonical_value, digest, stable_strategy_paper_performance_evaluation_id


@pytest.fixture
def facts(tmp_path):
    data = history()
    portfolio, orders, components, account, source, evaluations = persist_facts(tmp_path, data=data)
    return SimpleNamespace(root=tmp_path, portfolio=portfolio, orders=orders, components=components,
                           account=account, source=source, evaluations=evaluations, data=data)


def inspect_memory(facts, *, decisions=None, attrs=None, orders=None, source=None, account=None,
                   spec=None, evaluations=None, now=NOW, revision="runtime-sha"):
    portfolio = SimpleNamespace(
        decisions=lambda: decisions if decisions is not None else facts.portfolio.decisions(),
        execution_attributions=lambda: attrs if attrs is not None else facts.portfolio.execution_attributions(),
    )
    order_port = SimpleNamespace(portfolio_order_truth=lambda: orders if orders is not None else facts.orders.portfolio_order_truth())
    mapped = dict(zip((x.evaluation_id for x in facts.evaluations), evaluations)) if evaluations is not None else {}
    eval_port = SimpleNamespace(get_evaluation=lambda evaluation_id: mapped[evaluation_id]) if evaluations is not None else facts.components.repository
    app = Application(spec=spec or spec_for(facts.evaluations), portfolio_repository=portfolio,
                      order_truth=order_port, evaluations=eval_port, strategies=facts.components.application._strategies,
                      broker_order_truth=source or facts.source)
    return app.inspect(now=now, runtime_revision=revision, broker=account or facts.account)


def artifact(proof):
    return edge.make_artifact(proof, generated_at=NOW)


def evaluation_changed(value, **changes):
    payload = {f.name: getattr(value, f.name) for f in fields(value)}
    payload.update(changes)
    payload.pop("evaluation_id")
    return replace(value, evaluation_id=stable_strategy_paper_performance_evaluation_id(payload), **changes)


def test_empty_stores_no_canary_evidence_without_creating_database(tmp_path):
    proof = build(Spec(("a", "b"), ("session",), ()), runtime_root=tmp_path / "absent").inspect(now=NOW, runtime_revision=None)
    assert proof.status is S.NO_CANARY_EVIDENCE
    assert proof.fill_count == 0 and proof.attribution_count == 0
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("risk", [False, True])
def test_decision_only_remains_incomplete(facts, risk):
    decision = facts.portfolio.decisions()[0]
    decision = replace(decision, order_id=None, dispatch_outcome_recorded=False,
                       dispatch_submitted=False, dispatch_status=None,
                       risk_outcome=decision.risk_outcome if risk else None,
                       risk_decision=decision.risk_decision if risk else None)
    proof = inspect_memory(facts, decisions=(decision,), attrs=(), orders=PortfolioOrderTruth(()))
    assert proof.status is S.INCOMPLETE_SESSION_TRUTH


def test_missing_attribution_blocks(facts):
    proof = inspect_memory(facts, attrs=())
    assert proof.status is S.INCOMPLETE_SESSION_TRUTH
    assert "missing_execution_attribution" in proof.blockers
    assert "attribution_incomplete" in proof.blockers
    assert not proof.attribution_complete and proof.replay_blockers


def test_no_fills_cannot_pass(facts):
    truth = facts.orders.portfolio_order_truth()
    proof = inspect_memory(facts, orders=PortfolioOrderTruth(tuple(replace(x, fills=()) for x in truth.orders)))
    assert proof.status is S.INCOMPLETE_SESSION_TRUTH
    assert "decision_order_fill_required" in proof.blockers


def test_terminal_truth_snapshot_is_not_restart_pass(facts):
    proof = proof_for(facts.root, facts.evaluations)
    assert proof.status is S.RESTART_BASELINE_MISSING and not proof.blockers
    assert proof.fill_count == 3 and proof.attribution_count == 2
    assert proof.attributed_fill_counts == (("a", 3), ("b", 3))
    assert proof.open_order_ids == () and not proof.replay_blockers
    assert json.loads(proof.reconciliation_semantics)["blockers"] == []
    restarted = proof_for(facts.root, facts.evaluations)
    assert compare_restart(artifact(proof), restarted).status is S.OPERATIONAL_PROOF_PASS


def test_partial_fill_reconstructs_deterministically(tmp_path):
    *_, account, source, evaluations = persist_facts(tmp_path, partial=True)
    first = proof_for(tmp_path, evaluations, account=account, source=source)
    second = proof_for(tmp_path, evaluations, account=account, source=source)
    assert first.status is S.RESTART_BASELINE_MISSING and not first.blockers
    assert first.open_order_ids == ("a-sell",)
    assert json.loads(first.reconciliation_semantics)["open_order_ids"] == ["a-sell"]
    assert compare_restart(artifact(first), second).status is S.OPERATIONAL_PROOF_PASS


def test_duplicate_execution_id_blocks(facts):
    truth = facts.orders.portfolio_order_truth()
    duplicated = replace(truth.orders[0], fills=truth.orders[0].fills + truth.orders[0].fills)
    proof = inspect_memory(facts, orders=PortfolioOrderTruth((duplicated, *truth.orders[1:])))
    assert proof.status is S.INCOMPLETE_SESSION_TRUTH and proof.replay_blockers
    assert "replay_blocked" in proof.blockers


def test_wrong_portfolio_attribution_blocks(facts):
    attrs = facts.portfolio.execution_attributions()
    bad = replace(attrs[0], portfolio_decision_id="absent", contributions=tuple(
        replace(x, portfolio_decision_id="absent") for x in attrs[0].contributions))
    proof = inspect_memory(facts, attrs=(bad, *attrs[1:]))
    assert proof.status is S.INCOMPLETE_SESSION_TRUTH and proof.replay_blockers
    assert "replay_blocked" in proof.blockers


def test_unexplained_broker_order_blocks(facts):
    proof = inspect_memory(facts, source=broker_source(extra=True))
    assert proof.status is S.RECONCILIATION_BLOCKED
    assert "canonical_reconciliation_blocked" in proof.blockers
    assert "unexplained_order" in json.loads(proof.reconciliation_semantics)["blockers"]


def test_missing_broker_observation_blocks(facts):
    app = build(spec_for(facts.evaluations), runtime_root=facts.root)
    proof = app.inspect(now=NOW, runtime_revision="runtime-sha")
    assert proof.status is S.RECONCILIATION_BLOCKED and "broker_observation_missing" in proof.blockers


def test_stale_or_live_broker_truth_blocks(facts):
    from us_quant.trading.domain.common import Environment
    stale = inspect_memory(facts, now=NOW + timedelta(minutes=6))
    assert stale.status is S.RECONCILIATION_BLOCKED
    live = inspect_memory(facts, account=replace(facts.account, account=replace(facts.account.account, environment=Environment.LIVE)))
    assert live.status is S.RECONCILIATION_BLOCKED and "paper_broker_required" in live.blockers


def test_no_performance_evaluation_blocks(facts):
    proof = inspect_memory(facts, spec=replace(spec_for(facts.evaluations), performance_evaluation_ids=()))
    assert proof.status is S.PERFORMANCE_MISSING
    assert "exact_performance_strategy_set_required" in proof.blockers


def test_evaluation_before_last_session_fill_is_incomplete(tmp_path):
    decisions, attrs, truth = history()
    sell = next(x for x in truth.orders if x.intent.order_id == "a-sell")
    old_fill = replace(sell.fills[0], quantity=Decimal(40))
    later_fill = replace(old_fill, execution_id="late-fill", occurred_at=NOW)
    truth = PortfolioOrderTruth(tuple(replace(x, fills=(old_fill, later_fill)) if x is sell else x for x in truth.orders))
    _, _, components, account, source, _ = persist_facts(tmp_path, data=(decisions, attrs, truth))
    evaluations = tuple(components.application.evaluate(
        strategy_version_id=v, policy_id="paper-policy", window_start=NOW-timedelta(days=3),
        window_end=NOW-timedelta(hours=12), broker=account, evaluated_at=NOW,
    ) for v in ("a", "b"))
    proof = proof_for(tmp_path, evaluations, account=account, source=source)
    assert proof.status is S.PERFORMANCE_MISSING
    assert "performance_does_not_cover_all_session_fills" in proof.blockers


def test_missing_performance_id_blocks(facts):
    proof = inspect_memory(facts, spec=replace(spec_for(facts.evaluations), performance_evaluation_ids=("missing",)))
    assert proof.status is S.PERFORMANCE_MISSING and "performance_evaluation_missing" in proof.blockers


@pytest.mark.parametrize("kind", ["strategy", "session"])
def test_exact_target_identity_mismatch_blocks(facts, kind):
    spec = spec_for(facts.evaluations)
    spec = replace(spec, **({"strategy_version_ids": ("a", "c")} if kind == "strategy" else {"expected_session_ids": ("buy-session", "other")}))
    proof = inspect_memory(facts, spec=spec)
    assert proof.status is S.INCOMPLETE_SESSION_TRUTH
    assert f"exact_{kind}_set_mismatch" in proof.blockers


def test_runtime_revision_mismatch_visible(facts):
    proof = inspect_memory(facts, revision="different-revision")
    assert proof.status is S.RESTART_MISMATCH and "runtime_revision_mismatch" in proof.blockers


def test_performance_is_exactly_reread_from_durable_store(facts):
    proof = proof_for(facts.root, facts.evaluations)
    assert tuple(sorted(proof.performance_evaluations, key=lambda x: x.evaluation_id)) == tuple(sorted(facts.evaluations, key=lambda x: x.evaluation_id))
    assert {x.evaluation_id for x in proof.performance_evaluations} == {x.evaluation_id for x in facts.evaluations}
    assert {x.source_digest for x in proof.performance_evaluations} == {x.source_digest for x in facts.evaluations}


@pytest.mark.parametrize("field,value", [
    ("source_portfolio_decision_ids", ("unknown",)), ("source_order_ids", ("unknown",)),
    ("source_execution_ids", ("unknown",)), ("paper_session_ids", ("unknown",)),
])
def test_performance_links_must_match_durable_facts(facts, field, value):
    changes = {field: value}
    if field == "paper_session_ids":
        changes["metrics"] = replace(facts.evaluations[0].metrics, paper_session_ids=value)
    evaluations = (evaluation_changed(facts.evaluations[0], **changes), facts.evaluations[1])
    proof = inspect_memory(facts, evaluations=evaluations)
    assert proof.status is S.PERFORMANCE_MISSING
    if field == "paper_session_ids":
        assert "performance_session_linkage_mismatch" in proof.blockers
    else:
        assert "performance_source_linkage_mismatch" in proof.blockers
        assert "performance_source_replay_mismatch" in proof.blockers


def test_performance_metrics_must_match_canonical_replay(facts):
    first = facts.evaluations[0]
    bad = evaluation_changed(first, metrics=replace(first.metrics, realized_pnl=first.metrics.realized_pnl + 1,
                                                   net_realized_pnl=first.metrics.net_realized_pnl + 1))
    proof = inspect_memory(facts, evaluations=(bad, facts.evaluations[1]))
    assert proof.status is S.PERFORMANCE_MISSING
    assert "performance_metrics_replay_mismatch" in proof.blockers


def test_fresh_reconstruction_accepts_new_digest_and_evaluation_id(facts):
    before = proof_for(facts.root, facts.evaluations)
    # The operator uses the formal writer outside the auditor with a new broker observation.
    new_at = NOW + timedelta(seconds=2)
    facts.components.application._broker_orders = broker_source(observed_at=new_at)
    fresh_account = broker(quantities={}, observed_at=new_at)
    fresh = tuple(facts.components.application.evaluate(
        strategy_version_id=v, policy_id="paper-policy", window_start=x.window_start,
        window_end=x.window_end, broker=fresh_account, evaluated_at=new_at,
    ) for v, x in zip(("a", "b"), facts.evaluations))
    assert fresh[0].source_digest != facts.evaluations[0].source_digest
    assert fresh[0].evaluation_id != facts.evaluations[0].evaluation_id
    # Inspect at the new observation time; the fixture helper's NOW would be future-stale.
    app = build(spec_for(fresh), runtime_root=facts.root, broker_order_truth=broker_source(observed_at=new_at))
    after = app.inspect(now=new_at, runtime_revision="runtime-sha", broker=fresh_account)
    assert not after.blockers
    assert compare_restart(artifact(before), after, mode=Mode.FRESH_RECONSTRUCTION).status is S.OPERATIONAL_PROOF_PASS
    assert compare_restart(artifact(before), after).status is S.RESTART_MISMATCH


@pytest.mark.parametrize("kind", ["baseline", "mixed"])
def test_fresh_comparison_rejects_reused_evaluations(facts, kind):
    before = proof_for(facts.root, facts.evaluations)
    fresh = tuple(evaluation_changed(x, reconciliation_observed_at=NOW + timedelta(seconds=1))
                  for x in before.performance_evaluations)
    reused = before.performance_evaluations if kind == "baseline" else (fresh[0], before.performance_evaluations[1])
    result = compare_restart(artifact(before), replace(before, performance_evaluations=reused),
                             mode=Mode.FRESH_RECONSTRUCTION)
    assert result.status is S.RESTART_MISMATCH
    assert result.blockers == ("fresh_evaluation_required",)


@pytest.mark.parametrize("seconds", [(1, 1), (2, 2), (1, 3), (3, 3)])
def test_fresh_evaluations_must_postdate_baseline(facts, seconds):
    before = proof_for(facts.root, facts.evaluations)
    # Different persisted IDs alone cannot prove reconstruction after the snapshot.
    fresh = []
    for version, original, delta in zip(("a", "b"), facts.evaluations, seconds):
        at = NOW + timedelta(seconds=delta)
        facts.components.application._broker_orders = broker_source(observed_at=at)
        fresh.append(facts.components.application.evaluate(
            strategy_version_id=version, policy_id="paper-policy",
            window_start=original.window_start, window_end=original.window_end,
            broker=broker(quantities={}, observed_at=at), evaluated_at=at,
        ))
    fresh = tuple(fresh)
    assert not {x.evaluation_id for x in fresh} & {x.evaluation_id for x in facts.evaluations}
    baseline_at = (NOW + timedelta(seconds=2)).astimezone(timezone(timedelta(hours=8)))
    baseline = edge.validate_artifact(edge.make_artifact(before, generated_at=baseline_at))
    after = build(spec_for(fresh), runtime_root=facts.root,
                  broker_order_truth=broker_source(observed_at=NOW + timedelta(seconds=4))).inspect(
        now=NOW + timedelta(seconds=4), runtime_revision="runtime-sha",
        broker=broker(quantities={}, observed_at=NOW + timedelta(seconds=4)),
    )
    assert not after.blockers
    assert before.semantic_projection(Mode.FRESH_RECONSTRUCTION) == after.semantic_projection(Mode.FRESH_RECONSTRUCTION)
    result = compare_restart(baseline, after, mode=Mode.FRESH_RECONSTRUCTION)
    if seconds == (3, 3):
        assert result.status is S.OPERATIONAL_PROOF_PASS
    else:
        assert result.status is S.RESTART_MISMATCH
        assert result.blockers == ("fresh_evaluation_must_postdate_baseline",)


def test_fresh_comparison_binds_requested_missing_policy(facts):
    def evaluate_missing(name, at):
        return tuple(facts.components.application.evaluate(
            strategy_version_id=v, policy_id=name, window_start=x.window_start,
            window_end=x.window_end, broker=facts.account, evaluated_at=at,
        ) for v, x in zip(("a", "b"), facts.evaluations))
    first = evaluate_missing("missing-before", NOW)
    second = evaluate_missing("missing-after", NOW + timedelta(seconds=1))
    assert all(x.policy_id is None for x in (*first, *second))
    assert first[0].metrics == second[0].metrics
    assert first[0].verdict == second[0].verdict
    assert first[0].blockers == second[0].blockers
    before, after = proof_for(facts.root, first), proof_for(facts.root, second)
    assert not before.blockers and not after.blockers
    result = compare_restart(artifact(before), after, mode=Mode.FRESH_RECONSTRUCTION)
    assert result.status is S.RESTART_MISMATCH
    assert result.blockers == ("restart_semantic_mismatch",)


@pytest.mark.parametrize("field", ["semver", "parameter_hash", "universe_hash", "code_hash"])
def test_fresh_comparison_binds_stable_strategy_source(facts, field):
    before = proof_for(facts.root, facts.evaluations)
    # Deliberate corruption case: repository versions are otherwise immutable.
    # An operator-created evaluation must not conceal changed durable identity.
    with sqlite3.connect(facts.root / "strategies.sqlite3") as connection:
        connection.execute(f"UPDATE strategy_version SET {field}=? WHERE version_id='a'", ("changed-business-source",))
    new_at = NOW + timedelta(seconds=2)
    source = broker_source(observed_at=new_at)
    account = broker(quantities={}, observed_at=new_at)
    facts.components.application._broker_orders = source
    fresh = tuple(facts.components.application.evaluate(
        strategy_version_id=v, policy_id="paper-policy", window_start=x.window_start,
        window_end=x.window_end, broker=account, evaluated_at=new_at,
    ) for v, x in zip(("a", "b"), facts.evaluations))
    assert fresh[0].metrics == facts.evaluations[0].metrics
    assert fresh[0].verdict == facts.evaluations[0].verdict
    assert fresh[0].source_digest != facts.evaluations[0].source_digest
    after = build(spec_for(fresh), runtime_root=facts.root, broker_order_truth=source).inspect(
        now=new_at, runtime_revision="runtime-sha", broker=account,
    )
    assert not after.blockers
    assert before.strategy_source_identities != after.strategy_source_identities
    result = compare_restart(artifact(before), after, mode=Mode.FRESH_RECONSTRUCTION)
    assert result.status is S.RESTART_MISMATCH
    assert result.blockers == ("restart_semantic_mismatch",)


@pytest.mark.parametrize("field", ["metrics", "verdict", "blockers", "source_digest", "evaluation_id"])
def test_performance_changed_semantics_rejected(facts, field):
    from us_quant.trading.domain.strategy_paper_performance import StrategyPaperPerformanceVerdict, StrategyPaperPerformanceBlocker
    before = proof_for(facts.root, facts.evaluations)
    first = before.performance_evaluations[0]
    changes = {
        "metrics": {"metrics": replace(first.metrics, realized_pnl=first.metrics.realized_pnl + 1,
                                        net_realized_pnl=first.metrics.net_realized_pnl + 1)},
        "verdict": {"verdict": StrategyPaperPerformanceVerdict.PASS, "blockers": ()},
        "blockers": {"verdict": StrategyPaperPerformanceVerdict.FAIL, "blockers": (StrategyPaperPerformanceBlocker.POLICY_MISSING,)},
        "source_digest": {"source_digest": "f" * 64},
        "evaluation_id": {"reconciliation_observed_at": first.reconciliation_observed_at + timedelta(seconds=1)},
    }
    after = replace(before, performance_evaluations=(evaluation_changed(first, **changes[field]), *before.performance_evaluations[1:]))
    assert compare_restart(artifact(before), after).status is S.RESTART_MISMATCH
    if field in ("metrics", "verdict", "blockers"):
        fresh = tuple(evaluation_changed(x, reconciliation_observed_at=NOW + timedelta(seconds=1),
                                         evaluated_at=NOW + timedelta(seconds=1))
                      for x in after.performance_evaluations)
        result = compare_restart(artifact(before), replace(after, performance_evaluations=fresh),
                                 mode=Mode.FRESH_RECONSTRUCTION)
        assert result.status is S.RESTART_MISMATCH
        assert result.blockers == ("restart_semantic_mismatch",)


@pytest.mark.parametrize("field,value", [
    ("portfolio_decision_ids", ("different",)), ("order_ids", ("different",)),
    ("execution_ids", ("different",)), ("durable_truth_digest", "f" * 64),
    ("attribution_count", 1), ("open_order_ids", ("unknown",)), ("runtime_revision", "other"),
])
def test_changed_durable_projection_mismatches(facts, field, value):
    proof = proof_for(facts.root, facts.evaluations)
    result = compare_restart(artifact(proof), replace(proof, **{field: value}))
    assert result.status is S.RESTART_MISMATCH


def test_missing_or_ineligible_restart_baseline_cannot_pass(facts):
    proof = proof_for(facts.root, facts.evaluations)
    assert compare_restart(None, proof).status is S.RESTART_BASELINE_MISSING
    baseline = artifact(proof)
    baseline["snapshot_status"] = S.NO_CANARY_EVIDENCE
    assert compare_restart(baseline, proof).status is S.RESTART_MISMATCH
    dirty = replace(proof, status=S.RECONCILIATION_BLOCKED, blockers=("blocked",))
    assert compare_restart(artifact(proof), dirty).status is S.RECONCILIATION_BLOCKED


@pytest.mark.parametrize("revision", [None, "", " "])
def test_unknown_runtime_revision_blocks_inspection(facts, revision):
    spec = replace(spec_for(facts.evaluations), expected_runtime_revision=None)
    proof = inspect_memory(facts, spec=spec, revision=revision)
    assert proof.status is S.RESTART_MISMATCH
    assert proof.blockers == ("runtime_revision_unknown",)


@pytest.mark.parametrize("before_revision,after_revision", [
    (None, None), (None, "runtime-sha"), ("runtime-sha", None),
])
def test_unknown_runtime_revision_blocks_comparison(facts, before_revision, after_revision):
    proof = proof_for(facts.root, facts.evaluations)
    baseline = edge.validate_artifact(artifact(replace(proof, runtime_revision=before_revision)))
    current = replace(proof, runtime_revision=after_revision)
    result = compare_restart(baseline, current)
    assert result.status is S.RESTART_MISMATCH
    assert result.blockers == ("runtime_revision_unknown",)


def test_baseline_self_hash_and_write_once(facts):
    value = artifact(proof_for(facts.root, facts.evaluations))
    path = facts.root / "baseline.json"
    edge.write_artifact_once(path, value)
    assert edge.load_artifact(path) == value
    edge.write_artifact_once(path, value)
    different = edge.make_artifact(proof_for(facts.root, facts.evaluations), generated_at=NOW + timedelta(seconds=1))
    with pytest.raises(FileExistsError, match="different baseline"):
        edge.write_artifact_once(path, different)
    assert edge.load_artifact(path) == value
    assert not path.with_name(path.name + ".lock").exists()


def test_edited_or_malformed_artifact_rejected(facts):
    value = artifact(proof_for(facts.root, facts.evaluations))
    value["durable_truth_digest"] = "f" * 64
    with pytest.raises(ValueError, match="self-hash"):
        edge.validate_artifact(value)
    value["artifact_sha256"] = digest({k: v for k, v in value.items() if k != "artifact_sha256"})
    with pytest.raises(ValueError, match="linkage"):
        edge.validate_artifact(value)
    path = facts.root / "duplicates.json"
    path.write_text('{"schema_version":"one","schema_version":"two"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON"):
        edge.load_artifact(path)
    unsupported = artifact(proof_for(facts.root, facts.evaluations))
    unsupported["schema_version"] = "unsupported"
    unsupported["artifact_sha256"] = digest({k: v for k, v in unsupported.items() if k != "artifact_sha256"})
    with pytest.raises(ValueError, match="unsupported baseline"):
        edge.validate_artifact(unsupported)


def test_row_read_order_and_utc_offsets_do_not_change_digest(facts):
    first = inspect_memory(facts)
    zone = timezone(timedelta(hours=8))
    decisions = tuple(replace(x, created_at=x.created_at.astimezone(zone), observed_at=x.observed_at.astimezone(zone))
                      for x in reversed(facts.portfolio.decisions()))
    orders = PortfolioOrderTruth(tuple(replace(x,
        intent=replace(x.intent, created_at=x.intent.created_at.astimezone(zone)),
        events=tuple(replace(e, occurred_at=e.occurred_at.astimezone(zone)) for e in reversed(x.events)),
        fills=tuple(replace(f, occurred_at=f.occurred_at.astimezone(zone)) for f in reversed(x.fills)),
    ) for x in reversed(facts.orders.portfolio_order_truth().orders)))
    second = inspect_memory(facts, decisions=decisions, attrs=tuple(reversed(facts.portfolio.execution_attributions())), orders=orders)
    assert first.durable_truth_digest == second.durable_truth_digest
    assert first.semantic_projection() == second.semantic_projection()
    later = inspect_memory(facts, now=NOW + timedelta(seconds=1))
    assert first.durable_truth_digest == later.durable_truth_digest


@pytest.mark.parametrize("kind", ["fill", "attribution", "decision", "order"])
def test_business_fact_payload_changes_digest(facts, kind):
    before = inspect_memory(facts)
    decisions = facts.portfolio.decisions()
    attrs = facts.portfolio.execution_attributions()
    truth = facts.orders.portfolio_order_truth()
    if kind == "decision":
        decisions = (replace(decisions[0], revision=decisions[0].revision + 1), *decisions[1:])
    elif kind == "attribution":
        attrs = (replace(attrs[0], contributions=tuple(replace(x, proposal_id=x.proposal_id + "-changed")
                                                       for x in attrs[0].contributions)), *attrs[1:])
    elif kind == "fill":
        order = replace(truth.orders[0], fills=tuple(replace(x, price=x.price + 1) for x in truth.orders[0].fills))
        truth = PortfolioOrderTruth((order, *truth.orders[1:]))
    else:
        order = replace(truth.orders[0], intent=replace(truth.orders[0].intent, reason="changed durable reason"))
        truth = PortfolioOrderTruth((order, *truth.orders[1:]))
    after = inspect_memory(facts, decisions=decisions, attrs=attrs, orders=truth)
    assert after.durable_truth_digest != before.durable_truth_digest
    assert compare_restart(artifact(before), after).status is not S.OPERATIONAL_PROOF_PASS


def test_different_sqlite_insert_order_same_semantics(facts, tmp_path):
    decisions, attrs, orders = facts.data
    second_root = tmp_path / "reordered"
    *_, account, source, evaluations = persist_facts(second_root, data=(tuple(reversed(decisions)), tuple(reversed(attrs)),
                                                                 PortfolioOrderTruth(tuple(reversed(orders.orders)))))
    first = proof_for(facts.root, facts.evaluations)
    second = proof_for(second_root, evaluations, account=account, source=source)
    assert first.durable_truth_digest == second.durable_truth_digest
    assert first.semantic_projection() == second.semantic_projection()


def test_both_strategies_need_not_each_have_fills(tmp_path):
    from us_quant.trading.domain.portfolio import PortfolioDecision, PortfolioOrderAttribution, PortfolioVerdict
    from us_quant.trading.domain.portfolio_ledger import PortfolioDecisionRecord
    decisions, attrs, truth = history()
    decisions = tuple(replace(x, decision=replace(x.decision, strategy_version_ids=("a",),
        attribution=tuple(replace(a, strategy_version_id="a") for a in x.decision.attribution))) for x in decisions)
    attrs = tuple(replace(x, contributions=tuple(replace(a, strategy_version_id="a") for a in x.contributions)) for x in attrs)
    portfolio, orders, components, account, source, evaluations = persist_facts(tmp_path, data=(decisions, attrs, truth))
    # B has an actual zero-net portfolio decision in the same session-linked cycle.
    b_id = "b-zero-net"
    decision = PortfolioDecision(b_id, PortfolioVerdict.APPROVE, "AAPL", ("b",), 2, 0, None, (
        PortfolioOrderAttribution(b_id, "b", "b-buy", "AAPL", 1),
        PortfolioOrderAttribution(b_id, "b", "b-sell", "AAPL", -1),
    ), None)
    portfolio.record_decision(PortfolioDecisionRecord(decision, decisions[0].portfolio_cycle_id, NOW, "policy", "1", NOW))
    # Recreate evaluations through the formal writer because the durable decision set changed.
    evaluations = tuple(components.application.evaluate(strategy_version_id=v, policy_id="paper-policy",
        window_start=NOW-timedelta(days=3), window_end=NOW, broker=account, evaluated_at=NOW) for v in ("a", "b"))
    proof = proof_for(tmp_path, evaluations, account=account, source=source)
    assert not proof.blockers and proof.attributed_fill_counts == (("a", 6), ("b", 0))
    assert compare_restart(artifact(proof), proof).status is S.OPERATIONAL_PROOF_PASS


def test_application_is_read_only_and_no_runtime_authority(facts, monkeypatch):
    calls = []
    for cls, method in ((SQLitePortfolioRepository, "record_decision"), (SQLitePortfolioRepository, "record_dispatch_outcome"),
                        (SQLiteOrderRepository, "record_intent"), (SQLiteOrderRepository, "record_event"), (SQLiteOrderRepository, "record_fill"),
                        (type(facts.components.repository), "record_evaluation")):
        def forbidden(*args, **kwargs):
            calls.append(True)
            raise AssertionError("business write from auditor")
        monkeypatch.setattr(cls, method, forbidden)
    before = {p.name: p.read_bytes() for p in facts.root.glob("*.sqlite3")}
    proof = proof_for(facts.root, facts.evaluations)
    assert not proof.blockers and not calls
    assert {p.name: p.read_bytes() for p in facts.root.glob("*.sqlite3")} == before


def test_readonly_repositories_never_initialize_and_refuse_writes(facts, tmp_path):
    for cls, name in ((SQLitePortfolioRepository, "portfolio_execution.sqlite3"), (SQLiteOrderRepository, "ibkr_paper_orders.sqlite3")):
        repository = cls(facts.root / name, read_only=True)
        with repository._connection() as connection:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                connection.execute("CREATE TABLE forbidden (id INTEGER)")
        missing = tmp_path / "missing" / name
        cls(missing, read_only=True)
        assert not missing.parent.exists()


def test_architecture_proof_has_only_read_ports():
    import us_quant.trading.application.paper_canary_operational_proof as module
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    forbidden_imports = ("execution", "risk", "portfolio_runtime", "paper.orchestrator", "strategies", "strategy_lifecycle", "portfolio_plan")
    forbidden_calls = {"submit", "cancel", "start", "stop", "arm", "evaluate", "record_evaluation", "save", "transition", "apply", "record_intent", "record_decision"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not any((node.module or "").endswith("." + x) for x in forbidden_imports)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in forbidden_calls
    source_root = Path(module.__file__).resolve().parents[1]
    for directory in ("application", "runtime", "domain"):
        for path in (source_root / directory).rglob("*.py"):
            if path == Path(module.__file__):
                continue
            text = path.read_text(encoding="utf-8")
            assert "paper_canary_operational_proof" not in text
            assert "operational-proof-before-restart.json" not in text


@pytest.mark.parametrize("corruption", ["none", "fill", "attribution", "order"])
def test_two_os_processes_durable_only_proof(tmp_path, corruption):
    root = Path(__file__).resolve().parents[1]
    helper = root / "tests/support/paper_proof_process.py"
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(root / "src"), str(root / "tests"))), PYTHONUTF8="1")
    # Shared Windows CI can be much slower than local execution under four
    # workers. Keep a bounded timeout without retrying or swallowing failures.
    first = subprocess.run([sys.executable, str(helper), "a", str(tmp_path)], capture_output=True, text=True, env=env, timeout=120)
    assert first.returncode == 0, first.stderr
    before = json.loads(first.stdout)
    second = subprocess.run([sys.executable, str(helper), "b", str(tmp_path), corruption], capture_output=True, text=True, env=env, timeout=120)
    assert second.returncode == 0, second.stderr
    after = json.loads(second.stdout)
    if corruption == "none":
        assert after["status"] == S.OPERATIONAL_PROOF_PASS and before["digest"] == after["digest"]
    else:
        assert after["status"] in {S.RESTART_MISMATCH, S.INCOMPLETE_SESSION_TRUTH, S.RECONCILIATION_BLOCKED, S.PERFORMANCE_MISSING}
        assert after["digest"] != before["digest"]


def test_cli_empty_snapshot_and_missing_baseline(tmp_path, capsys):
    common = ["--strategy-version", "a", "--session-id", "session", "--runtime-root", str(tmp_path / "absent")]
    target = tmp_path / "proof.json"
    assert edge.main(["snapshot", *common, "--output", str(target)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == S.NO_CANARY_EVIDENCE
    assert target.exists() and not (tmp_path / "absent").exists()
    assert edge.main(["verify-restart", *common, "--baseline", str(tmp_path / "missing.json")]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == S.RESTART_BASELINE_MISSING


def test_broker_observation_edge_decode(facts):
    path = facts.root / "broker.json"
    path.write_text(canonical_json({"portfolio": facts.account, "open_orders": facts.source.broker_open_order_truth()}), encoding="utf-8")
    account, source = edge.load_broker_observation(path)
    assert account == facts.account
    assert source.broker_open_order_truth() == facts.source.broker_open_order_truth()


def test_cli_snapshot_then_verify_is_readonly(facts, capsys, monkeypatch):
    from datetime import datetime
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(edge, "datetime", Clock)
    monkeypatch.setattr(edge, "resolve_runtime_revision", lambda: "runtime-sha")
    observation = facts.root / "broker.json"
    observation.write_text(canonical_json({"portfolio": facts.account, "open_orders": facts.source.broker_open_order_truth()}), encoding="utf-8")
    common = ["--strategy-version", "a", "--strategy-version", "b", "--session-id", "buy-session",
              "--session-id", "sell-session", "--runtime-root", str(facts.root), "--broker-observation", str(observation),
              "--expected-runtime-revision", "runtime-sha"]
    evaluation_args = [part for x in facts.evaluations for part in ("--evaluation-id", x.evaluation_id)]
    path = facts.root / "baseline.json"
    before = {p.name: p.read_bytes() for p in facts.root.glob("*.sqlite3")}
    assert edge.main(["snapshot", *common, *evaluation_args, "--output", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == S.RESTART_BASELINE_MISSING
    assert edge.main(["verify-restart", *common, "--baseline", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == S.OPERATIONAL_PROOF_PASS
    assert {p.name: p.read_bytes() for p in facts.root.glob("*.sqlite3")} == before
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["generated_at"] = "edited"
    path.write_text(json.dumps(changed), encoding="utf-8")
    assert edge.main(["verify-restart", *common, "--baseline", str(path)]) == 2
    assert "self-hash" in json.loads(capsys.readouterr().out)["blockers"][0]


@pytest.mark.parametrize("kind", ["missing", "baseline", "mixed", "fresh", "preexisting", "equal"])
def test_cli_fresh_requires_explicit_new_evaluations(facts, capsys, monkeypatch, kind):
    from datetime import datetime
    new_at = NOW + timedelta(seconds=2)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return new_at
    monkeypatch.setattr(edge, "datetime", Clock)
    monkeypatch.setattr(edge, "resolve_runtime_revision", lambda: "runtime-sha")
    path = facts.root / "baseline.json"
    baseline_at = NOW + timedelta(seconds={"preexisting": 3, "equal": 2}.get(kind, 0))
    edge.write_artifact_once(path, edge.make_artifact(proof_for(facts.root, facts.evaluations),
                                                   generated_at=baseline_at))
    source = broker_source(observed_at=new_at)
    account = broker(quantities={}, observed_at=new_at)
    facts.components.application._broker_orders = source
    fresh = tuple(facts.components.application.evaluate(
        strategy_version_id=v, policy_id="paper-policy", window_start=x.window_start,
        window_end=x.window_end, broker=account, evaluated_at=new_at,
    ) for v, x in zip(("a", "b"), facts.evaluations))
    observation = facts.root / "broker.json"
    observation.write_text(canonical_json({"portfolio": account, "open_orders": source.broker_open_order_truth()}), encoding="utf-8")
    selected = {"missing": (), "baseline": facts.evaluations,
                "mixed": (fresh[0], facts.evaluations[1]), "fresh": fresh,
                "preexisting": fresh, "equal": fresh}[kind]
    evaluation_args = [part for x in selected for part in ("--evaluation-id", x.evaluation_id)]
    before = {p.name: p.read_bytes() for p in facts.root.glob("*.sqlite3")}
    code = edge.main(["verify-restart", "--strategy-version", "a", "--strategy-version", "b",
                      "--session-id", "buy-session", "--session-id", "sell-session",
                      "--runtime-root", str(facts.root), "--broker-observation", str(observation),
                      "--baseline", str(path), "--comparison", "fresh-reconstruction", *evaluation_args])
    result = json.loads(capsys.readouterr().out)
    assert code == (0 if kind == "fresh" else 2)
    assert result["status"] == (S.OPERATIONAL_PROOF_PASS if kind == "fresh" else S.RESTART_MISMATCH)
    if kind == "missing":
        assert "explicit post-restart" in result["blockers"][0]
    elif kind in ("preexisting", "equal"):
        assert result["blockers"] == ["fresh_evaluation_must_postdate_baseline"]
    elif kind != "fresh":
        assert result["blockers"] == ["fresh_evaluation_required"]
    assert {p.name: p.read_bytes() for p in facts.root.glob("*.sqlite3")} == before


def test_spec_rejects_duplicate_identities():
    with pytest.raises(ValueError, match="unique"):
        Spec(("a", "a"), ("session",), ())


@pytest.mark.parametrize("kind", ["exact", "ancestor", "dirty"])
def test_runtime_revision_uses_exact_resource_root(tmp_path, monkeypatch, kind):
    root = tmp_path / "installed-app"
    monkeypatch.setattr(edge.ApplicationPaths, "discover", lambda: SimpleNamespace(resource_root=root))
    calls = []
    def run(arguments, **kwargs):
        assert kwargs["cwd"] == root
        calls.append(arguments)
        if "status" in arguments:
            return SimpleNamespace(stdout=" M file" if kind == "dirty" else "")
        if "--show-toplevel" in arguments:
            return SimpleNamespace(stdout=str(root if kind == "exact" else tmp_path))
        return SimpleNamespace(stdout="application-sha")
    monkeypatch.setattr(edge.subprocess, "run", run)
    assert edge.resolve_runtime_revision() == ("application-sha" if kind == "exact" else None)
    if kind == "ancestor":
        assert not any("HEAD" in x for x in calls)
