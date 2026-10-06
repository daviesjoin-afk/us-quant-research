"""Read-only post-run auditor: snapshot, then verify-restart in a new process.

Broker input is an explicit observation exported from the existing broker read
path, in canonical JSON: {portfolio: BrokerAccountPortfolio, open_orders:
BrokerOpenOrderTruth}. No broker input means BLOCKED, never an assumed flat
account. This reporting input cannot authorize or resume trading.
"""

from __future__ import annotations

import argparse
from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile

from us_quant.trading.application.paper_canary_operational_proof import (
    PaperCanaryOperationalProofSpec, PerformanceComparison, ProofStatus, compare_restart,
)
from us_quant.trading.composition.paper_canary_operational_proof import build_paper_canary_operational_proof_application
from us_quant.trading.domain.account import BrokerAccountPortfolio, BrokerAccountSnapshot, BrokerDiagnostic, BrokerPositionSnapshot
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.orders import Side
from us_quant.trading.domain.portfolio_ledger import PortfolioStoreUnreadable
from us_quant.trading.domain.portfolio_reconciliation import BrokerOpenOrder, BrokerOpenOrderTruth
from us_quant.trading.domain.strategy_paper_performance import canonical_json, canonical_value, digest, require_aware
from us_quant.trading.ports.strategy_paper_performance_repository import StrategyPaperPerformanceRepositoryError
from us_quant.trading.ports.strategy_repository import StrategyRepositoryError
from us_quant.paths import ApplicationPaths

SCHEMA_VERSION = "paper-canary-operational-proof-v1"
ARTIFACT_FIELDS = frozenset({
    "schema_version", "generated_at", "runtime_revision", "strategies", "sessions",
    "decision_ids", "order_ids", "execution_ids", "performance_evaluation_ids",
    "durable_truth_digest", "semantic_projection", "fresh_semantic_projection",
    "snapshot_status", "snapshot_blockers", "artifact_sha256",
})


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def make_artifact(proof, *, generated_at: datetime):
    require_aware(generated_at)
    value = canonical_value({
        "schema_version": SCHEMA_VERSION, "generated_at": generated_at,
        "runtime_revision": proof.runtime_revision,
        "strategies": proof.strategy_version_ids, "sessions": proof.paper_session_ids,
        "decision_ids": proof.portfolio_decision_ids, "order_ids": proof.order_ids,
        "execution_ids": proof.execution_ids,
        "performance_evaluation_ids": tuple(x.evaluation_id for x in proof.performance_evaluations),
        "durable_truth_digest": proof.durable_truth_digest,
        "semantic_projection": proof.semantic_projection(),
        "fresh_semantic_projection": proof.semantic_projection(PerformanceComparison.FRESH_RECONSTRUCTION),
        "snapshot_status": proof.status, "snapshot_blockers": proof.blockers,
    })
    value["artifact_sha256"] = digest(value)
    return value


def validate_artifact(value):
    if not isinstance(value, dict) or set(value) != ARTIFACT_FIELDS:
        raise ValueError("baseline artifact schema mismatch")
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported baseline schema")
    payload = {k: v for k, v in value.items() if k != "artifact_sha256"}
    if digest(payload) != value["artifact_sha256"]:
        raise ValueError("baseline self-hash mismatch")
    require_aware(datetime.fromisoformat(value["generated_at"]))
    ProofStatus(value["snapshot_status"])
    projection = value["semantic_projection"]
    if not isinstance(projection, dict):
        raise ValueError("baseline projection required")
    for name, projected in (
        ("runtime_revision", "runtime_revision"), ("strategies", "strategy_version_ids"),
        ("sessions", "paper_session_ids"), ("decision_ids", "portfolio_decision_ids"),
        ("order_ids", "order_ids"), ("execution_ids", "execution_ids"),
        ("durable_truth_digest", "durable_truth_digest"),
    ):
        if value[name] != projection.get(projected):
            raise ValueError(f"baseline projection linkage mismatch: {name}")
    fresh = dict(projection)
    performances = projection.get("performance_evaluations")
    if not isinstance(performances, list):
        raise ValueError("baseline performance projection required")
    ids = sorted(x["evaluation_id"] for x in performances)
    if ids != sorted(value["performance_evaluation_ids"]):
        raise ValueError("baseline performance identity mismatch")
    fresh["performance_evaluations"] = sorted(
        ({k: v for k, v in x.items() if k not in ("evaluation_id", "source_digest")} for x in performances),
        key=canonical_json,
    )
    if canonical_json(fresh) != canonical_json(value["fresh_semantic_projection"]):
        raise ValueError("baseline comparison modes disagree")
    return value


def load_artifact(path):
    return validate_artifact(_read_json(path))


def write_artifact_once(path, artifact):
    """Serialize under an exclusive writer lock, then publish by atomic replace."""
    validate_artifact(artifact)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    temp_path = None
    try:
        if path.exists():
            if canonical_json(load_artifact(path)) != canonical_json(artifact):
                raise FileExistsError("different baseline artifact already exists")
            return
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(canonical_json(artifact) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        os.close(descriptor)
        lock.unlink()


def _record(cls, values, *, decimals=(), dates=(), conversions=None):
    if not isinstance(values, dict) or set(values) != {x.name for x in fields(cls)}:
        raise ValueError(f"invalid broker observation schema: {cls.__name__}")
    values = dict(values)
    for key in decimals:
        values[key] = Decimal(values[key]) if values[key] is not None else None
        if values[key] is not None and not values[key].is_finite():
            raise ValueError("non-finite broker quantity")
    for key in dates:
        values[key] = datetime.fromisoformat(values[key])
    for key, convert in (conversions or {}).items():
        values[key] = convert(values[key])
    return cls(**values)


def load_broker_observation(path):
    """Typed reporting input only; no broker connection or execution import."""
    value = _read_json(path)
    if set(value) != {"portfolio", "open_orders"}:
        raise ValueError("broker observation requires portfolio and open_orders")
    portfolio = value["portfolio"]
    if set(portfolio) != {"account", "positions", "diagnostics"}:
        raise ValueError("invalid broker portfolio schema")
    account = _record(BrokerAccountSnapshot, portfolio["account"],
        decimals=("net_liquidation", "cash", "available_funds", "buying_power", "gross_position_value",
                  "excess_liquidity", "maintenance_margin", "cushion", "daily_pnl", "unrealized_pnl", "realized_pnl"),
        dates=("observed_at",), conversions={"environment": Environment})
    positions = tuple(_record(BrokerPositionSnapshot, x,
        decimals=("quantity", "average_cost", "market_value", "daily_pnl", "unrealized_pnl", "realized_pnl"),
        dates=("observed_at",)) for x in portfolio["positions"])
    diagnostics = tuple(_record(BrokerDiagnostic, x) for x in portfolio["diagnostics"])
    orders = dict(value["open_orders"])
    orders["open_orders"] = tuple(_record(BrokerOpenOrder, x,
        decimals=("quantity", "remaining_quantity"), conversions={"side": Side}) for x in orders["open_orders"])
    order_truth = _record(BrokerOpenOrderTruth, orders, dates=("observed_at",))
    if type(order_truth.snapshot_complete) is not bool:
        raise ValueError("snapshot_complete must be bool")
    if any(x.account_alias != account.account_alias for x in positions):
        raise ValueError("broker observation account mismatch")
    class ReadPort:
        def broker_open_order_truth(self):
            return order_truth
    return BrokerAccountPortfolio(account, positions, diagnostics), ReadPort()


def resolve_runtime_revision():
    try:
        root = ApplicationPaths.discover().resource_root
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                               capture_output=True, text=True, check=True, timeout=5)
        if dirty.stdout.strip():
            return None
        top_level = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root,
                                   capture_output=True, text=True, check=True, timeout=5)
        if Path(top_level.stdout.strip()).resolve() != root.resolve():
            return None
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True, check=True, timeout=5)
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("snapshot", "verify-restart"))
    parser.add_argument("--strategy-version", action="append", required=True)
    parser.add_argument("--session-id", action="append", required=True)
    parser.add_argument("--evaluation-id", action="append")
    parser.add_argument("--expected-runtime-revision")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--broker-observation", type=Path, help="explicit canonical broker observation JSON; absent => BLOCKED")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--comparison", choices=tuple(PerformanceComparison), default=PerformanceComparison.DURABLE_RECOVERY)
    args = parser.parse_args(argv)
    try:
        baseline = None
        if args.mode == "verify-restart":
            if args.baseline is None:
                raise ValueError("verify-restart requires --baseline")
            if not args.baseline.exists():
                print(canonical_json({"status": ProofStatus.RESTART_BASELINE_MISSING, "blockers": ["restart_baseline_missing"]}))
                return 2
            baseline = load_artifact(args.baseline)
        elif args.output is None:
            raise ValueError("snapshot requires --output")
        if (args.mode == "verify-restart"
                and args.comparison == PerformanceComparison.FRESH_RECONSTRUCTION
                and not args.evaluation_id):
            raise ValueError("fresh-reconstruction requires explicit post-restart --evaluation-id")
        spec = PaperCanaryOperationalProofSpec(
            tuple(args.strategy_version), tuple(args.session_id),
            tuple(args.evaluation_id or (baseline["performance_evaluation_ids"] if baseline else ())),
            args.expected_runtime_revision,
        )
        broker, source = load_broker_observation(args.broker_observation) if args.broker_observation else (None, None)
        application = build_paper_canary_operational_proof_application(
            spec, runtime_root=args.runtime_root, broker_order_truth=source,
        )
        now = datetime.now(UTC)
        proof = application.inspect(now=now, runtime_revision=resolve_runtime_revision(), broker=broker)
        if args.mode == "snapshot":
            write_artifact_once(args.output, make_artifact(proof, generated_at=datetime.now(UTC)))
        else:
            proof = compare_restart(baseline, proof, mode=PerformanceComparison(args.comparison))
        print(canonical_json(proof))
        return 0 if proof.status is ProofStatus.OPERATIONAL_PROOF_PASS or (
            args.mode == "snapshot" and proof.status is ProofStatus.RESTART_BASELINE_MISSING and not proof.blockers
        ) else 2
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError, sqlite3.Error,
            PortfolioStoreUnreadable, StrategyRepositoryError, StrategyPaperPerformanceRepositoryError) as error:
        print(canonical_json({"status": ProofStatus.RESTART_MISMATCH, "blockers": [f"{type(error).__name__}: {error}"]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
