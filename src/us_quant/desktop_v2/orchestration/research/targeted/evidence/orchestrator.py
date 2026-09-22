"""Targeted evidence orchestration: the desktop owner of the research evidence.

Nine pieces of evidence state used to live on ``MainWindow`` -- seven result
lists, two selected run ids -- plus two one-shot integer fields that existed only
so a finished suite could switch tabs on the next repaint.  All nine moved here;
the two integers were replaced by the page's own semantic navigation.

This class owns the snapshot, the two requests, the two selection intents and the
one evidence render entry point, and nothing else.  :attr:`snapshot` is published
because a real cross-capability consumer exists -- the terminal export reads all
seven families -- so the boundary is one immutable value rather than seven
accessors.  ``BacktestOrchestrator``'s runs stay private because nothing reads
them; the rule is the consumer, not a taste for privacy.

Timing is the contract and is inherited unchanged.  The strategy version, the
target symbol and the research capital are frozen **now**, on the UI thread,
through three providers each read once per request; the worker re-reads none of
them, so editing the capital control while a run is queued cannot change the run
about to start.  The universe is read **now** too and only as an eligibility
gate: the task never reads it, because targeted research has never consumed the
universe and an execution-time re-read added "for consistency" with Cross Section
would change what a request means.  No universe is *not* a refusal.  The minute
evidence is read by the service, at execution time.

Not owned here: the task lifecycle (``submit_task``), the dialog
(:attr:`refused`), the event store (:attr:`runtime_event_requested`), the desktop
route (:attr:`focus_requested`), the minute-evidence status, and the Shadow
session.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import QObject, Signal

from us_quant.desktop_targeted_evidence_models import (
    TargetedEvidenceRunInputs,
    TargetedEvidenceRuntimeEvent,
    TargetedEvidenceSnapshot,
    TargetedRobustnessBundle,
)
from us_quant.desktop_targeted_evidence_service import (
    DesktopTargetedEvidenceService,
)
from us_quant.desktop_v2.orchestration.research.scenario_capital import (
    ResearchScenarioCapitalState,
)
from us_quant.desktop_v2.orchestration.research.targeted.evidence import (
    models,
)
from us_quant.desktop_v2.orchestration.research.targeted.evidence.messages import (
    REPLAY_START_MESSAGE,
    ROBUSTNESS_START_MESSAGE,
    bundle_event,
    bundle_log,
    replay_event,
    replay_log,
)
from us_quant.desktop_v2.orchestration.research.targeted.evidence.projector import (
    project_evidence,
)
from us_quant.desktop_v2.orchestration.tasking import TaskSubmitter
from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedEvidenceWorkspace,
    TargetedWorkspace,
)
from us_quant.trading.domain.strategy import StrategyVersion
from us_quant.universe import UniverseSnapshot

#: The resource group that serializes targeted research.  A second concurrent run
#: would rewrite the robustness artifacts while the first still produced them.
TARGETED_RESOURCE_GROUP = "targeted"

REPLAY_PURPOSE = models.REPLAY_PURPOSE
ROBUSTNESS_PURPOSE = models.ROBUSTNESS_PURPOSE


class TargetedEvidenceOrchestrator(QObject):
    """Owns the targeted research evidence, its requests and its render."""

    #: A request was refused before it reached the service.  The window owns the
    #: dialog, so this module stays widget-free.
    refused = Signal(str, str)

    #: A status line for the window's footer.
    log_requested = Signal(str)

    #: One runtime event for the window to record.  The store is not ours.
    runtime_event_requested = Signal(object)

    #: Fresh minute evidence landed for ``symbol``, so the session panel's minute
    #: summary is stale.  Minute status is not an evidence fact.
    minute_status_refresh_requested = Signal(str)

    #: A suite finished, so the desktop should bring research into view.  Which
    #: route that means is shell composition, not decided here.
    focus_requested = Signal()

    def __init__(
        self,
        *,
        service: DesktopTargetedEvidenceService,
        page: object,
        submit_task: TaskSubmitter,
        universe_provider: Callable[[], UniverseSnapshot | None],
        strategy_provider: Callable[[], StrategyVersion | None],
        target_symbol_provider: Callable[[], str],
        capital_state: ResearchScenarioCapitalState,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._page = page
        self._submit_task = submit_task
        self._universe_provider = universe_provider
        self._strategy_provider = strategy_provider
        self._target_symbol_provider = target_symbol_provider
        self._capital_state = capital_state
        self._snapshot = TargetedEvidenceSnapshot()

    @property
    def snapshot(self) -> TargetedEvidenceSnapshot:
        """The canonical desktop evidence truth.

        A stored fact, not a delegation: the service is stateless, so the desktop
        layer is where the evidence lives.  Consumers read this rather than
        keeping a mirrored copy, which would be a second truth.
        """

        return self._snapshot

    # -- lifecycle ---------------------------------------------------------

    def restore_saved(self) -> None:
        """Adopt the seven artifact families the startup loader read from disk.

        Startup restoration is **not** a new run: it seeds the truth and paints
        once, and publishes no runtime event and no focus.  Both selections stay
        ``None``, as startup has always done -- auto-selecting the newest suite
        would be a behaviour change dressed as an extraction.  A malformed
        artifact leaves the truth empty, still paints exactly once and logs.
        """

        try:
            restored = self._service.load_saved()
        except Exception as error:
            self._snapshot = TargetedEvidenceSnapshot()
            self.render_current()
            self.log_requested.emit(
                f"目标研究证据产物读取失败："
                f"{type(error).__name__}: {error}"
            )
            return
        self._snapshot = restored
        self.render_current()

    # -- request intents ---------------------------------------------------

    def request_replay(self) -> None:
        """Replay the latest recorded session of the selected provider."""

        self._request(REPLAY_PURPOSE)

    def request_robustness(self) -> None:
        """Run the multi-day robustness suite and its five dependent studies."""

        self._request(ROBUSTNESS_PURPOSE)

    def _request(self, purpose: str) -> None:
        """Validate, freeze every request-time fact, then submit one task.

        The three providers are read **once, here**, and the task closes over
        their values: reading any of them again inside the task would let a
        queued run change its own inputs.
        """

        strategy = self._strategy_provider()
        symbol = self._target_symbol_provider()
        refusal = models.request_refusal(
            purpose=purpose,
            strategy=strategy,
            symbol=symbol,
            universe=self._universe_provider(),
        )
        if refusal is not None:
            self.refused.emit(*refusal)
            return
        assert strategy is not None  # request_refusal already rejected None

        inputs = TargetedEvidenceRunInputs.of(
            symbol=symbol,
            strategy=strategy,
            initial_equity=self._capital_state.decimal_value,
        )
        replay = purpose == REPLAY_PURPOSE

        def task(progress: Callable[[str], None]) -> object:
            if replay:
                return self._service.run_replay(inputs, progress=progress)
            return self._service.run_robustness(inputs, progress=progress)

        template = (
            REPLAY_START_MESSAGE if replay else ROBUSTNESS_START_MESSAGE
        )
        self._submit_task(
            task,
            on_success=(
                self._replay_finished if replay else self._robustness_finished
            ),
            start_message=template.format(symbol=symbol),
            resource_group=TARGETED_RESOURCE_GROUP,
        )

    # -- selection intents -------------------------------------------------

    def select_robustness_run(self, run_id: str) -> None:
        """Point the robustness detail at one historical run.

        A selection is an intent, not a fact: an id that no longer matches a run
        is recorded as-is and the presenter falls back to the newest one, so a
        stale click cannot leave the panel empty.  The research route is not
        changed -- selecting evidence is not navigating.
        """

        self._snapshot = replace(
            self._snapshot, selected_robustness_run_id=run_id
        )
        self.render_current()

    def select_review_run(self, run_id: str) -> None:
        """Point the review detail at one historical run."""

        self._snapshot = replace(
            self._snapshot, selected_review_run_id=run_id
        )
        self.render_current()

    # -- rendering ---------------------------------------------------------

    def render_current(self) -> None:
        """Draw the evidence from this capability's own state.

        It never fetches: the evidence is whatever the last successful run or
        startup restore committed.  It also paints **evidence only** -- the
        session side has its own entry point and is not this capability's to
        draw, which is what stops a market tick from rebuilding seven tables.
        """

        self._page.render_evidence(project_evidence(self._snapshot))

    # -- success paths -----------------------------------------------------

    def _replay_finished(self, result: object) -> None:
        """Publish one successful replay: snapshot, page, minute refresh, log.

        **Everything that can fail happens before the commit.**  The type check
        and the projection run first, so a result that reached the truth is one
        the whole success path can finish -- the contract the Cross Section round
        had to repair after the fact.  A wrong object fails loudly while the last
        good evidence stays put, the page is not repainted, and no event or log
        claims a run completed.  A failed task is the same.
        """

        replay = models.validate_replay(result)
        candidate = models.commit_replay(self._snapshot, replay)
        view = project_evidence(candidate)
        event = replay_event(replay)
        log = replay_log(replay)

        # Only past this line may the capability's truth change.
        self._snapshot = candidate
        self._page.render_evidence(view)
        self.minute_status_refresh_requested.emit(replay.symbol)
        self.runtime_event_requested.emit(event)
        self.log_requested.emit(log)

    def _robustness_finished(self, result: object) -> None:
        """Publish one successful suite: all six results, atomically.

        The whole bundle is validated and projected before anything is committed,
        so there is no state in which the robustness list is new while the review
        entry is not -- the failure mode of committing five lists one at a time.
        Both selections move to the new run ids, exactly as before.

        The suite is the one event that also looks at its own result: the page's
        evidence workspace moves to the review section and
        :attr:`focus_requested` asks the window to bring research into view.
        """

        bundle: TargetedRobustnessBundle = models.validate_bundle(result)
        candidate = models.commit_bundle(self._snapshot, bundle)
        view = project_evidence(candidate)
        event = bundle_event(bundle)
        log = bundle_log(bundle)

        # Only past this line may the capability's truth change.
        self._snapshot = candidate
        self._page.render_evidence(view)
        self._page.set_active_workspace(TargetedWorkspace.EVIDENCE)
        self._page.set_active_evidence_workspace(
            TargetedEvidenceWorkspace.REVIEW
        )
        self.focus_requested.emit()
        self.runtime_event_requested.emit(event)
        self.log_requested.emit(log)


__all__ = [
    "REPLAY_PURPOSE",
    "REPLAY_START_MESSAGE",
    "ROBUSTNESS_PURPOSE",
    "ROBUSTNESS_START_MESSAGE",
    "TARGETED_RESOURCE_GROUP",
    "TargetedEvidenceOrchestrator",
]
