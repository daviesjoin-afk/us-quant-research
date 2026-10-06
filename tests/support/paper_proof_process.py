"""Two independent interpreters create/verify proof; only paths cross processes."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

from paper_performance_support import NOW, ACCOUNT_ALIAS, broker, history, policy
from test_trading_strategy_repository import _audit, _new_version
from us_quant.paper_canary_operational_proof import make_artifact, load_artifact, write_artifact_once
from us_quant.trading.adapters.sqlite.order_repository import SQLiteOrderRepository
from us_quant.trading.adapters.sqlite.portfolio_repository import SQLitePortfolioRepository
from us_quant.trading.adapters.sqlite.strategy_repository import SQLiteStrategyRepository
from us_quant.trading.application.paper_canary_operational_proof import (
    PaperCanaryOperationalProofSpec, compare_restart,
)
from us_quant.trading.composition.paper_canary_operational_proof import build_paper_canary_operational_proof_application
from us_quant.trading.composition.strategy_paper_performance import build_strategy_paper_performance_components
from us_quant.trading.domain.portfolio_reconciliation import BrokerOpenOrder, BrokerOpenOrderTruth
from us_quant.trading.domain.orders import Side
from us_quant.trading.domain.strategy import StrategyMode, StrategyStatus
from us_quant.trading.domain.strategy_paper_performance import canonical_json


def broker_source(*, partial=False, extra=False, observed_at=NOW):
    entries = []
    if partial:
        entries.append(BrokerOpenOrder(8, ACCOUNT_ALIAS, "AAPL", Side.SELL, Decimal(80), Decimal(40)))
    if extra:
        entries.append(BrokerOpenOrder(99, ACCOUNT_ALIAS, "AAPL", Side.BUY, Decimal(10), Decimal(10)))
    truth = BrokerOpenOrderTruth(ACCOUNT_ALIAS, observed_at, True, tuple(entries))
    return SimpleNamespace(broker_open_order_truth=lambda: truth)


def persist_facts(root, *, data=None, partial=False):
    """Normal repository paths plus the formal performance-writing application."""
    root = Path(root)
    strategies = SQLiteStrategyRepository(root / "strategies.sqlite3")
    portfolio = SQLitePortfolioRepository(root / "portfolio_execution.sqlite3")
    orders = SQLiteOrderRepository(root / "ibkr_paper_orders.sqlite3")
    for version_id in ("a", "b"):
        version = _new_version(version_id=version_id, status=StrategyStatus.PAPER_SHADOW, mode=StrategyMode.PAPER_SHADOW)
        version = replace(version, semver=f"1.0.{0 if version_id == 'a' else 1}-research")
        strategies.insert_version(version, audit=_audit(version))
    decisions, attrs, truth = data if data is not None else history(partial_sell=partial)
    for decision, attribution in zip(decisions, attrs):
        stored = portfolio.record_decision(decision)
        portfolio.record_dispatch_outcome(
            replace(stored, dispatch_outcome_recorded=True, dispatch_submitted=True, dispatch_status="submitted"),
            attribution, expected_revision=stored.revision,
        )
    for order in truth.orders:
        orders.record_intent(order.intent, broker_order_id=order.broker_order_id, account_alias=ACCOUNT_ALIAS)
        for event in order.events:
            orders.record_event(event)
        for execution in order.fills:
            orders.record_fill(execution)
    source = broker_source(partial=partial)
    components = build_strategy_paper_performance_components(
        database_path=root / "strategy_governance.sqlite3", strategies=strategies,
        portfolio_repository=portfolio, order_truth=orders, broker_order_truth=source,
    )
    components.repository.append_policy_revision(policy(), expected_current_revision=None)
    account = broker(quantities={"AAPL": 40} if partial else {})
    evaluations = tuple(components.application.evaluate(
        strategy_version_id=v, policy_id="paper-policy", window_start=NOW-timedelta(days=3),
        window_end=NOW, broker=account, evaluated_at=NOW,
    ) for v in ("a", "b"))
    return portfolio, orders, components, account, source, evaluations


def spec_for(evaluations):
    return PaperCanaryOperationalProofSpec(("a", "b"), ("buy-session", "sell-session"),
                                          tuple(x.evaluation_id for x in evaluations), "runtime-sha")


def proof_for(root, evaluations, *, account=None, source=None):
    application = build_paper_canary_operational_proof_application(
        spec_for(evaluations), runtime_root=root, broker_order_truth=source or broker_source(),
    )
    return application.inspect(now=NOW, runtime_revision="runtime-sha", broker=account or broker(quantities={}))


def phase_a(root):
    *_, account, source, evaluations = persist_facts(root)
    proof = proof_for(root, evaluations, account=account, source=source)
    artifact = make_artifact(proof, generated_at=NOW)
    write_artifact_once(Path(root) / "proof.json", artifact)
    print(canonical_json({"status": proof.status, "digest": proof.durable_truth_digest}), flush=True)
    os._exit(0)


def phase_b(root, corruption):
    # SQL is confined to explicitly named corruption cases, never fact seeding.
    if corruption in {"fill", "order"}:
        with sqlite3.connect(Path(root) / "ibkr_paper_orders.sqlite3") as connection:
            if corruption == "fill":
                connection.execute("UPDATE paper_execution SET price='101' WHERE execution_id='b1'")
            else:
                connection.execute("UPDATE paper_order_intent SET session_id='different-session' WHERE intent_id='z-buy'")
    elif corruption == "attribution":
        with sqlite3.connect(Path(root) / "portfolio_execution.sqlite3") as connection:
            connection.execute("DELETE FROM portfolio_execution_attribution WHERE order_id='z-buy'")
    baseline = load_artifact(Path(root) / "proof.json")
    spec = PaperCanaryOperationalProofSpec(tuple(baseline["strategies"]), tuple(baseline["sessions"]),
                                          tuple(baseline["performance_evaluation_ids"]), "runtime-sha")
    application = build_paper_canary_operational_proof_application(spec, runtime_root=root, broker_order_truth=broker_source())
    current = application.inspect(now=NOW, runtime_revision="runtime-sha", broker=broker(quantities={}))
    proof = compare_restart(baseline, current)
    print(canonical_json({"status": proof.status, "digest": proof.durable_truth_digest, "blockers": proof.blockers}))


if __name__ == "__main__":
    phase, root = sys.argv[1:3]
    if phase == "a":
        phase_a(root)
    elif phase == "b":
        phase_b(root, sys.argv[3] if len(sys.argv) > 3 else "none")
    else:
        raise ValueError("unknown phase")
