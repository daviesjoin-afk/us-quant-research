"""Stable identity for Research secondary navigation.

The order and labels below are the single source of truth for the Research
aggregate.  This module is deliberately Qt-free so tooling and tests can use
the semantic workspace keys without importing a widget toolkit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ResearchWorkspace(str, Enum):
    TARGETED = "targeted"
    UNIVERSE = "universe"
    HISTORY = "history"
    SCANNER = "scanner"
    BACKTEST = "backtest"
    CROSS_SECTION = "cross_section"


@dataclass(frozen=True, slots=True)
class ResearchNavigationItem:
    workspace: ResearchWorkspace
    label: str


RESEARCH_NAVIGATION_ITEMS: tuple[ResearchNavigationItem, ...] = (
    ResearchNavigationItem(ResearchWorkspace.TARGETED, "针对性验证"),
    ResearchNavigationItem(ResearchWorkspace.UNIVERSE, "广域标的池"),
    ResearchNavigationItem(ResearchWorkspace.HISTORY, "历史数据"),
    ResearchNavigationItem(ResearchWorkspace.SCANNER, "市场扫描"),
    ResearchNavigationItem(ResearchWorkspace.BACKTEST, "回测"),
    ResearchNavigationItem(ResearchWorkspace.CROSS_SECTION, "横截面研究"),
)