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


@dataclass(frozen=True, slots=True, eq=False)
class PaperPromotionReservation:
    """One launch's outstanding claim on the active order-service slot.

    Handed out by ``reserve_candidate_promotion`` and required back -- as the *same*
    instance -- by either ``commit_candidate_promotion`` or
    ``cancel_candidate_promotion``.  It exists because promotion is the one transition
    that has to be two-phase: the ownership move has to happen *before*
    ``publish_armed`` creates a workflow that expects an owner, while the launch is
    only entitled to keep the slot if publication then succeeds.

    So a reservation is not a promise to promote later.  By the time a caller holds
    one, the named candidate is already installed as the active service; the
    reservation is the token saying the installation belongs to this launch, plus the
    exclusivity that stops any other promotion, discard or connection from touching
    the slot until this launch declares which of the two endings it was.

    ``eq=False`` on purpose: identity *is* the meaning of a reservation, so equality
    must not be a second, quietly weaker notion of it.
    """

    candidate_id: str


__all__ = [
    "PaperPromotionReservation",
    "PaperReconciliationStatus",
    "PaperTradingLifecycleError",
    "PaperTradingSnapshot",
]
