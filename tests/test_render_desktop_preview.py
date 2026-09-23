"""Tooling regression for the desktop preview script.

``scripts/render_desktop_preview.py`` is not a second ``MainWindow``.  It
navigates the real shell, seeds synthetic evidence, emits operator intents,
selects a presentation section by its semantic key and captures a frame.  Every
surface it names must therefore be one of:

* a public page method or signal (``set_active_detail``, ``replay_requested``);
* a capability's own public query (``scanner_orchestrator.scan``,
  ``task_controller.active_count``).

What it may *not* name is a window attribute or handler that an orchestration
migration already retired, nor a widget inside a page.  Each guard below is
paired with the positive assertion that keeps it non-vacuous: a test that only
banned a spelling would also pass against an empty script.

The bans are deliberately a named list rather than "every ``window._*``".
One private dependency is still legitimate here -- ``_populate_auto_quant_
candidates`` (AutoQuant preparation, v2O-E) -- and a blanket rule would block the
next migration instead of protecting the boundaries these rounds repaired.

Targeted is no longer one of these: v2O-C5B moved the session half's refresh
behind the capability, so the script now names
``targeted_session_orchestrator.refresh_minute_status`` -- a public command on the
object that owns the minute status -- rather than a window handler.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_PREVIEW_PATH = _REPO_ROOT / "scripts" / "render_desktop_preview.py"


def _preview_module():
    spec = importlib.util.spec_from_file_location(
        "render_desktop_preview_under_test",
        _PREVIEW_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source() -> str:
    return _PREVIEW_PATH.read_text(encoding="utf-8")


#: The window surface the orchestrations already retired, plus the two the
#: mutable worker collection.  Checked as attribute *uses* rather than as
#: substrings, because the module docstring legitimately explains the
#: retirements and ``window.scanner_orchestrator.scan`` contains ``window.scan``.
RETIRED_WINDOW_SURFACE = (
    "scan",
    "auto_summary_label",
    "auto_detail_tabs",
    "target_symbol_input",
    "targeted_workspace_tabs",
    "targeted_research_tabs",
    "targeted_robustness_detail_tabs",
    "targeted_review_detail_tabs",
    "workers",
    "_run_targeted_replay",
    "_run_targeted_robustness",
    "_run_backtest_workspace",
)


def _window_attributes() -> set[str]:
    """Every ``window.<attr>`` the script reads, as an attribute use."""

    attributes: set[str] = set()
    for node in ast.walk(ast.parse(_source())):
        if not isinstance(node, ast.Attribute):
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "window":
            attributes.add(node.attr)
    return attributes


def _window_method_calls() -> set[str]:
    """Every ``window.<attr>(...)`` the script calls."""

    calls: set[str] = set()
    for node in ast.walk(ast.parse(_source())):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "window"
        ):
            calls.add(func.attr)
    return calls


def _canonical_scan_reads() -> int:
    """How many times the script reads ``window.scanner_orchestrator.scan``."""

    reads = 0
    for node in ast.walk(ast.parse(_source())):
        if not isinstance(node, ast.Attribute) or node.attr != "scan":
            continue
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and owner.attr == "scanner_orchestrator"
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "window"
        ):
            reads += 1
    return reads


# -- the retired surface is gone -------------------------------------------


@pytest.mark.parametrize("surface", RETIRED_WINDOW_SURFACE)
def test_the_preview_names_no_retired_window_surface(surface: str) -> None:
    """No retired attribute is read, and no retired handler is called."""

    used = _window_attributes() | _window_method_calls()
    assert surface not in used, surface


def test_the_preview_really_does_use_the_window() -> None:
    """The ban above cannot pass by the script having stopped driving it."""

    used = _window_attributes()
    for required in (
        "shell",
        "research_page",
        "system_page",
        "execution_page",
        "targeted_validation_page",
        "scanner_orchestrator",
        "task_controller",
        "backtest_page",
        "backtest_orchestrator",
        "minute_quote_store",
        "settings_page",
    ):
        assert required in used, required


# -- scanner truth comes from the capability -------------------------------


def test_the_preview_reads_the_canonical_scan() -> None:
    # Read once into a local, so the script cannot drift between two reads of
    # the capability it does not own.
    assert _canonical_scan_reads() == 1


# -- execution page: summary, detail navigation ----------------------------


def test_the_preview_renders_the_execution_summary_through_the_page() -> None:
    source = _source()
    assert "execution_page.render_context(" in source
    assert "auto_summary_label" not in source


def test_the_preview_selects_execution_details_by_semantic_key() -> None:
    source = _source()
    assert "ExecutionDetailWorkspace.ORDERS" in source
    assert "ExecutionDetailWorkspace.PORTFOLIO" in source
    assert "execution_page.set_active_detail(" in source
    # Not the widget, and not a bare index.
    assert "execution_page.details" not in source
    assert "setCurrentIndex" not in source


def _attributes_of(handle: str) -> set[str]:
    """Every ``window.<handle>.<attr>`` the script names.

    ``window.execution_page.set_active_detail`` yields ``set_active_detail``:
    the page handle is the only part of the path the script is allowed to know.
    """

    attributes: set[str] = set()
    for node in ast.walk(ast.parse(_source())):
        if not isinstance(node, ast.Attribute):
            continue
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and owner.attr == handle
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "window"
        ):
            attributes.add(node.attr)
    return attributes


def test_the_preview_does_not_reach_into_the_execution_page() -> None:
    """Only the page's own public surface, never a widget inside it."""

    assert _attributes_of("execution_page") <= {
        "render_context",
        "set_active_detail",
    }, sorted(_attributes_of("execution_page"))
    # And it really does drive the page, so the guard cannot pass by silence.
    assert "set_active_detail" in _attributes_of("execution_page")


# -- targeted page: intent, navigation -------------------------------------


def test_the_preview_emits_the_target_symbol_intent() -> None:
    source = _source()
    assert "target_apply_requested.emit(" in source
    assert "target_symbol_input" not in source
    assert "_apply_target_symbol" not in source


def test_the_preview_emits_the_targeted_run_intents() -> None:
    source = _source()
    assert "replay_requested.emit()" in source
    assert "robustness_requested.emit()" in source


@pytest.mark.parametrize(
    "key",
    (
        "TargetedWorkspace.EVIDENCE",
        "TargetedWorkspace.PREFLIGHT",
        "TargetedEvidenceWorkspace.ROBUSTNESS",
        "TargetedEvidenceWorkspace.REVIEW",
        "TargetedRobustnessDetail.SCENARIOS",
        "TargetedReviewDetail.GATES",
    ),
)
def test_the_preview_uses_the_semantic_targeted_keys(key: str) -> None:
    assert key in _source(), key


def test_the_preview_selects_targeted_panels_through_the_page() -> None:
    source = _source()
    for method in (
        "set_active_workspace(",
        "set_active_evidence_workspace(",
        "set_active_robustness_detail(",
        "set_active_review_detail(",
    ):
        assert method in source, method
    # The panel is an implementation detail of the page, so the script may not
    # take a handle on it.
    assert "evidence_panel" not in source
    assert "workspace_tabs" not in source


def test_the_preview_does_not_reach_into_the_targeted_page() -> None:
    """Only the page's own public surface, never a panel inside it."""

    named = _attributes_of("targeted_validation_page")
    assert named <= {
        "set_active_workspace",
        "set_active_evidence_workspace",
        "set_active_robustness_detail",
        "set_active_review_detail",
        "target_apply_requested",
        "replay_requested",
        "robustness_requested",
    }, sorted(named)
    # And it really does drive the page, so the guard cannot pass by silence.
    assert "set_active_workspace" in named
    assert "replay_requested" in named


# -- waiting reads a public query ------------------------------------------


def test_the_preview_waits_on_the_task_controller() -> None:
    source = _source()
    assert "task_controller.active_count" in source
    assert "window.workers" not in source


def test_the_preview_timeout_fails_loudly() -> None:
    """The wait helper raises; nothing catches it and carries on."""

    tree = ast.parse(_source())
    assert "raise TimeoutError(" in _source()

    # No handler anywhere in the script swallows an exception, and the wait
    # helper has no ``except`` of its own to fall through from.
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            assert node.body, "an empty except would swallow the timeout"
            assert not all(
                isinstance(statement, ast.Pass) for statement in node.body
            ), "a pass-only except would swallow the timeout"
    assert "contextlib.suppress" not in _source()


# -- the helper is exercised directly --------------------------------------


class _FakeTaskController:
    def __init__(self, active: int) -> None:
        self.active_count = active


class _FakeWaitWindow:
    def __init__(self, active: int) -> None:
        self.task_controller = _FakeTaskController(active)


class _FakeApplication:
    def __init__(self) -> None:
        self.pumped = 0

    def processEvents(self) -> None:
        self.pumped += 1


def test_waiting_returns_once_no_task_is_active() -> None:
    module = _preview_module()
    application = _FakeApplication()
    window = _FakeWaitWindow(active=0)

    module._wait_for_tasks(
        window,
        application,
        timeout_seconds=1.0,
        step="fake step",
    )

    assert application.pumped >= 1


def test_waiting_raises_when_the_deadline_passes(monkeypatch) -> None:
    module = _preview_module()
    application = _FakeApplication()
    window = _FakeWaitWindow(active=3)

    # A clock that advances on every read, and a sleep that does not actually
    # wait: the test must not spend the timeout it is asserting.
    ticks = iter(range(0, 1_000))
    monkeypatch.setattr(module, "monotonic", lambda: float(next(ticks)))
    monkeypatch.setattr(module, "sleep", lambda _seconds: None)

    with pytest.raises(TimeoutError) as error:
        module._wait_for_tasks(
            window,
            application,
            timeout_seconds=1.0,
            step="多日稳健性评估",
        )

    assert "多日稳健性评估" in str(error.value)


def test_waiting_drains_the_queued_completion_before_returning(
    monkeypatch,
) -> None:
    """A worker stops its thread before its result is drawn.

    The helper must pump once more after the count reaches zero, or a screenshot
    can be captured between "thread finished" and "result published".
    """

    module = _preview_module()

    class _CountdownController:
        def __init__(self) -> None:
            self.active_count = 1

        def settle(self) -> None:
            self.active_count = 0

    controller = _CountdownController()
    window = _FakeWaitWindow(active=1)
    window.task_controller = controller

    pumped_before_settle: list[int] = []

    class _SettlingApplication:
        def __init__(self) -> None:
            self.pumped = 0

        def processEvents(self) -> None:
            self.pumped += 1
            pumped_before_settle.append(self.pumped)

    application = _SettlingApplication()
    monkeypatch.setattr(module, "sleep", lambda _seconds: controller.settle())

    module._wait_for_tasks(
        window,
        application,
        timeout_seconds=5.0,
        step="fake step",
    )

    # One pump while the task looked active, then the post-zero drain.
    assert application.pumped >= 2


# -- the script is still a real script -------------------------------------


#: The literal artifact names the script must still write.  The eight targeted
#: light twins are built from a theme suffix and are asserted separately.
ARTIFACT_NAMES = (
    "desktop_preview.png",
    "desktop_auto_quant_preview.png",
    "desktop_auto_orders_preview.png",
    "desktop_account_preview.png",
    "desktop_quotes_preview.png",
    "desktop_strategy_manager_preview.png",
    "desktop_backtest_preview.png",
    "desktop_scanner_preview.png",
    "desktop_strategy_preview.png",
    "desktop_runtime_preview.png",
    "desktop_settings_dark.png",
    "desktop_preview_light.png",
    "desktop_auto_quant_preview_light.png",
    "desktop_auto_orders_preview_light.png",
    "desktop_quotes_preview_light.png",
    "desktop_settings_light.png",
)

#: The targeted screenshots whose names are formed from a theme suffix.
THEME_SUFFIXED_ARTIFACTS = (
    "desktop_shadow_preview",
    "desktop_robustness_preview",
    "desktop_walk_forward_preview",
    "desktop_overfit_preview",
    "desktop_data_quality_preview",
    "desktop_execution_stress_preview",
    "desktop_review_preview",
    "desktop_target_preflight_preview",
)


@pytest.mark.parametrize("name", ARTIFACT_NAMES)
def test_the_preview_still_captures_each_artifact(name: str) -> None:
    """The guards above cannot pass by the script having been emptied."""

    assert name in _source(), name


@pytest.mark.parametrize("stem", THEME_SUFFIXED_ARTIFACTS)
def test_the_preview_keeps_the_theme_suffixed_artifacts(stem: str) -> None:
    """Both themes are captured from the one name, via the suffix."""

    assert f'f"{stem}{{suffix}}.png"' in _source(), stem


def test_the_preview_captures_both_themes_of_every_targeted_artifact() -> None:
    """The dark and light suites really are both called."""

    source = _source()
    assert '_capture_targeted_suite(window, "")' in source
    assert '_capture_targeted_suite(window, "_light")' in source
    assert '_capture_targeted_console(window, "")' in source
    assert '_capture_targeted_console(window, "_light")' in source


# -- backtest, kept from the previous round --------------------------------


class _FakeBacktestPage:
    def __init__(self, draft: object) -> None:
        self._draft = draft

    def current_draft(self) -> object:
        return self._draft


class _FakeBacktestOrchestrator:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def request_selected(self, draft: object) -> None:
        self.requests.append(draft)


class _FakeWindow:
    def __init__(self, draft: object) -> None:
        self.backtest_page = _FakeBacktestPage(draft)
        self.backtest_orchestrator = _FakeBacktestOrchestrator()

    @property
    def seen(self) -> list[object]:
        return self.backtest_orchestrator.requests


def test_preview_uses_current_draft_for_backtest_orchestration() -> None:
    draft = object()
    window = _FakeWindow(draft)
    _preview_module()._start_backtest_preview(window)
    assert window.seen == [draft]


def test_preview_does_not_call_the_retired_window_handler() -> None:
    source = _source()
    assert "_run_backtest_workspace" not in source
    assert "backtest_page.current_draft()" in source
    assert "backtest_orchestrator.request_selected" in source


class _FakeShell:
    def __init__(self) -> None:
        self.routes: list[str] = []

    def navigate_to(self, route: str) -> None:
        self.routes.append(route)


class _FakeResearchPage:
    def __init__(self) -> None:
        self.workspaces: list[object] = []

    def set_active_workspace(self, workspace: object) -> None:
        self.workspaces.append(workspace)


class _FakeSelectWindow:
    def __init__(self) -> None:
        self.shell = _FakeShell()
        self.research_page = _FakeResearchPage()


def test_preview_select_research_uses_the_semantic_workspace() -> None:
    module = _preview_module()
    window = _FakeSelectWindow()

    module.select_research(
        window,
        module.ResearchWorkspace.BACKTEST,
    )

    assert window.shell.routes == ["research"]
    assert window.research_page.workspaces == [
        module.ResearchWorkspace.BACKTEST
    ]
