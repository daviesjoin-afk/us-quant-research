"""Immutable view models for the market route.

Everything here is a display fact: a string the operator reads, a boolean that
decides whether a control is offered, and the tone a row should be coloured
with.  Nothing here is a decision -- the page is handed these and draws them, and
none of these types can fetch its own data, resolve a credential or start a feed.

The two exceptions worth explaining:

* ``MarketProviderOption`` carries the provider's ``source_id`` next to its
  label, because the stable key is the id and the label is only ever shown.  A
  page that keyed off the label would break the moment a label was reworded;
* ``MarketSubscriptionDraft`` is the operator's *input*, not the truth.  It is
  what the page can read back off its own controls, and the orchestration layer
  decides whether that draft is acceptable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MarketRowTone(str, Enum):
    """How a quote row's status should read, in presentation terms only."""

    NEUTRAL = "neutral"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class MarketProviderOption:
    """One selectable market data source: the stable id and the shown label."""

    source_id: str
    label: str


@dataclass(frozen=True, slots=True)
class MarketSubscriptionDraft:
    """What the operator currently has typed and selected; not the truth."""

    source_id: str
    symbols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MarketQuoteRow:
    """One quote as the table shows it.

    Every field is already a string: the table must not interpret
    ``MarketDataMode``, ``realtime_ready`` or ``stale``.  ``tone`` belongs to the
    status column's colour.
    """

    symbol: str
    bid: str
    ask: str
    last: str
    close: str
    spread: str
    mode: str
    updated_at: str
    age: str
    generation: str
    source: str
    coverage: str
    status: str
    reason: str
    tone: MarketRowTone = MarketRowTone.NEUTRAL


@dataclass(frozen=True, slots=True)
class MarketMetricView:
    """One metric card: the value on top and the note under it."""

    value: str
    note: str


@dataclass(frozen=True, slots=True)
class MarketControlView:
    """Which market controls the page may offer right now.

    Declared as plain booleans so the page never learns *why* a control is
    closed: whether the stop is refused because a Paper session holds positions
    is the orchestration layer's business.
    """

    start_enabled: bool
    stop_enabled: bool
    symbols_enabled: bool
    provider_enabled: bool
    load_watchlist_enabled: bool
    start_label: str


@dataclass(frozen=True, slots=True)
class MarketReadinessFacts:
    """The readiness counts the presentation needs, already calculated.

    The page must not call ``calculate_quote_readiness_breakdown`` itself: that
    calculation depends on auto-quant candidates and market reference symbols,
    which are not this route's business.
    """

    candidate_count: int
    candidate_current_count: int
    candidate_recent_count: int
    reference_count: int
    reference_current_count: int
    reference_recent_count: int
    subscription_count: int
    subscription_current_count: int
    subscription_recent_count: int


@dataclass(frozen=True, slots=True)
class MarketConnectingFacts:
    """Facts for the worker-started, first-snapshot-pending state."""

    source_id: str
    symbol_count: int


@dataclass(frozen=True, slots=True)
class MarketPageView:
    """Everything one render of the market page draws."""

    connection: MarketMetricView
    feed: MarketMetricView
    readiness: MarketMetricView
    watchlist: MarketMetricView
    scope: str
    empty_message: str | None
    rows: tuple[MarketQuoteRow, ...]
    health_text: str
    controls: MarketControlView


__all__ = [
    "MarketConnectingFacts",
    "MarketControlView",
    "MarketMetricView",
    "MarketPageView",
    "MarketProviderOption",
    "MarketQuoteRow",
    "MarketReadinessFacts",
    "MarketRowTone",
    "MarketSubscriptionDraft",
]
