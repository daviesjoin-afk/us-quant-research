"""Composition for one governed multi-strategy Paper session."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

from us_quant.trading.adapters.ibkr.portfolio_snapshot import (
    IBKRPaperPortfolioObservationSource,
)
from us_quant.trading.adapters.sqlite.portfolio_repository import (
    SQLitePortfolioRepository,
)
from us_quant.trading.application.portfolio_operations import (
    PortfolioOperatingPlanApplication,
    PortfolioOperationsApplication,
)
from us_quant.trading.application.portfolio_snapshot import (
    BrokerPortfolioSnapshotSource,
)
from us_quant.trading.composition.execution import build_execution_application
from us_quant.trading.composition.runtime import (
    PortfolioRuntimeRegistry,
    build_portfolio_runtime,
)
from us_quant.trading.domain.portfolio_operations import PortfolioOperatingPlan
from us_quant.trading.runtime.config import TradingSessionConfig
from us_quant.trading.runtime.dispatch import OrderDispatch
from us_quant.trading.runtime.portfolio import SessionBook
from us_quant.trading.runtime.portfolio_cycle import PortfolioPaperCycleDriver
from us_quant.trading.runtime.portfolio_dispatch import PortfolioOrderDispatchBridge
from us_quant.trading.runtime.portfolio_paper import PortfolioPaperEngine
from us_quant.trading.runtime.portfolio_strategies import PortfolioStrategyWorkers
from us_quant.trading.runtime.session import SessionState
from us_quant.trading.composition.session_config import resolve_paper_session_capital


@dataclass(frozen=True, slots=True)
class PortfolioPaperSessionComposition:
    engine: PortfolioPaperEngine
    orders: object
    session_id: str
    candidate_count: int
    max_order_notional: Decimal


def build_portfolio_paper_session(
    *,
    runtime_root: str | Path,
    request,
    reading,
    order_repository,
    paper_service,
    strategies,
    plan_application: PortfolioOperatingPlanApplication,
    runtime_registry: PortfolioRuntimeRegistry,
    risk,
    autonomous_entries_allowed: Callable[[], bool],
    publish_operations_facts: Callable[..., None],
) -> PortfolioPaperSessionComposition:
    """Build the one Paper book, allocator, snapshot source and dispatch path."""

    portfolio_launch = request.portfolio
    if portfolio_launch is None:
        raise RuntimeError("Paper portfolio launch fact is missing")
    plan = plan_application.load()
    if (
        plan.plan_id != portfolio_launch.plan_id
        or plan.revision != portfolio_launch.plan_revision
        or tuple(plan.selected_version_ids) != portfolio_launch.selected_version_ids
        or plan.policy != portfolio_launch.policy
    ):
        raise RuntimeError("frozen Paper portfolio plan changed before runtime composition")
    selected_strategies = plan_application.selected_versions(plan)
    if tuple(item.version_id for item in selected_strategies) != portfolio_launch.selected_version_ids:
        raise RuntimeError("selected Paper strategy set changed before runtime composition")
    for version, frozen in zip(selected_strategies, portfolio_launch.strategies, strict=True):
        if (
            version.identity != frozen.identity
            or version.parameter_hash != frozen.parameter_hash
            or version.status.value != frozen.status
            or version.mode.value != frozen.mode
        ):
            raise RuntimeError("selected Paper strategy facts changed before runtime composition")

    paper_capital = resolve_paper_session_capital(
        net_liquidation=reading.net_liquidation,
        cash=reading.cash,
        requested_limit=request.plan.requested_capital_limit,
    )
    commission = max(
        (
            Decimal(str(version.parameters.get("commission_per_order", "0.35")))
            for version in selected_strategies
        ),
        default=Decimal("0.35"),
    )
    config = TradingSessionConfig(
        initial_cash=Decimal(paper_capital),
        capital_source=(
            f"IBKR Paper {reading.account_alias} 现金约束；"
            f"Portfolio plan {plan.plan_id} revision {plan.revision}"
        ),
        commission_per_order=commission,
        daily_loss_limit=Decimal(paper_capital) * Decimal("0.01"),
    )

    execution = build_execution_application(repository=order_repository, broker=paper_service)
    book = SessionBook(
        initial_cash=config.initial_cash,
        commission=config.commission_per_order,
    )
    book.reset()
    session = SessionState()
    session.begin(candidate_count=len(request.candidates), warmup_minutes=config.warmup_minutes)
    assert session.session_id is not None
    session_id = session.session_id
    dispatch = OrderDispatch(config=config, risk=risk, execution=execution)

    portfolio_repository = SQLitePortfolioRepository(
        Path(runtime_root) / "portfolio_execution.sqlite3"
    )
    broker_observation = IBKRPaperPortfolioObservationSource(
        orders=paper_service,
        session_id=session_id,
    )
    portfolio_snapshot = BrokerPortfolioSnapshotSource(
        broker_portfolio=broker_observation.broker_portfolio,
        broker_open_orders=broker_observation,
        order_truth=order_repository,
        portfolio_repository=portfolio_repository,
    )

    def publish_operations_view() -> None:
        reconciliation = portfolio_snapshot.last_reconciliation
        runtime_state = (
            "STOPPING" if session.stop_requested
            else "RUNNING" if session.active
            else "HALTED"
        )
        publish_operations_facts(
            snapshot=portfolio_snapshot.last_snapshot,
            reconciliation=reconciliation,
            decisions=portfolio_repository.decisions(),
            plan=plan,
            runtime_state=runtime_state,
        )

    workers = PortfolioStrategyWorkers(
        strategies=selected_strategies,
        candidates=request.candidates,
        policy=plan.policy,
    )
    portfolio_dispatch = PortfolioOrderDispatchBridge(
        dispatch=dispatch,
        book=book,
        session_id=session_id,
        allowed_symbols=frozenset(request.candidate_symbols),
    )
    portfolio_runtime = build_portfolio_runtime(
        strategies=strategies,
        proposals=workers,
        snapshots=portfolio_snapshot,
        repository=portfolio_repository,
        risk_path=portfolio_dispatch,
    )
    runtime_registry.register(account_alias=reading.account_alias, runtime=portfolio_runtime)
    operations = PortfolioOperationsApplication(
        runtime=portfolio_runtime,
        repository=portfolio_repository,
    )
    cycle_driver = PortfolioPaperCycleDriver(
        operations=operations,
        plan_application=plan_application,
        frozen_plan=plan,
        workers=workers,
        session_id=session_id,
        account_alias=reading.account_alias,
        broker_observation_source=broker_observation,
        autonomous_entries_allowed=autonomous_entries_allowed,
        publish_operations_view=publish_operations_view,
    )
    engine = PortfolioPaperEngine(
        config=config,
        candidate_count=len(request.candidates),
        book=book,
        session=session,
        portfolio_runtime=portfolio_runtime,
        launch_plan=plan,
        portfolio_cycle=cycle_driver,
    )
    max_order_notional = min(
        Decimal(paper_capital),
        plan.policy.max_gross_exposure,
        plan.policy.max_single_position_notional,
    )
    return PortfolioPaperSessionComposition(
        engine=engine,
        orders=paper_service,
        session_id=session_id,
        candidate_count=len(request.candidates),
        max_order_notional=max_order_notional,
    )


__all__ = [
    "PortfolioPaperSessionComposition",
    "build_portfolio_paper_session",
]
