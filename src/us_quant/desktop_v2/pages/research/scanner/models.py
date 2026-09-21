"""Immutable presentation models for the market scanner page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class ScannerFilterMode(str, Enum):
    ALL = "all"
    TREND = "trend"
    TRADE_ELIGIBLE = "trade_eligible"
    LEADERS = "leaders"


@dataclass(frozen=True, slots=True)
class ScannerRowView:
    symbol: str
    execution_symbol: str
    name: str
    sector: str
    leader_tier: str
    signal: str
    score: str
    close: str
    whole_share_capacity: str
    return_20d: str
    return_63d: str
    volatility_20d: str
    rsi_14d: str
    reason: str
    trade_eligible: bool
    trend_candidate: bool
    leader: bool


@dataclass(frozen=True, slots=True)
class ScannerCoverageFacts:
    scanned_count: int
    skipped_count: int
    research_count: int


@dataclass(frozen=True, slots=True)
class ScannerPageView:
    rows: tuple[ScannerRowView, ...]
    coverage: ScannerCoverageFacts
    has_scan: bool = False


@dataclass(frozen=True, slots=True)
class ScannerChartView:
    symbol: str
    points: tuple[tuple[date, float], ...]
    title: str = ""


__all__ = [
    "ScannerChartView",
    "ScannerCoverageFacts",
    "ScannerFilterMode",
    "ScannerPageView",
    "ScannerRowView",
]
