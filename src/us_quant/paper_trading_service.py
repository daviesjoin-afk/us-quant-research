"""The Paper trading application boundary: order-service ownership and reads.

``MainWindow`` reaches the Paper execution stack only through this service.  It
owns the order-service lifecycle end to end: it constructs and connects
*candidate* order services, promotes exactly one of them into the single
*active* slot once a launch has been validated and armed, reconnects that
active service for manual reconciliation, disconnects it for the zero-state
finalization proof, and clears the slot only after the workflow itself reports
the session finalized.

It deliberately does **not** own the trading semantics.  Submitting, cancelling,
replacing, arming rules, broker/account validation, capital resolution,
reconciliation algorithms, the finalization decision, phase transitions, and the
execution lease all stay exactly where they were.  This class decides only *who
currently holds the broker connection* and hands out the reads and lifecycle
calls the window is allowed to make.

Two ownership slots, never one:

* ``_candidates`` holds services that are connected but not yet trusted.  A
  broker connection succeeding is *not* permission to become the active
  session: the launch still has to survive the preflight, strategy, and capital
  re-checks, so an expired asynchronous callback must be able to dispose of its
  own candidate without ever touching the active slot.
* ``_order_service`` holds the one service that a validated, armed launch
  published.  Promotion refuses to overwrite it, and it is never disconnected
  implicitly by a newer candidate arriving.

Candidate keys are opaque strings supplied by the caller.  This module never
imports the launch-plan or workflow types -- the service must not learn the
shape of a strategy launch.

Construction takes a getter rather than the workflow object.  ``MainWindow``
still replaces ``paper_workflow`` in the safety tests that drive the halted and
refused-close paths; reading through a getter keeps this boundary honest about
which controller is live.

Locking is minimal and never wraps a network call: ownership state is read or
committed under ``RLock``, while ``connect``/``disconnect`` always run outside
it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from .trading.composition.execution import build_execution_candidate
from .workflow_state import PaperWorkflowPhase


class PaperTradingLifecycleError(RuntimeError):
    """A Paper order-service ownership transition was refused.

    Raised when a caller asks for a transition that would lose control of a
    broker connection or silently replace an owner: promoting over a live
    active service, clearing an owner that still reports connected, discarding
    an unknown candidate, or reusing a candidate id.  Every one of these is
    fail-closed -- nothing is mutated and the existing state is left intact.
    """


class PaperConnectionPort(Protocol):
    """The connection fact this service reads; order detail stays in the service."""

    connected: bool


class PaperOrderServicePort(Protocol):
    """The read/lifecycle slice of ``IBKRPaperOrderService`` used here."""

    def connect(self) -> object: ...
    def connection_snapshot(self) -> PaperConnectionPort: ...
    def broker_state(self) -> object: ...
    def disconnect(self) -> None: ...
    def reconciliation_rows_with_latency(
        self, *, session_id: str | None = None, limit: int = 1000
    ) -> Sequence[dict]: ...


class PaperOrderServiceFactory(Protocol):
    """How this service builds an execution channel; injectable for tests.

    The default is the execution composition root, so the window never names
    the concrete IBKR adapter or the concrete order store.
    """

    def __call__(
        self,
        config: object,
        *,
        repository: object,
        extended_hours_enabled: bool,
    ) -> PaperOrderServicePort: ...


class PaperSessionStatePort(Protocol):
    """The finalized flag carried by a coordinator result."""

    finalized: bool


class PaperSessionResultPort(Protocol):
    """The coordinator result slice this service reads."""

    state: PaperSessionStatePort


class PaperWorkflowPort(Protocol):
    """The workflow controller slice this service reads."""

    @property
    def phase(self) -> PaperWorkflowPhase: ...
    @property
    def result(self) -> PaperSessionResultPort | None: ...
    @property
    def reconciliation_evidence(self) -> object | None: ...


WorkflowGetter = Callable[[], PaperWorkflowPort]


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


class PaperTradingService:
    """Owner of the Paper order-service lifecycle, plus the window's reads."""

    def __init__(
        self,
        *,
        workflow_getter: WorkflowGetter,
        order_service_factory: PaperOrderServiceFactory = build_execution_candidate,
    ) -> None:
        self._workflow_getter = workflow_getter
        self._order_service_factory = order_service_factory
        self._lock = threading.RLock()
        # A ``None`` value is a *reservation*: a connect for that id is in
        # flight.  Reserving before connecting is what makes id collisions
        # genuinely fail-closed instead of a check-then-insert race.
        self._candidates: dict[str, PaperOrderServicePort | None] = {}
        self._order_service: PaperOrderServicePort | None = None
        self._last_error: str | None = None

    # -- reads ---------------------------------------------------------

    def phase(self) -> PaperWorkflowPhase:
        """The workflow controller's current phase; never a second copy."""

        return self._workflow_getter().phase

    def is_finalized(self) -> bool:
        """Whether no Paper session is still awaiting finalization.

        The truth comes from the controller's own result, never from a guess
        here.  A window that has not started a session yet also answers ``True``
        -- nothing is outstanding -- which is exactly the close gate's meaning:
        it refuses to close while an unfinalized session still exists.
        """

        result = self._workflow_getter().result
        return result is None or bool(result.state.finalized)

    def has_order_service(self) -> bool:
        """Whether an active order service is currently owned."""

        with self._lock:
            return self._order_service is not None

    def is_connected(self) -> bool:
        """Whether the *active* order service reports a live broker connection.

        Candidates are deliberately excluded: an unarmed candidate is not the
        session, and asking about it here would let a stale connection look
        like the live one.
        """

        service = self._active_service()
        if service is None:
            return False
        return bool(service.connection_snapshot().connected)

    def reconciliation_status(self) -> PaperReconciliationStatus:
        """Whether a reconciliation proof is awaiting explicit confirmation."""

        evidence = self._workflow_getter().reconciliation_evidence
        return PaperReconciliationStatus(awaiting_confirmation=evidence is not None)

    def snapshot(self) -> PaperTradingSnapshot:
        """Derive one immutable lifecycle reading from the live components.

        This is an explicit status query, not a cheap accessor: ``is_connected``
        asks the order service for its connection fact, so do not call it on a
        per-tick render path.
        """

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
        """Whether this id is currently registered (or reserved by a connect)."""

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
        """Build and connect one *candidate* order service; never the owner.

        Returns whatever the underlying ``connect()`` returns.  The candidate is
        registered only on success, so a caller that sees an exception knows
        nothing is tracked.  On failure the created service is disconnected on a
        best-effort basis; if even that fails the reference is kept registered
        rather than dropped, because losing it would mean losing control of a
        possibly-live broker connection.
        """

        key = self._candidate_key(candidate_id)
        with self._lock:
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
        """Borrow a registered candidate service for pre-promotion wiring.

        This is a *borrowed* reference, not new ownership: the caller must not
        store it, assign it to a member, or keep it past the call stack that
        consumed it.  It exists only so the existing ``arm``/``submit``/
        ``publish_armed`` wiring can run before promotion.
        """

        key = self._candidate_key(candidate_id)
        service = self._candidate_or_raise(key)
        return service

    def ensure_candidate_can_promote(self, candidate_id: str) -> None:
        """Pure check that promotion would be legal; mutates nothing.

        Call it before the irreversible ``publish_armed`` step so that the
        promotion which follows cannot fail for a reason that was already
        knowable.
        """

        key = self._candidate_key(candidate_id)
        self._candidate_or_raise(key)
        with self._lock:
            if self._order_service is not None:
                raise PaperTradingLifecycleError(
                    "a Paper order service is already active;"
                    " refusing to replace it"
                )

    def promote_candidate(self, candidate_id: str) -> None:
        """Move a validated candidate into the single active slot.

        Refuses to overwrite an existing active service and never disconnects
        one implicitly -- deciding that an old session is over is the
        finalization path's job, not a side effect of a new launch.
        """

        key = self._candidate_key(candidate_id)
        service = self._candidate_or_raise(key)
        with self._lock:
            if self._order_service is not None:
                raise PaperTradingLifecycleError(
                    "a Paper order service is already active;"
                    " refusing to replace it"
                )
            self._order_service = service
            del self._candidates[key]
        self._record_error(None)

    def discard_candidate(self, candidate_id: str) -> None:
        """Disconnect and forget one stale candidate, and nothing else.

        Only the named candidate is touched: the active service and every other
        candidate are left exactly as they were.  The registration is removed
        only after a successful disconnect, so a failed teardown keeps the
        candidate tracked (and its error recorded) instead of losing the
        reference.
        """

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
        """Reconnect the active service; never creates one.

        Manual reconciliation runs against the session that already exists, so
        an unowned slot is a programming error rather than a reason to connect
        something new.
        """

        service = self._active_service()
        if service is None:
            raise PaperTradingLifecycleError(
                "no active Paper order service to connect"
            )
        return service.connect()

    def disconnect(self) -> None:
        """Run the existing ``disconnect`` semantics once and record any failure.

        This is the same teardown the window performed inline: no submit, no
        cancel, no reconciliation, no resume, and no lease change.  It does
        **not** clear ownership -- a successful disconnect proves the socket is
        gone, not that the session may be released.  A failure is recorded in
        ``last_error`` and re-raised unchanged, so the caller keeps seeing
        exactly the exception it saw before and nothing is swallowed.
        """

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

        Fail-closed in both directions: an active service that still reports a
        live connection is refused (dropping the reference would abandon a
        socket nobody can reach), and ``expected_service`` lets a late caller
        prove it is clearing the service it actually means rather than a newer
        one that replaced it in the meantime.
        """

        with self._lock:
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
        """Connect, read, and disconnect an order channel that is never owned.

        This is the operator's "is the order channel reachable" check: it must
        not register a candidate and must not disturb the active slot.
        """

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
