"""Architecture guards for the v2O-A Market orchestration extraction.

These are structural, not behavioural.  They read the source tree and assert
that the ownership the extraction moved really moved -- that ``MainWindow`` no
longer holds the feed, and that ``MarketOrchestrator`` cannot reach back into
the window or sideways into Paper, Shadow or any page but its own.

The guards are written as *exact* deltas where a set is involved: a later round
that adds a market method back must declare it here, so the boundary cannot
erode one convenient accessor at a time.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP_PATH = _SRC / "desktop.py"
_MARKET_DIR = _SRC / "desktop_v2" / "orchestration" / "market"
_ORCHESTRATOR_PATH = _MARKET_DIR / "orchestrator.py"
_RENDERER_PATH = _MARKET_DIR / "renderer.py"
_MODELS_PATH = _MARKET_DIR / "models.py"
_HEALTH_PATH = _MARKET_DIR / "health.py"

#: The base commit this round is measured against.  Recorded so the line-count
#: guard has a fixed reference rather than "whatever main is today".
BASE_COMMIT = "0b443ad9a2b48f9d76c1d0d1f1c86015cea753fb"

#: The market runtime state that must no longer be assigned anywhere on the
#: window.  Every one of these was ``self.<name>`` before the extraction.
RETIRED_WINDOW_MARKET_STATE = (
    "stream_worker",
    "stream_snapshot",
    "stream_timer",
    "_pending_stream_switch",
    "_stream_stop_pending",
    "_last_stream_event_key",
    "_last_stream_status_key",
    "_last_stream_status_log_at",
    "_last_stream_push_monotonic",
    "_quote_last_ready_monotonic",
    "_dashboard_market_stop_reason",
    "_market_scope",
    "_market_watchlist_note",
)

#: Compatibility shims the round explicitly forbids: a property that re-exposes
#: the orchestrator's runtime would leave the old call sites -- and the habit of
#: reaching for them -- in place.
FORBIDDEN_COMPATIBILITY_PROPERTIES = (
    "stream_worker",
    "stream_snapshot",
    "stream_timer",
    "_pending_stream_switch",
    "_stream_stop_pending",
)

#: The market methods the window used to own, which are deleted rather than
#: shimmed.  Declared as an exact set so a re-added one fails here.
RETIRED_WINDOW_MARKET_METHODS = (
    "_activate_pending_stream_switch",
    "_active_stream_provider",
    "_invalidate_stream_snapshot",
    "_market_controls",
    "_poll_stream_snapshot",
    "_publish_market_controls",
    "_publish_market_health",
    "_publish_market_view",
    "_quote_was_recently_ready",
    "_request_stream_switch",
    "_start_stream",
    "_stop_stream",
    "_stream_failed",
    "_stream_finished",
    "_stream_is_live",
    "_stream_snapshot_pushed",
    "_stream_snapshot_received",
    "_stream_symbols_from_input",
    "_update_quote_readiness",
)

#: What the orchestrator may import.  Each entry is a capability, not merely a
#: module it happens not to use: an orchestrator that could import the Paper
#: workflow could decide the interlock itself, which is exactly what this round
#: keeps on the window.
FORBIDDEN_ORCHESTRATOR_IMPORTS = (
    "us_quant.desktop",
    "us_quant.paper",
    "us_quant.paper_workflow",
    "us_quant.paper_trading_service",
    "us_quant.paper_order_journal",
    "us_quant.paper_order_models",
    "us_quant.paper_session",
    "us_quant.shadow",
    "us_quant.auto_quant",
    "us_quant.workflow_controller",
    "us_quant.runtime_supervisor",
    "us_quant.runtime_events",
    "us_quant.artifact_state",
    "us_quant.desktop_v2.workflows",
    "us_quant.desktop_v2.pages.dashboard",
    "us_quant.desktop_v2.pages.execution",
    "us_quant.desktop_v2.pages.system",
    "us_quant.desktop_v2.pages.research",
    "us_quant.desktop_v2.pages.account",
    "us_quant.desktop_v2.pages.risk",
    "us_quant.desktop_v2.pages.strategy",
    "us_quant.trading.runtime.trading",
    "us_quant.trading.runtime.artifacts",
    "us_quant.trading.runtime.models",
    "us_quant.trading.adapters",
    "us_quant.trading.composition",
    "ibapi",
)

#: Names the orchestrator must not *call* even if the import is indirect.
FORBIDDEN_ORCHESTRATOR_CALLS = (
    "PaperWorkflowPhase",
    "PaperWorkflowController",
    "PaperTradingService",
    "ShadowPaperEngine",
    "TradingRuntime",
    "build_trading_runtime",
    "WorkflowController",
    "RuntimeSupervisor",
)

#: The orchestrator's public read surface.  Anything else it exposes is a leak
#: of runtime state, so this is asserted as an exact set rather than a subset.
#:
#: ``is_live`` and ``worker_running`` are both booleans and they answer
#: different questions: ``is_live`` is feed availability (false as soon as a
#: stop is pending), ``worker_running`` is actual thread liveness.  Shutdown
#: reads the second one.  Neither exposes the worker: ``worker_running`` is a
#: *fact about* the runtime, not a handle to it.
PUBLIC_READ_SURFACE = (
    "active_market_exchange",
    "active_source_id",
    "is_live",
    "polling_active",
    "recently_ready_symbols",
    "snapshot",
    "stop_reason",
    "was_recently_ready",
    "worker_running",
)

#: The orchestrator's public write/lifecycle surface.
PUBLIC_COMMAND_SURFACE = (
    "maybe_request_extended_session_rotation",
    "request_switch",
    "selected_provider",
    "set_readiness_inputs",
    "set_scope",
    "set_selected_provider",
    "set_subscription_symbols",
    "start",
    "stop",
    "stop_polling",
    "subscription_symbols",
)

#: Runtime state that must never be a public attribute or property.
FORBIDDEN_PUBLIC_RUNTIME_STATE = (
    "worker",
    "timer",
    "pending_switch",
    "readiness",
    "last_push",
    "_worker",
    "_timer",
    "_pending",
    "_stop_pending",
    "_snapshot",
)

#: The worker *handle* names.  ``worker_running`` is a boolean fact and is
#: allowed (it is in ``PUBLIC_READ_SURFACE``); a member that hands back the
#: ``StreamWorker``/``QThread`` object is not, under any spelling.  Checked as
#: an exact set so the accessor guard below can name every leak it knows.
FORBIDDEN_WORKER_HANDLE_MEMBERS = (
    "worker",
    "stream_worker",
    "_worker",
)

#: Per-file line caps.  The orchestrator's is the spec's hard ceiling: a file
#: that grows past it must be split by responsibility, not re-budgeted.
LINE_BUDGETS = {
    "__init__.py": 40,
    "models.py": 120,
    "health.py": 200,
    "renderer.py": 300,
    "orchestrator.py": 550,
}

#: The page entry points only the orchestrator (or its renderer) may call.
#: A second caller would make the page's state ambiguous.
PAGE_RENDER_ENTRY_POINTS = (
    "render",
    "render_controls",
    "render_health",
    "render_failure",
    "render_scope",
)


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _python_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _imports(path: pathlib.Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _matches(modules: set[str], prefixes: tuple[str, ...]) -> set[str]:
    return {
        module
        for module in modules
        for prefix in prefixes
        if module == prefix or module.startswith(f"{prefix}.")
    }


def _main_window(path: pathlib.Path) -> ast.ClassDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise AssertionError("MainWindow not found")


def _method_names(path: pathlib.Path) -> set[str]:
    return {
        node.name
        for node in _main_window(path).body
        if isinstance(node, ast.FunctionDef)
    }


def _assigned_self_attrs(path: pathlib.Path) -> set[str]:
    """Every ``self.<name>`` assigned anywhere in the module."""

    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                names.add(target.attr)
    return names


def _property_names(path: pathlib.Path) -> set[str]:
    """The class-level properties ``MainWindow`` defines."""

    names: set[str] = set()
    for node in _main_window(path).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if any(
            (
                isinstance(decorator, ast.Name)
                and decorator.id == "property"
            )
            or (
                isinstance(decorator, ast.Attribute)
                and decorator.attr == "setter"
            )
            for decorator in node.decorator_list
        ):
            names.add(node.name)
    return names


def _class_of(path: pathlib.Path, name: str) -> ast.ClassDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found in {path.name}")


def _public_members(path: pathlib.Path, name: str) -> set[str]:
    """Public methods and properties of one class, minus dunders."""

    members: set[str] = set()
    for node in _class_of(path, name).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name.startswith("_"):
            continue
        members.add(node.name)
    return members


def _method_source(path: pathlib.Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    for node in _main_window(path).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} not found")


# -- Guard A: the window no longer owns the market runtime ---------------


@pytest.mark.parametrize("attribute", RETIRED_WINDOW_MARKET_STATE)
def test_the_window_assigns_no_market_runtime_state(attribute: str) -> None:
    """Every one of these was ``self.<name>`` before the extraction.

    The window may hold the orchestrator and nothing inside it: a window that
    still stamped the snapshot would be a second owner of the feed, and the two
    copies would drift the first time one path forgot to update the other.
    """

    assert attribute not in _assigned_self_attrs(_DESKTOP_PATH), attribute


@pytest.mark.parametrize(
    "attribute", FORBIDDEN_COMPATIBILITY_PROPERTIES
)
def test_the_window_has_no_compatibility_property(attribute: str) -> None:
    """A property that re-exposes the runtime is a shim, not a migration.

    It would satisfy every old call site while leaving the old ownership
    intact -- the window would still read "the worker" and still be able to
    start a second feed from it.
    """

    assert attribute not in _property_names(_DESKTOP_PATH), attribute


@pytest.mark.parametrize("name", RETIRED_WINDOW_MARKET_METHODS)
def test_the_retired_market_methods_are_gone(name: str) -> None:
    assert name not in _method_names(_DESKTOP_PATH), name


def test_the_window_still_owns_the_page_and_the_orchestrator() -> None:
    """The complement: the window composes both, and names no market widget."""

    names = _assigned_self_attrs(_DESKTOP_PATH)
    assert "market_page" in names
    assert "market_orchestrator" in names


def test_the_window_never_reaches_through_the_orchestrator() -> None:
    """``market_orchestrator._worker`` is the leak this guard exists for.

    Every ``self.market_orchestrator.<name>`` in the module must be a declared
    public member.  A private read would let the window keep a second handle on
    the feed, which is the ownership the extraction removed.
    """

    allowed = set(PUBLIC_READ_SURFACE) | set(PUBLIC_COMMAND_SURFACE)
    offending: list[tuple[str, int]] = []
    for node in ast.walk(_tree(_DESKTOP_PATH)):
        if not isinstance(node, ast.Attribute):
            continue
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "self"
            and owner.attr == "market_orchestrator"
        ):
            if node.attr not in allowed:
                offending.append((node.attr, node.lineno))
    assert not offending, offending


def test_the_window_holds_no_stream_worker() -> None:
    """The class name must not appear at all: neither an import nor a build."""

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert "StreamWorker" not in source
    assert "MarketDataStartRequest" not in source
    assert "MarketDataCredentials" not in source


# -- Guard B: the orchestrator knows only the market capability ----------


def test_the_orchestrator_imports_no_other_capability() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_MARKET_DIR):
        for module in _matches(
            _imports(path), FORBIDDEN_ORCHESTRATOR_IMPORTS
        ):
            offending.append((path.name, module))
    assert not offending, offending


def test_the_orchestrator_never_names_another_workflow() -> None:
    """Even without an import, constructing one would be the same coupling."""

    offending: list[tuple[str, str]] = []
    for path in _python_files(_MARKET_DIR):
        tree = _tree(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(
                node.func, "attr", None
            )
            if name in FORBIDDEN_ORCHESTRATOR_CALLS:
                offending.append((path.name, str(name)))
    assert not offending, offending


def test_the_orchestrator_never_reaches_a_window_attribute() -> None:
    """``self.window``, ``self.main_window`` and friends must not exist."""

    forbidden = {
        "window",
        "main_window",
        "desktop",
        "context",
        "services",
        "pages",
        "runtime",
    }
    offending: list[tuple[str, str]] = []
    for path in _python_files(_MARKET_DIR):
        for node in ast.walk(_tree(path)):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
                and node.attr in forbidden
            ):
                offending.append((path.name, node.attr))
    assert not offending, offending


def test_the_models_and_health_modules_are_qt_free() -> None:
    """The published facts must be inspectable without a widget toolkit."""

    for path in (_MODELS_PATH, _HEALTH_PATH):
        modules = _imports(path)
        assert not any(
            module == "PySide6" or module.startswith("PySide6.")
            for module in modules
        ), path.name


def test_the_models_module_is_importable_without_qt() -> None:
    """The Qt-free claim, exercised rather than read off the imports.

    Loaded by *file path* in a fresh interpreter on purpose: importing it as a
    submodule would execute the package initializer, which re-exports the Qt
    orchestrator, and this guard is about the module's own dependencies.
    """

    import subprocess
    import sys

    snippet = (
        "import importlib.util, sys; "
        f"spec = importlib.util.spec_from_file_location('probe', r'{_MODELS_PATH}'); "
        "module = importlib.util.module_from_spec(spec); "
        "sys.modules['probe'] = module; "
        "spec.loader.exec_module(module); "
        "raise SystemExit(1 if 'PySide6' in sys.modules else 0)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


# -- Guard C: the public surface is exactly the declared one --------------


def test_the_orchestrator_exposes_exactly_the_declared_surface() -> None:
    """Public members are the API; anything else would be a leak.

    Asserted in both directions: a member that disappears breaks the callers
    this round wrote, and a member that appears unannounced is a new coupling
    nobody reviewed.
    """

    public = _public_members(_ORCHESTRATOR_PATH, "MarketOrchestrator")
    declared = set(PUBLIC_READ_SURFACE) | set(PUBLIC_COMMAND_SURFACE)
    assert public == declared, sorted(public ^ declared)


def test_the_orchestrator_exposes_no_worker_handle() -> None:
    """``worker_running`` is a boolean; the worker object stays unreachable.

    Two shapes are refused, because they leak differently:

    * a member *named* for the handle (``worker`` / ``stream_worker`` /
      ``_worker``) -- ``PUBLIC_READ_SURFACE`` also rejects an undeclared
      public member, but this states the rule where a reader looks for it;
    * a public member whose return value *is* the handle (``return
      self._worker``).  A private helper reading ``self._worker`` into a local
      is fine and unavoidable -- ``worker_running`` does exactly that -- so
      the check is on what is handed back, not on what is read.
    """

    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    defined = {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
    for forbidden in FORBIDDEN_WORKER_HANDLE_MEMBERS:
        assert forbidden not in defined, forbidden

    leaked: list[tuple[str, int]] = []
    for node in _class_of(_ORCHESTRATOR_PATH, "MarketOrchestrator").body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Return) or child.value is None:
                continue
            for inner in ast.walk(child.value):
                if (
                    isinstance(inner, ast.Attribute)
                    and isinstance(inner.value, ast.Name)
                    and inner.value.id == "self"
                    and inner.attr in {"_worker", "worker", "stream_worker"}
                ):
                    leaked.append((node.name, child.lineno))
    assert not leaked, leaked


def test_the_orchestrator_exposes_no_runtime_state() -> None:
    """The runtime half must stay private, in both naming conventions."""

    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    exposed: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not any(
            isinstance(decorator, ast.Name) and decorator.id == "property"
            for decorator in node.decorator_list
        ):
            continue
        if not node.name.startswith("_"):
            exposed.add(node.name)
    for forbidden in ("worker", "timer", "pending_switch", "readiness"):
        assert forbidden not in exposed, forbidden


# -- Guard D: the page is rendered from one place ------------------------


def test_only_the_orchestration_calls_the_page_render_entry_points() -> None:
    """One writer for the page, or the route's state becomes ambiguous.

    The renderer is the orchestrator's own projection, so both are allowed to
    call these; anything else -- the window above all -- would reintroduce the
    "a dozen handlers write widgets" shape this round removes.
    """

    allowed_owners = {
        "desktop_v2/orchestration/market/orchestrator.py",
        "desktop_v2/orchestration/market/renderer.py",
    }
    offending: list[tuple[str, str]] = []
    for path in _python_files(_SRC):
        relative = path.relative_to(_SRC).as_posix()
        if relative in allowed_owners:
            continue
        tree = _tree(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in PAGE_RENDER_ENTRY_POINTS:
                continue
            owner = func.value
            if (
                isinstance(owner, ast.Attribute)
                and owner.attr == "market_page"
            ):
                offending.append((relative, func.attr))
    assert not offending, offending


def test_the_window_never_calls_a_page_render_entry_point() -> None:
    """The same rule stated for the window alone, where it is load-bearing."""

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    for entry in PAGE_RENDER_ENTRY_POINTS:
        assert f"market_page.{entry}" not in source, entry


def test_only_the_orchestrator_holds_the_worker_and_the_timer() -> None:
    """Both are runtime truth: a second holder is a second owner."""

    holders: dict[str, list[str]] = {"StreamWorker": [], "QTimer": []}
    for path in _python_files(_SRC):
        if path.name in {"desktop_workers.py"}:
            continue
        relative = path.relative_to(_SRC).as_posix()
        source = path.read_text(encoding="utf-8")
        if "StreamWorker(" in source:
            holders["StreamWorker"].append(relative)
        if "QTimer(" in source:
            holders["QTimer"].append(relative)

    assert holders["StreamWorker"] == [
        "desktop_v2/orchestration/market/orchestrator.py"
    ], holders["StreamWorker"]
    # ``QTimer`` is a general Qt facility; what must not happen is a *second*
    # market poll timer, which the snapshot-timer name would reveal.
    assert "stream_timer" not in _DESKTOP_PATH.read_text(encoding="utf-8")


def test_the_window_registers_the_orchestrators_lifecycle() -> None:
    """Shutdown goes through the public API, never through the worker."""

    register = _method_source(_DESKTOP_PATH, "_register_runtime_components")
    assert "market_orchestrator.stop_polling" in register
    assert "market_orchestrator.polling_active" in register
    # The market component's liveness probe is *thread* liveness.  ``is_live``
    # is false as soon as a stop is pending, so wiring it here would let a
    # timed-out stop report a clean release over a live network thread.
    assert "market_orchestrator.worker_running" in register
    assert "market_orchestrator.is_live" not in register
    assert "market_orchestrator._worker" not in register
    assert "self.stream_worker" not in register

    close = _method_source(_DESKTOP_PATH, "closeEvent")
    assert "self.market_orchestrator.worker_running" in close
    assert "self.market_orchestrator.is_live" not in close
    assert "StreamWorker" not in close


# -- Guard E: the snapshot fan-out does not take ownership back ----------


def test_the_snapshot_bridge_only_fans_out() -> None:
    """The one cross-workflow bridge this round leaves behind.

    It is allowed to hand the snapshot to the other workflows; it must not
    render the page, touch the worker or maintain the readiness cache, all of
    which moved into the orchestrator.
    """

    source = _method_source(_DESKTOP_PATH, "_on_market_snapshot_changed")
    for forbidden in (
        "market_page.render",
        "_worker",
        "stream_worker",
        "_recent_ready",
        "_update_quote_readiness",
        "_publish_market_view",
    ):
        assert forbidden not in source, forbidden


def test_the_snapshot_bridge_still_reaches_every_declared_consumer() -> None:
    """The fan-out must not silently lose a consumer.

    Deleting one of these calls would leave a workflow reading a stale market
    fact, which no other test in this file would catch.
    """

    source = _method_source(_DESKTOP_PATH, "_on_market_snapshot_changed")
    for consumer in (
        "workflow_controller.market_account.update",
        "_record_minute_snapshot",
        "_publish_dashboard_view",
        "_populate_auto_quant_candidates",
        "_refresh_target_preflight",
        "paper_workflow.on_stream",
        "shadow_engine.on_stream",
    ):
        assert consumer in source, consumer


# -- Guard F: line budgets and the net reduction -------------------------


@pytest.mark.parametrize(("name", "budget"), sorted(LINE_BUDGETS.items()))
def test_each_orchestration_file_stays_inside_its_budget(
    name: str, budget: int
) -> None:
    lines = len(
        (_MARKET_DIR / name).read_text(encoding="utf-8").splitlines()
    )
    assert lines <= budget, f"{name} is {lines} lines, budget {budget}"


def test_the_orchestrator_does_not_become_the_new_god_file() -> None:
    """The hard ceiling, asserted separately from the softer budgets.

    A 1000-line orchestrator would be the same problem moved one file over, so
    this is a ceiling rather than a target: past it, split by responsibility.
    """

    lines = len(
        _ORCHESTRATOR_PATH.read_text(encoding="utf-8").splitlines()
    )
    assert lines <= 550, f"orchestrator.py is {lines} lines"


def test_the_window_actually_shrank() -> None:
    """The extraction must remove code, not add a second copy of it.

    Measured against the recorded base commit, so the guard stays meaningful
    after later rounds change the file.
    """

    import subprocess

    base = subprocess.run(
        ["git", "show", f"{BASE_COMMIT}:src/us_quant/desktop.py"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    ).stdout
    if not base:
        pytest.skip(f"base commit {BASE_COMMIT} is unreachable")

    before = len(base.splitlines())
    after = len(_DESKTOP_PATH.read_text(encoding="utf-8").splitlines())
    assert after <= before - 300, f"desktop.py went {before} -> {after}"


def test_the_orchestration_package_is_the_only_new_home() -> None:
    """No ``DesktopManager``/``ApplicationContext`` was introduced.

    A single object holding market *and* account *and* paper would be the God
    object this round forbids, so the package holds one directory per capability.
    ``research/`` is the exception that proves the rule: it is a *route
    aggregate* -- the navigation-level grouping -- not a capability, so it holds
    no orchestrator of its own and each workspace that owns runtime truth gets a
    subpackage instead.  That structure is asserted in
    ``tests/test_desktop_research_foundations_architecture.py``; what matters
    here is that ``research/`` did not become a fourth kind of thing.
    """

    children = {
        path.name
        for path in (_SRC / "desktop_v2" / "orchestration").iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert children == {"market", "account", "research"}, children

    # The route aggregate must stay an aggregate: a capability-level
    # orchestrator at its root is the God object in a new costume.
    assert not (
        _SRC / "desktop_v2" / "orchestration" / "research" / "orchestrator.py"
    ).exists()

    research_children = {
        path.name
        for path in (
            _SRC / "desktop_v2" / "orchestration" / "research"
        ).iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert research_children == {
        "universe",
        "history",
        "scanner",
        "backtest",
    }, research_children

    forbidden_classes = (
        "DesktopManager",
        "DesktopController",
        "ApplicationContext",
        "DesktopContext",
        "ServiceContainer",
        "WorkflowManager",
    )
    defined: set[str] = set()
    for path in _python_files(_SRC):
        defined |= {
            node.name
            for node in _tree(path).body
            if isinstance(node, ast.ClassDef)
        }
    for name in forbidden_classes:
        assert name not in defined, name
