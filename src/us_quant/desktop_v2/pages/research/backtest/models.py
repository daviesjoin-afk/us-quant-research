"""Immutable presentation models for the native backtest page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class BacktestStrategyOption:
    version_id: str
    label: str


@dataclass(frozen=True, slots=True)
class BacktestFormDraft:
    strategy_version_id: str
    symbol: str
    start_date: date
    end_date: date

    initial_equity: int
    target_weight_percent: int

    per_share_commission: str
    minimum_commission: str
    slippage_bps: str


@dataclass(frozen=True, slots=True)
class BacktestMetricView:
    value: str
    note: str


@dataclass(frozen=True, slots=True)
class BacktestSummaryView:
    total_return: BacktestMetricView
    cagr: BacktestMetricView
    sharpe: BacktestMetricView
    drawdown: BacktestMetricView
    trades: BacktestMetricView


@dataclass(frozen=True, slots=True)
class BacktestComparisonRow:
    run_id: str
    run_id_display: str

    strategy: str
    symbol: str
    interval: str

    total_return: str
    annualized_return: str
    sharpe: str
    sortino: str
    calmar: str
    max_drawdown: str
    turnover: str
    trade_count: str
    commission: str


@dataclass(frozen=True, slots=True)
class BacktestTradeRow:
    signal_date: str
    fill_date: str

    signal_symbol: str
    execution_symbol: str

    side: str
    quantity: str

    raw_price: str
    fill_price: str
    slippage_cost: str
    commission: str

    position_after: str
    cash_after: str
    reason: str


@dataclass(frozen=True, slots=True)
class BacktestChartView:
    symbol: str
    points: tuple[tuple[date, float], ...]
    title: str


@dataclass(frozen=True, slots=True)
class BacktestDetailView:
    run_id: str | None
    summary: BacktestSummaryView
    chart: BacktestChartView | None
    trades: tuple[BacktestTradeRow, ...]
    evidence: str


@dataclass(frozen=True, slots=True)
class BacktestControlView:
    run_selected_enabled: bool
    compare_all_enabled: bool


@dataclass(frozen=True, slots=True)
class BacktestPageView:
    controls: BacktestControlView
    runs: tuple[BacktestComparisonRow, ...]
    selected_run_id: str | None
    detail: BacktestDetailView


__all__ = [
    "BacktestChartView",
    "BacktestComparisonRow",
    "BacktestControlView",
    "BacktestDetailView",
    "BacktestFormDraft",
    "BacktestMetricView",
    "BacktestPageView",
    "BacktestStrategyOption",
    "BacktestSummaryView",
    "BacktestTradeRow",
]
