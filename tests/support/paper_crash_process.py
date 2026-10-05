"""Child-process entry points for Paper crash-boundary recovery tests.

Every invocation composes fresh repositories from file paths.  The parent never
passes a Python object to a child, and phase A exits with ``os._exit`` after the
requested durable boundary to model loss of the process rather than orderly
session shutdown.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

from us_quant.trading.adapters.sqlite.order_repository import SQLiteOrderRepository
from us_quant.trading.adapters.sqlite.portfolio_repository import SQLitePortfolioRepository
from us_quant.trading.application.execution import ExecutionApplication
from us_quant.trading.application.portfolio_runtime import PortfolioRuntime
from us_quant.trading.domain.portfolio import (
    PortfolioCapitalPolicy, PortfolioSnapshot, PortfolioStrategyAllocation,
)
from us_quant.trading.domain.portfolio_runtime import PortfolioDispatchResult
from us_quant.trading.domain.account import BrokerAccountPortfolio, BrokerAccountSnapshot, BrokerPositionSnapshot
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.orders import ExecutionFill, OrderEvent, OrderStatus, Side
from us_quant.trading.domain.portfolio_reconciliation import BrokerOpenOrder, BrokerOpenOrderTruth
from us_quant.trading.application.portfolio_reconciliation import PortfolioReconciliationApplication
from us_quant.trading.domain.risk import RiskDecision
from us_quant.trading.domain.strategy import (
    StrategyDefinition, StrategyIdentity, StrategyMode, StrategyStatus,
    StrategyVersion, TradeAction, TradeProposal,
)
from us_quant.trading.ports.broker_execution import ExecutionSubmissionUncertain
from us_quant.trading.runtime.workflow import PaperWorkflowController

NOW = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)
VERSION_ID = "crash-matrix-v1"


def _version() -> StrategyVersion:
    return StrategyVersion(
        definition=StrategyDefinition("crash-family", "Crash test", "matrix"),
        identity=StrategyIdentity("crash-family", VERSION_ID, "crash-hash"),
        semver="1.0.0", status=StrategyStatus.PAPER_SHADOW,
        mode=StrategyMode.PAPER_SHADOW, parameters={}, universe_hash="u",
        code_hash="c", risk_budget_pct=Decimal("0.01"), gate_passed=True,
        gate_reason="test-only", created_at=NOW, updated_at=NOW,
    )


class _Strategies:
    def list_versions(self):
        return (_version(),)


class _Proposals:
    def proposals_for(self, strategy, *, observed_at, proposal_cutoff, portfolio_snapshot):
        return (TradeProposal(
            strategy=strategy.identity, symbol="AAPL", action=TradeAction.BUY,
            desired_quantity=10, reference_price=Decimal("10"), reason="crash test",
            generated_at=observed_at,
        ),)


class _Snapshots:
    def snapshot(self, *, observed_at):
        return PortfolioSnapshot(
            cash=Decimal("1000"), equity=Decimal("1000"), gross_exposure=Decimal(0),
            net_exposure=Decimal(0), observed_at=observed_at,
        )


class _Broker:
    """Small broker port whose independently durable journal survives restart."""

    def __init__(self, path: Path, *, outcome: str):
        self.path = path
        self.outcome = outcome
        self.submit_calls = 0
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS broker_truth (order_id TEXT PRIMARY KEY, broker_id INTEGER NOT NULL, status TEXT NOT NULL)")

    def reserve(self, intent):
        return type("Reservation", (), {
            "broker_order_id": 101, "account_alias": "DU-TEST", "order_id": intent.order_id,
        })()

    def submit(self, reservation):
        self.submit_calls += 1
        if self.outcome == "intent_unknown":
            # ExecutionApplication has committed the local intent; terminate
            # before submit yields any broker observation to the caller.
            os._exit(0)
        if self.outcome in {"uncertain", "broker_only", "partial", "terminal"}:
            with sqlite3.connect(self.path) as db:
                db.execute("INSERT OR REPLACE INTO broker_truth VALUES (?, ?, ?)", (reservation.order_id, reservation.broker_order_id, "Submitted"))
        if self.outcome == "uncertain":
            raise ExecutionSubmissionUncertain("simulated lost acknowledgement", order_id=reservation.order_id, broker_order_id=reservation.broker_order_id)

    def cancel(self, order_id):
        return False


class _RiskPath:
    def __init__(self, execution, broker, *, submit_enabled=True):
        self.execution, self.broker, self.submit_enabled = execution, broker, submit_enabled
        self.submit_calls = 0
        self.evaluate_calls = 0

    def evaluate(self, decision, *, observed_at):
        self.evaluate_calls += 1
        return RiskDecision.approve(requested_quantity=decision.action.quantity)

    def submit(self, decision, risk_decision, *, observed_at):
        self.submit_calls += 1
        if not self.submit_enabled:
            raise AssertionError("restart attempted a new broker submission")
        proposal = TradeProposal(
            strategy=StrategyIdentity("crash-family", VERSION_ID, "crash-hash"),
            symbol=decision.action.symbol,
            action=TradeAction.BUY if decision.action.side.value == "buy" else TradeAction.SELL,
            desired_quantity=decision.action.quantity,
            reference_price=decision.action.reference_price,
            reason="crash matrix", generated_at=observed_at,
        )
        try:
            submitted = self.execution.submit_approved(
                proposal=proposal, decision=risk_decision,
                execution_symbol=proposal.symbol, session_id="paper-crash-session",
                reason="crash matrix",
            )
        except ExecutionSubmissionUncertain as error:
            return PortfolioDispatchResult(
                submitted=False, halt=True, order_id=error.order_id,
                status="submission uncertain; reconciliation required",
            )
        return PortfolioDispatchResult(submitted=True, halt=False, order_id=submitted.intent.order_id, status="submitted")


def _paths(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    return root / "portfolio.sqlite3", root / "orders.sqlite3", root / "broker.sqlite3"


def phase_a(root_text: str, boundary: str) -> None:
    root = Path(root_text)
    portfolio_path, order_path, broker_path = _paths(root)
    portfolio = SQLitePortfolioRepository(portfolio_path)
    orders = SQLiteOrderRepository(order_path)
    broker = _Broker(broker_path, outcome=boundary)
    execution = ExecutionApplication(repository=orders, broker=broker)
    path = _RiskPath(execution, broker, submit_enabled=boundary != "decision_only")
    runtime = PortfolioRuntime(
        strategies=_Strategies(), proposals=_Proposals(), snapshots=_Snapshots(),
        repository=portfolio, risk_path=path,
    )
    if boundary == "decision_only":
        # The application has no public mid-cycle hook.  Generate its durable
        # decision with a controlled risk-path interruption immediately after
        # allocation, before any broker/order truth can exist.
        class StopBeforeRisk(_RiskPath):
            def evaluate(self, decision, *, observed_at):
                raise RuntimeError("crash after durable decision")
        runtime = PortfolioRuntime(
            strategies=_Strategies(), proposals=_Proposals(), snapshots=_Snapshots(),
            repository=portfolio, risk_path=StopBeforeRisk(execution, broker),
        )
    cycle = None
    try:
        cycle = runtime.evaluate_cycle(
            portfolio_cycle_id="crash-cycle", observed_at=NOW, proposal_cutoff=NOW,
            snapshot_identity="snapshot-1", policy=_policy(), policy_identity="paper-policy",
            policy_revision="1", selected_version_ids=frozenset({VERSION_ID}),
        )
    except RuntimeError as error:
        if str(error) != "crash after durable decision":
            raise
    if boundary in {"partial", "terminal"}:
        record = portfolio.decisions()[0]
        order_id = record.order_id
        order = orders.portfolio_order_truth().orders[0]
        event_time = NOW
        partial = boundary == "partial"
        orders.record_event(OrderEvent(
            order_id=order_id, broker_order_id=101,
            status=OrderStatus.PARTIALLY_FILLED if partial else OrderStatus.FILLED,
            broker_status="PartiallyFilled" if partial else "Filled",
            filled=Decimal(4 if partial else 10), remaining=Decimal(6 if partial else 0),
            average_fill_price=Decimal("10"), last_fill_price=Decimal("10"),
            message="test fill", occurred_at=event_time,
        ))
        orders.record_fill(ExecutionFill(
            execution_id="exec-partial-1" if partial else "exec-terminal-1",
            order_id=order_id, broker_order_id=101, symbol="AAPL", side=Side.BUY,
            quantity=Decimal(4 if partial else 10), price=Decimal("10"),
            occurred_at=event_time, fee=Decimal("0"),
        ))
        print(json.dumps({"performance": _performance_semantic(root, create_version=True)}, sort_keys=True), flush=True)
    # Abrupt process death: do not serialize or pass any in-memory object onward.
    os._exit(0)


def _policy():
    allocation = PortfolioStrategyAllocation(
        strategy_version_id=VERSION_ID, capital_weight=Decimal(1),
        max_capital=Decimal(1000), max_gross_exposure=Decimal(1000), enabled=True,
    )
    return PortfolioCapitalPolicy(
        total_capital_limit=Decimal(1000), max_gross_exposure=Decimal(1000),
        max_net_exposure=Decimal(1000), max_single_position_notional=Decimal(1000),
        max_symbol_concentration=Decimal(1), max_strategy_concentration=Decimal(1),
        max_positions=10, max_open_orders=10, allocations=(allocation,),
    )


def _performance_semantic(root: Path, *, create_version: bool):
    from paper_performance_support import broker as performance_broker, policy as performance_policy
    from test_trading_strategy_repository import _audit, _new_version
    from us_quant.trading.adapters.sqlite.strategy_repository import SQLiteStrategyRepository
    from us_quant.trading.composition.strategy_paper_performance import build_strategy_paper_performance_components
    from us_quant.trading.domain.strategy_paper_performance import canonical_json

    strategies = SQLiteStrategyRepository(root / "strategy.sqlite3")
    if create_version:
        version = _new_version(version_id=VERSION_ID, status=StrategyStatus.PAPER_SHADOW, mode=StrategyMode.PAPER_SHADOW)
        strategies.insert_version(version, audit=_audit(version))
    orders = SQLiteOrderRepository(root / "orders.sqlite3")
    portfolio = SQLitePortfolioRepository(root / "portfolio.sqlite3")
    source = SimpleNamespace(broker_open_order_truth=lambda: BrokerOpenOrderTruth(
        account_alias="DU-TEST", observed_at=NOW, snapshot_complete=True, open_orders=(),
    ))
    components = build_strategy_paper_performance_components(
        database_path=root / "performance.sqlite3", strategies=strategies,
        portfolio_repository=portfolio, order_truth=orders, broker_order_truth=source,
    )
    if create_version:
        components.repository.append_policy_revision(
            performance_policy(maximum_adverse_slippage=Decimal(10000)),
            expected_current_revision=None,
        )
    evaluation = components.application.evaluate(
        strategy_version_id=VERSION_ID, policy_id="paper-policy",
        window_start=NOW.replace(year=NOW.year - 1), window_end=NOW,
        broker=performance_broker(quantities={}),
        evaluated_at=NOW if create_version else NOW + timedelta(seconds=1),
    )
    payload = json.loads(canonical_json(evaluation))
    return {key: payload[key] for key in ("source_digest", "metrics", "verdict", "blockers")}


def phase_b(root_text: str, boundary: str) -> None:
    root = Path(root_text)
    portfolio_path, order_path, broker_path = _paths(root)
    portfolio = SQLitePortfolioRepository(portfolio_path)
    orders = SQLiteOrderRepository(order_path)
    broker = _Broker(broker_path, outcome="never")
    path = _RiskPath(ExecutionApplication(repository=orders, broker=broker), broker, submit_enabled=False)
    decisions_before = portfolio.decisions()
    runtime_repository = portfolio
    if boundary == "injected_recovery_error":
        class _FailDuringRecoveryRead:
            def __init__(self, repository):
                self._repository = repository

            def __getattr__(self, name):
                return getattr(self._repository, name)

            def decision(self, decision_id):
                raise ValueError("injected repository recovery read failure")

        runtime_repository = _FailDuringRecoveryRead(portfolio)
    runtime = PortfolioRuntime(
        strategies=_Strategies(), proposals=_Proposals(), snapshots=_Snapshots(),
        repository=runtime_repository, risk_path=path,
    )
    cycle = runtime.evaluate_cycle(
        portfolio_cycle_id="crash-cycle", observed_at=NOW, proposal_cutoff=NOW,
        snapshot_identity="snapshot-1", policy=_policy(), policy_identity="paper-policy",
        policy_revision="1", selected_version_ids=frozenset({VERSION_ID}),
    )
    with sqlite3.connect(broker_path) as db:
        broker_rows = db.execute("SELECT COUNT(*) FROM broker_truth").fetchone()[0]
    order_truth = orders.portfolio_order_truth()
    fills = sum(len(item.fills) for item in order_truth.orders)
    event_count = sum(len(item.events) for item in order_truth.orders)
    reconciliation_blockers = []
    reconciliation_positions = []
    reconciliation_accounting = []
    if boundary in {"partial", "terminal", "broker_only"}:
        account = BrokerAccountSnapshot(
            environment=Environment.PAPER, account_alias="DU-TEST",
            net_liquidation=Decimal(1000), cash=Decimal(900), available_funds=Decimal(900),
            buying_power=Decimal(1800), gross_position_value=Decimal(40 if boundary == "partial" else 100 if boundary == "terminal" else 0),
            excess_liquidity=Decimal(900), maintenance_margin=Decimal(10), cushion=Decimal("0.9"),
            daily_pnl=Decimal(0), unrealized_pnl=Decimal(0), realized_pnl=Decimal(0),
            observed_at=NOW, pnl_source="test",
        )
        positions = ((BrokerPositionSnapshot(
            account_alias="DU-TEST", con_id=1, symbol="AAPL", local_symbol="AAPL",
            security_type="STK", exchange="NASDAQ", currency="USD",
            quantity=Decimal(4), average_cost=Decimal(10), market_value=Decimal(40),
            daily_pnl=Decimal(0), unrealized_pnl=Decimal(0), realized_pnl=Decimal(0), observed_at=NOW,
        ),) if boundary == "partial" else (BrokerPositionSnapshot(
            account_alias="DU-TEST", con_id=1, symbol="AAPL", local_symbol="AAPL",
            security_type="STK", exchange="NASDAQ", currency="USD",
            quantity=Decimal(10), average_cost=Decimal(10), market_value=Decimal(100),
            daily_pnl=Decimal(0), unrealized_pnl=Decimal(0), realized_pnl=Decimal(0), observed_at=NOW,
        ),) if boundary == "terminal" else ())
        broker_portfolio = BrokerAccountPortfolio(account=account, positions=positions)
        open_truth = BrokerOpenOrderTruth(
            account_alias="DU-TEST", observed_at=NOW, snapshot_complete=True,
            open_orders=((BrokerOpenOrder(
                broker_order_id=202 if boundary == "broker_only" else 101, account_alias="DU-TEST", symbol="AAPL", side=Side.BUY,
                quantity=Decimal(10), remaining_quantity=Decimal(6),
            ),) if boundary in {"partial", "broker_only"} else ()),
        )
        class _Truth:
            def broker_open_order_truth(self):
                return open_truth
        reconciliation = PortfolioReconciliationApplication(
            portfolio_repository=portfolio, order_truth=orders, broker_order_truth=_Truth(),
        ).reconcile(broker=broker_portfolio, now=NOW)
        reconciliation_blockers = sorted(item.value for item in reconciliation.blockers)
        reconciliation_positions = [
            {"symbol": item.symbol, "broker_quantity": str(item.broker_quantity), "attributed_quantity": item.attributed_quantity}
            for item in reconciliation.positions
        ]
        reconciliation_accounting = [
            {"strategy_version_id": item.strategy_version_id, "symbol": item.symbol,
             "quantity": item.quantity, "filled_quantity": item.filled_quantity}
            for item in reconciliation.strategy_accounting
        ]
    workflow = PaperWorkflowController()
    report = {
        "before_decisions": len(decisions_before),
        "after_decisions": len(portfolio.decisions()),
        "local_orders": len(orders.portfolio_order_truth().orders),
        "broker_orders": broker_rows,
        "submit_calls_after_restart": broker.submit_calls,
        "portfolio_dispatch_calls_after_restart": path.submit_calls,
        "risk_evaluations_after_restart": path.evaluate_calls,
        "risk_outcomes": [item.risk_outcome for item in portfolio.decisions()],
        "dispatch_recorded": [item.dispatch_outcome_recorded for item in portfolio.decisions()],
        "actions_recovered": [item.recovered for item in cycle.actions] if cycle is not None else [],
        "action_dispatch_halts": [item.dispatch.halt for item in cycle.actions if item.dispatch is not None] if cycle is not None else [],
        "attributions": len(portfolio.execution_attributions()),
        "fills": fills,
        "order_events": event_count,
        "reconciliation_blockers": reconciliation_blockers,
        "reconciliation_positions": reconciliation_positions,
        "reconciliation_accounting": reconciliation_accounting,
        "paper_phase": workflow.phase.value,
        "execution_lease": workflow.lease.value,
        "session_active": workflow.phase.value == "RUNNING",
        "armed": workflow.lease.value == "PAPER",
        "restart_is_session_resume": False,
    }
    if boundary in {"partial", "terminal"}:
        report["performance"] = _performance_semantic(root, create_version=False)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    phase, root_arg, boundary_arg = sys.argv[1:]
    if phase == "a":
        phase_a(root_arg, boundary_arg)
    elif phase == "b":
        phase_b(root_arg, boundary_arg)
    else:
        raise SystemExit(f"unknown phase: {phase}")
