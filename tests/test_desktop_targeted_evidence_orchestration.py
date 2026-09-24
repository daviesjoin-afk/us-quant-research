"""Architecture guards for the v2O-C5A targeted evidence extraction.

Structural, not behavioural: these read the source tree and assert that the
ownership the extraction moved really moved, and that the new capability cannot
reach sideways into another workflow.  The behaviour lives in
``tests/test_desktop_targeted_evidence_orchestrator.py``; the real wiring in
``tests/test_desktop_v2_targeted_wiring.py``; the artifact boundary in
``tests/test_desktop_targeted_evidence_service.py``.

The guards that matter most:

* the window holds **no** evidence state -- not the seven result lists, not the
  two selected run ids, not the two one-shot tab fields -- and declares no
  compatibility property or forwarding method for any of them.  A forwarding
  method is the tempting way to keep a small diff, and it is a trap: every
  consumer keeps working without naming the owner, so "who owns the robustness
  evidence?" stops being one grep;
* ``render_evidence`` has exactly one production caller -- the capability -- and
  ``render_session`` has exactly one that is *not* the capability.  That is the
  boundary this round establishes, and the asymmetry is deliberate: the evidence
  half moved, the session half did not;
* the capability imports no other orchestrator, no shell, no route, no Shadow
  engine, no market stream and no broker account.  Its universe arrives as a
  ``Callable`` provider, its strategy as a ``Callable``, its symbol as a
  ``Callable`` and its capital as a state object it does not own.

The dependency guard is an **allowlist**, not a list of bans.  A ban list is
written in capability vocabulary and is structurally blind to the symbol that
actually needs forbidding living somewhere the vocabulary does not cover -- so a
new dependency has to be declared on purpose here, in a place a reviewer sees.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP = _SRC / "desktop.py"
_RESEARCH = _SRC / "desktop_v2" / "orchestration" / "research"
_EVIDENCE_DIR = _RESEARCH / "targeted" / "evidence"
_SERVICE_PATH = _SRC / "desktop_targeted_evidence_service.py"
_MODELS_PATH = _SRC / "desktop_targeted_evidence_models.py"
_PAGE_PATH = (
    _SRC / "desktop_v2" / "pages" / "research" / "targeted" / "page.py"
)
_PAGE_MODELS_PATH = (
    _SRC / "desktop_v2" / "pages" / "research" / "targeted" / "models.py"
)

#: The per-file line budgets.  Exceeding one means a responsibility leaked in
#: (Shadow, Market, Account, the preflight, the target session); raise the *code's*
#: separation, not these numbers.
LINE_BUDGETS = {
    "orchestrator.py": 320,
    "models.py": 210,
    "messages.py": 180,
    "projector.py": 60,
    "__init__.py": 60,
}
SERVICE_LINE_BUDGET = 360
NEUTRAL_MODELS_LINE_BUDGET = 200

#: Exactly which modules each file may import.  Compared for **equality**, so an
#: undeclared dependency fails rather than passing silently.
ALLOWED_IMPORTS = {
    "orchestrator.py": {
        "__future__",
        "collections.abc",
        "dataclasses",
        "PySide6.QtCore",
        "us_quant.desktop_targeted_evidence_models",
        "us_quant.desktop_targeted_evidence_service",
        "us_quant.desktop_v2.orchestration.research.scenario_capital",
        "us_quant.desktop_v2.orchestration.research.targeted.evidence",
        "us_quant.desktop_v2.orchestration.research.targeted.evidence.messages",
        "us_quant.desktop_v2.orchestration.research.targeted.evidence.projector",
        "us_quant.desktop_v2.orchestration.tasking",
        "us_quant.desktop_v2.pages.research.targeted.models",
        "us_quant.trading.domain.strategy",
        "us_quant.universe",
    },
    "models.py": {
        "__future__",
        "dataclasses",
        "us_quant.desktop_targeted_evidence_models",
        "us_quant.desktop_v2.orchestration.research.targeted.evidence.messages",
        "us_quant.targeted_data_quality",
        "us_quant.targeted_execution_stress",
        "us_quant.targeted_overfit",
        "us_quant.targeted_replay",
        "us_quant.targeted_review",
        "us_quant.targeted_robustness",
        "us_quant.targeted_validation",
        "us_quant.trading.domain.strategy",
        "us_quant.universe",
    },
    "messages.py": {
        "__future__",
        "re",
        "us_quant.desktop_targeted_evidence_models",
        "us_quant.targeted_replay",
    },
    "projector.py": {
        "__future__",
        "us_quant.desktop_targeted_evidence_models",
        "us_quant.desktop_v2.pages.research.targeted.evidence_presenter",
        "us_quant.desktop_v2.pages.research.targeted.models",
    },
}

#: Symbols the capability may never import, by name.  A module-path guard cannot
#: see ``from <allowed_module> import <forbidden>``, so both directions are checked.
FORBIDDEN_SYMBOLS = (
    "MainWindow",
    "MarketOrchestrator",
    "AccountOrchestrator",
    "UniverseOrchestrator",
    "ScannerOrchestrator",
    "HistoryOrchestrator",
    "BacktestOrchestrator",
    "CrossSectionOrchestrator",
    "ShadowPaperEngine",
    "ShadowPaperStore",
    "ShadowSnapshot",
    "ShadowWorkflowController",
    "PaperWorkflow",
    "PaperWorkflowPhase",
    "PaperTradingService",
    "TradingRuntime",
    "ExecutionApplication",
    "RiskApplication",
    "BrokerAccountApplication",
    "RuntimeSupervisor",
    "TaskThread",
    "DesktopTaskController",
    "DesktopShellV2",
    "ResearchPage",
    "ResearchWorkspace",
    "QWidget",
    "QMessageBox",
)

#: The same ban as module paths.  ``Paper*`` / ``Shadow*`` / ``Execution*`` are
#: prefixes rather than exact names, and a symbol guard would miss
#: ``import us_quant.shadow.engine`` entirely.
FORBIDDEN_MODULE_PREFIXES = (
    "us_quant.desktop_workers",
    "us_quant.desktop_tasks",
    "us_quant.runtime_supervisor",
    "us_quant.desktop_v2.orchestration.account",
    "us_quant.desktop_v2.orchestration.market",
    "us_quant.desktop_v2.orchestration.research.universe",
    "us_quant.desktop_v2.orchestration.research.history",
    "us_quant.desktop_v2.orchestration.research.scanner",
    "us_quant.desktop_v2.orchestration.research.backtest",
    "us_quant.desktop_v2.orchestration.research.cross_section",
    "us_quant.desktop_v2.pages.dashboard",
    "us_quant.desktop_v2.pages.system",
    "us_quant.trading.runtime",
    "us_quant.trading.application.risk",
    "us_quant.shadow",
    "us_quant.paper",
    "us_quant.execution",
    "us_quant.targeted_preflight",
)

#: ``us_quant.desktop`` is the window module, so the capability may not reach it
#: at all -- except that the two ``us_quant.desktop*`` modules it *does* own are
#: explicitly allowed below.  Stated separately so the ban above stays a rule.
FORBIDDEN_EXACT_MODULES = (
    "us_quant.desktop",
)

#: The four ``us_quant.desktop*`` modules the capability and its service may
#: reach: the neutral contract, the service, and nothing else.
ALLOWED_DESKTOP_MODULES = (
    "us_quant.desktop_targeted_evidence_models",
    "us_quant.desktop_targeted_evidence_service",
)

#: These must never be declared.  One object owning the evidence *and* the target
#: session *and* the Shadow runtime is the bag this stage forbids -- it would be a
#: second ``MainWindow`` rather than a decomposition of the first.
FORBIDDEN_AGGREGATES = (
    "TargetedOrchestrator",
    "TargetedManager",
    "TargetedContext",
    "TargetedState",
    "TargetedServices",
    "TargetedEvidenceManager",
    "TargetedEvidenceContext",
    "TargetedEvidenceServices",
    "ResearchOrchestrator",
    "ResearchManager",
    "DesktopContext",
)

#: The files that must never exist: a single targeted orchestrator at the
#: capability root, which is the shape this split exists to prevent.
FORBIDDEN_PATHS = (
    _RESEARCH / "targeted" / "orchestrator.py",
    _RESEARCH / "targeted" / "models.py",
    _RESEARCH / "targeted" / "manager.py",
)

#: Spec: the window state the extraction deleted rather than shimmed.  The three
#: spellings per name cover an annotated declaration, a plain assignment and a read.
#:
#: The session half joined this list in v2O-C5B: the target status, the minute
#: status and the last preflight result left the window with the capability that
#: now owns them.
RETIRED_WINDOW_STATE = (
    "self.targeted_replay_results",
    "self.targeted_robustness_results",
    "self.targeted_walk_forward_results",
    "self.targeted_overfit_results",
    "self.targeted_data_quality_results",
    "self.targeted_execution_stress_results",
    "self.targeted_review_results",
    "self._selected_robustness_run_id",
    "self._selected_review_run_id",
    "self._targeted_active_workspace",
    "self._targeted_active_evidence_tab",
    "self._target_status",
    "self._minute_status",
    "self.target_preflight_result",
)

#: The window methods the extraction deleted rather than forwarded.  The first
#: group went with the evidence half (v2O-C5A); the second went with the session
#: half (v2O-C5B), where a compatibility property or forwarding method would have
#: kept the window working as a second owner.
RETIRED_WINDOW_METHODS = (
    "_run_targeted_replay",
    "_targeted_replay_finished",
    "_run_targeted_robustness",
    "_targeted_robustness_finished",
    "_robustness_run_selected",
    "_review_run_selected",
    "_publish_targeted_view",
    "_current_target_symbol",
    "_target_symbol_requested",
    "_target_subscribe_requested",
    "_apply_target_symbol",
    "_sync_targeted_symbol_to_stream",
    "_refresh_minute_data_status",
    "_refresh_target_preflight",
    "_targeted_controls",
    "_publish_targeted_session_view",
    "_shadow_strategy_selection_changed",
)

#: The retired presentation model.  A combined view model would let the window
#: hand over session and evidence truth in one call again, which is exactly the
#: coupling the render split removed.
RETIRED_PAGE_MODELS = ("TargetedValidationView",)

#: The retired page API: a combined render that draws both halves.
RETIRED_PAGE_METHODS = ("render",)

#: The only window methods whose name starts with a targeted word.
#:
#: v2O-C5B removed the session half's handlers -- the target apply/subscribe
#: commands, the minute and preflight refreshes, the controls projection and the
#: session paint -- and added the small composition helpers the session capability
#: reads its inputs through.  Those helpers answer "what is the current value of a
#: fact this window can see?"; none of them decides anything.
ALLOWED_WINDOW_TARGETED_METHODS = (
    "_connect_targeted_validation_page",
    "_report_targeted_session_refusal",
    "_report_targeted_evidence_refusal",
    "_focus_targeted_evidence",
    "_selected_shadow_strategy_record",
    "_shadow_capital_fact",
    "_shadow_account_alias",
    "_report_shadow_refusal",
    "_paper_runtime_is_active",
    "_targeted_account_snapshot",
    "_targeted_displayed_strategy",
    "_targeted_strategy_options",
    "_targeted_strategy_selected",
)


# -- helpers --------------------------------------------------------------


def _python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _imports(path: pathlib.Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imported_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def _main_window(path: pathlib.Path) -> ast.ClassDef:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise AssertionError("MainWindow not found")


def _declared_names(path: pathlib.Path) -> set[str]:
    """Every name ``MainWindow`` declares: methods, properties and assignments."""

    declared: set[str] = set()
    for node in _main_window(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            declared.add(node.name)
    for node in ast.walk(_main_window(path)):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "self":
                declared.add(node.attr)
    return declared


def _called_pieces(path: pathlib.Path) -> set[tuple[str, str]]:
    """Every ``<expr>.<attr>(...)`` in ``path`` as ``(owner, attr)``.

    A call guard has to see the *receiver*, not just the method name: the point
    of this round is which object may call ``render_evidence``, and a name-only
    search would count the page's own definition.
    """

    pieces: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            owner = ast.unparse(func.value)
            pieces.add((owner, func.attr))
    return pieces


# -- Guard A: the window holds no evidence state --------------------------


@pytest.mark.parametrize("needle", RETIRED_WINDOW_STATE)
def test_the_window_holds_no_evidence_state(needle: str) -> None:
    """Checked against the parsed code, not the file text.

    A comment that *names* a retired attribute to explain what replaced it is not
    a second truth, and a text search would flag it -- so the check is on real
    attribute access, which is what a mirror list would have to be.
    """

    tree = ast.parse(_DESKTOP.read_text(encoding="utf-8"))
    accessed = {
        f"self.{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    assert needle not in accessed, needle


@pytest.mark.parametrize("name", RETIRED_WINDOW_METHODS)
def test_the_window_declares_no_retired_evidence_method(name: str) -> None:
    """No compatibility alias, property or forwarding method restores the old surface."""

    assert name not in _declared_names(_DESKTOP), name


def test_the_window_declares_no_targeted_method_outside_the_declared_set() -> None:
    """One grep answers "what does the window still do for Targeted?".

    Methods only.  The window legitimately *holds* the targeted page, the
    capability and the service, plus the session-side status strings that are
    v2O-C5B's to move -- those are composition, not behaviour, and the state guard
    above is what pins what state may exist.
    """

    declared = {
        node.name
        for node in _main_window(_DESKTOP).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    targeted = {
        name
        for name in declared
        if "target" in name or "evidence" in name
    }
    assert targeted <= set(ALLOWED_WINDOW_TARGETED_METHODS), sorted(
        targeted - set(ALLOWED_WINDOW_TARGETED_METHODS)
    )


def test_the_window_declares_the_four_bridges_it_needs() -> None:
    """The capability publishes facts; the window routes them.  All of them exist.

    v2O-F1 replaced the evidence-specific event adapter with the window's single
    ``_route_runtime_event`` forwarder, so the bridge the evidence capability's
    ``runtime_event_requested`` reaches is that one -- asserted here beside the
    three routes that stayed evidence-specific.
    """

    declared = _declared_names(_DESKTOP)
    for name in (
        "_report_targeted_evidence_refusal",
        "_route_runtime_event",
        "_focus_targeted_evidence",
        "_report_targeted_session_refusal",
    ):
        assert name in declared, name
    assert "_record_targeted_evidence_runtime_event" not in declared


# -- Guard B: exactly one owner of the evidence render --------------------


def _evidence_render_callers() -> list[tuple[str, str]]:
    """Production modules that call ``render_evidence``, as ``(file, owner)``."""

    callers: list[tuple[str, str]] = []
    for path in _python_files(_SRC):
        if path == _PAGE_PATH:
            continue
        for owner, attr in _called_pieces(path):
            if attr == "render_evidence":
                callers.append((path.name, owner))
    return callers


def test_the_evidence_capability_is_the_only_render_evidence_caller() -> None:
    callers = _evidence_render_callers()
    assert callers == [("orchestrator.py", "self._page")], callers


def _session_render_callers() -> list[tuple[str, str]]:
    """Production modules that call ``render_session``, as ``(file, owner)``.

    v2O-C5B closed this list to exactly one: the session capability.  Before it,
    the window was the session painter; the asymmetry with ``render_evidence``
    (which has always had one caller) is now gone, and both halves have exactly
    the owner that holds their truth.
    """

    callers: list[tuple[str, str]] = []
    for path in _python_files(_SRC):
        if path == _PAGE_PATH:
            continue
        for owner, attr in _called_pieces(path):
            if attr == "render_session":
                callers.append((path.name, owner))
    return callers


def test_the_session_capability_is_the_only_render_session_caller() -> None:
    callers = _session_render_callers()
    assert callers == [("orchestrator.py", "self._page")], callers


def test_the_window_paints_neither_half_of_the_targeted_page() -> None:
    """It composes the capabilities and routes their facts; it draws neither half."""

    pieces = {attr for _owner, attr in _called_pieces(_DESKTOP)}
    assert "render_session" not in pieces
    assert "render_evidence" not in pieces


def test_the_window_never_reaches_through_the_page_for_session_truth() -> None:
    """The page's editor is not a truth the window may read.

    The retired ``_current_target_symbol`` read the ``QLineEdit``; the target now
    comes from the session snapshot, so a surviving window-side read of
    ``target_symbol()`` would be the second owner coming back.
    """

    page_reads = {
        attr
        for owner, attr in _called_pieces(_DESKTOP)
        if owner == "self.targeted_validation_page"
    }
    assert "target_symbol" not in page_reads, sorted(page_reads)
    assert "selected_strategy_version_id" not in page_reads, sorted(page_reads)


def test_the_capability_never_calls_the_session_render() -> None:
    source = (_EVIDENCE_DIR / "orchestrator.py").read_text(encoding="utf-8")
    assert "render_session" not in source


def test_the_evidence_orchestrator_publishes_a_focus_request_not_a_route() -> None:
    """The capability asks; the window decides.  Asserted as a declared signal."""

    source = (_EVIDENCE_DIR / "orchestrator.py").read_text(encoding="utf-8")
    assert "focus_requested = Signal()" in source
    assert "minute_status_refresh_requested = Signal(str)" in source


# -- Guard C: the page has two entry points and no combined one -----------


def test_the_page_has_both_render_entry_points() -> None:
    source = _PAGE_PATH.read_text(encoding="utf-8")
    assert "def render_session(" in source
    assert "def render_evidence(" in source


@pytest.mark.parametrize("name", RETIRED_PAGE_METHODS)
def test_the_page_retires_the_combined_render(name: str) -> None:
    """No compatibility wrapper: a wrapper would let the window regain evidence render."""

    assert f"def {name}(" not in _PAGE_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", RETIRED_PAGE_MODELS)
def test_the_combined_view_model_is_retired(name: str) -> None:
    source = _PAGE_MODELS_PATH.read_text(encoding="utf-8")
    assert f"class {name}" not in source, name
    assert f'"{name}"' not in source, name


def test_the_page_runs_nothing() -> None:
    """No executor, no engine, no window: the page draws and emits."""

    identifiers = {
        node.id
        for node in ast.walk(ast.parse(_PAGE_PATH.read_text(encoding="utf-8")))
        if isinstance(node, ast.Name)
    }
    for forbidden in (
        "run_targeted_replay",
        "run_targeted_robustness",
        "run_targeted_review",
        "ShadowPaperEngine",
        "MainWindow",
    ):
        assert forbidden not in identifiers, forbidden


# -- Guard D: the dependency direction ------------------------------------


def test_each_evidence_file_imports_only_what_it_declares() -> None:
    for name, allowed in sorted(ALLOWED_IMPORTS.items()):
        actual = _imports(_EVIDENCE_DIR / name)
        assert actual == allowed, (name, sorted(actual - allowed), sorted(allowed - actual))


def test_the_capability_imports_no_forbidden_symbol() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_EVIDENCE_DIR):
        for name in _imported_names(path):
            if name in FORBIDDEN_SYMBOLS:
                offending.append((path.name, name))
    assert not offending, offending


def test_the_capability_imports_no_forbidden_module() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_EVIDENCE_DIR):
        for module in _imports(path):
            if module in FORBIDDEN_EXACT_MODULES:
                offending.append((path.name, module))
                continue
            if module.startswith("us_quant.desktop") and module not in (
                ALLOWED_DESKTOP_MODULES
            ) and not module.startswith("us_quant.desktop_v2"):
                offending.append((path.name, module))
                continue
            for prefix in FORBIDDEN_MODULE_PREFIXES:
                if module == prefix or module.startswith(f"{prefix}."):
                    offending.append((path.name, module))
    assert not offending, offending


def test_the_service_imports_no_qt_and_no_orchestration() -> None:
    """The arrow points one way.  A reverse import would let the service reach back."""

    modules = _imports(_SERVICE_PATH) | _imports(_MODELS_PATH)
    for module in sorted(modules):
        assert not module.startswith("PySide6"), module
        assert not module.startswith("us_quant.desktop_v2"), module
        assert module != "us_quant.desktop", module


def test_the_neutral_contract_holds_no_qt_and_no_executor() -> None:
    """It is the one module both sides may import, so it must stay inert."""

    modules = _imports(_MODELS_PATH)
    names = _imported_names(_MODELS_PATH)
    assert not any(module.startswith("PySide6") for module in modules)
    for name in names:
        assert not name.startswith(
            ("run_targeted_", "save_targeted_", "load_targeted_")
        ), name


def test_the_service_stays_qt_free_and_widget_free() -> None:
    identifiers = {
        node.id
        for node in ast.walk(ast.parse(_SERVICE_PATH.read_text(encoding="utf-8")))
        if isinstance(node, ast.Name)
    } | {
        node.attr
        for node in ast.walk(ast.parse(_SERVICE_PATH.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute)
    }
    for forbidden in (
        "QMessageBox",
        "QWidget",
        "MainWindow",
        "RuntimeEventStore",
    ):
        assert forbidden not in identifiers, forbidden


def test_the_capability_reads_its_dependencies_through_callables() -> None:
    """The universe, the strategy and the symbol are providers, not objects."""

    orchestrator = (_EVIDENCE_DIR / "orchestrator.py").read_text(encoding="utf-8")
    assert "universe_provider: Callable" in orchestrator
    assert "strategy_provider: Callable" in orchestrator
    assert "target_symbol_provider: Callable" in orchestrator
    # The capital is the shared state object: read but not owned.
    assert "capital_state: ResearchScenarioCapitalState" in orchestrator


# -- Guard E: no aggregate, no forbidden file -----------------------------


@pytest.mark.parametrize("path", FORBIDDEN_PATHS)
def test_the_forbidden_targeted_file_does_not_exist(path: pathlib.Path) -> None:
    assert not path.exists(), path


def test_no_forbidden_aggregate_is_declared() -> None:
    defined: set[str] = set()
    for path in _python_files(_SRC):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ClassDef):
                defined.add(node.name)
    for name in FORBIDDEN_AGGREGATES:
        assert name not in defined, name


def test_the_capability_package_is_not_its_own_home() -> None:
    """The targeted root holds the split's two halves -- and no shared models.

    v2O-C5A moved the evidence half in and v2O-C5B the session half, so the root
    now has two children.  What must never appear there is a ``models.py``,
    ``orchestrator.py`` or ``context.py``: one object owning evidence *and*
    session *and* Shadow would be the second ``MainWindow`` this split exists to
    prevent.
    """

    children = {
        path.name
        for path in (_RESEARCH / "targeted").iterdir()
        if path.name != "__pycache__"
    }
    assert children == {"__init__.py", "evidence", "session"}, children


# -- Guard F: line budgets ------------------------------------------------


@pytest.mark.parametrize(("name", "budget"), sorted(LINE_BUDGETS.items()))
def test_each_evidence_file_stays_inside_its_budget(
    name: str, budget: int
) -> None:
    lines = len((_EVIDENCE_DIR / name).read_text(encoding="utf-8").splitlines())
    assert lines <= budget, f"{name} is {lines} lines, budget {budget}"


def test_the_service_stays_inside_its_budget() -> None:
    lines = len(_SERVICE_PATH.read_text(encoding="utf-8").splitlines())
    assert lines <= SERVICE_LINE_BUDGET, lines


def test_the_neutral_contract_stays_inside_its_budget() -> None:
    lines = len(_MODELS_PATH.read_text(encoding="utf-8").splitlines())
    assert lines <= NEUTRAL_MODELS_LINE_BUDGET, lines


# -- Guard G: the export reads the snapshot, and only reads -------------


def test_the_terminal_export_reads_the_evidence_snapshot() -> None:
    """One read of one published value, and no second truth to prefer."""

    source = _DESKTOP.read_text(encoding="utf-8")
    assert "self.targeted_evidence_orchestrator.snapshot" in source


def test_the_window_holds_no_targeted_results_cache() -> None:
    """The export is a reader; a cache would be a second truth by another name."""

    declared = _declared_names(_DESKTOP)
    for name in (
        "targeted_results",
        "targeted_results_cache",
        "export_targeted_results",
    ):
        assert name not in declared, name


def test_the_export_does_not_write_evidence_state() -> None:
    """A read-only consumer: no selection edit, no commit, no research trigger.

    v2O-F1 renamed the window's half of the export: it is the composition
    provider ``_export_runtime_bundle`` the Runtime Events orchestrator calls,
    so the property this guard locks is unchanged and the method it inspects
    moved with the sequencing.
    """

    tree = ast.parse(_DESKTOP.read_text(encoding="utf-8"))
    method = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_export_runtime_bundle":
            method = node
    assert method is not None, "MainWindow._export_runtime_bundle not found"

    body = ast.unparse(method)
    for forbidden in (
        "select_robustness_run",
        "select_review_run",
        "request_replay",
        "request_robustness",
        "commit_",
        "render_evidence",
    ):
        assert forbidden not in body, forbidden


# -- Guard H: startup paints the evidence exactly once -------------------


def test_startup_restores_the_evidence_without_a_second_evidence_paint() -> None:
    """``restore_saved()`` paints once, so a ``render_current()`` next to it doubles it.

    The window's startup calls ``restore_saved()`` and then paints the *session*
    half.  Adding an evidence ``render_current()`` there would repaint seven
    research tables for a file that was merely re-read -- the same waste the
    render split removed, reintroduced at the one place nobody would look.
    """

    tree = ast.parse(_DESKTOP.read_text(encoding="utf-8"))
    restore = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_load_local_state"
        ):
            restore = node
    assert restore is not None, "MainWindow._load_local_state not found"

    evidence_calls = [
        ast.unparse(node)
        for node in ast.walk(restore)
        if isinstance(node, ast.Call)
        and "targeted_evidence_orchestrator" in ast.unparse(node)
    ]
    assert evidence_calls, "startup must restore the evidence"
    for call in evidence_calls:
        assert "restore_saved" in call, call
        assert "render_current" not in call, call
        assert "render_evidence" not in call, call


def test_startup_uses_one_call_for_all_seven_families() -> None:
    """No hand-written loader per family: a family missed renders as empty forever."""

    body = _DESKTOP.read_text(encoding="utf-8")
    for name in (
        "load_targeted_replays",
        "load_targeted_robustness",
        "load_targeted_walk_forwards",
        "load_targeted_overfits",
        "load_targeted_data_quality",
        "load_targeted_execution_stress",
        "load_targeted_reviews",
    ):
        assert name not in body, name
