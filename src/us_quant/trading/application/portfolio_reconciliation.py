"""Rebuild portfolio ownership and accounting from durable and broker facts."""

from __future__ import annotations

from datetime import datetime, timedelta

from us_quant.trading.domain.account import BrokerAccountPortfolio
from us_quant.trading.domain.portfolio_reconciliation import (
    PortfolioReconciliationResult,
    reconcile_portfolio_truth,
    portfolio_order_truth_for_reconciliation,
)
from us_quant.trading.ports.broker_open_order_truth import BrokerOpenOrderTruthSource
from us_quant.trading.ports.portfolio_order_truth import PortfolioOrderTruthSource
from us_quant.trading.ports.portfolio_repository import PortfolioStateRepositoryPort


class PortfolioReconciliationApplication:
    """Stateless recovery authority; each call reloads every durable input."""

    def __init__(
        self,
        *,
        portfolio_repository: PortfolioStateRepositoryPort,
        order_truth: PortfolioOrderTruthSource,
        broker_order_truth: BrokerOpenOrderTruthSource,
        max_snapshot_age: timedelta = timedelta(minutes=5),
    ) -> None:
        self._portfolio_repository = portfolio_repository
        self._order_truth = order_truth
        self._broker_order_truth = broker_order_truth
        self._max_snapshot_age = max_snapshot_age

    def reconcile(
        self,
        *,
        broker: BrokerAccountPortfolio,
        now: datetime,
    ) -> PortfolioReconciliationResult:
        """Build a fresh result with no trust in process-local portfolio state."""
        broker_order_truth = self._broker_order_truth.broker_open_order_truth()
        decisions = self._portfolio_repository.decisions()
        execution_attributions = self._portfolio_repository.execution_attributions()
        order_truth = portfolio_order_truth_for_reconciliation(
            order_truth=self._order_truth.portfolio_order_truth(),
            broker_order_truth=broker_order_truth,
            decisions=decisions,
            execution_attributions=execution_attributions,
        )
        return reconcile_portfolio_truth(
            now=now,
            broker=broker,
            broker_order_truth=broker_order_truth,
            order_truth=order_truth,
            decisions=decisions,
            execution_attributions=execution_attributions,
            max_snapshot_age=self._max_snapshot_age,
        )


__all__ = ["PortfolioReconciliationApplication"]
