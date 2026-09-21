"""Immutable presentation models for the History page."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HistoryQueueRow:
    symbol: str
    duration: str
    priority: str
    status: str
    attempts: str
    row_count: str
    note: str


@dataclass(frozen=True, slots=True)
class HistoryControlView:
    progress_percent: int


@dataclass(frozen=True, slots=True)
class HistoryPageView:
    summary: str
    rows: tuple[HistoryQueueRow, ...]
    controls: HistoryControlView


__all__ = [
    "HistoryControlView",
    "HistoryPageView",
    "HistoryQueueRow",
]
