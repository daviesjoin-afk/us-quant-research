"""Immutable presentation models for the native cross-section page."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CrossSectionResearchDraft:
    research_capital: int


@dataclass(frozen=True, slots=True)
class CrossSectionMetricView:
    value: str
    note: str


@dataclass(frozen=True, slots=True)
class CrossSectionSummaryView:
    gate: CrossSectionMetricView
    return_proxy: CrossSectionMetricView
    drawdown: CrossSectionMetricView
    cost_stress: CrossSectionMetricView
    folds: CrossSectionMetricView


@dataclass(frozen=True, slots=True)
class CrossSectionChartRow:
    date: str
    strategy_equity: float
    cost_2x_equity: float


@dataclass(frozen=True, slots=True)
class CrossSectionCandidateRow:
    selected: str
    oos_return: str
    oos_drawdown: str
    training_sharpe: str
    trade_count: str
    cost_2x_return: str


@dataclass(frozen=True, slots=True)
class CrossSectionFoldRow:
    fold: str
    test_interval: str
    selected: str
    oos_return: str
    cost_2x_return: str
    max_risk_exposure: str
    average_cash: str


@dataclass(frozen=True, slots=True)
class CrossSectionResearchView:
    has_report: bool
    summary: CrossSectionSummaryView
    chart_rows: tuple[CrossSectionChartRow, ...]
    candidates: tuple[CrossSectionCandidateRow, ...]
    folds: tuple[CrossSectionFoldRow, ...]


__all__ = [
    "CrossSectionCandidateRow",
    "CrossSectionChartRow",
    "CrossSectionFoldRow",
    "CrossSectionMetricView",
    "CrossSectionResearchDraft",
    "CrossSectionResearchView",
    "CrossSectionSummaryView",
]