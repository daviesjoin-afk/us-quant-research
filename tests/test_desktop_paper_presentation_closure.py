"""v2O-E4 behaviour: the Paper session's presentation ownership, and its closure.

The E1/E2/E3 files test sequencing -- when a launch may proceed, when a live session
polls, when a proof is due, when ownership may be given up.  This one tests a different
kind of claim, the one E4 exists for: **what the execution route draws, and who owns the
fact it draws from**.

Four properties carry the round, and each has tests that would fail on a plausible wrong
implementation rather than only on a missing one:

* **one retained presentation fact, owned by the capability.**  A published result is
  projected once into an immutable view; the projection is never the engine's own object;
  and there is exactly one writer.  The window keeps no copy -- that is a structural
  guard's job, and it is in ``test_desktop_paper_orchestration_architecture``;
* **a finished session stays drawable.**  ``finalize_if_safe`` clears the canonical
  result as part of releasing PAPER, so a route that read the workflow would blank out at
  the moment a session ends.  The retained view survives it, and the route still renders
  the session it is reporting on;
* **retained is not authoritative.**  A blocked release leaves the finalized session on
  screen *and* leaves the ownership claim exactly where it was: no ``session_finalized``,
  no ``finalize_if_safe`` call, and the shutdown verdict still read from canonical truth.
  Nothing on any decision path may read the retained view, and that is asserted
  structurally as well as behaviourally;
* **replacement is atomic and late.**  PREPARING, READY, CONNECTING and a failed connect
  publish nothing, so the previous session survives every one of them; a new session
  replaces it only when it has a result of its own to show.

No Qt application is needed for the capability half -- the harness is a fake workflow and
a fake order-service owner.  Two cases build a real offscreen ``MainWindow`` so the
*production* render path is exercised rather than a stand-in for it.
"""

from __future__ import annotations

import ast
import os
import pathlib
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from us_quant.auto_launch import build_auto_launch_plan
from us_quant.desktop import MainWindow
from us_quant.desktop_v2.orchestration.paper.models import (
    PREFLIGHT_TITLE,
    PaperLaunchRequest,
    PaperOrderChannel,
    PaperShutdownDisposition,
    PaperStrategyLaunchFact,
)
from us_quant.desktop_v2.orchestration.paper.orchestrator import PaperOrchestrator
from us_quant.desktop_v2.orchestration.paper.presentation import (
    PaperPresentationSnapshot,
    project_presentation,
)
from us_quant.desktop_v2.pages.execution.projector import (
    audit_by_intent,
    build_session_view,
    pending_by_symbol,
    session_positions,
)
from us_quant.trading.application.paper.models import PaperTradingLifecycleError
from us_quant.trading.domain.strategy import StrategyIdentity, parameter_hash_for
from us_quant.trading.runtime.preflight import AutoQuantPreflight
from us_quant.trading.runtime.workflow_state import (
    ExecutionLease,
    PaperWorkflowPhase,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_PAPER_DIR = _SRC / "desktop_v2" / "orchestration" / "paper"
_ORCHESTRATOR_PATH = _PAPER_DIR / "orchestrator.py"
_PRESENTATION_PATH = _PAPER_DIR / "presentation.py"
_PROJECTOR_PATH = _SRC / "desktop_v2" / "pages" / "execution" / "projector.py"

_APP = QApplication.instance() or QApplication([])

#: "This result carries no engine snapshot", as distinct from "a snapshot with defaults".
#: The two are different facts and one of the cases below turns on telling them apart.
_NO_SNAPSHOT = object()


# -- fakes ---------------------------------------------------------------


class _Position:
    def __init__(self, symbol: str, *, quantity: int = 1) -> None:
        self.symbol = symbol
        self.quantity = quantity
        self.average_price = Decimal("100")
        self.opened_at = "2026-09-24T14:00:00Z"
        self.provider = "ibkr"


class _PendingOrder:
    def __init__(self, symbol: str) -> None:
        self.execution_symbol = symbol
        self.limit_price = Decimal("101.5")


class _Snapshot:
    """A stand-in engine snapshot, carrying the fields the projection reads."""

    def __init__(
        self,
        *,
        session_id: str = "session-1",
        active: bool = True,
        entries_paused: bool = False,
        stop_requested: bool = False,
        status: str = "running",
        candidate_count: int = 2,
        trades_today: int = 0,
        initial_equity: Decimal = Decimal("1000"),
        positions: tuple[object, ...] = (),
        pending_orders: tuple[object, ...] = (),
    ) -> None:
        self.session_id = session_id
        self.active = active
        self.entries_paused = entries_paused
        self.stop_requested = stop_requested
        self.status = status
        self.candidate_count = candidate_count
        self.trades_today = trades_today
        self.initial_equity = initial_equity
        self.estimated_equity = initial_equity
        self.estimated_realized_pnl = Decimal("0")
        self.estimated_unrealized_pnl = Decimal("0")
        self.positions = positions
        self.pending_orders = pending_orders
        self.fills = ()


class _State:
    def __init__(self, *, halted: bool = False, finalized: bool = False) -> None:
        self.halted = halted
        self.finalized = finalized


class _Result:
    def __init__(
        self,
        snapshot: object = _NO_SNAPSHOT,
        *,
        halted: bool = False,
        finalized: bool = False,
    ) -> None:
        self.state = _State(halted=halted, finalized=finalized)
        self.engine_snapshot = None if snapshot is _NO_SNAPSHOT else snapshot
        self.health = None
        self.events: tuple[object, ...] = ()


class _Workflow:
    """A faithful-enough ``PaperWorkflowController`` for the presentation surface.

    It owns what the real controller owns -- the phase, the latest result and the lease --
    and it models the one behaviour the round turns on: ``finalize_if_safe`` is a
    check-and-commit call that clears the result it releases.  ``calls`` records the
    attempt before it does anything, so "the capability never asked" and "the capability
    asked and was refused" stay distinguishable.
    """

    def __init__(
        self,
        *,
        phase: PaperWorkflowPhase = PaperWorkflowPhase.RUNNING,
        result: object = None,
        finalize_returns: bool = True,
    ) -> None:
        self.phase = phase
        self.result = result
        self.lease = (
            ExecutionLease.PAPER if result is not None else ExecutionLease.NONE
        )
        self.next_result: object = None
        self.finalize_returns = finalize_returns
        self.calls: list[str] = []

    def poll(self) -> object:
        self.calls.append("poll")
        result = self.next_result if self.next_result is not None else _Result(_Snapshot())
        self.next_result = None
        self.result = result
        return result

    def request_stop(self, snapshot: object | None = None) -> object:
        self.calls.append("request_stop")
        return self.poll()

    def reject_connecting(self, plan: object) -> bool:
        self.calls.append("reject_connecting")
        self.phase = PaperWorkflowPhase.READY
        return True

    def finalize_if_safe(self) -> bool:
        self.calls.append("finalize_if_safe")
        if not self.finalize_returns:
            return False
        # The real gate releases PAPER, its result, its coordinator and both evidence
        # records.  Modelled here as the release itself, because "the canonical result is
        # gone afterwards" is exactly why the retained view has to exist.
        self.result = None
        self.lease = ExecutionLease.NONE
        self.phase = PaperWorkflowPhase.FINALIZED
        return True


class _Trading:
    """A stand-in order-service owner: what is held, and what a release does."""

    def __init__(
        self,
        *,
        holds_a_slot: bool = False,
        reserve_refusal: str | None = None,
    ) -> None:
        self._holds_a_slot = holds_a_slot
        self._reserve_refusal = reserve_refusal
        self.calls: list[str] = []

    def has_order_service(self) -> bool:
        return self._holds_a_slot

    def has_candidate_ownership(self) -> bool:
        return False

    def is_connected(self) -> bool:
        return self._holds_a_slot

    def reconciliation_status(self) -> object:
        return type("_Status", (), {"awaiting_confirmation": False})()

    def broker_state(self) -> object:
        return type("_Broker", (), {"positions": ()})()

    def disconnect(self) -> None:
        self.calls.append("disconnect")

    def reserve_active_release(self, expected_service: object = None) -> object:
        self.calls.append("reserve_active_release")
        if self._reserve_refusal is not None:
            raise PaperTradingLifecycleError(self._reserve_refusal)
        return object()

    def commit_active_release(self, reservation: object) -> None:
        self.calls.append("commit_active_release")

    def cancel_active_release(self, reservation: object) -> bool:
        self.calls.append("cancel_active_release")
        return True


def _orchestrator(
    workflow: _Workflow,
    *,
    paper_trading: _Trading | None = None,
    preflight_provider: object = None,
) -> PaperOrchestrator:
    """The capability over the two fakes, with every other seam unused."""

    trading = paper_trading if paper_trading is not None else _Trading()
    preflight = (
        preflight_provider
        if preflight_provider is not None
        else (lambda: None)
    )
    return PaperOrchestrator(
        workflow_getter=lambda: workflow,
        paper_trading_getter=lambda: trading,
        build_session=lambda *args, **kwargs: None,  # type: ignore[arg-type,return-value]
        submit_task=lambda *args, **kwargs: False,
        health_evaluator=lambda **kwargs: None,
        preflight_provider=preflight,  # type: ignore[arg-type]
        strategy_provider=lambda: None,
        candidates_provider=lambda: (),
        capital_limit_provider=lambda: Decimal("0"),
        order_channel_provider=lambda: None,  # type: ignore[arg-type,return-value]
        shadow_is_active=lambda: False,
        market_snapshot_provider=lambda: None,
        reconciliation_rows_provider=lambda session_id: (),
        clear_arm_confirmation=lambda: None,
        render_launch_state=lambda: None,
        render_launch_context=lambda summary: None,
        clock=lambda: 0.0,
    )


def _launch_request() -> PaperLaunchRequest:
    """One frozen attempt, for driving the connect callback's failure path.

    Built through the same pure rules production uses (``build_auto_launch_plan`` and the
    parameter hash) rather than hand-rolled, so the request the callback validates is a
    real one.
    """

    parameters: dict[str, object] = {}
    hash_of_parameters = parameter_hash_for(parameters)
    strategy = PaperStrategyLaunchFact(
        version_id="version-1",
        parameter_hash=hash_of_parameters,
        identity=StrategyIdentity(
            strategy_id="intraday-auto-rotation",
            version_id="version-1",
            parameter_hash=hash_of_parameters,
        ),
        parameters=parameters,
    )
    return PaperLaunchRequest(
        plan=build_auto_launch_plan(
            attempt_id=1,
            strategy_version_id=strategy.version_id,
            parameter_hash=strategy.parameter_hash,
            candidate_symbols=("AAA",),
            requested_capital_limit=Decimal("1000"),
        ),
        strategy=strategy,
        candidates=(),
        order_channel=PaperOrderChannel(
            config=None, repository=None, extended_hours_enabled=False
        ),
    )


def _retain(workflow: _Workflow, result: object) -> PaperOrchestrator:
    """Publish one result through the capability's one result path."""

    orchestrator = _orchestrator(workflow)
    workflow.next_result = result
    orchestrator.poll()
    return orchestrator


# -- A. presentation ownership -------------------------------------------


def test_a_published_result_is_projected_once_into_an_immutable_view() -> None:
    """One publication, one projection -- and not the engine's own object.

    Retaining the engine snapshot itself would keep a live runtime's positions, pending
    order intents and fills reachable from a route whose whole job is to draw strings.
    The projection detaches them: ``PaperPresentationSnapshot`` carries plain facts and
    nothing that can reach a session.
    """

    workflow = _Workflow()
    orchestrator = _retain(
        workflow,
        _Result(_Snapshot(session_id="session-1", active=True, candidate_count=3)),
    )

    retained = orchestrator.presentation
    assert isinstance(retained, PaperPresentationSnapshot)
    assert retained == project_presentation(workflow.result)
    assert retained is not workflow.result.engine_snapshot
    assert retained.session_id == "session-1"
    assert retained.active is True
    assert retained.candidate_count == 3


def test_each_publication_replaces_the_retained_view_rather_than_merging() -> None:
    """A new result is a new view, not a patch over the old one.

    Merging is the tempting implementation and it is the wrong one: a field the new
    session does not have would silently keep the previous session's value, so the route
    would show one session's holdings beside another's status.
    """

    workflow = _Workflow()
    orchestrator = _orchestrator(workflow)

    workflow.next_result = _Result(_Snapshot(positions=(_Position("AAA"),)))
    orchestrator.poll()
    assert [row.symbol for row in orchestrator.presentation.positions] == ["AAA"]

    workflow.next_result = _Result(_Snapshot(positions=(), status="已停止"))
    orchestrator.poll()
    assert orchestrator.presentation.positions == ()
    assert orchestrator.presentation.status == "已停止"


def test_a_result_with_nothing_to_draw_keeps_the_retained_view() -> None:
    """A publication that carries no engine snapshot cannot blank the page.

    The failure this guards is symmetric to the finalization one: a page that blanks on a
    missing snapshot is the same regression as a page that blanks when the canonical
    result is released, reached from the other direction.
    """

    workflow = _Workflow()
    orchestrator = _orchestrator(workflow)
    workflow.next_result = _Result(_Snapshot(session_id="session-1"))
    orchestrator.poll()
    retained = orchestrator.presentation
    assert retained is not None

    workflow.next_result = _Result(_NO_SNAPSHOT)
    orchestrator.poll()
    assert orchestrator.presentation is retained


def test_the_retained_view_has_exactly_one_writer_and_no_clear() -> None:
    """Only a published result may create the view, and nothing may take it away.

    Checked on the source rather than behaviourally *as well*, because the property is
    about what is expressible: a ``clear``, a ``reset`` or a hand-patched field would each
    be a way to fabricate a session transition, and the round's rule is that the retained
    fact moves only when the workflow publishes a new result.
    """

    tree = ast.parse(_ORCHESTRATOR_PATH.read_text(encoding="utf-8"))
    writes: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "PaperOrchestrator":
            continue
        for member in node.body:
            if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(member):
                targets: list[ast.expr] = []
                if isinstance(inner, ast.Assign):
                    targets = list(inner.targets)
                elif isinstance(inner, ast.AnnAssign):
                    targets = [inner.target]
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "_presentation"
                    ):
                        writes.append(
                            (member.name, ast.unparse(inner))
                        )
    # ``__init__``'s declaration, and the one writer.
    assert len(writes) == 2, writes
    assert {name for name, _ in writes} == {"__init__", "_retain_presentation"}, writes
    assert writes[0][1] == "self._presentation: PaperPresentationSnapshot | None = None"
    # And the writer only ever stores a projection of a real result.
    assert writes[1][1] == "self._presentation = projection"

    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    for fabricated in (
        "def clear_presentation",
        "def reset_presentation",
        "def invalidate_presentation",
        "_presentation = None",
    ):
        assert fabricated not in source, fabricated


def test_the_projection_is_a_pure_function_of_the_result() -> None:
    """Same result in, same view out -- and nothing else consulted.

    A projection that read a clock, a service or the previous view would be a second
    place that decides what a session looks like.
    """

    result = _Result(_Snapshot(positions=(_Position("AAA", quantity=2),), trades_today=1))
    first = project_presentation(result)
    second = project_presentation(result)
    assert first == second
    assert first is not second
    assert first.positions[0].quantity == 2
    assert first.trades_today == 1


# -- B. a finalized session stays drawable -------------------------------


def _finalized_setup(
    *, holds_a_slot: bool = False, reserve_refusal: str | None = None
) -> tuple[PaperOrchestrator, _Workflow, _Trading]:
    """The capability over a session that has reached the end of an orderly stop."""

    workflow = _Workflow(phase=PaperWorkflowPhase.STOPPING)
    trading = _Trading(holds_a_slot=holds_a_slot, reserve_refusal=reserve_refusal)
    orchestrator = _orchestrator(workflow, paper_trading=trading)
    return orchestrator, workflow, trading


def _publish_finalized(
    orchestrator: PaperOrchestrator,
    workflow: _Workflow,
    *,
    session_id: str = "session-1",
) -> None:
    """Publish the one result that reports the session finalized."""

    workflow.next_result = _Result(
        _Snapshot(session_id=session_id, active=False, status="已停止"),
        finalized=True,
    )
    orchestrator.poll()


def _finalized_orchestrator(
    *, holds_a_slot: bool = False, reserve_refusal: str | None = None
) -> tuple[PaperOrchestrator, _Workflow, _Trading]:
    orchestrator, workflow, trading = _finalized_setup(
        holds_a_slot=holds_a_slot, reserve_refusal=reserve_refusal
    )
    _publish_finalized(orchestrator, workflow)
    return orchestrator, workflow, trading


def test_a_finalized_session_survives_the_canonical_result_being_released() -> None:
    """The regression this round exists to prevent, asserted at both ends.

    ``finalize_if_safe`` is the one gate allowed to release PAPER, and it clears the
    result, the coordinator and both evidence records in the same call.  So the canonical
    read is empty afterwards -- and the route still has the session to draw.
    """

    orchestrator, workflow, _ = _finalized_orchestrator()

    assert workflow.calls.count("finalize_if_safe") == 1
    assert orchestrator.result is None, "the canonical result was released"

    retained = orchestrator.presentation
    assert retained is not None
    assert retained.session_id == "session-1"
    assert retained.active is False
    assert retained.status == "已停止"


def test_the_execution_route_still_renders_the_finished_session() -> None:
    """The end of the data flow: the retained view is what the cards are built from.

    A view built from the canonical result would not exist at all here, so this asserts
    the route has something real to draw *after* the release -- a status, the session's
    own summary, and no exception on the way.
    """

    orchestrator, workflow, _ = _finalized_orchestrator()

    view = build_session_view(
        session=orchestrator.presentation,
        account=None,
        broker_state=None,
        quotes={},
        candidates=(),
        reconciliations=(),
        audit_rows=(),
        latency=(),
        recently_ready=lambda symbol: False,
    )

    assert view.status.value == "已停止"
    assert "session-" in view.summary
    assert "会话风险资金" in view.summary
    # And the canonical read really is empty, so the view above cannot have come from it.
    assert orchestrator.result is None
    assert workflow.result is None


# -- C. a blocked release keeps the view and the claim -------------------


def test_a_blocked_release_keeps_the_finished_view_and_the_ownership_claim() -> None:
    """Retained is not authoritative, in the one case where the two can disagree.

    The session reports itself finalized, so the route legitimately keeps showing it --
    but the release could not be proved, so nothing may be given up and nothing may say it
    was.  ``finalize_if_safe`` never runs, which is the strongest available evidence: the
    lease, the result, the coordinator and both evidence records are all cleared *inside*
    that call, so "it was never called" means "none of them was touched".
    """

    orchestrator, workflow, trading = _finalized_setup(
        holds_a_slot=True,
        reserve_refusal="a promotion claim still holds the slot",
    )
    finalized: list[int] = []
    orchestrator.session_finalized.connect(lambda: finalized.append(1))

    _publish_finalized(orchestrator, workflow)

    assert "reserve_active_release" in trading.calls
    assert "finalize_if_safe" not in workflow.calls, "the lease must not be released"
    assert finalized == [], "nothing may claim the ownership was given up"
    # The session is still on screen...
    assert orchestrator.presentation is not None
    assert orchestrator.presentation.session_id == "session-1"


def test_a_blocked_release_leaves_the_shutdown_verdict_on_canonical_truth() -> None:
    """The control path reads ownership; the retained view may not stand in for it.

    The failure this rules out is the tempting short-cut: "the view says the session is
    over, so the close is fine".  It is not -- the slot is still held and the lease was
    never released -- and the verdict has to say so.
    """

    orchestrator, workflow, _ = _finalized_orchestrator(
        holds_a_slot=True,
        reserve_refusal="a promotion claim still holds the slot",
    )
    workflow.phase = PaperWorkflowPhase.FINALIZED

    verdict = orchestrator.prepare_shutdown()

    assert verdict.disposition is PaperShutdownDisposition.OWNERSHIP_BLOCKED
    # And the retained view, which shows a finished session, was not consulted.
    assert orchestrator.presentation is not None
    assert orchestrator.presentation.session_id == "session-1"


def test_no_decision_path_reads_the_retained_view() -> None:
    """§20F, asserted structurally: presentation is a drawing fact, never an input.

    Every method that decides something about a session is checked for a *code* reference
    to the retained view.  Docstrings are excluded on purpose: the removal is documented
    by naming the old cache, and a text search would fire on the explanation instead of on
    a read.
    """

    tree = ast.parse(_ORCHESTRATOR_PATH.read_text(encoding="utf-8"))
    methods: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "PaperOrchestrator":
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[member.name] = member

    def reads_the_view(method: str) -> bool:
        body = list(methods[method].body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
        ):
            body = body[1:]
        for statement in body:
            for inner in ast.walk(statement):
                if isinstance(inner, ast.Attribute) and inner.attr == "_presentation":
                    return True
                if isinstance(inner, ast.Name) and inner.id == "presentation":
                    return True
        return False

    for method in (
        "start",
        "on_market_snapshot",
        "poll",
        "pause",
        "resume",
        "stop",
        "reconcile",
        "confirm_reconciliation_resume",
        "prepare_shutdown",
        "_shutdown_verdict_after_the_stop",
        "_ownership_verdict",
        "_release_paper_ownership_if_proven",
        "_maybe_schedule_finalization",
        "_start_finalization",
        "_maybe_finish_finalized_session",
        "_after_result",
        "_arm_and_publish",
        "runtime_active",
        "has_runtime_obligations",
        "session_control_facts",
    ):
        assert not reads_the_view(method), method


# -- D. how a new session replaces the previous one ----------------------


def test_the_finished_session_survives_preparing_ready_and_connecting() -> None:
    """Nothing before a result may clear the screen.

    The phases a new launch walks through publish no result at all, so the finished
    session is the only thing the route has to show.  Clearing it at "Start" would leave
    the operator with a blank page and no record of the session that just completed -- and
    if the connect then failed, with nothing at all.
    """

    orchestrator, workflow, _ = _finalized_orchestrator()
    assert orchestrator.presentation.session_id == "session-1"

    for phase in (
        PaperWorkflowPhase.PREPARING,
        PaperWorkflowPhase.READY,
        PaperWorkflowPhase.CONNECTING,
    ):
        workflow.phase = phase
        assert orchestrator.presentation.session_id == "session-1", phase


def test_a_failed_connect_leaves_the_previous_session_on_screen() -> None:
    """The connect's own failure path, driven through the callback that reports it.

    This is the case §14 calls out: the attempt produced no session, so there is nothing
    to replace the previous one *with*, and dropping it would lose a completed record over
    a connection that never happened.
    """

    orchestrator, workflow, _ = _finalized_orchestrator()
    workflow.phase = PaperWorkflowPhase.CONNECTING

    orchestrator._connect_finished(("candidate-1", _launch_request(), "connection refused"))

    assert workflow.calls.count("reject_connecting") == 1
    assert orchestrator.presentation.session_id == "session-1"


def test_a_new_session_replaces_the_previous_one_when_it_publishes() -> None:
    """And the replacement is atomic: the moment the new session has a result.

    Not at Start, not at PREPARING and not at CONNECTING -- at the result.  Which is the
    only point at which there is something real to replace it with.
    """

    orchestrator, workflow, _ = _finalized_orchestrator()
    assert orchestrator.presentation.session_id == "session-1"

    workflow.phase = PaperWorkflowPhase.RUNNING
    workflow.next_result = _Result(_Snapshot(session_id="session-2", active=True))
    orchestrator.poll()

    assert orchestrator.presentation.session_id == "session-2"
    assert orchestrator.presentation.active is True
    assert orchestrator.presentation.status == "running"


# -- E. the projection is pure, and the route renders once ----------------


class _Row:
    """One broker holding, as the broker reports it."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.quantity = 5
        self.average_cost = Decimal("10")


def test_the_read_model_is_pure_and_mutates_nothing_it_is_given() -> None:
    """Scope, keys and joins are conversions -- so they may not rewrite their inputs.

    ``_render_auto_quant_snapshot`` used to do this inline on the window, where the same
    call both fetched and assembled.  As a pure function it has to be testable on its own,
    and the cheapest way to keep it that way is to assert it leaves its inputs alone --
    including the broker reading, which a projection has business reading and none
    acting on.
    """

    session = project_presentation(
        _Result(
            _Snapshot(
                session_id="session-1",
                positions=(_Position("AAA"),),
                pending_orders=(_PendingOrder("AAA"),),
            )
        )
    )
    assert session is not None
    broker_state = _BrokerStub(positions=(_Row("AAA"), _Row("ZZZ")))
    rows: list[dict[str, object]] = [
        {"intent_id": "i-1", "session_id": "session-1"},
        {"intent_id": "i-2", "session_id": "other"},
    ]

    assert [row.symbol for row in session_positions(broker_state, symbols=("AAA",))] == [
        "AAA"
    ]
    assert set(pending_by_symbol(session)) == {"AAA"}
    assert set(audit_by_intent(rows, session_id="session-1")) == {"i-1"}
    # The inputs are exactly as they were: nothing was popped, filtered in place or
    # re-keyed, and nothing was done to the broker.
    assert [row.symbol for row in broker_state.positions] == ["AAA", "ZZZ"]
    assert [row["intent_id"] for row in rows] == ["i-1", "i-2"]
    assert broker_state.mutations == []
    # And the whole assembly, end to end, still writes nothing.
    build_session_view(
        session=session,
        account=None,
        broker_state=broker_state,
        quotes={},
        candidates=(),
        reconciliations=(),
        audit_rows=rows,
        latency=(),
        recently_ready=lambda symbol: False,
    )
    assert broker_state.mutations == []
    # And a session with no id has no audit rows of its own -- rather than matching every
    # row whose session_id happens to be absent.
    assert audit_by_intent([{"intent_id": "i-3"}], session_id="") == {}


def test_a_retained_finished_session_does_not_gate_a_new_launch() -> None:
    """§15's hard invariant, as behaviour rather than as prose.

    The retained view legitimately shows a finished session -- ``active=False`` -- while
    the operator starts the next one.  A launch gate that read it would refuse every
    launch after the first, so what the gate reads has to be the *canonical* phase.  The
    proof is that the launch proceeds to its own gates and comes back with the preflight's
    refusal, not with a duplicate-attempt one.
    """

    probes: list[str] = []

    def preflight() -> object:
        probes.append("preflight")
        return AutoQuantPreflight(ready=False, checks=())

    workflow = _Workflow(phase=PaperWorkflowPhase.STOPPING)
    orchestrator = _orchestrator(workflow, preflight_provider=preflight)
    _publish_finalized(orchestrator, workflow)
    assert orchestrator.presentation.active is False, "a finished session is on screen"

    workflow.phase = PaperWorkflowPhase.READY
    refused: list[str] = []
    orchestrator.refused.connect(lambda title, message: refused.append(title))

    orchestrator.start()

    assert probes == ["preflight"], "the launch read the canonical phase"
    assert refused == [PREFLIGHT_TITLE]


class _BrokerStub:
    """The broker's account reading, and a record of anything done *to* it.

    Mutations are recorded rather than merely absent: a projector that called
    ``disconnect`` on a broker it was handed would otherwise be invisible to a test that
    only checked the returned view.
    """

    def __init__(self, positions: tuple[object, ...]) -> None:
        self.positions = positions
        self.mutations: list[str] = []

    def disconnect(self) -> None:
        self.mutations.append("disconnect")

    def cancel(self, order_id: str) -> bool:
        self.mutations.append("cancel")
        return True


@pytest.fixture(autouse=True)
def _silence_dialogs(monkeypatch: pytest.MonkeyPatch):
    """A modal dialog blocks a headless run, and a close can raise one."""

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))


def test_one_published_result_renders_the_execution_route_once() -> None:
    """One Paper tick is one route render -- not a desktop repaint.

    The window's part in a result is *delegation*: hand the page the view its presenter
    built.  A route that repainted candidates, the dashboard or the system pages on every
    tick would make a quiet watchdog poll cost a full redraw, and would be reaching into
    pages that have nothing to do with the session.
    """

    window = MainWindow()
    _APP.processEvents()
    try:
        rendered: list[object] = []
        candidates_drawn: list[object] = []
        window.execution_page.render = lambda view: rendered.append(view)  # type: ignore[method-assign]
        window.execution_page.render_candidates = (  # type: ignore[method-assign]
            lambda view: candidates_drawn.append(view)
        )
        dashboard_before = window.dashboard_page.isVisible()

        window.paper_orchestrator._publish_result(
            _PaperResult(_session_snapshot())
        )

        assert len(rendered) == 1, "one result, one session render"
        assert len(candidates_drawn) == 1, "the shortlist is drawn once per render"
        assert window.dashboard_page.isVisible() == dashboard_before
        # And the view the page was handed came from the capability's retained fact.
        assert isinstance(window.paper_orchestrator.presentation, PaperPresentationSnapshot)
    finally:
        window.close()
        window.deleteLater()


# -- the real window's route, over the production result type -------------


def _session_snapshot():
    from us_quant.trading.runtime.artifacts import AutoQuantSnapshot

    return AutoQuantSnapshot(
        session_id="session-1",
        active=True,
        strategy_version_id="version-1",
        parameter_hash="hash-1",
        candidate_count=2,
        initial_equity=Decimal("10000"),
        estimated_cash=Decimal("10000"),
        estimated_equity=Decimal("10000"),
        estimated_realized_pnl=Decimal("0"),
        estimated_unrealized_pnl=Decimal("0"),
        positions=(),
        fills=(),
        intents=(),
        pending_orders=(),
        trades_today=0,
        trading_day="2026-09-24",
        status="running",
        observed_at="2026-09-24T14:00:00+00:00",
    )


def _PaperResult(snapshot, *, finalized: bool = False):
    from us_quant.trading.runtime.paper_models import (
        PaperSessionResult,
        PaperSessionState,
    )

    return PaperSessionResult(
        state=PaperSessionState(
            active=bool(snapshot.active),
            entries_paused=False,
            stop_requested=False,
            halted=False,
            finalized=finalized,
            local_position_count=0,
            pending_order_count=0,
            broker_open_order_count=0,
            broker_position_count=0,
            unreconciled_order_count=0,
            health_status=None,
        ),
        engine_snapshot=snapshot,
        health=None,
        events=(),
    )


# -- Guard 3/4/5: the dependency directions this round created -----------


def _imports(path: pathlib.Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_the_presentation_module_imports_no_widget_no_adapter_and_no_service() -> None:
    """Guard 3: the retained view and its projection are Qt-free and service-free."""

    modules = _imports(_PRESENTATION_PATH)
    for forbidden in (
        "PySide6",
        "us_quant.trading.adapters",
        "us_quant.trading.application",
        "us_quant.trading.composition",
        "us_quant.trading.ports",
        "us_quant.trading.runtime.models",
        "us_quant.trading.runtime.trading",
        "us_quant.trading.runtime.workflow",
        "ibapi",
        "sqlite3",
    ):
        assert not any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for module in modules
        ), (forbidden, sorted(modules))
    # It may name the result *type* for its annotations, and nothing else from the
    # lifecycle: a view that could reach the workflow would be a second owner.
    assert not any(
        module.startswith("us_quant.desktop_v2.orchestration.paper.")
        for module in modules
    ), sorted(modules)


def test_the_projector_imports_no_orchestrator_and_no_service() -> None:
    """Guard 5: the page's read model reads its session view structurally.

    Importing the Paper capability from the page package would drag ``PySide6.QtCore``
    into a module that must stay testable without a widget toolkit -- and would make the
    page a place that knows an orchestrator.  The contract is pinned by the test below
    instead.
    """

    modules = _imports(_PROJECTOR_PATH)
    for forbidden in (
        "PySide6",
        "us_quant.desktop",
        "us_quant.desktop_v2.orchestration",
        "us_quant.trading",
        "ibapi",
        "sqlite3",
    ):
        assert not any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for module in modules
        ), (forbidden, sorted(modules))


def test_the_retained_model_is_never_imported_downward() -> None:
    """Guard 4: trading, risk and execution may not reach up into a desktop view."""

    offending: list[tuple[str, str]] = []
    for path in sorted((_SRC / "trading").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for module in _imports(path):
            if "orchestration.paper.presentation" in module:
                offending.append((path.name, module))
    assert not offending, offending

    # Nor is the model's *name* reachable from outside the desktop package.
    for path in sorted((_SRC / "trading").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        assert "PaperPresentationSnapshot" not in path.read_text(encoding="utf-8"), path


def test_the_projector_never_mutates_anything() -> None:
    """Guard for M9: a projection reads, and nothing in it writes.

    A "helpful" read model that disconnected the broker, cancelled an order or cleaned up
    a journal row would be doing an operation while rendering -- reached from a page tick,
    with no owner deciding it.  Asserted on the *call names*, so a rename of the row
    builders cannot smuggle one in.
    """

    called: set[str] = set()
    for node in ast.walk(ast.parse(_PROJECTOR_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name:
                called.add(str(name))
    for forbidden in (
        "connect",
        "disconnect",
        "placeOrder",
        "cancelOrder",
        "cancel",
        "submit",
        "commit",
        "delete",
        "insert",
        "write",
        "update",
        "release",
        "reserve",
    ):
        assert forbidden not in called, (forbidden, sorted(called))


def test_the_projector_reads_the_fields_the_model_provides() -> None:
    """The presentation contract: the duck-typed read cannot drift silently.

    ``presenter`` and ``rows`` read the session view structurally -- that is the existing
    convention, and it is what keeps the page package free of orchestrator imports -- so
    the field names are a contract between two modules that must not import each other.
    This pins it in both directions: every name the projector reads, and every name the
    entry point hands on, exists on the model.
    """

    model_fields: set[str] = set()
    for node in ast.walk(ast.parse(_PRESENTATION_PATH.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ClassDef) or node.name != "PaperPresentationSnapshot":
            continue
        for member in node.body:
            if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
                model_fields.add(member.target.id)
    assert model_fields, "the model must declare its fields explicitly"

    # What the read model itself needs from a session.
    assert {"session_id", "pending_orders"} <= model_fields
    # What the page's presenter and rows draw from it.  Spelled out here because these
    # are read by ``getattr`` in a Qt-free module that cannot import the model to check.
    assert {
        "status",
        "active",
        "entries_paused",
        "stop_requested",
        "candidate_count",
        "trades_today",
        "initial_equity",
        "estimated_equity",
        "estimated_realized_pnl",
        "estimated_unrealized_pnl",
        "positions",
        "fills",
    } <= model_fields

    # And the row facts carry the columns those builders read.
    row_fields: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(_PRESENTATION_PATH.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ClassDef):
            continue
        row_fields[node.name] = {
            member.target.id
            for member in node.body
            if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name)
        }
    assert row_fields["PaperPositionFact"] == {
        "symbol",
        "quantity",
        "average_price",
        "opened_at",
        "provider",
    }
    assert row_fields["PaperPendingOrderFact"] == {"execution_symbol", "limit_price"}
    assert row_fields["PaperFillFact"] == {
        "occurred_at",
        "symbol",
        "side",
        "quantity",
        "price",
        "estimated_commission",
        "realized_pnl",
    }
