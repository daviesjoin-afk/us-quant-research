"""The Paper autonomy control core: the precedence order and the action ledger.

Two components are exercised here, and they are the two with the least room for
error.

* :func:`decide_paper_autonomy` is a pure function, so its precedence order can
  be pinned exhaustively.  The order *is* the policy: which reason outranks
  which is a decision somebody made, and the cases below are written one per
  clause so that a clause that creeps up or down the list fails a named test
  rather than quietly changing what a blocked scheduler does.
* the action ledger is where idempotency lives.  A scheduler tick is not
  idempotent by construction, so "the same request can be claimed once" has to
  be a property of the store, and it has to hold against two ticks running at
  once rather than against two ticks that politely take turns.

The supervisor that drives them, the schedule adapter and the desktop host are
not covered here: they do not exist yet on this branch, and a test that asserted
their absence would be a comment rather than a guard.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import pathlib
import sqlite3
import threading

import pytest

from us_quant.trading.adapters.sqlite.paper_autonomy_action_repository import (
    SQLitePaperAutonomyActionRepository,
)
from us_quant.trading.domain.paper_autonomy import PaperAutonomyMode
from us_quant.trading.domain.paper_autonomy_supervisor import (
    AUTONOMOUS_PREPARE_WINDOWS,
    AUTONOMOUS_START_WINDOWS,
    MAXIMUM_TICK_INTERVAL_SECONDS,
    MINIMUM_TICK_INTERVAL_SECONDS,
    TERMINAL_ACTION_STATUSES,
    PaperAutonomyAction,
    PaperAutonomyActionSlot,
    PaperAutonomyActionStatus,
    PaperAutonomyActionType,
    PaperAutonomyDecision,
    PaperAutonomyPolicy,
    PaperAutonomyRuntimeFacts,
    PaperAutonomyScheduleFacts,
    PaperAutonomySessionProvenance,
    PaperAutonomySessionWindow,
    PaperAutonomyStartupFacts,
    PaperAutonomySupervisorViolation,
    action_key_for,
    decide_paper_autonomy,
    shuts_down_action,
)
from us_quant.trading.ports.paper_autonomy_action_repository import (
    PaperAutonomyActionRecord,
    PaperAutonomyActionRepositoryError,
    PaperAutonomyActionRepositoryPort,
    PaperAutonomyActionStoreUnreadable,
)

_DAY = date(2026, 9, 28)
_NOW = datetime(2026, 9, 28, 13, 45, tzinfo=timezone.utc)


# =====================================================================
# Builders: one per fact type, defaulted to the quiet state
# =====================================================================


def _runtime(**overrides: object) -> PaperAutonomyRuntimeFacts:
    """Runtime facts for an idle, healthy, attributable Paper capability.

    ``session_provenance`` is deliberately **not** inferred from the session
    flags.  A helper that filled it in would be the one place a test could forget
    that provenance is a fact in its own right, and every case about a manual or
    unattributable session would then be testing the helper instead.
    """

    base: dict = {
        "shutting_down": False,
        "preparation_active": False,
        "preparation_ready": False,
        "launch_in_flight": False,
        "session_running": False,
        "session_paused": False,
        "session_provenance": PaperAutonomySessionProvenance.NONE,
        "manual_recovery_required": False,
        "finalization_pending": False,
        "paper_ownership_consistent": True,
    }
    return PaperAutonomyRuntimeFacts(**{**base, **overrides})


def _autonomous(**overrides: object) -> PaperAutonomyRuntimeFacts:
    """Facts for a session this supervisor started."""

    return _runtime(
        session_provenance=PaperAutonomySessionProvenance.AUTONOMOUS,
        **overrides,
    )


def _manual(**overrides: object) -> PaperAutonomyRuntimeFacts:
    """Facts for a session an operator started from the Execution route."""

    return _runtime(
        session_provenance=PaperAutonomySessionProvenance.MANUAL, **overrides
    )


def _unattributable(**overrides: object) -> PaperAutonomyRuntimeFacts:
    """Facts for a session nobody can currently account for."""

    return _runtime(
        session_provenance=PaperAutonomySessionProvenance.UNKNOWN, **overrides
    )


def _schedule(**overrides: object) -> PaperAutonomyScheduleFacts:
    """Schedule facts for an ordinary regular session, mid-prepare window."""

    base: dict = {
        "trading_day": _DAY,
        "session": PaperAutonomySessionWindow.REGULAR,
        "preparation_allowed": True,
        "start_allowed": True,
        "orderly_stop_due": False,
        "exceptional_schedule_uncertain": False,
    }
    return PaperAutonomyScheduleFacts(**{**base, **overrides})


def _startup(**overrides: object) -> PaperAutonomyStartupFacts:
    """Startup facts for a process that has proven itself safe."""

    base: dict = {
        "intent_store_readable": True,
        "action_store_readable": True,
        "broker_state_known": True,
        "account_identity_known": True,
        "open_broker_orders": 0,
        "broker_positions": 0,
        "unreconciled_rows": 0,
        "paper_ownership_clear": True,
        "manual_recovery_required": False,
    }
    return PaperAutonomyStartupFacts(**{**base, **overrides})


def _decide(**overrides: object) -> PaperAutonomyDecision:
    """Decide with everything quiet unless a case overrides it."""

    base: dict = {
        "intent_mode": PaperAutonomyMode.ENABLED,
        "kill_switch_latched": False,
        "intent_revision": 4,
        "runtime": _runtime(),
        "schedule": _schedule(),
        "startup": _startup(),
        "unresolved_action": None,
        "start_already_attempted_today": False,
        "control_plane_readable": True,
    }
    return decide_paper_autonomy(**{**base, **overrides})


# =====================================================================
# The precedence order, one case per clause
# =====================================================================


def test_the_control_plane_outranks_everything() -> None:
    """An unreadable intent store is the first thing checked, not the last.

    Everything else in the order is a statement about a World the store
    describes; if the description cannot be read, the rest is guesswork -- and a
    scheduler that guessed would be acting on an authorisation it cannot see.
    """

    decision = _decide(
        control_plane_readable=False,
        kill_switch_latched=True,
        runtime=_autonomous(session_running=True, shutting_down=True),
        unresolved_action="paper:2026-09-28:r4:start:session",
    )
    assert decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert "control-plane store" in decision.reason


def test_inconsistent_ownership_blocks_before_anything_acts() -> None:
    """A lifecycle and an ownership that disagree is a question, not a state.

    Checked above every automated action -- including the ones that would remove
    work -- because "the Paper capability says nothing is running and something
    holds a candidate/service/lease" is precisely the situation where an orderly
    stop might be aimed at the wrong thing.
    """

    decision = _decide(
        runtime=_runtime(paper_ownership_consistent=False),
    )
    assert decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert "ownership" in decision.reason

    killing = _decide(
        kill_switch_latched=True,
        runtime=_autonomous(session_running=True, paper_ownership_consistent=False),
    )
    assert killing.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR


def test_the_kill_switch_outranks_shutdown_and_everything_below() -> None:
    decision = _decide(kill_switch_latched=True, runtime=_runtime(shutting_down=True))
    assert decision.action is PaperAutonomyAction.NOOP
    assert "kill switch" in decision.reason


def test_a_kill_switch_with_an_autonomous_session_asks_for_the_canonical_stop() -> None:
    """The kill switch stops asking; it does not skip finalization.

    An orderly stop is a request to flatten and finalize through the same path a
    human stop uses.  Anything faster would be a scheduler deciding that the
    session did not need to finish -- which is not an authority it has.
    """

    for facts in (
        {"session_running": True},
        {"session_paused": True},
    ):
        decision = _decide(kill_switch_latched=True, runtime=_autonomous(**facts))
        assert decision.action is PaperAutonomyAction.STOP


def test_a_kill_switch_leaves_a_manual_session_alone() -> None:
    """The boundary the provenance fact exists for.

    An autonomy kill switch is not a global Paper stop.  The operator's autonomy
    authorisation covers the sessions automation started; a session they launched
    by hand is not enrolled in it, and stopping it because an autonomy switch
    moved would change the semantics of a feature nobody put under automation.
    The reason says exactly that rather than implying the kill acted on it.
    """

    decision = _decide(
        kill_switch_latched=True, runtime=_manual(session_running=True)
    )
    assert decision.action is PaperAutonomyAction.NOOP
    assert decision.action_key is None
    assert "manual" in decision.reason
    assert "not controlled by the supervisor" in decision.reason


def test_a_kill_switch_never_reconciles() -> None:
    """With a human already required, the kill switch blocks rather than stops."""

    decision = _decide(
        kill_switch_latched=True,
        runtime=_autonomous(
            session_running=True, manual_recovery_required=True
        ),
    )
    assert decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert "recovery" in decision.reason


def test_shutdown_outranks_the_operator_intent() -> None:
    """Close admission before reading the intent, because closing is not revocable."""

    decision = _decide(runtime=_runtime(shutting_down=True))
    assert decision.action is PaperAutonomyAction.NOOP
    assert "shutting down" in decision.reason


def test_an_unresolved_action_blocks_before_the_intent_is_considered() -> None:
    """An action whose outcome is unknown is a question, not a retry."""

    decision = _decide(
        unresolved_action="paper:2026-09-28:r4:start:session",
        intent_mode=PaperAutonomyMode.DISABLED,
    )
    assert decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert "unresolved" in decision.reason


def test_an_unproven_startup_blocks_before_the_intent_is_considered() -> None:
    decision = _decide(startup=_startup(broker_state_known=False))
    assert decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert "startup" in decision.reason


def test_manual_recovery_blocks_and_is_never_reconciled() -> None:
    """The one case the whole design exists to protect.

    A halted or reconciled session has exactly one exit -- an operator -- and a
    scheduler that offered an automatic one would be offering to make a
    reconciliation decision nobody is in a position to make.
    """

    decision = _decide(runtime=_runtime(manual_recovery_required=True))
    assert decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert "reconciliation" in decision.reason
    assert decision.action_key is None


def test_finalization_pending_outranks_the_intent_and_waits() -> None:
    decision = _decide(runtime=_runtime(finalization_pending=True))
    assert decision.action is PaperAutonomyAction.NOOP
    assert "finalizing" in decision.reason


def test_a_disabled_intent_stops_its_autonomous_session_and_starts_nothing() -> None:
    idle = _decide(intent_mode=PaperAutonomyMode.DISABLED)
    assert idle.action is PaperAutonomyAction.NOOP

    running = _decide(
        intent_mode=PaperAutonomyMode.DISABLED,
        runtime=_autonomous(session_running=True),
    )
    assert running.action is PaperAutonomyAction.STOP


def test_a_paused_intent_closes_new_entries_and_never_starts() -> None:
    idle = _decide(intent_mode=PaperAutonomyMode.PAUSED)
    assert idle.action is PaperAutonomyAction.NOOP

    running = _decide(
        intent_mode=PaperAutonomyMode.PAUSED,
        runtime=_autonomous(session_running=True),
    )
    assert running.action is PaperAutonomyAction.PAUSE_ENTRIES

    already = _decide(
        intent_mode=PaperAutonomyMode.PAUSED,
        runtime=_autonomous(session_paused=True),
    )
    assert already.action is PaperAutonomyAction.NOOP


def test_an_enabled_intent_resumes_its_autonomous_paused_session() -> None:
    decision = _decide(runtime=_autonomous(session_paused=True))
    assert decision.action is PaperAutonomyAction.RESUME_ENTRIES


def test_an_enabled_intent_leaves_its_running_session_alone() -> None:
    decision = _decide(runtime=_autonomous(session_running=True))
    assert decision.action is PaperAutonomyAction.NOOP
    assert "already running" in decision.reason


# =====================================================================
# A manual session is not under automation
# =====================================================================


@pytest.mark.parametrize(
    "mode",
    (PaperAutonomyMode.ENABLED, PaperAutonomyMode.DISABLED, PaperAutonomyMode.PAUSED),
)
def test_the_supervisor_never_touches_a_manual_session(
    mode: PaperAutonomyMode,
) -> None:
    """No autonomy switch changes what happens to a hand-launched session.

    Asserted across all three modes at once, because the guarantee is the same
    one in each: the supervisor's authority is over the sessions it started, so
    disabling autonomy, pausing it or enabling it must all be no-ops while a
    manual session is up.  A test per mode would let one of them quietly start
    stopping somebody else's session.
    """

    decision = _decide(intent_mode=mode, runtime=_manual(session_running=True))
    assert decision.action is PaperAutonomyAction.NOOP
    assert decision.action_key is None
    assert "manual" in decision.reason


def test_a_manual_session_is_not_resumed_either() -> None:
    decision = _decide(runtime=_manual(session_paused=True))
    assert decision.action is PaperAutonomyAction.NOOP
    assert decision.action_key is None


def test_a_manual_session_does_not_become_a_double_start() -> None:
    """Ready candidates plus somebody else's session is still not a launch.

    The manual session returns before the start block is ever reached, so the
    scheduler cannot start a second Paper session alongside one it does not own.
    """

    decision = _decide(
        runtime=_manual(session_running=True, preparation_ready=True)
    )
    assert decision.action is PaperAutonomyAction.NOOP
    assert decision.action_key is None


def test_an_unattributable_session_blocks_and_is_never_controlled() -> None:
    """Unknown is not manual: nobody can account for the session at all.

    A manual session is one the supervisor knows to leave alone.  A session whose
    provenance is unknown is one that might be the previous process's, and the
    safe reading is that a human has to establish what it is -- never to pause,
    resume, stop or start on the strength of a guess.
    """

    for facts in ({"session_running": True}, {"session_paused": True}):
        for mode in (
            PaperAutonomyMode.ENABLED,
            PaperAutonomyMode.DISABLED,
            PaperAutonomyMode.PAUSED,
        ):
            decision = _decide(
                intent_mode=mode, runtime=_unattributable(**facts)
            )
            assert (
                decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
            ), (facts, mode)
            assert decision.action_key is None

    killing = _decide(
        kill_switch_latched=True, runtime=_unattributable(session_running=True)
    )
    assert killing.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR


# =====================================================================
# The orderly stop boundary
# =====================================================================


def _at_the_boundary() -> PaperAutonomyScheduleFacts:
    """A schedule at the mandatory wind-down: nothing may be added."""

    return _schedule(
        preparation_allowed=False,
        start_allowed=False,
        orderly_stop_due=True,
    )


def _uncertain() -> PaperAutonomyScheduleFacts:
    """A schedule whose calendar could not be read: it permits nothing.

    Both permissions have to be off, and the type enforces that: a schedule that
    says "I could not be read" and "starting is permitted" at once is a
    contradiction, not a fact the decision should have to arbitrate.
    """

    return _schedule(
        exceptional_schedule_uncertain=True,
        preparation_allowed=False,
        start_allowed=False,
    )


def test_an_autonomous_running_session_stops_at_the_boundary() -> None:
    decision = _decide(
        runtime=_autonomous(session_running=True), schedule=_at_the_boundary()
    )
    assert decision.action is PaperAutonomyAction.STOP


def test_an_autonomous_paused_session_stops_at_the_boundary() -> None:
    """The bug this replaced: a paused session at the boundary was *resumed*.

    Reading the active session before the clock meant the paused branch reached
    ``RESUME_ENTRIES`` first, and the wind-down was never consulted -- the
    scheduler would have opened entries into the close on the one day it had
    already decided to be flat by a deadline.
    """

    decision = _decide(
        runtime=_autonomous(session_paused=True), schedule=_at_the_boundary()
    )
    assert decision.action is PaperAutonomyAction.STOP


def test_a_manual_session_at_the_boundary_is_left_alone() -> None:
    decision = _decide(
        runtime=_manual(session_running=True), schedule=_at_the_boundary()
    )
    assert decision.action is PaperAutonomyAction.NOOP


def test_an_unattributable_session_at_the_boundary_blocks() -> None:
    decision = _decide(
        runtime=_unattributable(session_running=True),
        schedule=_at_the_boundary(),
    )
    assert decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR


def test_the_boundary_outranks_the_intent_but_not_a_kill() -> None:
    """Within an autonomous session, the clock beats the mode, and both beat nothing.

    A kill switch already stops, so the boundary cannot matter there; a paused
    intent at the boundary stops rather than pausing, because a session being
    wound down has nothing left to pause for.
    """

    paused_at_boundary = _decide(
        intent_mode=PaperAutonomyMode.PAUSED,
        runtime=_autonomous(session_running=True),
        schedule=_at_the_boundary(),
    )
    assert paused_at_boundary.action is PaperAutonomyAction.STOP

    killed_at_boundary = _decide(
        kill_switch_latched=True,
        runtime=_autonomous(session_running=True),
        schedule=_at_the_boundary(),
    )
    assert killed_at_boundary.action is PaperAutonomyAction.STOP


def test_the_boundary_stops_a_new_session_being_prepared_or_started() -> None:
    decision = _decide(
        runtime=_runtime(preparation_ready=True), schedule=_at_the_boundary()
    )
    assert decision.action is PaperAutonomyAction.NOOP
    assert decision.action_key is None


# =====================================================================
# An unreadable calendar
# =====================================================================


def test_an_uncertain_calendar_is_not_acted_on() -> None:
    """No new session is prepared or started on a schedule nobody can read."""

    assert _decide(schedule=_uncertain()).action is PaperAutonomyAction.NOOP
    assert (
        _decide(
            runtime=_runtime(preparation_ready=True), schedule=_uncertain()
        ).action
        is PaperAutonomyAction.NOOP
    )


def test_an_uncertain_calendar_pauses_an_autonomous_running_session() -> None:
    """Uncertainty closes entries; it does not open them.

    The narrowest fail-closed action available once a session exists.  A stop
    would be more than the situation calls for -- the exits, the risk logic and
    the stop path are all still working -- and a no-op would leave the session
    opening new positions on a calendar nobody could read.
    """

    decision = _decide(
        runtime=_autonomous(session_running=True), schedule=_uncertain()
    )
    assert decision.action is PaperAutonomyAction.PAUSE_ENTRIES
    assert decision.action is not PaperAutonomyAction.STOP
    assert "calendar" in decision.reason


def test_an_uncertain_calendar_does_not_resume_an_autonomous_paused_session(
) -> None:
    """The fail-closed property the review found missing.

    Reading the operator's mode before the calendar meant an uncertain schedule
    *resumed* a paused autonomous session -- the uncertainty itself was the
    reason new entries were opened.  Now the calendar is consulted first and the
    only answer it can give a paused session is "stay paused".
    """

    decision = _decide(
        runtime=_autonomous(session_paused=True), schedule=_uncertain()
    )
    assert decision.action is PaperAutonomyAction.NOOP
    assert decision.action_key is None


def test_an_uncertain_calendar_never_controls_a_manual_session() -> None:
    for facts in ({"session_running": True}, {"session_paused": True}):
        decision = _decide(runtime=_manual(**facts), schedule=_uncertain())
        assert decision.action is PaperAutonomyAction.NOOP, facts
        assert decision.action_key is None, facts


def test_an_uncertain_calendar_with_unknown_provenance_blocks() -> None:
    for facts in ({"session_running": True}, {"session_paused": True}):
        decision = _decide(
            runtime=_unattributable(**facts), schedule=_uncertain()
        )
        assert (
            decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
        ), facts


def test_the_stop_boundary_outranks_an_uncertain_calendar() -> None:
    """A session that must be flat by a deadline still stops.

    Both facts can hold at once, and when they do the known wind-down wins: a
    pause would leave a session that has to be closed by a deadline merely
    not opening new positions.
    """

    decision = _decide(
        runtime=_autonomous(session_running=True),
        schedule=_schedule(
            orderly_stop_due=True,
            exceptional_schedule_uncertain=True,
            preparation_allowed=False,
            start_allowed=False,
        ),
    )
    assert decision.action is PaperAutonomyAction.STOP


def test_an_uncertain_calendar_pauses_under_a_paused_intent_too() -> None:
    """The paused-intent branch already fails closed, and stays that way."""

    decision = _decide(
        intent_mode=PaperAutonomyMode.PAUSED,
        runtime=_autonomous(session_running=True),
        schedule=_uncertain(),
    )
    assert decision.action is PaperAutonomyAction.PAUSE_ENTRIES


def test_an_uncertain_schedule_cannot_advertise_start_or_prepare() -> None:
    """The contradiction is refused where it is built, not arbitrated later."""

    with pytest.raises(PaperAutonomySupervisorViolation):
        _schedule(exceptional_schedule_uncertain=True, start_allowed=True)
    with pytest.raises(PaperAutonomySupervisorViolation):
        _schedule(exceptional_schedule_uncertain=True, preparation_allowed=True)

    allowed = _uncertain()
    assert allowed.exceptional_schedule_uncertain is True
    assert (allowed.start_allowed, allowed.preparation_allowed) == (False, False)


def test_one_autonomous_start_per_trading_day() -> None:
    """The day's first attempt closes the day, whatever became of it.

    Checked against ``start_already_attempted_today`` rather than against a
    success, because the attempt whose outcome is unknown is precisely the one a
    retry would double.
    """

    decision = _decide(
        runtime=_runtime(preparation_ready=True),
        start_already_attempted_today=True,
    )
    assert decision.action is PaperAutonomyAction.NOOP
    assert "already been attempted" in decision.reason


def test_preparation_in_flight_is_not_prepared_twice() -> None:
    decision = _decide(
        runtime=_runtime(preparation_active=True), schedule=_schedule()
    )
    assert decision.action is PaperAutonomyAction.NOOP
    assert "already in flight" in decision.reason


def test_a_launch_in_flight_is_not_started_twice() -> None:
    decision = _decide(
        runtime=_runtime(launch_in_flight=True, preparation_ready=True)
    )
    assert decision.action is PaperAutonomyAction.NOOP
    assert "launch is already in flight" in decision.reason


def test_ready_candidates_with_an_open_start_window_start() -> None:
    decision = _decide(runtime=_runtime(preparation_ready=True))
    assert decision.action is PaperAutonomyAction.START
    assert decision.action_key is not None


def test_ready_candidates_outside_the_start_window_do_not_start() -> None:
    """Ready is not permission: the window decides, and v1's window is regular.

    Asserted window by window rather than as one "not regular" case, because the
    interesting claim is which windows are enumerated -- a future widening has to
    change this list on purpose.
    """

    for window in (
        PaperAutonomySessionWindow.PREMARKET,
        PaperAutonomySessionWindow.AFTER_HOURS,
        PaperAutonomySessionWindow.OVERNIGHT,
        PaperAutonomySessionWindow.CLOSED,
        PaperAutonomySessionWindow.MAINTENANCE,
    ):
        decision = _decide(
            runtime=_runtime(preparation_ready=True),
            schedule=_schedule(
                session=window,
                # Premarket legitimately permits preparation; the others permit
                # nothing, and the schedule type refuses to pretend otherwise.
                preparation_allowed=(
                    window in AUTONOMOUS_PREPARE_WINDOWS
                ),
                start_allowed=False,
            ),
        )
        assert decision.action is PaperAutonomyAction.NOOP, window


def test_premarket_may_prepare() -> None:
    """The one window outside regular hours that may do anything at all."""

    decision = _decide(
        schedule=_schedule(
            session=PaperAutonomySessionWindow.PREMARKET,
            preparation_allowed=True,
            start_allowed=False,
        )
    )
    assert decision.action is PaperAutonomyAction.PREPARE


def test_a_closed_session_prepares_nothing() -> None:
    decision = _decide(
        schedule=_schedule(
            session=PaperAutonomySessionWindow.CLOSED,
            preparation_allowed=False,
            start_allowed=False,
        )
    )
    assert decision.action is PaperAutonomyAction.NOOP


def test_the_windows_autonomy_may_use_are_pinned() -> None:
    """v1's narrowness is a fact about this vocabulary, not about a config.

    Regular-only starts, and no configuration flag that widens them: an
    unattended system should be permitted strictly less than a human, and a
    future autonomous extended-hours decision needs its own review rather than a
    switch somebody can flip.
    """

    assert AUTONOMOUS_START_WINDOWS == (PaperAutonomySessionWindow.REGULAR,)
    assert PaperAutonomySessionWindow.PREMARKET in AUTONOMOUS_PREPARE_WINDOWS
    for window in (
        PaperAutonomySessionWindow.AFTER_HOURS,
        PaperAutonomySessionWindow.OVERNIGHT,
        PaperAutonomySessionWindow.CLOSED,
        PaperAutonomySessionWindow.MAINTENANCE,
    ):
        assert window not in AUTONOMOUS_PREPARE_WINDOWS


def test_the_quiet_case_is_a_preparation_and_not_a_noop() -> None:
    """The default here is an authorised operator inside an open prepare window.

    Deliberately *not* a no-op: every precedence case below asserts that some
    reason above preparation wins *despite* an open window, which is only a
    claim worth making if the window was open to begin with.
    """

    assert _decide().action is PaperAutonomyAction.PREPARE


def test_a_window_that_permits_nothing_is_a_noop() -> None:
    assert (
        _decide(
            schedule=_schedule(preparation_allowed=False, start_allowed=False)
        ).action
        is PaperAutonomyAction.NOOP
    )


def test_no_decision_offers_a_repairing_action() -> None:
    """Nothing in the vocabulary can reconcile, flatten, resume or cancel.

    Asserted over the enum rather than over any single decision: the guarantee
    is that there is no member to decide, so a future branch cannot return one
    without this failing.
    """

    forbidden = (
        "AUTO_RECONCILE",
        "CONFIRM_RECONCILIATION",
        "FORCE_FLAT",
        "FORCE_RESUME",
        "FORCE_CANCEL_ALL",
        "RELEASE_LEASE",
    )
    names = {member.name for member in PaperAutonomyAction}
    for name in forbidden:
        assert name not in names


def test_shutting_down_actions_are_classified_once() -> None:
    assert shuts_down_action(PaperAutonomyAction.STOP)
    assert shuts_down_action(PaperAutonomyAction.PAUSE_ENTRIES)
    assert shuts_down_action(PaperAutonomyAction.NOOP)
    assert not shuts_down_action(PaperAutonomyAction.START)
    assert not shuts_down_action(PaperAutonomyAction.PREPARE)
    assert not shuts_down_action(PaperAutonomyAction.RESUME_ENTRIES)


# =====================================================================
# Decision identity
# =====================================================================


def test_an_executable_decision_needs_a_key_and_a_day() -> None:
    with pytest.raises(PaperAutonomySupervisorViolation):
        PaperAutonomyDecision(
            action=PaperAutonomyAction.START,
            reason="because",
            trading_day=_DAY,
            intent_revision=1,
            action_key=None,
        )
    with pytest.raises(PaperAutonomySupervisorViolation):
        PaperAutonomyDecision(
            action=PaperAutonomyAction.START,
            reason="because",
            trading_day=None,
            intent_revision=1,
            action_key="k",
        )


def test_the_same_situation_produces_the_same_action_key() -> None:
    """Determinism is the mechanism, so it is asserted directly.

    Two ticks that see the same intent revision on the same day in the same slot
    must produce the same key; that is what lets the store refuse the second one.
    A randomly generated key would prove the opposite of what this is for.
    """

    first = _decide(runtime=_runtime(preparation_ready=True))
    second = _decide(runtime=_runtime(preparation_ready=True))
    assert first.action_key == second.action_key

    other_revision = _decide(
        runtime=_runtime(preparation_ready=True), intent_revision=5
    )
    assert other_revision.action_key != first.action_key


def test_an_action_key_names_the_day_revision_action_and_slot() -> None:
    key = action_key_for(
        trading_day=_DAY,
        intent_revision=7,
        action=PaperAutonomyActionType.PREPARE,
        slot=PaperAutonomyActionSlot.SESSION,
    )
    assert key == "paper:2026-09-28:r7:prepare:session"


def test_an_action_key_refuses_a_negative_attempt_and_revision() -> None:
    with pytest.raises(PaperAutonomySupervisorViolation):
        action_key_for(
            trading_day=_DAY,
            intent_revision=-1,
            action=PaperAutonomyActionType.PREPARE,
            slot=PaperAutonomyActionSlot.SESSION,
        )
    with pytest.raises(PaperAutonomySupervisorViolation):
        action_key_for(
            trading_day=_DAY,
            intent_revision=1,
            action=PaperAutonomyActionType.PREPARE,
            slot=PaperAutonomyActionSlot.SESSION,
            attempt=-1,
        )


def test_a_backoff_attempt_is_a_different_key() -> None:
    base = action_key_for(
        trading_day=_DAY,
        intent_revision=1,
        action=PaperAutonomyActionType.PREPARE,
        slot=PaperAutonomyActionSlot.SESSION,
    )
    retry = action_key_for(
        trading_day=_DAY,
        intent_revision=1,
        action=PaperAutonomyActionType.PREPARE,
        slot=PaperAutonomyActionSlot.SESSION,
        attempt=1,
    )
    assert retry != base
    assert retry.startswith(base)


# =====================================================================
# Policy
# =====================================================================


def _policy(**overrides: object) -> PaperAutonomyPolicy:
    base: dict = {
        "prepare_not_before_et": time(9, 20),
        "start_not_before_et": time(9, 30),
        "latest_start_et": time(15, 30),
        "orderly_stop_at_et": time(15, 50),
        "candidate_limit": 5,
        "requested_capital_limit": Decimal("500"),
        "tick_interval_seconds": 30,
    }
    return PaperAutonomyPolicy(**{**base, **overrides})


def test_a_policy_refuses_unordered_times() -> None:
    with pytest.raises(PaperAutonomySupervisorViolation):
        _policy(start_not_before_et=time(9, 0))


def test_a_policy_refuses_nonsense_limits() -> None:
    with pytest.raises(PaperAutonomySupervisorViolation):
        _policy(candidate_limit=0)
    with pytest.raises(PaperAutonomySupervisorViolation):
        _policy(requested_capital_limit=Decimal("-1"))
    with pytest.raises(PaperAutonomySupervisorViolation):
        _policy(tick_interval_seconds=0)


def test_the_tick_interval_is_clamped_rather_than_trusted() -> None:
    """A hand-edited config cannot produce a busy loop or a dead scheduler."""

    assert _policy(tick_interval_seconds=10_000).bounded_tick_interval_seconds == (
        MAXIMUM_TICK_INTERVAL_SECONDS
    )
    assert _policy(tick_interval_seconds=1).bounded_tick_interval_seconds >= (
        MINIMUM_TICK_INTERVAL_SECONDS
    )


def test_the_policy_is_not_part_of_the_operator_intent() -> None:
    """Two truths, two owners: authorising autonomy is not choosing the hours."""

    from dataclasses import fields

    from us_quant.trading.domain.paper_autonomy import PaperAutonomyIntent

    intent_fields = {field.name for field in fields(PaperAutonomyIntent)}
    policy_fields = {field.name for field in fields(PaperAutonomyPolicy)}
    assert intent_fields.isdisjoint(policy_fields - {"trading_day"})


# =====================================================================
# Facts
# =====================================================================


def test_startup_is_only_safe_when_everything_is_proven() -> None:
    assert _startup().proven_safe is True
    assert _startup(open_broker_orders=None).proven_safe is False
    assert _startup(broker_positions=None).proven_safe is False
    assert _startup(unreconciled_rows=None).proven_safe is False
    assert _startup(open_broker_orders=1).proven_safe is False
    assert _startup(broker_positions=2).proven_safe is False
    assert _startup(unreconciled_rows=1).proven_safe is False
    assert _startup(account_identity_known=False).proven_safe is False
    assert _startup(broker_state_known=False).proven_safe is False
    assert _startup(intent_store_readable=False).proven_safe is False
    assert _startup(action_store_readable=False).proven_safe is False
    assert _startup(paper_ownership_clear=False).proven_safe is False
    assert _startup(manual_recovery_required=True).proven_safe is False


def test_unknown_is_not_the_same_as_zero() -> None:
    """The whole reason the counts are optional.

    An ``int`` defaulting to zero would erase the difference between "there are
    no open orders" and "I could not find out" -- which is the difference
    between a system that is safe to start and one that only looks safe.
    """

    unknown = _startup(open_broker_orders=None)
    empty = _startup(open_broker_orders=0)
    assert unknown.proven_safe is not empty.proven_safe


def test_runtime_facts_derive_activity() -> None:
    assert _runtime().idle is True
    assert _autonomous(session_running=True).idle is False
    assert _autonomous(session_paused=True).session_active is True
    assert _runtime(preparation_ready=True).session_active is False
    assert _runtime(preparation_ready=True).idle is False
    assert _runtime(finalization_pending=True).idle is False


def test_only_an_autonomous_session_is_the_supervisors_to_control() -> None:
    assert _autonomous(session_running=True).autonomous_session_active is True
    assert _manual(session_running=True).autonomous_session_active is False
    assert _unattributable(session_running=True).autonomous_session_active is False
    assert _runtime().autonomous_session_active is False


# =====================================================================
# Facts that contradict themselves cannot be constructed
# =====================================================================


def test_a_session_cannot_be_running_and_paused_at_once() -> None:
    with pytest.raises(PaperAutonomySupervisorViolation):
        _autonomous(session_running=True, session_paused=True)


def test_a_live_session_must_be_attributed() -> None:
    """A session with provenance ``NONE`` is a caller that forgot to look.

    ``NONE`` means "no session is up".  A fact set that also says one is running
    is not a situation for the precedence order to resolve: by the time it could,
    it would already be choosing an action for a session it cannot attribute.
    """

    with pytest.raises(PaperAutonomySupervisorViolation):
        _runtime(session_running=True)
    with pytest.raises(PaperAutonomySupervisorViolation):
        _runtime(session_paused=True)


@pytest.mark.parametrize(
    "provenance",
    (
        PaperAutonomySessionProvenance.AUTONOMOUS,
        PaperAutonomySessionProvenance.MANUAL,
        PaperAutonomySessionProvenance.UNKNOWN,
    ),
)
def test_an_idle_capability_is_not_attributed(
    provenance: PaperAutonomySessionProvenance,
) -> None:
    """The other direction: a provenance with nothing to attribute it to.

    An idle capability carrying a provenance is a caller working from a stale
    reading, and the direction matters -- the decision would otherwise be free to
    act on a session that is not there.
    """

    with pytest.raises(PaperAutonomySupervisorViolation):
        _runtime(session_provenance=provenance)


# =====================================================================
# Regular hours only, structurally
# =====================================================================


@pytest.mark.parametrize(
    "window",
    (
        PaperAutonomySessionWindow.PREMARKET,
        PaperAutonomySessionWindow.AFTER_HOURS,
        PaperAutonomySessionWindow.OVERNIGHT,
        PaperAutonomySessionWindow.CLOSED,
        PaperAutonomySessionWindow.MAINTENANCE,
    ),
)
def test_no_window_outside_regular_can_permit_a_start(
    window: PaperAutonomySessionWindow,
) -> None:
    """The schedule cannot *express* an autonomous start outside regular hours.

    While ``start_allowed`` was the only thing the decision read, "regular only"
    was a promise the adapter made: a fact carrying ``after_hours`` with
    ``start_allowed=True`` would have launched a session in a window v1 does not
    permit, and nothing would have objected.  Now it cannot be built -- manual
    Paper keeps every window, and the autonomy vocabulary simply has no way to
    describe the permission.
    """

    with pytest.raises(PaperAutonomySupervisorViolation):
        _schedule(session=window, start_allowed=True)


@pytest.mark.parametrize(
    "window",
    (
        PaperAutonomySessionWindow.AFTER_HOURS,
        PaperAutonomySessionWindow.OVERNIGHT,
        PaperAutonomySessionWindow.CLOSED,
        PaperAutonomySessionWindow.MAINTENANCE,
    ),
)
def test_no_window_outside_the_prepare_set_can_permit_preparation(
    window: PaperAutonomySessionWindow,
) -> None:
    """Isolated from the start rule on purpose.

    ``start_allowed`` is set to ``False`` here so the *start* invariant cannot be
    the reason the construction fails.  Without that, every case below would pass
    for the wrong rule and the preparation limit would be untested -- measured,
    not assumed: a mutation that removes this invariant survived until the cases
    were narrowed this way.
    """

    with pytest.raises(PaperAutonomySupervisorViolation):
        _schedule(
            session=window, preparation_allowed=True, start_allowed=False
        )


def test_premarket_may_prepare_and_may_not_start() -> None:
    """The one window outside regular hours that keeps a permission."""

    allowed = _schedule(
        session=PaperAutonomySessionWindow.PREMARKET,
        preparation_allowed=True,
        start_allowed=False,
    )
    assert allowed.preparation_allowed is True
    with pytest.raises(PaperAutonomySupervisorViolation):
        _schedule(
            session=PaperAutonomySessionWindow.PREMARKET,
            preparation_allowed=True,
            start_allowed=True,
        )


def test_regular_hours_permit_both() -> None:
    allowed = _schedule(
        session=PaperAutonomySessionWindow.REGULAR,
        preparation_allowed=True,
        start_allowed=True,
    )
    assert (allowed.preparation_allowed, allowed.start_allowed) == (True, True)


def test_a_wind_down_cannot_also_permit_work() -> None:
    """Ordering a session flat while permitting a new one is not a schedule.

    The decision function would have believed whichever branch it read first, so
    the contradiction is refused where it is built rather than resolved later.
    """

    with pytest.raises(PaperAutonomySupervisorViolation):
        _schedule(orderly_stop_due=True, start_allowed=True)
    with pytest.raises(PaperAutonomySupervisorViolation):
        _schedule(orderly_stop_due=True, preparation_allowed=True)

    clean = _schedule(
        orderly_stop_due=True,
        preparation_allowed=False,
        start_allowed=False,
    )
    assert clean.orderly_stop_due is True


# =====================================================================
# The action ledger
# =====================================================================


def _ledger(tmp_path: pathlib.Path) -> SQLitePaperAutonomyActionRepository:
    return SQLitePaperAutonomyActionRepository(tmp_path / "actions.sqlite3")


def _claim(
    store: SQLitePaperAutonomyActionRepository,
    key: str,
    *,
    action: PaperAutonomyActionType = PaperAutonomyActionType.PREPARE,
    day: date = _DAY,
    revision: int = 4,
    status: PaperAutonomyActionStatus = PaperAutonomyActionStatus.CLAIMED,
    detail: str = "the scheduler asked for it",
) -> bool:
    return store.claim(
        PaperAutonomyActionRecord(
            action_key=key,
            intent_revision=revision,
            trading_day=day,
            action=action,
            status=status,
            claimed_at=_NOW,
            completed_at=None,
            detail=detail,
        )
    )


def test_the_ledger_satisfies_its_port(
    tmp_path: pathlib.Path,
) -> None:
    assert isinstance(_ledger(tmp_path), PaperAutonomyActionRepositoryPort)


def test_an_action_key_can_be_claimed_exactly_once(
    tmp_path: pathlib.Path,
) -> None:
    store = _ledger(tmp_path)
    assert _claim(store, "k") is True
    assert _claim(store, "k") is False
    assert len(store.recent()) == 1


def test_two_concurrent_claims_produce_one_winner(
    tmp_path: pathlib.Path,
) -> None:
    """Eight ticks, one action: the store decides, not the last caller.

    Each worker builds its own repository over one file, which is what the
    scheduler, a restart and the operator CLI actually are -- separate objects,
    one ledger.  The barrier makes them race rather than queue, so a
    read-then-write claim fails this instead of passing it by luck.
    """

    path = tmp_path / "actions.sqlite3"
    SQLitePaperAutonomyActionRepository(path)
    workers = 8
    barrier = threading.Barrier(workers)

    def attempt(_index: int) -> bool:
        store = SQLitePaperAutonomyActionRepository(path)
        barrier.wait()
        return _claim(store, "same-key")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(attempt, range(workers)))

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == workers - 1
    assert len(SQLitePaperAutonomyActionRepository(path).recent()) == 1


def test_a_claim_survives_a_restart(tmp_path: pathlib.Path) -> None:
    store = _ledger(tmp_path)
    assert _claim(store, "k") is True
    assert _claim(_ledger(tmp_path), "k") is False


def test_a_claim_is_unresolved_until_it_gets_an_outcome(
    tmp_path: pathlib.Path,
) -> None:
    """The state crash recovery reads, asserted at both levels.

    A claimed action carries no completion time at all, and that absence is what
    a restart turns into "a human has to look": the record cannot say whether
    the request was performed, half-performed or never performed.
    """

    store = _ledger(tmp_path)
    _claim(store, "k")
    unresolved = store.unresolved()
    assert len(unresolved) == 1
    assert unresolved[0].is_terminal is False
    assert unresolved[0].completed_at is None


def test_an_outcome_is_written_exactly_once(tmp_path: pathlib.Path) -> None:
    store = _ledger(tmp_path)
    _claim(store, "k")
    store.mark_requested(action_key="k", detail="the owner accepted the request")
    store.complete(
        action_key="k",
        status=PaperAutonomyActionStatus.SUCCEEDED,
        completed_at=_NOW,
        detail="the session was observed running",
    )
    assert store.unresolved() == ()
    assert store.recent()[0].status is PaperAutonomyActionStatus.SUCCEEDED

    with pytest.raises(PaperAutonomyActionRepositoryError):
        store.complete(
            action_key="k",
            status=PaperAutonomyActionStatus.FAILED,
            completed_at=_NOW,
            detail="a late callback disagreeing with a finished action",
        )
    assert store.recent()[0].status is PaperAutonomyActionStatus.SUCCEEDED


def test_an_outcome_for_an_unknown_key_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    store = _ledger(tmp_path)
    with pytest.raises(PaperAutonomyActionRepositoryError):
        store.complete(
            action_key="never-claimed",
            status=PaperAutonomyActionStatus.SUCCEEDED,
            completed_at=_NOW,
            detail="nothing to complete",
        )


def test_an_outcome_must_be_terminal(tmp_path: pathlib.Path) -> None:
    store = _ledger(tmp_path)
    _claim(store, "k")
    for status in (
        PaperAutonomyActionStatus.CLAIMED,
        PaperAutonomyActionStatus.REQUESTED,
    ):
        with pytest.raises(PaperAutonomyActionRepositoryError):
            store.complete(
                action_key="k",
                status=status,
                completed_at=_NOW,
                detail="not an outcome",
            )


def test_every_terminal_status_can_be_recorded(
    tmp_path: pathlib.Path,
) -> None:
    """All three terminal outcomes are reachable, from a legal state.

    ``SUCCEEDED`` goes through ``REQUESTED``, and only through it: a success
    comes from a completion the canonical owner published, and an owner that was
    never recorded as having accepted the request cannot have published one.
    ``REFUSED`` and ``FAILED`` are reachable both straight from ``CLAIMED`` (the
    owner refused, or the call raised) and after a request was accepted (the
    owner later published a failure).
    """

    store = _ledger(tmp_path)
    terminal_outcomes = (
        (PaperAutonomyActionStatus.REFUSED, False),
        (PaperAutonomyActionStatus.FAILED, False),
        (PaperAutonomyActionStatus.REFUSED, True),
        (PaperAutonomyActionStatus.FAILED, True),
        (PaperAutonomyActionStatus.SUCCEEDED, True),
    )
    for index, (status, through_request) in enumerate(terminal_outcomes):
        key = f"k{index}"
        _claim(store, key)
        if through_request:
            store.mark_requested(action_key=key, detail="the owner accepted it")
        store.complete(
            action_key=key,
            status=status,
            completed_at=_NOW,
            detail="terminal",
        )

    assert store.unresolved() == ()
    assert {row.status for row in store.recent()} == {
        status for status, _ in terminal_outcomes
    }


def test_a_start_attempt_is_remembered_whatever_became_of_it(
    tmp_path: pathlib.Path,
) -> None:
    """One unattended start per trading day, counted from *attempts*.

    A ledger that only remembered successes could not see the launch that was
    requested while the broker was connecting and then lost to a crash -- which
    is exactly the attempt a retry would double.  So the query looks for any
    start row, in any status.
    """

    store = _ledger(tmp_path)
    assert store.start_attempted(_DAY) is False

    _claim(store, "s", action=PaperAutonomyActionType.START)
    assert store.start_attempted(_DAY) is True

    store.complete(
        action_key="s",
        status=PaperAutonomyActionStatus.REFUSED,
        completed_at=_NOW,
        detail="the owner refused the launch",
    )
    assert store.start_attempted(_DAY) is True


def test_a_start_attempt_is_scoped_to_its_trading_day(
    tmp_path: pathlib.Path,
) -> None:
    store = _ledger(tmp_path)
    _claim(store, "s", action=PaperAutonomyActionType.START)
    assert store.start_attempted(_DAY + timedelta(days=1)) is False


def test_a_preparation_is_not_a_start(tmp_path: pathlib.Path) -> None:
    store = _ledger(tmp_path)
    _claim(store, "p", action=PaperAutonomyActionType.PREPARE)
    assert store.start_attempted(_DAY) is False


def test_the_ledger_records_no_session_truth(tmp_path: pathlib.Path) -> None:
    """What the ledger is allowed to remember, as an exact field set.

    It answers "what did the scheduler ask for"; anything about what the market
    or the session *is* belongs to the component that owns it, and a copy here
    could only be stale.
    """

    from dataclasses import fields

    names = {field.name for field in fields(PaperAutonomyActionRecord)}
    assert names == {
        "action_key",
        "intent_revision",
        "trading_day",
        "action",
        "status",
        "claimed_at",
        "completed_at",
        "detail",
    }
    for forbidden in (
        "symbols",
        "candidate_symbols",
        "strategy_version_id",
        "phase",
        "positions",
        "orders",
        "portfolio",
        "lease",
    ):
        assert forbidden not in names


@pytest.mark.parametrize(
    "column, value",
    (
        ("action", "SOMETHING_ELSE"),
        ("action", ""),
        ("status", "RUNNING"),
        ("status", ""),
        ("claimed_at", "not a timestamp"),
        ("claimed_at", "2026-09-28T13:45:00"),
        ("intent_revision", "many"),
        ("trading_day", "not a date"),
        ("detail", "   "),
        ("action_key", ""),
    ),
)
def test_a_corrupt_action_record_is_never_read_as_a_valid_one(
    tmp_path: pathlib.Path, column: str, value: str
) -> None:
    """An unreadable ledger refuses every read, for the same reason a row does.

    The ledger's whole job is to answer "has this already happened".  A record
    that cannot be read cannot answer it, and answering with a default would be
    the permissive direction -- so the read raises and the scheduler does not
    act.
    """

    store = _ledger(tmp_path)
    _claim(store, "k")
    connection = sqlite3.connect(store.path)
    try:
        connection.execute(
            f"UPDATE paper_autonomy_action SET {column} = ? WHERE action_key = ?",
            (value, "k"),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperAutonomyActionStoreUnreadable):
        store.recent()
    with pytest.raises(PaperAutonomyActionStoreUnreadable):
        store.unresolved()


def test_a_terminal_record_cannot_carry_no_completion_time(
    tmp_path: pathlib.Path,
) -> None:
    """The record's own invariant, asserted where it lives.

    A terminal row without a completion time would leave a crash analysis unable
    to say whether the record is finished; a non-terminal one *with* a time
    would hand it an outcome to reason from that no action produced.
    """

    store = _ledger(tmp_path)
    _claim(store, "k")
    connection = sqlite3.connect(store.path)
    try:
        connection.execute(
            "UPDATE paper_autonomy_action SET completed_at = NULL, "
            "status = 'succeeded' WHERE action_key = 'k'"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperAutonomyActionStoreUnreadable):
        store.recent()


def test_the_ledger_and_the_intent_store_are_separate(
    tmp_path: pathlib.Path,
) -> None:
    """Two questions, two files: asking one must not need the other.

    Asserted on the paths rather than on the classes, because the failure this
    guards against is somebody pointing both at one database and calling it
    tidier.
    """

    from us_quant.trading.adapters.sqlite.paper_autonomy_repository import (
        SQLitePaperAutonomyRepository,
    )

    intent = SQLitePaperAutonomyRepository(tmp_path / "intent.sqlite3")
    actions = _ledger(tmp_path)
    assert intent.path != actions.path
    assert intent.path.exists() and actions.path.exists()


# =====================================================================
# The action state machine
# =====================================================================


def _record(
    key: str,
    *,
    status: PaperAutonomyActionStatus,
    completed_at: datetime | None = None,
) -> PaperAutonomyActionRecord:
    return PaperAutonomyActionRecord(
        action_key=key,
        intent_revision=4,
        trading_day=_DAY,
        action=PaperAutonomyActionType.PREPARE,
        status=status,
        claimed_at=_NOW,
        completed_at=completed_at,
        detail="assembled by hand",
    )


def test_an_accepted_request_is_recorded_once(
    tmp_path: pathlib.Path,
) -> None:
    """``CLAIMED`` to ``REQUESTED``, and the step happens once.

    The two states mean different things to a restart: a claim whose request
    never reached an owner is a request that certainly did not execute, while an
    accepted one may have.  Repeating the step would erase that difference.
    """

    store = _ledger(tmp_path)
    _claim(store, "k")
    store.mark_requested(action_key="k", detail="the owner accepted the request")
    assert store.recent()[0].status is PaperAutonomyActionStatus.REQUESTED

    with pytest.raises(PaperAutonomyActionRepositoryError):
        store.mark_requested(action_key="k", detail="and again")
    assert store.recent()[0].detail == "the owner accepted the request"


def test_an_accepted_request_survives_a_restart(
    tmp_path: pathlib.Path,
) -> None:
    store = _ledger(tmp_path)
    _claim(store, "k")
    store.mark_requested(action_key="k", detail="the owner accepted the request")

    restarted = _ledger(tmp_path)
    assert [row.status for row in restarted.unresolved()] == [
        PaperAutonomyActionStatus.REQUESTED
    ]


def test_marking_an_unknown_action_requested_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    store = _ledger(tmp_path)
    with pytest.raises(PaperAutonomyActionRepositoryError):
        store.mark_requested(action_key="never-claimed", detail="nothing there")
    assert store.recent() == ()


def test_marking_a_finished_action_requested_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    store = _ledger(tmp_path)
    _claim(store, "k")
    store.complete(
        action_key="k",
        status=PaperAutonomyActionStatus.REFUSED,
        completed_at=_NOW,
        detail="the owner refused it",
    )
    with pytest.raises(PaperAutonomyActionRepositoryError):
        store.mark_requested(action_key="k", detail="too late")
    assert store.recent()[0].status is PaperAutonomyActionStatus.REFUSED


def test_a_success_cannot_be_claimed_before_the_request_was_accepted(
    tmp_path: pathlib.Path,
) -> None:
    """The transition that keeps "accepted" and "completed" apart.

    A success has to come from a completion the canonical owner published, and an
    owner that was never recorded as having accepted the request cannot have
    published one.  Everything else -- refusal and failure straight from a claim
    -- stays legal, because an owner can refuse a request or the call can raise.
    """

    store = _ledger(tmp_path)
    _claim(store, "k")
    with pytest.raises(PaperAutonomyActionRepositoryError):
        store.complete(
            action_key="k",
            status=PaperAutonomyActionStatus.SUCCEEDED,
            completed_at=_NOW,
            detail="claimed success",
        )
    assert store.recent()[0].status is PaperAutonomyActionStatus.CLAIMED
    assert store.unresolved()[0].is_terminal is False


@pytest.mark.parametrize(
    "status",
    (
        PaperAutonomyActionStatus.REQUESTED,
        PaperAutonomyActionStatus.REFUSED,
        PaperAutonomyActionStatus.FAILED,
        PaperAutonomyActionStatus.SUCCEEDED,
    ),
)
def test_a_claim_can_only_open_an_action(
    tmp_path: pathlib.Path, status: PaperAutonomyActionStatus
) -> None:
    """A claim writes an opening state or it writes nothing.

    Accepting any status would let a caller skip the state machine the ledger
    exists to record, and the row it wrote would be indistinguishable from one a
    canonical owner reported.
    """

    store = _ledger(tmp_path)
    with pytest.raises(PaperAutonomyActionRepositoryError):
        store.claim(
            _record(
                "k",
                status=status,
                completed_at=(
                    None
                    if status is PaperAutonomyActionStatus.REQUESTED
                    else _NOW
                ),
            )
        )
    assert store.recent() == ()


def test_a_claim_cannot_carry_a_completion_time(
    tmp_path: pathlib.Path,
) -> None:
    store = _ledger(tmp_path)
    record = PaperAutonomyActionRecord(
        action_key="k",
        intent_revision=4,
        trading_day=_DAY,
        action=PaperAutonomyActionType.PREPARE,
        status=PaperAutonomyActionStatus.CLAIMED,
        claimed_at=_NOW,
        completed_at=None,
        detail="a claim",
    )
    assert store.claim(record) is True
    # The value type refuses the contradictory pair outright, so the storage
    # boundary is never reached with one.
    with pytest.raises(PaperAutonomySupervisorViolation):
        PaperAutonomyActionRecord(
            action_key="k2",
            intent_revision=4,
            trading_day=_DAY,
            action=PaperAutonomyActionType.PREPARE,
            status=PaperAutonomyActionStatus.CLAIMED,
            claimed_at=_NOW,
            completed_at=_NOW,
            detail="a claim with a completion time",
        )


def test_an_action_record_refuses_a_negative_revision() -> None:
    with pytest.raises(PaperAutonomySupervisorViolation):
        PaperAutonomyActionRecord(
            action_key="k",
            intent_revision=-1,
            trading_day=_DAY,
            action=PaperAutonomyActionType.PREPARE,
            status=PaperAutonomyActionStatus.CLAIMED,
            claimed_at=_NOW,
            completed_at=None,
            detail="a revision no operator intent produced",
        )


def test_marking_requested_refuses_a_corrupt_row(
    tmp_path: pathlib.Path,
) -> None:
    """A record this repository cannot fully read is not moved into a new state."""

    store = _ledger(tmp_path)
    _claim(store, "k")
    _corrupt(store, "trading_day = ?", "not a date")
    with pytest.raises(PaperAutonomyActionStoreUnreadable):
        store.mark_requested(action_key="k", detail="the owner accepted it")


def test_completing_refuses_a_corrupt_row(
    tmp_path: pathlib.Path,
) -> None:
    store = _ledger(tmp_path)
    _claim(store, "k")
    _corrupt(store, "intent_revision = ?", "many")
    with pytest.raises(PaperAutonomyActionStoreUnreadable):
        store.complete(
            action_key="k",
            status=PaperAutonomyActionStatus.REFUSED,
            completed_at=_NOW,
            detail="completed anyway",
        )


def _corrupt(
    store: SQLitePaperAutonomyActionRepository,
    assignment: str,
    value: str,
    *,
    key: str = "k",
) -> None:
    connection = sqlite3.connect(store.path)
    try:
        connection.execute(
            f"UPDATE paper_autonomy_action SET {assignment} WHERE action_key = ?",
            (value, key),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize(
    "assignment, value",
    (
        ("action = ?", "BROKEN"),
        ("status = ?", "BROKEN"),
        ("trading_day = ?", "not a date"),
        ("claimed_at = ?", "not a timestamp"),
        ("intent_revision = ?", "many"),
    ),
)
def test_a_corrupt_row_makes_the_start_query_unreadable(
    tmp_path: pathlib.Path, assignment: str, value: str
) -> None:
    """The query that decides whether today's start already happened, fail closed.

    This is the defect the query was restructured for.  While it selected the
    matching row directly, damaging the ``action`` column stopped the row from
    matching -- and the query then answered ``False``, which reads as "no start
    was attempted today" and is the one answer that must never be invented.  The
    end of the day has to be that the ledger is unreadable, not that it is empty.
    """

    store = _ledger(tmp_path)
    _claim(store, "s", action=PaperAutonomyActionType.START)
    _corrupt(store, assignment, value, key="s")

    with pytest.raises(PaperAutonomyActionStoreUnreadable):
        store.start_attempted(_DAY)


def test_a_corrupt_unrelated_row_also_makes_the_start_query_unreadable(
    tmp_path: pathlib.Path,
) -> None:
    """The whole ledger is unreadable, so the scheduler does not act on any of it.

    A damaged preparation row says nothing about today's start on its own, but it
    does say that this ledger can no longer be trusted to answer the question --
    and the scheduler's response to an untrustworthy ledger is to stop.
    """

    store = _ledger(tmp_path)
    _claim(store, "p", action=PaperAutonomyActionType.PREPARE)
    _claim(store, "s", action=PaperAutonomyActionType.START)
    connection = sqlite3.connect(store.path)
    try:
        connection.execute(
            "UPDATE paper_autonomy_action SET status = 'BROKEN' "
            "WHERE action_key = 'p'"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperAutonomyActionStoreUnreadable):
        store.start_attempted(_DAY)
    with pytest.raises(PaperAutonomyActionStoreUnreadable):
        store.unresolved()
