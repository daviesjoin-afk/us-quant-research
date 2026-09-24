"""``PaperTradingService``: the owner of the Paper order-service lifecycle.

Moved here from the ``paper_trading_service`` root module as a package-boundary
change, not a redesign: every method below is the one that ran before.  It owns
*who currently holds the broker order connection* and nothing else -- submitting,
cancelling, arming rules, capital resolution, reconciliation algorithms,
finalization, phase transitions and the execution lease all stay where they were.
``docs/TRADING_ARCHITECTURE_V2.md`` carries the full ownership argument.
"""

from __future__ import annotations

import threading
from typing import Sequence

from us_quant.trading.application.paper.active_release import PaperActiveRelease
from us_quant.trading.application.paper.contracts import (
    PaperOrderServiceFactory,
    PaperOrderServicePort,
    WorkflowGetter,
)
from us_quant.trading.application.paper.models import (
    PaperActiveReleaseReservation,
    PaperPromotionReservation,
    PaperReconciliationStatus,
    PaperTradingLifecycleError,
    PaperTradingSnapshot,
)
from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase


class PaperTradingService(PaperActiveRelease):
    """Owner of the Paper order-service lifecycle, plus the window's reads.

    Two ownership slots, never one.  ``_candidates`` holds services that are
    connected but not yet trusted -- a successful connect is not permission to
    become the session -- and ``_order_service`` holds the single service a
    validated, armed launch published; promotion refuses to overwrite it and it
    is never disconnected implicitly.  Phase and evidence are read through an
    injected workflow *getter*, because ``MainWindow`` replaces that controller
    in the safety tests and this boundary must read whichever one is live.

    Promotion is the one transition here that is *two-phase*, because it is the
    only one a caller must be able to take back: ``reserve_candidate_promotion``
    installs the owner **and** locks the slot, and ``commit``/``cancel`` end the
    launch's claim.  Those two methods carry the argument for why the
    installation cannot wait until after publication.

    Its mirror -- releasing a finished session -- is two-phase here too, and is
    mixed in from :mod:`active_release` for the reason ``recovery`` mixes in the
    runtime's two protocols: the state is one object's, so neither half should have
    to be read through the other.  See that module for why the slot has to be
    proved releasable *before* the execution lease is handed back.
    """

    def __init__(
        self,
        *,
        workflow_getter: WorkflowGetter,
        order_service_factory: PaperOrderServiceFactory,
    ) -> None:
        self._workflow_getter = workflow_getter
        self._order_service_factory = order_service_factory
        self._lock = threading.RLock()
        # A ``None`` value is a *reservation*: a connect for that id is in
        # flight.  Reserving before connecting is what makes id collisions
        # genuinely fail-closed instead of a check-then-insert race.
        self._candidates: dict[str, PaperOrderServicePort | None] = {}
        self._order_service: PaperOrderServicePort | None = None
        # The launch that currently has a promotion in flight.  Not a second copy
        # of the owner -- the owner is ``_order_service``; this only records that
        # the slot is spoken for and by whom.
        self._promotion_reservation: PaperPromotionReservation | None = None
        # The release that currently has the active slot locked for it.  The mirror of
        # the reservation above and the same kind of fact: not a copy of the owner, a
        # statement that the slot's *ending* belongs to one caller until it says which
        # of the two endings it was.  See ``reserve_active_release``.
        self._active_release_reservation: PaperActiveReleaseReservation | None = None
        # Whether a re-open of the active service is in flight.  A third claim, and the
        # one that makes the release reservation mean anything: a connect and a release
        # both start by reading the connection and then act on it, so without exclusion
        # a release can lock a slot that a connect is about to make live again -- leaving
        # a live broker socket, no owner and no lease.  Like the two above it is installed
        # *inside* the critical section, before any call into the broker boundary.
        self._active_connect_inflight = False
        self._last_error: str | None = None

    # -- reads ---------------------------------------------------------

    def phase(self) -> PaperWorkflowPhase:
        """The workflow controller's current phase; never a second copy."""

        return self._workflow_getter().phase

    def is_finalized(self) -> bool:
        """Whether no Paper session still awaits finalization (the close gate)."""

        result = self._workflow_getter().result
        return result is None or bool(result.state.finalized)

    def has_order_service(self) -> bool:
        """Whether an active order service is currently owned."""

        with self._lock:
            return self._order_service is not None

    def has_candidate_ownership(self) -> bool:
        """Whether any connected-but-unpromoted candidate is still tracked.

        The service owns **two** slots, not one: the active order service and the
        candidates that were connected but never promoted.  A candidate is a real broker
        connection, so "no active service" is not "nothing owned" -- and E1's discard path
        leaves exactly this state behind when a candidate cannot be disposed: the candidate
        stays tracked while the workflow's plan is rejected and the PAPER lease is released.
        A caller deciding whether a session is finished has to ask about both.
        """

        with self._lock:
            return bool(self._candidates)

    def is_connected(self) -> bool:
        """Whether the *active* service reports a live connection (not a candidate)."""

        service = self._active_service()
        if service is None:
            return False
        return bool(service.connection_snapshot().connected)

    def reconciliation_status(self) -> PaperReconciliationStatus:
        """Whether a reconciliation proof is awaiting explicit confirmation."""

        evidence = self._workflow_getter().reconciliation_evidence
        return PaperReconciliationStatus(awaiting_confirmation=evidence is not None)

    def snapshot(self) -> PaperTradingSnapshot:
        """One immutable lifecycle reading; asks the broker, so not per tick."""

        return PaperTradingSnapshot(
            phase=self.phase().value,
            connected=self.is_connected(),
            finalized=self.is_finalized(),
            last_error=self._last_error,
        )

    # -- read-only order status ----------------------------------------

    def broker_state(self) -> object | None:
        """The active service's last broker snapshot, or ``None`` when unowned."""

        service = self._active_service()
        if service is None:
            return None
        return service.broker_state()

    def reconciliation_rows_with_latency(
        self, *, session_id: str | None = None, limit: int = 1000
    ) -> Sequence[dict]:
        """Journal rows with submit latency; empty when nothing is owned."""

        service = self._active_service()
        if service is None:
            return ()
        return service.reconciliation_rows_with_latency(
            session_id=session_id, limit=limit
        )

    # -- candidate lifecycle -------------------------------------------

    def has_candidate(self, candidate_id: object) -> bool:
        """Whether this id is registered, or reserved by an in-flight connect."""

        if not isinstance(candidate_id, str) or not candidate_id.strip():
            return False
        with self._lock:
            return candidate_id in self._candidates

    def connect_candidate(
        self,
        candidate_id: str,
        *,
        config: object,
        repository: object,
        extended_hours_enabled: bool,
    ) -> object:
        """Build and connect one *candidate* order service; never the owner."""

        key = self._candidate_key(candidate_id)
        with self._lock:
            if (
                self._promotion_reservation is not None
                and self._promotion_reservation.candidate_id == key
            ):
                # Reserving moves the candidate into the active slot, so the id is no
                # longer in the candidate map and the duplicate check below would not
                # see it.  Registering a second service under that id then lets the
                # reservation's own rollback cancel *into* the map it was already
                # replaced in: the rollback overwrites the newer service, which
                # disappears from every ownership map while its broker connection stays
                # open and untracked.  The claim owns the id until it ends, either way.
                raise PaperTradingLifecycleError(
                    f"Paper candidate {key!r} is reserved by an in-flight promotion;"
                    " refusing to register it again"
                )
            if key in self._candidates:
                raise PaperTradingLifecycleError(
                    f"Paper candidate {key!r} is already registered;"
                    " refusing to overwrite it"
                )
            self._candidates[key] = None
        service: PaperOrderServicePort | None = None
        try:
            service = self._order_service_factory(
                config,
                repository=repository,
                extended_hours_enabled=extended_hours_enabled,
            )
            connection = service.connect()
        except Exception as error:
            self._record_error(f"Paper candidate {key!r} connect failed: {error}")
            if service is None:
                self._release_reservation(key)
            elif self._best_effort_disconnect(service, f"Paper candidate {key!r}"):
                self._release_reservation(key)
            else:
                # Cleanup failed, so keep tracking it: an untracked broker
                # connection could outlive every attempt to close it.
                self._publish_service(key, service)
            raise
        self._publish_service(key, service)
        self._record_error(None)
        return connection

    def candidate_service(self, candidate_id: str) -> PaperOrderServicePort:
        """Borrow a registered candidate for pre-promotion wiring; do not store it."""

        key = self._candidate_key(candidate_id)
        service = self._candidate_or_raise(key)
        return service

    def reserve_candidate_promotion(
        self, candidate_id: str
    ) -> PaperPromotionReservation:
        """Install ``candidate_id`` as the active service and lock the slot to it.

        The successor to the old two-step "check that it *could* be promoted, then
        promote it after publishing".  That shape left the slot empty across
        publication, so a promotion refused after it stranded a *running* workflow
        holding an armed broker channel that no recovery path could adopt -- every
        one of them starts at :meth:`has_order_service`.

        So the ownership move happens here, before publication, and this call is all
        of it: :meth:`commit_candidate_promotion` only ends the launch's claim.  The
        ordering constraint stops being a promise about the future and becomes the
        state of the slot -- after publication the workflow cannot be ownerless,
        because there is no longer an ordering in which it could be.

        Exclusive while it lasts, over the *id* as well as the slot.  A second
        reservation and a slot that already holds a service are both refused,
        :meth:`clear_active` refuses while this one stands, and
        :meth:`connect_candidate` refuses to register the reserved id again -- so the
        slot cannot be emptied out from under the claim, and the claim cannot be made to
        overwrite a candidate that replaced it.  That is what makes the ending
        deterministic rather than a race, and it leaves the rollback below exactly one
        thing to undo.
        """

        key = self._candidate_key(candidate_id)
        service = self._candidate_or_raise(key)
        with self._lock:
            self._refuse_if_release_in_flight(action="promote into that slot")
            # The claim is checked before the occupied-slot check because it is the more
            # specific refusal: an occupied slot *is* one of these two, and "a promotion
            # is in flight" says far more than "a service is already active" when one is.
            if self._promotion_reservation is not None:
                raise PaperTradingLifecycleError(
                    f"Paper candidate {self._promotion_reservation.candidate_id!r}"
                    " already holds the promotion reservation;"
                    " refusing to overlap it"
                )
            if self._order_service is not None:
                raise PaperTradingLifecycleError(
                    "a Paper order service is already active;"
                    " refusing to replace it"
                )
            if self._candidates.get(key) is not service:
                # A discard or a reconnect replaced it between the read above and
                # this lock; installing the stale handle would make the candidate
                # owned-but-unreachable in the same breath.
                raise PaperTradingLifecycleError(
                    f"Paper candidate {key!r} changed while it was being reserved;"
                    " refusing to reserve a different service"
                )
            reservation = PaperPromotionReservation(candidate_id=key)
            self._order_service = service
            del self._candidates[key]
            self._promotion_reservation = reservation
        self._record_error(None)
        return reservation

    def commit_candidate_promotion(
        self, reservation: PaperPromotionReservation
    ) -> None:
        """End the launch's claim on the slot it already owns.

        Total by construction, which is what reserving bought: ownership was taken
        before publication, so a successful publication leaves nothing left to decide
        and nothing left that can fail.  Its two refusals are misuse and corruption
        rather than races -- a reservation that is not the outstanding one, and a slot
        that no longer holds the service the reservation was taken for.

        Not bookkeeping, either.  While a reservation stands every other promotion is
        refused, so a launch that never ended its claim would leave the service
        permanently unreservable: fail-closed, but still a defect.  That is why a
        guard pins the call into the launch sequence.
        """

        with self._lock:
            if self._promotion_reservation is not reservation:
                raise PaperTradingLifecycleError(
                    "stale or foreign Paper promotion reservation;"
                    " refusing to commit it"
                )
            if self._order_service is None:
                # Unreachable while ``clear_active`` refuses a reserved slot, and kept
                # anyway because this is the check that makes "a reservation locks the
                # slot" true rather than merely intended: a commit that reported success
                # over an empty slot would declare an ownerless session owned, which is
                # exactly the state this pair of methods exists to make unreachable.
                #
                # The claim is deliberately *left standing*.  If the owner cannot be
                # accounted for, handing the slot back for reuse is the one thing that
                # must not happen, so the failure is loud and the service stays claimed.
                raise PaperTradingLifecycleError(
                    "the reserved Paper promotion no longer holds the active slot;"
                    " refusing to commit it"
                )
            self._promotion_reservation = None
        self._record_error(None)

    def cancel_candidate_promotion(
        self, reservation: PaperPromotionReservation
    ) -> bool:
        """Undo a reservation: the named service becomes a candidate again.

        The launch's publication-refused path, and the reason taking ownership early
        is safe -- the rollback is exactly the reverse of the installation, so
        afterwards the ordinary candidate path (:meth:`discard_candidate`) works
        unchanged.  Returns whether this call released anything, so a caller holding a
        stale reservation is told it released nothing.

        Deliberately does not raise.  It runs *inside* the rollback, where raising
        would skip the rejection and strand ``CONNECTING`` holding PAPER for good, so
        a refusal here has to be a return value rather than an exception.
        """

        with self._lock:
            if self._promotion_reservation is not reservation:
                return False
            if reservation.candidate_id in self._candidates:
                # Unreachable while ``connect_candidate`` refuses a reserved id, and kept
                # as a corruption defence: putting the reserved service back would
                # overwrite a candidate this method cannot account for, and the
                # overwritten one would vanish from every ownership map while its broker
                # connection stayed open -- an orphan nobody could reach or close.
                #
                # So this is reported as "released nothing", with the active slot, the
                # claim and the existing candidate all left exactly as they are.  The
                # launch treats that as a rollback it cannot complete and fails closed.
                return False
            service = self._order_service
            if service is None:
                # Unreachable: a reservation is only ever taken together with the
                # installation, and nothing else may empty the slot.  Reported as
                # "released nothing" rather than raising, for the reason above.
                return False
            self._order_service = None
            self._candidates[reservation.candidate_id] = service
            self._promotion_reservation = None
        self._record_error(None)
        return True

    def discard_candidate(self, candidate_id: str) -> None:
        """Disconnect and forget one stale candidate, and nothing else."""

        key = self._candidate_key(candidate_id)
        service = self._candidate_or_raise(key)
        try:
            service.disconnect()
        except Exception as error:
            self._record_error(
                f"Paper candidate {key!r} disconnect failed: {error}"
            )
            raise
        with self._lock:
            if self._candidates.get(key) is not service:
                raise PaperTradingLifecycleError(
                    f"Paper candidate {key!r} changed while it was being"
                    " discarded; refusing to remove a different service"
                )
            del self._candidates[key]
        self._record_error(None)

    # -- active lifecycle ----------------------------------------------

    def connect_active(self) -> object:
        """Re-open the active service; never creates one.

        The call is **claimed under the lock before the broker call is made**, and the claim
        is what makes it mutually exclusive with a release.  Both operations start by
        reading the connection and then acting on what they read, so a check-then-act pair
        of them is a real race in both directions: a release that observed "disconnected"
        can lock the slot while a connect is in flight and about to make it live again --
        leaving a live broker socket with no owner and no execution lease -- and a connect
        that observed "no release" can re-open a slot a release is about to drop.

        A claim installed in the same critical section closes both: neither operation can
        begin while the other holds its claim.  The broker call itself stays outside the
        lock, as it must.
        """

        with self._lock:
            self._refuse_if_release_in_flight(action="re-open the slot it has reserved")
            if self._active_connect_inflight:
                raise PaperTradingLifecycleError(
                    "an active Paper re-open is already in flight;"
                    " refusing to overlap it"
                )
            service = self._order_service
            if service is None:
                raise PaperTradingLifecycleError(
                    "no active Paper order service to connect"
                )
            self._active_connect_inflight = True
        try:
            return service.connect()
        finally:
            with self._lock:
                self._active_connect_inflight = False

    def disconnect(self) -> None:
        """Run the existing ``disconnect`` semantics once; never clears ownership."""

        service = self._active_service()
        if service is None:
            return
        try:
            service.disconnect()
        except Exception as error:
            self._record_error(f"Paper disconnect failed: {error}")
            raise
        self._record_error(None)

    def clear_active(self, *, expected_service: object | None = None) -> None:
        """Release ownership -- only ever after the session is truly finalized.

        Fail-closed four ways: a promotion still in flight, or an active *release* in
        flight, is refused outright; a service still reporting a live connection is
        refused (dropping it would abandon a socket nobody can reach); and
        ``expected_service`` lets a late caller prove which service it means.

        The promotion refusal is what makes a reservation *lock the slot* rather than
        merely say so.  Without it a finalization or recovery caller could empty the
        slot between ``reserve_candidate_promotion`` and
        ``commit_candidate_promotion``, and the launch would publish a session whose
        owner had already been dropped -- the ownerless ``RUNNING`` state the
        two-phase promotion exists to make unreachable, re-enterable through a
        different public method.

        The release refusal is its mirror: while a release has reserved the slot, the
        only thing allowed to empty it is that release's own commit, so a second clearer
        cannot drop the ownership out from under a caller that has already released the
        execution lease.
        """

        with self._lock:
            self._refuse_if_release_in_flight(action="clear the slot it has reserved")
            if self._promotion_reservation is not None:
                raise PaperTradingLifecycleError(
                    f"Paper candidate {self._promotion_reservation.candidate_id!r} holds"
                    " the promotion reservation; refusing to clear the slot it is"
                    " reserved to"
                )
            service = self._order_service
            if service is None:
                raise PaperTradingLifecycleError(
                    "no active Paper order service to clear"
                )
            if expected_service is not None and service is not expected_service:
                raise PaperTradingLifecycleError(
                    "refusing to clear a different active Paper order service"
                )
        if bool(service.connection_snapshot().connected):
            raise PaperTradingLifecycleError(
                "refusing to clear an active Paper order service that still"
                " reports a live connection"
            )
        with self._lock:
            if self._order_service is not service:
                raise PaperTradingLifecycleError(
                    "the active Paper order service changed while it was being"
                    " cleared; refusing to remove a different service"
                )
            self._order_service = None
        self._record_error(None)

    # -- one-shot probe -------------------------------------------------

    def probe_order_channel(
        self,
        *,
        config: object,
        repository: object,
        extended_hours_enabled: bool,
    ) -> tuple[object, object]:
        """Connect, read, and disconnect an order channel that is never owned."""

        service: PaperOrderServicePort | None = None
        try:
            service = self._order_service_factory(
                config,
                repository=repository,
                extended_hours_enabled=extended_hours_enabled,
            )
            connection = service.connect()
            broker_state = service.broker_state()
            return connection, broker_state
        finally:
            if service is not None:
                service.disconnect()

    # -- internals -------------------------------------------------------

    @staticmethod
    def _candidate_key(candidate_id: object) -> str:
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise PaperTradingLifecycleError(
                "a Paper candidate id must be a non-empty string"
            )
        return candidate_id

    def _active_service(self) -> PaperOrderServicePort | None:
        with self._lock:
            return self._order_service

    def _candidate_or_raise(self, key: str) -> PaperOrderServicePort:
        with self._lock:
            if key not in self._candidates:
                raise PaperTradingLifecycleError(
                    f"unknown Paper candidate {key!r}"
                )
            service = self._candidates[key]
        if service is None:
            raise PaperTradingLifecycleError(
                f"Paper candidate {key!r} is still connecting"
            )
        return service

    def _publish_service(
        self, key: str, service: PaperOrderServicePort
    ) -> None:
        with self._lock:
            self._candidates[key] = service

    def _release_reservation(self, key: str) -> None:
        """Drop a reservation without ever dropping a real service."""

        with self._lock:
            if self._candidates.get(key, "missing") is None:
                del self._candidates[key]

    def _best_effort_disconnect(
        self, service: PaperOrderServicePort, description: str
    ) -> bool:
        """Disconnect without raising; report whether control was released."""

        try:
            service.disconnect()
        except Exception as error:
            self._record_error(f"{description} disconnect failed: {error}")
            return False
        return True

    def _record_error(self, message: str | None) -> None:
        with self._lock:
            self._last_error = message


__all__ = ["PaperTradingService"]
