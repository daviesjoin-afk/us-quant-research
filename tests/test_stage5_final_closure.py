from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from us_quant.trading.adapters.sqlite.order_repository import SQLiteOrderRepository
from us_quant.trading.adapters.sqlite.portfolio_repository import SQLitePortfolioRepository
from us_quant.trading.application.execution import ExecutionApplication
from us_quant.trading.application.portfolio_reconciliation import PortfolioReconciliationApplication
from us_quant.trading.application.portfolio_runtime import PortfolioRuntime
from us_quant.trading.application.risk import RiskApplication
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
    BrokerPositionSnapshot,
)
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
    OrderStatus,
    Side,
)
from us_quant.trading.domain.portfolio import (
    PortfolioAction,
    PortfolioCapitalPolicy,
    PortfolioDecision,
    PortfolioOrderAttribution,
    PortfolioPosition,
    PortfolioSide,
    PortfolioSnapshot,
    PortfolioStrategyAllocation,
    PortfolioStrategyExposure,
    PortfolioVerdict,
)
from us_quant.trading.domain.portfolio_ledger import (
    PortfolioDecisionRecord,
    PortfolioExecutionAttribution,
    execution_contributions_for_quantity,
)
from us_quant.trading.domain.portfolio_reconciliation import (
    BrokerOpenOrderTruth,
)
from us_quant.trading.domain.risk import LayeredRiskLimits, RiskDecision, RiskLimits
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
    TradeAction,
    TradeProposal,
    parameter_hash_for,
)
from us_quant.trading.ports.broker_execution import BrokerOrderReservation
from us_quant.trading.runtime.config import TradingSessionConfig
from us_quant.trading.runtime.dispatch import OrderDispatch
from us_quant.trading.runtime.portfolio import SessionBook
from us_quant.trading.runtime.portfolio_dispatch import PortfolioOrderDispatchBridge


NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
ACCOUNT_ALIAS = "DU***01"


def _version(version_id: str) -> StrategyVersion:
    parameters = {"market_reference_symbols": ["SPY"]}
    return StrategyVersion(
        definition=StrategyDefinition("intraday-auto-rotation", version_id, "test"),
        identity=StrategyIdentity(
            "intraday-auto-rotation", version_id, parameter_hash_for(parameters)
        ),
        semver="1.0.0",
        status=StrategyStatus.PAPER_SHADOW,
        mode=StrategyMode.PAPER_SHADOW,
        parameters=parameters,
        universe_hash="universe",
        code_hash="code",
        risk_budget_pct=Decimal("0.01"),
        gate_passed=True,
        gate_reason="approved for Paper",
        created_at=NOW,
        updated_at=NOW,
    )


class _Strategies:
    def __init__(self, versions):
        self._versions = tuple(versions)

    def list_versions(self):
        return self._versions


class _Proposals:
    def __init__(self, versions):
        self._mapping = {
            versions[0].version_id: TradeProposal(
                versions[0].identity, "AAPL", TradeAction.BUY, 10,
                Decimal("10"), "strategy-a entry", NOW,
            ),
            versions[1].version_id: TradeProposal(
                versions[1].identity, "AAPL", TradeAction.SELL, 6,
                Decimal("10"), "strategy-b reduction", NOW,
            ),
            versions[2].version_id: TradeProposal(
                versions[2].identity, "AAPL", TradeAction.HOLD, 0,
                Decimal("10"), "strategy-c hold", NOW,
            ),
        }

    def proposals_for(self, strategy, *, observed_at, proposal_cutoff, portfolio_snapshot):
        assert observed_at == proposal_cutoff == NOW
        assert portfolio_snapshot.observed_at == NOW
        return (self._mapping[strategy.version_id],)


class _SnapshotSource:
    def snapshot(self, *, observed_at):
        assert observed_at == NOW
        return PortfolioSnapshot(
            cash=Decimal("9940"),
            equity=Decimal("10000"),
            gross_exposure=Decimal("60"),
            net_exposure=Decimal("60"),
            positions=(PortfolioPosition("AAPL", 6, Decimal("60")),),
            strategy_exposure=(PortfolioStrategyExposure(
                "strategy-b", "AAPL", Decimal("60"), 6,
                average_cost=Decimal("10"),
            ),),
            observed_at=NOW,
        )


class _ZeroNetSnapshotSource:
    def snapshot(self, *, observed_at):
        return PortfolioSnapshot(
            cash=Decimal("9950"),
            equity=Decimal("10000"),
            gross_exposure=Decimal("50"),
            net_exposure=Decimal("50"),
            positions=(PortfolioPosition("AAPL", 5, Decimal("50")),),
            strategy_exposure=(PortfolioStrategyExposure(
                "strategy-b", "AAPL", Decimal("50"), 5,
                average_cost=Decimal("10"),
            ),),
            observed_at=observed_at,
        )


class _ZeroNetProposals:
    def __init__(self, versions):
        self.proposals = {
            versions[0].version_id: TradeProposal(
                versions[0].identity, "AAPL", TradeAction.BUY, 5,
                Decimal("10"), "strategy-a entry", NOW,
            ),
            versions[1].version_id: TradeProposal(
                versions[1].identity, "AAPL", TradeAction.SELL, 5,
                Decimal("10"), "strategy-b reduction", NOW,
            ),
        }

    def proposals_for(self, strategy, *, observed_at, proposal_cutoff, portfolio_snapshot):
        assert observed_at == proposal_cutoff == NOW
        return (self.proposals[strategy.version_id],)


class _CountingRisk(RiskApplication):
    def __init__(self):
        super().__init__(LayeredRiskLimits(account=RiskLimits(
            max_gross_exposure_pct=Decimal("1"),
            max_position_exposure_pct=Decimal("1"),
            daily_loss_halt_pct=Decimal("0.10"),
            drawdown_halt_pct=Decimal("0.50"),
        )))
        self.calls = 0

    def evaluate(self, **kwargs):
        self.calls += 1
        return super().evaluate(**kwargs)


class _TracingOrderRepository(SQLiteOrderRepository):
    def __init__(self, path, trace):
        self.trace = trace
        super().__init__(path)

    def record_intent(self, intent, *, broker_order_id, account_alias):
        super().record_intent(
            intent, broker_order_id=broker_order_id, account_alias=account_alias
        )
        self.trace.append("durable")


class _Broker:
    def __init__(self, order_repository, trace):
        self.order_repository = order_repository
        self.trace = trace
        self.next_id = 42
        self.submissions = 0
        self.reservations = {}

    def connect(self):
        pass

    def disconnect(self):
        pass

    def reserve(self, intent):
        self.trace.append("reserve")
        reservation = BrokerOrderReservation(
            intent.order_id, self.next_id, ACCOUNT_ALIAS
        )
        self.reservations[intent.order_id] = reservation
        self.next_id += 1
        return reservation

    def submit(self, reservation):
        assert self.order_repository.intent(reservation.order_id) is not None
        self.trace.append("submit")
        self.submissions += 1

    def cancel(self, order_id):
        return True

    def events(self):
        return ()

    def fills(self):
        return ()


class _FreshBrokerOrders:
    def broker_open_order_truth(self):
        return BrokerOpenOrderTruth(
            account_alias=ACCOUNT_ALIAS,
            observed_at=NOW + timedelta(minutes=2),
            snapshot_complete=True,
            open_orders=(),
        )


def _policy(versions):
    return PortfolioCapitalPolicy(
        total_capital_limit=Decimal("1000"),
        max_gross_exposure=Decimal("1000"),
        max_net_exposure=Decimal("1000"),
        max_single_position_notional=Decimal("1000"),
        max_symbol_concentration=Decimal("1"),
        max_strategy_concentration=Decimal("1"),
        max_positions=5,
        max_open_orders=5,
        allocations=tuple(
            PortfolioStrategyAllocation(
                version.version_id, Decimal("0.3"), Decimal("300"),
                Decimal("300"), True,
            )
            for version in versions
        ),
    )


def _seed_strategy_b_position(portfolio_repository, order_repository):
    decision_id = "seed-strategy-b-position"
    decision = PortfolioDecision(
        decision_id=decision_id,
        decision=PortfolioVerdict.APPROVE,
        symbol="AAPL",
        strategy_version_ids=("strategy-b",),
        requested_quantity=6,
        net_quantity=6,
        blocker=None,
        attribution=(PortfolioOrderAttribution(
            decision_id, "strategy-b", "seed-b", "AAPL", 6
        ),),
        action=PortfolioAction("AAPL", PortfolioSide.BUY, 6, Decimal("10")),
    )
    record = portfolio_repository.record_decision(PortfolioDecisionRecord(
        decision=decision,
        portfolio_cycle_id="seed-cycle",
        observed_at=NOW - timedelta(minutes=1),
        proposal_cutoff=NOW - timedelta(minutes=1),
        snapshot_identity="seed-snapshot",
        policy_identity="seed-policy",
        policy_revision="1",
        created_at=NOW - timedelta(minutes=1),
        risk_outcome="approved",
        risk_decision=RiskDecision.approve(requested_quantity=6),
    ))
    intent = OrderIntent.create(
        session_id="seed-session",
        strategy_version_id="portfolio-runtime",
        signal_symbol="AAPL",
        execution_symbol="AAPL",
        side=Side.BUY,
        quantity=6,
        limit_price=Decimal("10"),
        reason="seed strategy ownership",
    )
    intent = replace(intent, order_id="seed-order", client_order_id="uq-seed-order")
    order_repository.record_intent(
        intent, broker_order_id=41, account_alias=ACCOUNT_ALIAS
    )
    order_repository.record_fill(ExecutionFill(
        execution_id="seed-fill",
        order_id=intent.order_id,
        broker_order_id=41,
        symbol="AAPL",
        side=Side.BUY,
        quantity=Decimal("6"),
        price=Decimal("10"),
        occurred_at=NOW - timedelta(minutes=1),
        fee=Decimal("0.06"),
    ))
    order_repository.record_event(OrderEvent(
        order_id=intent.order_id,
        broker_order_id=41,
        status=OrderStatus.FILLED,
        filled=Decimal("6"),
        remaining=Decimal("0"),
        occurred_at=NOW - timedelta(minutes=1),
    ))
    attribution = PortfolioExecutionAttribution(
        order_id=intent.order_id,
        portfolio_decision_id=decision_id,
        symbol="AAPL",
        side=PortfolioSide.BUY,
        quantity=6,
        contributions=execution_contributions_for_quantity(decision, 6),
    )
    portfolio_repository.record_dispatch_outcome(
        replace(
            record,
            order_id=intent.order_id,
            dispatch_outcome_recorded=True,
            dispatch_submitted=True,
            dispatch_status="submitted",
        ),
        attribution,
        expected_revision=record.revision,
    )


def _broker_portfolio(quantity: int, observed_at: datetime):
    account = BrokerAccountSnapshot(
        environment=Environment.PAPER,
        account_alias=ACCOUNT_ALIAS,
        net_liquidation=Decimal("10005.90"),
        cash=Decimal("9895.90"),
        available_funds=Decimal("9895.90"),
        buying_power=Decimal("19791.80"),
        gross_position_value=Decimal("110"),
        excess_liquidity=Decimal("9800"),
        maintenance_margin=Decimal("100"),
        cushion=Decimal("0.99"),
        daily_pnl=Decimal("5.90"),
        unrealized_pnl=Decimal("10"),
        realized_pnl=Decimal("6"),
        observed_at=observed_at,
        pnl_source="deterministic test broker",
    )
    positions = (
        BrokerPositionSnapshot(
            account_alias=ACCOUNT_ALIAS,
            con_id=1,
            symbol="AAPL",
            local_symbol="AAPL",
            security_type="STK",
            exchange="NASDAQ",
            currency="USD",
            quantity=Decimal(quantity),
            average_cost=Decimal("10.4"),
            market_value=Decimal(quantity * 11),
            daily_pnl=Decimal("0"),
            unrealized_pnl=Decimal("6"),
            realized_pnl=Decimal("6"),
            observed_at=observed_at,
        ),
    ) if quantity else ()
    return BrokerAccountPortfolio(account=account, positions=positions)


def test_final_paper_net_order_is_risk_dispatched_filled_attributed_and_reconciled(tmp_path):
    versions = tuple(_version(name) for name in ("strategy-a", "strategy-b", "strategy-c"))
    trace = []
    portfolio_repository = SQLitePortfolioRepository(tmp_path / "portfolio.sqlite")
    order_repository = _TracingOrderRepository(tmp_path / "orders.sqlite", trace)
    _seed_strategy_b_position(portfolio_repository, order_repository)
    trace.clear()
    broker = _Broker(order_repository, trace)
    risk = RiskApplication(LayeredRiskLimits(account=RiskLimits(
        max_gross_exposure_pct=Decimal("1"),
        max_position_exposure_pct=Decimal("1"),
        daily_loss_halt_pct=Decimal("0.10"),
        drawdown_halt_pct=Decimal("0.50"),
    )))
    config = TradingSessionConfig(
        initial_cash=Decimal("10000"),
        capital_source="deterministic portfolio Paper closure test",
    )
    book = SessionBook(initial_cash=config.initial_cash, commission=Decimal("0.01"))
    book.reset()
    dispatch = OrderDispatch(
        config=config,
        risk=risk,
        execution=ExecutionApplication(repository=order_repository, broker=broker),
    )
    risk_path = PortfolioOrderDispatchBridge(
        dispatch=dispatch,
        book=book,
        session_id="portfolio-session",
        allowed_symbols=frozenset({"AAPL"}),
    )
    runtime = PortfolioRuntime(
        strategies=_Strategies(versions),
        proposals=_Proposals(versions),
        snapshots=_SnapshotSource(),
        repository=portfolio_repository,
        risk_path=risk_path,
    )

    cycle = runtime.evaluate_cycle(
        portfolio_cycle_id="paper-final-cycle",
        observed_at=NOW,
        proposal_cutoff=NOW,
        snapshot_identity="snapshot-before-net-fill",
        policy=_policy(versions),
        policy_identity="paper-plan",
        policy_revision="1",
        selected_version_ids=frozenset(item.version_id for item in versions),
    )

    assert len(cycle.decisions) == 1
    decision = cycle.decisions[0]
    assert decision.action == PortfolioAction("AAPL", PortfolioSide.BUY, 4, Decimal("10"))
    saved = portfolio_repository.decision(decision.decision_id)
    assert saved is not None and saved.risk_decision is not None
    assert saved.risk_decision.approved and saved.risk_decision.approved_quantity == 4
    assert saved.order_id is not None and saved.dispatch_submitted
    intent = order_repository.intent(saved.order_id)
    assert intent is not None and intent.side is Side.BUY and intent.quantity == 4
    assert order_repository.broker_order_id(saved.order_id) == 42
    assert trace == ["reserve", "durable", "submit"]
    assert broker.submissions == 1

    order_repository.record_fill(ExecutionFill(
        execution_id="net-fill",
        order_id=intent.order_id,
        broker_order_id=42,
        symbol="AAPL",
        side=Side.BUY,
        quantity=Decimal("4"),
        price=Decimal("11"),
        occurred_at=NOW + timedelta(seconds=30),
        fee=Decimal("0.04"),
    ))
    order_repository.record_event(OrderEvent(
        order_id=intent.order_id,
        broker_order_id=42,
        status=OrderStatus.FILLED,
        filled=Decimal("4"),
        remaining=Decimal("0"),
        occurred_at=NOW + timedelta(seconds=30),
    ))

    reconciliation = PortfolioReconciliationApplication(
        portfolio_repository=portfolio_repository,
        order_truth=order_repository,
        broker_order_truth=_FreshBrokerOrders(),
    ).reconcile(
        broker=_broker_portfolio(10, NOW + timedelta(minutes=2)),
        now=NOW + timedelta(minutes=2),
    )

    assert reconciliation.can_open_exposure
    assert reconciliation.positions[0].broker_quantity == Decimal("10")
    assert reconciliation.positions[0].attributed_quantity == 10
    accounting = {item.strategy_version_id: item for item in reconciliation.strategy_accounting}
    assert accounting["strategy-a"].quantity == 10
    assert accounting["strategy-b"].quantity == 0
    assert accounting["strategy-b"].realized_pnl == Decimal("6")
    assert accounting["strategy-a"].slippage == Decimal("10")
    assert accounting["strategy-b"].slippage == Decimal("-6")
    assert sum((item.fees for item in accounting.values()), Decimal("0")) == Decimal("0.10")


def test_final_zero_net_retains_audit_without_risk_or_execution(tmp_path):
    versions = (_version("strategy-a"), _version("strategy-b"))
    portfolio_repository = SQLitePortfolioRepository(tmp_path / "portfolio.sqlite")
    trace = []
    order_repository = _TracingOrderRepository(tmp_path / "orders.sqlite", trace)
    broker = _Broker(order_repository, trace)
    risk = _CountingRisk()
    config = TradingSessionConfig(
        initial_cash=Decimal("10000"),
        capital_source="deterministic zero-net closure test",
    )
    book = SessionBook(initial_cash=config.initial_cash, commission=Decimal("0.01"))
    book.reset()
    bridge = PortfolioOrderDispatchBridge(
        dispatch=OrderDispatch(
            config=config,
            risk=risk,
            execution=ExecutionApplication(repository=order_repository, broker=broker),
        ),
        book=book,
        session_id="zero-net-session",
        allowed_symbols=frozenset({"AAPL"}),
    )
    runtime = PortfolioRuntime(
        strategies=_Strategies(versions),
        proposals=_ZeroNetProposals(versions),
        snapshots=_ZeroNetSnapshotSource(),
        repository=portfolio_repository,
        risk_path=bridge,
    )

    result = runtime.evaluate_cycle(
        portfolio_cycle_id="zero-net-cycle",
        observed_at=NOW,
        proposal_cutoff=NOW,
        snapshot_identity="zero-net-snapshot",
        policy=_policy(versions),
        policy_identity="paper-plan",
        policy_revision="1",
        selected_version_ids=frozenset(item.version_id for item in versions),
    )

    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.decision is PortfolioVerdict.APPROVE
    assert decision.net_quantity == 0 and decision.action is None
    assert {item.strategy_version_id: item.signed_requested_quantity for item in decision.attribution} == {
        "strategy-a": 5,
        "strategy-b": -5,
    }
    assert risk.calls == 0
    assert broker.reservations == {}
    assert broker.submissions == 0
    assert order_repository.portfolio_order_truth().orders == ()
    saved = portfolio_repository.decision(decision.decision_id)
    assert saved is not None and saved.order_id is None
    assert not saved.dispatch_outcome_recorded
    assert saved.decision.attribution == decision.attribution
