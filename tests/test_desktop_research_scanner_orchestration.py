"""Architecture guards for the v2O-C2 scanner orchestration extraction.

Spec 39/42/43/44/35/36.  These are structural, not behavioural: they read the
source and assert what may and may not exist, so a future edit that quietly
reintroduces a second scan owner fails here rather than in production.

The three that matter most for this stage:

* the window holds **no** ``self.scan`` and no compatibility property forwarding
  to the capability.  A forwarding property is the tempting way to keep a diff
  small, and it is a trap: it keeps every unmigrated consumer silently working,
  so "who reads scan truth" stops being one grep;
* ``ScannerPage.render`` and ``ScannerPage.render_chart`` have exactly one
  caller -- the capability.  A second caller is a second state that can paint
  the page;
* the capability imports no other orchestrator and no Paper/Shadow/Execution/
  Market/Account module.  The direction of the AutoQuant hand-off is
  AutoQuant -> Scanner, so Scanner must never learn that those exist.

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
_SCANNER_DIR = _RESEARCH / "scanner"

#: Spec 41: the per-file line budget.  Exceeding it means a responsibility
#: leaked in (Paper, AutoQuant, History, Market, Account, candidate selection);
#: raise the *code's* separation, not this number.
ORCHESTRATOR_LINE_BUDGET = 320
MODELS_LINE_BUDGET = 140

#: Spec 35: exactly which modules each file may import.  Compared for
#: **equality**, so an undeclared dependency fails rather than passing silently.
ALLOWED_IMPORTS = {
    "__init__.py": {
        "__future__",
        "us_quant.desktop_v2.orchestration.research.scanner.models",
        "us_quant.desktop_v2.orchestration.research.scanner.orchestrator",
    },
    "models.py": {
        "__future__",
        "dataclasses",
        "decimal",
        "us_quant.portfolio",
    },
    "orchestrator.py": {
        "__future__",
        "collections.abc",
        "PySide6.QtCore",
        "us_quant.desktop_market_scan_service",
        "us_quant.desktop_v2.orchestration.research.scanner.models",
        "us_quant.desktop_v2.orchestration.tasking",
        "us_quant.desktop_v2.pages.research.scanner.models",
        "us_quant.desktop_v2.pages.research.scanner.presenter",
        "us_quant.scanner",
        "us_quant.universe",
    },
}

#: Spec 30: symbols the scanner capability may never import, by name.  A
#: module-path guard cannot see ``from <allowed_module> import <forbidden>``,
#: so both directions are checked.
FORBIDDEN_SYMBOLS = (
    "MainWindow",
    "MarketOrchestrator",
    "AccountOrchestrator",
    "HistoryOrchestrator",
    "UniverseOrchestrator",
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
    "HistoryJobStore",
    "QWidget",
    "QMessageBox",
)

#: Spec 30: the same ban expressed as module paths.  ``Paper*`` / ``Shadow*`` /
#: ``Execution*`` are prefixes rather than exact names, and a symbol guard would
#: miss ``import us_quant.trading.application.paper_session`` entirely.
FORBIDDEN_MODULE_PREFIXES = (
    "us_quant.desktop",
    "us_quant.desktop_workers",
    "us_quant.desktop_tasks",
    "us_quant.runtime_supervisor",
    "us_quant.trading.runtime",
    "us_quant.trading.application.strategy",
    "us_quant.trading.application.risk",
    "us_quant.shadow",
    "us_quant.paper",
    "us_quant.execution",
    "us_quant.backtest",
    "us_quant.cross_section",
    "us_quant.history_queue",
)

#: The one ``us_quant.desktop*`` module the capability may reach: its own
#: stateless service.  Declared separately so the prefix ban above stays a ban
#: rather than a list of exceptions buried inside it.
ALLOWED_DESKTOP_MODULES = ("us_quant.desktop_market_scan_service",)

#: Spec 43: this file must never exist, and these classes must never be
#: declared.  One object owning Scanner *and* the workflows that consume a scan
#: is the failure mode this stage is designed around.
FORBIDDEN_AGGREGATES = (
    "ScannerManager",
    "ScannerContext",
    "ScannerServices",
    "ScannerController",
    "ResearchOrchestrator",
    "ResearchManager",
    "ResearchContext",
    "DesktopContext",
    "ServiceContainer",
)

#: Spec 31: the entire public surface of the capability.  Compared for
#: equality, so a convenience accessor fails here.
PUBLIC_SURFACE = (
    # Signals, which are the capability's published facts.
    "log_requested",
    "refused",
    "scan_changed",
    # The read-only fact.
    "scan",
    # The three arrival paths, the chart read and the one render entry point.
    "adopt_external_scan",
    "render_current",
    "request_chart",
    "request_scan",
    "restore_saved",
)

#: Spec 31: names that would turn the capability into a domain facade or leak
#: its internals.  These are not capability API.
FORBIDDEN_ACCESSORS = (
    "results",
    "skipped",
    "summary",
    "scanned_count",
    "trade_candidates",
    "research_count",
    "scan_path",
    "service",
    "page",
)

#: Spec 28: the methods the window must no longer declare.
RETIRED_WINDOW_METHODS = (
    "_run_scan",
    "_scan_finished",
    "_load_scan_file",
    "_publish_scanner_view",
    "_scanner_symbol_selected",
)

#: Spec 28: the scanner state the window must no longer hold.  The three
#: spellings cover an annotated declaration, a plain assignment and a read.
RETIRED_WINDOW_STATE = (
    "self.scan ",
    "self.scan:",
    "self.scan =",
)

#: Spec 28: the only scanner methods the window may still declare, and only as
#: composition.  ``_connect_scanner_page`` must be pure wiring; the other two
#: are the documented bridges.
ALLOWED_SCANNER_METHODS = (
    "_connect_scanner_page",
    "_scanner_run_inputs",
    "_report_scanner_refusal",
)


# -- helpers ------------------------------------------------------------


def _python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _parse(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _module_imports(path: pathlib.Path) -> set[str]:
    """Every dotted module path the file imports (absolute imports only)."""

    found: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            found.add(node.module or "")
    return found


def _imported_names(path: pathlib.Path) -> set[str]:
    """Every name the file binds from an import, as written."""

    found: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                found.add(alias.asname or alias.name)
    return found


def _main_window() -> ast.ClassDef:
    for node in ast.walk(_parse(_DESKTOP)):
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise AssertionError("MainWindow is gone")


def _method_source(name: str) -> str | None:
    """The source text of one ``MainWindow`` method, or ``None``."""

    for node in _main_window().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    return None


def _calls_attr(path: pathlib.Path, attribute: str) -> bool:
    """Does ``path`` call ``<anything>.<attribute>(...)``?"""

    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == attribute:
                return True
    return False


def _touches_window_scan(tree: ast.Module) -> bool:
    """Does ``tree`` read or write the attribute ``self.scan``?

    Exact on the attribute name, so ``self.scanner_orchestrator`` -- the
    capability handle the bridge is *supposed* to use -- does not match.
    """

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "scan"
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            return True
    return False


def _public_names(path: pathlib.Path) -> set[str]:
    """Class-level names a caller may use: signals, properties, methods."""

    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ClassDef):
            names: set[str] = set()
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
    raise AssertionError(f"no class in {path}")


# -- spec 43: no scanner god object ------------------------------------


def test_the_scanner_package_has_its_own_orchestrator() -> None:
    assert (_SCANNER_DIR / "orchestrator.py").exists()
    assert (_SCANNER_DIR / "models.py").exists()


def test_no_scanner_or_research_god_object_is_declared() -> None:
    offenders: list[tuple[str, str]] = []
    for path in _python_files(_RESEARCH):
        for node in ast.walk(_parse(path)):
            if (
                isinstance(node, ast.ClassDef)
                and node.name in FORBIDDEN_AGGREGATES
            ):
                offenders.append((str(path.relative_to(_SRC)), node.name))
    for node in _main_window().body:
        if (
            isinstance(node, ast.ClassDef)
            and node.name in FORBIDDEN_AGGREGATES
        ):
            offenders.append(("desktop.py", node.name))
    assert not offenders, offenders


# -- spec 28: the window's scanner surface is gone ---------------------


@pytest.mark.parametrize("name", RETIRED_WINDOW_METHODS)
def test_the_window_no_longer_declares_the_retired_method(name: str) -> None:
    assert _method_source(name) is None, f"{name} must be retired"


@pytest.mark.parametrize("needle", RETIRED_WINDOW_STATE)
def test_the_window_no_longer_holds_the_scan(needle: str) -> None:
    source = _DESKTOP.read_text(encoding="utf-8")
    assert needle not in source, f"{needle!r} must not appear in desktop.py"


def test_the_window_declares_no_scan_compatibility_property() -> None:
    """Spec 5/28: no forwarding property, in either spelling.

    ``@property def scan(...)`` and a plain class-level assignment would both
    keep every unmigrated consumer working, which is exactly the state the
    extraction must not leave behind.
    """

    for node in _main_window().body:
        if isinstance(node, ast.FunctionDef) and node.name in (
            "scan",
            "market_scan",
            "scan_result",
        ):
            raise AssertionError(f"compatibility property {node.name!r} exists")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            for target in targets:
                if isinstance(target, ast.Name) and target.id in (
                    "scan",
                    "market_scan",
                ):
                    raise AssertionError(
                        f"compatibility attribute {target.id!r} exists"
                    )


def test_the_window_keeps_only_the_declared_scanner_methods() -> None:
    """Spec 28: composition and two bridges, nothing else.

    Asserted as a set rather than by listing the retired names: a *new*
    scanner-looking handler added later would pass a removal-only check.
    """

    declared = {
        node.name
        for node in _main_window().body
        if isinstance(node, ast.FunctionDef) and "scanner" in node.name
    }
    assert declared == set(ALLOWED_SCANNER_METHODS), declared


def test_the_window_does_not_reach_into_the_capability() -> None:
    """Spec 39: no ``scanner_orchestrator._x`` from the window."""

    offenders: list[str] = []
    for node in ast.walk(_main_window()):
        if not isinstance(node, ast.Attribute) or not node.attr.startswith("_"):
            continue
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and owner.attr == "scanner_orchestrator"
        ):
            offenders.append(f"scanner_orchestrator.{node.attr}")
    assert not offenders, offenders


def test_the_window_never_paints_the_scanner_page() -> None:
    """Spec 16: ``render`` and ``render_chart`` have one caller -- the capability.

    Matched on the attribute name over the whole window class, so renaming the
    receiver cannot smuggle a second painter back in.
    """

    for attribute in ("render", "render_chart"):
        offenders: list[str] = []
        for node in ast.walk(_main_window()):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != attribute:
                continue
            target = func.value
            name = (
                target.id
                if isinstance(target, ast.Name)
                else target.attr
                if isinstance(target, ast.Attribute)
                else ""
            )
            if "scanner" in name or name == "_page":
                offenders.append(f"{name}.{attribute}")
        assert not offenders, (attribute, offenders)


def test_the_capability_is_the_only_caller_of_the_page_render() -> None:
    """The positive half of the rule: exactly one caller, and it is Scanner."""

    callers = [
        path
        for path in _python_files(_SRC)
        if _calls_attr(path, "render_chart")
    ]
    assert callers == [_SCANNER_DIR / "orchestrator.py"], [
        str(path.relative_to(_SRC)) for path in callers
    ]


def test_the_window_constructs_the_scanner_page_exactly_once() -> None:
    """A second page would be a page the capability cannot see."""

    offenders: list[str] = []
    for path in _python_files(_SRC):
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if name == "ScannerPage":
                offenders.append(str(path.relative_to(_SRC)))
                break
    assert offenders == ["desktop.py"], offenders


def test_the_connect_method_is_wiring_only() -> None:
    """Spec 29: ``_connect_scanner_page`` connects, and does nothing else.

    The temptation is to leave the old handler bodies reachable from the
    connect method; this asserts the method contains no call at all beyond the
    ``connect`` registrations.
    """

    source = _method_source("_connect_scanner_page")
    assert source is not None

    called = {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }
    assert called == {"connect"}, called


def test_the_autoquant_preparation_path_still_scans_directly() -> None:
    """Spec 19/40: the duplicate is deliberate and stays direct.

    AutoQuant's preparation runs the scanner itself because it is wired into
    Paper ``PREPARING`` and failure cleanup.  If this ever goes through the
    scanner capability, the AutoQuant/Paper chain was changed by a refactor that
    promised not to touch it.
    """

    source = _method_source("_prepare_auto_quant_candidates")
    assert source is not None
    assert "scan_market" in source
    assert "save_market_scan" in source
    assert "scanner_orchestrator" not in source


def test_the_autoquant_completion_only_publishes_into_the_capability() -> None:
    """Spec 20/21: the bridge, and only the bridge.

    ``_auto_market_scan_finished`` may call ``adopt_external_scan``; it may not
    assign the scan itself, and it may not ask the capability to render.

    The window-state check is an AST attribute lookup, not a substring test:
    ``"self.scan" in source`` would also match ``self.scanner_orchestrator``,
    which is the very call this test requires.
    """

    source = _method_source("_auto_market_scan_finished")
    assert source is not None
    assert "adopt_external_scan" in source
    assert not _touches_window_scan(ast.parse(source))
    assert "_publish_scanner_view" not in source
    # The cross-workflow work it owns is untouched.
    for kept in (
        "HistoryJobStore",
        "prioritized_research_symbols",
        "_select_auto_quant_candidates",
    ):
        assert kept in source, kept


def test_the_scanner_keeps_its_imports_for_the_autoquant_path() -> None:
    """Spec 19: AutoQuant still needs both names on the window."""

    imported = {
        alias.asname or alias.name
        for node in ast.walk(_parse(_DESKTOP))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "scan_market" in imported
    assert "save_market_scan" in imported


# -- spec 35/36: the dependency rule -----------------------------------


def test_each_scanner_file_imports_only_its_declared_modules() -> None:
    offenders: list[tuple[str, list[str]]] = []
    for relative, allowed in ALLOWED_IMPORTS.items():
        path = _SCANNER_DIR / relative
        assert path.exists(), f"{relative} is missing"
        undeclared = sorted(_module_imports(path) - allowed)
        if undeclared:
            offenders.append((relative, undeclared))
    assert not offenders, offenders


def test_every_declared_import_is_actually_used() -> None:
    """The other direction: a stale declaration would silently widen the gate."""

    offenders: list[tuple[str, list[str]]] = []
    for relative, allowed in ALLOWED_IMPORTS.items():
        actual = _module_imports(_SCANNER_DIR / relative)
        unused = sorted(allowed - actual)
        if unused:
            offenders.append((relative, unused))
    assert not offenders, offenders


@pytest.mark.parametrize("name", FORBIDDEN_SYMBOLS)
def test_the_scanner_capability_imports_no_forbidden_symbol(
    name: str,
) -> None:
    offenders: list[str] = []
    for path in _python_files(_SCANNER_DIR):
        if name in _imported_names(path):
            offenders.append(str(path.relative_to(_SRC)))
    assert offenders == [], f"{name} imported by {offenders}"


def test_the_scanner_capability_imports_no_forbidden_module() -> None:
    """Spec 35 as module paths, which is the half a symbol guard cannot see.

    ``import us_quant.trading.application.paper_session`` binds a module, not a
    class, so it would slip past ``FORBIDDEN_SYMBOLS`` entirely.  The scanner
    service is the only ``us_quant.desktop*`` module allowed, and it is allowed
    because it is stateless: reaching ``us_quant.desktop`` itself would drag in
    ``MainWindow``.
    """

    offenders: list[tuple[str, str]] = []
    for path in _python_files(_SCANNER_DIR):
        for module in _module_imports(path):
            if module in ALLOWED_DESKTOP_MODULES:
                continue
            for prefix in FORBIDDEN_MODULE_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    offenders.append((str(path.relative_to(_SRC)), module))
                    break
    assert not offenders, offenders


def test_the_scanner_capability_does_not_import_another_orchestrator() -> None:
    """Spec 30: capabilities reach each other through callables, never objects.

    ``us_quant.desktop_v2.orchestration.tasking`` is the one exception, and it is
    not a capability: it is a types-only module holding the ``TaskSubmitter``
    protocol every capability's constructor is annotated with.  Importing it
    couples Scanner to a signature, not to another workspace's runtime.
    """

    offenders: list[tuple[str, str]] = []
    for path in _python_files(_SCANNER_DIR):
        for module in _module_imports(path):
            if module == "us_quant.desktop_v2.orchestration.tasking":
                continue
            if module.startswith(
                "us_quant.desktop_v2.orchestration"
            ) and "research.scanner" not in module:
                offenders.append((str(path.relative_to(_SRC)), module))
    assert not offenders, offenders


def test_the_scanner_capability_never_names_a_worker() -> None:
    """Spec 30: ``TaskThread`` is the window's, and the capability must not know.

    The whole point of ``submit_task`` is that the capability never holds the
    worker.  Checked by *shape* as well as by name, because ``self._worker``
    would be renamed long before it was deleted.
    """

    offenders: list[str] = []
    for path in _python_files(_SCANNER_DIR):
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Attribute) and node.attr in (
                "_worker",
                "_task",
                "_thread",
                "worker",
                "task",
            ):
                owner = node.value
                if isinstance(owner, ast.Name) and owner.id == "self":
                    offenders.append(f"{path.relative_to(_SRC)}:{node.attr}")
            if isinstance(node, ast.Name) and node.id in (
                "TaskThread",
                "DesktopTaskController",
            ):
                offenders.append(f"{path.relative_to(_SRC)}:{node.id}")
    assert not offenders, offenders


def test_the_scanner_models_are_qt_free() -> None:
    """Spec 5: the input value object is application code, not presentation."""

    for module in _module_imports(_SCANNER_DIR / "models.py"):
        assert not (
            module == "PySide6" or module.startswith("PySide6.")
        ), module


def test_the_scanner_capability_holds_no_dialog() -> None:
    """Spec 10: a refusal is a signal; the window owns every dialog."""

    for path in _python_files(_SCANNER_DIR):
        imported = _imported_names(path)
        for forbidden in ("QMessageBox", "QWidget"):
            assert forbidden not in imported, (path.name, forbidden)


# -- spec 31: the public surface stays small ---------------------------


def test_the_public_surface_is_exactly_the_declared_one() -> None:
    """Spec 31: a small API, compared for equality.

    Equality in both directions is the point.  A subset check would let a new
    accessor in; the reverse check would fail on a signal that was never
    declared.  ``service``/``page``/``_scan`` are private by construction, which
    is what keeps the capability from being a facade over what it composes.
    """

    assert _public_names(_SCANNER_DIR / "orchestrator.py") == set(
        PUBLIC_SURFACE
    )


@pytest.mark.parametrize("name", FORBIDDEN_ACCESSORS)
def test_no_internal_is_exposed_as_a_public_name(name: str) -> None:
    """Spec 31: these are not capability API."""

    names = _public_names(_SCANNER_DIR / "orchestrator.py")
    assert name not in names, name


# -- spec 41: line budgets ---------------------------------------------


def test_the_orchestrator_stays_inside_its_budget() -> None:
    path = _SCANNER_DIR / "orchestrator.py"
    count = len(path.read_text(encoding="utf-8").splitlines())
    assert count <= ORCHESTRATOR_LINE_BUDGET, (
        f"scanner/orchestrator.py is {count} lines; over budget means a "
        f"responsibility leaked in (Paper, AutoQuant, History, Market, Account, "
        f"candidate selection) -- split the code, do not raise the budget"
    )


def test_the_models_stay_inside_their_budget() -> None:
    path = _SCANNER_DIR / "models.py"
    count = len(path.read_text(encoding="utf-8").splitlines())
    assert count <= MODELS_LINE_BUDGET, (
        f"scanner/models.py is {count} lines"
    )


def test_the_window_actually_shrank() -> None:
    """The extraction must remove code, not add a second copy of it."""

    import subprocess

    base = subprocess.run(
        ["git", "show", "ba9b2902c752405aae66cfa3661d7da909dc02a9:src/us_quant/desktop.py"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    ).stdout
    if not base:
        pytest.skip("the base commit is unreachable")

    before = len(base.splitlines())
    after = len(_DESKTOP.read_text(encoding="utf-8").splitlines())
    assert after < before, f"desktop.py went {before} -> {after}"
