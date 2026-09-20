"""The Paper application's data shapes and its provider-neutral error.

Two of the three are *readings*: an immutable snapshot of the application
lifecycle and the one fact the recovery route asks about.  Neither carries order
truth -- positions, fills and reconciliation rows stay in the existing service
and journal, because duplicating them here would create a second place where
broker truth is assembled.

The error is the fail-closed refusal this package raises for every unsafe
ownership transition.  It lives here rather than in ``service.py`` so a caller
can catch it without importing the service's implementation.
"""

from __future__ import annotations

from dataclasses import dataclass


class PaperTradingLifecycleError(RuntimeError):
    """A Paper order-service ownership transition was refused.

    Raised when a caller asks for a transition that would lose control of a
    broker connection or silently replace an owner: promoting over a live
    active service, clearing an owner that still reports connected, discarding
    an unknown candidate, or reusing a candidate id.  Every one of these is
    fail-closed -- nothing is mutated and the existing state is left intact.
    """


@dataclass(frozen=True, slots=True)
class PaperTradingSnapshot:
    """One immutable reading of the Paper *application lifecycle*.

    Order truth deliberately stays in the existing service and journal: this
    snapshot never carries positions, orders, fills, or reconciliation rows.
    """

    phase: str
    connected: bool
    finalized: bool
    last_error: str | None


@dataclass(frozen=True, slots=True)
class PaperReconciliationStatus:
    """Whether a one-shot reconciliation proof waits for human confirmation."""

    awaiting_confirmation: bool


__all__ = [
    "PaperReconciliationStatus",
    "PaperTradingLifecycleError",
    "PaperTradingSnapshot",
]
