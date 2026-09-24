"""Paper orchestration: the one owner of a Paper session's whole run.

Launch: READY -> CONNECTING -> (2nd preflight + identity revalidation) -> broker gate
-> build runtime -> arm -> reserve the promotion -> publish_armed -> commit -> RUNNING.

Active session: market ingress, the watchdog poll, pause/resume and an orderly stop.

Recovery and finalization: ``HALTED`` -> manual reconciliation -> one-shot evidence ->
an explicit operator confirmation -> the existing session resumed; ``STOPPING`` -> the
zero-state proof (captured *before* the disconnect, confirmed *after* it) -> the
workflow's own ``finalize_if_safe`` gate -> the active Paper ownership released -> PAPER
released.

Presentation: every published result is projected into the one immutable
:class:`~us_quant.desktop_v2.orchestration.paper.presentation.PaperPresentationSnapshot`
the execution route draws, and that projection is retained here -- because
``finalize_if_safe`` clears the canonical result, and a route reading it would blank out
at the moment a session ends.  The projection itself is a pure function in its own
module, so this class sequences and does not assemble views.

Three rules matter more than the rest:

* **there is no second truth.**  The workflow owns the phase, the active plan, the
  lease, the latest result *and* both one-shot evidence records; the order-service owner
  owns the candidate and active connections.  This class mirrors none of them -- no
  ``_active_plan``, no ``_result``, no ``_runtime``, no ``_reconciliation_evidence``,
  no ``_finalization_evidence`` -- and the duplicate, staleness and shutdown gates read
  ``workflow.phase``, which is equivalent to the retired ``_active_auto_launch_plan is
  not None`` and stays correct after publication, when the plan legitimately outlives the
  attempt;
* **one result publication path.**  Every operation ends in :meth:`_publish_result`, so
  the launch's ``RUNNING`` and each later tick, reconciliation and finalization are
  published by the same code, an event is requested exactly once per operation, and the
  result's own consequences (:meth:`_after_result`) are decided in one place rather than
  by each caller;
* **the arm/reserve/publish/commit order is a safety constraint, and the promotion is
  taken rather than checked.**  The order-service owner installs the candidate as the
  active service *and* locks the slot in one call, so the workflow cannot reach
  ``RUNNING`` without an owner: there is no longer an ordering in which a published
  session could be left ownerless.  Publication still splits the failure handling -- a
  raise at or before ``publish_armed`` is a rollback (cancel the reservation, dispose the
  candidate, reject the plan, release PAPER), and it proceeds only if the cancel confirms
  it gave the slot back; after ``publish_armed`` returns only the launch's own claim is
  left to end, and neither side unwinds what it cannot account for;
* **the candidate is borrowed, never stored.**  It lives in one callback's local, never
  an attribute -- a stored handle would be a second owner of the connection;
* **nothing a halt or a finalization does is automatic.**  A halt is sticky and its only
  exit is an explicit operator reconciliation followed by a separate confirmation, and
  the Paper lease and the active ownership are released by no path *except* the
  workflow's own ``finalize_if_safe`` gate -- a successful disconnect proves nothing.

It does not own the workflow transitions it merely *calls*; the generic task lifecycle
(``TaskThread``, the controller, the closing gate and the busy dialog stay on the window,
reached through an injected ``TaskSubmitter`` -- no ``asyncio``); widgets and dialogs,
which is why the operator confirmations stay on the window; or the session's composition.
The seams it reads across the capability boundary arrive as providers for that reason,
never as imports: Market is not a dependency of Paper, and the journal rows the release
sequencing proves against arrive as one narrow callable rather than as a repository.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal
from time import monotonic
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

from us_quant.desktop_v2.orchestration.paper import queries
from us_quant.desktop_v2.orchestration.paper import presentation as paper_presentation
from us_quant.desktop_v2.orchestration.paper.models import (
    ARMED_EVENT_MESSAGE,
    ARMING_FAILED_MESSAGE,
    BAD_CALLBACK_MESSAGE,
    BEGIN_REFUSED_TITLE,
    CONNECTING_SUMMARY,
    CONNECT_FAILED_MESSAGE,
    CONNECT_PROGRESS,
    CONNECT_START_MESSAGE,
    CONNECT_VERIFIED_PROGRESS,
    DUPLICATE_MESSAGE,
    DUPLICATE_TITLE,
    FINALIZATION_PROGRESS,
    FINALIZATION_START_MESSAGE,
    IDENTITY_CHANGED_MESSAGE,
    LAUNCH_FAILED_TITLE,
    PAPER_ARMED_CODE,
    PAPER_EXECUTION_COMPONENT,
    PAPER_LAUNCH_COMPONENT,
    PAPER_LAUNCH_ROLLBACK_CODE,
    PAPER_LAUNCH_ROLLBACK_MESSAGE,
    PAPER_LAUNCH_ROLLBACK_TITLE,
    PAPER_PROMOTION_INVARIANT_CODE,
    PAPER_PROMOTION_INVARIANT_MESSAGE,
    PAPER_PROMOTION_INVARIANT_TITLE,
    PAPER_RELEASE_INVARIANT_CODE,
    PAPER_RELEASE_INVARIANT_MESSAGE,
    PAPER_STRATEGY_INTEGRITY_CODE,
    PAPER_STRATEGY_INTEGRITY_TITLE,
    PAUSE_SUCCEEDED_MESSAGE,
    PREFLIGHT_CHANGED_MESSAGE,
    PREFLIGHT_PREFIX,
    PREFLIGHT_TITLE,
    RECONCILIATION_NO_SERVICE_MESSAGE,
    RECONCILIATION_PROGRESS,
    RECONCILIATION_STARTED_MESSAGE,
    RECONCILIATION_START_MESSAGE,
    RESUME_EVIDENCE_MISSING_MESSAGE,
    RESUME_NOT_READY_MESSAGE,
    RESUME_PROGRESS,
    RESUME_START_MESSAGE,
    RESUME_SUCCEEDED_MESSAGE,
    SHADOW_ACTIVE_MESSAGE,
    SHADOW_ACTIVE_TITLE,
    SHUTDOWN_CANDIDATE_OWNERSHIP_REASON,
    SHUTDOWN_FINALIZATION_PENDING_MESSAGE,
    SHUTDOWN_LAUNCH_IN_FLIGHT_REASON,
    SHUTDOWN_MANUAL_RECOVERY_MESSAGE,
    SHUTDOWN_OWNERSHIP_BLOCKED_WITH_REASON_MESSAGE,
    SHUTDOWN_STOP_REFUSED_REASON,
    SHUTDOWN_STOP_REQUESTED_MESSAGE,
    SHUTDOWN_UNPROVABLE_SESSION_REASON,
    STALE_PLAN_MESSAGE,
    PaperLaunchIntegrityError,
    PaperLaunchRequest,
    PaperOrderChannel,
    PaperRuntimeEventRequest,
    PaperSessionBuilder,
    PaperShutdownDisposition,
    PaperShutdownResult,
)
from us_quant.trading.application.paper.models import PaperTradingLifecycleError
from us_quant.trading.runtime.workflow_state import (
    ExecutionLease,
    PaperWorkflowPhase,
    WorkflowStateError,
)

if TYPE_CHECKING:
    from us_quant.desktop_v2.orchestration.paper.models import PaperControlFacts
    from us_quant.desktop_v2.orchestration.paper.presentation import (
        PaperPresentationSnapshot,
    )
    from us_quant.trading.application.paper.models import PaperPromotionReservation
    from us_quant.trading.application.paper.service import PaperTradingService
    from us_quant.trading.runtime.models import AutoQuantCandidate
    from us_quant.trading.runtime.paper_models import PaperSessionResult
    from us_quant.trading.runtime.preflight import AutoQuantPreflight
    from us_quant.trading.runtime.workflow import PaperWorkflowController

    from us_quant.desktop_v2.orchestration.tasking import TaskSubmitter

#: How long a stream tick suppresses the watchdog poll that would repeat its work.
#: The comparison is strictly ``<``: 1.199s is suppressed, 1.200s is not.
STREAM_INGRESS_SUPPRESSION_SECONDS = 1.2

#: How long the zero-state proof is held back while the engine is still flattening.
#: While exits are pending, every stream tick must not launch a fresh full-broker
#: refresh; the proof is a *reader* of the same connection the session is draining, so
#: hammering it once per tick would interleave two readers for no new information.
#: Only consulted while the engine is still active -- a dormant session has nothing left
#: to wait for, and holding the proof back there would stall the close.
FINALIZATION_REFRESH_BACKOFF_SECONDS = 5.0


class PaperOrchestrator(QObject):
    """Owns a Paper session's run: the launch sequence and the active-session loop.

    It is *not* the workflow and *not* the order-service owner: it asks each for the
    step it owns and publishes what the outcome should look like.  Everything deciding
    what a Paper session *is* -- the phase machine, the lease, the arming rules, the risk
    verdict, order submission, the latest result -- stays where it lived.

    It is a **sequencing owner**, not a second state owner.  What it keeps is only what
    nothing else can: the next attempt's sequence number, the monotonic stamp of the
    last stream ingress the poll suppression reads, the two flags that decide whether
    *this object* already has a zero-state proof in flight, and -- since v2O-E4 -- the
    last published presentation projection.  The first four are bookkeeping about this
    object's own calls; the fifth is the one retained *presentation* fact, and it is
    retained precisely because the canonical result it projects is cleared when PAPER is
    released.  Neither is a fact about the session: every question about the session is
    still answered by reading the workflow, the order-service owner or the broker, and
    no business path may read the presentation projection at all.
    """

    #: A start was refused before anything was built; the window shows the dialog.
    refused = Signal(str, str)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    #: The workflow's latest result, from any operation.  One emission per operation.
    result_changed = Signal(object)

    #: One runtime event the window should record.
    runtime_event_requested = Signal(object)

    #: A transition produced no new result but the route's controls moved.
    #:
    #: ``HALTED`` -> ``RECONCILING``, a task that was never admitted, and a failed
    #: finalization proof all change what the operator may click without a
    #: ``PaperSessionResult`` existing for them.  Manufacturing a result to carry that
    #: would be a fabricated fact; the window is simply asked to repaint from the
    #: canonical truth it reads live.  Carries nothing on purpose -- no phase copy, no
    #: boolean and no state dict, because each of those is a second truth.
    presentation_refresh_requested = Signal()

    #: Every Paper ownership this capability held has been safely released.
    #:
    #: Emitted *after* the workflow's own ``finalize_if_safe`` gate agreed and the
    #: service slot was given up, so a handler that reacts to it cannot be reading a
    #: later state as if it were this one.  Payload-free: the window repaints from the
    #: canonical truth rather than from a copy carried on the wire.
    session_finalized = Signal()

    #: This session can now only be continued through the operator.
    #:
    #: The capability's answer to "is a human required?", which the window used to
    #: derive from the Paper phase itself.  Fired when a result lands ``HALTED`` and when
    #: a recovery step fails closed into it.  Payload-free for the same reason.
    manual_recovery_required = Signal()

    def __init__(
        self,
        *,
        workflow_getter: Callable[[], PaperWorkflowController],
        paper_trading_getter: Callable[[], PaperTradingService],
        build_session: PaperSessionBuilder,
        submit_task: TaskSubmitter,
        health_evaluator: Any,
        preflight_provider: Callable[[], AutoQuantPreflight],
        strategy_provider: Callable[[], Any],
        candidates_provider: Callable[[], Sequence[AutoQuantCandidate]],
        capital_limit_provider: Callable[[], Decimal],
        order_channel_provider: Callable[[], PaperOrderChannel],
        shadow_is_active: Callable[[], bool],
        market_snapshot_provider: Callable[[], object | None],
        reconciliation_rows_provider: Callable[[str], Sequence[object]],
        clear_arm_confirmation: Callable[[], None],
        render_launch_state: Callable[[], None],
        render_launch_context: Callable[[str], None],
        clock: Callable[[], float] = monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        # Getters rather than the objects themselves, for the reason
        # ``PaperTradingService`` takes a workflow getter: the composition root owns
        # both and the safety tests replace each to drive the halted, refused-close and
        # fake-broker paths, so every read must resolve whichever is live *now*.
        self._workflow_getter = workflow_getter
        self._paper_trading_getter = paper_trading_getter
        self._build_session = build_session
        self._submit_task = submit_task
        self._health_evaluator = health_evaluator
        self._preflight_provider = preflight_provider
        self._strategy_provider = strategy_provider
        self._candidates_provider = candidates_provider
        self._capital_limit_provider = capital_limit_provider
        self._order_channel_provider = order_channel_provider
        self._shadow_is_active = shadow_is_active
        # The market fact an orderly stop is judged against, injected rather than
        # imported: Market is not a dependency of Paper.  Read at call time, so the stop
        # sees the snapshot that exists when the operator clicks, never one frozen when
        # this orchestrator was built.
        self._market_snapshot_provider = market_snapshot_provider
        # The journal rows the release sequencing proves "no unreconciled order" against,
        # injected as one narrow callable rather than as the repository: the composition
        # root owns the repository and this layer must not import an adapter, and a single
        # `session_id -> rows` function is the whole of what the proof reads.
        self._reconciliation_rows_provider = reconciliation_rows_provider
        self._clear_arm_confirmation = clear_arm_confirmation
        self._render_launch_state = render_launch_state
        self._render_launch_context = render_launch_context
        # Injected so the suppression boundary is testable without sleeping: production
        # passes ``monotonic``, tests pass a clock they advance by hand.
        self._clock = clock
        # Desktop orchestration bookkeeping, not session truth: it only gives each
        # attempt a distinct identity, and keeps the retired integer-increment
        # semantics -- a UUID would change the candidate id on the wire for nothing.
        self._next_attempt = 0
        # Also bookkeeping, and also about this object's own calls: the moment of the
        # last stream ingress, which only decides whether the next watchdog poll would
        # repeat work this object just did.  Nothing reads it as session state.
        #
        # ``None`` rather than ``0.0``: "no ingress has happened yet" and "an ingress
        # happened at the clock's origin" are different facts, and the retired
        # ``0.0`` conflated them -- harmless only because a real ``monotonic()`` is
        # never near zero.  Under an injected clock the conflation silently suppresses
        # the first poll, which is exactly the poll that has to run.
        self._last_stream_ingress_monotonic: float | None = None
        # v2O-E3's two flags, and the same kind of bookkeeping: whether *this object*
        # already has a zero-state proof running, and when it last asked for one.  They
        # decide whether a result should start another proof, which is a question about
        # this object's own task sequencing -- not about the session.  Kept here rather
        # than read across the boundary, which is why the E2 ``finalization_inflight``
        # provider exists no longer: a provider kept the owner of a lifecycle decision on
        # the window.
        #
        # ``None`` rather than ``0.0`` for the same reason the ingress stamp uses it:
        # "no proof has ever been asked for" and "one was asked for at the clock's origin"
        # are different facts, and conflating them under an injected clock silently
        # suppresses the first proof -- which is the one the close needs.
        self._finalization_inflight = False
        self._last_finalization_started: float | None = None
        # v2O-E4's one retained presentation fact, and not session truth: it is the last
        # result this object published, projected into the immutable view the execution
        # route draws.  It exists because ``finalize_if_safe`` clears the canonical
        # result as part of releasing PAPER, so reading the workflow after that would
        # blank a page the operator is still reading.
        #
        # What keeps it honest is the shape rather than a rule: there is no method that
        # clears it, and its only writer is the one result publication path.  So no
        # transition -- PREPARING, CONNECTING, a failed connect, a finalized session --
        # can blank the route by hand, and nothing can fabricate a transition by patching
        # a field.  No business path may read it; see :attr:`presentation`.
        self._presentation: PaperPresentationSnapshot | None = None

    # -- lifecycle -------------------------------------------------------

    @property
    def _workflow(self) -> PaperWorkflowController:
        """Whichever controller is live -- see the getter's note in ``__init__``."""
        return self._workflow_getter()

    @property
    def _paper_trading(self) -> PaperTradingService:
        """Whichever order-service owner is live -- see the getter's note."""
        return self._paper_trading_getter()

    # -- delegated queries -----------------------------------------------
    #
    # Three questions other capabilities ask about a Paper session.  Each one is
    # answered by reading the workflow *now*: nothing is cached, because a cached
    # answer is a second truth that can disagree with the session it describes.

    @property
    def result(self) -> PaperSessionResult | None:
        """The workflow's latest result, or ``None`` when it holds none."""

        return self._workflow.result

    @property
    def runtime_active(self) -> bool:
        """Whether a Paper session is running.

        Read off the latest result's engine snapshot, which is the workflow's own
        projection of the runtime it owns.  The window used to answer this from a
        runtime handle it held; that handle was a second owner of a live session.
        """

        result = self._workflow.result
        return queries.session_active(
            result.engine_snapshot if result is not None else None
        )

    @property
    def has_runtime_obligations(self) -> bool:
        """Whether the session holds positions or orders a market stop would strand.

        The interlock a market stop or source switch is refused against.  It reads the
        same snapshot the operator is looking at rather than a counter kept beside it.
        """

        result = self._workflow.result
        return queries.runtime_obligations(
            result.engine_snapshot if result is not None else None
        )

    @property
    def presentation(self) -> PaperPresentationSnapshot | None:
        """The last published presentation fact: what the execution route should draw.

        Deliberately **not** the workflow's result.  ``finalize_if_safe`` clears the
        canonical result as part of releasing PAPER, so a route that read it would blank
        out at the moment a session ends -- exactly when the operator wants to read what
        just happened.  This is the retained projection instead, and the two are
        different kinds of fact: one is the current session truth, the other is the last
        fact this capability published *for display*.

        It is a presentation read and nothing else.  No launch gate, no stop, no
        reconciliation, no finalization, no ownership or lease decision and no risk or
        execution path may consult it; every one of those reads the canonical phase,
        the order-service owner or the broker, never this.
        """

        return self._presentation

    @property
    def session_control_facts(self) -> PaperControlFacts:
        """Which session controls the *canonical* phase makes available, as booleans.

        One of the delegated questions other capabilities ask, and the reason the window
        no longer compares phase values to publish the route's controls: which phase
        enables which button is a Paper rule, and it belongs beside the phase machine
        rather than on a presentation path in the window.

        Read from the workflow and the order-service owner *now*, never from
        :attr:`presentation`: a retained view of the last session may legitimately show
        ``active=False`` while a launch is in flight, and enabling controls from it would
        be the "retained presentation decides a launch" failure this round forbids.
        """

        return queries.control_facts(
            self._workflow.phase,
            awaiting_confirmation=(
                self._paper_trading.reconciliation_status().awaiting_confirmation
            ),
        )

    def start(self) -> None:
        """Run the launch gates, freeze the attempt, acquire PAPER and connect.

        The **duplicate gate comes first**: a second attempt refused after the connect
        would already have built two candidates, two broker tasks and a second
        ``begin_connecting`` over the live plan.  All three gates run *before* the lease
        is taken, so a refused start holds no PAPER and creates no candidate.
        """

        if queries.launch_attempt_in_flight(self._workflow.phase):
            self.refused.emit(DUPLICATE_TITLE, DUPLICATE_MESSAGE)
            return
        if self._shadow_is_active():
            # The shared lease makes Shadow XOR Paper structural, so this is a
            # courtesy refusal with the operator's sentence: the lease would refuse
            # the launch anyway, and would report it less clearly.
            self._clear_arm_confirmation()
            self.refused.emit(SHADOW_ACTIVE_TITLE, SHADOW_ACTIVE_MESSAGE)
            return
        preflight = self._preflight_provider()
        if queries.preflight_failed(preflight):
            self._clear_arm_confirmation()
            self.refused.emit(
                PREFLIGHT_TITLE,
                PREFLIGHT_PREFIX + queries.preflight_failure_text(preflight),
            )
            return
        strategy = self._strategy_provider()
        if strategy is None:
            # The preflight already refuses a missing strategy, so reaching here means
            # the selection changed between the two reads.  Refusing rather than
            # asserting keeps a race an operator condition, not a crash.
            self._clear_arm_confirmation()
            self.refused.emit(PREFLIGHT_TITLE, PREFLIGHT_PREFIX)
            return
        self._next_attempt += 1
        try:
            request = queries.freeze_launch(
                attempt_id=self._next_attempt,
                strategy=strategy,
                candidates=self._candidates_provider(),
                requested_capital_limit=self._capital_limit_provider(),
                order_channel=self._order_channel_provider(),
            )
        except PaperLaunchIntegrityError as error:
            # A catalogue fault, caught *by name* rather than by ``Exception``.  The
            # transaction is already safe -- nothing is bound, no lease is taken and no
            # candidate exists -- but leaving it to escape would strand the UI: the
            # operator's confirmation is already set, so they would see "armed" with no
            # session, no refusal, no log line and no event, and the traceback would
            # surface from a Qt slot rather than from a path that can report it.
            #
            # Nothing about this is a retry or a degraded launch, so the arm flag is
            # cleared and the fault is reported as an error event plus an operator
            # refusal.  The launch does not proceed under either hash.
            self._clear_arm_confirmation()
            self.log_requested.emit(str(error))
            self.runtime_event_requested.emit(
                PaperRuntimeEventRequest(
                    severity="error",
                    component=PAPER_LAUNCH_COMPONENT,
                    code=PAPER_STRATEGY_INTEGRITY_CODE,
                    message=str(error),
                )
            )
            self.refused.emit(PAPER_STRATEGY_INTEGRITY_TITLE, str(error))
            return
        try:
            # Bound and acquired *before* the connect: Shadow and Paper share one
            # lease, so this ordering is the structural mutex, not a UI gate.
            self._workflow.begin_connecting(request.plan)
        except WorkflowStateError as error:
            self.refused.emit(BEGIN_REFUSED_TITLE, str(error))
            return
        self._render_launch_state()
        self._render_launch_context(CONNECTING_SUMMARY)
        candidate_id = str(request.plan.attempt_id)
        channel = request.order_channel

        def task(progress: Callable[[str], None]) -> Any:
            progress(CONNECT_PROGRESS)
            try:
                connection = self._paper_trading.connect_candidate(
                    candidate_id,
                    config=channel.config,
                    repository=channel.repository,
                    extended_hours_enabled=channel.extended_hours_enabled,
                )
            except Exception as error:  # noqa: BLE001 - reported verbatim
                return candidate_id, request, str(error)
            progress(
                CONNECT_VERIFIED_PROGRESS.format(
                    alias=connection.account_alias  # type: ignore[attr-defined]
                )
            )
            return candidate_id, request, None

        started = self._submit_task(
            task,
            on_success=self._connect_finished,
            start_message=CONNECT_START_MESSAGE,
            resource_group="broker",
        )
        if not started:
            # Not admitted -- closing, or the broker group is busy.  No task exists, so
            # this attempt must be unwound here: reject the matching plan (releasing
            # PAPER) and restore the controls.  Otherwise it is a CONNECTING zombie.
            self._workflow.reject_connecting(request.plan)
            self._render_launch_state()

    def _connect_finished(self, result: object) -> None:
        """Continue the launch after the async candidate connect returned.

        Five safety properties, in order.  The **shape is checked loudly**: swallowing
        a malformed result is how a launch stops silently with neither an armed session
        nor a released lease.  A **connect error ends only this attempt**; a **stale plan
        disposes only its own candidate** and cannot touch the active service or a newer
        attempt's plan, lease or phase.  The **preflight is re-run** and the **identity
        revalidated**: market, account, strategy, candidate and capital may all have
        changed while connecting.
        """

        candidate_id, request, connection_error = _unpack(result)
        if connection_error is not None:
            self._reject_without_candidate(
                request, CONNECT_FAILED_MESSAGE.format(error=connection_error)
            )
            return
        if self._workflow.active_plan != request.plan:
            self._discard_candidate(
                candidate_id, request, STALE_PLAN_MESSAGE, show_message=False
            )
            return
        if queries.preflight_failed(self._preflight_provider()):
            self._discard_candidate(
                candidate_id, request, PREFLIGHT_CHANGED_MESSAGE, show_message=True
            )
            return
        if not queries.current_inputs_match(
            request.plan,
            strategy=self._strategy_provider(),
            candidates=self._candidates_provider(),
            requested_capital_limit=self._capital_limit_provider(),
        ):
            self._discard_candidate(
                candidate_id, request, IDENTITY_CHANGED_MESSAGE, show_message=True
            )
            return
        self._arm_and_publish(candidate_id, request)

    # -- the active session ----------------------------------------------
    #
    # What happens between ``RUNNING`` and recovery: the market fact going in, the
    # watchdog poll that keeps running when the market is quiet, the two entry controls
    # and the orderly stop.  Each of these is an *intent* about the active session, and
    # all four of them now have exactly one owner.

    def on_market_snapshot(self, snapshot: object) -> None:
        """Feed one market fact into a live session, and do nothing otherwise.

        Three gates, and the retired handler's order is preserved rather than tidied:

        * **a finalization task defers the ingress entirely.**  The proof of broker
          zero-state is reading the same broker the session is, so letting a tick in
          while it runs would interleave two readers of one connection;
        * **the phase gate.**  Only ``RUNNING``, ``PAUSED`` and ``STOPPING`` own the run
          loop.  A session that halted must not be re-entered by the next tick -- the
          halt is sticky by design and its recovery is v2O-E3, so nothing here may
          "repair" it;
        * **the ingress is stamped before the workflow is called**, so the stamp means
          "the stream just did this work" even if the call itself raises.

        ``STOPPING`` deliberately still receives the stream: the exits, the broker events
        and the health the operator watches all still depend on it while the session
        flattens.
        """

        if self._finalization_inflight:
            return
        if not queries.active_session_phase(self._workflow.phase):
            return
        self._last_stream_ingress_monotonic = self._clock()
        try:
            self._publish_result(self._workflow.on_stream(snapshot))
        except WorkflowStateError as error:
            self.log_requested.emit(str(error))

    def poll(self) -> None:
        """Drive one watchdog poll, unless a stream tick just did the same work.

        The timer's entry point.  It exists independently of the market stream because
        the order lifecycle -- stale-BUY cancel, SELL intervention, health evaluation --
        must keep running while the feed is down; it only skips when a stream tick
        recently drove the identical sequence.
        """

        if self._finalization_inflight:
            return
        last_ingress = self._last_stream_ingress_monotonic
        if (
            last_ingress is not None
            and self._clock() - last_ingress < STREAM_INGRESS_SUPPRESSION_SECONDS
        ):
            return
        if not queries.active_session_phase(self._workflow.phase):
            return
        try:
            self._publish_result(self._workflow.poll())
        except WorkflowStateError as error:
            self.log_requested.emit(str(error))

    def pause(self) -> None:
        """Pause new entries; the exits keep running.

        A refusal is a *log line and nothing else*: no dialog, no fabricated result and
        no attempt to move the phase by hand.  ``WorkflowStateError`` here means the
        session is not in a phase that can pause -- a condition the operator fixes by
        looking at the panel, not one to be papered over with a result that did not come
        from the workflow.
        """

        try:
            result = self._workflow.set_entries_paused(True)
        except WorkflowStateError as error:
            self.log_requested.emit(str(error))
            return
        self._publish_result(result)
        self.log_requested.emit(PAUSE_SUCCEEDED_MESSAGE)

    def resume(self) -> None:
        """Resume new entries, from ``PAUSED`` only."""

        try:
            result = self._workflow.set_entries_paused(False)
        except WorkflowStateError as error:
            self.log_requested.emit(str(error))
            return
        self._publish_result(result)
        self.log_requested.emit(RESUME_SUCCEEDED_MESSAGE)

    def stop(self) -> None:
        """Ask for an orderly stop, judged against the market fact of *this* moment.

        The snapshot is read through the provider at call time rather than captured when
        the orchestrator was built: a stop is judged against the quotes that exist when
        the operator asks for it, and a frozen one would silently age.
        """

        try:
            result = self._workflow.request_stop(self._market_snapshot_provider())
        except WorkflowStateError as error:
            self.log_requested.emit(str(error))
            return
        self._publish_result(result)

    # -- manual recovery ---------------------------------------------------
    #
    # ``HALTED`` is sticky and nothing here may leave it: the two operations below walk
    # the only route out, and neither of them trades.  Reconciliation *reads* broker truth
    # and files a one-shot proof; the confirmation *revalidates* that proof against fresh
    # truth and hands the workflow's own decision its go-ahead.  No step here creates a
    # replacement intent, resubmits a pending order or arms anything.

    def reconcile(self) -> None:
        """Collect fresh broker evidence for a halted session.

        The **preconditions are checked before anything moves**: reconciliation reads the
        session's own order service, so a halted session with no service has no route here
        at all -- and it must say so rather than appear to start and then fail.

        Reconnecting is *not* resuming.  The task's only two steps are "the service is
        connected again" and "file the proof"; a successful proof lands in
        ``RECONCILING_READY``, which is where the operator -- and only the operator, with
        a separate confirmation -- can take it further.

        A task that is never admitted rolls the attempt back, because the workflow has
        already moved to ``RECONCILING``: leaving it there would be a zombie phase whose
        only exit is the operator confirming a proof that was never taken.
        """

        if not self._paper_trading.has_order_service():
            self.log_requested.emit(RECONCILIATION_NO_SERVICE_MESSAGE)
            return
        try:
            attempt_id = self._workflow.begin_manual_reconciliation()
        except WorkflowStateError as error:
            self.log_requested.emit(str(error))
            return
        self.presentation_refresh_requested.emit()
        self.log_requested.emit(RECONCILIATION_STARTED_MESSAGE)

        def task(progress: Callable[[str], None]) -> Any:
            progress(RECONCILIATION_PROGRESS)
            if not self._paper_trading.is_connected():
                self._paper_trading.connect_active()
            return self._workflow.complete_manual_reconciliation(attempt_id)

        started = self._submit_task(
            task,
            on_success=self._reconciliation_finished,
            on_failure=lambda message: self._reconciliation_failed(
                attempt_id, message
            ),
            start_message=RECONCILIATION_START_MESSAGE,
            resource_group="broker",
        )
        if not started:
            # Not admitted -- the broker group is busy, or the client is closing.  The
            # attempt is rolled back only if it is still this attempt's, so a stale
            # callback cannot fail a newer reconciliation.
            self._workflow.fail_manual_reconciliation(attempt_id)
            self.presentation_refresh_requested.emit()
            self._announce_manual_recovery_if_required()

    def confirm_reconciliation_resume(self) -> None:
        """Resume the existing session -- after the operator has explicitly said so.

        **The proof is read here and now, after the confirmation, not before the dialog.**
        A proof captured when the question was asked can be consumed or superseded while
        the operator reads it, and resuming against a stale one is how a session comes
        back onto broker truth nobody checked.  So this method is the *whole* of the
        gate: it re-reads the phase and the evidence, freezes the evidence id into this
        attempt's closure, and only then submits.

        A refusal is a log line and no task.  There is deliberately no fabricated result:
        answering "not resumed" with a result the workflow never produced would be the
        second truth this boundary exists to prevent.

        Nothing here connects, arms, submits or resubmits -- the workflow's
        ``confirm_manual_resume`` revalidates the proof and asks the coordinator's engine
        recovery exactly once, keeping the same order port.
        """

        phase = self._workflow.phase
        evidence = self._workflow.reconciliation_evidence
        if not queries.reconciliation_resume_ready(phase, evidence):
            # The phase is checked first, exactly as the retired handler did: "there is
            # nothing to confirm" and "the proof that was here is gone" are different
            # situations and the operator fixes them differently.
            self.log_requested.emit(
                RESUME_NOT_READY_MESSAGE
                if phase is not PaperWorkflowPhase.RECONCILING_READY
                else RESUME_EVIDENCE_MISSING_MESSAGE
            )
            return
        # Frozen into this call's closure: a later proof -- or this one being consumed by
        # a duplicate click -- cannot resurrect a resume that was never approved.  The
        # workflow refuses an id that no longer matches its current evidence.
        evidence_id = str(evidence.evidence_id)

        def task(progress: Callable[[str], None]) -> Any:
            progress(RESUME_PROGRESS)
            return self._workflow.confirm_manual_resume(evidence_id)

        started = self._submit_task(
            task,
            on_success=self._publish_result,
            on_failure=self._resume_failed,
            start_message=RESUME_START_MESSAGE,
            resource_group="broker",
        )
        if not started:
            # The workflow was never reached, so the proof is still available for another
            # attempt; only the controls need repainting.
            self.presentation_refresh_requested.emit()

    def _reconciliation_finished(self, result: object) -> None:
        """Publish the evidence refresh.  Reconnect is collection, never resumption."""

        self._publish_result(result)  # type: ignore[arg-type]

    def _reconciliation_failed(self, attempt_id: str, message: str) -> None:
        """Fail only this attempt, and keep the session halted and retryable.

        A failed evidence refresh must leave the session exactly where it was -- halted,
        owned, leased -- because the alternative is a phase that looks recoverable while
        the broker truth behind it was never read.
        """

        self._workflow.fail_manual_reconciliation(attempt_id)
        self.log_requested.emit(message)
        self.presentation_refresh_requested.emit()
        self._announce_manual_recovery_if_required()

    def _resume_failed(self, message: str) -> None:
        """A consumed or changed proof always returns to sticky HALTED.

        The workflow has already moved the phase; nothing here repairs it, and the
        operator is left with the one route that remains, a fresh reconciliation.
        """

        self.log_requested.emit(message)
        self.presentation_refresh_requested.emit()
        self._announce_manual_recovery_if_required()

    # -- publication -----------------------------------------------------

    def _publish_result(self, result: PaperSessionResult) -> None:
        """Publish one workflow result, request its events once, and act on it once.

        The capability's *only* result path.  Three things happen, in this order, and
        nothing else may do any of them on a result's behalf: the window is handed the
        result to render; each event the operation produced is requested as one
        runtime-event write; and :meth:`_after_result` decides what the result *implies*
        -- whether a zero-state proof is due, and whether a finalized session's ownership
        can be given up.

        The consequences live here rather than at each call site deliberately.  A
        ``STOPPING`` result arrives from a stop, from a stream tick and from a poll, and
        three copies of "is the proof due?" is how one of them starts its own proof.  The
        window still owns the store and the rendering, so neither a result nor an event is
        ever recorded twice for one operation.
        """

        self._retain_presentation(result)
        self.result_changed.emit(result)
        for event in result.events:
            self.runtime_event_requested.emit(
                PaperRuntimeEventRequest(
                    severity=event.severity,
                    component=PAPER_EXECUTION_COMPONENT,
                    code=event.code,
                    message=event.message,
                )
            )
        self._after_result(result)

    def _retain_presentation(self, result: PaperSessionResult) -> None:
        """Replace the retained presentation fact -- from a real result, and only so.

        The one writer, and it runs *before* the result is published so a handler
        rendering on ``result_changed`` sees the new view rather than the previous one.

        Two properties are structural here rather than enforced elsewhere:

        * **there is no clear.**  No method, signal or branch takes the retained fact
          away, so a transition with no result -- PREPARING, CONNECTING, a refused
          launch, a failed connect -- cannot blank the route.  That is exactly the rule
          the round requires: the last displayable session survives until a *new*
          displayable session replaces it;
        * **a publication with nothing to draw keeps the previous fact.**  A result that
          carries no engine snapshot has nothing to show, and showing "nothing" would be
          the same blanking failure reached the other way.
        """

        projection = paper_presentation.project_presentation(result)
        if projection is None:
            return
        self._presentation = projection

    def _after_result(self, result: PaperSessionResult) -> None:
        """The one place a published result's consequences are decided.

        Two questions, and a result may answer both: whether a proof of broker zero-state
        is due, and whether a finalized session's ownership can now be given up.  Neither
        is a *new* result, so neither may publish one, and neither may move a phase by
        hand -- they ask the workflow and act on its answer.
        """

        self._maybe_schedule_finalization(result)
        self._maybe_finish_finalized_session(result)
        self._announce_manual_recovery_if_required()

    # -- finalization ------------------------------------------------------

    def _maybe_schedule_finalization(self, result: PaperSessionResult) -> None:
        """Start the zero-state proof when one is due, and at most once per window.

        The gate, in order, and every clause is load-bearing:

        * **the phase.**  Only ``STOPPING`` is flattening toward a disconnection proof.
          A ``RUNNING`` result must never start one -- the session is still trading -- and
          a ``HALTED`` one must never either, because a halt needs the operator rather
          than a proof;
        * **the result.**  A result that is already finalized has nothing left to prove;
        * **this object's own in-flight flag**, so a result arriving while its own proof
          runs cannot start a second reader of the same broker connection;
        * **the backoff**, only while the engine is still active: the exits are still
          working, and re-reading the whole broker once per tick would learn nothing new.
          A dormant engine clears the backoff, because holding the proof back there delays
          the close for no reason.
        """

        if self._workflow.phase is not PaperWorkflowPhase.STOPPING:
            return
        if result.state.finalized:
            return
        if self._finalization_inflight:
            return
        engine_active = bool(getattr(result.engine_snapshot, "active", True))
        last_started = self._last_finalization_started
        if (
            engine_active
            and last_started is not None
            and self._clock() - last_started < FINALIZATION_REFRESH_BACKOFF_SECONDS
        ):
            return
        self._start_finalization()

    def _start_finalization(self) -> None:
        """Prove broker zero-state, disconnect, confirm -- and release nothing yet.

        The task's order is the safety constraint, and it is two-sided: the **evidence is
        captured before the disconnect**, because the proof is a coherent reading of the
        broker the session is still connected to, and it is **confirmed after it**, because
        the confirmation consumes that proof only once the callback thread has joined.  A
        disconnect that happened first would have nothing left to read; a confirmation that
        happened first would certify a connection that was still live.

        Releasing PAPER is deliberately *not* here.  A successful disconnect is not a
        finalization, and ``finalize_if_safe`` is the only gate that may release it.
        """

        if not self._paper_trading.has_order_service():
            # Nothing to prove against: there is no order session left, so the workflow
            # itself has to fail the refresh and require the operator.  The halt this
            # lands is announced by :meth:`_after_result`, which is the only caller --
            # announcing here as well would say the same thing twice for one transition.
            self._workflow.fail_finalization_refresh()
            self.presentation_refresh_requested.emit()
            return
        self._finalization_inflight = True
        self._last_finalization_started = self._clock()

        def task(progress: Callable[[str], None]) -> Any:
            progress(FINALIZATION_PROGRESS)
            result, evidence_id = self._workflow.capture_finalization_evidence()
            if evidence_id is None:
                return result
            self._paper_trading.disconnect()
            return self._workflow.confirm_finalization_after_disconnect(evidence_id)

        started = self._submit_task(
            task,
            on_success=self._finalization_completed,
            on_failure=self._finalization_failed,
            start_message=FINALIZATION_START_MESSAGE,
            resource_group="broker",
            suppress_busy_message=True,
            # The proof is part of the close path itself, not new work: it is what lets
            # the session reach ``finalized`` so the window can be closed at all.
            shutdown_essential=True,
        )
        if not started:
            # **A refusal is not a failure.**  ``False`` means the broker resource group
            # was busy and the task was never scheduled -- no proof ran and none failed,
            # so the session is left exactly as it was and the next legal result retries
            # under the backoff.  Failing the refresh here would halt a session over a
            # scheduling collision.
            self._finalization_inflight = False
            self.presentation_refresh_requested.emit()

    def _finalization_completed(self, result: object) -> None:
        """Publish a finished proof, and keep the flag up until it has been acted on.

        The flag is cleared in a ``finally`` *after* publication, so the publication's own
        consequences -- which include a fresh proof being due when the engine only just
        stopped -- cannot start a second one inside this one.  Clearing it first would
        re-enter :meth:`_maybe_schedule_finalization` from the result it is publishing.
        """

        try:
            self._publish_result(result)  # type: ignore[arg-type]
        finally:
            self._finalization_inflight = False

    def _finalization_failed(self, message: str) -> None:
        """Fail closed: keep the PAPER lease, keep ownership, require the operator.

        This runs only for a task that *started* and then failed.  The workflow moves
        ``STOPPING`` -> ``HALTED``, which is the automatic failure route -- and the one
        route a phase check at close time cannot see, which is why the halt is announced
        rather than assumed.
        """

        self._finalization_inflight = False
        self._workflow.fail_finalization_refresh()
        self.log_requested.emit(message)
        self.presentation_refresh_requested.emit()
        self._announce_manual_recovery_if_required()

    # -- releasing the session ---------------------------------------------

    def _maybe_finish_finalized_session(self, result: PaperSessionResult) -> None:
        """Give up ownership when -- and only when -- the workflow says it is safe.

        Two facts have to hold at once for a session to be *over*: the result must report
        itself finalized, and the release sequencing must be able to prove that no
        ownership is being dropped that still has something behind it.  Anything else
        leaves both the ownership and the lease exactly where they were.
        """

        if not result.state.finalized:
            return
        if self._release_paper_ownership_if_proven(result) is not None:
            return
        self.session_finalized.emit()

    def _release_paper_ownership_if_proven(
        self, result: PaperSessionResult
    ) -> str | None:
        """Release the service slot and the lease, or say why neither was released.

        ``None`` means ownership was provably given up; a string is the reason it was not,
        for the shutdown dialog.  The order is the constraint, and it is *two-phase on the
        slot* because the workflow's side cannot be undone:

        1. the result must exist and must report itself finalized -- a disconnect alone
           proves nothing about whether the session finished;
        2. the broker must be flat and the journal must have no unreconciled row, read
           *before* the disconnect, because those are the facts that make giving up the
           session context safe.  A position or an unreconciled order means the operator
           still needs the session and the ownership survives;
        3. the disconnect, so nothing holds a socket the proof just certified;
        4. **``reserve_active_release``** -- prove the slot can be released and lock it,
           *before* the workflow is asked anything.  This is the step that makes the
           release safe in the one direction that matters: ``finalize_if_safe`` is a
           check-and-commit call on a canonical owner that may not be changed, so by the
           time it answers ``True`` the PAPER lease is already gone.  Asking it first
           would leave a slot that can still refuse to be dropped as the only thing
           between the operator and a session holding no lease at all -- E1's
           ownerless-session failure, reached from the other end;
        5. ``finalize_if_safe`` -- the workflow's own gate, and the only thing allowed to
           release the PAPER lease.  A refusal there cancels the reservation, which drops
           nothing, so the slot ends up exactly as it was found;
        6. ``commit_active_release`` last, and total: everything that could have
           invalidated it was refused while the reservation stood.
        """

        if not result.state.finalized:
            return "the session does not report itself finalized"
        session_id = getattr(result.engine_snapshot, "session_id", None)
        if not self._paper_trading.has_order_service():
            # Nothing is held here, so there is no slot to reserve -- but the workflow's
            # own gate still has to agree, because it is the only thing that may release
            # PAPER.
            if not self._workflow.finalize_if_safe():
                return "the workflow refused to release the Paper lease"
            return None
        broker_state = self._paper_trading.broker_state()
        if getattr(broker_state, "positions", ()):
            return "the broker still reports positions"
        if any(
            not getattr(row, "reconciled", False)
            for row in self._reconciliation_rows_provider(str(session_id))
        ):
            return "the order journal still has unreconciled rows"
        self._paper_trading.disconnect()
        try:
            reservation = self._paper_trading.reserve_active_release()
        except PaperTradingLifecycleError as error:
            # The slot cannot be accounted for, so the lease must not be touched either.
            # This is the whole point of reserving first: a promotion claim that still
            # holds the slot refuses *here*, while nothing has happened yet.
            return str(error)
        if not self._workflow.finalize_if_safe():
            # Give the lock back: nothing was dropped, so the slot is held and unlocked
            # exactly as it was found.
            if not self._paper_trading.cancel_active_release(reservation):
                return "the reserved release could not be given back"
            return "the workflow refused to release the Paper lease"
        try:
            self._paper_trading.commit_active_release(reservation)
        except PaperTradingLifecycleError as error:
            # Unreachable while the reservation locks the slot, and reported rather than
            # swallowed: what it would leave behind is the single state this ordering
            # exists to prevent, and an ordinary "ownership cannot be proved" refusal
            # would describe it as the wrong kind of problem.
            self._report_release_invariant(error)
            return str(error)
        return None

    def _report_release_invariant(self, error: Exception) -> None:
        """Report a release that could not be committed after the lease was already gone.

        A **bug report, not a state report**, like E1's published-session invariant: the
        two-phase release makes this unreachable, and the situation it describes -- PAPER
        released while an ownership is still held -- is different for the operator from an
        unfinished claim, so it is filed under its own code instead of being folded into
        the ordinary refusal.
        """

        message = PAPER_RELEASE_INVARIANT_MESSAGE.format(error=error)
        self.log_requested.emit(message)
        self.runtime_event_requested.emit(
            PaperRuntimeEventRequest(
                severity="error",
                component=PAPER_EXECUTION_COMPONENT,
                code=PAPER_RELEASE_INVARIANT_CODE,
                message=message,
            )
        )

    # -- shutdown ----------------------------------------------------------

    def prepare_shutdown(self) -> PaperShutdownResult:
        """Decide what a close must do about the session, from the canonical phase.

        The classification starts from the **workflow's phase**, never from
        "``result is None`` means nothing is owned".  That inference is the trap the
        previous shape fell into: ``CONNECTING`` legitimately has no result and a held
        PAPER lease, and a launch that published nothing has left a slot that may already
        hold a connected candidate -- so reading "no result" as "nothing to settle" would
        let a close walk straight past an owned session.

        Three questions, in the order that keeps each answer cheap and safe:

        * **is the session still going?**  ``RUNNING``/``PAUSED`` have an automatic route,
          and it is this capability's own :meth:`stop` -- asking the workflow for a second
          stop here would be a second sequencing of the same request.  What the stop
          produced is then *re-read* rather than assumed: the same call can halt the
          session outright, and "waiting for finalization" would be a lie about a session
          that has no automatic route left;
        * **can only the operator leave it?**  ``HALTED``, ``RECONCILING`` and
          ``RECONCILING_READY`` are the three phases no automatic route reaches, and every
          step out of them is a task.  Nothing here confirms a recovery on the operator's
          behalf;
        * **is an attempt or an ownership still outstanding?**  ``CONNECTING`` is refused
          outright, and everything else is settled by asking whether a slot is held and
          whether it can be proved releasable.

        Fail-closed throughout: no ownership is forced, no lease is released and no
        ``PaperTradingLifecycleError`` is swallowed to let the process exit.
        """

        phase = self._workflow.phase
        if phase in {PaperWorkflowPhase.RUNNING, PaperWorkflowPhase.PAUSED}:
            self.stop()
            return self._shutdown_verdict_after_the_stop()
        if phase is PaperWorkflowPhase.STOPPING:
            return PaperShutdownResult(
                PaperShutdownDisposition.WAITING_FOR_FINALIZATION,
                SHUTDOWN_FINALIZATION_PENDING_MESSAGE,
            )
        if queries.manual_recovery_phase(phase):
            return PaperShutdownResult(
                PaperShutdownDisposition.MANUAL_RECOVERY_REQUIRED,
                SHUTDOWN_MANUAL_RECOVERY_MESSAGE,
            )
        if phase is PaperWorkflowPhase.CONNECTING:
            # A launch attempt owns the PAPER lease and may already hold a connected
            # candidate, so this is not a state a close may pass through -- and the fact
            # that the workflow holds no *result* yet says nothing about what is owned.
            return self._ownership_blocked(SHUTDOWN_LAUNCH_IN_FLIGHT_REASON)
        # ``IDLE`` / ``PREPARING`` / ``READY`` / ``FINALIZED``: no session is being
        # started and none is waiting on a human, so what is left to settle is ownership.
        return self._ownership_verdict()

    def _shutdown_verdict_after_the_stop(self) -> PaperShutdownResult:
        """Classify the close again from what the stop actually produced.

        An orderly stop is not a phase edit.  The same call can halt the session -- a
        stale BUY that cannot be cancelled, an aged protective SELL, unsafe health -- or
        finalize it outright, and the operator has to be told which of those happened
        rather than "waiting for finalization" for a session nothing will finalize.
        """

        phase = self._workflow.phase
        if queries.manual_recovery_phase(phase):
            return PaperShutdownResult(
                PaperShutdownDisposition.MANUAL_RECOVERY_REQUIRED,
                SHUTDOWN_MANUAL_RECOVERY_MESSAGE,
            )
        if phase is PaperWorkflowPhase.STOPPING:
            return PaperShutdownResult(
                PaperShutdownDisposition.WAITING_FOR_FINALIZATION,
                SHUTDOWN_STOP_REQUESTED_MESSAGE,
            )
        if phase in {PaperWorkflowPhase.RUNNING, PaperWorkflowPhase.PAUSED}:
            # The stop was refused and the session is still trading: nothing may be
            # released, and the close must not proceed past it either.
            return self._ownership_blocked(SHUTDOWN_STOP_REFUSED_REASON)
        # A fast, clean stop can reach ``FINALIZED`` in the same call, having already
        # released the ownership through the ordinary result path -- so decide from
        # ownership, exactly as a close that arrived in that phase would.
        return self._ownership_verdict()

    def _ownership_verdict(self) -> PaperShutdownResult:
        """``READY`` only once every Paper ownership has been given up.

        Three owners, not one, and the gate is the *absence of all three* -- a candidate, the
        active order-service slot, and the execution lease.  The old shape short-circuited on
        "no active service", which is not the same claim at all: ``PaperTradingService`` owns
        a candidate slot as well, and E1's discard path leaves one tracked while the plan is
        rejected and the lease released, so a close could walk away from a live broker
        connection and still be told ``READY``.

        The lease is the final hard condition on purpose, and it is what makes ``READY`` mean
        "this capability has no unexplained ownership": it is the one piece of ownership that
        outlives every other release, and only the workflow's own gate may hand it back.  So
        when a slot is still held -- or when only the lease is -- the sequencing helper runs,
        which is also where the broker evidence is proved and the workflow is asked.
        """

        if self._paper_trading.has_candidate_ownership():
            return self._ownership_blocked(SHUTDOWN_CANDIDATE_OWNERSHIP_REASON)
        holds_a_slot = self._paper_trading.has_order_service()
        # Only *Paper's own* lease counts.  The lease manager is shared with Shadow, and
        # ``workflow.lease`` answers with whichever of the two holds it -- so asking "is the
        # shared lease free?" would make a perfectly healthy Shadow session block every
        # Paper close (and the Shadow teardown that follows it in ``closeEvent`` would never
        # run).  Shadow's lease is Shadow's to release, in its own step.
        holds_the_lease = self._workflow.lease is ExecutionLease.PAPER
        if not holds_a_slot and not holds_the_lease:
            return PaperShutdownResult(PaperShutdownDisposition.READY)
        result = self._workflow.result
        if result is None:
            # Ownership is held with no result to prove anything about: a launch fault left
            # something behind.  E1's invariant, reported rather than unwound.
            return self._ownership_blocked(SHUTDOWN_UNPROVABLE_SESSION_REASON)
        reason = self._release_paper_ownership_if_proven(result)
        if reason is not None:
            return self._ownership_blocked(reason)
        self.session_finalized.emit()
        return PaperShutdownResult(PaperShutdownDisposition.READY)

    @staticmethod
    def _ownership_blocked(reason: str) -> PaperShutdownResult:
        return PaperShutdownResult(
            PaperShutdownDisposition.OWNERSHIP_BLOCKED,
            SHUTDOWN_OWNERSHIP_BLOCKED_WITH_REASON_MESSAGE.format(reason=reason),
        )

    def _announce_manual_recovery_if_required(self) -> None:
        """Say plainly when the session has no automatic route left.

        Read from the workflow's phase rather than from a flag, and fired whenever the
        phase is one of the manual-recovery three -- not only on the *transition* into
        them, because nothing here keeps a previous phase to diff against, and a previous
        phase would be a second copy of the state this class refuses to mirror.  A
        listener therefore has to be idempotent, which is the right contract anyway: the
        operator's situation is the same on every emission.
        """

        if not queries.manual_recovery_phase(self._workflow.phase):
            return
        self.manual_recovery_required.emit()

    def _arm_and_publish(
        self, candidate_id: str, request: PaperLaunchRequest
    ) -> None:
        """Validate the reading, arm, take the promotion, publish, and end the claim.

        One sequence, here rather than buried in the injected build seam: ``arm`` ->
        ``reserve_candidate_promotion`` -> ``publish_armed`` ->
        ``commit_candidate_promotion``.

        **The promotion is taken before publication, not after it.**  The order-service
        owner installs the candidate as the active service *and* locks the slot in that
        one call, so the workflow cannot reach ``RUNNING`` without an owner: between
        here and the commit there is no step that could leave a published session whose
        order port -- already the coordinator's -- belongs to nobody the recovery paths
        can find.  The retired shape checked promotability, published, and promoted
        afterwards, and a refusal in that last step stranded exactly such a session:
        ``RUNNING``, PAPER held, a published coordinator holding an armed channel, and
        ``has_order_service()`` false, so every recovery path returned early.

        **Publication still splits the failure handling in two**, but the boundary now
        concerns the launch's *claim* rather than the ownership.  A raise at or before
        ``publish_armed`` is a rollback: cancel the reservation (which restores the
        candidate exactly as it was found), dispose it, reject the matching plan and
        release PAPER.  Once ``publish_armed`` returns there is nothing left to roll
        back -- only a claim to end -- and cancelling instead would be the unsafe edit.
        """

        reservation: PaperPromotionReservation | None = None
        try:
            # Borrowed for this call stack only: never assigned to an attribute, never
            # kept past the promotion.  A stored handle would be a second owner.
            service = self._paper_trading.candidate_service(candidate_id)
            broker_state = service.broker_state()
            refusal = queries.validate_broker_state(broker_state)
            if refusal is not None:
                raise _LaunchRefused(refusal)
            reading = queries.account_reading(
                broker_state,
                account_alias=service.connection_snapshot().account_alias,
            )
            built = self._build_session(request, service, reading)
            # Explicit, and this sequence's: a step hidden in the injected callable
            # could not be ordered against publication by a guard.
            service.arm(
                session_id=built.session_id,
                allowed_symbols=request.candidate_symbols,
                max_order_notional=built.max_order_notional,
                sellable_quantities={},
            )
            # Ownership is taken here, and kept only if publication then succeeds --
            # which is what the cancel below is for, not an afterthought.
            reservation = self._paper_trading.reserve_candidate_promotion(candidate_id)
            result = self._workflow.publish_armed(
                request.plan,
                engine=built.engine,
                orders=built.orders,
                health_evaluator=self._health_evaluator,
                candidate_symbols=frozenset(request.candidate_symbols),
            )
        except Exception as error:  # noqa: BLE001 - nothing is published yet
            if reservation is not None:
                # Always first: the slot is this launch's to give back, and the
                # discard below cannot even see a candidate that is still installed.
                # ``cancel`` is the one call in this sequence that must not raise --
                # see its own note -- so its answer arrives as a return value, and a
                # `False` has to stop the rollback rather than merely be discarded.
                if not self._paper_trading.cancel_candidate_promotion(reservation):
                    self._fail_to_release_promotion(error)
                    return
            self._discard_candidate(
                candidate_id,
                request,
                ARMING_FAILED_MESSAGE.format(error=error),
                show_message=True,
            )
            return
        # Both failure paths above returned, so this is always a real reservation.
        # Asserted rather than defaulted, so a later reordering of this method cannot
        # quietly skip ending the claim and leave the slot spoken for.
        assert reservation is not None
        try:
            self._paper_trading.commit_candidate_promotion(reservation)
        except PaperTradingLifecycleError as error:
            # Caught *by name*, and by the type that lives beside the port rather than
            # inside the service, because a refusal here is the reservation machinery
            # disagreeing with itself rather than an operator condition -- and because
            # an exception leaving a Qt slot is reported nowhere at all.
            self._fail_after_publication(error)
            return
        # The launch's own result goes out through the same path every later tick will
        # use, so "how is a Paper result published?" has one answer and one code path
        # from the first ``RUNNING`` to the last ``FINALIZED``.
        self._publish_result(result)
        self.runtime_event_requested.emit(
            PaperRuntimeEventRequest(
                severity="warning",
                component=PAPER_LAUNCH_COMPONENT,
                code=PAPER_ARMED_CODE,
                message=ARMED_EVENT_MESSAGE.format(
                    session=built.session_id[:8],
                    candidates=built.candidate_count,
                ),
            )
        )

    def _fail_after_publication(self, error: Exception) -> None:
        """Report a published launch that could not end its promotion claim.

        A **bug report, not a state report**.  Taking the promotion before publication
        is what makes the session live *and* owned whatever happens next, and the commit
        that only ends the claim has no failure left that this sequence can reach.  So
        nothing here is a launch failure and nothing here may unwind: what a stuck claim
        costs is the *next* launch, because the slot stays spoken for and every later
        reservation is refused.  That is fail-closed, and it is still a defect worth the
        operator's attention, so it is reported at error severity under its own code.

        Nothing is rolled back, deliberately: ``reject_connecting`` is a no-op outside
        ``CONNECTING``, discarding the candidate would drop the owner of a running
        session, and releasing PAPER would un-enforce the Shadow/Paper mutex while a
        session is live.
        """

        message = PAPER_PROMOTION_INVARIANT_MESSAGE.format(error=error)
        self.log_requested.emit(message)
        self.runtime_event_requested.emit(
            PaperRuntimeEventRequest(
                severity="error",
                component=PAPER_LAUNCH_COMPONENT,
                code=PAPER_PROMOTION_INVARIANT_CODE,
                message=message,
            )
        )
        self.refused.emit(PAPER_PROMOTION_INVARIANT_TITLE, message)

    def _fail_to_release_promotion(self, error: Exception) -> None:
        """Fail closed when a refused publication could not give the promotion back.

        The mirror of :meth:`_fail_after_publication`, on the other side of publication.
        There, the session was live and owned so nothing could be unwound; here nothing
        was published at all, so the attempt simply **stays in flight**.

        ``cancel_candidate_promotion`` answering that it released nothing means the
        ownership cannot be shown to have returned to the candidate slot.  Completing
        the rollback anyway would dispose of a service that may still hold the slot,
        reject the plan and release PAPER -- the shared lease with Shadow -- so an armed
        channel could survive while the workflow reports ``READY`` and nothing excludes
        Shadow.  Measured on the real wiring before this guard existed: ``READY``,
        ``lease NONE``, the active owner still held, the armed channel still alive, and
        the only operator-visible line was an ordinary "launch failed".

        So nothing is rolled back here either: the plan stays bound, the candidate stays
        registered, PAPER stays held, and the operator is told under a code of its own --
        distinct from the published-session invariant, because a stuck launch is a
        different situation from an ownerless live session.  A stuck launch is visible
        and actionable; a silently free lease protecting nothing is not.
        """

        message = PAPER_LAUNCH_ROLLBACK_MESSAGE.format(error=error)
        self.log_requested.emit(message)
        self.runtime_event_requested.emit(
            PaperRuntimeEventRequest(
                severity="error",
                component=PAPER_LAUNCH_COMPONENT,
                code=PAPER_LAUNCH_ROLLBACK_CODE,
                message=message,
            )
        )
        self.refused.emit(PAPER_LAUNCH_ROLLBACK_TITLE, message)

    # -- rejection -------------------------------------------------------

    def _discard_candidate(
        self,
        candidate_id: str,
        request: PaperLaunchRequest,
        message: str,
        *,
        show_message: bool,
    ) -> None:
        """Dispose one candidate and reject only the attempt it belongs to.

        The rejection runs in a ``finally`` so a failing disconnect can never strand
        ``CONNECTING`` holding the lease; the error still propagates and the service
        keeps ownership of a candidate it could not dispose.  ``reject_connecting``
        returning ``True`` is the retired ``_reset_auto_launch_controls`` predicate, so
        presenting and repainting are gated on it: a stale callback must not clear a
        newer attempt's arm confirmation or raise a dialog about a launch that moved on.
        The log line is not gated, so the operator still sees the result was ignored.
        """

        try:
            if self._paper_trading.has_candidate(candidate_id):
                self._paper_trading.discard_candidate(candidate_id)
        finally:
            current = self._workflow.reject_connecting(request.plan)
            if current:
                self._clear_arm_confirmation()
                self._render_launch_state()
            self.log_requested.emit(message)
            if current and show_message:
                self.refused.emit(LAUNCH_FAILED_TITLE, message)

    def _reject_without_candidate(
        self, request: PaperLaunchRequest, message: str
    ) -> None:
        """Finish a failed connect that produced no candidate at all.

        A stale one changes nothing: it must not reset the newer attempt's controls nor
        raise a dialog about a launch that already moved on.
        """

        current = self._workflow.reject_connecting(request.plan)
        if current:
            self._clear_arm_confirmation()
            self._render_launch_state()
        self.log_requested.emit(message)
        if current:
            self.refused.emit(LAUNCH_FAILED_TITLE, message)


class _LaunchRefused(Exception):
    """One broker gate failed; carried to the rejection path above."""


def _unpack(result: object) -> tuple[str, PaperLaunchRequest, str | None]:
    """Validate the connect callback's shape, loudly.

    A wrong shape means the task boundary and this module disagree, which is a
    programming error rather than an operator condition: it is raised so the launch
    cannot continue with a half-understood result."""

    try:
        candidate_id, request, connection_error = result  # type: ignore[misc]
    except (TypeError, ValueError) as error:
        raise TypeError(BAD_CALLBACK_MESSAGE) from error
    if not isinstance(request, PaperLaunchRequest):
        raise TypeError(BAD_CALLBACK_MESSAGE)
    return candidate_id, request, connection_error


__all__ = ["PaperOrchestrator"]
