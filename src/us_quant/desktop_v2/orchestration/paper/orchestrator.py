"""Paper launch orchestration: the one owner of starting a Paper AutoQuant session.

Before this module the launch was two ``MainWindow`` handlers -- ``_start_auto_quant``
and ``_auto_order_service_connected`` -- plus four helpers only they called.  The window
read the preflight, froze a launch plan into ``self._active_auto_launch_plan``, acquired
the PAPER lease through the workflow, submitted a broker-connect task, and then, in the
callback, decided whether the result was stale, re-ran the preflight, re-checked the
frozen identity, validated the broker reading, built the risk and execution authorities,
armed the channel, published the workflow and promoted the candidate.

Three rules matter more than the rest:

* **there is no second truth.**  ``PaperWorkflowController`` owns the phase, the active
  plan and the execution lease; ``PaperTradingService`` owns the candidate and active
  broker connections; ``TradingRuntime`` owns the session.  This class mirrors none of
  them -- it stores **no** ``_active_plan``.  The duplicate gate and every staleness
  decision read ``workflow.phase`` through :mod:`.queries`, which is exactly equivalent
  to the retired ``_active_auto_launch_plan is not None`` and stays correct after
  publication, when the plan legitimately outlives the attempt;
* **the arm/publish/promote order is a safety constraint.**  A successful broker connect
  is not permission for the session to own execution, so the order is ``arm`` ->
  ``ensure_candidate_can_promote`` -> ``publish_armed`` -> ``promote_candidate``, with the
  pure check placed before publication because the published session must never be left
  without an owner for a reason already knowable;
* **the candidate is borrowed, never stored.**  ``candidate_service`` is called for one
  callback's stack and its result lives in a local, never an attribute: a stored handle
  would be a second owner of the broker connection and a way for a late callback to reach
  a newer attempt.

What it does not own: the workflow transitions it merely *calls*; the generic task
lifecycle (``TaskThread``, the controller, the closing gate and the busy dialog stay on
the window, reached through an injected ``TaskSubmitter`` -- no ``asyncio``); widgets and
dialogs, which is why the confirmation step stays on the window and a refusal is
published as a payload; the session's composition, reached through the narrow build seam;
and the armed session's runtime -- polling, stream ingress, pause/resume, an orderly stop,
halt recovery, manual reconciliation and finalization are v2O-E2/E3 and named nowhere
here.
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
    PREFLIGHT_CHANGED_MESSAGE,
    PREFLIGHT_PREFIX,
    PREFLIGHT_TITLE,
    SHADOW_ACTIVE_MESSAGE,
    SHADOW_ACTIVE_TITLE,
    STALE_PLAN_MESSAGE,
    PaperLaunchEvent,
    PaperLaunchPublication,
    PaperLaunchRequest,
    PaperOrderChannel,
    PaperSessionBuilder,
)
from us_quant.trading.runtime.workflow_state import WorkflowStateError

if TYPE_CHECKING:
    from us_quant.trading.application.paper.service import PaperTradingService
    from us_quant.trading.runtime.models import AutoQuantCandidate
    from us_quant.trading.runtime.preflight import AutoQuantPreflight
    from us_quant.trading.runtime.workflow import PaperWorkflowController

    from us_quant.desktop_v2.orchestration.tasking import TaskSubmitter


class PaperOrchestrator(QObject):
    """Owns the Paper launch sequence and the facts it publishes about itself.

    It is *not* the workflow and *not* the order-service owner: it asks the first for
    transitions and the second for a candidate, and publishes what the outcome should
    look like.  Everything that decides what a Paper session *is* -- the phase machine,
    the lease, the arming rules, the risk verdict, order submission -- stays where it
    already lived.
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
        # both and the safety tests replace each to drive the halted, refused-close
        # and fake-broker paths, so every read here must resolve whichever is live
        # *now* rather than one captured at construction.
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
        # The attempt sequence.  Desktop orchestration bookkeeping, not trading or
        # session truth: it is only here to give each attempt a distinct identity, and
        # it deliberately keeps the retired integer-increment semantics.  A UUID would
        # change what a candidate id looks like on the wire for no benefit.
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

        The gate order is the retired handler's and it matters.  The **duplicate gate
        comes first**: a second attempt must be refused before anything is built, or
        the operator ends up with two candidates, two broker tasks and a second
        ``begin_connecting`` overwriting the live plan.  Then the Shadow/Paper mutex,
        then the local preflight -- all three *before* the lease is taken, so a refused
        start never holds PAPER and never creates a candidate.

        Every input is read once, at the moment the last gate passes, and frozen into
        the request.  The connect is submitted to the window's task infrastructure;
        :meth:`_connect_finished` then continues on the callback's stack.
        """

        if queries.launch_attempt_in_flight(self._workflow.phase):
            self.refused.emit(DUPLICATE_TITLE, DUPLICATE_MESSAGE)
            return
        if self._shadow_is_active():
            # The shared execution lease is what makes Shadow XOR Paper structural,
            # so this is a courtesy refusal with the operator's sentence -- the lease
            # would refuse the launch anyway, and would report it less clearly.
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
            # The preflight already refuses a missing strategy, so reaching here with
            # ``None`` means the selection changed between the two reads.  Refusing
            # rather than asserting keeps a race an operator condition, not a crash.
            self._clear_arm_confirmation()
            self.refused.emit(PREFLIGHT_TITLE, PREFLIGHT_PREFIX)
            return
        self._next_attempt += 1
        request = queries.freeze_launch(
            attempt_id=self._next_attempt,
            strategy=strategy,
            candidates=self._candidates_provider(),
            requested_capital_limit=self._capital_limit_provider(),
            order_channel=self._order_channel_provider(),
        )
        try:
            # The plan is bound and PAPER is acquired *before* the broker connect
            # starts: Shadow and Paper share one execution lease, so this ordering is
            # the structural mutex rather than a UI gate.
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
            # Not admitted -- the window is closing or the broker group is busy.  No
            # task was created, so this attempt must be unwound here: reject the
            # matching plan (which releases PAPER) and put the controls back.  Leaving
            # it would be a CONNECTING zombie holding the lease forever.
            self._workflow.reject_connecting(request.plan)
            self._render_launch_state()

    def _connect_finished(self, result: object) -> None:
        """Continue the launch after the async candidate connect returned.

        The five decisions here are the retired callback's, in its order, and each is a
        safety property rather than a convenience:

        * **the shape is checked loudly.**  A malformed result is a programming error;
          swallowing it with ``except Exception: return`` is how a launch silently stops
          without either an armed session or a released lease;
        * **a connect error ends only this attempt** -- no candidate, no active service
          and no newer attempt is touched;
        * **a stale plan disposes only its own candidate**, and cannot disconnect the
          active service, clear ownership or alter a newer attempt's plan, lease or
          phase.  ``reject_connecting`` returning ``False`` confirms the callback is
          stale, and every mutation of the plan is inside it;
        * **the preflight is re-run** -- market, account, strategy, candidate and capital
          inputs can all have changed while the broker was connecting, so the first pass
          is not evidence about the present;
        * **the frozen identity is revalidated**, so the attempt proceeds only if the
          plan still describes what the operator would confirm now.
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
        """Validate the broker reading, build the session, arm it, publish, promote.

        The order is fixed by safety.  A broker connect proves reachability, not
        permission: the candidate becomes the active owner of execution only after
        ``publish_armed`` succeeded, and ``ensure_candidate_can_promote`` runs *before*
        publication because a failure there -- an occupied active slot -- must be
        discovered while the session can still be cleanly abandoned.

        One failure path, deliberately: anything raised before publication discards
        this candidate and rejects the matching CONNECTING plan.  It cannot touch the
        active service, because nothing here ever assigned it -- once promotion
        succeeds there is no candidate left, so the ``except`` cannot tear down a live
        session.
        """

        try:
            # Borrowed for this call stack only.  Never assigned to an attribute,
            # never kept past promotion, never handed to another capability: a stored
            # handle would be a second owner of the broker connection.
            service = self._paper_trading.candidate_service(candidate_id)
            broker_state = service.broker_state()
            refusal = queries.validate_broker_state(broker_state)
            if refusal is not None:
                raise _LaunchRefused(refusal)
            reading = queries.account_reading(
                broker_state,
                account_alias=service.connection_snapshot().account_alias,
            )
            # The composition root builds the runtime, the risk authority and the
            # execution application over the *same* borrowed channel, then arms it.
            built = self._build_session(request, service, reading)
            # Pure check, no mutation: promotion after ``publish_armed`` must not be
            # able to fail for a reason already knowable here, so a published session
            # can never end up without an owner.
            self._paper_trading.ensure_candidate_can_promote(candidate_id)
            result = self._workflow.publish_armed(
                request.plan,
                engine=built.engine,
                orders=built.orders,
                health_evaluator=self._health_evaluator,
                candidate_symbols=frozenset(request.candidate_symbols),
            )
            self._paper_trading.promote_candidate(candidate_id)
        except Exception as error:  # noqa: BLE001 - every path rejects this attempt
            self._discard_candidate(
                candidate_id,
                request,
                ARMING_FAILED_MESSAGE.format(error=error),
                show_message=True,
            )
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

        The rejection runs in a ``finally`` so a failing broker disconnect can never
        leave the workflow stuck in ``CONNECTING`` holding the lease; the disconnect
        error itself still propagates, and ``PaperTradingService`` keeps ownership of a
        candidate it could not dispose rather than this layer forcing its map.

        ``reject_connecting`` returning ``True`` is the workflow confirming this callback
        was the *current* attempt -- exactly the retired ``_reset_auto_launch_controls``
        predicate.  Presenting and repainting are both gated on it, so a stale callback
        cannot clear a newer attempt's arm confirmation, repaint its controls, or raise a
        dialog about a launch that already moved on.  The log line is deliberately *not*
        gated: the operator should still see that a late result was ignored.
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

        ``reject_connecting`` reports whether this callback was the current attempt; a
        stale one changes nothing, so it must neither reset the controls the newer
        attempt owns nor raise a dialog about a launch that already moved on.
        """

        current = self._workflow.reject_connecting(request.plan)
        if current:
            self._clear_arm_confirmation()
            self._render_launch_state()
        self.log_requested.emit(message)
        if current:
            self.refused.emit(LAUNCH_FAILED_TITLE, message)


class _LaunchRefused(Exception):
    """One broker gate failed; carried to the single rejection path above."""


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
