"""Immutable presentation models for the execution page.

Everything here is a display fact: a string an operator reads, and the tone the
page should colour it with.  Nothing here is a decision, an order, a verdict or
a service -- the models are what the presenter produces and what the page
draws, so they deliberately carry no Qt type, no repository, no broker and no
runtime.  A model that could fetch its own data would make the page a second
place where truth is assembled.

Two shapes are worth explaining:

* a row is a dataclass with named fields, and the table reads its columns in
  declaration order.  The field names are the column headers, so there is one
  place to change when a column moves;
* a row carries a single ``tone`` rather than a colour.  The *table* knows which
  of its columns is the status column; the presenter only says whether that
  status is good, uncertain or bad.  Choosing the actual colour is the page's
  job, which is what keeps ``ThemePalette`` out of the Qt-free presenter.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum


class ExecutionDetailWorkspace(IntEnum):
    """Which detail section is on screen, as a name rather than an index.

    A caller that wants the order table has no way to say so in ``int`` without
    hard-coding a tab position, and a position is exactly what changes when the
    page is re-laid out.  These keys are *presentation vocabulary*: they select
    a tab the page already owns and carry no workflow meaning, so nothing here
    can be read as a session phase or used to start, arm or stop anything.
    """

    PORTFOLIO = 0
    SHADOW = 1
    LATENCY = 2
    CANDIDATES = 3
    ORDERS = 4


class Tone(str, Enum):
    """How a status cell should read, in presentation terms only."""

    NEUTRAL = "neutral"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class MetricView:
    """One metric card: the value on top and the note under it."""

    value: str
    note: str


@dataclass(frozen=True, slots=True)
class PositionRow:
    """One holding, valued at the freshest mark available."""

    symbol: str
    quantity: str
    average_price: str
    mark: str
    unrealized: str
    held_for: str
    source: str


@dataclass(frozen=True, slots=True)
class FillRow:
    """One execution the session recorded."""

    occurred_at: str
    symbol: str
    side: str
    quantity: str
    price: str
    commission: str
    realized: str


@dataclass(frozen=True, slots=True)
class ShadowRow:
    """One candidate's research-only shadow limit band.

    ``tone`` belongs to the status column: a resting limit is the expected
    state, waiting for a quote is the one worth noticing.
    """

    symbol: str
    bid: str
    ask: str
    shadow_buy: str
    shadow_sell: str
    limit_price: str
    status: str
    tone: Tone = Tone.NEUTRAL


@dataclass(frozen=True, slots=True)
class LatencyRow:
    """One order's local-to-broker submission latency."""

    intent_id: str
    symbol: str
    side: str
    latency: str
    generated_at: str
    tone: Tone = Tone.NEUTRAL


@dataclass(frozen=True, slots=True)
class CandidateRow:
    """The six candidate columns that only move when the candidate set does.

    Split from :class:`CandidateRealtime` because the candidate table is
    rebuilt on the quote tick: the scan columns are static per set, and only the
    realtime column changes, so keeping them apart is what lets the page skip
    the rebuild.
    """

    symbol: str
    name: str
    sector: str
    tier: str
    scan_score: str
    signal: str


@dataclass(frozen=True, slots=True)
class CandidateRealtime:
    """The candidate table's one live column."""

    status: str
    tone: Tone = Tone.NEUTRAL


@dataclass(frozen=True, slots=True)
class OrderRow:
    """One reconciled session order.

    ``tone`` belongs to the status column: reconciled is good, a terminal row
    that never reconciled is the failure this table exists to surface.
    """

    status: str
    symbol: str
    side: str
    quantities: str
    limit_price: str
    explanation: str
    broker_order_id: str
    tone: Tone = Tone.NEUTRAL


@dataclass(frozen=True, slots=True)
class ExecutionCandidatesView:
    """The candidate table on its own, without a session behind it.

    A candidate set exists before any session does -- the operator approves a
    shortlist and only then arms it -- so the candidate table is the one table
    that must be renderable while every session card is still empty.  Carrying
    the static key here keeps the rebuild optimisation next to the rows it
    protects.
    """

    candidates: tuple[CandidateRow, ...]
    realtime: tuple[CandidateRealtime, ...]
    static_key: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class ExecutionRuntimeView:
    """Everything one render of the execution page draws from session facts."""

    status: MetricView
    equity: MetricView
    realized: MetricView
    unrealized: MetricView
    position_count: MetricView
    summary: str
    positions: tuple[PositionRow, ...]
    fills: tuple[FillRow, ...]
    shadow: tuple[ShadowRow, ...]
    latency: tuple[LatencyRow, ...]
    candidates: ExecutionCandidatesView
    orders: tuple[OrderRow, ...]


@dataclass(frozen=True, slots=True)
class ExecutionControlState:
    """Which controls the page may offer right now.

    Declared as plain booleans rather than as a workflow phase so the page never
    learns a lifecycle vocabulary: it is told what to enable, not why, and a
    page that cannot name ``HALTED`` cannot act on it.
    """

    # Launch and preparation.
    prepare_enabled: bool
    start_enabled: bool
    channel_check_enabled: bool
    strategy_combo_enabled: bool
    candidate_limit_enabled: bool
    capital_limit_enabled: bool
    arm_confirm_enabled: bool
    # The running session.
    pause_enabled: bool
    resume_enabled: bool
    stop_enabled: bool
    stop_stream_enabled: bool
    # Explicit recovery.
    reconcile_enabled: bool
    resume_reconciliation_enabled: bool