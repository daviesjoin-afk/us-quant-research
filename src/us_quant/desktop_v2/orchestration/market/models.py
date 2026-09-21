"""Immutable facts the market orchestrator consumes and publishes.

This module is deliberately Qt-free.  Everything here is either an *input* the
composition root pushes in (readiness symbols it owns the source of) or an
*output* the window renders (shell health).  Keeping both as plain frozen data
means the orchestrator's cross-boundary surface is inspectable without a widget
toolkit, and a later round can move the consumers without moving a type.

Two boundaries are load-bearing:

* ``MarketReadinessInputs`` exists so the orchestrator never has to reach for
  the execution page or the strategy application to find out which symbols are
  candidates.  The window knows; it hands over finished symbol tuples.
* ``MarketShellHealthView`` exists so the orchestrator never holds a badge.  The
  global header belongs to the shell, so the orchestrator publishes text and
  state and the window does the painting.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketReadinessInputs:
    """The cross-domain symbols the readiness breakdown classifies.

    ``candidate_symbols`` come from the prepared auto-quant shortlist and
    ``reference_symbols`` from the selected strategy's parameters.  Neither is
    market data this route owns, which is why they arrive as data rather than
    being looked up here.
    """

    candidate_symbols: tuple[str, ...] = ()
    reference_symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketRuntimeEvent:
    """One runtime event the orchestrator wants recorded.

    The orchestrator does not hold the event store: it asks, and the window
    writes.  That keeps ``Market -> System`` from becoming a dependency.
    """

    severity: str
    component: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class MarketShellHealthView:
    """The global header facts one market state produces.

    ``handshake_*`` are ``None`` when the handshake badge must be left alone:
    the legacy rule only ever *promoted* that badge, never demoted it, and a
    view that always carried a value would have quietly changed that.
    """

    market_text: str
    market_state: str
    handshake_text: str | None = None
    handshake_state: str | None = None
    status_log: str | None = None


__all__ = [
    "MarketReadinessInputs",
    "MarketRuntimeEvent",
    "MarketShellHealthView",
]
