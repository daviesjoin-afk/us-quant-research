"""Immutable Paper session results, and the projection that builds one.

These are the only values the workflow controller and the window render, and
they carry facts rather than ports: a reader of a ``PaperSessionResult`` cannot
reach the broker or the engine through it.  Reconciliation evidence deliberately
lives elsewhere -- a proof of safety is a different responsibility from the state
a screen draws, and mixing them would let rendering code mint one.
"""

from __future__ import annotations

from dataclasses import dataclass

from us_quant.trading.runtime.paper_contracts import (
    EngineSnapshot,
    PaperHealth,
)


@dataclass(frozen=True, slots=True)
class PaperSessionEvent:
    """One thing the coordinator did or discovered during an operation."""

    code: str
    severity: str
    message: str


@dataclass(frozen=True, slots=True)
class PaperSessionState:
    """The renderable Paper session state, derived from one engine snapshot."""

    active: bool
    entries_paused: bool
    stop_requested: bool
    halted: bool
    finalized: bool
    local_position_count: int
    pending_order_count: int
    broker_open_order_count: int
    broker_position_count: int
    unreconciled_order_count: int
    health_status: str | None


@dataclass(frozen=True, slots=True)
class PaperSessionResult:
    """One coordinator result: the state, the snapshot behind it and its events."""

    state: PaperSessionState
    engine_snapshot: EngineSnapshot
    health: PaperHealth | None
    events: tuple[PaperSessionEvent, ...]