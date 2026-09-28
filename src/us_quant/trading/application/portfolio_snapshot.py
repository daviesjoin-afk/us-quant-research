"""Build allocator snapshots from one fresh broker/reconciliation observation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from us_quant.trading.domain.account import BrokerAccountPortfolio
from us_quant.trading.domain.portfolio import (
    PortfolioOpenOrder,
    PortfolioPosition,
    PortfolioSide,
    PortfolioSnapshot,
    PortfolioStrategyExposure,
)
from us_quant.trading.domain.portfolio_ledger import execution_contributions_for_quantity
from us_quant.trading.domain.portfolio_reconciliation import (
    BrokerOpenOrderTruth,
    PortfolioOrderTruth,
    PortfolioReconciliationBlocker,
    PortfolioReconciliationResult,
    reconcile_portfolio_truth,
    portfolio_order_truth_for_reconciliation,
)
from us_quant.trading.ports.broker_open_order_truth import BrokerOpenOrderTruthSource
from us_quant.trading.ports.portfolio_order_truth import PortfolioOrderTruthSource
from us_quant.trading.ports.portfolio_repository import PortfolioStateRepositoryPort


class PortfolioSnapshotUnavailable(RuntimeError):
    """Fresh capital truth is unavailable or reconciliation is blocked."""

    def __init__(self, message: str, *, reconciliation: PortfolioReconciliationResult | None = None) -> None:
        super().__init__(message)
        self.reconciliation = reconciliation


class BrokerPortfolioSnapshotSource:
    """Read broker and durable truth once per allocator cycle; never cache clear state."""

    def __init__(
        self,
        *,
        broker_portfolio: Callable[[], BrokerAccountPortfolio],
        broker_open_orders: BrokerOpenOrderTruthSource,
        order_truth: PortfolioOrderTruthSource,
        portfolio_repository: PortfolioStateRepositoryPort,
    ) -> None:
        self._broker_portfolio = broker_portfolio
        self._broker_open_orders = broker_open_orders
        self._order_truth = order_truth
        self._portfolio_repository = portfolio_repository
        self.last_reconciliation: PortfolioReconciliationResult | None = None
        self.last_snapshot: PortfolioSnapshot | None = None

    def snapshot(self, *, observed_at: datetime) -> PortfolioSnapshot:
        broker = self._broker_portfolio()
        open_truth = self._broker_open_orders.broker_open_order_truth()
        order_truth = self._order_truth.portfolio_order_truth()
        decisions = self._portfolio_repository.decisions()
        attributions = self._portfolio_repository.execution_attributions()
        order_truth = portfolio_order_truth_for_reconciliation(
            order_truth=order_truth,
            broker_order_truth=open_truth,
            decisions=decisions,
            execution_attributions=attributions,
        )
        result = reconcile_portfolio_truth(
            now=observed_at,
            broker=broker,
            broker_order_truth=open_truth,
            order_truth=order_truth,
            decisions=decisions,
            execution_attributions=attributions,
        )
        self.last_reconciliation = result
        if not result.can_open_exposure:
            codes = ", ".join(item.value for item in result.blockers)
            raise PortfolioSnapshotUnavailable(
                f"fresh portfolio reconciliation blocks new exposure: {codes}",
                reconciliation=result,
            )
        account = broker.account
        if account.cash is None or account.net_liquidation is None:
            raise PortfolioSnapshotUnavailable("broker cash or equity is unknown")
        if account.account_alias != open_truth.account_alias or not open_truth.snapshot_complete:
            raise PortfolioSnapshotUnavailable("broker open-order truth is incomplete or account-mismatched")

        positions = []
        market_price_by_symbol: dict[str, Decimal] = {}
        signed_value = Decimal("0")
        gross = Decimal("0")
        for item in broker.positions:
            if item.quantity == 0:
                continue
            if item.quantity < 0:
                raise PortfolioSnapshotUnavailable("short broker positions are not enabled by the Paper portfolio policy")
            if item.quantity != item.quantity.to_integral_value() or item.market_value is None:
                raise PortfolioSnapshotUnavailable("fractional or unvalued broker position cannot enter Paper allocation")
            quantity = int(abs(item.quantity))
            notional = abs(item.market_value)
            positions.append(PortfolioPosition(item.symbol, quantity, notional))
            market_price_by_symbol[item.symbol.strip().upper()] = notional / quantity
            signed_value += item.market_value
            gross += notional

        strategy_exposure = []
        for item in result.strategy_accounting:
            if item.quantity == 0:
                continue
            if item.quantity < 0:
                raise PortfolioSnapshotUnavailable("short strategy ownership is not enabled by the Paper portfolio policy")
            if item.average_cost is None or item.average_cost <= 0:
                raise PortfolioSnapshotUnavailable("strategy position has no durable average cost")
            market_price = market_price_by_symbol.get(item.symbol.strip().upper())
            if market_price is None:
                raise PortfolioSnapshotUnavailable("strategy position lacks a fresh broker market valuation")
            strategy_exposure.append(PortfolioStrategyExposure(
                strategy_version_id=item.strategy_version_id,
                symbol=item.symbol,
                quantity=abs(item.quantity),
                notional=market_price * abs(item.quantity),
                realized_pnl=item.realized_pnl,
                average_cost=item.average_cost,
                trades_today=item.trades_today,
            ))

        orders = _project_open_orders(
            broker_truth=open_truth,
            durable_order_truth=order_truth,
            decisions=decisions,
            execution_attributions=attributions,
        )
        reserved_buy_notional = sum(
            (item.notional for item in orders if item.side is PortfolioSide.BUY),
            Decimal("0"),
        )
        gross += reserved_buy_notional
        snapshot = PortfolioSnapshot(
            cash=account.cash,
            equity=account.net_liquidation,
            gross_exposure=gross,
            # Pending buys can become account exposure without another portfolio
            # decision. Include their conservative reservation in the net ceiling.
            net_exposure=signed_value + reserved_buy_notional,
            positions=tuple(sorted(positions, key=lambda item: item.symbol)),
            open_orders=orders,
            strategy_exposure=tuple(sorted(strategy_exposure, key=lambda item: (item.strategy_version_id, item.symbol))),
            observed_at=observed_at,
        )
        self.last_snapshot = snapshot
        return snapshot


def _project_open_orders(*, broker_truth: BrokerOpenOrderTruth, durable_order_truth: PortfolioOrderTruth, decisions, execution_attributions) -> tuple[PortfolioOpenOrder, ...]:
    durable_by_broker_id = {
        item.broker_order_id: item
        for item in durable_order_truth.orders
        if item.broker_order_id is not None
    }
    decisions_by_id = {item.decision.decision_id: item.decision for item in decisions}
    order_attribution_by_order = {item.order_id: item for item in execution_attributions}
    result = []
    for broker_order in broker_truth.open_orders:
        record = durable_by_broker_id.get(broker_order.broker_order_id)
        if record is None or record.intent is None:
            raise PortfolioSnapshotUnavailable("broker open order has no durable local identity")
        intent = record.intent
        if (
            record.account_alias != broker_order.account_alias
            or intent.execution_symbol != broker_order.symbol.strip().upper()
            or intent.side is not broker_order.side
            or intent.quantity <= 0
            or broker_order.remaining_quantity is None
            or broker_order.remaining_quantity != broker_order.remaining_quantity.to_integral_value()
        ):
            raise PortfolioSnapshotUnavailable("broker open order does not match durable intent facts")
        remaining = int(broker_order.remaining_quantity)
        if remaining <= 0:
            raise PortfolioSnapshotUnavailable("broker open-order remaining quantity is not positive")
        order_attribution = order_attribution_by_order.get(intent.order_id)
        decision = decisions_by_id.get(order_attribution.portfolio_decision_id) if order_attribution else None
        if decision is None:
            raise PortfolioSnapshotUnavailable("broker open order has no durable portfolio decision")
        contributions = execution_contributions_for_quantity(decision, remaining)
        price = intent.limit_price
        for contribution in contributions:
            side = (
                PortfolioSide.BUY
                if contribution.signed_quantity > 0
                else PortfolioSide.SELL
            )
            notional = price * abs(contribution.signed_quantity)
            result.append(PortfolioOpenOrder(
                strategy_version_id=contribution.strategy_version_id,
                symbol=broker_order.symbol,
                side=side,
                notional=notional if side is PortfolioSide.BUY else Decimal("0"),
                quantity=abs(contribution.signed_quantity),
            ))
    return tuple(sorted(result, key=lambda item: (item.strategy_version_id, item.symbol, item.side.value, item.quantity)))


__all__ = ["BrokerPortfolioSnapshotSource", "PortfolioSnapshotUnavailable"]
