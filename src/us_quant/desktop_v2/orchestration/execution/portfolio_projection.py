"""Build display-only views from portfolio application/domain facts."""

from __future__ import annotations

from decimal import Decimal

from us_quant.desktop_v2.pages.execution.portfolio_models import (
    PortfolioOperationsView,
    PortfolioStrategyOperationsRow,
    PortfolioSymbolOperationsRow,
)
from us_quant.trading.domain.portfolio import PortfolioCapitalPolicy, PortfolioSnapshot
from us_quant.trading.domain.portfolio_ledger import PortfolioDecisionRecord
from us_quant.trading.domain.portfolio_reconciliation import PortfolioReconciliationResult


def build_portfolio_operations_view(
    *,
    snapshot: PortfolioSnapshot,
    policy: PortfolioCapitalPolicy,
    decisions: tuple[PortfolioDecisionRecord, ...],
    reconciliation: PortfolioReconciliationResult | None,
    runtime_state: str,
) -> PortfolioOperationsView:
    """Build a deterministic display projection from current and durable facts."""

    if not isinstance(snapshot, PortfolioSnapshot) or not isinstance(policy, PortfolioCapitalPolicy):
        raise TypeError("snapshot and policy are required portfolio facts")
    latest_by_strategy: dict[str, str] = {}
    pending_by_strategy: dict[str, Decimal] = {}
    latest_cycle = "无"
    latest_key = None
    for record in sorted(decisions, key=lambda item: (item.observed_at, item.decision.decision_id)):
        decision = record.decision
        if latest_key is None or record.observed_at >= latest_key:
            latest_key = record.observed_at
            latest_cycle = record.portfolio_cycle_id
        status = decision.decision.value
        if record.risk_outcome:
            status += f" / Risk {record.risk_outcome}"
        if record.dispatch_halt:
            status += " / HALT"
        for strategy_id in decision.strategy_version_ids:
            latest_by_strategy[strategy_id] = status
        if record.risk_outcome == "approved" and record.order_id is None and decision.action is not None:
            value = decision.action.reference_price * decision.action.quantity
            for attribution in decision.attribution:
                pending_by_strategy[attribution.strategy_version_id] = (
                    pending_by_strategy.get(attribution.strategy_version_id, Decimal("0"))
                    + value * Decimal(attribution.signed_requested_quantity)
                    / Decimal(max(1, decision.requested_quantity))
                )

    accounting = {}
    if reconciliation is not None:
        accounting = {
            (row.strategy_version_id, row.symbol): row
            for row in reconciliation.strategy_accounting
        }
    strategy_ids = sorted(
        {row.strategy_version_id for row in policy.allocations}
        | {row.strategy_version_id for row in snapshot.strategy_exposure}
    )
    allocation_by_id = {row.strategy_version_id: row for row in policy.allocations}
    exposure_by_id: dict[str, Decimal] = {}
    shares_by_id: dict[str, int] = {}
    for row in snapshot.strategy_exposure:
        exposure_by_id[row.strategy_version_id] = exposure_by_id.get(row.strategy_version_id, Decimal("0")) + row.notional
        shares_by_id[row.strategy_version_id] = shares_by_id.get(row.strategy_version_id, 0) + row.quantity
    strategy_rows = []
    for strategy_id in strategy_ids:
        allocation = allocation_by_id.get(strategy_id)
        strategy_stats = [
            value for (owner, _symbol), value in accounting.items() if owner == strategy_id
        ]
        strategy_rows.append(PortfolioStrategyOperationsRow(
            strategy_version_id=strategy_id,
            enabled=bool(allocation and allocation.enabled),
            capital_weight=str(allocation.capital_weight if allocation else Decimal("0")),
            capital_ceiling=str(allocation.max_capital if allocation else Decimal("0")),
            gross_ceiling=str(allocation.max_gross_exposure if allocation else Decimal("0")),
            exposure=str(exposure_by_id.get(strategy_id, Decimal("0"))),
            shares=shares_by_id.get(strategy_id, 0),
            pending_exposure=str(pending_by_strategy.get(strategy_id, Decimal("0"))),
            realized_pnl=str(sum((item.realized_pnl for item in strategy_stats), Decimal("0"))),
            fees=str(sum((item.fees for item in strategy_stats), Decimal("0"))),
            last_decision=latest_by_strategy.get(strategy_id, "无"),
        ))

    strategy_by_symbol: dict[str, set[str]] = {}
    for row in snapshot.strategy_exposure:
        strategy_by_symbol.setdefault(row.symbol, set()).add(row.strategy_version_id)
    positions = []
    for position in sorted(snapshot.positions, key=lambda item: item.symbol):
        concentration = position.notional / snapshot.equity if snapshot.equity > 0 else Decimal("0")
        positions.append(PortfolioSymbolOperationsRow(
            symbol=position.symbol,
            shares=position.quantity,
            notional=str(position.notional),
            concentration=f"{concentration:.2%}",
            contributing_strategies=", ".join(sorted(strategy_by_symbol.get(position.symbol, ()))) or "未归属",
        ))
    open_orders = tuple(
        f"{row.side.value} {row.quantity} {row.symbol} / {row.strategy_version_id} / {row.notional}"
        for row in sorted(snapshot.open_orders, key=lambda item: (item.symbol, item.strategy_version_id, item.side.value))
    )
    pending_actions = tuple(
        f"{record.decision.symbol} {record.decision.net_quantity:+d} / "
        f"{', '.join(f'{item.strategy_version_id}:{item.signed_requested_quantity:+d}' for item in record.decision.attribution)} / "
        f"{record.decision.decision_id}"
        for record in decisions
        if record.risk_outcome == "approved" and record.order_id is None and record.decision.action is not None
    )
    blockers = reconciliation.blockers if reconciliation is not None else None
    reconciliation_state = (
        "未知 / 阻止新开仓" if blockers is None
        else "通过" if not blockers
        else "阻止新开仓: " + ", ".join(item.value for item in blockers)
    )
    return PortfolioOperationsView(
        mode="PAPER",
        runtime_state=runtime_state,
        total_capital_limit=str(policy.total_capital_limit),
        cash=str(snapshot.cash),
        equity=str(snapshot.equity),
        gross_exposure=str(snapshot.gross_exposure),
        net_exposure=str(snapshot.net_exposure),
        positions=tuple(positions),
        open_orders=open_orders,
        pending_actions=pending_actions,
        reconciliation_state=reconciliation_state,
        last_cycle=latest_cycle,
        strategy_allocations=tuple(strategy_rows),
    )


def build_unavailable_portfolio_operations_view(
    *,
    policy: PortfolioCapitalPolicy,
    runtime_state: str,
    reconciliation: PortfolioReconciliationResult | None,
) -> PortfolioOperationsView:
    """Display unknown money as unknown and keep the entry gate visibly closed."""

    blockers = tuple(reconciliation.blockers) if reconciliation is not None else ()
    reconciliation_state = (
        "未知 / 阻止新开仓" if reconciliation is None
        else "账户估值或报价不可用 / 阻止新开仓" if not blockers
        else "阻止新开仓: " + ", ".join(item.value for item in blockers)
    )
    return PortfolioOperationsView(
        mode="PAPER",
        runtime_state=runtime_state,
        total_capital_limit=str(policy.total_capital_limit),
        cash="未知",
        equity="未知",
        gross_exposure="未知",
        net_exposure="未知",
        positions=(),
        open_orders=(),
        pending_actions=(),
        reconciliation_state=reconciliation_state,
        last_cycle="无可信快照",
        strategy_allocations=tuple(
            PortfolioStrategyOperationsRow(
                strategy_version_id=item.strategy_version_id,
                enabled=item.enabled,
                capital_weight=str(item.capital_weight),
                capital_ceiling=str(item.max_capital),
                gross_ceiling=str(item.max_gross_exposure),
                exposure="未知",
                shares=0,
                pending_exposure="未知",
                realized_pnl="未知",
                fees="未知",
                last_decision="未执行",
            )
            for item in sorted(policy.allocations, key=lambda row: row.strategy_version_id)
        ),
    )


__all__ = ["build_portfolio_operations_view", "build_unavailable_portfolio_operations_view"]
