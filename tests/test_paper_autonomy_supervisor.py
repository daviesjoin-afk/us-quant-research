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


def _runtime(**overrides: bool) -> PaperAutonomyRuntimeFacts:
    """Runtime facts for an idle, healthy Paper capability."""

    base = {
        "shutting_down": False,
        "preparation_active": False,
        "preparation_ready": False,
        "launch_in_flight": False,
        "session_running": False,
        "session_paused": False,
        "manual_recovery_required": False,
        "finalization_pending": False,
        "paper_ownership_clear": True,
    }
    return PaperAutonomyRuntimeFacts(**{**base, **overrides})


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
        runtime=_runtime(session_running=True, shutting_down=True),
        unresolved_action="paper:2026-09-28:r4:start:session",
    )
    assert decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert "control-plane store" in decision.reason


def test_the_kill_switch_outranks_shutdown_and_everything_below() -> None:
    decision = _decide(kill_switch_latched=True, runtime=_runtime(shutting_down=True))
    assert decision.action is PaperAutonomyAction.NOOP
    assert "kill switch" in decision.reason


def test_a_kill_switch_with_a_live_session_asks_for_the_canonical_stop() -> None:
    """The kill switch stops asking; it does not skip finalization.

    An orderly stop is a request to flatten and finalize through the same path a
    human stop uses.  Anything faster would be a scheduler deciding that the
    session did not need to finish -- which is not an authority it has.
    """

    for facts in (
        {"session_running": True},
        {"session_paused": True},
    ):
        decision = _decide(kill_switch_latched=True, runtime=_runtime(**facts))
        assert decision.action is PaperAutonomyAction.STOP


def test_a_kill_switch_never_reconciles() -> None:
    """With a human already required, the kill switch blocks rather than stops."""

    decision = _decide(
        kill_switch_latched=True,
        runtime=_runtime(session_running=True, manual_recovery_required=True),
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


def test_a_disabled_intent_stops_its_own_session_and_starts_nothing() -> None:
    idle = _decide(intent_mode=PaperAutonomyMode.DISABLED)
    assert idle.action is PaperAutonomyAction.NOOP

    running = _decide(
        intent_mode=PaperAutonomyMode.DISABLED, runtime=_runtime(session_running=True)
    )
    assert running.action is PaperAutonomyAction.STOP


def test_a_paused_intent_closes_new_entries_and_never_starts() -> None:
    idle = _decide(intent_mode=PaperAutonomyMode.PAUSED)
    assert idle.action is PaperAutonomyAction.NOOP

    running = _decide(
        intent_mode=PaperAutonomyMode.PAUSED, runtime=_runtime(session_running=True)
    )
    assert running.action is PaperAutonomyAction.PAUSE_ENTRIES

    already = _decide(
        intent_mode=PaperAutonomyMode.PAUSED, runtime=_runtime(session_paused=True)
    )
    assert already.action is PaperAutonomyAction.NOOP


def test_an_enabled_intent_resumes_its_own_paused_session() -> None:
    decision = _decide(runtime=_runtime(session_paused=True))
    assert decision.action is PaperAutonomyAction.RESUME_ENTRIES


def test_an_enabled_intent_leaves_a_running_session_alone() -> None:
    decision = _decide(runtime=_runtime(session_running=True))
    assert decision.action is PaperAutonomyAction.NOOP
    assert "already running" in decision.reason


def test_the_orderly_stop_boundary_outranks_starting_a_new_session() -> None:
    decision = _decide(
        runtime=_runtime(preparation_ready=True),
        schedule=_schedule(orderly_stop_due=True),
    )
    assert decision.action is PaperAutonomyAction.NOOP
    assert "orderly stop boundary" in decision.reason


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


def test_an_uncertain_calendar_is_not_acted_on() -> None:
    decision = _decide(
        runtime=_runtime(preparation_ready=True),
        schedule=_schedule(exceptional_schedule_uncertain=True),
    )
    assert decision.action is PaperAutonomyAction.NOOP
    assert "calendar" in decision.reason


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
            schedule=_schedule(session=window, start_allowed=False),
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
    assert _runtime(session_running=True).idle is False
    assert _runtime(session_paused=True).session_active is True
    assert _runtime(preparation_ready=True).session_active is False
    assert _runtime(preparation_ready=True).idle is False
    assert _runtime(finalization_pending=True).idle is False


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
    store = _ledger(tmp_path)
    for index, status in enumerate(TERMINAL_ACTION_STATUSES):
        key = f"k{index}"
        _claim(store, key)
        store.complete(
            action_key=key,
            status=status,
            completed_at=_NOW,
            detail="terminal",
        )
    assert store.unresolved() == ()
    assert {row.status for row in store.recent()} == set(TERMINAL_ACTION_STATUSES)


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
