"""The supervisor's four seams: runtime facts, startup facts, schedule, executor.

Each port is narrow on purpose, and the narrowness is the safety property rather
than a tidiness preference.  A supervisor is a component that acts without
anybody watching, so what it *cannot* reach matters more than what it can:

* it cannot see a Paper phase, a session result, a candidate, a strategy or a
  broker object -- the runtime-facts port answers booleans that the Paper
  capability has already turned into a verdict, so the sequencing decisions stay
  with the component that owns the session;
* it cannot take a write lock on the scheduler's authority -- the schedule port
  answers what the *canonical* calendar permits, so there is one holiday
  calendar and one set of session times in the system;
* it cannot connect, submit, cancel, reconcile or release -- the executor port
  carries five requests, and each of them is a question asked of an owner that
  is free to refuse.

The one thing none of these ports can express is completion.  Asking an owner to
start a session is not starting one, so a request's outcome is a separate,
narrower fact than the action's outcome, and the scheduler observes the latter
from the same canonical publication everybody else reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from us_quant.trading.domain.paper_autonomy import PaperAutonomyIntent
from us_quant.trading.domain.paper_autonomy_supervisor import (
    PaperAutonomyRuntimeFacts,
    PaperAutonomyScheduleFacts,
    PaperAutonomyStartupFacts,
    PaperAutonomySupervisorViolation,
)


@dataclass(frozen=True, slots=True)
class PaperAutonomyPreparationRequest:
    """The two inputs a preparation needs, as values rather than as widgets.

    Candidate preparation is owned by the Execution capability, and its size is
    a decision somebody has to make.  While that decision is read off a spin box,
    an unattended scheduler's preparation is sized by whatever the operator last
    left in the UI -- which is a coupling nobody chose.  Passing the two numbers
    as a value breaks it without moving preparation anywhere: the manual route
    still fills exactly the same numbers in, from the widgets it owns.
    """

    candidate_limit: int
    capital_limit: Decimal

    def __post_init__(self) -> None:
        if self.candidate_limit <= 0:
            raise PaperAutonomySupervisorViolation(
                f"a preparation needs a positive candidate limit, not "
                f"{self.candidate_limit}"
            )
        if self.capital_limit < 0:
            raise PaperAutonomySupervisorViolation(
                "a preparation capital limit cannot be negative"
            )


@dataclass(frozen=True, slots=True)
class PaperAutonomyRequestOutcome:
    """What an owner said about a request -- and nothing more.

    ``accepted`` means the owner admitted the request, not that the action
    happened.  A launch is asynchronous by construction, so the honest answer at
    this seam is "I have taken it", and the scheduler learns the outcome from
    the canonical publication later.  There is deliberately no field here that a
    caller could read as "started".
    """

    accepted: bool
    detail: str

    def __post_init__(self) -> None:
        if not str(self.detail).strip():
            raise PaperAutonomySupervisorViolation(
                "a request outcome must say what the owner reported"
            )


@runtime_checkable
class PaperAutonomyIntentReaderPort(Protocol):
    """The operator's intent, read-only.

    One method, and no write method at all.  ``PaperAutonomyApplication`` remains
    the only authority that may *change* the intent; what a scheduler needs is the
    reading, so giving it the whole application would hand it
    ``enable``/``pause``/``disable``/``engage_kill_switch`` by accident -- and the
    authority it would then hold is precisely the one the control plane exists to
    keep separate from the machinery that acts.

    A read may raise: the store can be unreadable, and the caller must be able to
    turn that into the decision's first clause rather than into an exception.
    """

    def snapshot(self) -> PaperAutonomyIntent:
        """The current intent, or a failure that says it cannot be read."""


@runtime_checkable
class PaperAutonomyRuntimeFactsPort(Protocol):
    """What the Paper capability is doing, projected for one tick.

    Implementations must read the canonical owners *now* and cache nothing.  A
    supervisor tick that reused a previous tick's facts would be deciding on the
    strength of a session that may since have stopped -- and it is the tick
    after a session ends that matters most.
    """

    def facts(self) -> PaperAutonomyRuntimeFacts:
        """The current Paper-side state, as booleans a scheduler can act on."""


@runtime_checkable
class PaperAutonomyStartupFactsPort(Protocol):
    """What the broker, the account and the stores said at startup.

    Read once per process, before any autonomous work is admitted, and never
    repaired: an unknown or non-empty answer is an operator's problem.  An
    implementation may be backed by a real read-only channel probe or by
    providers that already exist; what it must not be is a guess.
    """

    def startup_facts(self) -> PaperAutonomyStartupFacts:
        """The startup classification, with ``None`` wherever it is unknown."""


@runtime_checkable
class PaperAutonomySchedulePort(Protocol):
    """Where the trading day is, and what that permits.

    The verdict is produced from the canonical session provider by an adapter;
    the supervisor never compares a clock to a boundary, so the session times
    and the holiday calendar keep exactly one definition.
    """

    def schedule(self, *, now: datetime) -> PaperAutonomyScheduleFacts:
        """The current trading day and the autonomy windows it is in."""


@runtime_checkable
class PaperAutonomyExecutorPort(Protocol):
    """The five things a supervisor may ask of the existing owners.

    Requests only, and each one is addressed to the owner that already knows how
    to perform it:

    * ``request_prepare`` asks the Execution capability to build a shortlist, and
      carries the two sizing values as a value rather than letting the owner
      read them off a widget;
    * ``request_start`` asks the Paper capability for one unattended launch
      through its canonical entry point -- the same one the button uses;
    * ``request_pause`` / ``request_resume`` change whether new entries may
      open, leaving exits and risk alone;
    * ``request_stop`` asks for an orderly wind-down, which is a request to
      flatten and finalize through the canonical path, never a directive to
      release anything.

    Absent by design: reconcile, confirm-reconciliation, force-flat, force-resume
    and cancel-all.  A supervisor that could ask for those would be a supervisor
    with the authority the manual recovery gate exists to withhold.
    """

    def request_prepare(
        self, request: PaperAutonomyPreparationRequest
    ) -> PaperAutonomyRequestOutcome:
        """Ask for a candidate shortlist of the given size."""

    def request_start(self) -> PaperAutonomyRequestOutcome:
        """Ask for one unattended Paper launch, through the canonical start."""

    def request_pause(self) -> PaperAutonomyRequestOutcome:
        """Ask for new entries to close."""

    def request_resume(self) -> PaperAutonomyRequestOutcome:
        """Ask for new entries to open again."""

    def request_stop(self) -> PaperAutonomyRequestOutcome:
        """Ask for an orderly stop through the canonical wind-down."""


__all__ = [
    "PaperAutonomyExecutorPort",
    "PaperAutonomyIntentReaderPort",
    "PaperAutonomyPreparationRequest",
    "PaperAutonomyRequestOutcome",
    "PaperAutonomyRuntimeFactsPort",
    "PaperAutonomySchedulePort",
    "PaperAutonomyStartupFactsPort",
]
