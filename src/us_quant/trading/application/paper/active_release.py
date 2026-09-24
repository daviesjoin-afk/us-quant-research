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
    _active_connect_inflight: bool
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

    def reserve_active_release(
        self, *, expected_service: object | None = None
    ) -> PaperActiveReleaseReservation:
        """Prove the active slot can be released and lock it for that release.

        **The claim goes on first, and the proof afterwards.**  That order is the whole of
        the concurrency argument: the reservation is installed in the *same* critical
        section that checks the slot and captures the service, so from that instant no
        other release, clear, promotion or re-open can touch the slot -- and only then is
        the connection read, outside the lock, because that read is a call into the broker
        boundary.

        Asking the broker first and installing the claim afterwards would leave a
        check-then-act window in both directions: an overlapping reserve could overwrite
        this reservation (making the caller's token stale, so the commit that follows an
        already-released lease fails), and a re-open could make the slot live again between
        the proof and the claim -- leaving a live broker socket with no owner and no lease
        while this method reported the slot releasable.

        The refusals, all inside that same section:

        * an overlapping release refuses, so one release's lock is never another's claim;
        * a re-open in flight refuses: a connect this call cannot see the end of must not
          be locked out of the slot it is about to make live.  This is what makes
          :meth:`PaperTradingService.clear_active` safe without a check of its own -- it *is*
          this call;
        * a promotion claim still outstanding refuses.  This is the E1 invariant path and
          the reason the boundary exists: if the slot cannot be accounted for, PAPER must
          not be released either, so the refusal has to arrive *before* the workflow is
          asked;
        * no active service at all refuses, because there is nothing to release;
        * ``expected_service`` -- when given -- is checked here too, so a caller that means
          one service cannot lock a slot another one replaced while it was deciding.

        The connection read then happens outside the lock, and a service that still reports
        a live connection -- or a read that raises -- takes the claim back off again and
        refuses.  So this method is a claim or an exception, never a half-held claim.
        """

        with self._lock:
            if self._active_release_reservation is not None:
                raise PaperTradingLifecycleError(
                    "an active Paper release is already in flight;"
                    " refusing to overlap it"
                )
            if self._active_connect_inflight:
                raise PaperTradingLifecycleError(
                    "an active Paper re-open is in flight; refusing to reserve the slot"
                    " it is re-opening"
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
            if expected_service is not None and service is not expected_service:
                raise PaperTradingLifecycleError(
                    "refusing to clear a different active Paper order service"
                )
            reservation = PaperActiveReleaseReservation()
            self._active_release_reservation = reservation
        try:
            connected = bool(service.connection_snapshot().connected)
        except Exception:
            # A read that failed proved nothing, so the claim goes back rather than being
            # held over a slot whose state this method never learned.
            self.cancel_active_release(reservation)
            raise
        if connected:
            self.cancel_active_release(reservation)
            raise PaperTradingLifecycleError(
                "refusing to release an active Paper order service that still"
                " reports a live connection"
            )
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
