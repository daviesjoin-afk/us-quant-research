"""Architecture guards for the v2O-C3 backtest orchestration extraction.

Spec 27/28/34/35/36/39/40.  These are structural, not behavioural: they read the
source and assert what may and may not exist, so a future edit that quietly
reintroduces a second backtest owner fails here rather than in production.

The three that matter most for this stage:

* the window holds **no** ``self.backtest_runs``, ``self._selected_backtest_run_id``
  or ``self._backtest_busy``, and no compatibility property forwarding to the
  capability.  A forwarding property is the tempting way to keep a diff small,
  and it is a trap: it keeps every unmigrated consumer silently working, so "who
  owns the backtest runs?" stops being one grep;
* ``BacktestPage.render`` and ``BacktestPage.set_strategy_options`` have exactly
  one caller -- the capability.  A second caller is a second state that can
  paint the page;
* the capability imports no other orchestrator and no Paper/Shadow/Execution/
  Market/Account module, and it does not import ``StrategySelectionService``.
  It takes a provider callable instead, so a change to strategy application
  cannot ripple into the backtest workspace.

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
_ORCHESTRATION = _SRC / "desktop_v2" / "orchestration"
_BACKTEST_DIR = _ORCHESTRATION / "research" / "backtest"
_TASKING = _ORCHESTRATION / "tasking.py"


# -- mechanical AST queries --------------------------------------------
#
# Kept local on purpose.  A shared ``tests/desktop_architecture_support.py``
# would have exactly two consumers today (this file and the wiring test), which
# is not enough to justify the extra hop: the rule of three applies to test
# helpers too.  When a third real guard needs the same queries, extract them
# then -- not in advance.


def _python_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Every ``.py`` file under ``root``, sorted, without ``__pycache__``."""

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


def _method_source(path: pathlib.Path, name: str) -> str | None:
    """The source text of one ``MainWindow`` method, or ``None``."""

    for node in _main_window().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(
                path.read_text(encoding="utf-8"), node
            )
    return None


def _receiver_methods(path: pathlib.Path, receiver: str) -> set[str]:
    """Every ``<receiver>.<method>`` attribute reached anywhere in the file."""

    methods: set[str] = set()
    for node in ast.walk(_parse(path)):
        if not isinstance(node, ast.Attribute):
            continue
        if (
            isinstance(node.value, ast.Attribute)
            and node.value.attr == receiver
        ):
            methods.add(node.attr)
    return methods


def _strategy_options_receivers(path: pathlib.Path) -> set[str]:
    """Every receiver ``path`` calls ``set_strategy_options`` on."""

    receivers: set[str] = set()
    for node in ast.walk(_parse(path)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "set_strategy_options"
        ):
            receivers.add(ast.unparse(func.value))
    return receivers


def _strategy_options_callers() -> list[tuple[str, str]]:
    """Who fills a strategy combo: every ``<owner>.set_strategy_options`` call
    outside the page package.

    The pages delegate to their own controls
    (``self.controls.set_strategy_options``); that is the widget plumbing the
    method exists for, not a driver of it, so the page package is filtered out.
    What remains is exactly the set of modules that decide what a combo shows.
    """

    callers: list[tuple[str, str]] = []
    for path in _python_files(_SRC):
        if "pages" in path.parts:
            continue
        for receiver in _strategy_options_receivers(path):
            callers.append((path.relative_to(_SRC).as_posix(), receiver))
    return sorted(callers)


def _class_names(path: pathlib.Path) -> set[str]:
    """Every class name declared in one file."""

    return {
        node.name
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.ClassDef)
    }

def _public_names(path: pathlib.Path, class_name: str) -> set[str]:
    """Class-level names a caller may use: signals, properties, methods."""

    names: set[str] = set()
    for node in ast.walk(_parse(path)):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
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
        break
    return {name for name in names if not name.startswith("_")}


def _main_window() -> ast.ClassDef:
    for node in ast.parse(_DESKTOP.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise AssertionError("MainWindow is gone")

#: Spec 36: the per-file line budget.  Exceeding it means a responsibility
#: leaked in (Cross-Section, Targeted, the strategy catalogue, the task
#: lifecycle); raise the *code's* separation, not this number.
ORCHESTRATOR_LINE_BUDGET = 320
QUERIES_LINE_BUDGET = 220
TASKING_LINE_BUDGET = 100

#: Spec 35: exactly which modules each file may import.  Compared for
#: **equality**, so an undeclared dependency fails rather than passing silently.
ALLOWED_IMPORTS = {
    "__init__.py": {
        "__future__",
        "us_quant.desktop_v2.orchestration.research.backtest.orchestrator",
        "us_quant.desktop_v2.orchestration.research.backtest.queries",
    },
    "queries.py": {
        "__future__",
        "collections.abc",
        "decimal",
        "us_quant.backtest_workspace",
        "us_quant.desktop_v2.pages.research.backtest.models",
        "us_quant.trading.domain.strategy",
    },
    "orchestrator.py": {
        "__future__",
        "collections.abc",
        "PySide6.QtCore",
        "us_quant.backtest_workspace",
        "us_quant.desktop_backtest_service",
        "us_quant.desktop_v2.orchestration.research.backtest.queries",
        "us_quant.desktop_v2.orchestration.tasking",
        "us_quant.desktop_v2.pages.research.backtest.models",
        "us_quant.desktop_v2.pages.research.backtest.presenter",
        "us_quant.trading.domain.strategy",
    },
}

#: Spec 27: symbols the backtest capability may never import, by name.  A
#: module-path guard cannot see ``from <allowed_module> import <forbidden>``,
#: so both directions are checked.
FORBIDDEN_SYMBOLS = (
    "MainWindow",
    "MarketOrchestrator",
    "AccountOrchestrator",
    "UniverseOrchestrator",
    "HistoryOrchestrator",
    "ScannerOrchestrator",
    "PaperWorkflow",
    "PaperWorkflowPhase",
    "PaperTradingService",
    "TradingRuntime",
    "ExecutionApplication",
    "RiskApplication",
    "StrategyApplication",
    "StrategySelectionService",
    "StrategySelectionPurpose",
    "ShadowPaperEngine",
    "ShadowWorkflowController",
    "RuntimeSupervisor",
    "TaskThread",
    "DesktopTaskController",
    "QWidget",
    "QMessageBox",
)

#: Spec 27: the same ban expressed as module paths.  ``Paper*`` / ``Shadow*`` /
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
    "us_quant.cross_section",
    "us_quant.history_queue",
    "us_quant.universe",
    "us_quant.scanner",
    "us_quant.targeted",
)

#: The one ``us_quant.desktop*`` module the capability may reach: its own
#: stateless service.  Declared separately so the prefix ban above stays a ban
#: rather than a list of exceptions buried inside it.
ALLOWED_DESKTOP_MODULES = ("us_quant.desktop_backtest_service",)

#: Spec 39: this file must never exist, and these classes must never be
#: declared.  One object owning Backtest *and* the strategy catalogue or the
#: task lifecycle is the failure mode this stage is designed around.
FORBIDDEN_AGGREGATES = (
    "BacktestManager",
    "BacktestContext",
    "BacktestServices",
    "BacktestController",
    "BaseOrchestrator",
    "ResearchOrchestrator",
    "WorkspaceOrchestrator",
    "ResearchManager",
    "ResearchContext",
    "DesktopContext",
    "ServiceContainer",
    "NotificationService",
    "DialogManager",
    "DesktopNoticeBus",
    "EventBus",
)

#: Spec 35: the entire public surface of the capability.  Compared for
#: equality, so a convenience accessor fails here.
PUBLIC_SURFACE = (
    # Signals, which are the capability's published facts.
    "log_requested",
    "refused",
    # The four operation entry points and the one render entry point.
    "refresh_strategy_options",
    "render_current",
    "request_compare_all",
    "request_selected",
    "select_run",
)

#: Spec 35: names that would turn the capability into a domain facade or leak
#: its internals.  ``runs`` is first because "expose the runs just in case" is
#: the change this stage explicitly forbids: no workflow reads them today, and
#: an accessor added for a hypothetical consumer is one the next capability
#: will start using.
FORBIDDEN_ACCESSORS = (
    "runs",
    "selected_run",
    "selected_run_id",
    "busy",
    "selected_strategy",
    "service",
    "page",
    "workers",
    "task_controller",
    "config",
)

#: Spec 25/28: the methods the window must no longer declare.
RETIRED_WINDOW_METHODS = (
    "_publish_backtest_strategy_options",
    "_publish_backtest_view",
    "_backtest_run_selected",
    "_backtest_records",
    "_run_selected_backtest",
    "_run_all_backtests",
    "_run_backtest_workspace",
    "_backtest_task_failed",
    "_backtest_workspace_finished",
)

#: Spec 25: the backtest state the window must no longer hold.  The three
#: spellings cover an annotated declaration, a plain assignment and a read.
RETIRED_WINDOW_STATE = (
    "self.backtest_runs ",
    "self.backtest_runs:",
    "self.backtest_runs =",
    "self._selected_backtest_run_id ",
    "self._selected_backtest_run_id:",
    "self._selected_backtest_run_id =",
    "self._backtest_busy ",
    "self._backtest_busy:",
    "self._backtest_busy =",
)

#: Spec 26: the only backtest methods the window may still declare, and only as
#: composition.  ``_connect_backtest_page`` must be pure wiring; the other is
#: the documented dialog bridge.
ALLOWED_BACKTEST_METHODS = (
    "_connect_backtest_page",
    "_report_backtest_refusal",
)


# -- spec 39: no backtest god object -----------------------------------


def test_the_backtest_package_has_its_own_orchestrator() -> None:
    assert (_BACKTEST_DIR / "orchestrator.py").exists()
    assert (_BACKTEST_DIR / "queries.py").exists()


def test_no_backtest_or_research_god_object_is_declared() -> None:
    offenders: list[tuple[str, str]] = []
    for path in _python_files(_ORCHESTRATION):
        for node in _class_names(path):
            if node in FORBIDDEN_AGGREGATES:
                offenders.append(
                    (str(path.relative_to(_SRC)), node)
                )
    assert not offenders, offenders


def test_the_shared_tasking_module_is_types_only() -> None:
    """Spec 5: ``tasking.py`` declares a protocol and two aliases, nothing else.

    The temptation is to put a small worker registry in here "while we are at
    it".  That would move the generic task lifecycle off the window and into a
    module two capabilities import -- the opposite of what the module is for.
    """

    import ast

    tree = ast.parse(_TASKING.read_text(encoding="utf-8"))
    declared = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    assert declared == {"TaskSubmitter"}, declared

    classes = [
        node for node in tree.body if isinstance(node, ast.ClassDef)
    ]
    assert len(classes) == 1
    assert {base.id for base in classes[0].bases if isinstance(base, ast.Name)} == {
        "Protocol"
    }
    # Only methods may be declared on the protocol, and only ``__call__``.
    assert [
        node.name
        for node in classes[0].body
        if isinstance(node, ast.FunctionDef)
    ] == ["__call__"]


def test_the_task_submitter_protocol_matches_the_window_signature() -> None:
    """The protocol must describe ``MainWindow._start_task`` exactly.

    ``TaskSubmitter`` is not a wrapper and no adapter is constructed -- the
    window's method satisfies it structurally.  That only holds while the two
    signatures agree, so the parameter names, their defaults and the return type
    are compared here.  A new keyword added to ``_start_task`` without being
    declared on the protocol (or a protocol parameter the window does not
    accept) fails.

    Compared by parsing both sources rather than by importing either object:
    this is a source-level contract, and importing ``desktop`` would drag in Qt
    for an assertion about a signature.
    """

    def signature(path: pathlib.Path, name: str) -> dict[str, object]:
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                args = node.args
                names = [a.arg for a in args.posonlyargs + args.args]
                names += [a.arg for a in args.kwonlyargs]
                defaults = [
                    ast.unparse(default) if default is not None else None
                    for default in args.kw_defaults
                ]
                return {
                    "names": names,
                    "defaults": defaults,
                    "returns": (
                        ast.unparse(node.returns) if node.returns else None
                    ),
                }
        raise AssertionError(f"{name} not found in {path}")

    protocol = signature(_TASKING, "__call__")
    window = signature(_DESKTOP, "_start_task")

    # Both declare ``self`` explicitly -- the protocol because ``__call__`` is
    # written as a method, the window because it is one -- so the two lists are
    # compared whole rather than with an offset.
    assert protocol["names"] == window["names"], (
        protocol["names"],
        window["names"],
    )
    assert protocol["defaults"] == window["defaults"]
    assert protocol["returns"] == window["returns"] == "bool"


def test_the_tasking_module_has_no_runtime_behaviour() -> None:
    """Spec 5: no worker, no thread, no resource-group state.

    Checked against the parsed module rather than the text, because the
    module's own docstring *names* the things it refuses to contain -- a
    substring test would fail on the explanation instead of on a violation.
    """

    import ast

    offenders: list[str] = []
    for node in ast.walk(ast.parse(_TASKING.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Name) and node.id in (
            "QObject",
            "TaskThread",
            "DesktopTaskController",
            "MainWindow",
        ):
            offenders.append(node.id)
        if isinstance(node, ast.Import) and any(
            alias.name in ("threading", "PySide6")
            for alias in node.names
        ):
            offenders.append("runtime import")
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            ("PySide6", "threading")
        ):
            offenders.append(node.module or "")
    assert not offenders, offenders

    assert _module_imports(_TASKING) == {"__future__", "collections.abc", "typing"}


# -- spec 25/28: the window's backtest surface is gone -----------------


@pytest.mark.parametrize("name", RETIRED_WINDOW_METHODS)
def test_the_window_no_longer_declares_the_retired_method(name: str) -> None:
    assert _method_source(_DESKTOP, name) is None, f"{name} must be retired"


@pytest.mark.parametrize("needle", RETIRED_WINDOW_STATE)
def test_the_window_no_longer_holds_the_backtest_state(needle: str) -> None:
    source = _DESKTOP.read_text(encoding="utf-8")
    assert needle not in source, f"{needle!r} must not appear in desktop.py"


def test_the_window_declares_no_backtest_compatibility_property() -> None:
    """Spec 25/28: no forwarding property, in either spelling.

    ``@property def backtest_runs(...)`` and a plain class-level assignment
    would both keep every unmigrated consumer working, which is exactly the
    state the extraction must not leave behind.
    """

    import ast

    window = _main_window()
    for node in window.body:
        if isinstance(node, ast.FunctionDef) and node.name in (
            "backtest_runs",
            "selected_backtest_run_id",
            "backtest_busy",
        ):
            raise AssertionError(f"compatibility property {node.name!r} exists")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            for target in targets:
                if isinstance(target, ast.Name) and target.id in (
                    "backtest_runs",
                    "backtest_busy",
                ):
                    raise AssertionError(
                        f"compatibility attribute {target.id!r} exists"
                    )


def test_the_window_keeps_only_the_declared_backtest_methods() -> None:
    """Spec 26: composition and the dialog bridge, nothing else.

    Asserted as a set rather than by listing the retired names: a *new*
    backtest-looking handler added later would pass a removal-only check.
    """

    import ast

    declared = {
        node.name
        for node in _main_window().body
        if isinstance(node, ast.FunctionDef) and "backtest" in node.name
    }
    assert declared == set(ALLOWED_BACKTEST_METHODS), declared


def test_the_window_does_not_reach_into_the_capability() -> None:
    """Spec 35: no ``backtest_orchestrator._x`` from the window."""

    import ast

    offenders: list[str] = []
    for node in ast.walk(_main_window()):
        if not isinstance(node, ast.Attribute) or not node.attr.startswith("_"):
            continue
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and owner.attr == "backtest_orchestrator"
        ):
            offenders.append(f"backtest_orchestrator.{node.attr}")
    assert not offenders, offenders


# -- spec 34: the page has one caller ----------------------------------


def test_the_window_never_paints_the_backtest_page() -> None:
    """Spec 15/34: ``render`` has one caller -- the capability.

    Matched on the attribute name over the whole window class, so renaming the
    receiver cannot smuggle a second painter back in.
    """

    import ast

    offenders: list[str] = []
    for node in ast.walk(_main_window()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "render":
            continue
        target = func.value
        name = (
            target.id
            if isinstance(target, ast.Name)
            else target.attr
            if isinstance(target, ast.Attribute)
            else ""
        )
        if "backtest" in name:
            offenders.append(f"{name}.render")
    assert not offenders, offenders


def test_the_window_never_sets_the_backtest_strategy_options() -> None:
    """Spec 14/34: the *backtest* page's options are the capability's to set.

    G2-B made this guard stronger, and the scoping caveat it used to carry is gone.
    When the *execution* page's combo moved to ``ExecutionOrchestrator`` the window
    stopped filling any combo at all, so "the window fills the execution page's
    options, and the targeted page's" is no longer a reason to scope the rule: the
    window calls ``set_strategy_options`` on nothing, and each of the three combo
    pages has exactly one driver -- its own capability.  Asserting only the absence
    would still pass if the window had simply stopped refreshing every combo and
    left the pages empty, so the positive half is asserted here too.

    ``set_palette`` is deliberately allowed: the theme is the window's, and
    repainting the page on a theme change is composition rather than backtest
    state.
    """

    assert _strategy_options_receivers(_DESKTOP) == set(), sorted(
        _strategy_options_receivers(_DESKTOP)
    )

    reached = _receiver_methods(_DESKTOP, "backtest_page")
    assert "set_strategy_options" not in reached, reached
    assert "render" not in reached, reached
    assert "set_palette" in reached

    assert _strategy_options_callers() == [
        (
            "desktop_v2/orchestration/execution/orchestrator.py",
            "self._page",
        ),
        (
            "desktop_v2/orchestration/research/backtest/orchestrator.py",
            "self._page",
        ),
        (
            "desktop_v2/orchestration/research/targeted/session/orchestrator.py",
            "self._page",
        ),
    ], _strategy_options_callers()


def test_only_the_window_obtains_a_backtest_page() -> None:
    """Spec 34: one page, built once, handed to one driver.

    The capability deliberately does **not** import ``BacktestPage``: it takes
    the page as an opaque handle, so it cannot construct a second one and does
    not depend on the page module at all.  That makes the precise question
    "which modules outside the page package can obtain a ``BacktestPage``?", and
    the answer must be exactly one -- the window's composition.

    The capability's half of the rule (that it is the one that *drives* the
    page) is the sibling test below, so this guard cannot pass vacuously by the
    page simply never being driven.
    """

    page_package = _SRC / "desktop_v2" / "pages" / "research" / "backtest"
    holders = [
        str(path.relative_to(_SRC))
        for path in _python_files(_SRC)
        if page_package not in path.parents
        and (
            "BacktestPage" in _imported_names(path)
            or "BacktestPage(" in path.read_text(encoding="utf-8")
        )
    ]
    assert holders == ["desktop.py"], holders


def test_the_capability_does_drive_the_page() -> None:
    """The positive half: the page the window builds is the one Backtest paints."""

    reached = _receiver_methods(_BACKTEST_DIR / "orchestrator.py", "_page")
    assert {"render", "set_strategy_options"} <= reached, reached


def test_the_window_uses_only_the_declared_backtest_page_surface() -> None:
    """Spec 34: the page surface the window may touch, compared exactly."""

    reached = _receiver_methods(_DESKTOP, "backtest_page")
    assert reached <= {
        "render",
        "set_strategy_options",
        "set_palette",
        "current_draft",
        "run_selected_requested",
        "compare_all_requested",
        "run_selected",
    }, reached


def test_the_window_constructs_the_backtest_page_exactly_once() -> None:
    """A second page would be a page the capability cannot see."""

    import ast

    offenders: list[str] = []
    for path in _python_files(_SRC):
        for node in ast.walk(
            ast.parse(path.read_text(encoding="utf-8"))
        ):
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
            if name == "BacktestPage":
                offenders.append(str(path.relative_to(_SRC)))
                break
    assert offenders == ["desktop.py"], offenders


def test_the_connect_method_is_wiring_only() -> None:
    """Spec 13/26: ``_connect_backtest_page`` connects, and does nothing else."""

    import ast

    source = _method_source(_DESKTOP, "_connect_backtest_page")
    assert source is not None

    called = {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }
    assert called == {"connect"}, called


def test_the_refusal_bridge_holds_no_business_logic() -> None:
    """Spec 18: the window's handler shows a dialog and decides nothing.

    It may branch on the severity the capability sent -- that is dispatch, not
    policy -- but it must not read the draft, the runs or the service.
    """

    source = _method_source(_DESKTOP, "_report_backtest_refusal")
    assert source is not None
    for forbidden in (
        "draft",
        "_runs",
        "backtest_service",
        "start_date",
        "end_date",
    ):
        assert forbidden not in source, forbidden


# -- spec 35/36: the dependency rule -----------------------------------


def test_each_backtest_file_imports_only_its_declared_modules() -> None:
    offenders: list[tuple[str, list[str]]] = []
    for relative, allowed in ALLOWED_IMPORTS.items():
        path = _BACKTEST_DIR / relative
        assert path.exists(), f"{relative} is missing"
        undeclared = sorted(_module_imports(path) - allowed)
        if undeclared:
            offenders.append((relative, undeclared))
    assert not offenders, offenders


def test_every_declared_import_is_actually_used() -> None:
    """The other direction: a stale declaration would silently widen the gate."""

    offenders: list[tuple[str, list[str]]] = []
    for relative, allowed in ALLOWED_IMPORTS.items():
        actual = _module_imports(_BACKTEST_DIR / relative)
        unused = sorted(allowed - actual)
        if unused:
            offenders.append((relative, unused))
    assert not offenders, offenders


@pytest.mark.parametrize("name", FORBIDDEN_SYMBOLS)
def test_the_backtest_capability_imports_no_forbidden_symbol(
    name: str,
) -> None:
    offenders: list[str] = []
    for path in _python_files(_BACKTEST_DIR):
        if name in _imported_names(path):
            offenders.append(str(path.relative_to(_SRC)))
    assert offenders == [], f"{name} imported by {offenders}"


def test_the_backtest_capability_imports_no_forbidden_module() -> None:
    """Spec 27 as module paths, which is the half a symbol guard cannot see.

    ``import us_quant.trading.application.paper_session`` binds a module, not a
    class, so it would slip past ``FORBIDDEN_SYMBOLS`` entirely.  The backtest
    service is the only ``us_quant.desktop*`` module allowed, and it is allowed
    because it is stateless: reaching ``us_quant.desktop`` itself would drag in
    ``MainWindow``.
    """

    offenders: list[tuple[str, str]] = []
    for path in _python_files(_BACKTEST_DIR):
        for module in _module_imports(path):
            if module in ALLOWED_DESKTOP_MODULES:
                continue
            for prefix in FORBIDDEN_MODULE_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    offenders.append((str(path.relative_to(_SRC)), module))
                    break
    assert not offenders, offenders


def test_the_backtest_capability_does_not_import_another_orchestrator() -> None:
    """Spec 28: Backtest is the most self-contained Research capability.

    ``us_quant.desktop_v2.orchestration.tasking`` is the one exception, and it is
    not a capability: it is a types-only module holding the ``TaskSubmitter``
    protocol this capability's constructor is annotated with.
    """

    offenders: list[tuple[str, str]] = []
    for path in _python_files(_BACKTEST_DIR):
        for module in _module_imports(path):
            if module == "us_quant.desktop_v2.orchestration.tasking":
                continue
            if module.startswith(
                "us_quant.desktop_v2.orchestration"
            ) and "research.backtest" not in module:
                offenders.append((str(path.relative_to(_SRC)), module))
    assert not offenders, offenders


def test_the_backtest_capability_never_names_a_worker() -> None:
    """Spec 16/27: ``TaskThread`` is the window's, and the capability must not know.

    The whole point of ``submit_task`` is that the capability never holds the
    worker.  Checked by *shape* as well as by name, because ``self._worker``
    would be renamed long before it was deleted.
    """

    import ast

    offenders: list[str] = []
    for path in _python_files(_BACKTEST_DIR):
        for node in ast.walk(
            ast.parse(path.read_text(encoding="utf-8"))
        ):
            if isinstance(node, ast.Attribute) and node.attr in (
                "_worker",
                "_workers",
                "_thread",
                "worker",
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


def test_the_backtest_queries_are_qt_free() -> None:
    """Spec 12: the pure rules must be testable without starting Qt."""

    for module in _module_imports(_BACKTEST_DIR / "queries.py"):
        assert not (
            module == "PySide6" or module.startswith("PySide6.")
        ), module


def test_the_backtest_capability_holds_no_dialog() -> None:
    """Spec 18: a refusal is a signal; the window owns every dialog."""

    for path in _python_files(_BACKTEST_DIR):
        imported = _imported_names(path)
        for forbidden in ("QMessageBox", "QWidget"):
            assert forbidden not in imported, (path.name, forbidden)


def test_the_capability_does_not_import_the_strategy_service() -> None:
    """Spec 10: the catalogue arrives as a provider, not as a service handle.

    This is the coupling that would make a strategy-application refactor reach
    the backtest workspace.  The provider is a stable fact-shaped boundary.

    Asserted on the *imports*, not on the text: the module's docstring explains
    why it refuses to depend on the service, and naming the thing it avoids is
    not the same as importing it.
    """

    source = _module_imports(_BACKTEST_DIR / "orchestrator.py")
    assert "us_quant.trading.application.strategy_selection" not in source
    assert "us_quant.trading.application.strategies" not in source
    assert "StrategySelectionService" not in _imported_names(
        _BACKTEST_DIR / "orchestrator.py"
    )
    assert "StrategyApplication" not in _imported_names(
        _BACKTEST_DIR / "orchestrator.py"
    )
    assert (
        "strategy_versions_provider"
        in (_BACKTEST_DIR / "orchestrator.py").read_text(encoding="utf-8")
    )


# -- spec 35: the public surface stays small ---------------------------


def test_the_public_surface_is_exactly_the_declared_one() -> None:
    """Spec 35: a small API, compared for equality.

    Equality in both directions is the point.  A subset check would let a new
    accessor in; the reverse check would fail on a signal that was never
    declared.
    """

    assert _public_names(
        _BACKTEST_DIR / "orchestrator.py", "BacktestOrchestrator"
    ) == set(PUBLIC_SURFACE)


@pytest.mark.parametrize("name", FORBIDDEN_ACCESSORS)
def test_no_internal_is_exposed_as_a_public_name(name: str) -> None:
    """Spec 9/35: these are not capability API."""

    names = _public_names(
        _BACKTEST_DIR / "orchestrator.py", "BacktestOrchestrator"
    )
    assert name not in names, name


# -- spec 36: line budgets ---------------------------------------------


def test_the_orchestrator_stays_inside_its_budget() -> None:
    count = len(
        (_BACKTEST_DIR / "orchestrator.py")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert count <= ORCHESTRATOR_LINE_BUDGET, (
        f"backtest/orchestrator.py is {count} lines; over budget means a "
        f"responsibility leaked in -- split the code, do not raise the budget"
    )


def test_the_queries_stay_inside_their_budget() -> None:
    count = len(
        (_BACKTEST_DIR / "queries.py").read_text(encoding="utf-8").splitlines()
    )
    assert count <= QUERIES_LINE_BUDGET, f"queries.py is {count} lines"


def test_the_tasking_module_stays_inside_its_budget() -> None:
    count = len(_TASKING.read_text(encoding="utf-8").splitlines())
    assert count <= TASKING_LINE_BUDGET, f"tasking.py is {count} lines"


def test_the_window_actually_shrank() -> None:
    """The extraction must remove code, not add a second copy of it."""

    import subprocess

    base = subprocess.run(
        [
            "git",
            "show",
            "81813443887028ed54db2a6d021dc02c6d3dd425:src/us_quant/desktop.py",
        ],
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


def test_the_window_no_longer_imports_the_backtest_builders() -> None:
    """Spec 25: the window neither builds requests nor projects options."""

    source = _DESKTOP.read_text(encoding="utf-8")
    assert "build_backtest_view" not in source
    assert "BacktestStrategyOption" not in source
    assert "BacktestFormDraft" not in source

