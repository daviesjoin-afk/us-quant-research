"""Architecture guards for the Research foundations extraction (v2O-C1).

Spec 42/43/44/35/36.  These are structural, not behavioural: they read the
source and assert what may and may not exist, so a future edit that quietly
reintroduces a second owner fails here rather than in production.

Two of them are the long-term maintenance guards this stage exists for:

* there is **no** ``ResearchOrchestrator``.  Research is a route aggregate for
  navigation, and one object owning every workspace would be a second
  ``MainWindow`` -- the decomposition would be undone in a single file;
* the window holds **no** universe or history runtime state, and no
  compatibility property forwarding to the orchestrators.  A forwarding
  property is the tempting way to keep a diff small, and it is a trap: it keeps
  every unmigrated caller silently working, so "who reads universe truth" stops
  being one grep and the next extraction cannot find the remaining consumers.

The dependency guard is an **allowlist**, not a list of bans.  A ban list is
written in capability vocabulary and is structurally blind to the symbol that
actually needs forbidding living somewhere the vocabulary does not cover -- so a
new dependency has to be declared on purpose here, in a place a reviewer sees.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "us_quant"
_DESKTOP = _SRC / "desktop.py"
_RESEARCH = _SRC / "desktop_v2" / "orchestration" / "research"
_UNIVERSE_DIR = _RESEARCH / "universe"
_HISTORY_DIR = _RESEARCH / "history"

#: Spec 43: this file must never exist, and these classes must never be
#: declared.  One object owning all of Universe/History/Scanner/Backtest/
#: Cross-Section/Targeted is the failure mode this stage is designed around.
FORBIDDEN_AGGREGATES = (
    "ResearchOrchestrator",
    "ResearchManager",
    "ResearchContext",
    "ResearchServices",
    "ResearchController",
    "DesktopContext",
)

#: Spec 44: the per-capability line budget.  Exceeding it means a
#: responsibility leaked in (Scanner, Targeted, Market, System, or the generic
#: task lifecycle); raise the *code's* separation, not this number.
LINE_BUDGET = 320

#: Spec 35: exactly which modules each file may import.  Compared for
#: **equality**, so an undeclared dependency fails rather than passing silently.
ALLOWED_IMPORTS = {
    "__init__.py": {"__future__"},
    "universe/__init__.py": {
        "__future__",
        "us_quant.desktop_v2.orchestration.research.universe.orchestrator",
    },
    "universe/orchestrator.py": {
        "__future__",
        "collections.abc",
        "threading",
        "PySide6.QtCore",
        "us_quant.desktop_universe_service",
        "us_quant.desktop_v2.pages.research.universe.presenter",
        "us_quant.universe",
    },
    "history/__init__.py": {
        "__future__",
        "us_quant.desktop_v2.orchestration.research.history.orchestrator",
    },
    "history/orchestrator.py": {
        "__future__",
        "collections.abc",
        "PySide6.QtCore",
        "us_quant.desktop_history_service",
        "us_quant.desktop_v2.pages.research.history.presenter",
        "us_quant.ibkr",
        "us_quant.universe",
    },
}

#: Spec 35: symbols no Research capability may import, by name.  A module-path
#: guard cannot see ``from <allowed_module> import <forbidden_symbol>``, so both
#: directions are checked.
FORBIDDEN_SYMBOLS = (
    "MainWindow",
    "MarketOrchestrator",
    "AccountOrchestrator",
    "TaskThread",
    "DesktopTaskController",
    "RuntimeSupervisor",
    "ShadowPaperEngine",
    "ShadowWorkflowController",
    "PaperWorkflowPhase",
    "TradingRuntime",
    "StrategyApplication",
    "RiskApplication",
    "QWidget",
    "QMessageBox",
)

#: Spec 35: the same ban expressed as module paths.  ``Paper*`` / ``Shadow*`` /
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
    "us_quant.scanner",
    "us_quant.backtest",
    "us_quant.cross_section",
)

#: The one ``us_quant.desktop*`` module each capability is allowed to reach:
#: its own stateless service.  Declared separately so the prefix ban above stays
#: a ban rather than a list of exceptions buried inside it.
ALLOWED_DESKTOP_MODULES = (
    "us_quant.desktop_universe_service",
    "us_quant.desktop_history_service",
)

#: Spec 36: the two capabilities must not know each other.  History reaches the
#: universe through a ``Callable`` provider, so a change to how the universe is
#: implemented cannot ripple into History.
MUTUAL_FORBIDDEN = {
    "universe": ("HistoryOrchestrator",),
    "history": ("UniverseOrchestrator",),
}

#: The runtime state the window must no longer hold (spec 27/42).
RETIRED_WINDOW_STATE = (
    "self.universe ",
    "self.universe:",
    "self.universe =",
    "universe_refresh_cancel_event",
    "universe_refresh_worker",
    "_history_progress_percent",
)

#: The methods the window must no longer declare (spec 28).
RETIRED_WINDOW_METHODS = (
    "_publish_universe_view",
    "_refresh_universe",
    "_cancel_universe_refresh",
    "_reset_universe_refresh_controls",
    "_universe_refreshed",
    "_publish_history_view",
    "_schedule_history",
    "_run_history",
    "_history_finished",
    "_history_task_failed",
    "_run_public_history",
    "_retry_failed",
)


#: Spec 16: the entire public surface of each capability.  Compared for
#: equality, so a convenience accessor (``research_symbols()``,
#: ``eligible_symbols()``, ``summary()`` ...) fails here.  Spec 30 forbids
#: turning the orchestrator into a domain facade: other workflows read the
#: immutable ``UniverseSnapshot`` itself, not a method that wraps it.
PUBLIC_SURFACE = {
    "universe": (
        # Signals, which are the capability's published facts.
        "log_requested",
        "snapshot_changed",
        # The read-only fact.
        "snapshot",
        # Lifecycle and intents.
        "restore_snapshot",
        "request_refresh",
        "request_cancel",
        "cancel_for_shutdown",
        "render_current",
    ),
    "history": (
        "history_changed",
        "log_requested",
        "refused",
        "render_current",
        "request_run_ibkr",
        "request_run_public",
        "request_schedule",
        "retry_failed",
    ),
}

#: Spec 30: names that would make the orchestrator a domain facade.
FORBIDDEN_ACCESSORS = (
    "research_symbols",
    "research_count",
    "trading_symbols",
    "records",
    "eligible_symbols",
    "summary",
)


#: Spec 27/32/33/34: runtime state this round must *not* migrate.  These belong
#: to later slices, and asserting they are still on the window is what stops a
#: half-finished migration from looking complete: a reader who sees "Scanner
#: migrated" in one guard and "Scanner state still in MainWindow" in another has
#: found a real inconsistency rather than a stale comment.
UNTOUCHED_WINDOW_STATE = (
    "self.scan = ",
    "self.backtest_runs",
    "self._selected_backtest_run_id",
    "self._backtest_busy",
    "self.cross_section_report",
    "self._research_capital_value",
    "self.shadow_engine",
)


#: Spec 18: history queue truth must not be copied into the capability.
FORBIDDEN_HISTORY_STATE = (
    "self._jobs",
    "self._queue",
    "self._snapshot",
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


def _render_callers(page_name: str) -> list[str]:
    """Files in ``src/`` that call ``.render(...)`` on ``<page_name>``."""

    offenders: list[str] = []
    for path in _python_files(_SRC):
        for node in ast.walk(_parse(path)):
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
            if name == page_name:
                offenders.append(str(path.relative_to(_SRC)))
                break
    return offenders


# -- spec 43: no Research god object -----------------------------------


def test_the_research_package_has_no_aggregate_orchestrator() -> None:
    assert not (_RESEARCH / "orchestrator.py").exists(), (
        "research/orchestrator.py must not exist: an aggregate controller over "
        "every Research workspace is a second MainWindow"
    )


def test_no_research_god_object_is_declared() -> None:
    offenders: list[tuple[str, str]] = []
    for path in _python_files(_RESEARCH):
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.ClassDef) and node.name in FORBIDDEN_AGGREGATES:
                offenders.append((str(path.relative_to(_SRC)), node.name))
    # The window must not grow one either.
    for node in _main_window().body:
        if isinstance(node, ast.ClassDef) and node.name in FORBIDDEN_AGGREGATES:
            offenders.append(("desktop.py", node.name))
    assert not offenders, offenders


def test_each_research_workspace_has_its_own_orchestrator() -> None:
    assert (_UNIVERSE_DIR / "orchestrator.py").exists()
    assert (_HISTORY_DIR / "orchestrator.py").exists()


# -- spec 42: the window's state is gone -------------------------------


@pytest.mark.parametrize("needle", RETIRED_WINDOW_STATE)
def test_the_window_no_longer_holds_research_state(needle: str) -> None:
    source = _DESKTOP.read_text(encoding="utf-8")
    assert needle not in source, f"{needle!r} must not appear in desktop.py"


@pytest.mark.parametrize("needle", UNTOUCHED_WINDOW_STATE)
def test_the_later_slices_state_is_still_where_it_was(needle: str) -> None:
    """Spec 32/33/34: Scanner / Backtest / Cross-Section / Shadow stay put.

    Asserted positively rather than left implicit: a comment saying "not
    migrated this round" cannot fail, so a later round that half-moves one of
    these would have no guard to contradict it.
    """

    source = _DESKTOP.read_text(encoding="utf-8")
    assert needle in source, f"{needle!r} moved without its slice"


@pytest.mark.parametrize("name", RETIRED_WINDOW_METHODS)
def test_the_window_no_longer_declares_the_retired_method(name: str) -> None:
    assert _method_source(name) is None, f"{name} must be retired"


def test_the_window_declares_no_compatibility_property() -> None:
    """Spec 5/27: no forwarding property, in either spelling.

    ``@property def universe(...)`` and a plain class-level assignment would
    both keep every unmigrated consumer working, which is exactly the state the
    extraction must not leave behind.
    """

    for node in _main_window().body:
        if isinstance(node, ast.FunctionDef) and node.name in (
            "universe",
            "history",
            "universe_snapshot",
            "history_progress_percent",
        ):
            raise AssertionError(f"compatibility property {node.name!r} exists")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            for target in targets:
                if isinstance(target, ast.Name) and target.id in (
                    "universe",
                    "history",
                ):
                    raise AssertionError(
                        f"compatibility attribute {target.id!r} exists"
                    )


def test_the_window_does_not_reach_into_the_orchestrators() -> None:
    """Spec 42: no ``universe_orchestrator._x`` / ``history_orchestrator._x``."""

    offenders: list[str] = []
    for node in ast.walk(_main_window()):
        if not isinstance(node, ast.Attribute) or not node.attr.startswith("_"):
            continue
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and owner.attr in ("universe_orchestrator", "history_orchestrator")
        ):
            offenders.append(f"{owner.attr}.{node.attr}")
    assert not offenders, offenders


# -- spec 42: single render caller -------------------------------------


def test_the_window_never_calls_a_page_render_entry_point() -> None:
    offenders = _render_callers("universe_page") + _render_callers(
        "history_page"
    )
    assert offenders == [], offenders


def _page_render_count(directory: pathlib.Path) -> int:
    """How many ``self._page.render(...)`` calls the capability makes."""

    source = (directory / "orchestrator.py").read_text(encoding="utf-8")
    return source.count("self._page.render(")


@pytest.mark.parametrize(
    ("directory", "expected"),
    ((_UNIVERSE_DIR, "universe"), (_HISTORY_DIR, "history")),
)
def test_each_capability_renders_its_own_page_exactly_once(
    directory: pathlib.Path, expected: str
) -> None:
    """Spec 15/26: one render entry point per capability.

    Exactly one call site, so there is no second path that paints the page from
    a different state, and no path that paints it from stale state.
    """

    assert _page_render_count(directory) == 1, (
        f"{expected}/orchestrator.py must have exactly one page render"
    )


@pytest.mark.parametrize(
    "page_class", ("UniversePage", "HistoryPage")
)
def test_the_page_class_is_constructed_once_in_the_whole_source_tree(
    page_class: str,
) -> None:
    """A second construction would mean a second page the capability cannot see.

    The orchestrator must never build its own page: it renders the widget the
    composition root handed it, or the window would be showing one page while
    the capability paints another.

    Counted as *calls*, not as a substring: the page's own module declares
    ``class UniversePage``, which a text search cannot tell from a construction.
    """

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
            if name == page_class:
                offenders.append(str(path.relative_to(_SRC)))
                break
    assert offenders == ["desktop.py"], offenders


def test_the_universe_capability_never_mentions_the_history_page() -> None:
    offenders: list[str] = []
    for path in _python_files(_UNIVERSE_DIR):
        if "history_page" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(_SRC)))
    assert offenders == [], offenders


def test_the_history_capability_never_mentions_the_universe_page() -> None:
    offenders: list[str] = []
    for path in _python_files(_HISTORY_DIR):
        if "universe_page" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(_SRC)))
    assert offenders == [], offenders


# -- spec 35/36: the dependency rule -----------------------------------


def test_each_research_file_imports_only_its_declared_modules() -> None:
    offenders: list[tuple[str, list[str]]] = []
    for relative, allowed in ALLOWED_IMPORTS.items():
        path = _RESEARCH / relative
        assert path.exists(), f"{relative} is missing"
        undeclared = sorted(_module_imports(path) - allowed)
        if undeclared:
            offenders.append((relative, undeclared))
    assert not offenders, offenders


def test_every_declared_import_is_actually_used() -> None:
    """The other direction: a stale declaration would silently widen the gate."""

    offenders: list[tuple[str, list[str]]] = []
    for relative, allowed in ALLOWED_IMPORTS.items():
        actual = _module_imports(_RESEARCH / relative)
        unused = sorted(allowed - actual)
        if unused:
            offenders.append((relative, unused))
    assert not offenders, offenders


@pytest.mark.parametrize("name", FORBIDDEN_SYMBOLS)
def test_the_research_capabilities_import_no_forbidden_symbol(name: str) -> None:
    offenders: list[str] = []
    for path in _python_files(_RESEARCH):
        if name in _imported_names(path):
            offenders.append(str(path.relative_to(_SRC)))
    assert offenders == [], f"{name} imported by {offenders}"


def test_the_research_capabilities_import_no_forbidden_module() -> None:
    """Spec 35 as module paths, which is the half a symbol guard cannot see.

    ``import us_quant.trading.application.paper_session`` binds a module, not a
    class, so it would slip past ``FORBIDDEN_SYMBOLS`` entirely.  The capability
    services are the only ``us_quant.desktop*`` modules allowed, and they are
    allowed because they are stateless: reaching ``us_quant.desktop`` itself
    would drag in ``MainWindow``.
    """

    offenders: list[tuple[str, str]] = []
    for path in _python_files(_RESEARCH):
        for module in _module_imports(path):
            if module in ALLOWED_DESKTOP_MODULES:
                continue
            for prefix in FORBIDDEN_MODULE_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    offenders.append((str(path.relative_to(_SRC)), module))
                    break
    assert not offenders, offenders


@pytest.mark.parametrize(
    ("package", "forbidden"),
    tuple(MUTUAL_FORBIDDEN.items()),
)
def test_the_two_capabilities_do_not_know_each_other(
    package: str, forbidden: tuple[str, ...]
) -> None:
    directory = _UNIVERSE_DIR if package == "universe" else _HISTORY_DIR
    offenders: list[tuple[str, str]] = []
    for path in _python_files(directory):
        names = _imported_names(path)
        modules = _module_imports(path)
        for symbol in forbidden:
            if symbol in names:
                offenders.append((str(path.relative_to(_SRC)), symbol))
        for module in modules:
            if module.endswith(
                f"orchestration.research.{'history' if package == 'universe' else 'universe'}"
            ):
                offenders.append((str(path.relative_to(_SRC)), module))
    assert not offenders, offenders


def test_history_reaches_the_universe_only_through_a_callable() -> None:
    """Spec 36: the provider is a fact-shaped boundary, not an object handle."""

    source = (_HISTORY_DIR / "orchestrator.py").read_text(encoding="utf-8")
    assert "universe_provider: Callable[[], UniverseSnapshot | None]" in source


@pytest.mark.parametrize("needle", FORBIDDEN_HISTORY_STATE)
def test_history_holds_no_copy_of_the_queue(needle: str) -> None:
    """Spec 18: the store is the queue truth; the capability stores intents.

    ``_progress_percent`` is deliberately not in this list -- it is desktop
    presentation state, not queue truth, which is exactly the distinction the
    spec draws.  ``self._snapshot`` is forbidden because the universe
    capability's equivalent name would make a copied queue snapshot look
    reasonable; the history side must never grow one.
    """

    source = (_HISTORY_DIR / "orchestrator.py").read_text(encoding="utf-8")
    assert needle not in source, f"history must not hold {needle!r}"


def test_the_universe_capability_holds_no_worker_or_task_handle() -> None:
    """Spec 8/34: the capability must not know what a ``TaskThread`` is.

    The whole point of ``on_finished`` is that a capability learns of its own
    completion without holding the worker.  A capability that kept a handle
    could compare identity against the worker list -- which is the coupling the
    hook exists to remove -- and could also read ``isRunning()`` to guess at
    state that is really the window's.  Checked by *type-ish shape* as well as
    by name, because ``self._worker`` would be renamed long before it was
    deleted.
    """

    offenders: list[str] = []
    for path in _python_files(_RESEARCH):
        tree = _parse(path)
        for node in ast.walk(tree):
            # ``self._worker`` / ``self._task`` / ``self._thread`` in any spelling
            if isinstance(node, ast.Attribute) and node.attr in (
                "_worker",
                "_task",
                "_thread",
                "worker",
                "task",
            ):
                owner = node.value
                if isinstance(owner, ast.Name) and owner.id == "self":
                    offenders.append(
                        f"{path.relative_to(_SRC)}:{node.attr}"
                    )
            # Any import of the worker/controller machinery.
            if isinstance(node, ast.Name) and node.id in (
                "TaskThread",
                "DesktopTaskController",
            ):
                offenders.append(f"{path.relative_to(_SRC)}:{node.id}")
    assert not offenders, offenders


def test_the_universe_capability_owns_its_snapshot_as_a_stored_fact() -> None:
    """The contrast that makes the two designs legible.

    Universe stores the snapshot because ``DesktopUniverseService`` is
    stateless; History stores none because ``DesktopHistoryService`` is not.
    Asserting both halves together is what keeps the difference from reading as
    an inconsistency.
    """

    universe = (_UNIVERSE_DIR / "orchestrator.py").read_text(encoding="utf-8")
    history = (_HISTORY_DIR / "orchestrator.py").read_text(encoding="utf-8")

    assert "self._snapshot: UniverseSnapshot | None = None" in universe
    assert "self._snapshot" not in history
    assert "self._service.snapshot()" in history


# -- spec 16/30: the public surface stays small ------------------------


def _public_names(relative: str) -> set[str]:
    """Class-level names a caller may use: signals, properties, methods."""

    tree = _parse(_RESEARCH / relative)
    for node in ast.walk(tree):
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
    raise AssertionError(f"no class in {relative}")


@pytest.mark.parametrize(
    ("package", "relative"),
    (
        ("universe", "universe/orchestrator.py"),
        ("history", "history/orchestrator.py"),
    ),
)
def test_the_public_surface_is_exactly_the_declared_one(
    package: str, relative: str
) -> None:
    """Spec 16/30: a small API, compared for equality.

    Equality in both directions is the point.  A subset check would let a new
    accessor in; the reverse check would fail on a signal that was never
    declared.  ``service``/``page``/``_cancel_event`` are private by
    construction, which is what keeps the capability from being a facade over
    the objects it composes.
    """

    assert _public_names(relative) == set(PUBLIC_SURFACE[package])


@pytest.mark.parametrize("name", FORBIDDEN_ACCESSORS)
def test_no_convenience_accessor_was_added(name: str) -> None:
    """Spec 30: other workflows read the immutable snapshot, not a wrapper."""

    offenders: list[str] = []
    for path in _python_files(_RESEARCH):
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.ClassDef):
                if any(
                    isinstance(item, ast.FunctionDef) and item.name == name
                    for item in node.body
                ):
                    offenders.append(str(path.relative_to(_SRC)))
    assert offenders == [], f"{name} added by {offenders}"


# -- spec 44: line budget ----------------------------------------------

@pytest.mark.parametrize(
    "relative", ("universe/orchestrator.py", "history/orchestrator.py")
)
def test_the_line_budget_holds(relative: str) -> None:
    path = _RESEARCH / relative
    count = len(path.read_text(encoding="utf-8").splitlines())
    assert count <= LINE_BUDGET, (
        f"{relative} is {count} lines; over budget means a responsibility "
        f"leaked in (Scanner, Targeted, Market, System, or the generic task "
        f"lifecycle) -- split the code, do not raise the budget"
    )
