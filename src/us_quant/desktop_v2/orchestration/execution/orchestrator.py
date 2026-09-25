"""Execution / AutoQuant orchestration: the route's desktop owner.

Before this module the AutoQuant route was the largest remaining block of
route-specific orchestration in ``MainWindow``: the retained candidate
shortlist, the local launch-busy flag, the order-channel probe flag, the
preflight, the two-step candidate preparation (scan, adopt, schedule history,
select), the channel probe, the execution page's control state and the whole
session render.  G2-B moves all of it here.

The boundary that matters most:

* **the route's mutable state is route-local and small.**  A launch-busy flag,
  a probe-in-flight flag and the retained shortlist.  Everything else -- the
  market snapshot, the account portfolio, Paper's presentation, the scan, the
  universe, the strategy catalogue -- is read through a provider *on every use*.
  A guard fails on a second copy of any of them.
* **the shortlist is the one retained candidate fact.**  ``candidates`` is the
  single desktop shortlist: the candidate table, the market readiness inputs and
  the Paper launch all read this one tuple.  ``ScannerOrchestrator.scan`` stays
  the scan's canonical owner and is never copied here.
* **Paper keeps its lifecycle.**  This class never acquires or releases a lease,
  never promotes a broker candidate, never reads reconciliation evidence and
  never compares a workflow phase.  The three transitions it needs --
  begin / cancel / mark-ready preparation -- go through narrow delegated seams on
  ``PaperOrchestrator``, which stay pure proxies of the canonical workflow.  This
  package imports no Paper type at all.
* **the page has one orchestration caller.**  This class is the only caller of
  ``ExecutionPage.render`` / ``render_candidates`` / ``render_context`` /
  ``render_preflight`` / ``render_execution_health`` / ``set_control_state`` /
  ``set_strategy_options`` / ``set_arm_confirmed``.  The window only constructs
  the page and hands it the palette.

What it deliberately does not own:

* **the Market interlock.**  A start, a switch, a subscription and a stop are
  *requested*; the window applies the Paper and Shadow gates and calls the
  market capability, because only composition may name two capabilities at once.
  The stop is the sharpest case: this route does not read ``Paper``'s runtime
  obligations at all, so it cannot know whether a stop is allowed -- it asks, and
  the window answers with a refusal or a completed stop for it to present.
* **dialogs and the shell.**  Every refusal and every log line is requested.
  The launch confirmation in particular is asked for and answered back, so the
  operator's consent is collected by the window while the *decision* stays here.
* **concrete construction.**  The IBKR order-channel config, the order
  repository, the risk authority, the execution application and the trading
  runtime are built by the composition root and reached through narrow callables.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Callable

from PySide6.QtCore import QObject, Signal

from us_quant.desktop_v2.orchestration.execution import queries
from us_quant.desktop_v2.orchestration.execution.models import (
    BROKER_RESOURCE_GROUP,
    CHANNEL_BUSY_MESSAGE,
    CHANNEL_BUSY_TITLE,
    CHANNEL_OK_HEALTH_PREFIX,
    CHANNEL_OK_LOG_PREFIX,
    CHANNEL_PROGRESS_MESSAGE,
    CHANNEL_START_MESSAGE,
    CONFIRM_MESSAGE,
    CONFIRM_TITLE,
    FRESH_CAPITAL_MESSAGE,
    FRESH_CAPITAL_TITLE,
    HISTORY_SCHEDULED_MESSAGE,
    INSUFFICIENT_CANDIDATES_TITLE,
    LAUNCH_IN_FLIGHT_MESSAGE,
    LAUNCH_IN_FLIGHT_TITLE,
    MINIMUM_CANDIDATES,
    PREPARATION_REFUSED_TITLE,
    PREPARING_SUMMARY,
    PREPARE_PROGRESS_MESSAGE,
    PREPARE_START_MESSAGE,
    SAFE_END_HEALTH,
    SCAN_RESOURCE_GROUP,
    SESSION_RUNNING_MESSAGE,
    SESSION_RUNNING_TITLE,
    STOP_STREAM_BLOCKED_MESSAGE,
    STOP_STREAM_BLOCKED_TITLE,
    STOP_STREAM_SUMMARY,
    UNIVERSE_MISSING_MESSAGE,
    UNIVERSE_MISSING_TITLE,
    CandidateSelection,
    ExecutionProviders,
    MarketReadinessFact,
    PaperFactsPort,
    SubmitTask,
)
from us_quant.desktop_v2.pages.execution.presenter import (
    build_candidates_view,
    control_state,
)
from us_quant.desktop_v2.pages.execution.projector import (
    AUDIT_ROW_LIMIT,
    LATENCY_ROW_LIMIT,
    RECONCILIATION_ROW_LIMIT,
    build_session_view,
)
from us_quant.desktop_v2.pages.strategy import strategy_option_label
from us_quant.extended_hours import paper_order_routing, us_equity_session
from us_quant.intraday_universe import select_paper_rotation_rows
from us_quant.trading.application.strategy_selection import (
    StrategySelectionError,
    StrategySelectionPurpose,
    StrategySelectionService,
)
from us_quant.trading.runtime.models import AutoQuantCandidate
from us_quant.trading.runtime.preflight import (
    AutoQuantPreflight,
    calculate_quote_readiness_breakdown,
    evaluate_auto_quant_preflight,
)

class ExecutionOrchestrator(QObject):
    """Owns the desktop execution / AutoQuant route and renders its page."""

    #: The route wants the operator's launch consent (title, message).  The
    #: window shows the question and answers through :meth:`confirm_start`;
    #: consent is presentation, the decision is not.
    start_confirmation_requested = Signal(str, str)

    #: The operator accepted, the arm flag is written and Paper may be asked to
    #: start.  The window bridges this to ``paper_orchestrator.start()``: this
    #: class must not name the Paper capability.
    paper_start_requested = Signal()

    #: Market commands, as *requests*: the window applies the Paper / Shadow
    #: interlocks and reaches the market capability itself.  There are four of
    #: them and there will not be a fifth smuggled in as a provider field -- a
    #: stop is refused while a Paper session has obligations, and this route may
    #: not even *ask* that question, let alone answer it.
    market_start_requested = Signal()
    market_switch_requested = Signal(str)
    market_subscription_requested = Signal(object)
    market_stop_requested = Signal()

    #: The cross-domain symbols the market readiness card must classify.  Emitted
    #: as a finished fact so the window turns it into a market input without
    #: recomputing either half.
    market_readiness_inputs_changed = Signal(object)

    #: Dialogs the window shows.  The capability may not import a widget.
    information_requested = Signal(str, str)
    warning_requested = Signal(str, str)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    def __init__(
        self,
        *,
        page: object,
        selection: StrategySelectionService,
        paper: PaperFactsPort,
        providers: ExecutionProviders,
        submit_task: SubmitTask,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._page = page
        self._selection = selection
        self._paper = paper
        self._providers = providers
        self._submit_task = submit_task
        # The three route-local facts.  Nothing else about the route is stored:
        # a market snapshot, an account portfolio, a Paper presentation, a scan
        # or a universe kept here would be a second truth.
        self._launch_busy = False
        self._channel_probe_inflight = False
        self._candidates: tuple[AutoQuantCandidate, ...] = ()

    # -- the retained shortlist -----------------------------------------

    @property
    def candidates(self) -> tuple[AutoQuantCandidate, ...]:
        """The one retained AutoQuant shortlist on the desktop.

        Consumed by the candidate table, the market readiness inputs, the Paper
        launch and the session view.  There is deliberately no window alias.
        """

        return self._candidates

    @property
    def capital_limit(self) -> Decimal:
        """The operator's capital ceiling, read from the page when asked."""

        return self._page.capital_limit()

    def clear_arm_confirmation(self) -> None:
        """Clear the launch confirmation; Paper's narrow callback."""

        self._page.set_arm_confirmed(False)

    def render_launch_context(self, summary: str) -> None:
        """Draw one launch context line; Paper's narrow callback."""

        self._page.render_context(summary=summary)

    # -- runtime strategy selection -------------------------------------

    @property
    def current_strategy(self):
        """The canonical AUTO_ROTATION version, read from the service.

        Never from the combo: the widget is a view, and a route that read it
        would run a different version than the operator selected if a repaint
        ever failed.
        """

        return self._selection.selected(
            StrategySelectionPurpose.AUTO_ROTATION
        )

    def select_strategy(self, version_id: object) -> None:
        """Adopt the page's choice as the AUTO_ROTATION runtime selection.

        A refused selection is logged rather than silently kept -- it means the
        widget shows a version the policy no longer permits -- and it changes
        nothing else: the current selection stays whatever the service says.
        """

        if not version_id:
            return
        purpose = StrategySelectionPurpose.AUTO_ROTATION
        try:
            self._selection.select(purpose, str(version_id))
        except StrategySelectionError as error:
            self.log_requested.emit(
                f"{purpose} 运行选择未生效：{error}；运行时保持原版本"
            )

    def refresh_strategy_options(self) -> None:
        """Refill the combo from the selection service and repaint the preflight.

        The one place the execution combo is refilled.  It is cleared, refilled
        from the policy's options and aimed at the service's current selection,
        so it can never keep displaying a version the runtime will not use.
        """

        purpose = StrategySelectionPurpose.AUTO_ROTATION
        selected = self._selection.restore_or_default(purpose)
        self._page.set_strategy_options(
            [
                (strategy_option_label(version), version.version_id)
                for version in self._selection.options(purpose)
            ],
            selected.version_id if selected else None,
        )
        self.refresh_preflight()

    # -- preflight ------------------------------------------------------

    @property
    def current_preflight(self) -> AutoQuantPreflight:
        """The preflight, recomputed from live facts on every read.

        Nothing about it is cached: the canonical pure rules decide, and the
        inputs -- the AUTO_ROTATION version, the market snapshot, the shortlist,
        the reference symbols, fresh Paper capital, the capability preference,
        the arm confirmation and the open session -- are read here each time.
        """

        strategy = self.current_strategy
        eligible, detail = queries.strategy_eligibility(strategy)
        snapshot = self._providers.market_snapshot()
        readiness = calculate_quote_readiness_breakdown(
            snapshot if snapshot is not None else (),
            candidate_symbols=(row.symbol for row in self._candidates),
            reference_symbols=self._reference_symbols(),
            recently_ready_symbols=self._providers.recently_ready_symbols(),
        )
        return evaluate_auto_quant_preflight(
            capability_enabled=self._providers.paper_capability_enabled(),
            paper_confirmed=self._page.arm_confirmed(),
            strategy_eligible=eligible,
            strategy_detail=detail,
            candidate_count=readiness.candidate_count,
            realtime_ready_count=readiness.candidate_current_count,
            paper_capital=self._providers.fresh_paper_capital(),
            recent_ready_count=readiness.candidate_recent_count,
            minimum_realtime_quotes=queries.minimum_realtime_quotes(
                us_equity_session()
            ),
        )

    def refresh_preflight(self, *_args: object) -> None:
        """Republish the readiness inputs and redraw the preflight line.

        The reference symbols come from the selected version, so this is the
        moment they change and the market layer must be told before the card is
        read.
        """

        self._publish_readiness_inputs()
        tally = queries.preflight_tally(self.current_preflight)
        self._page.render_preflight(tally.ready, tally.total, tally.details)

    def _reference_symbols(self) -> tuple[str, ...]:
        strategy = self.current_strategy
        return queries.reference_symbols(
            strategy.parameters if strategy is not None else None
        )

    def _publish_readiness_inputs(self) -> None:
        self.market_readiness_inputs_changed.emit(
            MarketReadinessFact(
                candidate_symbols=tuple(
                    row.symbol for row in self._candidates
                ),
                reference_symbols=self._reference_symbols(),
            )
        )

    # -- context lines --------------------------------------------------

    def set_scope(self, text: str) -> None:
        """Draw the finished scope sentence the window composed."""

        self._page.render_context(scope=text)

    def refresh_extended_hours_status(self) -> None:
        """Draw the 5×24 Paper line for the current US equity session."""

        routing = paper_order_routing(
            extended_hours_enabled=self._providers.extended_hours_enabled()
        )
        self._page.render_context(
            session=queries.extended_hours_status_text(
                self._providers.extended_hours_enabled(), routing
            )
        )

    # -- controls -------------------------------------------------------

    def refresh_controls(self) -> None:
        """Publish the route's control state from the canonical facts.

        The interpretation of Paper's phase is the *capability's*:
        ``session_control_facts`` answers which session controls the canonical
        phase makes available, so no phase is compared here.  ``stream_running``
        and the launch lock are what disable 停止行情 under a live session --
        stopping the feed would starve the strategy of the quotes its exit gates
        read.
        """

        facts = self._paper.session_control_facts
        self._page.set_control_state(
            control_state(
                launch_locked=self.launch_locked(),
                session_running=facts.running,
                session_paused=facts.paused,
                reconcile_available=facts.reconcile_available,
                resume_ready=facts.resume_ready,
                stream_running=self._providers.market_is_live(),
            )
        )

    def launch_locked(self) -> bool:
        """Whether a launch attempt currently owns the route's inputs.

        True while a local preparation is in flight, while the channel probe is
        running, while a connection attempt is pending, and while a session or an
        order service exists.  The probe is checked as its own fact rather than
        through the local busy flag: it is released by the probe's own worker, so
        an unrelated task finishing cannot reopen the route mid-probe.

        Every Paper half comes from the capability's own narrow seams -- this
        class never reads a phase, and never inspects broker ownership.
        """

        return bool(
            self._launch_busy
            or self._channel_probe_inflight
            or self._paper.launch_attempt_in_flight
            or self._paper.order_service_held
            or self._paper.runtime_active
        )

    def _set_launch_busy(self, busy: bool) -> None:
        self._launch_busy = busy
        self.refresh_controls()

    # -- channel probe --------------------------------------------------

    def request_channel_check(self) -> None:
        """Probe the IBKR Paper order channel, once at a time.

        The probe owns the route while it runs, so a second request can only come
        from a path that ignored the disabled control: it returns without
        touching the first probe's flag.  A session that already holds the order
        channel needs no check at all, and saying so is the whole response.
        """

        if self._channel_probe_inflight:
            return
        if self._paper.order_service_held or self._paper.runtime_active:
            self.information_requested.emit(
                CHANNEL_BUSY_TITLE, CHANNEL_BUSY_MESSAGE
            )
            return
        self._channel_probe_inflight = True
        self.refresh_controls()
        started = self._submit_task(
            self._channel_probe_task,
            on_success=self._channel_probe_finished,
            on_failure=self._channel_probe_failed,
            start_message=CHANNEL_START_MESSAGE,
            resource_group=BROKER_RESOURCE_GROUP,
        )
        if not started:
            # This attempt never owned the route -- the broker group was busy or
            # the client is closing -- so only this attempt's own flag is
            # released.
            self._channel_probe_inflight = False
            self.refresh_controls()

    def _channel_probe_task(
        self, progress: Callable[[str], None]
    ) -> object:
        progress(CHANNEL_PROGRESS_MESSAGE)
        return self._providers.probe_order_channel()

    def _channel_probe_failed(self, _message: str) -> None:
        """Release the probe's own lock; the generic path then reports failure."""

        self._channel_probe_inflight = False
        self.refresh_controls()

    def _channel_probe_finished(self, result: object) -> None:
        # The detail is decoded before the flag is released, so a probe that
        # returned an unreadable object leaves the route closed rather than
        # silently reopening it on a result nobody can read.
        detail = queries.channel_detail(result)
        self._channel_probe_inflight = False
        self.refresh_controls()
        self._page.render_execution_health(
            CHANNEL_OK_HEALTH_PREFIX + detail
        )
        self.log_requested.emit(CHANNEL_OK_LOG_PREFIX + detail)

    # -- candidate preparation ------------------------------------------

    def request_prepare(self) -> None:
        """Build a new shortlist: refuse, mark PREPARING, then scan off-thread.

        The sequence is the contract.  A missing universe or a live session stops
        before anything is claimed; only then does the canonical workflow enter
        PREPARING, and only then is the route marked busy.  If the task is not
        admitted -- the scan group is busy, or the client is closing -- both are
        given back, so a refused attempt leaves nothing behind.
        """

        if self._providers.universe() is None:
            self.information_requested.emit(
                UNIVERSE_MISSING_TITLE, UNIVERSE_MISSING_MESSAGE
            )
            return
        if self._paper.runtime_active:
            self.information_requested.emit(
                SESSION_RUNNING_TITLE, SESSION_RUNNING_MESSAGE
            )
            return
        refusal = self._paper.begin_preparation()
        if refusal is not None:
            self.information_requested.emit(
                PREPARATION_REFUSED_TITLE, refusal
            )
            return
        self._set_launch_busy(True)
        self._page.render_context(summary=PREPARING_SUMMARY)
        # The scan is sized on the *research scenario* capital, not on Paper
        # cash: it is an affordability screen over the research pool, and the
        # shortlist it produces is sized again on fresh Paper capital below.
        research_capital = self._providers.research_scenario_capital()
        started = self._submit_task(
            lambda progress: self._prepare_task(progress, research_capital),
            on_success=self._preparation_finished,
            on_failure=self._preparation_failed,
            start_message=PREPARE_START_MESSAGE,
            resource_group=SCAN_RESOURCE_GROUP,
        )
        if not started:
            self._paper.cancel_preparation()
            self._set_launch_busy(False)

    def _prepare_task(
        self, progress: Callable[[str], None], research_capital: Decimal
    ) -> object:
        progress(PREPARE_PROGRESS_MESSAGE)
        # Execution-time read: the same timing rule the scanner's manual request
        # uses, so a universe refresh during the queue wait is picked up.
        return self._providers.run_market_scan(
            self._providers.universe(), research_capital
        )

    def _preparation_failed(self, _message: str) -> None:
        """Release PREPARING after an asynchronous scan failure."""

        self._cancel_preparation_if_active()
        self._set_launch_busy(False)

    def _preparation_finished(self, result: object) -> None:
        # The shape is asserted *before* the fact is handed over.  Adopting a
        # result the scanner cannot read would put a non-scan into the scan
        # truth and only fail later, inside the capability's own repaint --
        # after the truth had already been corrupted.
        queries.scan_counts(result)
        # The preparation path runs its own scan on purpose -- it is wired into
        # Paper PREPARING and its failure cleanup -- and then hands the finished
        # fact to the capability that owns scan truth.  The direction is
        # AutoQuant -> Scanner: the scanner never learns Paper exists, and no
        # manual "扫描完成" line is written because nobody clicked 扫描.
        self._providers.adopt_scan(result)
        scheduled = self._providers.schedule_history(
            self._providers.universe()
        )
        self._providers.refresh_history()
        if scheduled:
            self.log_requested.emit(
                HISTORY_SCHEDULED_MESSAGE.format(scheduled=scheduled)
            )
        self._build_shortlist()

    def _cancel_preparation_if_active(self) -> None:
        if self._paper.preparation_active:
            self._paper.cancel_preparation()

    def _build_shortlist(self) -> None:
        """Turn the adopted scan into the retained candidate shortlist.

        Every refusal below gives back *both* things the attempt claimed -- the
        workflow's PREPARING and the local busy flag -- and publishes no
        shortlist: a fake list would be armed and traded on.  The capital rule is
        the load-bearing one: sizing runs on fresh Paper cash, the operator's
        limit may only shrink it, and the research scenario figure is never a
        sizing input here.
        """

        universe = self._providers.universe()
        scan = self._providers.scan()
        if scan is None or universe is None:
            self._cancel_preparation_if_active()
            self._set_launch_busy(False)
            return
        limit = self._page.candidate_limit()
        paper_capital = self._providers.fresh_paper_capital()
        if paper_capital is None:
            self._cancel_preparation_if_active()
            self._set_launch_busy(False)
            self.information_requested.emit(
                FRESH_CAPITAL_TITLE, FRESH_CAPITAL_MESSAGE
            )
            return
        effective_capital = queries.bounded_capital(
            paper_capital, self._page.capital_limit()
        )
        references = self._reference_symbols()
        eligible = select_paper_rotation_rows(
            scan,
            universe,
            capital=effective_capital,
            max_position_fraction=(
                self._providers.maximum_position_exposure_pct()
            ),
            limit=limit,
            maximum_per_sector=queries.maximum_per_sector(limit),
            risk_multipliers=self._providers.exposure_multipliers(),
            liquidity_first=queries.rotation_liquidity_first(
                us_equity_session()
            ),
            excluded_symbols=references,
        )
        candidates = queries.build_candidates(eligible, references)
        if len(candidates) < MINIMUM_CANDIDATES:
            self._cancel_preparation_if_active()
            self.warning_requested.emit(
                INSUFFICIENT_CANDIDATES_TITLE,
                queries.insufficient_candidates_message(
                    len(candidates), MINIMUM_CANDIDATES
                ),
            )
            self._set_launch_busy(False)
            return
        self._candidates = candidates
        if self._paper.preparation_active:
            self._paper.mark_preparation_ready()
        scanned_count, skipped_count = queries.scan_counts(scan)
        selection = CandidateSelection(
            candidates=candidates,
            references=references,
            scanned_count=scanned_count,
            skipped_count=skipped_count,
            research_count=int(universe.summary()["research_eligible"]),
        )
        self._page.render_context(
            scope=queries.candidate_scope_text(
                research_count=selection.research_count,
                scanned_count=selection.scanned_count,
                skipped_count=selection.skipped_count,
                candidate_count=len(selection.candidates),
            ),
            summary=queries.candidate_summary_text(
                len(selection.candidates)
            ),
        )
        self.market_subscription_requested.emit(
            queries.stream_symbols(selection.symbols, references)
        )
        self._set_launch_busy(False)
        self.refresh_all()
        if self._providers.market_is_live():
            self._page.render_context(
                summary=queries.switching_summary_text(
                    len(selection.candidates)
                )
            )
            self.market_switch_requested.emit(
                self._providers.market_provider() or "finnhub_trades"
            )
            return
        self.market_start_requested.emit()

    def request_stop_stream(self) -> None:
        """Ask for the feed to be stopped.  Say nothing else.

        Whether a stop is *allowed* is not this route's question: stopping the
        feed under a live Paper session would strand its positions and orders,
        and stopping the internal Shadow book is a Shadow decision -- so the
        Paper and Shadow facts the answer depends on may only be read by
        composition.  This method therefore publishes one request and returns;
        the window reads those facts, applies the interlocks, calls the market
        capability, and hands the *outcome* back through
        :meth:`on_market_stop_refused` / :meth:`on_market_stopped`.
        """

        self.market_stop_requested.emit()

    def on_market_stop_refused(self) -> None:
        """Draw the refusal composition decided on.  Presentation only."""

        self.information_requested.emit(
            STOP_STREAM_BLOCKED_TITLE, STOP_STREAM_BLOCKED_MESSAGE
        )

    def on_market_stopped(self) -> None:
        """Draw a stop that actually happened.  Presentation only."""

        self._page.render_context(summary=STOP_STREAM_SUMMARY)

    # -- launch ---------------------------------------------------------

    def request_start(self) -> None:
        """Ask the operator to confirm a launch, or refuse a duplicate attempt.

        An attempt that is already in its connect step owns the route: asking
        again would collect consent for a launch that cannot begin.
        """

        if self._paper.launch_attempt_in_flight:
            self.information_requested.emit(
                LAUNCH_IN_FLIGHT_TITLE, LAUNCH_IN_FLIGHT_MESSAGE
            )
            return
        self.start_confirmation_requested.emit(CONFIRM_TITLE, CONFIRM_MESSAGE)

    def confirm_start(self, accepted: bool) -> None:
        """Record the operator's answer and hand the launch to Paper.

        Consent is not authorization: this writes the arm fact and requests the
        launch, and ``PaperOrchestrator.start()`` still runs the canonical
        preflight and every safety gate before anything is submitted.
        """

        if not accepted:
            self._page.set_arm_confirmed(False)
            return
        self._page.set_arm_confirmed(True)
        self.paper_start_requested.emit()

    # -- session render -------------------------------------------------

    def refresh_current(self) -> None:
        """Draw the route from live facts, assembling no read model here.

        What this method owns is *fetching*: the ambient route facts (quotes, the
        retained shortlist, the read-only account snapshot, the broker's own
        reading) and the journal rows the order tables read.  Everything that
        used to make the window a read-model assembler stays where v2O-E4 put it:

        * the session fact is ``paper_orchestrator.presentation`` -- the
          capability's retained, immutable projection -- and it is read at render
          time, never stored.  It is for **display only**: no launch gate, no
          market or Shadow interlock, no lifecycle decision may consult it.
        * which broker holdings belong to the session, how the pending orders are
          keyed and which journal rows are the session's are pure conversions
          living in ``pages/execution/projector.py``.

        The candidate table is drawn first and on its own, because it has content
        before any session does: the operator approves a shortlist and only then
        arms it, so a route that waited for a session would show an empty table
        at exactly the moment the shortlist is the thing being approved.
        """

        stream = self._providers.market_snapshot()
        quotes = {
            quote.symbol: quote
            for quote in (stream.quotes if stream is not None else ())
        }
        candidates = self._candidates
        self._page.render_candidates(
            build_candidates_view(
                candidates=candidates,
                quotes=quotes,
                recently_ready=self._providers.was_recently_ready,
            )
        )
        session = self._paper.presentation
        if session is None:
            return
        session_id = session.session_id
        portfolio = self._providers.account_portfolio()
        self._page.render(
            build_session_view(
                session=session,
                account=(
                    portfolio.account if portfolio is not None else None
                ),
                broker_state=self._providers.broker_state(),
                quotes=quotes,
                candidates=candidates,
                reconciliations=(
                    self._providers.reconciliation_rows(
                        session_id, RECONCILIATION_ROW_LIMIT
                    )
                    if session_id
                    else ()
                ),
                audit_rows=self._providers.audit_rows(AUDIT_ROW_LIMIT),
                latency=self._providers.latency_rows(
                    session_id, LATENCY_ROW_LIMIT
                ),
                recently_ready=self._providers.was_recently_ready,
            )
        )

    def refresh_all(self) -> None:
        """Repaint the three independent surfaces of the route."""

        self.refresh_current()
        self.refresh_preflight()
        self.refresh_extended_hours_status()

    # -- Paper publications ---------------------------------------------

    def on_paper_result_changed(self, _result: object) -> None:
        """Render one Paper result.  Presentation only.

        Nothing here decides anything about the session and nothing here stores
        anything about it: the snapshot this route draws is
        ``paper_orchestrator.presentation``, which the capability refreshed from
        the same result before publishing it.  A second cache taken here is how
        this route becomes a second truth owner about a Paper session -- and how
        the page blanks out when the canonical result is cleared.
        """

        self.refresh_current()
        self.refresh_controls()

    def on_paper_session_finalized(self) -> None:
        """Draw a Paper session that has safely ended.  Presentation only.

        The disconnect, the workflow's own release gate and ``clear_active`` all
        already happened inside the capability, in that order, so nothing here
        may repeat any of them -- an ownership that is gone could only be
        "released" again by corrupting the record of what happened.
        """

        self._page.render_execution_health(SAFE_END_HEALTH)
        self._page.set_arm_confirmed(False)
        self.refresh_controls()


__all__ = ["ExecutionOrchestrator"]
