"""Behaviour of the v2O-C4 cross-section capability, without a window.

The capability is constructed with plain callables and a stubbed service, so
every rule it owns can be asserted here -- the refusal, the two opposite timing
rules, what a malformed result does to the last good report, and the three
startup-restore paths -- without starting a Qt event loop or a worker thread.

What these tests cannot prove (that the page button reaches the capability,
that the window holds no second truth) belongs to the wiring and architecture
files.
"""

from __future__ import annotations

import ast
import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from us_quant.desktop_v2.orchestration.research.cross_section import (
    CROSS_SECTION_PROGRESS_MESSAGE,
    CROSS_SECTION_RESOURCE_GROUP,
    CROSS_SECTION_START_MESSAGE,
    MISSING_UNIVERSE_MESSAGE,
    MISSING_UNIVERSE_TITLE,
    CrossSectionOrchestrator,
)
from us_quant.desktop_v2.orchestration.research.scenario_capital import (
    ResearchScenarioCapitalState,
)
from us_quant.desktop_v2.pages.research.cross_section.models import (
    CrossSectionResearchDraft,
)

_ORCHESTRATOR_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "desktop_v2"
    / "orchestration"
    / "research"
    / "cross_section"
    / "orchestrator.py"
)


def _public_names() -> set[str]:
    """Class-level names a caller may use: signals, properties, methods."""

    names: set[str] = set()
    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name != "CrossSectionOrchestrator":
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


def _report() -> dict:
    return {
        "scope": {"initial_equity": 2500.0},
        "out_of_sample": {
            "strategy": {
                "final_equity": 3000.0,
                "total_return": 0.2,
                "max_drawdown": 0.1,
                "worst_day": -0.03,
            },
            "cost_2x": {"final_equity": 2800.0, "total_return": 0.12},
            "folds": [],
        },
        "chart_data": [],
        "promotion_gate": {"passed": False, "reasons": []},
    }


class _Page:
    """Records every render, so "painted exactly once" is assertable."""

    def __init__(self) -> None:
        self.rendered: list[object] = []

    def render(self, view) -> None:
        self.rendered.append(view)


class _Service:
    """A stubbed service: records requests, returns a scripted value."""

    def __init__(self, result: object = None, saved: object = None) -> None:
        self.result = _report() if result is None else result
        self.saved = saved
        self.runs: list[tuple] = []
        self.loads = 0
        self.raises: Exception | None = None

    def run(self, universe, *, research_capital):
        self.runs.append((universe, research_capital))
        if self.raises is not None:
            raise self.raises
        return self.result

    def load_saved(self):
        self.loads += 1
        if self.raises is not None:
            raise self.raises
        return self.saved


class _Submit:
    """Captures the submitted task and answers admission as scripted.

    ``run()`` executes the captured task the way the real task boundary does --
    the worker calls the task, and the result is delivered to ``on_success`` --
    because the capability's whole success path is that callback.  A stub that
    only captured the task would never exercise it.
    """

    def __init__(self, admitted: bool = True) -> None:
        self.admitted = admitted
        self.calls: list[tuple] = []
        self.task = None
        self.on_success = None

    def __call__(self, task, *, on_success, **kwargs):
        self.task = task
        self.on_success = on_success
        self.calls.append((task, kwargs))
        return self.admitted

    def run(self, progress=None):
        """Run the captured task and hand its result to ``on_success``."""

        result = self.task(progress or (lambda message: None))
        self.on_success(result)
        return result


def _make(
    *,
    service=None,
    page=None,
    submit=None,
    universe="UNIVERSE",
    capital=1500,
):
    state = ResearchScenarioCapitalState(capital)

    def provider():
        return universe

    orchestrator = CrossSectionOrchestrator(
        service=service or _Service(),
        page=page or _Page(),
        submit_task=submit or _Submit(),
        universe_provider=provider,
        capital_state=state,
    )
    return orchestrator, state


# -- initial state -------------------------------------------------------


def test_the_capability_starts_with_no_report() -> None:
    orchestrator, _ = _make()

    assert orchestrator._report is None


# -- capital editing -----------------------------------------------------


def test_a_capital_edit_moves_the_canonical_state_and_publishes_once() -> None:
    orchestrator, state = _make(capital=1500)
    seen: list[int] = []
    orchestrator.capital_changed.connect(seen.append)

    orchestrator.request_capital_change(2500)

    assert state.value == 2500
    assert seen == [2500]


def test_the_same_capital_does_not_publish_twice() -> None:
    """A no-op edit must not repaint the account presentation."""

    orchestrator, state = _make(capital=2500)
    seen: list[int] = []
    orchestrator.capital_changed.connect(seen.append)

    orchestrator.request_capital_change(2500)

    assert state.value == 2500
    assert seen == []


# -- request_run: refusal and timing -------------------------------------


def test_a_missing_universe_is_refused_and_no_task_runs() -> None:
    submit = _Submit()
    orchestrator, _ = _make(submit=submit, universe=None)
    refused: list[tuple] = []
    orchestrator.refused.connect(
        lambda title, message: refused.append((title, message))
    )

    orchestrator.request_run(CrossSectionResearchDraft(research_capital=2500))

    assert refused == [(MISSING_UNIVERSE_TITLE, MISSING_UNIVERSE_MESSAGE)]
    assert submit.calls == []


def test_the_request_freezes_the_capital_and_adopts_it() -> None:
    """The draft's capital becomes canonical and the run uses that value."""

    service = _Service()
    submit = _Submit()
    orchestrator, state = _make(service=service, submit=submit, capital=1500)

    orchestrator.request_run(CrossSectionResearchDraft(research_capital=2500))
    submit.run()

    assert state.value == 2500
    assert service.runs == [("UNIVERSE", 2500)]


def test_editing_the_capital_after_the_request_does_not_rerun() -> None:
    """The freeze is the contract: a queued run is not re-priced."""

    service = _Service()
    submit = _Submit()
    orchestrator, state = _make(service=service, submit=submit, capital=1500)

    orchestrator.request_run(CrossSectionResearchDraft(research_capital=2500))
    # The operator edits the control while the task is still queued.
    orchestrator.request_capital_change(9999)

    submit.task(lambda message: None)

    assert service.runs == [("UNIVERSE", 2500)]
    assert state.value == 9999


def test_the_task_re_reads_the_universe_at_execution_time() -> None:
    """A refresh that lands while the task queues is the universe researched."""

    service = _Service()
    submit = _Submit()
    holder = {"universe": "OLD"}

    state = ResearchScenarioCapitalState(1500)
    orchestrator = CrossSectionOrchestrator(
        service=service,
        page=_Page(),
        submit_task=submit,
        universe_provider=lambda: holder["universe"],
        capital_state=state,
    )

    orchestrator.request_run(CrossSectionResearchDraft(research_capital=2500))
    holder["universe"] = "NEW"

    submit.task(lambda message: None)

    assert service.runs == [("NEW", 2500)]


def test_a_universe_that_vanishes_fails_closed() -> None:
    """Definitely not a stale capture: the task refuses rather than runs."""

    service = _Service()
    submit = _Submit()
    holder = {"universe": "UNIVERSE"}
    state = ResearchScenarioCapitalState(1500)
    orchestrator = CrossSectionOrchestrator(
        service=service,
        page=_Page(),
        submit_task=submit,
        universe_provider=lambda: holder["universe"],
        capital_state=state,
    )

    orchestrator.request_run(CrossSectionResearchDraft(research_capital=2500))
    holder["universe"] = None

    with pytest.raises(RuntimeError, match=MISSING_UNIVERSE_MESSAGE):
        submit.task(lambda message: None)
    assert service.runs == []


def test_the_submission_uses_the_strategy_resource_group() -> None:
    submit = _Submit()
    orchestrator, _ = _make(submit=submit)

    orchestrator.request_run(CrossSectionResearchDraft(research_capital=2500))

    _task, kwargs = submit.calls[0]
    assert kwargs["resource_group"] == CROSS_SECTION_RESOURCE_GROUP
    assert kwargs["resource_group"] == "strategy"
    assert kwargs["start_message"] == CROSS_SECTION_START_MESSAGE


def test_the_progress_line_is_unchanged() -> None:
    submit = _Submit()
    orchestrator, _ = _make(submit=submit)
    orchestrator.request_run(CrossSectionResearchDraft(research_capital=2500))

    lines: list[str] = []
    submit.task(lines.append)

    assert lines == [CROSS_SECTION_PROGRESS_MESSAGE]
    assert lines[0].startswith("正在按整股、组合风险预算")


# -- success -------------------------------------------------------------


def test_a_successful_run_commits_renders_announces_and_logs() -> None:
    service = _Service()
    page = _Page()
    submit = _Submit()
    orchestrator, _ = _make(service=service, page=page, submit=submit)
    published: list[None] = []
    logged: list[str] = []
    orchestrator.report_changed.connect(lambda: published.append(None))
    orchestrator.log_requested.connect(logged.append)

    orchestrator.request_run(CrossSectionResearchDraft(research_capital=2500))
    submit.run()

    assert orchestrator._report is service.result
    assert len(page.rendered) == 1
    assert published == [None]
    assert logged and logged[0].startswith("组合研究完成：OOS +20.0%")


def test_a_wrong_result_type_fails_loudly_and_keeps_the_last_good() -> None:
    service = _Service()
    page = _Page()
    submit = _Submit()
    orchestrator, _ = _make(service=service, page=page, submit=submit)
    orchestrator.request_run(CrossSectionResearchDraft(research_capital=2500))
    submit.run()
    last_good = orchestrator._report
    renders_before = len(page.rendered)

    with pytest.raises(TypeError):
        orchestrator._report_finished(["not", "a", "report"])

    assert orchestrator._report is last_good
    # The truth was not touched, so nothing needed repainting either.
    assert len(page.rendered) == renders_before


def test_numeric_string_metrics_complete_the_whole_success_path() -> None:
    """Regression: a projectable report must not die in the completion log.

    The presenter coerces with ``float(...)``, so a report carrying numeric
    *strings* projects fine -- but the completion message formats those same
    values with a raw ``{:+.1%}``, which raises ``ValueError`` on a ``str``.
    That failure used to happen *after* the truth had moved, the page repainted
    and ``report_changed`` been emitted: a "successful" run that left the
    capability in a new state, fired the Dashboard bridge, and wrote no
    completion log.

    So the contract is asserted as one indivisible outcome: if the projection
    accepted it, the whole success path must finish -- commit, render, announce,
    log -- with no partial state in between.
    """

    report = _report()
    report["out_of_sample"]["strategy"]["total_return"] = "0.2"
    report["out_of_sample"]["strategy"]["max_drawdown"] = "0.1"

    service = _Service(result=report)
    page = _Page()
    submit = _Submit()
    orchestrator, _ = _make(service=service, page=page, submit=submit)
    published: list[None] = []
    logged: list[str] = []
    orchestrator.report_changed.connect(lambda: published.append(None))
    orchestrator.log_requested.connect(logged.append)

    orchestrator.request_run(CrossSectionResearchDraft(research_capital=2500))
    submit.run()

    assert orchestrator._report is report
    assert len(page.rendered) == 1
    assert published == [None]
    assert len(logged) == 1
    assert "OOS +20.0%" in logged[0]
    assert "最大回撤 10.0%" in logged[0]


def test_a_malformed_report_fails_loudly_and_keeps_the_last_good() -> None:
    """Projection happens before the commit, so the truth is never poisoned.

    A schema-incomplete dict passes the type check and fails in the presenter,
    which is a ``KeyError`` -- normalised here into a ``TypeError`` so the
    capability reports one consistent failure while ``__cause__`` keeps the
    original for anyone debugging the artifact.

    The four assertions are the whole point, and each one is checked because
    the failure mode this guards against is *partial* success: the truth must
    not move, the page must not repaint, ``report_changed`` must not fire, and
    no completion log may be written.  A looser "it raised" check is exactly
    what let the post-commit window through in the first place.
    """

    service = _Service()
    page = _Page()
    submit = _Submit()
    orchestrator, _ = _make(service=service, page=page, submit=submit)
    orchestrator.request_run(CrossSectionResearchDraft(research_capital=2500))
    submit.run()
    last_good = orchestrator._report
    renders_before = len(page.rendered)
    published: list[None] = []
    logged: list[str] = []
    orchestrator.report_changed.connect(lambda: published.append(None))
    orchestrator.log_requested.connect(logged.append)

    with pytest.raises(TypeError) as raised:
        orchestrator._report_finished({"status": "research_exploratory"})

    assert isinstance(raised.value.__cause__, KeyError)
    assert orchestrator._report is last_good
    assert len(page.rendered) == renders_before
    assert published == []
    assert logged == []


@pytest.mark.parametrize(
    "broken",
    (
        {"out_of_sample": {"strategy": {"total_return": "not-a-number"}}},
        {"out_of_sample": {"strategy": None}},
        {"out_of_sample": "not-a-mapping"},
    ),
)
def test_every_unusable_shape_fails_before_any_side_effect(broken) -> None:
    """The window is closed for coercion failures and shape failures alike.

    ``total_return`` is formatted, so a non-numeric string is the case that
    reaches the message builder; the other two fail in the presenter.  Both
    must land on the same side of the commit line.
    """

    service = _Service()
    page = _Page()
    submit = _Submit()
    orchestrator, _ = _make(service=service, page=page, submit=submit)
    orchestrator.request_run(CrossSectionResearchDraft(research_capital=2500))
    submit.run()
    last_good = orchestrator._report
    renders_before = len(page.rendered)
    published: list[None] = []
    logged: list[str] = []
    orchestrator.report_changed.connect(lambda: published.append(None))
    orchestrator.log_requested.connect(logged.append)

    with pytest.raises(TypeError):
        orchestrator._report_finished(broken)

    assert orchestrator._report is last_good
    assert len(page.rendered) == renders_before
    assert published == []
    assert logged == []


def test_a_task_failure_keeps_the_last_good_report() -> None:
    """A failed run is not evidence that the previous report was wrong."""

    service = _Service()
    page = _Page()
    submit = _Submit()
    orchestrator, _ = _make(service=service, page=page, submit=submit)
    orchestrator.request_run(CrossSectionResearchDraft(research_capital=2500))
    submit.run()
    last_good = orchestrator._report

    # The window's generic task-failure path is not this capability's; what it
    # must not do is clear its own truth.
    service.raises = RuntimeError("boom")
    orchestrator.request_run(CrossSectionResearchDraft(research_capital=2500))
    with pytest.raises(RuntimeError):
        submit.task(lambda message: None)

    assert orchestrator._report is last_good


# -- startup restore -----------------------------------------------------


def test_restore_without_a_saved_report_paints_empty_exactly_once() -> None:
    service = _Service(saved=None)
    page = _Page()
    orchestrator, _ = _make(service=service, page=page)
    published: list[None] = []
    logged: list[str] = []
    orchestrator.report_changed.connect(lambda: published.append(None))
    orchestrator.log_requested.connect(logged.append)

    orchestrator.restore_saved()

    assert orchestrator._report is None
    assert len(page.rendered) == 1
    assert page.rendered[0].has_report is False
    assert published == []
    assert logged == []


def test_restore_with_a_valid_report_paints_once_silently() -> None:
    report = _report()
    service = _Service(saved=report)
    page = _Page()
    orchestrator, _ = _make(service=service, page=page)
    published: list[None] = []
    logged: list[str] = []
    orchestrator.report_changed.connect(lambda: published.append(None))
    orchestrator.log_requested.connect(logged.append)

    orchestrator.restore_saved()

    assert orchestrator._report == report
    assert len(page.rendered) == 1
    assert page.rendered[0].has_report is True
    assert published == []
    assert logged == []


def test_restore_with_a_malformed_report_paints_empty_once_and_logs() -> None:
    service = _Service(saved={"status": "research_exploratory"})
    page = _Page()
    orchestrator, _ = _make(service=service, page=page)
    published: list[None] = []
    logged: list[str] = []
    orchestrator.report_changed.connect(lambda: published.append(None))
    orchestrator.log_requested.connect(logged.append)

    orchestrator.restore_saved()

    assert orchestrator._report is None
    assert len(page.rendered) == 1
    assert published == []
    assert logged and "风险一致研究产物读取失败" in logged[0]


def test_restore_survives_a_raising_loader() -> None:
    """A corrupt artifact must not stop the desktop from starting."""

    service = _Service()
    service.raises = ValueError("bad json")
    page = _Page()
    orchestrator, _ = _make(service=service, page=page)
    logged: list[str] = []
    orchestrator.log_requested.connect(logged.append)

    orchestrator.restore_saved()

    assert orchestrator._report is None
    assert len(page.rendered) == 1
    assert logged and "风险一致研究产物读取失败：ValueError: bad json" == logged[0]


# -- render --------------------------------------------------------------


def test_render_current_draws_from_the_capabilitys_own_state() -> None:
    """It never fetches: no run and no reload may happen here."""

    service = _Service(saved=_report())
    page = _Page()
    submit = _Submit()
    orchestrator, _ = _make(service=service, page=page, submit=submit)
    orchestrator.restore_saved()

    orchestrator.render_current()
    orchestrator.render_current()

    assert len(page.rendered) == 3
    assert service.runs == []
    assert service.loads == 1


def test_the_public_surface_is_exactly_the_declared_one() -> None:
    """No ``report`` getter, no ``service``, no ``page``, no ``path``.

    A convenience accessor added "in case" is an accessor the next capability
    starts using, at which point the report truth is no longer self-contained.
    Read from the *source* rather than ``dir()``: a ``QObject`` subclass also
    inherits Qt's own public surface, which is not this capability's API.
    """

    assert _public_names() == {
        "capital_changed",
        "report_changed",
        "refused",
        "log_requested",
        "request_capital_change",
        "request_run",
        "restore_saved",
        "render_current",
    }
