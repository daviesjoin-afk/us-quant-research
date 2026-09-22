"""The backtest orchestration capability.

``BacktestOrchestrator`` owns the Backtest route's desktop runtime: this
session's ``BacktestRun`` batch, the comparison-table selection, the busy flag,
the two run requests and the one render entry point.  The runs live here and
nowhere else -- there is no ``MainWindow.backtest_runs`` and no compatibility
property -- and the page is painted only from here.

The pure rules it applies are in :mod:`queries`: which versions the combo
offers, which versions a click means to run, and how a form draft becomes a
``BacktestRequest``.  That module is Qt-free, so the ordering and the decimal
precision can be tested without a window.

This is the most self-contained Research capability: it imports no other
Research workspace.  Universe, History, Scanner, Cross-Section and Targeted all
have their own truths and Backtest reads none of them.
"""

from __future__ import annotations

from us_quant.desktop_v2.orchestration.research.backtest.queries import (
    build_backtest_requests,
    select_backtest_versions,
    strategy_options,
)
from us_quant.desktop_v2.orchestration.research.backtest.orchestrator import (
    BACKTEST_RESOURCE_GROUP,
    BUSY_MESSAGE,
    BUSY_TITLE,
    INVALID_DATE_MESSAGE,
    INVALID_DATE_TITLE,
    NO_STRATEGY_MESSAGE,
    NO_STRATEGY_TITLE,
    REFUSAL_INFORMATION,
    REFUSAL_WARNING,
    BacktestOrchestrator,
)

__all__ = [
    "BACKTEST_RESOURCE_GROUP",
    "BUSY_MESSAGE",
    "BUSY_TITLE",
    "BacktestOrchestrator",
    "INVALID_DATE_MESSAGE",
    "INVALID_DATE_TITLE",
    "NO_STRATEGY_MESSAGE",
    "NO_STRATEGY_TITLE",
    "REFUSAL_INFORMATION",
    "REFUSAL_WARNING",
    "build_backtest_requests",
    "select_backtest_versions",
    "strategy_options",
]
