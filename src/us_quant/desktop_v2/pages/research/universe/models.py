"""Immutable presentation models for the Universe page."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UniverseFilterMode(str, Enum):
    RESEARCH = "research"
    TRADING = "trading"
    ALL = "all"
    EXCLUDED = "excluded"


@dataclass(frozen=True, slots=True)
class UniverseRowView:
    symbol: str
    name: str
    exchange: str
    security_type: str
    sector: str
    leader_tier: str
    country_evidence: str
    eligibility: str
    note: str
    research_eligible: bool
    trading_eligible: bool


@dataclass(frozen=True, slots=True)
class UniverseControlView:
    refresh_enabled: bool
    cancel_enabled: bool
    refresh_label: str
    cancel_label: str


@dataclass(frozen=True, slots=True)
class UniversePageView:
    rows: tuple[UniverseRowView, ...]
    controls: UniverseControlView
    count_text: str = "显示 0 / 0"


__all__ = [
    "UniverseControlView",
    "UniverseFilterMode",
    "UniversePageView",
    "UniverseRowView",
]
