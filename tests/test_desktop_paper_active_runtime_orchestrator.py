"""Behavioural tests for the active-session half of ``PaperOrchestrator``.

The v2O-E2 counterpart of ``test_desktop_paper_launch_orchestrator.py``: no Qt
application, no broker, no real workflow.  What is asserted here is the *sequencing*
this round moved off ``MainWindow`` -- which phase may consume the market, when the
watchdog poll is suppressed, what each entry control does, and above all that every
operation publishes through one result path exactly once.

Four properties carry the round, and each has tests that would fail on a plausible
wrong implementation rather than only on a missing one:

* **the phase gate is the only thing that decides ingress.**  ``RUNNING``, ``PAUSED``
  and ``STOPPING`` are fed; every other phase is a no-op, and the test asserts the
  workflow was not even *reached* rather than merely that nothing was rendered;
* **a halt is sticky.**  After a tick halts the session, the next tick and the next
  timer fire must not enter the coordinator, and nothing here may "repair" the phase.
  That is the regression this round exists to protect: a second answer to "does this
  session want the market?" is how a halted session gets re-entered and re-trades;
* **the poll suppression is measured from the last ingress, with a strict boundary**:
  ``1.199s`` is suppressed, ``1.200s`` is not.  The clock is injected, so the boundary
  is asserted rather than slept through;
* **one publication path.**  Every operation ends in ``_publish_result``, so a result is
  handed out once and each of its events is requested once -- no operation may emit a
  result or an event itself.

A "refused" operation here is modelled as the real controller refuses it: by raising
``WorkflowStateError``.  The fakes record the *attempt* before raising, so "the
orchestrator never reached the workflow" and "the workflow refused it" stay
distinguishable -- a distinction the logs alone cannot make.
"""

from __future__ import annotations

import pytest

from us_quant.desktop_v2.orchestration.paper import models as messages
from us_quant.desktop_v2.orchestration.paper.models import (
    PAPER_EXECUTION_COMPONENT,
    PAUSE_SUCCEEDED_MESSAGE,
    RESUME_SUCCEEDED_MESSAGE,
)
from us_quant.desktop_v2.orchestration.paper.orchestrator import (
    STREAM_INGRESS_SUPPRESSION_SECONDS,
    PaperOrchestrator,
)
from us_quant.trading.runtime.workflow_state import (
    PaperWorkflowPhase,
    WorkflowStateError,
)


# -- fakes ---------------------------------------------------------------


#: The phases that own the run loop, spelled out here rather than imported: an
#: assertion that reuses the implementation's own set would pass on a wrong set.
LIVE_PHASES = (
    PaperWorkflowPhase.RUNNING,
    PaperWorkflowPhase.PAUSED,
    PaperWorkflowPhase.STOPPING,
)

#: Every other phase, each of which must be a no-op for ingress and for the poll.
DEAD_PHASES = tuple(
    phase for phase in PaperWorkflowPhase if phase not in LIVE_PHASES
)


class _Snapshot:
    """One engine snapshot, with the three facts the interlock reads."""

    def __init__(
        self,
        *,
        active: bool = True,
        positions: tuple[str, ...] = (),
        pending_orders: tuple[str, ...] = (),
    ) -> None:
        self.active = active
        self.positions = positions
        self.pending_orders = pending_orders
        self.session_id = "session-1"


class _State:
    def __init__(self, *, halted: bool = False, finalized: bool = False) -> None:
        self.halted = halted
        self.finalized = finalized


class _Event:
    def __init__(self, code: str, *, severity: str = "info") -> None:
        self.code = code
        self.severity = severity
        self.message = f"{code} happened"


class _Result:
    """A stand-in ``PaperSessionResult``."""

    def __init__(
        self,
        *,
        active: bool = True,
        halted: bool = False,
        finalized: bool = False,
        positions: tuple[str, ...] = (),
        pending_orders: tuple[str, ...] = (),
        events: tuple[_Event, ...] = (),
    ) -> None:
        self.state = _State(halted=halted, finalized=finalized)
        self.engine_snapshot = _Snapshot(
            active=active, positions=positions, pending_orders=pending_orders
        )
        self.events = events
        self.health = None


class _Workflow:
    """A faithful ``PaperWorkflowController`` for the active-session surface.

    It mirrors the real controller where the orchestrator can observe it: the phase
    transitions each operation performs, and the refusals.  Every entry point records
    the **attempt** first, so a test can tell "the capability never called" from "the
    capability called and was refused".
    """

    def __init__(
        self,
        *,
        phase: PaperWorkflowPhase = PaperWorkflowPhase.RUNNING,
        result: _Result | None = None,
        raise_on: str | None = None,
    ) -> None:
        self._phase = phase
        self._result = result if result is not None else _Result()
        self.attempts: list[str] = []
        self.ingress: list[object] = []
        self.polls = 0
        self.pauses: list[bool] = []
        self.stop_snapshots: list[object] = []
        self.raise_on: str | None = raise_on
        self.halt_on_stream = False
        self.halt_on_pause = False
        # A canned result, so a test can hand the capability a result carrying events.
        self.canned_result = result

    @property
    def phase(self) -> PaperWorkflowPhase:
        return self._phase

    @property
    def result(self) -> _Result | None:
        return self._result

    def _refuse(self, operation: str) -> None:
        if self.raise_on == operation:
            raise WorkflowStateError(f"{operation} refused by the workflow.")
        if self._phase not in LIVE_PHASES:
            raise WorkflowStateError("No active Paper runtime is available.")

    def _publish(self, *, halted: bool = False, stop_requested: bool = False) -> _Result:
        result = (
            self.canned_result
            if self.canned_result is not None
            else _Result(halted=halted)
        )
        if stop_requested:
            result.state.stop_requested = True
        self._result = result
        if halted:
            self._phase = PaperWorkflowPhase.HALTED
        return result

    def on_stream(self, snapshot: object) -> _Result:
        self.attempts.append("on_stream")
        self._refuse("on_stream")
        self.ingress.append(snapshot)
        return self._publish(halted=self.halt_on_stream)

    def poll(self) -> _Result:
        self.attempts.append("poll")
        self._refuse("poll")
        self.polls += 1
        return self._publish()

    def set_entries_paused(self, paused: bool) -> _Result:
        self.attempts.append("set_entries_paused")
        if self.raise_on == "set_entries_paused":
            raise WorkflowStateError("set_entries_paused refused by the workflow.")
        if paused and self._phase not in {
            PaperWorkflowPhase.RUNNING,
            PaperWorkflowPhase.PAUSED,
        }:
            raise WorkflowStateError(
                "Entries can only be paused for an active Paper session."
            )
        if not paused and self._phase is not PaperWorkflowPhase.PAUSED:
            raise WorkflowStateError("Entries can only resume from PAUSED.")
        self.pauses.append(paused)
        self._phase = (
            PaperWorkflowPhase.PAUSED if paused else PaperWorkflowPhase.RUNNING
        )
        return self._publish(halted=self.halt_on_pause)

    def request_stop(self, stream_snapshot: object | None = None) -> _Result:
        self.attempts.append("request_stop")
        if self.raise_on == "request_stop":
            raise WorkflowStateError("request_stop refused by the workflow.")
        if self._phase not in {
            PaperWorkflowPhase.RUNNING,
            PaperWorkflowPhase.PAUSED,
        }:
            raise WorkflowStateError(
                "A Paper stop requires a RUNNING or PAUSED session."
            )
        self.stop_snapshots.append(stream_snapshot)
        self._phase = PaperWorkflowPhase.STOPPING
        return self._publish(stop_requested=True)


class _Events:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.results: list[object] = []
        self.runtime_events: list[object] = []


class _Clock:
    """A deterministic monotonic clock, advanced by hand."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Harness:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


def _build(
    *,
    workflow: _Workflow | None = None,
    market_snapshot: object | None = "market-snapshot",
    finalization_inflight: list[bool] | None = None,
    clock: _Clock | None = None,
) -> _Harness:
    """Assemble an orchestrator over the active-session fakes.

    ``finalization_inflight`` is a *list* so a test can flip the seam mid-flight the
    way the window's real flag moves when the zero-state proof starts and ends.
    """

    workflow = workflow or _Workflow()
    events = _Events()
    render_snapshots: list[object] = []
    inflight = finalization_inflight if finalization_inflight is not None else [False]
    clock = clock or _Clock()
    # A one-element list, so a test can replace the market fact between two calls and
    # assert the stop was judged against the newer one.
    market = [market_snapshot]

    orchestrator = PaperOrchestrator(
        workflow_getter=lambda: workflow,
        paper_trading_getter=lambda: None,
        build_session=lambda *args, **kwargs: None,
        submit_task=lambda *args, **kwargs: False,
        health_evaluator="health",
        preflight_provider=lambda: None,
        strategy_provider=lambda: None,
        candidates_provider=lambda: (),
        capital_limit_provider=lambda: 0,
        order_channel_provider=lambda: None,
        shadow_is_active=lambda: False,
        market_snapshot_provider=lambda: market[0],
        finalization_inflight_provider=lambda: inflight[0],
        clear_arm_confirmation=lambda: None,
        render_launch_state=lambda: None,
        render_launch_context=lambda summary: render_snapshots.append(summary),
        clock=clock,
    )
    orchestrator.log_requested.connect(events.logs.append)
    orchestrator.result_changed.connect(events.results.append)
    orchestrator.runtime_event_requested.connect(events.runtime_events.append)
    return _Harness(
        orchestrator=orchestrator,
        workflow=workflow,
        events=events,
        clock=clock,
        inflight=inflight,
        market=market,
    )


# -- the phase gate ------------------------------------------------------


@pytest.mark.parametrize("phase", LIVE_PHASES)
def test_ingress_feeds_a_live_session(phase: PaperWorkflowPhase) -> None:
    harness = _build(workflow=_Workflow(phase=phase))
    snapshot = object()

    harness.orchestrator.on_market_snapshot(snapshot)

    assert harness.workflow.ingress == [snapshot]
    assert len(harness.events.results) == 1


@pytest.mark.parametrize("phase", DEAD_PHASES)
def test_ingress_never_reaches_the_workflow_outside_the_live_phases(
    phase: PaperWorkflowPhase,
) -> None:
    """The gate is *before* the call, so the workflow is not even asked.

    Asserted on the recorded attempt rather than on the absence of a result: an
    implementation that called the coordinator and swallowed its refusal would leave
    the phase alone too, and would still be the wrong implementation.
    """

    harness = _build(workflow=_Workflow(phase=phase))

    harness.orchestrator.on_market_snapshot(object())

    assert harness.workflow.attempts == []
    assert harness.events.results == []
    assert harness.events.logs == []


@pytest.mark.parametrize("phase", DEAD_PHASES)
def test_the_poll_never_reaches_the_workflow_outside_the_live_phases(
    phase: PaperWorkflowPhase,
) -> None:
    harness = _build(workflow=_Workflow(phase=phase))
    harness.clock.advance(STREAM_INGRESS_SUPPRESSION_SECONDS)

    harness.orchestrator.poll()

    assert harness.workflow.attempts == []
    assert harness.events.results == []


def test_stopping_still_receives_the_market() -> None:
    """A stopping session is flattening; its exits and health still read the stream."""

    harness = _build(workflow=_Workflow(phase=PaperWorkflowPhase.STOPPING))

    harness.orchestrator.on_market_snapshot(object())

    assert harness.workflow.attempts == ["on_stream"]


# -- the finalization seam ----------------------------------------------


def test_ingress_is_deferred_while_a_finalization_task_runs() -> None:
    """The proof of zero-state reads the same broker; two readers must not interleave."""

    harness = _build(finalization_inflight=[True])

    harness.orchestrator.on_market_snapshot(object())

    assert harness.workflow.attempts == []
    assert harness.events.results == []


def test_the_poll_is_deferred_while_a_finalization_task_runs() -> None:
    harness = _build(finalization_inflight=[True])
    harness.clock.advance(STREAM_INGRESS_SUPPRESSION_SECONDS)

    harness.orchestrator.poll()

    assert harness.workflow.attempts == []


def test_the_deferral_ends_when_the_proof_ends() -> None:
    """The seam is a live question, not a flag captured at construction."""

    harness = _build(finalization_inflight=[True])
    harness.orchestrator.on_market_snapshot(object())

    harness.inflight[0] = False
    harness.orchestrator.on_market_snapshot(object())

    assert harness.workflow.attempts == ["on_stream"]


# -- the poll suppression -----------------------------------------------


def test_a_stream_tick_suppresses_the_next_poll() -> None:
    """The timer must not repeat the watchdog sequence a tick just drove."""

    harness = _build()

    harness.orchestrator.on_market_snapshot(object())
    harness.clock.advance(0.5)
    harness.orchestrator.poll()

    assert harness.workflow.attempts == ["on_stream"]
    assert len(harness.events.results) == 1


def test_the_suppression_boundary_is_strict() -> None:
    """1.199s is suppressed; 1.200s is not."""

    harness = _build()

    harness.orchestrator.on_market_snapshot(object())
    harness.clock.advance(STREAM_INGRESS_SUPPRESSION_SECONDS - 0.001)
    harness.orchestrator.poll()
    assert harness.workflow.attempts == ["on_stream"]

    harness.clock.advance(0.001)
    harness.orchestrator.poll()
    assert harness.workflow.attempts == ["on_stream", "poll"]


def test_the_suppression_is_measured_from_the_last_ingress() -> None:
    """Each new tick restarts the window rather than accumulating one."""

    harness = _build()

    harness.orchestrator.on_market_snapshot(object())
    harness.clock.advance(1.0)
    harness.orchestrator.on_market_snapshot(object())
    harness.clock.advance(1.0)
    harness.orchestrator.poll()

    assert harness.workflow.attempts == ["on_stream", "on_stream"]


def test_a_poll_runs_when_nothing_has_fed_the_session() -> None:
    """The heartbeat exists for the quiet market, so an empty stamp never suppresses."""

    harness = _build()

    harness.orchestrator.poll()

    assert harness.workflow.attempts == ["poll"]


def test_the_ingress_is_stamped_before_the_workflow_is_called() -> None:
    """The stamp means "the stream just did this work", even when the call refuses.

    A refused ingress is not a reason to let the timer repeat the same sequence
    immediately -- the stream is demonstrably alive.
    """

    harness = _build(workflow=_Workflow(raise_on="on_stream"))

    harness.orchestrator.on_market_snapshot(object())
    harness.clock.advance(0.1)
    harness.orchestrator.poll()

    assert harness.events.logs == ["on_stream refused by the workflow."]
    assert harness.workflow.attempts == ["on_stream"]


# -- one call, one publication per operation -----------------------------


def test_a_stream_tick_calls_the_workflow_once_and_publishes_once() -> None:
    harness = _build()

    harness.orchestrator.on_market_snapshot(object())

    assert harness.workflow.attempts == ["on_stream"]
    assert len(harness.events.results) == 1


def test_a_poll_calls_the_workflow_once_and_publishes_once() -> None:
    harness = _build()

    harness.orchestrator.poll()

    assert harness.workflow.attempts == ["poll"]
    assert len(harness.events.results) == 1


def test_each_event_of_a_result_is_requested_exactly_once() -> None:
    """Ownership of the events moved with the publication, and stayed one-per-event."""

    result = _Result(
        events=(
            _Event("STOP_REQUESTED"),
            _Event("PAPER_ENGINE_STOPPED", severity="warning"),
        )
    )
    harness = _build(workflow=_Workflow(result=result))

    harness.orchestrator.poll()

    codes = [event.code for event in harness.events.runtime_events]
    assert codes == ["STOP_REQUESTED", "PAPER_ENGINE_STOPPED"]


def test_the_result_events_are_filed_under_the_session_component() -> None:
    """Distinct from the launch component: the operator reads them as other things."""

    harness = _build(workflow=_Workflow(result=_Result(events=(_Event("X"),))))

    harness.orchestrator.poll()

    assert harness.events.runtime_events[0].component == PAPER_EXECUTION_COMPONENT
    assert PAPER_EXECUTION_COMPONENT != messages.PAPER_LAUNCH_COMPONENT


def test_a_result_without_events_requests_no_event() -> None:
    harness = _build()

    harness.orchestrator.poll()

    assert harness.events.runtime_events == []


# -- the halt is sticky --------------------------------------------------


def test_a_halt_on_a_tick_is_published_once_and_moves_the_phase() -> None:
    workflow = _Workflow()
    workflow.halt_on_stream = True
    harness = _build(workflow=workflow)

    harness.orchestrator.on_market_snapshot(object())

    assert harness.workflow.phase is PaperWorkflowPhase.HALTED
    assert len(harness.events.results) == 1
    assert harness.events.results[0].state.halted is True


def test_a_halted_session_is_never_re_entered_by_the_next_tick() -> None:
    """The regression this round exists to protect.

    The tick that halts the session is real; every tick after it must find the phase
    gate shut.  Asserted through the workflow's recorded attempts, because a session
    that re-entered the coordinator would have re-evaluated the strategy and could
    submit again.
    """

    workflow = _Workflow()
    workflow.halt_on_stream = True
    harness = _build(workflow=workflow)

    harness.orchestrator.on_market_snapshot(object())
    harness.orchestrator.on_market_snapshot(object())
    harness.orchestrator.on_market_snapshot(object())

    assert workflow.attempts == ["on_stream"]
    assert len(harness.events.results) == 1


def test_a_halted_session_is_never_re_entered_by_the_timer() -> None:
    workflow = _Workflow()
    workflow.halt_on_stream = True
    harness = _build(workflow=workflow)

    harness.orchestrator.on_market_snapshot(object())
    harness.clock.advance(10.0)
    harness.orchestrator.poll()
    harness.clock.advance(10.0)
    harness.orchestrator.poll()

    assert workflow.attempts == ["on_stream"]


def test_nothing_here_repairs_a_halted_session() -> None:
    """Recovery is v2O-E3: the capability may not move the phase by hand.

    Neither entry control is a repair route, and a refused one must not fabricate a
    result that says otherwise.
    """

    harness = _build(workflow=_Workflow(phase=PaperWorkflowPhase.HALTED))

    harness.orchestrator.pause()
    harness.orchestrator.resume()
    harness.orchestrator.stop()

    assert harness.workflow.pauses == []
    assert harness.workflow.stop_snapshots == []
    assert harness.events.results == []
    assert harness.workflow.phase is PaperWorkflowPhase.HALTED


# -- pause and resume ----------------------------------------------------


def test_pause_succeeds_and_logs_the_operator_sentence() -> None:
    harness = _build(workflow=_Workflow(phase=PaperWorkflowPhase.RUNNING))

    harness.orchestrator.pause()

    assert harness.workflow.pauses == [True]
    assert harness.workflow.phase is PaperWorkflowPhase.PAUSED
    assert len(harness.events.results) == 1
    assert harness.events.logs == [PAUSE_SUCCEEDED_MESSAGE]


def test_pause_again_from_the_paused_phase_is_accepted() -> None:
    """The controller allows pausing a paused session; so does the capability."""

    harness = _build(workflow=_Workflow(phase=PaperWorkflowPhase.PAUSED))

    harness.orchestrator.pause()

    assert harness.workflow.pauses == [True]


def test_resume_succeeds_and_logs_the_operator_sentence() -> None:
    harness = _build(workflow=_Workflow(phase=PaperWorkflowPhase.PAUSED))

    harness.orchestrator.resume()

    assert harness.workflow.pauses == [False]
    assert harness.workflow.phase is PaperWorkflowPhase.RUNNING
    assert len(harness.events.results) == 1
    assert harness.events.logs == [RESUME_SUCCEEDED_MESSAGE]


def test_resume_is_refused_unless_paused() -> None:
    harness = _build(workflow=_Workflow(phase=PaperWorkflowPhase.RUNNING))

    harness.orchestrator.resume()

    assert harness.workflow.pauses == []
    assert harness.events.results == []
    assert harness.events.logs == ["Entries can only resume from PAUSED."]


def test_pause_that_halts_the_session_publishes_the_halt_and_still_confirms() -> None:
    """The controller can halt on an entry control; its result is not swallowed.

    Both halves of the retired behaviour are preserved deliberately.  The halt result is
    published like any other (so the operator sees the halt event and the phase moves in
    the panel), and the confirmation sentence is still logged -- the retired handler
    logged it unconditionally after the call returned, and "the workflow accepted the
    pause" is what that line has always meant.  Changing it here would be a behaviour
    change this round did not ask for, so the test pins the existing semantics rather
    than an opinion about them.
    """

    workflow = _Workflow(phase=PaperWorkflowPhase.RUNNING)
    workflow.halt_on_pause = True
    harness = _build(workflow=workflow)

    harness.orchestrator.pause()

    assert len(harness.events.results) == 1
    assert harness.events.results[0].state.halted is True
    assert harness.events.logs == [PAUSE_SUCCEEDED_MESSAGE]
    assert harness.workflow.phase is PaperWorkflowPhase.HALTED


@pytest.mark.parametrize(
    "phase",
    (
        PaperWorkflowPhase.IDLE,
        PaperWorkflowPhase.PREPARING,
        PaperWorkflowPhase.READY,
        PaperWorkflowPhase.CONNECTING,
        PaperWorkflowPhase.HALTED,
        PaperWorkflowPhase.RECONCILING,
        PaperWorkflowPhase.RECONCILING_READY,
        PaperWorkflowPhase.FINALIZED,
    ),
)
def test_pause_is_refused_outside_a_live_session(phase: PaperWorkflowPhase) -> None:
    """Log only: no dialog, no fabricated result, no attempt to repair the phase."""

    harness = _build(workflow=_Workflow(phase=phase))

    harness.orchestrator.pause()

    assert harness.workflow.pauses == []
    assert harness.events.results == []
    assert harness.events.logs == [
        "Entries can only be paused for an active Paper session."
    ]
    assert harness.workflow.phase is phase


# -- the orderly stop ----------------------------------------------------


def test_stop_reads_the_market_fact_of_this_moment() -> None:
    """The snapshot arrives from the provider at call time, not at construction."""

    harness = _build(market_snapshot="first")

    harness.orchestrator.stop()

    assert harness.workflow.stop_snapshots == ["first"]


def test_stop_uses_the_latest_snapshot_when_it_changes() -> None:
    """A frozen snapshot would silently age; the provider is asked every time."""

    harness = _build(market_snapshot="first")

    harness.orchestrator.stop()
    harness.market[0] = "second"
    harness.workflow._phase = PaperWorkflowPhase.PAUSED
    harness.orchestrator.stop()

    assert harness.workflow.stop_snapshots == ["first", "second"]


def test_stop_publishes_its_result_once() -> None:
    harness = _build()

    harness.orchestrator.stop()

    assert harness.workflow.phase is PaperWorkflowPhase.STOPPING
    assert len(harness.events.results) == 1


@pytest.mark.parametrize("phase", LIVE_PHASES[:2])
def test_stop_succeeds_from_running_and_paused(phase: PaperWorkflowPhase) -> None:
    harness = _build(workflow=_Workflow(phase=phase))

    harness.orchestrator.stop()

    assert len(harness.workflow.stop_snapshots) == 1


@pytest.mark.parametrize(
    "phase",
    (
        PaperWorkflowPhase.IDLE,
        PaperWorkflowPhase.READY,
        PaperWorkflowPhase.CONNECTING,
        PaperWorkflowPhase.HALTED,
        PaperWorkflowPhase.STOPPING,
        PaperWorkflowPhase.RECONCILING,
        PaperWorkflowPhase.RECONCILING_READY,
        PaperWorkflowPhase.FINALIZED,
    ),
)
def test_stop_is_refused_by_the_controller_outside_a_live_session(
    phase: PaperWorkflowPhase,
) -> None:
    """The refusal is the controller's, and it is reported rather than repaired."""

    harness = _build(workflow=_Workflow(phase=phase))

    harness.orchestrator.stop()

    assert harness.workflow.stop_snapshots == []
    assert harness.events.results == []
    assert harness.events.logs == [
        "A Paper stop requires a RUNNING or PAUSED session."
    ]


# -- delegated queries ---------------------------------------------------


def test_runtime_active_reads_the_canonical_result() -> None:
    harness = _build()

    assert harness.orchestrator.runtime_active is True

    harness.workflow._result = _Result(active=False)
    assert harness.orchestrator.runtime_active is False


def test_runtime_active_is_false_before_a_session_exists() -> None:
    """No result means no session, whatever the phase happens to say."""

    workflow = _Workflow(phase=PaperWorkflowPhase.CONNECTING)
    workflow._result = None
    harness = _build(workflow=workflow)

    assert harness.orchestrator.runtime_active is False


def test_has_runtime_obligations_reads_the_canonical_snapshot() -> None:
    """The three facts an interlock must not let go of."""

    harness = _build()
    harness.workflow._result = _Result(active=True)
    assert harness.orchestrator.has_runtime_obligations is True

    harness.workflow._result = _Result(active=False)
    assert harness.orchestrator.has_runtime_obligations is False

    harness.workflow._result = _Result(active=False, positions=("AAPL",))
    assert harness.orchestrator.has_runtime_obligations is True

    harness.workflow._result = _Result(active=False, pending_orders=("intent-1",))
    assert harness.orchestrator.has_runtime_obligations is True


def test_has_runtime_obligations_is_false_without_a_result() -> None:
    workflow = _Workflow()
    workflow._result = None
    harness = _build(workflow=workflow)

    assert harness.orchestrator.has_runtime_obligations is False


def test_the_delegated_queries_never_cache() -> None:
    """Every read resolves the live workflow, so a replaced controller is seen.

    The window replaces both the workflow and its order-service owner; a cached answer
    would keep describing the session that was replaced.
    """

    live: list[_Workflow] = [_Workflow()]
    events = _Events()
    orchestrator = PaperOrchestrator(
        workflow_getter=lambda: live[0],
        paper_trading_getter=lambda: None,
        build_session=lambda *args, **kwargs: None,
        submit_task=lambda *args, **kwargs: False,
        health_evaluator="health",
        preflight_provider=lambda: None,
        strategy_provider=lambda: None,
        candidates_provider=lambda: (),
        capital_limit_provider=lambda: 0,
        order_channel_provider=lambda: None,
        shadow_is_active=lambda: False,
        market_snapshot_provider=lambda: None,
        finalization_inflight_provider=lambda: False,
        clear_arm_confirmation=lambda: None,
        render_launch_state=lambda: None,
        render_launch_context=lambda summary: None,
    )
    orchestrator.result_changed.connect(events.results.append)

    assert orchestrator.runtime_active is True

    replacement = _Workflow()
    replacement._result = _Result(active=False)
    live[0] = replacement

    assert orchestrator.runtime_active is False
    assert orchestrator.result is replacement.result
