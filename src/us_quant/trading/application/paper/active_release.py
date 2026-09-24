"""The two-phase ending of the active Paper order-service slot.

Promotion is two-phase because ownership has to move *before* publication, and the launch
is only entitled to keep the slot if publication then succeeds.  Releasing a finished
session needs the same shape for the mirror reason: the two transitions involved have to be
ordered, and only one of them can be taken back.

The one that cannot be taken back is the execution lease.  ``finalize_if_safe`` is a
check-and-commit call on ``PaperWorkflowController`` -- a canonical owner this service does
not get to change -- so by the time it answers ``True`` the PAPER lease is gone, along with
the workflow's result, coordinator and both evidence records.  If the slot could still
refuse to be dropped after that, the session would be left with PAPER released and an
ownership still held: exactly the ownerless-session state the promotion reservation exists
to make unreachable, reached from the other end.

So the slot's releasability is **proved and locked first**, and the workflow is asked
second.  A refusal here costs nothing -- nothing has happened yet -- while a refusal there
costs only :meth:`PaperActiveRelease.cancel_active_release`, which gives the lock back
without dropping anything.  The protocol is therefore safe in one direction only, which is
the direction that matters.

This is a mixin rather than a standalone object, for the same reason ``recovery``'s two
protocols are: the two phases are one object with one state, and the split exists so that
neither half has to be read through the other.  The state and the shared primitives are
declared by ``PaperTradingService``; nothing here connects, disconnects, submits or cancels
anything, and nothing here touches the execution lease.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from us_quant.trading.application.paper.models import (
    PaperActiveReleaseReservation,
    PaperPromotionReservation,
    PaperTradingLifecycleError,
)

if TYPE_CHECKING:
    from threading import RLock

    from us_quant.trading.application.paper.contracts import PaperOrderServicePort


class PaperActiveRelease:
    """The explicit release protocol of an already-finished Paper session.

    Every attribute below is set by ``PaperTradingService.__init__``, and the primitive
    (``_record_error``) is provided by that class.
    """

    _lock: RLock
    _order_service: PaperOrderServicePort | None
    _promotion_reservation: PaperPromotionReservation | None
    _active_release_reservation: PaperActiveReleaseReservation | None
    _record_error: Callable[[str | None], None]

    def _refuse_if_release_in_flight(self, *, action: str) -> None:
        """Refuse another slot transition while a release has reserved the slot's ending.

        The mirror of the promotion reservation's exclusivity, and the half that makes
        ``commit_active_release`` total: while the reservation stands, no promotion, no
        other clear and no re-open of the active slot can install or drop anything, so
        after ``finalize_if_safe`` there is nothing left that could fail.

        Defined once, here, rather than open-coded at each of the three call sites: a
        second copy is how one of them starts permitting the transition the others forbid,
        and the permitted one is enough to invalidate the commit.
        """

        if self._active_release_reservation is None:
            return
        raise PaperTradingLifecycleError(
            f"an active Paper release is in flight; refusing to {action}"
        )

    def reserve_active_release(self) -> PaperActiveReleaseReservation:
        """Prove the active slot can be released and lock it for that release.

        The checks are ``clear_active``'s, run *here* rather than after the caller has
        released an execution lease it cannot take back:

        * an overlapping release refuses, so one caller's lock is never two callers' claim;
        * a promotion claim still outstanding refuses outright.  This is the E1 invariant
          path, and it is the one the transaction boundary exists for: if the slot cannot
          be accounted for, PAPER must not be released either, so the refusal has to arrive
          *before* the workflow is asked;
        * no active service at all refuses, because there is nothing to release;
        * a service that still reports a live connection refuses -- dropping it would
          abandon a socket nobody could reach again;
        * the service identity is re-read under the lock, so a slot that changed while the
          connection was being checked cannot be locked by mistake.

        The connection read is deliberately outside the lock: this module never holds the
        lock across a call into the broker boundary.
        """

        with self._lock:
            if self._active_release_reservation is not None:
                raise PaperTradingLifecycleError(
                    "an active Paper release is already in flight;"
                    " refusing to overlap it"
                )
            if self._promotion_reservation is not None:
                raise PaperTradingLifecycleError(
                    f"Paper candidate {self._promotion_reservation.candidate_id!r} holds"
                    " the promotion reservation; refusing to reserve the slot it is"
                    " reserved to"
                )
            service = self._order_service
            if service is None:
                raise PaperTradingLifecycleError(
                    "no active Paper order service to release"
                )
        if bool(service.connection_snapshot().connected):
            raise PaperTradingLifecycleError(
                "refusing to release an active Paper order service that still"
                " reports a live connection"
            )
        with self._lock:
            if self._order_service is not service:
                raise PaperTradingLifecycleError(
                    "the active Paper order service changed while its release was being"
                    " prepared; refusing to reserve a different service"
                )
            reservation = PaperActiveReleaseReservation()
            self._active_release_reservation = reservation
        return reservation

    def commit_active_release(
        self, reservation: PaperActiveReleaseReservation
    ) -> None:
        """Drop the slot.  Total by construction, which is what reserving bought.

        Everything that could have invalidated this was refused while the reservation
        stood, so a caller that reaches here has already been told the slot is releasable
        -- and by then it has released the execution lease, which is why *nothing* may
        fail now.  The two refusals below are therefore misuse and corruption rather than
        races, exactly as ``commit_candidate_promotion``'s are.

        Like that one, a corruption refusal deliberately leaves the reservation standing:
        if the owner cannot be accounted for, handing the slot back for reuse is the one
        thing that must not happen.
        """

        with self._lock:
            if self._active_release_reservation is not reservation:
                raise PaperTradingLifecycleError(
                    "stale or foreign Paper active-release reservation;"
                    " refusing to commit it"
                )
            if self._order_service is None:
                # Unreachable while the reservation locks the slot, and kept because this
                # is the check that makes "a release reservation locks the slot" true
                # rather than merely intended.
                raise PaperTradingLifecycleError(
                    "the reserved Paper active release no longer holds the active slot;"
                    " refusing to commit it"
                )
            self._order_service = None
            self._active_release_reservation = None
        self._record_error(None)

    def cancel_active_release(
        self, reservation: PaperActiveReleaseReservation
    ) -> bool:
        """Give the lock back without dropping anything.

        The mirror of ``cancel_candidate_promotion``, and the reason reserving first is
        safe: the workflow refusing the release costs exactly this call, after which the
        slot is held and unlocked exactly as it was found.  Returns whether this call
        released anything, so a caller holding a stale reservation is told it released
        nothing rather than being left to assume it did.

        Deliberately does not raise.  It runs in the branch where the caller still has a
        refusal to report, so a raise here would displace the reason with a crash.
        """

        with self._lock:
            if self._active_release_reservation is not reservation:
                return False
            self._active_release_reservation = None
        return True


__all__ = ["PaperActiveRelease"]
