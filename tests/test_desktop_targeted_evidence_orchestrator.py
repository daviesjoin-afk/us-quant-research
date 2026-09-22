"""Behaviour of the v2O-C5A targeted evidence capability, without a window.

The capability is constructed with plain callables and a stubbed service, so
every rule it owns can be asserted here -- the three refusals, the request-time
freeze, the atomic commit, the last-good rule, the focus request and the two
startup paths -- without starting a Qt event loop or a worker thread.

What these tests cannot prove (that the page's buttons reach the capability, that
the window holds no second truth) belongs to the wiring and architecture files.
"""

from __future__ import annotations

import ast
import os
import pathlib
from dataclasses import replace
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from us_quant.desktop_targeted_evidence_models import (
    TargetedEvidenceRunInputs,
    TargetedEvidenceSnapshot,
    TargetedRobustnessBundle,
)
from us_quant.desktop_v2.orchestration.research.scenario_capital import (
    ResearchScenarioCapitalState,
)
from us_quant.desktop_v2.orchestration.research.targeted.evidence import (
    TargetedEvidenceOrchestrator,
)
from us_quant.desktop_v2.orchestration.research.targeted.evidence import (
    models,
)
from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedEvidenceWorkspace,
    TargetedWorkspace,
)
from us_quant.universe import UniverseRecord, UniverseSnapshot

from datetime import datetime, timezone  # noqa: E402

_ORCHESTRATOR_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "desktop_v2"
    / "orchestration"
    / "research"
    / "targeted"
    / "evidence"
    / "orchestrator.py"
)


def _replay(run_id: str = "replay-1", symbol: str = "AAPL"):
    from us_quant.targeted_replay import TargetedReplayResult

    return TargetedReplayResult(
        run_id=run_id,
        symbol=symbol,
        strategy_version_id="version-1",
        strategy_semver="1.0.0",
        parameter_hash="hash-1",
        parameters={},
        data_hash="data-1",
        first_minute="2026-07-20T14:00:00+00:00",
        last_minute="2026-07-20T20:59:00+00:00",
        row_count=346,
        gap_count=0,
        providers=("IBKR",),
        coverages=("Type 1",),
        initial_equity=Decimal("4200"),
        final_equity=Decimal("4260"),
        total_return=Decimal("0.0143"),
        maximum_drawdown=Decimal("0.0021"),
        realized_pnl=Decimal("60"),
        commission_cost=Decimal("0.70"),
        fills=(),
        status="research_replay",
    )


def _walk_forward(run_id: str = "walk-1", symbol: str = "AAPL"):
    """A real walk-forward result, since the presenter reads every field.

    A stand-in object would let this file pass while the evidence table rendered
    the wrong thing -- and the presenter is the one projection this round is not
    allowed to change.
    """

    from us_quant.targeted_validation import (
        TargetedWalkForwardResult,
        ValidationMetrics,
    )

    metrics = ValidationMetrics(
        session_count=5,
        compounded_return=Decimal("0.01"),
        mean_session_return=Decimal("0.002"),
        median_session_return=Decimal("0.0018"),
        worst_session_return=Decimal("-0.004"),
        maximum_drawdown=Decimal("0.006"),
        profitable_session_fraction=Decimal("0.6"),
        total_fills=12,
        commission_cost=Decimal("4.20"),
    )
    return TargetedWalkForwardResult(
        run_id=run_id,
        robustness_run_id="robustness-1",
        symbol=symbol,
        strategy_version_id="version-1",
        strategy_semver="1.0.0",
        base_parameter_hash="hash-1",
        data_hash="data-1",
        provider="IBKR",
        candidate_count=5,
        selection_rule="training_only",
        initial_train_sessions=10,
        validation_sessions=5,
        test_sessions=5,
        folds=(),
        out_of_sample_metrics=metrics,
        out_of_sample_benchmark=metrics,
        out_of_sample_excess_return=Decimal("0.004"),
        validation_passed_folds=0,
        evidence_grade="B",
        review_ready=False,
        status="research_walk_forward",
    )


def _bundle(run_id: str = "robustness-1", symbol: str = "AAPL"):
    from tests.test_desktop_v2_targeted_completion import _bundle as build

    return build(run_id, symbol)


class _Page:
    """Records every evidence paint and every navigation, in order.

    Both are recorded because the ordering matters: a suite paints first and
    navigates after, and a test that only counted paints would not notice a
    navigation that happened before the page had anything to show.
    """

    def __init__(self) -> None:
        self.rendered: list[object] = []
        self.events: list[tuple[str, object]] = []

    def render_evidence(self, view) -> None:
        self.rendered.append(view)
        self.events.append(("render_evidence", view))

    def render_session(self, view) -> None:
        # Present so a test can prove the capability never calls it.
        self.events.append(("render_session", view))

    def set_active_workspace(self, workspace) -> None:
        self.events.append(("set_active_workspace", workspace))

    def set_active_evidence_workspace(self, workspace) -> None:
        self.events.append(("set_active_evidence_workspace", workspace))


class _Service:
    """A stubbed service: records requests, returns scripted values."""

    def __init__(self, replay=None, bundle=None, saved=None) -> None:
        self.replay = _replay() if replay is None else replay
        self.bundle = _bundle() if bundle is None else bundle
        self.saved = saved if saved is not None else TargetedEvidenceSnapshot()
        self.replays: list[dict] = []
        self.bundles: list[dict] = []
        self.loads = 0
        self.raises: Exception | None = None

    def run_replay(self, inputs, *, progress):
        self.replays.append({"inputs": inputs, "progress": progress})
        if self.raises is not None:
            raise self.raises
        return self.replay

    def run_robustness(self, inputs, *, progress):
        self.bundles.append({"inputs": inputs, "progress": progress})
        if self.raises is not None:
            raise self.raises
        return self.bundle

    def load_saved(self):
        self.loads += 1
        if self.raises is not None:
            raise self.raises
        return self.saved


class _Submit:
    """Captures the submitted task and answers admission as scripted.

    ``run()`` executes the captured task the way the real task boundary does --
    the worker calls the task and delivers the result to ``on_success`` -- so the
    timing rules can be asserted end to end rather than by inspecting a closure.
    """

    def __init__(self, accept: bool = True) -> None:
        self.accept = accept
        self.calls: list[dict] = []

    def __call__(self, task, *, on_success, start_message, resource_group, **_kw):
        self.calls.append(
            {
                "task": task,
                "on_success": on_success,
                "start_message": start_message,
                "resource_group": resource_group,
            }
        )
        return self.accept

    def run(self, index: int = -1, progress=None):
        call = self.calls[index]
        result = call["task"](progress or (lambda _m: None))
        call["on_success"](result)
        return result


class _Strategy:
    version_id = "version-7"
    semver = "1.3.0-research"
    parameter_hash = "hash-7"
    parameters = {"momentum_lookback_minutes": 5}


def _universe(symbol: str = "AAPL", *, eligible: bool = True):
    return UniverseSnapshot(
        generated_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
        source_timestamps={"test": "now"},
        records=(
            UniverseRecord(
                symbol=symbol,
                name=symbol,
                exchange="NASDAQ",
                security_type="STK",
                eligible_for_research=eligible,
            ),
        ),
    )


def _build(
    *,
    service=None,
    page=None,
    submit=None,
    universe=None,
    strategy=_Strategy,
    symbol="AAPL",
    capital=4200,
):
    """Construct the capability with recording doubles for every dependency."""

    service = service or _Service()
    page = page or _Page()
    submit = submit or _Submit()
    capital_state = ResearchScenarioCapitalState(capital)
    orchestrator = TargetedEvidenceOrchestrator(
        service=service,
        page=page,
        submit_task=submit,
        universe_provider=lambda: universe,
        strategy_provider=lambda: strategy,
        target_symbol_provider=lambda: symbol,
        capital_state=capital_state,
    )
    return orchestrator, service, page, submit, capital_state


def _signals(orchestrator) -> dict[str, list]:
    seen: dict[str, list] = {
        "refused": [],
        "log": [],
        "event": [],
        "minute": [],
        "focus": [],
    }
    orchestrator.refused.connect(lambda *a: seen["refused"].append(a))
    orchestrator.log_requested.connect(seen["log"].append)
    orchestrator.runtime_event_requested.connect(seen["event"].append)
    orchestrator.minute_status_refresh_requested.connect(seen["minute"].append)
    orchestrator.focus_requested.connect(lambda: seen["focus"].append(True))
    return seen


# -- the initial state ----------------------------------------------------


def test_the_initial_snapshot_is_empty_and_selects_nothing() -> None:
    orchestrator, _service, _page, _submit, _capital = _build()
    snapshot = orchestrator.snapshot

    assert snapshot == TargetedEvidenceSnapshot()
    assert snapshot.replay_results == ()
    assert snapshot.robustness_results == ()
    assert snapshot.walk_forward_results == ()
    assert snapshot.overfit_results == ()
    assert snapshot.data_quality_results == ()
    assert snapshot.execution_stress_results == ()
    assert snapshot.review_results == ()
    assert snapshot.selected_robustness_run_id is None
    assert snapshot.selected_review_run_id is None


def test_the_constructor_renders_nothing() -> None:
    """The first paint happens on restore or on a result, never in the build."""

    _orchestrator, _service, page, _submit, _capital = _build()

    assert page.rendered == []
    assert page.events == []


# -- startup restore ------------------------------------------------------


def test_restore_saved_commits_and_paints_exactly_once() -> None:
    saved = TargetedEvidenceSnapshot(replay_results=(_replay(),))
    service = _Service(saved=saved)
    orchestrator, _s, page, _submit, _capital = _build(service=service)
    seen = _signals(orchestrator)

    orchestrator.restore_saved()

    assert orchestrator.snapshot == saved
    assert service.loads == 1
    assert len(page.rendered) == 1
    assert len(page.events) == 1
    assert seen["event"] == []
    assert seen["focus"] == []
    assert seen["log"] == []


def test_restoring_twice_still_paints_once_per_call() -> None:
    """Exactly-once is per restore, not per process: no hidden second paint.

    The window calls ``restore_saved()`` and then paints the *session* half.  A
    capability that painted from its constructor as well would make the evidence
    render twice for one startup, which is the double-render this pins.
    """

    orchestrator, service, page, _submit, _capital = _build()
    orchestrator.restore_saved()

    assert service.loads == 1
    assert len(page.rendered) == 1
    assert [name for name, _v in page.events] == ["render_evidence"]


def test_restore_saved_selects_nothing_even_with_history() -> None:
    """Auto-selecting the newest suite would be a behaviour change, not an extraction."""

    saved = TargetedEvidenceSnapshot(robustness_results=(_bundle().robustness,))
    orchestrator, _s, _p, _su, _c = _build(service=_Service(saved=saved))

    orchestrator.restore_saved()

    assert orchestrator.snapshot.selected_robustness_run_id is None
    assert orchestrator.snapshot.selected_review_run_id is None
    assert orchestrator.snapshot.robustness_results


def test_a_malformed_artifact_still_paints_once_and_logs() -> None:
    service = _Service()
    service.raises = ValueError("bad json")
    orchestrator, _s, page, _submit, _capital = _build(service=service)
    seen = _signals(orchestrator)

    orchestrator.restore_saved()

    assert orchestrator.snapshot == TargetedEvidenceSnapshot()
    assert len(page.rendered) == 1
    assert len(seen["log"]) == 1
    assert "读取失败" in seen["log"][0]
    # A corrupt file is not a run: no event, no focus.
    assert seen["event"] == []
    assert seen["focus"] == []


# -- the three refusals ---------------------------------------------------


def test_a_missing_strategy_version_refuses() -> None:
    orchestrator, _s, page, submit, _c = _build(strategy=None)
    seen = _signals(orchestrator)

    orchestrator.request_replay()

    assert seen["refused"] == [("缺少策略版本", "请选择指定标的日内 T 策略版本。")]
    assert submit.calls == []
    assert page.rendered == []


def test_an_invalid_symbol_refuses() -> None:
    orchestrator, _s, _p, submit, _c = _build(symbol="lower-case")
    seen = _signals(orchestrator)

    orchestrator.request_replay()

    assert seen["refused"][0][0] == "代码无效"
    assert submit.calls == []


def test_the_invalid_symbol_refusal_names_the_action() -> None:
    """Replay and robustness differ only here, and the operator reads it."""

    orchestrator, _s, _p, _submit, _c = _build(symbol="@@@")
    seen = _signals(orchestrator)

    orchestrator.request_replay()
    orchestrator.request_robustness()

    assert seen["refused"] == [
        ("代码无效", "请输入需要回放的股票或 ETF 代码。"),
        ("代码无效", "请输入需要评估的股票或 ETF 代码。"),
    ]


def test_an_ineligible_symbol_refuses_when_a_universe_exists() -> None:
    orchestrator, _s, _p, submit, _c = _build(
        universe=_universe("AAPL", eligible=False)
    )
    seen = _signals(orchestrator)

    orchestrator.request_robustness()

    assert seen["refused"][0][0] == "标的门未通过"
    assert "AAPL" in seen["refused"][0][1]
    assert submit.calls == []


def test_a_symbol_absent_from_an_existing_universe_refuses() -> None:
    """The gate is "listed and eligible", not "not explicitly barred"."""

    orchestrator, _s, _p, submit, _c = _build(
        universe=_universe("MSFT"), symbol="AAPL"
    )
    seen = _signals(orchestrator)

    orchestrator.request_replay()

    assert seen["refused"][0][0] == "标的门未通过"
    assert submit.calls == []


def test_no_universe_is_not_a_refusal() -> None:
    """Targeted research has always been allowed before a universe exists.

    Making this fail closed would be a product-behaviour change smuggled into an
    extraction, so the distinction is pinned rather than left to a docstring.
    """

    orchestrator, _s, _p, submit, _capital = _build(universe=None)
    seen = _signals(orchestrator)

    orchestrator.request_replay()

    assert seen["refused"] == []
    assert len(submit.calls) == 1


def test_an_existing_but_empty_universe_refuses() -> None:
    """An empty pool is still a pool that does not list the symbol."""

    empty = UniverseSnapshot(
        generated_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
        source_timestamps={"test": "now"},
        records=(),
    )
    orchestrator, _s, _p, submit, _c = _build(universe=empty)
    seen = _signals(orchestrator)

    orchestrator.request_replay()

    assert seen["refused"][0][0] == "标的门未通过"
    assert submit.calls == []


def test_a_refusal_uses_the_window_signal_not_a_widget() -> None:
    """The capability holds no dialog; the window shows the refusal."""

    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    identifiers = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    } | {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    assert "QMessageBox" not in identifiers
    assert "QWidget" not in identifiers


# -- the request-time freeze ----------------------------------------------


def _freeze_checks(orchestrator, submit, service, signal_name: str):
    """The three frozen facts, asserted from the executor's own arguments."""

    inputs = service.replays[0]["inputs"] if signal_name == "replay" else (
        service.bundles[0]["inputs"]
    )
    assert inputs == TargetedEvidenceRunInputs(
        symbol="AAPL",
        strategy_version_id="version-7",
        strategy_semver="1.3.0-research",
        parameter_hash="hash-7",
        parameters=(("momentum_lookback_minutes", 5),),
        initial_equity=Decimal("4200"),
    )


def test_replay_freezes_the_strategy_symbol_and_capital() -> None:
    orchestrator, service, _p, submit, _c = _build()
    orchestrator.request_replay()
    submit.run()

    _freeze_checks(orchestrator, submit, service, "replay")


def test_robustness_freezes_the_same_three_facts() -> None:
    orchestrator, service, _p, submit, _c = _build()
    orchestrator.request_robustness()
    submit.run()

    _freeze_checks(orchestrator, submit, service, "robustness")


def test_editing_the_capital_after_the_click_does_not_change_the_run() -> None:
    """The freeze is the whole point: a queued run uses the figure it was clicked at."""

    orchestrator, service, _p, submit, capital = _build(capital=4200)
    orchestrator.request_replay()
    capital.set(9999)

    submit.run()

    assert service.replays[0]["inputs"].initial_equity == Decimal("4200")
    assert capital.value == 9999


def test_switching_the_strategy_after_the_click_does_not_change_the_run() -> None:
    current = {"strategy": _Strategy}
    service = _Service()
    page = _Page()
    submit = _Submit()
    orchestrator = TargetedEvidenceOrchestrator(
        service=service,
        page=page,
        submit_task=submit,
        universe_provider=lambda: None,
        strategy_provider=lambda: current["strategy"],
        target_symbol_provider=lambda: "AAPL",
        capital_state=ResearchScenarioCapitalState(4200),
    )

    class Other:
        version_id = "version-OTHER"
        semver = "9.9.9"
        parameter_hash = "hash-OTHER"
        parameters = {}

    orchestrator.request_replay()
    current["strategy"] = Other
    submit.run()

    assert service.replays[0]["inputs"].strategy_version_id == "version-7"


def test_changing_the_target_symbol_after_the_click_does_not_change_the_run() -> None:
    symbol = {"value": "AAPL"}

    class _OtherService(_Service):
        pass

    service = _Service()
    orchestrator = TargetedEvidenceOrchestrator(
        service=service,
        page=_Page(),
        submit_task=_Submit(),
        universe_provider=lambda: None,
        strategy_provider=lambda: _Strategy,
        target_symbol_provider=lambda: symbol["value"],
        capital_state=ResearchScenarioCapitalState(4200),
    )

    orchestrator.request_replay()
    symbol["value"] = "MSFT"
    orchestrator._submit_task.run()

    assert service.replays[0]["inputs"].symbol == "AAPL"


def test_the_worker_never_reads_a_provider_again() -> None:
    """The task closes over the frozen inputs; it does not re-ask the window."""

    reads: list[str] = []
    service = _Service()

    def counting_strategy():
        reads.append("strategy")
        return _Strategy

    def counting_symbol():
        reads.append("symbol")
        return "AAPL"

    def counting_universe():
        reads.append("universe")
        return None

    submit = _Submit()
    orchestrator = TargetedEvidenceOrchestrator(
        service=service,
        page=_Page(),
        submit_task=submit,
        universe_provider=counting_universe,
        strategy_provider=counting_strategy,
        target_symbol_provider=counting_symbol,
        capital_state=ResearchScenarioCapitalState(4200),
    )
    orchestrator.request_replay()
    assert reads == ["strategy", "symbol", "universe"]

    submit.run()

    # Executing the task added no reads: the request handler read them once.
    assert reads == ["strategy", "symbol", "universe"]


def test_the_submitted_task_uses_the_shared_resource_group() -> None:
    orchestrator, _s, _p, submit, _c = _build()

    orchestrator.request_replay()
    orchestrator.request_robustness()

    assert [call["resource_group"] for call in submit.calls] == [
        "targeted",
        "targeted",
    ]
    assert submit.calls[0]["start_message"] == "AAPL 分钟回放开始…"
    assert submit.calls[1]["start_message"] == "AAPL 多日稳健性评估开始…"


# -- the replay success path ----------------------------------------------


def test_a_replay_commits_paints_and_publishes_in_order() -> None:
    orchestrator, _s, page, submit, _c = _build()
    seen = _signals(orchestrator)
    result = _replay("replay-9", "MSFT")
    orchestrator._service.replay = result

    orchestrator.request_replay()
    submit.run()

    snapshot = orchestrator.snapshot
    assert snapshot.replay_results == (result,)
    assert len(page.rendered) == 1
    assert page.rendered[0].replay_rows
    assert seen["minute"] == ["MSFT"]
    assert len(seen["event"]) == 1
    event = seen["event"][0]
    assert event.component == "targeted_replay"
    assert event.code == "REPLAY_COMPLETE"
    assert event.severity == "info"
    assert "MSFT" in event.message
    assert len(seen["log"]) == 1
    assert "MSFT" in seen["log"][0]
    assert "收益" in seen["log"][0]


def test_a_replay_prepends_to_the_existing_history() -> None:
    old = _replay("replay-old", "AAPL")
    service = _Service(replay=_replay("replay-new", "MSFT"), saved=TargetedEvidenceSnapshot(replay_results=(old,)))
    orchestrator, _s, _p, submit, _c = _build(service=service)
    orchestrator.restore_saved()

    orchestrator.request_replay()
    submit.run()

    assert [r.run_id for r in orchestrator.snapshot.replay_results] == [
        "replay-new",
        "replay-old",
    ]


def test_a_replay_does_not_touch_the_other_six_families() -> None:
    saved = TargetedEvidenceSnapshot(robustness_results=(_bundle().robustness,))
    orchestrator, _s, _p, submit, _c = _build(service=_Service(saved=saved))
    orchestrator.restore_saved()

    orchestrator.request_replay()
    submit.run()

    assert orchestrator.snapshot.robustness_results == saved.robustness_results
    assert orchestrator.snapshot.selected_robustness_run_id is None


def test_a_replay_does_not_request_focus() -> None:
    """Only a completed suite pulls the desktop to the research route."""

    orchestrator, _s, _p, submit, _c = _build()
    seen = _signals(orchestrator)

    orchestrator.request_replay()
    submit.run()

    assert seen["focus"] == []


# -- the last-good rule ---------------------------------------------------


def test_a_bad_replay_result_leaves_everything_alone() -> None:
    """No commit, no repaint, no event, no log -- and a loud failure."""

    good = _replay("replay-good")
    orchestrator, service, page, submit, _c = _build(
        service=_Service(saved=TargetedEvidenceSnapshot(replay_results=(good,)))
    )
    orchestrator.restore_saved()
    paints_before = len(page.rendered)
    seen = _signals(orchestrator)
    service.replay = object()

    orchestrator.request_replay()
    with pytest.raises(TypeError):
        submit.run()

    assert orchestrator.snapshot.replay_results == (good,)
    assert len(page.rendered) == paints_before
    assert seen["event"] == []
    assert seen["log"] == []
    assert seen["minute"] == []


def test_a_failed_replay_task_keeps_the_history() -> None:
    """A worker failure is not evidence that the previous run was wrong."""

    good = _replay("replay-good")
    service = _Service(saved=TargetedEvidenceSnapshot(replay_results=(good,)))
    orchestrator, _s, page, submit, _c = _build(service=service)
    orchestrator.restore_saved()
    paints_before = len(page.rendered)
    seen = _signals(orchestrator)
    service.raises = RuntimeError("minute store unavailable")

    orchestrator.request_replay()
    with pytest.raises(RuntimeError):
        submit.run()

    assert orchestrator.snapshot.replay_results == (good,)
    assert len(page.rendered) == paints_before
    assert seen["event"] == []
    assert seen["log"] == []


# -- the robustness success path ------------------------------------------


def test_a_suite_commits_all_six_families_atomically() -> None:
    old = _bundle("robustness-old", "AAPL")
    new = _bundle("robustness-new", "MSFT")
    service = _Service(bundle=new, saved=TargetedEvidenceSnapshot(
        robustness_results=(old.robustness,),
        overfit_results=(old.overfit,),
        data_quality_results=(old.data_quality,),
        execution_stress_results=(old.execution_stress,),
        review_results=(old.review,),
    ))
    orchestrator, _s, page, submit, _c = _build(service=service)
    orchestrator.restore_saved()

    orchestrator.request_robustness()
    submit.run()

    snapshot = orchestrator.snapshot
    assert [r.run_id for r in snapshot.robustness_results] == [
        "robustness-new",
        "robustness-old",
    ]
    assert [r.run_id for r in snapshot.overfit_results] == [
        new.overfit.run_id,
        old.overfit.run_id,
    ]
    assert [r.run_id for r in snapshot.data_quality_results] == [
        new.data_quality.run_id,
        old.data_quality.run_id,
    ]
    assert [r.run_id for r in snapshot.execution_stress_results] == [
        new.execution_stress.run_id,
        old.execution_stress.run_id,
    ]
    assert [r.run_id for r in snapshot.review_results] == [
        new.review.run_id,
        old.review.run_id,
    ]
    assert len(page.rendered) == 2  # restore + suite


def test_a_suite_selects_both_new_run_ids() -> None:
    new = _bundle("robustness-new", "MSFT")
    orchestrator, _s, _p, submit, _c = _build(service=_Service(bundle=new))

    orchestrator.request_robustness()
    submit.run()

    assert (
        orchestrator.snapshot.selected_robustness_run_id
        == new.robustness.run_id
    )
    assert orchestrator.snapshot.selected_review_run_id == new.review.run_id


def test_a_suite_with_walk_forward_adds_that_family() -> None:
    new = _bundle()
    walk = _walk_forward()
    bundle = replace(new, walk_forward=walk)
    orchestrator, _s, _p, submit, _c = _build(service=_Service(bundle=bundle))

    orchestrator.request_robustness()
    submit.run()

    assert orchestrator.snapshot.walk_forward_results == (walk,)


def test_a_suite_without_walk_forward_leaves_that_family_untouched() -> None:
    """Skipped below 20 sessions is not the same as "clear the history"."""

    old = _walk_forward("walk-old")
    service = _Service(saved=TargetedEvidenceSnapshot(
        walk_forward_results=(old,)
    ))
    orchestrator, _s, _p, submit, _c = _build(service=service)
    orchestrator.restore_saved()

    orchestrator.request_robustness()
    submit.run()

    assert orchestrator.snapshot.walk_forward_results == (old,)


def test_a_suite_navigates_its_page_and_asks_for_route_focus() -> None:
    orchestrator, _s, page, submit, _c = _build()
    seen = _signals(orchestrator)

    orchestrator.request_robustness()
    submit.run()

    assert page.events == [
        ("render_evidence", page.rendered[0]),
        ("set_active_workspace", TargetedWorkspace.EVIDENCE),
        (
            "set_active_evidence_workspace",
            TargetedEvidenceWorkspace.REVIEW,
        ),
    ]
    assert seen["focus"] == [True]
    # The route is the window's decision: the capability holds no shell.
    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    for forbidden in ("navigate_to", "DesktopShellV2", "ResearchPage", "ResearchWorkspace"):
        assert forbidden not in source, forbidden


def test_a_suite_publishes_one_event_and_one_log() -> None:
    orchestrator, _s, _p, submit, _c = _build()
    seen = _signals(orchestrator)

    orchestrator.request_robustness()
    submit.run()

    assert len(seen["event"]) == 1
    event = seen["event"][0]
    assert event.component == "targeted_robustness"
    assert event.code == "ROBUSTNESS_COMPLETE"
    assert "自动晋级 0" in event.message
    assert len(seen["log"]) == 1


def test_a_suite_does_not_refresh_minute_status() -> None:
    """Minute evidence is a replay concern; a suite does not record minutes."""

    orchestrator, _s, _p, submit, _c = _build()
    seen = _signals(orchestrator)

    orchestrator.request_robustness()
    submit.run()

    assert seen["minute"] == []


# -- the bundle's atomicity -----------------------------------------------


def test_a_bad_bundle_commits_nothing_at_all() -> None:
    """No partial state: the whole suite lands or none of it does."""

    old = _bundle("robustness-old", "AAPL")
    service = _Service(saved=TargetedEvidenceSnapshot(
        robustness_results=(old.robustness,),
        overfit_results=(old.overfit,),
        data_quality_results=(old.data_quality,),
        execution_stress_results=(old.execution_stress,),
        review_results=(old.review,),
    ))
    orchestrator, _s, page, submit, _c = _build(service=service)
    orchestrator.restore_saved()
    before = orchestrator.snapshot
    paints_before = len(page.rendered)
    seen = _signals(orchestrator)
    service.bundle = object()

    orchestrator.request_robustness()
    with pytest.raises(TypeError):
        submit.run()

    assert orchestrator.snapshot is before
    assert orchestrator.snapshot.robustness_results == (old.robustness,)
    assert orchestrator.snapshot.overfit_results == (old.overfit,)
    assert len(page.rendered) == paints_before
    assert seen["event"] == []
    assert seen["log"] == []
    assert seen["focus"] == []


def test_a_bundle_missing_a_member_is_rejected_whole() -> None:
    """A half-built bundle is not a bundle; the boundary types it rather than trusting it."""

    orchestrator, service, page, submit, _c = _build()
    paints_before = len(page.rendered)
    seen = _signals(orchestrator)
    service.bundle = replace(_bundle(), overfit=None)

    orchestrator.request_robustness()
    with pytest.raises(TypeError):
        submit.run()

    assert orchestrator.snapshot == TargetedEvidenceSnapshot()
    assert len(page.rendered) == paints_before
    assert seen["event"] == []


# -- selections -----------------------------------------------------------


def _bundle_with_history(run_id: str, symbol: str):
    """One suite whose ids the presenter can actually resolve."""

    from tests.test_desktop_v2_targeted_completion import _bundle as build

    return build(run_id, symbol)


def test_selecting_a_robustness_run_updates_the_snapshot_and_repaints() -> None:
    """A selection whose id resolves is reported back by the presenter.

    The presenter resolves the recorded id against the available rows and falls
    back to the newest; so for the view to name the selected id, the id has to
    exist in the snapshot.  That resolution is deliberately left in the
    presenter -- this round does not move projection rules into the capability.
    """

    bundle = _bundle_with_history("run-from-history", "MSFT")
    saved = TargetedEvidenceSnapshot(robustness_results=(bundle.robustness,))
    orchestrator, _s, page, _submit, _c = _build(service=_Service(saved=saved))
    orchestrator.restore_saved()
    paints_before = len(page.rendered)

    orchestrator.select_robustness_run(bundle.robustness.run_id)

    assert (
        orchestrator.snapshot.selected_robustness_run_id
        == bundle.robustness.run_id
    )
    assert len(page.rendered) == paints_before + 1
    assert (
        page.rendered[-1].selected_robustness_run_id
        == bundle.robustness.run_id
    )


def test_selecting_a_review_run_updates_the_snapshot_and_repaints() -> None:
    bundle = _bundle_with_history("run-from-history", "MSFT")
    saved = TargetedEvidenceSnapshot(review_results=(bundle.review,))
    orchestrator, _s, page, _submit, _c = _build(service=_Service(saved=saved))
    orchestrator.restore_saved()

    orchestrator.select_review_run(bundle.review.run_id)

    assert orchestrator.snapshot.selected_review_run_id == bundle.review.run_id
    assert page.rendered[-1].selected_review_run_id == bundle.review.run_id


def test_a_selection_survives_a_later_repaint() -> None:
    """The selection is snapshot state, not a one-shot paint instruction.

    This is the regression the retired one-shot tab fields used to hide: a
    selection must survive an unrelated repaint, of either half.
    """

    bundle = _bundle_with_history("run-a", "MSFT")
    saved = TargetedEvidenceSnapshot(
        robustness_results=(bundle.robustness,),
        review_results=(bundle.review,),
    )
    orchestrator, _s, page, submit, _c = _build(service=_Service(saved=saved))
    orchestrator.restore_saved()
    orchestrator.select_robustness_run(bundle.robustness.run_id)
    orchestrator.select_review_run(bundle.review.run_id)

    orchestrator.request_replay()
    submit.run()

    assert (
        orchestrator.snapshot.selected_robustness_run_id
        == bundle.robustness.run_id
    )
    assert orchestrator.snapshot.selected_review_run_id == bundle.review.run_id
    assert (
        page.rendered[-1].selected_robustness_run_id
        == bundle.robustness.run_id
    )
    assert page.rendered[-1].selected_review_run_id == bundle.review.run_id


def test_a_selection_does_not_navigate() -> None:
    """Selecting evidence is not navigating: the route is left alone."""

    orchestrator, _s, page, _submit, _c = _build()

    orchestrator.select_robustness_run("run-a")
    orchestrator.select_review_run("review-a")

    assert [name for name, _value in page.events] == [
        "render_evidence",
        "render_evidence",
    ]


def test_an_unknown_run_id_is_recorded_and_the_presenter_falls_back() -> None:
    """A stale click selects nothing rather than erroring, as before."""

    orchestrator, _s, page, _submit, _c = _build()

    orchestrator.select_robustness_run("run-that-does-not-exist")

    assert (
        orchestrator.snapshot.selected_robustness_run_id
        == "run-that-does-not-exist"
    )
    # The presenter picks the newest available run, so the rows are still drawn.
    assert page.rendered[-1].robustness_rows == ()


# -- rendering ownership --------------------------------------------------


def test_render_current_paints_evidence_only() -> None:
    orchestrator, _s, page, _submit, _c = _build()

    orchestrator.render_current()

    assert [name for name, _value in page.events] == ["render_evidence"]
    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert "render_session" not in source


def test_render_current_never_runs_a_study() -> None:
    """A render that could start a run would be a second run path."""

    orchestrator, service, _p, submit, _c = _build()

    orchestrator.render_current()

    assert service.replays == []
    assert service.bundles == []
    assert submit.calls == []


# -- the public surface ---------------------------------------------------


def _public_names() -> set[str]:
    """Class-level names a caller may use: signals, properties, methods."""

    names: set[str] = set()
    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name != "TargetedEvidenceOrchestrator":
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                names.add(item.name)
            elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                targets = (
                    item.targets
                    if isinstance(item, ast.Assign)
                    else [item.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
    return {name for name in names if not name.startswith("_")}


def test_the_public_surface_is_exactly_the_declared_one() -> None:
    assert _public_names() == {
        # Signals: the capability's published facts and requests.
        "refused",
        "log_requested",
        "runtime_event_requested",
        "minute_status_refresh_requested",
        "focus_requested",
        # The one published read-only fact.
        "snapshot",
        # The requests, the selections and the one render entry point.
        "request_replay",
        "request_robustness",
        "restore_saved",
        "select_robustness_run",
        "select_review_run",
        "render_current",
    }


def test_no_convenience_accessor_was_added() -> None:
    """Seven accessors or a page/service getter would be a second truth."""

    for forbidden in (
        "replays",
        "robustness",
        "reviews",
        "page",
        "service",
        "strategy",
        "capital",
        "state",
    ):
        assert forbidden not in _public_names(), forbidden


def test_the_snapshot_is_published_but_the_service_and_page_are_not() -> None:
    """One published fact, because one consumer needs it -- not seven, not the internals."""

    orchestrator, _s, _p, _submit, _c = _build()

    assert isinstance(orchestrator.snapshot, TargetedEvidenceSnapshot)
    # The service and the page are held privately: a public accessor would let the
    # next capability reach the executor or repaint the page behind this one.
    assert not hasattr(type(orchestrator), "service")
    assert not hasattr(type(orchestrator), "page")
    assert not hasattr(type(orchestrator), "capital_state")
