"""Immutable presentation models for the Dashboard route.

Everything in this module is a display fact.  The page receives these values
and only draws them; it cannot fetch an account, read a chart file, resolve an
artifact or probe a broker connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class DashboardArtifactTone(str, Enum):
    """How an artifact row should be coloured by the page."""

    NEUTRAL = "neutral"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DashboardMetricView:
    """One dashboard metric card: value plus explanatory note."""

    value: str
    note: str


@dataclass(frozen=True, slots=True)
class DashboardArtifactRowView:
    """One research artifact row, already translated for display."""

    artifact_type: str
    status_text: str
    data_as_of: str
    generated_at: str
    source: str
    run_id: str
    limitations: str
    tone: DashboardArtifactTone


@dataclass(frozen=True, slots=True)
class DashboardChartView:
    """The chart series the page may draw; no file-system knowledge."""

    symbol: str | None
    points: tuple[tuple[date, float], ...]


@dataclass(frozen=True, slots=True)
class DashboardView:
    """Everything one render of the Dashboard route draws."""

    net_liquidation: DashboardMetricView
    daily_pnl: DashboardMetricView
    positions: DashboardMetricView
    intraday_market: DashboardMetricView

    artifacts: tuple[DashboardArtifactRowView, ...]
    chart: DashboardChartView
    research_boundary_text: str


__all__ = [
    "DashboardArtifactRowView",
    "DashboardArtifactTone",
    "DashboardChartView",
    "DashboardMetricView",
    "DashboardView",
]
