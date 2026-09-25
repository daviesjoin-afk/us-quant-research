"""The one write authority for the operator's Paper autonomy intent.

Every accepted transition in the system goes through this object.  It decides
*which* transitions are legal (the store decides nothing), computes the revision
that results, performs the compare-and-swap through the port, and records the
transition in the audit trail.  A desktop button, an operator CLI invocation and
a future scheduler all end up calling the same method here, which is what makes
"two surfaces, one truth" a property rather than a hope.

What this authority does **not** do is the reason it is safe to add before any
automation exists.  It does not start, stop or inspect a Paper session; it does
not reach a broker, an order, a lease, a candidate list or a strategy.  A
scheduler built on top of it will read those from their canonical owners at the
moment it acts, and this module will still know nothing about them.  That
separation is deliberate: a persisted copy of a runtime fact goes stale, and the
copy that is read by an unattended process is exactly the copy that must not.

The kill switch is the one place where the shape of the value carries the safety
argument, so it is worth stating plainly:

* ``engage_kill_switch`` is *total* -- it is legal from any state and always
  produces ``DISABLED`` with the latch set.  It is the one transition an
  operator may need to perform without knowing what state they are in.
* ``clear_kill_switch`` clears the latch and **only** the latch.  It never
  restores the previous mode, because the previous mode is exactly what the
  operator latched away.  Re-enabling autonomy after a kill requires a fresh
  ``enable()``, which is a second, separate decision.
* ``enable()`` refuses while the latch is set.  Between the four of those, the
  sequence ``kill -> clear -> enable`` cannot collapse into ``kill -> clear``.

Errors are refusals, not exceptions on the happy path: a refused transition
writes nothing at all -- no revision, no event -- so a failed operator action
cannot be mistaken for a completed one in the audit trail.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone

from us_quant.trading.domain.paper_autonomy import (
    PaperAutonomyError,
    PaperAutonomyEventKind,
    PaperAutonomyIntent,
    PaperAutonomyMode,
)
from us_quant.trading.ports.paper_autonomy_repository import (
    PaperAutonomyConflict,
    PaperAutonomyEvent,
    PaperAutonomyRepositoryPort,
)


class PaperAutonomyRefused(PaperAutonomyError):
    """The requested transition is not one this authority will perform.

    Raised *instead of* writing, for an unknown or disallowed transition, a
    blank operator reason, and any attempt to resume autonomy while the kill
    switch is latched.  Nothing is stored when this is raised.
    """


#: Modes an ``enable()`` may leave behind.  ``ENABLED`` is absent on purpose: a
#: second enable of an already-enabled system is not a no-op to be absorbed, it
#: is an operator whose view of the system is wrong, and telling them so is
#: cheaper than writing an audit entry that says something changed.
_ENABLEABLE_MODES = (PaperAutonomyMode.DISABLED, PaperAutonomyMode.PAUSED)

#: Modes a ``pause()`` may leave behind.  Pausing a system that is not running
#: autonomously is the same stale-view case as above.
_PAUSABLE_MODES = (PaperAutonomyMode.ENABLED,)

#: Modes a ``disable()`` may leave behind.
_DISABLEABLE_MODES = (PaperAutonomyMode.ENABLED, PaperAutonomyMode.PAUSED)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PaperAutonomyApplication:
    """Legal transitions, revision bookkeeping and the audit trail."""

    def __init__(
        self,
        repository: PaperAutonomyRepositoryPort,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or _utc_now

    # -- reads ----------------------------------------------------------

    def snapshot(self) -> PaperAutonomyIntent:
        """The current intent, exactly as it is stored."""

        return self._repository.load_intent()

    def history(self, limit: int = 10) -> tuple[PaperAutonomyEvent, ...]:
        """The most recent transitions, oldest first."""

        return self._repository.recent_events(limit)

    # -- transitions ----------------------------------------------------

    def enable(
        self, expected_revision: int, reason: str
    ) -> PaperAutonomyIntent:
        """Authorise unsupervised Paper trading from ``DISABLED`` or ``PAUSED``."""

        current = self._prepare(expected_revision, reason, "enable")
        if current.kill_switch_latched:
            raise PaperAutonomyRefused(
                "the kill switch is latched; autonomy cannot be re-enabled "
                "until the latch is explicitly cleared and then enabled again"
            )
        self._require_mode(current, _ENABLEABLE_MODES, "enable")
        return self._write(
            current,
            expected_revision,
            mode=PaperAutonomyMode.ENABLED,
            kind=PaperAutonomyEventKind.ENABLED,
            reason=reason,
        )

    def pause(
        self, expected_revision: int, reason: str
    ) -> PaperAutonomyIntent:
        """Stop opening new Paper work, from ``ENABLED``."""

        current = self._prepare(expected_revision, reason, "pause")
        if current.kill_switch_latched:
            raise PaperAutonomyRefused(
                "the kill switch is latched; the intent is already disabled "
                "and there is nothing to pause"
            )
        self._require_mode(current, _PAUSABLE_MODES, "pause")
        return self._write(
            current,
            expected_revision,
            mode=PaperAutonomyMode.PAUSED,
            kind=PaperAutonomyEventKind.PAUSED,
            reason=reason,
        )

    def disable(
        self, expected_revision: int, reason: str
    ) -> PaperAutonomyIntent:
        """Withdraw the authorisation, from ``ENABLED`` or ``PAUSED``."""

        current = self._prepare(expected_revision, reason, "disable")
        if current.kill_switch_latched:
            raise PaperAutonomyRefused(
                "the kill switch is latched; the intent is already disabled"
            )
        self._require_mode(current, _DISABLEABLE_MODES, "disable")
        return self._write(
            current,
            expected_revision,
            mode=PaperAutonomyMode.DISABLED,
            kind=PaperAutonomyEventKind.DISABLED,
            reason=reason,
        )

    def engage_kill_switch(
        self, expected_revision: int, reason: str
    ) -> PaperAutonomyIntent:
        """Latch autonomy off, from any state.

        The two fields move together -- latched *and* ``DISABLED`` -- because
        the latch alone would leave an ``ENABLED`` intent behind it, and
        clearing the latch would then hand autonomy straight back.  ``kill ->
        clear`` has to end at ``DISABLED``, and it only does if the kill is what
        moved the mode.
        """

        current = self._prepare(expected_revision, reason, "kill")
        return self._write(
            current,
            expected_revision,
            mode=PaperAutonomyMode.DISABLED,
            kind=PaperAutonomyEventKind.KILL_LATCHED,
            reason=reason,
            latched=True,
        )

    def clear_kill_switch(
        self, expected_revision: int, reason: str
    ) -> PaperAutonomyIntent:
        """Release the latch, leaving the mode untouched.

        The mode is passed through unchanged rather than reset to ``DISABLED``:
        clearing a kill switch must not be able to *enable* anything, and it
        must not be able to look like a second disable either.  It releases one
        bit and records that it did.
        """

        current = self._prepare(expected_revision, reason, "clear-kill")
        return self._write(
            current,
            expected_revision,
            mode=current.mode,
            kind=PaperAutonomyEventKind.KILL_CLEARED,
            reason=reason,
            latched=False,
        )

    # -- internals ------------------------------------------------------

    @staticmethod
    def _validate_request(
        expected_revision: int,
        reason: str,
        operation: str,
    ) -> None:
        """Validate the request's own shape, before any state is consulted.

        Only the arguments' shape is checked here; whether the caller's view is
        current is decided in :meth:`_prepare`, and whether the write is still
        legal when it lands is decided by the store.
        """

        if not str(reason).strip():
            raise PaperAutonomyRefused(
                f"{operation} needs the operator's reason; an unrecorded "
                f"autonomy decision is not auditable"
            )
        if expected_revision < 0:
            raise PaperAutonomyRefused(
                f"{operation} needs the revision it was decided against, not "
                f"{expected_revision}"
            )

    def _prepare(
        self,
        expected_revision: int,
        reason: str,
        operation: str,
    ) -> PaperAutonomyIntent:
        """The stored intent a transition is about to be decided against.

        Read fresh on every call rather than cached: the value an operator
        decided against is a *claim* carried in ``expected_revision``, and the
        state the decision is checked against has to be the stored one.

        The revision comparison below is a **message**, not the guard.  It
        exists because the two failures are genuinely different and lead to
        different operator actions -- "your view is stale, re-read and decide
        again" is not "that transition is illegal" -- and running it first means
        a stale surface is told the truth instead of being handed a policy
        refusal for a mode it never saw.  It is not what makes the write safe,
        and it could not be: between this read and the write another surface may
        commit, which is exactly the window
        ``PaperAutonomyRepositoryPort.compare_and_swap_intent`` closes.
        """

        self._validate_request(expected_revision, reason, operation)
        current = self._repository.load_intent()
        if current.revision != expected_revision:
            raise PaperAutonomyConflict(
                f"the stored autonomy revision is {current.revision}, not the "
                f"expected {expected_revision}; re-read the intent and decide "
                f"against the revision that is actually stored"
            )
        return current

    @staticmethod
    def _require_mode(
        current: PaperAutonomyIntent,
        allowed: tuple[PaperAutonomyMode, ...],
        operation: str,
    ) -> None:
        if current.mode not in allowed:
            names = ", ".join(mode.value for mode in allowed)
            raise PaperAutonomyRefused(
                f"{operation} is legal from {names}, not from "
                f"'{current.mode.value}'"
            )

    def _write(
        self,
        current: PaperAutonomyIntent,
        expected_revision: int,
        *,
        mode: PaperAutonomyMode,
        kind: PaperAutonomyEventKind,
        reason: str,
        latched: bool | None = None,
    ) -> PaperAutonomyIntent:
        moment = self._clock()
        replacement = replace(
            current,
            revision=current.revision + 1,
            mode=mode,
            kill_switch_latched=(
                current.kill_switch_latched if latched is None else latched
            ),
            updated_at=moment,
            reason=reason,
        )
        self._repository.compare_and_swap_intent(
            expected_revision=expected_revision,
            replacement=replacement,
        )
        self._repository.append_event(
            PaperAutonomyEvent(
                revision=replacement.revision,
                kind=kind,
                detail=reason,
                occurred_at=moment,
            )
        )
        return replacement


__all__ = [
    "PaperAutonomyApplication",
    "PaperAutonomyRefused",
]
