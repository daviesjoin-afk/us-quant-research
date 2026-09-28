"""Results returned by one serialized portfolio evaluation cycle."""

from __future__ import annotations

from dataclasses import dataclass

from us_quant.trading.domain.portfolio import PortfolioDecision
from us_quant.trading.domain.risk import RiskDecision


@dataclass(frozen=True, slots=True)
class PortfolioDispatchResult:
    submitted: bool = False
    halt: bool = False
    order_id: str | None = None
    status: str = ""


@dataclass(frozen=True, slots=True)
class PortfolioActionResult:
    decision: PortfolioDecision
    risk: RiskDecision | None
    dispatch: PortfolioDispatchResult | None = None
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class PortfolioCycleResult:
    portfolio_cycle_id: str
    snapshot_identity: str
    decisions: tuple[PortfolioDecision, ...]
    actions: tuple[PortfolioActionResult, ...]

    @property
    def halted(self) -> bool:
        return any(item.dispatch is not None and item.dispatch.halt for item in self.actions)
