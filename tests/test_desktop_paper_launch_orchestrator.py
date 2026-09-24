"""Behavioural tests for ``PaperOrchestrator``, driven entirely by fakes.

No Qt application, no broker, no real workflow and no real runtime: the workflow,
the order-service owner, the task submitter, the session-build seam and every fact
provider are replaced, so each test asserts the *sequencing* this class owns -- which
collaborator it calls, in which order, and what it publishes -- rather than the
workflow's own transition legality (``test_runtime_workflow.py``) or the service's
candidate lifecycle (``test_paper_trading_service.py``).

The behaviours pinned here are the ones the extraction could plausibly break, and
they are the safety properties the round exists to preserve:

* a duplicate start is refused before anything is built, and never creates a second
  candidate or a second broker task;
* a start refused by the preflight, by Shadow, or by task admission leaves no lease
  held and no candidate behind;
* a late callback for a superseded attempt disposes **only** its own candidate and
  leaves the newer attempt's plan, lease and active service untouched;
* the second preflight and the frozen-identity revalidation both still refuse;
* every broker gate still refuses, with ``Decimal`` throughout;
* the arm/publish/promote order is fixed, with ``ensure_candidate_can_promote``
  before publication and promotion last;
* each failure path is checked against *state* -- phase, lease, candidate ownership,
  active ownership and the operator-visible outcome -- not merely a dialog.

A "lease" here is modelled as the real one is: shared, and held by whoever took it.
That is what makes the duplicate-start and stale-callback assertions meaningful.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from us_quant.auto_launch import AutoLaunchPlan, build_auto_launch_plan
from us_quant.desktop_v2.orchestration.paper import models as messages
from us_quant.desktop_v2.orchestration.paper import orchestrator as module
from us_quant.desktop_v2.orchestration.paper import queries
from us_quant.desktop_v2.orchestration.paper.models import (
    ARMING_FAILED_MESSAGE,
    CONNECT_FAILED_MESSAGE,
    IDENTITY_CHANGED_MESSAGE,
    PAPER_PROMOTION_INVARIANT_CODE,
    PREFLIGHT_CHANGED_MESSAGE,
    STALE_PLAN_MESSAGE,
    PaperAccountReading,
    PaperLaunchIntegrityError,
    PaperLaunchPublication,
    PaperLaunchRequest,
    PaperOrderChannel,
    PaperSessionBuildResult,
)
from us_quant.desktop_v2.orchestration.paper.orchestrator import (
    PaperOrchestrator,
)
from us_quant.paper_order_models import PaperBrokerPosition, PaperBrokerState
from us_quant.trading.domain.strategy import parameter_hash_for
from us_quant.trading.runtime.workflow_state import (
    PaperWorkflowPhase,
    WorkflowStateError,
)


# -- fakes ---------------------------------------------------------------


class _Lease:
    """The shared execution lease, modelled as the real one behaves.

    ``active`` is true whenever *anyone* holds it -- Shadow included -- which is
    exactly why a capability must never release one it did not take.  ``acquired``
    counts only this workflow's own acquisitions so a test can tell "still held" from
    "never taken".
    """

    def __init__(self) -> None:
        self.active = False
        self.acquired = 0
        self.released = 0

    def acquire_paper(self) -> None:
        if self.active:
            raise WorkflowStateError("Cannot acquire PAPER lease.")
        self.active = True
        self.acquired += 1

    def release_paper(self, *, finalized: bool = False) -> None:
        if not self.active:
            raise WorkflowStateError("Lease is not active.")
        self.active = False
        self.released += 1


class _Workflow:
    """A faithful ``PaperWorkflowController`` for the launch surface.

    Mirrors the real controller's state machine for exactly the transitions this
    round drives, including the property the stale path depends on:
    ``reject_connecting`` returns ``False`` for a plan that is not the active one and
    mutates nothing when it does.
    """

    def __init__(self, *, phase: PaperWorkflowPhase = PaperWorkflowPhase.READY) -> None:
        self._phase = phase
        self._active_plan: AutoLaunchPlan | None = None
        self._result: object | None = None
        self.lease = _Lease()
        self.begin_calls: list[AutoLaunchPlan] = []
        self.reject_calls: list[AutoLaunchPlan] = []
        self.published: list[AutoLaunchPlan] = []
        self.publish_error: Exception | None = None

    @property
    def phase(self) -> PaperWorkflowPhase:
        return self._phase

    @property
    def active_plan(self) -> AutoLaunchPlan | None:
        return self._active_plan

    @property
    def result(self) -> object | None:
        return self._result

    def begin_connecting(self, plan: AutoLaunchPlan) -> None:
        if self._active_plan is not None:
            raise WorkflowStateError("A Paper launch attempt is already active.")
        self._phase = PaperWorkflowPhase.CONNECTING
        self.lease.acquire_paper()
        self._active_plan = plan
        self.begin_calls.append(plan)

    def reject_connecting(self, plan: AutoLaunchPlan) -> bool:
        if (
            self._phase is not PaperWorkflowPhase.CONNECTING
            or plan != self._active_plan
        ):
            return False
        self.lease.release_paper(finalized=True)
        self._active_plan = None
        self._phase = PaperWorkflowPhase.READY
        self.reject_calls.append(plan)
        return True

    def publish_armed(
        self,
        plan: AutoLaunchPlan,
        *,
        engine: object,
        orders: object,
        health_evaluator: object,
        candidate_symbols: frozenset[str],
    ) -> object:
        if self.publish_error is not None:
            raise self.publish_error
        if (
            self._phase is not PaperWorkflowPhase.CONNECTING
            or plan != self._active_plan
        ):
            raise WorkflowStateError("Stale or invalid Paper launch publication.")
        self.published.append(plan)
        # The plan deliberately stays bound after publication: that is the real
        # controller's behaviour and the reason "in flight" must be read off the
        # phase rather than ``active_plan is None``.
        self._phase = PaperWorkflowPhase.RUNNING
        self._result = _Result()
        return self._result


class _Result:
    """Stands in for ``PaperSessionResult``; carries a snapshot identity."""

    class _State:
        finalized = False

    state = _State()

    def __init__(self) -> None:
        self.engine_snapshot = {"session_id": "session-1", "active": True}


class _Candidate:
    """A borrowed candidate: a broker reading and an ``arm``."""

    def __init__(
        self,
        *,
        net_liquidation: Decimal | None = Decimal("25000"),
        cash: Decimal | None = Decimal("25000"),
        positions: tuple[PaperBrokerPosition, ...] = (),
        alias: str = "DU1234567",
        arm_error: Exception | None = None,
    ) -> None:
        self._state = PaperBrokerState(
            account_alias=alias,
            net_liquidation=net_liquidation,
            cash=cash,
            available_funds=None,
            buying_power=None,
            daily_pnl=None,
            unrealized_pnl=None,
            realized_pnl=None,
            positions=positions,
            observed_at="2026-01-01T00:00:00Z",
        )
        self._alias = alias
        self._arm_error = arm_error
        self.arm_calls: list[dict[str, object]] = []

    def connection_snapshot(self) -> object:
        return type("_ConnectionFact", (), {"account_alias": self._alias})()

    def broker_state(self) -> PaperBrokerState:
        return self._state

    def arm(self, **kwargs: object) -> None:
        if self._arm_error is not None:
            raise self._arm_error
        self.arm_calls.append(kwargs)


class _Trading:
    """The candidate lifecycle of the order-service owner, with two slots."""

    def __init__(self, candidates: dict[str, _Candidate] | None = None) -> None:
        self._candidates: dict[str, _Candidate] = dict(candidates or {})
        self._active: _Candidate | None = None
        self.connected: list[str] = []
        self.discarded: list[str] = []
        self.promoted: list[str] = []
        self.connect_error: Exception | None = None
        self.discard_error: Exception | None = None
        self.promote_error: Exception | None = None
        self.would_refuse_promotion = False

    def connect_candidate(self, candidate_id: str, **kwargs: object) -> object:
        if self.connect_error is not None:
            raise self.connect_error
        self._candidates[candidate_id] = self._candidate_for(candidate_id)
        self.connected.append(candidate_id)
        return type(
            "_ConnectionFact", (), {"account_alias": "DU1234567"}
        )()

    def _candidate_for(self, candidate_id: str) -> _Candidate:
        return _Candidate()

    def has_candidate(self, candidate_id: object) -> bool:
        return isinstance(candidate_id, str) and candidate_id in self._candidates

    def candidate_service(self, candidate_id: str) -> _Candidate:
        return self._candidates[candidate_id]

    def ensure_candidate_can_promote(self, candidate_id: str) -> None:
        if self.would_refuse_promotion:
            raise RuntimeError("a Paper order service is already active")
        if candidate_id not in self._candidates:
            raise RuntimeError(f"unknown Paper candidate {candidate_id!r}")

    def promote_candidate(self, candidate_id: str) -> None:
        if self.promote_error is not None:
            raise self.promote_error
        if self._active is not None:
            raise RuntimeError("a Paper order service is already active")
        self._active = self._candidates.pop(candidate_id)
        self.promoted.append(candidate_id)

    def discard_candidate(self, candidate_id: str) -> None:
        self.discarded.append(candidate_id)
        if self.discard_error is not None:
            raise self.discard_error
        self._candidates.pop(candidate_id, None)

    @property
    def active(self) -> _Candidate | None:
        return self._active

    @property
    def candidate_ids(self) -> set[str]:
        return set(self._candidates)


class _Events:
    """Captures every published channel."""

    def __init__(self) -> None:
        self.refusals: list[tuple[str, str]] = []
        self.logs: list[str] = []
        self.publications: list[PaperLaunchPublication] = []
        self.runtime_events: list[object] = []


class _Preflight:
    def __init__(self, *, ready: bool = True, failures: tuple = ()) -> None:
        self.ready = ready
        self.checks = failures


class _Check:
    def __init__(self, name: str, detail: str) -> None:
        self.name = name
        self.detail = detail
        self.passed = False


class _Strategy:
    """A stand-in governed version whose hash really describes its parameters.

    Built through the domain's own ``parameter_hash_for`` rather than with a literal
    string, because ``freeze_launch`` re-computes the hash and refuses a version whose
    declared hash disagrees.  A fake with a made-up hash would be refused by that
    check, which is correct behaviour but not what most of these tests are about.
    """

    def __init__(
        self,
        *,
        version_id: str = "v-1",
        parameters: dict[str, object] | None = None,
        parameter_hash: str | None = None,
    ) -> None:
        self.version_id = version_id
        self.identity = f"identity-for-{version_id}"
        self.parameters: dict[str, object] = (
            {"market_reference_symbols": ["SPY"], "max_position_fraction": "0.05"}
            if parameters is None
            else parameters
        )
        # An explicit hash models a *tampered* catalogue entry; otherwise the hash is
        # derived so the version is internally consistent.
        self.parameter_hash = (
            parameter_hash
            if parameter_hash is not None
            else parameter_hash_for(self.parameters)
        )


class _Row:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol


class _Submitter:
    """Records submissions; ``admitted=False`` models a refused admission."""

    def __init__(self, *, admitted: bool = True) -> None:
        self.admitted = admitted
        self.calls: list[dict[str, object]] = []

    def __call__(self, task, **kwargs):
        self.calls.append({"task": task, **kwargs})
        if not self.admitted:
            return False
        # The real submitter runs the task on a worker and calls ``on_success``
        # with its return value; that callback path is exercised explicitly by
        # ``_finish`` below so a test controls exactly when the callback arrives.
        self.last_task = task
        self.last_on_success = kwargs["on_success"]
        return True

    def work(self):
        """Run the submitted task body, as the worker would."""

        return self.last_task(lambda _message: None)

    def finish(self, result: object) -> None:
        """Deliver a task result, as the worker's ``succeeded`` signal would."""

        self.last_on_success(result)


class _Builder:
    """The injected session-build seam.

    Faithful to the real seam's shape in the way this round made load-bearing: it
    composes and starts the runtime and returns what arming needs, but it does **not**
    arm.  So an arm failure surfaces from the orchestrator's own call, which is
    exactly where production raises it.
    """

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[PaperLaunchRequest, object, PaperAccountReading]] = []

    def __call__(self, request, service, reading) -> PaperSessionBuildResult:
        self.calls.append((request, service, reading))
        if self.error is not None:
            raise self.error
        return PaperSessionBuildResult(
            engine="engine",
            orders=service,
            session_id="session-1",
            candidate_count=len(request.candidates),
            runtime="runtime",
            max_order_notional=Decimal("1000"),
        )


def _build(
    *,
    workflow: _Workflow | None = None,
    trading: _Trading | None = None,
    submitter: _Submitter | None = None,
    builder: _Builder | None = None,
    preflights: list[_Preflight] | None = None,
    strategy: object | None = None,
    candidates: tuple[_Row, ...] | None = None,
    capital_limit: Decimal = Decimal("20000"),
    shadow_active: bool = False,
):
    """Assemble an orchestrator over fakes, returning it with its collaborators.

    ``preflights`` is a queue so a test can make the second pass disagree with the
    first -- which is the whole point of the re-run.
    """

    workflow = workflow or _Workflow()
    trading = trading or _Trading()
    submitter = submitter or _Submitter()
    builder = builder or _Builder()
    queue = list(preflights or [_Preflight()])
    state = {
        "strategy": strategy if strategy is not None else _Strategy(),
        "candidates": candidates if candidates is not None else (_Row("AAPL"),),
        "capital_limit": capital_limit,
    }
    events = _Events()
    renders: list[str | None] = []
    arm_clears: list[int] = []

    def preflight():
        return queue.pop(0) if len(queue) > 1 else queue[0]

    orchestrator = PaperOrchestrator(
        workflow_getter=lambda: workflow,
        paper_trading_getter=lambda: trading,
        build_session=builder,
        submit_task=submitter,
        health_evaluator="health",
        preflight_provider=preflight,
        strategy_provider=lambda: state["strategy"],
        candidates_provider=lambda: state["candidates"],
        capital_limit_provider=lambda: state["capital_limit"],
        order_channel_provider=lambda: PaperOrderChannel(
            config="config", repository="repository", extended_hours_enabled=False
        ),
        shadow_is_active=lambda: shadow_active,
        clear_arm_confirmation=lambda: arm_clears.append(1),
        render_launch_state=lambda: renders.append(None),
        render_launch_context=lambda summary: renders.append(summary),
    )
    orchestrator.refused.connect(
        lambda title, message: events.refusals.append((title, message))
    )
    orchestrator.log_requested.connect(events.logs.append)
    orchestrator.session_published.connect(events.publications.append)
    orchestrator.runtime_event_requested.connect(events.runtime_events.append)
    return (
        type(
            "_Harness",
            (),
            {
                "orchestrator": orchestrator,
                "workflow": workflow,
                "trading": trading,
                "submitter": submitter,
                "builder": builder,
                "events": events,
                "state": state,
                "renders": renders,
                "arm_clears": arm_clears,
                "preflights": queue,
            },
        )()
    )


def _launch(harness) -> object:
    """Start and complete one successful launch, returning the task result."""

    harness.orchestrator.start()
    result = harness.submitter.work()
    harness.submitter.finish(result)
    return result


# -- start / duplicate ---------------------------------------------------


def test_one_start_submits_one_connect_task_and_binds_one_plan() -> None:
    harness = _build()
    _launch(harness)

    assert harness.trading.connected == ["1"]
    assert [call["resource_group"] for call in harness.submitter.calls] == ["broker"]
    assert len(harness.workflow.begin_calls) == 1
    assert len(harness.workflow.published) == 1


def test_a_duplicate_start_is_refused_before_anything_is_built() -> None:
    """The first gate, and the reason it must stay first.

    A second attempt admitted here would create a second candidate, start a second
    broker task and run a second ``begin_connecting`` over the live plan.  The
    refusal must therefore happen before *any* fact is read.
    """

    harness = _build()
    harness.orchestrator.start()
    assert harness.workflow.phase is PaperWorkflowPhase.CONNECTING
    # The connect itself runs on the worker, so it has not happened yet.
    assert harness.trading.connected == []

    # A second start while the first is in flight.
    harness.orchestrator.start()

    assert len(harness.submitter.calls) == 1
    assert len(harness.workflow.begin_calls) == 1
    assert harness.workflow.lease.acquired == 1
    # The lease is neither released nor replaced by the refused attempt.
    assert harness.workflow.lease.active is True
    assert harness.workflow.lease.released == 0
    assert harness.workflow.active_plan is harness.workflow.begin_calls[0]
    assert harness.events.refusals[-1][0] == messages.DUPLICATE_TITLE


def test_a_duplicate_start_leaves_the_arm_confirmation_alone() -> None:
    """The retired handler did not clear it either; the attempt is still live."""

    harness = _build()
    harness.orchestrator.start()
    harness.arm_clears.clear()

    harness.orchestrator.start()

    assert harness.arm_clears == []


def test_shadow_active_refuses_the_paper_launch() -> None:
    harness = _build(shadow_active=True)
    harness.orchestrator.start()

    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.lease.acquired == 0
    assert harness.trading.connected == []
    assert harness.submitter.calls == []
    assert harness.arm_clears == [1]
    assert harness.events.refusals[-1][0] == messages.SHADOW_ACTIVE_TITLE


def test_a_failed_preflight_takes_no_lease_and_creates_no_candidate() -> None:
    failures = (_Check("行情", "没有 READY 行情"), _Check("资金", "未读取账户"))
    harness = _build(preflights=[_Preflight(ready=False, failures=failures)])
    harness.orchestrator.start()

    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.lease.acquired == 0
    assert harness.trading.connected == []
    assert harness.submitter.calls == []
    assert harness.arm_clears == [1]
    title, message = harness.events.refusals[-1]
    assert title == messages.PREFLIGHT_TITLE
    # The operator still gets the bulleted failure list.
    assert "行情：没有 READY 行情" in message
    assert "资金：未读取账户" in message


def test_a_task_refused_by_admission_rejects_the_plan_and_releases_the_lease() -> None:
    """No CONNECTING zombie: a refused task must unwind the attempt it opened.

    ``_start_task`` returning ``False`` means the task was never admitted -- the
    window is closing, or the broker group is busy.  The plan was already bound and
    PAPER already taken, so both must be handed back here.
    """

    harness = _build(submitter=_Submitter(admitted=False))
    harness.orchestrator.start()

    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.active_plan is None
    assert harness.workflow.lease.acquired == 1
    assert harness.workflow.lease.released == 1
    assert harness.workflow.lease.active is False
    assert harness.trading.connected == []
    assert harness.renders  # the launch state was republished


def test_the_begin_refusal_is_reported_and_nothing_is_bound() -> None:
    """A controller refusal is an operator condition, not a crash."""

    class _Busy(_Workflow):
        def begin_connecting(self, plan: AutoLaunchPlan) -> None:
            self.begin_calls.append(plan)
            raise WorkflowStateError("A Paper launch attempt is already active.")

    harness = _build(workflow=_Busy())
    harness.orchestrator.start()

    assert harness.events.refusals[-1][0] == messages.BEGIN_REFUSED_TITLE
    assert harness.trading.connected == []
    assert harness.workflow.lease.acquired == 0
    assert harness.submitter.calls == []


# -- the happy path -----------------------------------------------------


def test_a_successful_launch_publishes_the_session_and_promotes_once() -> None:
    harness = _build()
    _launch(harness)

    assert harness.workflow.phase is PaperWorkflowPhase.RUNNING
    assert harness.trading.promoted == ["1"]
    assert harness.trading.active is not None
    assert harness.trading.candidate_ids == set()
    assert len(harness.events.publications) == 1
    publication = harness.events.publications[0]
    assert publication.session_id == "session-1"
    assert publication.candidate_count == 1
    assert len(harness.events.runtime_events) == 1
    event = harness.events.runtime_events[0]
    assert event.code == messages.PAPER_ARMED_CODE
    assert event.component == messages.PAPER_LAUNCH_COMPONENT


def test_the_launch_binds_the_plan_before_the_broker_connects() -> None:
    """The lease must be taken before the connect, not after.

    Shadow and Paper share one execution lease, so this ordering *is* the structural
    mutual exclusion rather than a UI gate.
    """

    order: list[str] = []
    workflow = _Workflow()
    original_begin = workflow.begin_connecting

    def begin(plan):
        order.append("begin_connecting")
        original_begin(plan)

    workflow.begin_connecting = begin  # type: ignore[method-assign]

    class _OrderedTrading(_Trading):
        def connect_candidate(self, candidate_id, **kwargs):
            order.append("connect_candidate")
            return super().connect_candidate(candidate_id, **kwargs)

    harness = _build(workflow=workflow, trading=_OrderedTrading())
    _launch(harness)

    assert order == ["begin_connecting", "connect_candidate"]
    assert harness.workflow.lease.active is True


def test_the_attempt_sequence_is_an_incrementing_integer_from_one() -> None:
    """The retired semantics, kept: a distinct id per attempt, not a UUID.

    The first attempt is refused by its broker gate so the workflow returns to
    ``READY``, and the next start must therefore take id ``2`` rather than reusing
    ``1`` -- which is what stops a late callback for attempt 1 from being mistaken
    for one about attempt 2.
    """

    stranded = _Candidate(net_liquidation=None)
    trading = _Trading()

    def candidate_for(candidate_id: str) -> _Candidate:
        # The first attempt reads a bad account; the second a good one.
        return stranded if candidate_id == "1" else _Candidate()

    trading._candidate_for = candidate_for  # type: ignore[method-assign]
    harness = _build(trading=trading)

    _launch(harness)
    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.trading.connected == ["1"]

    harness.orchestrator.start()
    harness.submitter.finish(harness.submitter.work())

    assert harness.trading.connected == ["1", "2"]
    assert harness.trading.promoted == ["2"]
    assert harness.workflow.phase is PaperWorkflowPhase.RUNNING


# -- stale callback ------------------------------------------------------


def test_a_late_callback_disposes_only_its_own_candidate() -> None:
    """The core safety property of the stale path.

    Attempt A connects while attempt B becomes current.  When A's callback arrives it
    must discard A's candidate and reject A's unarmed plan -- and leave B's plan, B's
    lease and the active service exactly as they were.
    """

    harness = _build()
    harness.orchestrator.start()
    result_a = harness.submitter.work()

    # Attempt A is cancelled and superseded by B, which is now current.
    plan_b = build_auto_launch_plan(
        attempt_id=99,
        strategy_version_id="v-2",
        parameter_hash="hash-2",
        candidate_symbols=("MSFT",),
        requested_capital_limit=Decimal("5000"),
    )
    harness.workflow.reject_connecting(result_a[1].plan)
    harness.workflow.begin_connecting(plan_b)
    # A second candidate exists for B; A's own candidate is still registered because
    # its callback has not run yet.
    harness.trading._candidates["99"] = _Candidate()

    lease_before = harness.workflow.lease.active
    active_before = harness.trading.active

    harness.submitter.finish(result_a)

    assert harness.trading.discarded == ["1"]
    assert harness.trading.candidate_ids == {"99"}
    assert harness.workflow.active_plan is plan_b
    assert harness.workflow.lease.active is lease_before
    assert harness.trading.active is active_before
    assert harness.workflow.phase is PaperWorkflowPhase.CONNECTING
    # And no dialog about a launch that already moved on.
    assert all(title != messages.LAUNCH_FAILED_TITLE for title, _ in harness.events.refusals)
    assert harness.events.logs[-1] == STALE_PLAN_MESSAGE


def test_a_stale_callback_does_not_clear_the_newer_attempt_controls() -> None:
    harness = _build()
    harness.orchestrator.start()
    result_a = harness.submitter.work()
    plan_b = build_auto_launch_plan(
        attempt_id=99,
        strategy_version_id="v-2",
        parameter_hash="hash-2",
        candidate_symbols=("MSFT",),
        requested_capital_limit=Decimal("5000"),
    )
    harness.workflow.reject_connecting(result_a[1].plan)
    harness.workflow.begin_connecting(plan_b)
    harness.arm_clears.clear()
    harness.renders.clear()

    harness.submitter.finish(result_a)

    assert harness.arm_clears == []
    assert harness.renders == []


# -- second preflight ----------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda state: state.update(strategy=_Strategy(version_id="v-2")), id="strategy"),
        # A different *governed* parameter set: the hash differs because the content
        # does, which is the honest way to model "the operator applied another version".
        pytest.param(
            lambda state: state.update(
                strategy=_Strategy(parameters={"max_position_fraction": "0.02"})
            ),
            id="parameter_hash",
        ),
        pytest.param(lambda state: state.update(candidates=(_Row("MSFT"),)), id="candidates"),
        pytest.param(lambda state: state.update(capital_limit=Decimal("1")), id="capital_limit"),
    ],
)
def test_a_changed_input_during_the_connect_refuses_the_old_launch(mutate) -> None:
    """The frozen identity is revalidated, so the attempt is refused.

    Without this, an attempt could arm a strategy, shortlist or capital limit the
    operator changed while the broker was connecting.
    """

    harness = _build()
    harness.orchestrator.start()
    result = harness.submitter.work()
    mutate(harness.state)

    harness.submitter.finish(result)

    assert harness.trading.discarded == ["1"]
    assert harness.trading.promoted == []
    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.lease.active is False
    assert harness.trading.active is None
    assert harness.events.refusals[-1][1] == IDENTITY_CHANGED_MESSAGE


def test_a_preflight_that_fails_after_the_connect_refuses_the_launch() -> None:
    """Market/account readiness can change while the broker connects."""

    harness = _build(
        preflights=[
            _Preflight(),
            _Preflight(ready=False, failures=(_Check("行情", "行情已 stale"),)),
        ]
    )
    harness.orchestrator.start()
    result = harness.submitter.work()

    harness.submitter.finish(result)

    assert harness.trading.discarded == ["1"]
    assert harness.trading.promoted == []
    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.lease.active is False
    assert harness.events.refusals[-1][1] == PREFLIGHT_CHANGED_MESSAGE


def test_a_missing_strategy_during_the_connect_is_a_mismatch() -> None:
    """"No strategy selected" is a mismatch, not an exception."""

    harness = _build()
    harness.orchestrator.start()
    result = harness.submitter.work()
    harness.state["strategy"] = None

    harness.submitter.finish(result)

    assert harness.trading.discarded == ["1"]
    assert harness.events.refusals[-1][1] == IDENTITY_CHANGED_MESSAGE


# -- connect failure -----------------------------------------------------


def test_a_connect_error_ends_only_this_attempt() -> None:
    harness = _build(trading=_Trading())
    harness.trading.connect_error = RuntimeError("broker unreachable")

    harness.orchestrator.start()
    result = harness.submitter.work()
    harness.submitter.finish(result)

    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.lease.active is False
    assert harness.trading.active is None
    assert harness.trading.discarded == []
    title, message = harness.events.refusals[-1]
    assert title == messages.LAUNCH_FAILED_TITLE
    assert "broker unreachable" in message
    assert CONNECT_FAILED_MESSAGE.format(error="broker unreachable") == message


def test_a_malformed_callback_shape_fails_loudly() -> None:
    """A delegation error is raised, never swallowed into a silent stop.

    Swallowing it would leave the launch neither armed nor unwound: the session would
    stay in ``CONNECTING`` holding the lease with no path back.
    """

    harness = _build()
    harness.orchestrator.start()
    harness.submitter.work()

    with pytest.raises(TypeError):
        harness.submitter.finish("not-a-tuple")

    # The attempt is deliberately left as it was -- this is a programming error, and
    # hiding it behind a cleanup would be the failure mode the check exists to avoid.
    assert harness.workflow.phase is PaperWorkflowPhase.CONNECTING


def test_a_callback_whose_request_is_the_wrong_type_is_rejected() -> None:
    harness = _build()
    harness.orchestrator.start()
    harness.submitter.work()

    with pytest.raises(TypeError):
        harness.submitter.finish(("1", "not-a-request", None))


# -- broker gates --------------------------------------------------------


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        pytest.param(_Candidate(net_liquidation=None), messages.NET_LIQUIDATION_MESSAGE, id="missing-nlv"),
        pytest.param(_Candidate(net_liquidation=Decimal("0")), messages.NET_LIQUIDATION_MESSAGE, id="zero-nlv"),
        pytest.param(_Candidate(net_liquidation=Decimal("-5")), messages.NET_LIQUIDATION_MESSAGE, id="negative-nlv"),
        pytest.param(_Candidate(cash=None), messages.CASH_MESSAGE, id="missing-cash"),
        pytest.param(
            _Candidate(positions=(PaperBrokerPosition("AAPL", Decimal("1"), Decimal("1")),)),
            messages.POSITIONS_MESSAGE.format(symbols="AAPL"),
            id="existing-position",
        ),
    ],
)
def test_a_broker_gate_refuses_without_publishing(candidate, expected) -> None:
    """Every gate must refuse *before* publication, and promote nothing."""

    trading = _Trading()
    trading._candidate_for = lambda candidate_id: candidate  # type: ignore[method-assign]
    harness = _build(trading=trading)
    _launch(harness)

    assert harness.trading.promoted == []
    assert harness.trading.active is None
    assert harness.workflow.published == []
    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.lease.active is False
    assert harness.events.publications == []
    assert expected in harness.events.refusals[-1][1]


def test_the_capital_resolution_failure_refuses_the_launch() -> None:
    """A build-seam failure is still a single clean rejection."""

    candidate = _Candidate(net_liquidation=Decimal("100"), cash=Decimal("0"))
    trading = _Trading()
    trading._candidate_for = lambda candidate_id: candidate  # type: ignore[method-assign]

    harness = _build(trading=trading, builder=_Builder(error=ValueError("cash")))

    _launch(harness)

    assert harness.trading.promoted == []
    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.lease.active is False
    assert ARMING_FAILED_MESSAGE.format(error="cash") in harness.events.refusals[-1][1]


# -- publish ordering ----------------------------------------------------


def test_arm_ensure_publish_promote_order_is_fixed() -> None:
    """The irreversible sequence, asserted positively as an ordered trace.

    This is the whole constraint in one assertion, and v2O-E1's follow-up round is
    what makes it assertable: ``arm`` is the *orchestrator's* call now, so the trace
    covers all four steps instead of stopping at the build seam.  Before that, arming
    happened inside the injected seam and the real ordering could only be approximated.

    ``ensure`` before ``publish`` is the safety property: a publication that could
    still fail for an already-knowable reason would leave a session with no owner.
    ``promote`` last is the other one: a successful broker connect is not permission
    for the session to own execution.
    """

    order: list[str] = []

    class _TracingCandidate(_Candidate):
        def arm(self, **kwargs):
            order.append("arm")
            return super().arm(**kwargs)

    candidate = _TracingCandidate()
    trading = _Trading()
    trading._candidate_for = lambda candidate_id: candidate  # type: ignore[method-assign]

    original_ensure = trading.ensure_candidate_can_promote
    original_promote = trading.promote_candidate

    def ensure(candidate_id):
        order.append("ensure_candidate_can_promote")
        return original_ensure(candidate_id)

    def promote(candidate_id):
        order.append("promote_candidate")
        return original_promote(candidate_id)

    trading.ensure_candidate_can_promote = ensure  # type: ignore[method-assign]
    trading.promote_candidate = promote  # type: ignore[method-assign]

    workflow = _Workflow()
    original_publish = workflow.publish_armed

    def publish(*args, **kwargs):
        order.append("publish_armed")
        return original_publish(*args, **kwargs)

    workflow.publish_armed = publish  # type: ignore[method-assign]

    harness = _build(workflow=workflow, trading=trading)
    _launch(harness)

    assert order == [
        "arm",
        "ensure_candidate_can_promote",
        "publish_armed",
        "promote_candidate",
    ]


def test_the_wiring_reads_the_candidate_broker_state_before_arming() -> None:
    """Validation precedes the build, and the build receives the validated reading."""

    candidate = _Candidate()
    trading = _Trading()
    trading._candidate_for = lambda candidate_id: candidate  # type: ignore[method-assign]
    harness = _build(trading=trading)

    _launch(harness)

    assert len(harness.builder.calls) == 1
    request, service, reading = harness.builder.calls[0]
    assert isinstance(reading, PaperAccountReading)
    assert reading.net_liquidation == Decimal("25000")
    assert reading.cash == Decimal("25000")
    assert reading.account_alias == "DU1234567"
    # The borrowed candidate is the one that was armed and later promoted.
    assert service is candidate
    assert candidate.arm_calls
    assert harness.trading.active is candidate


def test_the_build_seam_receives_the_frozen_request_not_current_ui() -> None:
    harness = _build()
    _launch(harness)

    request = harness.builder.calls[0][0]
    assert isinstance(request, PaperLaunchRequest)
    assert request.plan.attempt_id == 1
    assert request.candidate_symbols == ("AAPL",)


# -- failure paths -------------------------------------------------------


def test_a_promotion_refused_before_publication_leaves_no_session() -> None:
    """An occupied active slot is discoverable *before* publication, so it is."""

    trading = _Trading()
    trading.would_refuse_promotion = True
    harness = _build(trading=trading)
    _launch(harness)

    assert harness.workflow.published == []
    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.lease.active is False
    assert harness.trading.active is None
    assert harness.trading.promoted == []
    assert harness.events.publications == []


def test_a_publish_failure_discards_the_candidate_and_rejects_the_plan() -> None:
    workflow = _Workflow()
    workflow.publish_error = WorkflowStateError("Stale or invalid publication.")
    harness = _build(workflow=workflow)

    _launch(harness)

    assert harness.trading.promoted == []
    assert harness.trading.discarded == ["1"]
    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.lease.active is False
    assert harness.trading.active is None
    assert harness.events.publications == []
    assert harness.events.refusals[-1][0] == messages.LAUNCH_FAILED_TITLE


def test_a_runtime_build_failure_discards_the_candidate() -> None:
    harness = _build(builder=_Builder(error=RuntimeError("runtime build failed")))
    _launch(harness)

    assert harness.trading.discarded == ["1"]
    assert harness.trading.promoted == []
    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.lease.active is False
    assert "runtime build failed" in harness.events.refusals[-1][1]


def test_an_arm_failure_discards_the_candidate() -> None:
    candidate = _Candidate(arm_error=RuntimeError("channel not connected"))
    trading = _Trading()
    trading._candidate_for = lambda candidate_id: candidate  # type: ignore[method-assign]
    harness = _build(trading=trading)

    _launch(harness)

    assert harness.trading.discarded == ["1"]
    assert harness.workflow.published == []
    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.lease.active is False
    assert "channel not connected" in harness.events.refusals[-1][1]


def test_a_candidate_discard_failure_still_rejects_the_plan() -> None:
    """The ``finally`` is load-bearing: a failed disconnect must not strand CONNECTING.

    ``PaperTradingService`` keeps ownership of a candidate it could not dispose, so
    this layer must not force its map -- but it must still release the workflow and
    the lease.
    """

    workflow = _Workflow()
    workflow.publish_error = WorkflowStateError("boom")
    trading = _Trading()
    trading.discard_error = RuntimeError("broker disconnect failed")
    harness = _build(workflow=workflow, trading=trading)

    with pytest.raises(RuntimeError):
        _launch(harness)

    # The plan was still rejected and the lease still released.
    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.active_plan is None
    assert harness.workflow.lease.active is False
    assert harness.trading.active is None


def test_a_failed_launch_leaves_the_controls_and_arm_flag_reset() -> None:
    harness = _build(builder=_Builder(error=RuntimeError("nope")))
    _launch(harness)

    assert harness.arm_clears
    assert harness.renders


def test_the_orchestrator_never_publishes_when_the_broker_gate_refuses() -> None:
    """One end-to-end assertion that "refused" means *no* session, not a partial one."""

    candidate = _Candidate(net_liquidation=None)
    trading = _Trading()
    trading._candidate_for = lambda candidate_id: candidate  # type: ignore[method-assign]
    harness = _build(trading=trading)

    _launch(harness)

    assert harness.events.publications == []
    assert harness.events.runtime_events == []
    assert harness.builder.calls == []
    assert harness.workflow.published == []


# -- Blocker 1: the frozen request really is frozen ----------------------


def test_freeze_launch_detaches_parameters_from_the_live_version() -> None:
    """The frozen request must not alias the live, mutable parameter mapping.

    This is the regression for the round's first blocker.  ``StrategyVersion`` is a
    frozen dataclass, but its ``parameters`` is a plain mutable ``dict`` and
    ``parameter_hash`` is read off the governed identity rather than recomputed -- so
    a request holding the live version would let the attempt be *planned* under hash A
    and *built* from parameters B.

    The edit mutates a **nested list** as well as a top-level scalar, because a
    shallow copy passes a scalar-only test while still aliasing the list the runtime
    reads for its market reference symbols.
    """

    live = _Strategy(
        parameters={
            "market_reference_symbols": ["SPY", "QQQ"],
            "max_position_fraction": "0.05",
        }
    )
    request = queries.freeze_launch(
        attempt_id=1,
        strategy=live,
        candidates=(_Row("AAPL"),),
        requested_capital_limit=Decimal("20000"),
        order_channel=PaperOrderChannel(
            config="config", repository="repository", extended_hours_enabled=False
        ),
    )

    live.parameters["max_position_fraction"] = "0.09"
    live.parameters["market_reference_symbols"].append("IWM")

    assert request.strategy.parameters["max_position_fraction"] == "0.05"
    assert request.strategy.parameters["market_reference_symbols"] == ["SPY", "QQQ"]
    # The plan's hash still describes exactly what the snapshot holds.
    assert request.plan.parameter_hash == parameter_hash_for(
        request.strategy.parameters
    )


def test_the_frozen_parameters_are_detached_from_the_source_object() -> None:
    """Mutating the request's own mapping cannot reach back into the live version."""

    live = _Strategy(parameters={"max_position_fraction": "0.05"})
    harness = _build(strategy=live)
    _launch(harness)

    request = harness.builder.calls[0][0]
    request.strategy.parameters["max_position_fraction"] = "0.01"

    assert live.parameters["max_position_fraction"] == "0.05"


def test_a_version_whose_hash_contradicts_its_parameters_is_refused() -> None:
    """Fail closed: never launch an inconsistent version under either hash.

    A version declaring a hash that does not describe its own parameters cannot be
    launched honestly.  Running it under the declared hash would execute parameters
    that hash does not cover -- the split-identity failure -- so the launch is
    refused before any plan is bound, the lease is taken or a candidate exists.
    """

    tampered = _Strategy(parameter_hash="0" * 64)
    harness = _build(strategy=tampered)

    with pytest.raises(PaperLaunchIntegrityError):
        harness.orchestrator.start()

    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.lease.acquired == 0
    assert harness.trading.connected == []
    assert harness.submitter.calls == []


def test_editing_the_live_parameters_during_the_connect_is_refused() -> None:
    """An in-place edit during the connect refuses the attempt -- it never runs B.

    This is the sharp end of blocker 1.  The governed identity still claims hash A
    while the live mapping now holds B, so there is no honest way to proceed: running
    A would ignore the edit, running B would execute parameters the plan does not
    cover.  The attempt is refused instead, and -- because this gate runs inside the
    connect callback's slot -- the refusal is a *mismatch*, not an exception, so the
    normal rejection path disposes the candidate and releases PAPER rather than
    stranding the attempt in ``CONNECTING``.

    The critical assertion is ``builder.calls == []``: the session was never built,
    so the edited parameters never reached the runtime.
    """

    live = _Strategy(
        parameters={
            "market_reference_symbols": ["SPY", "QQQ"],
            "max_position_fraction": "0.05",
        }
    )
    harness = _build(strategy=live)
    harness.orchestrator.start()
    result = harness.submitter.work()

    # The live version is edited in place while the broker connects.
    live.parameters["max_position_fraction"] = "0.09"
    live.parameters["market_reference_symbols"].append("IWM")

    harness.submitter.finish(result)

    assert harness.builder.calls == []
    assert harness.trading.promoted == []
    assert harness.trading.discarded == ["1"]
    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.lease.active is False
    assert harness.events.refusals[-1][1] == IDENTITY_CHANGED_MESSAGE


# -- Blocker 2: publication splits rollback from invariant failure -------


def _publication_then_promotion_failure():
    """A harness where ``publish_armed`` succeeds and promotion then raises.

    Promotion failing *after* publication is the broken-invariant window: the
    workflow has already reached ``RUNNING`` with a published coordinator, so there
    is nothing left to roll back.
    """

    workflow = _Workflow()
    trading = _Trading()
    trading.promote_error = RuntimeError("promotion slot taken")
    harness = _build(workflow=workflow, trading=trading)
    _launch(harness)
    return harness


def test_a_promotion_failure_after_publication_is_not_rolled_back() -> None:
    """The session, the lease and the candidate must all survive.

    This is the regression for the round's second blocker.  Rolling back here is
    actively harmful: ``reject_connecting`` is a no-op outside ``CONNECTING``,
    discarding the candidate leaves a *published* session with no owner, and releasing
    PAPER would un-enforce the Shadow/Paper mutex while the session is live.
    """

    harness = _publication_then_promotion_failure()

    # The workflow was published and stays running.
    assert harness.workflow.published == [harness.workflow.active_plan]
    assert harness.workflow.phase is PaperWorkflowPhase.RUNNING
    # The lease is still held -- not released.
    assert harness.workflow.lease.active is True
    assert harness.workflow.lease.released == 0
    # The candidate is still owned and was NOT discarded or disconnected.
    assert harness.trading.candidate_ids == {"1"}
    assert harness.trading.discarded == []
    # And no refusal was raised as though this were an ordinary launch failure.
    assert all(
        title != "Paper 会话未启动" for title, _ in harness.events.refusals
    )
    # The session was never published to the desktop either -- the window did not
    # adopt a runtime it cannot own.
    assert harness.events.publications == []


def test_a_promotion_failure_after_publication_is_reported_as_an_invariant() -> None:
    """It must be loud, with its own code, not disguised as a launch failure."""

    harness = _publication_then_promotion_failure()

    codes = [
        event.code
        for event in harness.events.runtime_events
        if getattr(event, "code", None) == PAPER_PROMOTION_INVARIANT_CODE
    ]
    assert codes == [PAPER_PROMOTION_INVARIANT_CODE]
    invariant = next(
        event
        for event in harness.events.runtime_events
        if event.code == PAPER_PROMOTION_INVARIANT_CODE
    )
    assert invariant.severity == "error"
    assert "promotion slot taken" in invariant.message
    # The operator is told, and told what state the session is in.
    assert harness.events.refusals[-1][0] == messages.PAPER_PROMOTION_INVARIANT_TITLE
    assert "已发布未接管" in harness.events.refusals[-1][1]


def test_a_publish_failure_before_publication_still_rolls_back() -> None:
    """The other side of the boundary: ``publish_armed`` raising is still a rollback.

    ``publish_armed`` raises *before* the workflow transitions to ``RUNNING``, so
    nothing was published and the attempt must be unwound normally -- candidate
    disposed, plan rejected, PAPER released.
    """

    workflow = _Workflow()
    workflow.publish_error = WorkflowStateError("Stale or invalid publication.")
    harness = _build(workflow=workflow)

    _launch(harness)

    assert harness.trading.discarded == ["1"]
    assert harness.workflow.phase is PaperWorkflowPhase.READY
    assert harness.workflow.lease.active is False
    assert harness.events.refusals[-1][0] == messages.LAUNCH_FAILED_TITLE


# -- Blocker 3: arming is the orchestrator's own step --------------------


def test_the_orchestrator_arms_the_channel_itself() -> None:
    """``arm`` must be called by the orchestrator, not hidden in the build seam.

    The round is named "launch / arm orchestration", and the point of the change is
    that ``arm -> ensure -> publish -> promote`` is one explicit sequence.  If arming
    moved back inside the injected seam, the ordered-trace test above would still
    pass while the real constraint became unassertable.
    """

    candidate = _Candidate()
    trading = _Trading()
    trading._candidate_for = lambda candidate_id: candidate  # type: ignore[method-assign]
    harness = _build(trading=trading)

    _launch(harness)

    assert candidate.arm_calls, "the orchestrator must arm the channel"
    armed = candidate.arm_calls[0]
    assert armed["session_id"] == "session-1"
    assert armed["allowed_symbols"] == ("AAPL",)
    # The notional came from the build seam's sizing, not from a literal here.
    assert armed["max_order_notional"] == Decimal("1000")
    # And the build seam itself never arms.
    assert harness.workflow.phase is PaperWorkflowPhase.RUNNING
