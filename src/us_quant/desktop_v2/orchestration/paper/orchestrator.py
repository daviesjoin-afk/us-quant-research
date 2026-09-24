"""Paper launch orchestration: the one owner of starting a Paper AutoQuant session.

Sequence: READY -> CONNECTING -> (2nd preflight + identity revalidation) -> broker gate
-> build runtime -> arm -> reserve the promotion -> publish_armed -> commit -> RUNNING.
The active-session half -- polling, stream ingress, pause/resume, an orderly stop, halt
recovery, manual reconciliation, finalization -- is v2O-E2/E3 and named nowhere here.

Three rules matter more than the rest:

* **there is no second truth.**  The workflow owns the phase, the active plan and the
  lease; the order-service owner owns the candidate and active connections; the runtime
  owns the session.  This class mirrors none of them -- no ``_active_plan``, and the
  duplicate and staleness gates read ``workflow.phase``, which is equivalent to the
  retired ``_active_auto_launch_plan is not None`` and stays correct after publication,
  when the plan legitimately outlives the attempt;
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
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal
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
    PAPER_LAUNCH_COMPONENT,
    PAPER_LAUNCH_ROLLBACK_CODE,
    PAPER_LAUNCH_ROLLBACK_MESSAGE,
    PAPER_LAUNCH_ROLLBACK_TITLE,
    PAPER_PROMOTION_INVARIANT_CODE,
    PAPER_PROMOTION_INVARIANT_MESSAGE,
    PAPER_PROMOTION_INVARIANT_TITLE,
    PAPER_STRATEGY_INTEGRITY_CODE,
    PAPER_STRATEGY_INTEGRITY_TITLE,
    PREFLIGHT_CHANGED_MESSAGE,
    PREFLIGHT_PREFIX,
    PREFLIGHT_TITLE,
    SHADOW_ACTIVE_MESSAGE,
    SHADOW_ACTIVE_TITLE,
    STALE_PLAN_MESSAGE,
    PaperLaunchEvent,
    PaperLaunchIntegrityError,
    PaperLaunchPublication,
    PaperLaunchRequest,
    PaperOrderChannel,
    PaperSessionBuilder,
)
from us_quant.trading.application.paper.models import PaperTradingLifecycleError
from us_quant.trading.runtime.workflow_state import WorkflowStateError

if TYPE_CHECKING:
    from us_quant.trading.application.paper.models import PaperPromotionReservation
    from us_quant.trading.application.paper.service import PaperTradingService
    from us_quant.trading.runtime.models import AutoQuantCandidate
    from us_quant.trading.runtime.preflight import AutoQuantPreflight
    from us_quant.trading.runtime.workflow import PaperWorkflowController

    from us_quant.desktop_v2.orchestration.tasking import TaskSubmitter


class PaperOrchestrator(QObject):
    """Owns the Paper launch sequence and the facts it publishes about itself.

    It is *not* the workflow and *not* the order-service owner: it asks each for the
    step it owns and publishes what the outcome should look like.  Everything deciding
    what a Paper session *is* -- the phase machine, the lease, the arming rules, the risk
    verdict, order submission -- stays where it lived.
    """

    #: A start was refused before anything was built; the window shows the dialog.
    refused = Signal(str, str)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    #: A launch reached ``RUNNING``; the window adopts the runtime and renders.
    session_published = Signal(object)

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
        clear_arm_confirmation: Callable[[], None],
        render_launch_state: Callable[[], None],
        render_launch_context: Callable[[str], None],
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
        self._clear_arm_confirmation = clear_arm_confirmation
        self._render_launch_state = render_launch_state
        self._render_launch_context = render_launch_context
        # Desktop orchestration bookkeeping, not session truth: it only gives each
        # attempt a distinct identity, and keeps the retired integer-increment
        # semantics -- a UUID would change the candidate id on the wire for nothing.
        self._next_attempt = 0

    # -- lifecycle -------------------------------------------------------

    @property
    def _workflow(self) -> PaperWorkflowController:
        """Whichever controller is live -- see the getter's note in ``__init__``."""
        return self._workflow_getter()

    @property
    def _paper_trading(self) -> PaperTradingService:
        """Whichever order-service owner is live -- see the getter's note."""
        return self._paper_trading_getter()

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
                PaperLaunchEvent(
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

    # -- publication -----------------------------------------------------

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
        self.session_published.emit(
            PaperLaunchPublication(
                runtime=built.runtime,
                result=result,
                session_id=built.session_id,
                candidate_count=built.candidate_count,
            )
        )
        self.runtime_event_requested.emit(
            PaperLaunchEvent(
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
            PaperLaunchEvent(
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
            PaperLaunchEvent(
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
