"""The supervisor tick: the sequence, and what only exists at this layer.

The decision function's precedence is pinned in
``tests/test_paper_autonomy_supervisor.py``; what is tested here is the part that
only exists once a supervisor drives it -- that the ledger is claimed *before* an
owner is asked, that an accepted request is recorded as a request rather than as
a success, that a refusal and a raised exception are both terminal, that nothing
is asked twice, and that an unreadable control plane asks nobody at all.

The four ports are fakes and the action ledger is the real SQLite store.  That
split is deliberate: the store's own behaviour is what idempotency rests on, so
faking it would make these tests prove only that the fake works.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import pathlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from us_quant.trading.adapters.sqlite.paper_autonomy_action_repository import (
    SQLitePaperAutonomyActionRepository,
)
from us_quant.trading.application.paper_autonomy_supervisor import (
    PaperAutonomySupervisor,
)
from us_quant.trading.domain.paper_autonomy import (
    PaperAutonomyIntent,
    PaperAutonomyMode,
)
from us_quant.trading.ports.paper_autonomy_repository import (
    PaperAutonomyStoreUnreadable,
)
from us_quant.trading.domain.paper_autonomy_supervisor import (
    PaperAutonomyAction,
    PaperAutonomyActionSlot,
    PaperAutonomyActionStatus,
    PaperAutonomyActionType,
    PaperAutonomyEventCode,
    PaperAutonomyPolicy,
    PaperAutonomyRuntimeFacts,
    PaperAutonomyScheduleFacts,
    PaperAutonomySessionProvenance,
    PaperAutonomySessionWindow,
    PaperAutonomyStartupFacts,
    action_key_for,
)
from us_quant.trading.ports.paper_autonomy_action_repository import (
    PaperAutonomyActionRecord,
)
from us_quant.trading.ports.paper_autonomy_supervisor import (
    PaperAutonomyPreparationRequest,
    PaperAutonomyRequestOutcome,
)

_DAY = date(2026, 9, 28)
_MOMENT = datetime(2026, 9, 28, 13, 45, tzinfo=timezone.utc)


# =====================================================================
# Fakes for the four ports
# =====================================================================


class _Intent:
    """The operator's intent, or a store that cannot be read."""

    def __init__(
        self,
        mode: PaperAutonomyMode = PaperAutonomyMode.ENABLED,
        *,
        revision: int = 4,
        latched: bool = False,
        unreadable: bool = False,
    ) -> None:
        self._intent = PaperAutonomyIntent(
            revision=revision,
            mode=mode,
            kill_switch_latched=latched,
            updated_at=_MOMENT,
            reason="assembled for the test",
        )
        self._unreadable = unreadable

    def snapshot(self) -> PaperAutonomyIntent:
        if self._unreadable:
            raise PaperAutonomyStoreUnreadable("the test store is corrupt")
        return self._intent


class _Runtime:
    def __init__(self, facts: PaperAutonomyRuntimeFacts) -> None:
        self._facts = facts

    def facts(self) -> PaperAutonomyRuntimeFacts:
        return self._facts


class _Startup:
    def __init__(self, facts: PaperAutonomyStartupFacts | None = None) -> None:
        self._facts = facts or _startup_facts()
        self.reads = 0

    def startup_facts(self) -> PaperAutonomyStartupFacts:
        self.reads += 1
        return self._facts


class _Schedule:
    def __init__(self, facts: PaperAutonomyScheduleFacts | None = None) -> None:
        self._facts = facts or _schedule_facts()
        self.reads = 0

    def schedule(self, *, now: datetime) -> PaperAutonomyScheduleFacts:
        self.reads += 1
        return self._facts


class _ExplodingRuntime:
    """A runtime facts port that fails the test if it is asked anything.

    Used to prove the *operational* precedence rather than the documented one:
    when the authorisation cannot be read, the runtime must not be consulted at
    all, and an assertion is the only way to assert a non-observation.
    """

    def facts(self):
        raise AssertionError("the runtime facts must not be read")


def _corrupt_ledger(path: pathlib.Path) -> None:
    """Add a row with a status no reader can interpret."""

    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO paper_autonomy_action(
                action_key, intent_revision, trading_day, action, status,
                claimed_at, completed_at, detail
            ) VALUES ('corrupt', 1, '2026-09-28', 'prepare', 'BROKEN',
                      '2026-09-28T13:45:00+00:00', NULL, 'unreadable')
            """
        )
        connection.commit()
    finally:
        connection.close()


# =====================================================================
# Operational precedence: an unreadable store stops the tick
# =====================================================================


def test_an_unreadable_intent_does_not_touch_any_other_port(
    tmp_path: pathlib.Path,
) -> None:
    """The first clause has to be an execution order, not only a policy order.

    ``decide_paper_autonomy`` checks the control plane first, but that is worth
    nothing if the supervisor has already read the runtime, the schedule and the
    ledger on the way there -- and it is worth less than nothing when one of them
    is broken too, because the tick then raises instead of reporting the reason
    that outranks every other.
    """

    harness = _Harness(tmp_path, intent=_Intent(unreadable=True))
    harness.runtime_port = _ExplodingRuntime()

    result = harness.tick()

    assert result.decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert "control-plane store" in result.decision.reason
    assert result.decision.trading_day is None
    assert harness.executor.calls == []
    assert harness.schedule_port.reads == 0
    assert harness.codes() == ["AUTONOMY_TICK_BLOCKED"]


def test_an_unreadable_intent_outranks_an_unreadable_ledger(
    tmp_path: pathlib.Path,
) -> None:
    """Both broken, and the operator is told about the authorisation first."""

    harness = _Harness(tmp_path, intent=_Intent(unreadable=True))
    _corrupt_ledger(harness.ledger.path)

    result = harness.tick()

    assert result.decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert "control-plane store" in result.decision.reason


def test_an_unreadable_action_ledger_blocks(tmp_path: pathlib.Path) -> None:
    """The ledger answers "has this already happened", and it has no safe default.

    An unreadable ledger is not an empty one: treating it as empty would let the
    supervisor ask for a second launch on the strength of a failed read.
    """

    harness = _Harness(tmp_path)
    _corrupt_ledger(harness.ledger.path)

    result = harness.tick()

    assert result.decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert "action ledger" in result.decision.reason
    assert result.claimed is False
    assert harness.executor.calls == []
    assert harness.codes() == ["AUTONOMY_TICK_BLOCKED"]


def test_an_unreadable_ledger_blocks_even_when_only_the_day_query_fails(
    tmp_path: pathlib.Path,
) -> None:
    """Same conclusion from the other read, because it is the same question."""

    harness = _Harness(tmp_path, runtime=_runtime_facts(preparation_ready=True))
    _corrupt_ledger(harness.ledger.path)

    result = harness.tick()

    assert result.decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert result.claimed is False
    assert harness.executor.calls == []


# =====================================================================
# What may and may not be emitted
# =====================================================================


def test_only_a_block_or_a_request_is_reported(tmp_path: pathlib.Path) -> None:
    """Signal, not noise.

    A tick every thirty seconds is 2880 ticks a day; a scheduler that reported
    each healthy no-op would bury its own warnings, and the modes below are all
    healthy ones.
    """

    quiet = _Harness(tmp_path, intent=_Intent(PaperAutonomyMode.DISABLED))
    assert quiet.tick().decision.action is PaperAutonomyAction.NOOP
    assert quiet.events == []

    # Already running under this intent.
    running = _Harness(
        tmp_path / "running",
        runtime=_runtime_facts(
            session_running=True,
            session_provenance=PaperAutonomySessionProvenance.AUTONOMOUS,
        ),
    )
    assert running.tick().decision.action is PaperAutonomyAction.NOOP
    assert running.events == []

    # A manual session, which autonomy must never touch.
    manual = _Harness(
        tmp_path / "manual",
        runtime=_runtime_facts(
            session_running=True,
            session_provenance=PaperAutonomySessionProvenance.MANUAL,
        ),
    )
    assert manual.tick().decision.action is PaperAutonomyAction.NOOP
    assert manual.events == []

    # The day's one start already attempted and finished.
    done = _Harness(
        tmp_path / "done", runtime=_runtime_facts(preparation_ready=True)
    )
    first = done.tick()
    done.ledger.complete(
        action_key=first.decision.action_key,
        status=PaperAutonomyActionStatus.SUCCEEDED,
        completed_at=_MOMENT,
        detail="the session ran and ended",
    )
    done.events.clear()
    assert done.tick().decision.action is PaperAutonomyAction.NOOP
    assert done.events == []


def test_a_block_is_reported_exactly_once(tmp_path: pathlib.Path) -> None:
    harness = _Harness(tmp_path, intent=_Intent(unreadable=True))

    harness.tick()
    harness.tick()

    assert harness.codes() == [
        "AUTONOMY_TICK_BLOCKED",
        "AUTONOMY_TICK_BLOCKED",
    ]
    assert [event.severity for event in harness.events] == ["warning", "warning"]


class _Executor:
    """Records every request, and answers however the case needs."""

    def __init__(
        self,
        *,
        accepted: bool = True,
        detail: str = "the owner accepted the request",
        raises: Exception | None = None,
    ) -> None:
        self.accepted = accepted
        self.detail = detail
        self.raises = raises
        self.calls: list[tuple[str, object]] = []
        self.preparations: list[PaperAutonomyPreparationRequest] = []

    def _answer(self, name: str, argument: object = None):
        self.calls.append((name, argument))
        if self.raises is not None:
            raise self.raises
        return PaperAutonomyRequestOutcome(
            accepted=self.accepted, detail=self.detail
        )

    def request_prepare(self, request: PaperAutonomyPreparationRequest):
        self.preparations.append(request)
        return self._answer("request_prepare", request)

    def request_start(self):
        return self._answer("request_start")

    def request_pause(self):
        return self._answer("request_pause")

    def request_resume(self):
        return self._answer("request_resume")

    def request_stop(self):
        return self._answer("request_stop")


# =====================================================================
# Builders
# =====================================================================


def _startup_facts(**overrides: object) -> PaperAutonomyStartupFacts:
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


def _schedule_facts(**overrides: object) -> PaperAutonomyScheduleFacts:
    base: dict = {
        "trading_day": _DAY,
        "session": PaperAutonomySessionWindow.REGULAR,
        "preparation_allowed": True,
        "start_allowed": True,
        "orderly_stop_due": False,
        "exceptional_schedule_uncertain": False,
    }
    return PaperAutonomyScheduleFacts(**{**base, **overrides})


def _runtime_facts(**overrides: object) -> PaperAutonomyRuntimeFacts:
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


class _Harness:
    """Everything a tick needs, with a real ledger over a temp file."""

    def __init__(
        self,
        tmp_path: pathlib.Path,
        *,
        intent: _Intent | None = None,
        runtime: PaperAutonomyRuntimeFacts | None = None,
        schedule: PaperAutonomyScheduleFacts | None = None,
        executor: _Executor | None = None,
        policy: PaperAutonomyPolicy | None = None,
    ) -> None:
        self.ledger = SQLitePaperAutonomyActionRepository(
            tmp_path / "actions.sqlite3"
        )
        self.executor = executor or _Executor()
        self.intent_port = intent or _Intent()
        self.runtime_port = _Runtime(runtime or _runtime_facts())
        self.schedule_port = _Schedule(schedule or _schedule_facts())
        self.startup_port = _Startup()
        self.events: list = []
        self.supervisor = PaperAutonomySupervisor(
            intent=self.intent_port,
            actions=self.ledger,
            runtime_facts=self.runtime_port,
            startup_facts=self.startup_port,
            schedule=self.schedule_port,
            executor=self.executor,
            policy=policy or _policy(),
            clock=lambda: _MOMENT,
            emit=self.events.append,
        )

    def tick(self, now: datetime | None = None):
        return self.supervisor.tick(now=now or _MOMENT)

    def rebuild(
        self,
        *,
        runtime: PaperAutonomyRuntimeFacts | None = None,
        intent: _Intent | None = None,
        schedule: PaperAutonomyScheduleFacts | None = None,
    ) -> None:
        """Start a new supervisor over the same ledger, as a restart does.

        The ledger is the only thing that survives, which is exactly what makes
        this the interesting case: whatever the next process knows about the
        previous one's attempts came out of the store.
        """

        self.runtime_port = _Runtime(runtime or _runtime_facts())
        if intent is not None:
            self.intent_port = intent
        if schedule is not None:
            self.schedule_port = _Schedule(schedule)
        self.supervisor = PaperAutonomySupervisor(
            intent=self.intent_port,
            actions=self.ledger,
            runtime_facts=self.runtime_port,
            startup_facts=self.startup_port,
            schedule=self.schedule_port,
            executor=self.executor,
            policy=_policy(),
            clock=lambda: _MOMENT,
            emit=self.events.append,
        )

    def rows(self):
        return self.ledger.recent(50)

    def codes(self) -> list[str]:
        return [event.code.value for event in self.events]


# =====================================================================
# The quiet tick
# =====================================================================


def test_a_tick_that_decides_nothing_asks_nobody(
    tmp_path: pathlib.Path,
) -> None:
    harness = _Harness(
        tmp_path, intent=_Intent(PaperAutonomyMode.DISABLED)
    )

    result = harness.tick()

    assert result.decision.action is PaperAutonomyAction.NOOP
    assert result.executable is False
    assert result.claimed is False
    assert harness.executor.calls == []
    assert harness.rows() == ()
    # A quiet tick says nothing.  A scheduler that reported every healthy no-op
    # would emit thousands of entries a day and teach its operator to ignore the
    # log, which is the same as having no log.
    assert harness.codes() == []


# =====================================================================
# Claim, ask, record
# =====================================================================


def test_a_prepare_tick_claims_before_it_asks_and_records_a_request(
    tmp_path: pathlib.Path,
) -> None:
    harness = _Harness(tmp_path)

    result = harness.tick()

    assert result.decision.action is PaperAutonomyAction.PREPARE
    assert result.claimed is True
    # Accepted is a *request*, never a success: the canonical publication is the
    # only thing that can move it further, and this layer cannot see one.
    assert result.status is PaperAutonomyActionStatus.REQUESTED
    assert [row.status for row in harness.rows()] == [
        PaperAutonomyActionStatus.REQUESTED
    ]
    assert [name for name, _ in harness.executor.calls] == ["request_prepare"]
    assert harness.codes() == ["AUTONOMY_PREPARE_REQUESTED"]


def test_the_preparation_is_sized_by_the_policy_not_by_a_widget(
    tmp_path: pathlib.Path,
) -> None:
    """The reason ``ExecutionPreparationRequest`` exists.

    The limits come from the deployment's policy, so an unattended preparation is
    not sized by whatever an operator last left in a spin box.
    """

    policy = _policy(candidate_limit=7, requested_capital_limit=Decimal("1234"))
    harness = _Harness(tmp_path, policy=policy)

    harness.tick()

    assert harness.executor.preparations == [
        PaperAutonomyPreparationRequest(
            candidate_limit=7, capital_limit=Decimal("1234")
        )
    ]


def test_an_identical_second_tick_does_not_ask_again(
    tmp_path: pathlib.Path,
) -> None:
    """Sequentially, the *unresolved* clause is what stops the repeat.

    Not the claim: after the first tick the accepted request is a non-terminal
    row, so the next tick blocks on it and asks a human rather than quietly
    re-asking the owner.  That is the stronger property of the two, and it is the
    one an earlier version of this test was passing on while claiming to test the
    claim -- measured, not assumed.
    """

    harness = _Harness(tmp_path)

    first = harness.tick()
    second = harness.tick()

    assert first.claimed is True
    assert second.claimed is False
    assert second.decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert "unresolved" in second.decision.reason
    assert [name for name, _ in harness.executor.calls] == ["request_prepare"]
    assert len(harness.rows()) == 1


def test_two_ticks_racing_ask_the_owner_once(tmp_path: pathlib.Path) -> None:
    """The claim is the *concurrency* carrier, and this is where it works alone.

    Several ticks that each read "nothing outstanding" before any of them claims
    must still produce one request.  The unresolved clause cannot help here --
    none of them has written anything yet -- so the store is the only thing
    deciding, and it decides by the action key.  Asserted as "exactly one call",
    which is deterministic even though the interleaving is not.
    """

    harness = _Harness(tmp_path)
    workers = 4
    barrier = threading.Barrier(workers)

    def attempt(_index: int):
        barrier.wait()
        try:
            return harness.tick()
        except Exception:  # noqa: BLE001 - the loser may fail at any step
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(attempt, range(workers)))

    assert len([row for row in results if row is not None and row.claimed]) == 1
    assert [name for name, _ in harness.executor.calls] == ["request_prepare"]
    assert len(harness.rows()) == 1


def test_an_owner_refusal_is_terminal_and_recorded_as_a_refusal(
    tmp_path: pathlib.Path,
) -> None:
    harness = _Harness(
        tmp_path, executor=_Executor(accepted=False, detail="no universe")
    )

    result = harness.tick()

    assert result.status is PaperAutonomyActionStatus.REFUSED
    assert [row.status for row in harness.rows()] == [
        PaperAutonomyActionStatus.REFUSED
    ]
    assert harness.ledger.unresolved() == ()
    assert harness.codes() == ["AUTONOMY_ACTION_REFUSED"]


def test_an_owner_exception_leaves_the_claim_unresolved(
    tmp_path: pathlib.Path,
) -> None:
    """A call that raised proves nothing about whether the effect happened.

    This is the semantics the whole crash story rests on.  A production bridge
    calling ``PaperOrchestrator.start(AUTONOMOUS)`` can have changed the workflow,
    emitted a signal and begun connecting a broker before something later in the
    same call fails; a terminal ``FAILED`` would then say "it is known not to have
    happened" about a session that may be starting.  So the claim is left exactly
    as it is -- ``CLAIMED``, unresolved -- and the next tick blocks on it.
    """

    harness = _Harness(tmp_path)
    harness.executor.raises = RuntimeError("the channel died")

    result = harness.tick()

    assert result.status is PaperAutonomyActionStatus.CLAIMED
    assert result.outcome is None
    rows = harness.rows()
    assert [row.status for row in rows] == [PaperAutonomyActionStatus.CLAIMED]
    assert rows[0].is_terminal is False
    assert rows[0].completed_at is None
    assert [row.action_key for row in harness.ledger.unresolved()] == [
        rows[0].action_key
    ]
    assert harness.codes() == ["AUTONOMY_ACTION_OUTCOME_UNKNOWN"]

    # The exception's own text never reaches the durable audit, and no retry is
    # attempted: the next tick blocks and asks a human.
    assert "channel died" not in rows[0].detail
    assert "channel died" not in harness.events[-1].detail

    second = harness.tick()
    assert second.decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert second.claimed is False
    assert [name for name, _ in harness.executor.calls] == ["request_prepare"]

    # And a restart reaches the same conclusion from the ledger alone.
    harness.executor.raises = None
    harness.rebuild()
    third = harness.tick()
    assert third.decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert [name for name, _ in harness.executor.calls] == ["request_prepare"]


def test_a_start_that_raised_is_never_replayed(tmp_path: pathlib.Path) -> None:
    """The highest-risk action, on its own.

    A launch whose request raised is the case where "retry once, it is probably
    fine" is most tempting and most dangerous: the broker may already be
    connecting, and a second attempt would race the first.
    """

    harness = _Harness(
        tmp_path, runtime=_runtime_facts(preparation_ready=True)
    )
    harness.executor.raises = RuntimeError("connect failed late")

    first = harness.tick()
    assert first.decision.action is PaperAutonomyAction.START
    assert first.status is PaperAutonomyActionStatus.CLAIMED

    restart = _Harness(tmp_path, runtime=_runtime_facts(preparation_ready=True))
    restart.executor.raises = None
    second = restart.tick()

    assert second.decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert second.claimed is False
    assert restart.executor.calls == []
    assert len(restart.rows()) == 1
    assert restart.rows()[0].status is PaperAutonomyActionStatus.CLAIMED


def test_an_accepted_start_is_never_recorded_as_a_success(
    tmp_path: pathlib.Path,
) -> None:
    """Asking an owner to start is not starting.

    The ledger can only reach ``SUCCEEDED`` from ``REQUESTED`` and only through a
    completion the canonical owner published, so a tick that returns has to stop
    at ``REQUESTED`` -- and the action key for the day then counts as attempted,
    which is what stops a second launch.
    """

    harness = _Harness(
        tmp_path, runtime=_runtime_facts(preparation_ready=True)
    )

    result = harness.tick()

    assert result.decision.action is PaperAutonomyAction.START
    assert result.status is PaperAutonomyActionStatus.REQUESTED
    assert all(
        row.status is not PaperAutonomyActionStatus.SUCCEEDED
        for row in harness.rows()
    )
    assert harness.ledger.start_attempted(_DAY) is True


def test_a_started_day_is_not_started_twice(tmp_path: pathlib.Path) -> None:
    """The session ended; the day did not.

    The first launch is recorded as terminal before the second tick, because an
    *open* request is a different state with a higher-priority answer: a
    ``REQUESTED`` start whose outcome nobody has observed blocks and asks a human
    (asserted separately).  What this test pins is the day rule -- one unattended
    start per trading day, whatever became of it.
    """

    harness = _Harness(
        tmp_path, runtime=_runtime_facts(preparation_ready=True)
    )
    first = harness.tick()
    assert first.decision.action is PaperAutonomyAction.START

    harness.ledger.complete(
        action_key=first.decision.action_key,
        status=PaperAutonomyActionStatus.FAILED,
        completed_at=_MOMENT,
        detail="the launch failed after the request was accepted",
    )

    second = harness.tick()

    assert second.decision.action is PaperAutonomyAction.NOOP
    assert "already been attempted" in second.decision.reason
    assert second.claimed is False
    assert [name for name, _ in harness.executor.calls] == ["request_start"]


# =====================================================================
# Fail closed
# =====================================================================


def test_an_unreadable_control_plane_asks_nobody(tmp_path: pathlib.Path) -> None:
    harness = _Harness(tmp_path, intent=_Intent(unreadable=True))

    result = harness.tick()

    assert result.decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert result.claimed is False
    assert harness.executor.calls == []
    assert harness.rows() == ()
    assert harness.codes() == ["AUTONOMY_TICK_BLOCKED"]


def test_an_unresolved_action_blocks_and_is_reported_as_recovery(
    tmp_path: pathlib.Path,
) -> None:
    """A previous attempt whose outcome is unknown is a question, not a retry.

    ``CLAIMED`` with no outcome is exactly what a process that died between
    claiming and recording leaves behind, so it is reproduced by writing that row
    rather than by killing anything.  A terminal outcome would be a different
    state -- the supervisor knows what happened -- and the difference is the whole
    reason the ledger has a non-terminal status at all.
    """

    harness = _Harness(tmp_path, runtime=_runtime_facts(preparation_ready=True))
    key = action_key_for(
        trading_day=_DAY,
        intent_revision=4,
        action=PaperAutonomyActionType.START,
        slot=PaperAutonomyActionSlot.SESSION,
    )
    assert harness.ledger.claim(
        PaperAutonomyActionRecord(
            action_key=key,
            intent_revision=4,
            trading_day=_DAY,
            action=PaperAutonomyActionType.START,
            status=PaperAutonomyActionStatus.CLAIMED,
            claimed_at=_MOMENT,
            completed_at=None,
            detail="a previous process claimed this and never came back",
        )
    )

    result = harness.tick()

    assert result.decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert result.claimed is False
    assert harness.executor.calls == []
    assert harness.codes() == ["AUTONOMY_RECOVERY_REQUIRED"]


def test_a_manual_recovery_is_reported_as_recovery(
    tmp_path: pathlib.Path,
) -> None:
    harness = _Harness(
        tmp_path, runtime=_runtime_facts(manual_recovery_required=True)
    )

    result = harness.tick()

    assert result.decision.action is PaperAutonomyAction.BLOCKED_REQUIRES_OPERATOR
    assert harness.executor.calls == []
    assert harness.codes() == ["AUTONOMY_RECOVERY_REQUIRED"]


def test_shutdown_never_asks_for_new_work(tmp_path: pathlib.Path) -> None:
    """Close admission, then ask nothing -- whatever the session is doing."""

    cases = (
        _runtime_facts(shutting_down=True),
        _runtime_facts(
            shutting_down=True,
            session_paused=True,
            session_provenance=PaperAutonomySessionProvenance.AUTONOMOUS,
        ),
        _runtime_facts(shutting_down=True, preparation_ready=True),
    )
    for index, facts in enumerate(cases):
        harness = _Harness(tmp_path / f"case{index}", runtime=facts)
        result = harness.tick()
        assert result.claimed is False, facts
        assert harness.executor.calls == [], facts


# =====================================================================
# What the supervisor may and may not reach
# =====================================================================


def test_a_kill_switch_stops_its_own_session_through_the_executor(
    tmp_path: pathlib.Path,
) -> None:
    """The canonical intent for a latched kill switch is DISABLED plus the latch.

    Not ``ENABLED`` plus the latch: the value type refuses that outright, because
    clearing a latch must never be able to resume anything.
    """

    harness = _Harness(
        tmp_path,
        intent=_Intent(PaperAutonomyMode.DISABLED, latched=True),
        runtime=_runtime_facts(
            session_running=True,
            session_provenance=PaperAutonomySessionProvenance.AUTONOMOUS,
        ),
    )

    result = harness.tick()

    assert result.decision.action is PaperAutonomyAction.STOP
    assert [name for name, _ in harness.executor.calls] == ["request_stop"]
    assert harness.codes() == ["AUTONOMY_STOP_REQUESTED"]


def test_a_kill_switch_does_not_touch_a_manual_session(
    tmp_path: pathlib.Path,
) -> None:
    harness = _Harness(
        tmp_path,
        intent=_Intent(PaperAutonomyMode.DISABLED, latched=True),
        runtime=_runtime_facts(
            session_running=True,
            session_provenance=PaperAutonomySessionProvenance.MANUAL,
        ),
    )

    result = harness.tick()

    assert result.decision.action is PaperAutonomyAction.NOOP
    assert harness.executor.calls == []
    assert "manual" in result.decision.reason


def test_an_uncertain_calendar_closes_entries(tmp_path: pathlib.Path) -> None:
    harness = _Harness(
        tmp_path,
        runtime=_runtime_facts(
            session_running=True,
            session_provenance=PaperAutonomySessionProvenance.AUTONOMOUS,
        ),
        schedule=_schedule_facts(
            exceptional_schedule_uncertain=True,
            preparation_allowed=False,
            start_allowed=False,
        ),
    )

    result = harness.tick()

    assert result.decision.action is PaperAutonomyAction.PAUSE_ENTRIES
    assert [name for name, _ in harness.executor.calls] == ["request_pause"]


def test_the_supervisor_only_ever_calls_one_executor_request_per_tick(
    tmp_path: pathlib.Path,
) -> None:
    """One decision, one request.  A tick that asked twice would be two actions."""

    harness = _Harness(tmp_path)
    for _ in range(5):
        harness.tick()
    assert len(harness.executor.calls) == 1


def test_the_startup_classification_is_read_once(tmp_path: pathlib.Path) -> None:
    """It describes this process's beginning, so it is asked once by design.

    Re-reading it every tick would answer a different question -- "is the world
    safe now" -- which is what the runtime and schedule facts are for.
    """

    harness = _Harness(tmp_path)
    for _ in range(3):
        harness.tick()
    assert harness.startup_port.reads == 1
    # And the live facts are re-read every tick, which is the other half.
    assert harness.schedule_port.reads == 3
