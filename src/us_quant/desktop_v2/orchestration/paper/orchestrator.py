"""Paper orchestration: the one owner of a Paper session's whole run.

Launch: READY -> CONNECTING -> (2nd preflight + identity revalidation) -> broker gate
-> build runtime -> arm -> reserve the promotion -> publish_armed -> commit -> RUNNING.

Active session: market ingress, the watchdog poll, pause/resume and an orderly stop.
The remaining half -- halt recovery, manual reconciliation and finalization -- is
v2O-E3 and named nowhere here.

Three rules matter more than the rest:

* **there is no second truth.**  The workflow owns the phase, the active plan, the
  lease *and* the latest result; the order-service owner owns the candidate and active
  connections.  This class mirrors none of them -- no ``_active_plan``, no ``_result``,
  no ``_runtime`` -- and the duplicate and staleness gates read ``workflow.phase``,
  which is equivalent to the retired ``_active_auto_launch_plan is not None`` and stays
  correct after publication, when the plan legitimately outlives the attempt;
* **one result publication path.**  Every operation ends in :meth:`_publish_result`, so
  the launch's ``RUNNING`` and each later tick are published by the same code, and an
  event is requested exactly once per operation;
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
  an attribute -- a stored handle would be a second owner of the connection.

It does not own the workflow transitions it merely *calls*; the generic task lifecycle
(``TaskThread``, the controller, the closing gate and the busy dialog stay on the window,
reached through an injected ``TaskSubmitter`` -- no ``asyncio``); widgets and dialogs,
which is why the confirmation step stays on the window; or the session's composition.
The two seams it reads across the capability boundary -- the market fact a stop is
judged against, and whether a finalization task is in flight -- arrive as providers for
that reason, never as imports: Market is not a dependency of Paper, and the
finalization-inflight provider is a deliberately temporary v2O-E3 seam.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal
from time import monotonic
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

from us_quant.desktop_v2.orchestration.paper import queries
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
    PAPER_STRATEGY_INTEGRITY_CODE,
    PAPER_STRATEGY_INTEGRITY_TITLE,
    PAUSE_SUCCEEDED_MESSAGE,
    PREFLIGHT_CHANGED_MESSAGE,
    PREFLIGHT_PREFIX,
    PREFLIGHT_TITLE,
    RESUME_SUCCEEDED_MESSAGE,
    SHADOW_ACTIVE_MESSAGE,
    SHADOW_ACTIVE_TITLE,
    STALE_PLAN_MESSAGE,
    PaperLaunchIntegrityError,
    PaperLaunchRequest,
    PaperOrderChannel,
    PaperRuntimeEventRequest,
    PaperSessionBuilder,
)
from us_quant.trading.application.paper.models import PaperTradingLifecycleError
from us_quant.trading.runtime.workflow_state import WorkflowStateError

if TYPE_CHECKING:
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


class PaperOrchestrator(QObject):
    """Owns a Paper session's run: the launch sequence and the active-session loop.

    It is *not* the workflow and *not* the order-service owner: it asks each for the
    step it owns and publishes what the outcome should look like.  Everything deciding
    what a Paper session *is* -- the phase machine, the lease, the arming rules, the risk
    verdict, order submission, the latest result -- stays where it lived.

    It is a **sequencing owner**, not a second state owner.  What it keeps is only what
    nothing else can: the next attempt's sequence number, and the monotonic stamp of the
    last stream ingress the poll suppression reads.  Both are bookkeeping about *this
    object's own calls*; neither is a fact about the session, and every question about
    the session is answered by reading the workflow.
    """

    #: A start was refused before anything was built; the window shows the dialog.
    refused = Signal(str, str)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    #: The workflow's latest result, from any operation.  One emission per operation.
    result_changed = Signal(object)

    #: One runtime event the window should record.
    runtime_event_requested = Signal(object)

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
        finalization_inflight_provider: Callable[[], bool],
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
        # TEMPORARY -- removed by v2O-E3.  Active ingress has to know whether a
        # finalization task is in flight, and finalization is not this round's to own.
        # A provider keeps the flag on the window, where E3 will retire it; copying the
        # flag here would be a second owner of a lifecycle fact.
        self._finalization_inflight_provider = finalization_inflight_provider
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

        if self._finalization_inflight_provider():
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

        if self._finalization_inflight_provider():
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

    # -- publication -----------------------------------------------------

    def _publish_result(self, result: PaperSessionResult) -> None:
        """Publish one workflow result, and request its events exactly once.

        The capability's *only* result path.  Two things happen, in this order, and
        nothing else may emit either signal on a result's behalf:  the window is handed
        the result to render, and each event the operation produced is requested as one
        runtime-event write.  The window still owns the store -- this only says which
        events a Paper operation produces -- and it owns the rendering, so neither a
        result nor an event is ever recorded twice for one operation.
        """

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
