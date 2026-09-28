"""Immutable view models for Portfolio operations on the execution page."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PortfolioStrategyOperationsRow:
    strategy_version_id: str
    enabled: bool
    capital_weight: str
    capital_ceiling: str
    gross_ceiling: str
    exposure: str
    shares: int
    pending_exposure: str
    realized_pnl: str
    fees: str
    last_decision: str


@dataclass(frozen=True, slots=True)
class PortfolioSymbolOperationsRow:
    symbol: str
    shares: int
    notional: str
    concentration: str
    contributing_strategies: str


@dataclass(frozen=True, slots=True)
class PortfolioOperationsView:
    mode: str
    runtime_state: str
    total_capital_limit: str
    cash: str
    equity: str
    gross_exposure: str
    net_exposure: str
    positions: tuple[PortfolioSymbolOperationsRow, ...]
    open_orders: tuple[str, ...]
    pending_actions: tuple[str, ...]
    reconciliation_state: str
    last_cycle: str
    strategy_allocations: tuple[PortfolioStrategyOperationsRow, ...]


__all__ = [
    "PortfolioOperationsView",
    "PortfolioStrategyOperationsRow",
    "PortfolioSymbolOperationsRow",
]
