"""Architecture guards for the v2O-C4 cross-section orchestration extraction.

Structural, not behavioural: these read the source tree and assert that the
ownership the extraction moved really moved, and that the new capability cannot
reach sideways into another workflow.  The behaviour lives in
``tests/test_desktop_cross_section_orchestrator.py``; the real wiring in
``tests/test_desktop_v2_cross_section_wiring.py``.

The four that matter most:

* the window holds **no** ``cross_section_report``, no ``cross_section_path``
  and no research-capital scalar, and declares no compatibility property or
  forwarding method for any of them.  A forwarding method is the tempting way
  to keep a small diff, and it is a trap: every consumer keeps working without
  naming the owner, so "who reads the report / the scenario capital" stops
  being one grep;
* ``CrossSectionResearchPage.render`` has exactly one production caller -- the
  capability.  A second caller is a second state that can paint the page;
* the capability imports no other orchestrator, no Dashboard, no artifact
  catalogue and no Paper/Shadow/Execution module.  Its universe arrives as a
  ``Callable`` provider and its capital as a state object it does not own;
* the dependency guard is an **allowlist**, not a list of bans.  A ban list is
  written in capability vocabulary and is structurally blind to the symbol that
  actually needs forbidding living somewhere the vocabulary does not cover.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP = _SRC / "desktop.py"
_RESEARCH = _SRC / "desktop_v2" / "orchestration" / "research"
_CROSS_SECTION_DIR = _RESEARCH / "cross_section"
_ORCHESTRATOR_PATH = _CROSS_SECTION_DIR / "orchestrator.py"
_SCENARIO_CAPITAL_PATH = _RESEARCH / "scenario_capital.py"
_SERVICE_PATH = _SRC / "desktop_cross_section_service.py"

#: Spec 70: the per-file line budgets.  Exceeding one means a responsibility
#: leaked in (Dashboard, Account, Targeted, Scanner, Risk); raise the *code's*
#: separation, not these numbers.
ORCHESTRATOR_LINE_BUDGET = 300
SCENARIO_CAPITAL_LINE_BUDGET = 80
SERVICE_LINE_BUDGET = 180

#: Spec 32: exactly which modules each file may import.  Compared for
#: **equality**, so an undeclared dependency fails rather than passing silently.
ALLOWED_ORCHESTRATOR_IMPORTS = {
    "__future__",
    "collections.abc",
    "PySide6.QtCore",
    "us_quant.desktop_cross_section_service",
    "us_quant.desktop_v2.orchestration.research.scenario_capital",
    "us_quant.desktop_v2.orchestration.tasking",
    "us_quant.desktop_v2.pages.research.cross_section.models",
    "us_quant.desktop_v2.pages.research.cross_section.presenter",
    "us_quant.universe",
}

ALLOWED_SCENARIO_CAPITAL_IMPORTS = {"__future__", "decimal"}

ALLOWED_SERVICE_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "decimal",
    "pathlib",
    "us_quant.config",
    "us_quant.executable_research",
    "us_quant.universe",
}

#: Spec 33: symbols the capability may never import, by name.  A module-path
#: guard cannot see ``from <allowed_module> import <forbidden>``.
FORBIDDEN_SYMBOLS = (
    "MainWindow",
    "UniverseOrchestrator",
    "ScannerOrchestrator",
    "HistoryOrchestrator",
    "BacktestOrchestrator",
    "AccountOrchestrator",
    "MarketOrchestrator",
    "PaperWorkflow",
    "PaperWorkflowPhase",
    "PaperTradingService",
    "TradingRuntime",
    "ExecutionApplication",
    "RiskApplication",
    "StrategyApplication",
    "ShadowPaperEngine",
    "ShadowWorkflowController",
    "RuntimeSupervisor",
    "TaskThread",
    "DesktopTaskController",
    "ArtifactCatalog",
    "DashboardPage",
    "QWidget",
    "QMessageBox",
)

#: Spec 33: the same ban as module paths.  ``Paper*`` / ``Shadow*`` /
#: ``Execution*`` are prefixes rather than exact names, and a symbol guard would
#: miss ``import us_quant.trading.application.paper_session`` entirely.
FORBIDDEN_MODULE_PREFIXES = (
    "us_quant.desktop",
    "us_quant.desktop_v2.orchestration.account",
    "us_quant.desktop_v2.orchestration.market",
    "us_quant.desktop_v2.orchestration.research.universe",
    "us_quant.desktop_v2.orchestration.research.history",
    "us_quant.desktop_v2.orchestration.research.scanner",
    "us_quant.desktop_v2.orchestration.research.backtest",
    "us_quant.desktop_v2.pages.dashboard",
    "us_quant.desktop_workers",
    "us_quant.desktop_tasks",
    "us_quant.runtime_supervisor",
    "us_quant.trading.runtime",
    "us_quant.trading.application.risk",
    "us_quant.shadow",
    "us_quant.paper",
    "us_quant.execution",
    "us_quant.backtest",
    "us_quant.cross_sectional",
)

#: The two ``us_quant.desktop*`` modules the capability and its services may
#: reach: its own service and the model module next to it.
ALLOWED_DESKTOP_MODULES = (
    "us_quant.desktop_cross_section_service",
)

#: Spec 54: these must never be declared.  One object owning the scenario
#: capital *and* the universe *and* the scanner is the bag this stage forbids.
FORBIDDEN_AGGREGATES = (
    "ResearchOrchestrator",
    "ResearchManager",
    "ResearchContext",
    "ResearchState",
    "ResearchServices",
    "DesktopContext",
    "CapitalAllocator",
    "CapitalManager",
    "PortfolioCapital",
    "TradingCapital",
    "AvailableCapital",
)

#: Spec 51/52: the window state the extraction deleted rather than shimmed.
RETIRED_WINDOW_STATE_NEEDLES = (
    "self.cross_section_report",
    "self.cross_section_path",
    "self._research_capital_value",
)

#: Spec 51/52: the window methods the extraction deleted rather than forwarded.
RETIRED_WINDOW_METHODS = (
    "_publish_cross_section_view",
    "_run_cross_section_research",
    "_cross_section_finished",
    "_load_cross_section_report",
    "_research_scenario_capital",
    "_research_capital_changed",
)

#: Spec 9/35: names that would turn the capability into a domain facade or
#: re-expose the truth the extraction made private.
FORBIDDEN_ACCESSORS = (
    "report",
    "metrics",
    "path",
    "service",
    "page",
    "research_config",
    "capital_state",
)

#: Spec 53: the only window methods whose name starts with a cross-section word.
ALLOWED_WINDOW_CROSS_SECTION_METHODS = (
    "_connect_cross_section_page",
    "_on_cross_section_report_changed",
    "_report_cross_section_refusal",
)


# -- helpers ------------------------------------------------------------


def _python_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: pathlib.Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imported_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _main_window() -> ast.ClassDef:
    for node in _tree(_DESKTOP).body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise AssertionError("MainWindow not found")


def _method_source(name: str) -> str | None:
    source = _DESKTOP.read_text(encoding="utf-8")
    for node in _main_window().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    return None


def _lines(path: pathlib.Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _calls_attr(path: pathlib.Path, attribute: str) -> bool:
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == attribute:
                return True
    return False


def _method_body(name: str) -> str:
    """The source text of one ``_report_finished``-style method.

    Kept local to this file: one ordering guard is not the repeated consumer
    that would justify a shared helper module.
    """

    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    for node in ast.walk(_tree(_ORCHESTRATOR_PATH)):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name != "CrossSectionOrchestrator":
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == name:
                return ast.get_source_segment(source, item) or ""
    raise AssertionError(f"{name} not found")


# -- the window no longer owns the cross-section runtime ----------------


@pytest.mark.parametrize("needle", RETIRED_WINDOW_STATE_NEEDLES)
def test_the_window_no_longer_holds_the_cross_section_state(
    needle: str,
) -> None:
    source = _DESKTOP.read_text(encoding="utf-8")
    for line in source.splitlines():
        # Comments may *name* the retired attributes while explaining their
        # absence; a declaration is what this guard forbids.
        if line.lstrip().startswith("#"):
            continue
        assert needle not in line, line


@pytest.mark.parametrize("name", RETIRED_WINDOW_METHODS)
def test_the_window_no_longer_declares_the_retired_method(
    name: str,
) -> None:
    assert _method_source(name) is None, f"{name} must be retired"


def test_the_window_declares_no_cross_section_compatibility_property() -> None:
    """Spec 51: no forwarding property, in either spelling.

    ``@property def cross_section_report(...)`` and a plain class-level
    assignment would both keep every unmigrated consumer working, which is
    exactly the state the extraction must not leave behind.
    """

    for node in _main_window().body:
        if isinstance(node, ast.FunctionDef) and node.name in (
            "cross_section_report",
            "cross_section_path",
            "research_scenario_capital_value",
        ):
            raise AssertionError(f"compatibility property {node.name!r}")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            for target in targets:
                if isinstance(target, ast.Name) and target.id in (
                    "cross_section_report",
                    "cross_section_path",
                ):
                    raise AssertionError(
                        f"compatibility attribute {target.id!r}"
                    )


def test_the_window_keeps_only_the_declared_cross_section_methods() -> None:
    """Composition, three bridges and the refusal dialog -- nothing else.

    Asserted as a set rather than by listing the retired names: a *new*
    cross-section-looking handler added later would pass a removal-only check.
    """

    declared = {
        node.name
        for node in _main_window().body
        if isinstance(node, ast.FunctionDef)
        and "cross_section" in node.name
    }
    assert declared == set(ALLOWED_WINDOW_CROSS_SECTION_METHODS), declared


def test_the_window_uses_the_two_named_cross_section_bridges() -> None:
    """The capital bridge and the report bridge are the declared pair.

    Both are thin by construction: neither stores the fact it routes.
    """

    capital = _method_source("_on_research_scenario_capital_changed")
    assert capital is not None
    assert "_publish_account_presentation_inputs()" in capital
    assert "render_current()" in capital
    # It must not become a second capital truth.
    assert "research_scenario_capital.set(" not in capital

    report = _method_source("_on_cross_section_report_changed")
    assert report is not None
    assert "load_artifact_catalog(" in report
    assert "dashboard_orchestrator.render_current()" in report


def test_the_window_never_paints_or_edits_the_cross_section_page() -> None:
    """Spec 49/50: the capability paints it and the page is the only editor."""

    source = _DESKTOP.read_text(encoding="utf-8")
    for forbidden in (
        "cross_section_page.render(",
        "cross_section_page.set_research_capital(",
    ):
        assert forbidden not in source, forbidden


def test_the_capability_is_the_only_caller_of_the_page_render() -> None:
    """The positive half: one caller, and it is the capability.

    Scoped to the cross-section package and the window, because those are the
    only production places that could hold a reference to *this* page.  Paired
    with ``test_the_window_never_paints_or_edits_the_cross_section_page``
    above, the two together say: the page has exactly one painter.
    """

    callers = [
        path
        for path in _python_files(_CROSS_SECTION_DIR)
        if _calls_attr(path, "render")
    ]
    assert callers == [_ORCHESTRATOR_PATH], [
        path.name for path in callers
    ]
    # And the window is not one of them.  Asserted on the receiver rather than
    # on ``.render`` generally: the window legitimately paints its *other*
    # pages, which is not this guard's subject.
    assert "cross_section_page.render" not in _DESKTOP.read_text(
        encoding="utf-8"
    )


def test_the_window_does_not_reach_into_the_capability() -> None:
    """No ``cross_section_orchestrator._x`` from the window."""

    offenders: list[str] = []
    for node in ast.walk(_main_window()):
        if not isinstance(node, ast.Attribute) or not node.attr.startswith("_"):
            continue
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and owner.attr == "cross_section_orchestrator"
        ):
            offenders.append(f"cross_section_orchestrator.{node.attr}")
    assert not offenders, offenders


def test_the_window_constructs_each_object_exactly_once() -> None:
    """A second page or service would be one the capability cannot see."""

    source = _DESKTOP.read_text(encoding="utf-8")
    assert source.count("CrossSectionResearchPage(") == 1
    assert source.count("DesktopCrossSectionService(") == 1
    assert source.count("CrossSectionOrchestrator(") == 1


# -- the scenario capital is a scalar, not a bag -------------------------


def test_the_scenario_capital_module_is_tiny() -> None:
    assert _lines(_SCENARIO_CAPITAL_PATH) <= SCENARIO_CAPITAL_LINE_BUDGET


def test_the_scenario_capital_imports_only_the_stdlib() -> None:
    assert _imports(_SCENARIO_CAPITAL_PATH) <= ALLOWED_SCENARIO_CAPITAL_IMPORTS


@pytest.mark.parametrize(
    "forbidden",
    ("PySide6", "QObject", "MainWindow", "Broker", "Risk", "Execution"),
)
def test_the_scenario_capital_reaches_no_trading_or_ui_layer(
    forbidden: str,
) -> None:
    source = _SCENARIO_CAPITAL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = ""
    assert forbidden not in ast.unparse(tree), forbidden


def test_the_scenario_capital_holds_one_attribute() -> None:
    """Spec 55: it is not a ``ResearchState`` bag -- ``self.universe`` fails."""

    tree = ast.parse(_SCENARIO_CAPITAL_PATH.read_text(encoding="utf-8"))
    attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    assert attributes == {"_value"}, attributes


# -- the capability's dependencies --------------------------------------


def test_the_orchestrator_stays_inside_its_budget() -> None:
    assert _lines(_ORCHESTRATOR_PATH) <= ORCHESTRATOR_LINE_BUDGET


def test_the_service_stays_inside_its_budget() -> None:
    assert _lines(_SERVICE_PATH) <= SERVICE_LINE_BUDGET


def test_the_orchestrator_imports_nothing_outside_the_allowlist() -> None:
    offending = sorted(
        _imports(_ORCHESTRATOR_PATH) - ALLOWED_ORCHESTRATOR_IMPORTS
    )
    assert not offending, offending


def test_the_service_imports_nothing_outside_the_allowlist() -> None:
    offending = sorted(_imports(_SERVICE_PATH) - ALLOWED_SERVICE_IMPORTS)
    assert not offending, offending


def test_the_package_initializer_is_lazy_and_allowlisted() -> None:
    """The package imports its own orchestrator module and nothing else.

    A relative import is the house style for a package initializer, so the
    recorded module path is the bare ``orchestrator``; ``test_trading_
    architecture`` already whitelists the package itself.
    """

    path = _CROSS_SECTION_DIR / "__init__.py"
    assert _imports(path) <= {"__future__", "orchestrator"}


def test_the_capability_imports_no_forbidden_symbol() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_CROSS_SECTION_DIR) + [_SCENARIO_CAPITAL_PATH]:
        for name in _imported_names(path):
            if name in FORBIDDEN_SYMBOLS:
                offending.append((path.name, name))
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(
                node.func, "attr", None
            )
            if name in FORBIDDEN_SYMBOLS:
                offending.append((path.name, name))
    assert not offending, offending


def test_the_capability_imports_no_forbidden_module() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_CROSS_SECTION_DIR):
        for module in _imports(path):
            allowed = any(
                module == entry or module.startswith(f"{entry}.")
                for entry in ALLOWED_DESKTOP_MODULES
            )
            if allowed:
                continue
            for prefix in FORBIDDEN_MODULE_PREFIXES:
                if module == prefix or module.startswith(f"{prefix}."):
                    offending.append((path.name, module))
    assert not offending, offending


def test_no_cross_section_aggregate_is_declared() -> None:
    """Spec 54: no ``ResearchOrchestrator`` and no premature capital owner.

    ``CapitalAllocator`` in particular: the eventual allocator is built from
    broker truth and portfolio risk, so a research scalar wearing that name
    would invite it into live sizing.
    """

    defined: set[str] = set()
    for path in _python_files(_SRC):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ClassDef):
                defined.add(node.name)
    assert not (defined & set(FORBIDDEN_AGGREGATES)), (
        defined & set(FORBIDDEN_AGGREGATES)
    )


@pytest.mark.parametrize("name", FORBIDDEN_ACCESSORS)
def test_no_internal_is_exposed_as_a_public_name(name: str) -> None:
    """Spec 35: the raw report stays private."""

    for node in ast.walk(_tree(_ORCHESTRATOR_PATH)):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == name:
                raise AssertionError(f"{name!r} is a public accessor")
            if isinstance(item, (ast.Assign, ast.AnnAssign)):
                targets = (
                    item.targets
                    if isinstance(item, ast.Assign)
                    else [item.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        raise AssertionError(f"{name!r} is a public attribute")


def test_the_capability_holds_no_dialog() -> None:
    """A refusal is published, not shown, which is what keeps it widget-free."""

    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert "QMessageBox" not in source
    assert "QWidget" not in source


def test_the_capability_never_holds_the_truth_it_does_not_own() -> None:
    """The report is private and the capital is delegated, never copied."""

    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert "self._capital_value" not in source
    assert "self._research_capital" not in source


def test_the_success_path_prepares_everything_before_it_commits() -> None:
    """The commit line is the last point of no return, so nothing may follow it.

    Read as ordered source, because the defect this pins was an *ordering* bug:
    the completion message used to be built after ``self._report = result``, so a
    report whose numbers the presenter could coerce (``float("0.2")``) but the
    formatter could not (``"0.2":+.1%``) killed the handler *after* the truth had
    moved, the page repainted and ``report_changed`` been emitted.

    So the contract is asserted structurally: the message is built above the
    commit, and the emit below it passes a finished variable rather than
    formatting a field of the result inline.  A reviewer reordering these two
    blocks would reintroduce the window, and this guard fails.
    """

    body = _method_body("_report_finished")
    commit = body.index("self._report = result")
    built = body.index("completion_message = (")
    emitted = body.index("self.log_requested.emit(completion_message)")

    assert built < commit, "the completion message must be built before commit"
    assert commit < emitted, "the emit must happen after the commit"
    # And the emit must not reformat the result inline -- that is the shape that
    # used to raise after the truth had already moved.
    assert "log_requested.emit(\n            f\"" not in body
    assert "{metrics[" not in body


def test_the_service_imports_no_qt() -> None:
    modules = _imports(_SERVICE_PATH)
    assert not any(
        module == "PySide6" or module.startswith("PySide6.")
        for module in modules
    )


def test_the_serializer_and_loader_are_paired_in_one_module() -> None:
    """Spec 27/28: the artifact schema is described where it is written.

    A loader living in the desktop service would mean the schema is guessed in
    one place and defined in another, so a format change would need two edits
    found by grep rather than one module read.
    """

    import us_quant.executable_research as module

    assert hasattr(module, "save_executable_research")
    assert hasattr(module, "load_executable_research")
    assert module.load_executable_research.__module__ == (
        "us_quant.executable_research"
    )
