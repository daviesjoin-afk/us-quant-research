"""Provider-neutral inputs and Risk path for portfolio evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from us_quant.trading.domain.portfolio import (
    PortfolioCapitalPolicy,
    PortfolioDecision,
    PortfolioSnapshot,
)
from us_quant.trading.domain.portfolio_runtime import PortfolioDispatchResult
from us_quant.trading.domain.risk import RiskDecision
from us_quant.trading.domain.strategy import StrategyVersion, TradeProposal


class PortfolioProposalSource(Protocol):
    def proposals_for(
        self,
        strategy: StrategyVersion,
        *,
        observed_at: datetime,
        proposal_cutoff: datetime,
    ) -> tuple[TradeProposal, ...]: ...


class PortfolioSnapshotSource(Protocol):
    def snapshot(self, *, observed_at: datetime) -> PortfolioSnapshot: ...


class PortfolioRiskPath(Protocol):
    def evaluate(
        self, decision: PortfolioDecision, *, observed_at: datetime
    ) -> RiskDecision: ...

    def submit(
        self,
        decision: PortfolioDecision,
        risk_decision: RiskDecision,
        *,
        observed_at: datetime,
    ) -> PortfolioDispatchResult: ...


__all__ = ["PortfolioProposalSource", "PortfolioSnapshotSource", "PortfolioRiskPath"]
