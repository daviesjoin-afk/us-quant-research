"""Architecture guards for the v2O-D Shadow orchestration extraction.

These are structural, not behavioural.  They read the source tree and assert that
the ownership the extraction moved really moved, and that the new capability
cannot reach back into the window or sideways into any other capability.

Three rules matter most, and each has a test that fails for the right reason:

* **Shadow orchestration imports no other capability.**  An orchestrator that
  could import Market / Account / Research / Paper could decide their fan-out
  itself, which is exactly how the centre of gravity comes back;
* **it holds no second Shadow truth.**  ``ShadowPaperEngine`` owns the session's
  mutable state; this package stores a reference and a snapshot, never a copy;
* **the window no longer runs the Shadow runtime.**  It may construct, wire, read
  cross-capability facts and route published ones -- and nothing else.

The import set is closed by an **allowlist** as well as a denylist.  A denylist
only forbids the couplings someone thought to name; ``test_the_..._imports_nothing_outside_the_allowlist``
means a new dependency has to be declared here on purpose, where a reviewer sees
it.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP_PATH = _SRC / "desktop.py"
_SHADOW_DIR = _SRC / "desktop_v2" / "orchestration" / "shadow"
_ORCHESTRATOR_PATH = _SHADOW_DIR / "orchestrator.py"
_QUERIES_PATH = _SHADOW_DIR / "queries.py"
_MODELS_PATH = _SHADOW_DIR / "models.py"

#: The Shadow runtime state the window must no longer hold.  ``shadow_engine``
#: and ``shadow_snapshot`` were the mutable truth this round moved.
RETIRED_WINDOW_SHADOW_STATE = (
    "shadow_engine",
    "shadow_snapshot",
)

#: The window methods the extraction deleted rather than shimmed.  Declared as an
#: exact set so a re-added one fails here.
RETIRED_WINDOW_SHADOW_METHODS = (
    "_start_shadow",
    "_stop_shadow",
)

#: What the Shadow package may import.  Each entry is a capability or a shared
#: boundary, not merely a module it happens not to use yet.
ALLOWED_IMPORTS = (
    "__future__",
    "collections.abc",
    "dataclasses",
    "datetime",
    "decimal",
    "re",
    "typing",
    # Qt, for the signals every orchestrator in this layer exposes.  Only
    # ``QtCore``: see the widget guard below.
    "PySide6.QtCore",
    "us_quant.desktop_v2.orchestration.shadow",
    # The Shadow capability itself.  This is the public API being *consumed*.
    "us_quant.shadow.config",
    "us_quant.shadow.engine",
    "us_quant.shadow.models",
    "us_quant.shadow.store",
    # Domain value types the gates read.  Read-only, and architecturally allowed
    # for every capability in this layer.
    "us_quant.trading.domain.market",
    "us_quant.trading.domain.strategy",
    "us_quant.universe",
)

#: The same rule stated the other way, kept because it names the capabilities the
#: spec calls out and gives a more legible failure message.  Both run: the
#: denylist documents intent, the allowlist closes the set.
FORBIDDEN_IMPORTS = (
    "us_quant.desktop",
    "us_quant.desktop_v2.orchestration.market",
    "us_quant.desktop_v2.orchestration.account",
    "us_quant.desktop_v2.orchestration.research",
    "us_quant.desktop_v2.workflows",
    "us_quant.workflow_controller",
    "us_quant.market_data",
    "us_quant.runtime_supervisor",
    "us_quant.runtime_events",
    "us_quant.artifact_state",
    "us_quant.desktop_workers",
    "us_quant.desktop_tasks",
    "us_quant.trading.application",
    "us_quant.trading.runtime",
    "us_quant.trading.adapters",
    "us_quant.trading.composition",
    "ibapi",
)

#: Names the package must not *call*, even via an allowed module.  The Paper and
#: execution lifecycle is v2O-E's, and the market/account runtimes belong to their
#: own capabilities.
FORBIDDEN_CALLS = (
    "MainWindow",
    "MarketOrchestrator",
    "AccountOrchestrator",
    "UniverseOrchestrator",
    "HistoryOrchestrator",
    "ScannerOrchestrator",
    "BacktestOrchestrator",
    "CrossSectionOrchestrator",
    "TargetedSessionOrchestrator",
    "TargetedEvidenceOrchestrator",
    "WorkflowController",
    "PaperWorkflowController",
    "PaperTradingService",
    "PaperSessionCoordinator",
    "TradingRuntime",
    "ExecutionApplication",
    "RiskApplication",
    "RuntimeSupervisor",
    "RuntimeEventStore",
    "TaskThread",
    "DesktopTaskController",
    "StreamWorker",
)

#: Qt classes the orchestrator may not touch.  §17 of the round forbids widget
#: mutation: the capability publishes facts and signals, and the window paints.
FORBIDDEN_QT_NAMES = (
    "QWidget",
    "QLabel",
    "QPushButton",
    "QComboBox",
    "QLineEdit",
    "QTableWidget",
    "QTableView",
    "QMessageBox",
    "QTimer",
    "QThread",
)

#: The orchestrator's public surface, asserted exactly in both directions.
PUBLIC_SURFACE = (
    "snapshot",
    "is_active",
    "recent_fills",
    "start",
    "stop",
    "shutdown",
    "on_market_snapshot",
)

#: The Qt signals the window relies on.
PUBLIC_SIGNALS = ("refused", "runtime_event_requested", "log_requested")

#: Per-file line caps.  The orchestrator is *sequencing*, so it stays small: §7 of
#: the round says a 700-1000 line orchestrator means core logic was copied in.
#: These caps are set from the real sizes with headroom for a fix, not chosen to
#: be tight -- roughly 182 of the orchestrator's lines are code, and the rest is
#: the docstrings that record the ordering constraints a maintainer would
#: otherwise have to rediscover.
LINE_BUDGETS = {
    "__init__.py": 60,
    "models.py": 240,
    "queries.py": 300,
    "orchestrator.py": 360,
}

#: Every ``self.shadow_orchestrator.<name>`` the window may reach for.
WINDOW_ALLOWED_ORCHESTRATOR_MEMBERS = set(PUBLIC_SURFACE) | set(PUBLIC_SIGNALS)

#: The page signals the window must still connect, and where they now go.
SHADOW_INTENT_WIRING = (
    ("shadow_start_requested", "self.shadow_orchestrator.start"),
    ("shadow_stop_requested", "self.shadow_orchestrator.stop"),
)


# -- helpers ------------------------------------------------------------


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
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _assigned_self_attrs(path: pathlib.Path) -> set[str]:
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


def _public_members(path: pathlib.Path, name: str) -> set[str]:
    members: set[str] = set()
    for node in _tree(path).body:
        if not isinstance(node, ast.ClassDef) or node.name != name:
            continue
        for member in node.body:
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not member.name.startswith("_"):
                    members.add(member.name)
    return members


def _signal_members(path: pathlib.Path, name: str) -> set[str]:
    members: set[str] = set()
    for node in _tree(path).body:
        if not isinstance(node, ast.ClassDef) or node.name != name:
            continue
        for member in node.body:
            value = None
            targets: list[ast.expr] = []
            if isinstance(member, ast.Assign):
                targets, value = list(member.targets), member.value
            elif isinstance(member, ast.AnnAssign):
                targets, value = [member.target], member.value
            if value is None:
                continue
            if (
                isinstance(value, ast.Call)
                and getattr(value.func, "id", None) == "Signal"
            ):
                targets_names = [
                    target.id for target in targets if isinstance(target, ast.Name)
                ]
                members.update(targets_names)
    return members


def _method_source(path: pathlib.Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    for node in _main_window(path).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} not found")


def _called_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(
                node.func, "attr", None
            )
            if name:
                names.add(str(name))
    return names


# -- Guard A: the window no longer runs the Shadow runtime ---------------


@pytest.mark.parametrize("attribute", RETIRED_WINDOW_SHADOW_STATE)
def test_the_window_assigns_no_shadow_runtime_state(attribute: str) -> None:
    assert attribute not in _assigned_self_attrs(_DESKTOP_PATH), attribute


@pytest.mark.parametrize("name", RETIRED_WINDOW_SHADOW_METHODS)
def test_the_retired_shadow_methods_are_gone(name: str) -> None:
    assert name not in _method_names(_DESKTOP_PATH), name


def test_the_window_still_owns_the_store_and_the_shared_lease() -> None:
    """Two Shadow-named handles legitimately stay, and neither is runtime truth.

    The *store* is the persistence the terminal export reads through the
    capability, and the *lease* is the shared execution handle
    ``WorkflowController`` gives to Paper and Shadow alike -- keeping it here is
    what makes "only one of them holds execution" structural rather than checked.
    """

    names = _assigned_self_attrs(_DESKTOP_PATH)
    assert "shadow_store" in names
    assert "shadow_workflow" in names
    assert "shadow_orchestrator" in names


def test_the_window_composes_the_shadow_orchestrator_once() -> None:
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert source.count("ShadowOrchestrator(") == 1


def test_the_window_never_reaches_through_the_orchestrator() -> None:
    """Every ``self.shadow_orchestrator.<name>`` must be a declared member."""

    offending: list[tuple[str, int]] = []
    for node in ast.walk(_tree(_DESKTOP_PATH)):
        if not isinstance(node, ast.Attribute):
            continue
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "self"
            and owner.attr == "shadow_orchestrator"
        ):
            if node.attr not in WINDOW_ALLOWED_ORCHESTRATOR_MEMBERS:
                offending.append((node.attr, node.lineno))
    assert not offending, offending


def test_the_shadow_intents_reach_the_capability() -> None:
    """The two page intents are wired to the capability, not to a window handler."""

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    for signal, target in SHADOW_INTENT_WIRING:
        assert f"{signal}.connect({target})" in source, signal


def test_the_window_does_not_build_a_shadow_engine_or_config() -> None:
    """Composing the engine and its config is the capability's job now."""

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert "ShadowPaperEngine(" not in source
    assert "build_targeted_shadow_config(" not in source


def test_the_window_does_not_read_the_shadow_store_directly() -> None:
    """The export reads fills through the capability, so the store has one reader."""

    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert "shadow_store.recent_fills" not in source
    assert "shadow_orchestrator.recent_fills" in source


def test_the_market_stop_interlock_goes_through_the_capability() -> None:
    """The interlock stays on the window; the stop is the capability's."""

    method = _method_source(_DESKTOP_PATH, "_stop_market_data")
    assert "self.shadow_orchestrator.is_active" in method
    assert "self.shadow_orchestrator.stop()" in method


def test_the_snapshot_bridge_hands_the_fact_to_the_capability() -> None:
    """The bridge fans out; it does not drive a Shadow engine itself.

    ``paper_workflow.on_stream`` legitimately remains in this method -- the Paper
    workflow is a separate capability with its own bridge -- so the check is that
    no *Shadow engine* is driven here, not that the substring is absent.
    """

    method = _method_source(_DESKTOP_PATH, "_on_market_snapshot_changed")
    assert "self.shadow_orchestrator.on_market_snapshot(snapshot)" in method
    assert "shadow_engine" not in method
    assert "shadow_snapshot =" not in method


def test_close_time_teardown_uses_the_quiet_shutdown() -> None:
    """Shutdown must not publish, so a close cannot write a "stopped" event."""

    method = _method_source(_DESKTOP_PATH, "closeEvent")
    assert "self.shadow_orchestrator.shutdown()" in method
    assert "self.shadow_orchestrator.stop()" not in method


# -- Guard B: the Shadow package knows only the Shadow capability --------


def test_the_package_imports_no_other_capability() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_SHADOW_DIR):
        for module in _matches(_imports(path), FORBIDDEN_IMPORTS):
            offending.append((path.name, module))
    assert not offending, offending


def test_the_package_imports_nothing_outside_the_allowlist() -> None:
    """The import set is closed, not merely denylisted."""

    offending: list[tuple[str, str]] = []
    for path in _python_files(_SHADOW_DIR):
        for module in sorted(_imports(path)):
            allowed = any(
                module == entry or module.startswith(f"{entry}.")
                for entry in ALLOWED_IMPORTS
            )
            if not allowed:
                offending.append((path.name, module))
    assert not offending, offending


def test_the_package_never_imports_a_forbidden_symbol() -> None:
    """Even an allowed module must not supply a forbidden *name*."""

    offending: list[tuple[str, str]] = []
    for path in _python_files(_SHADOW_DIR):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if alias.name in FORBIDDEN_CALLS:
                    offending.append((path.name, alias.name))
                if alias.asname in FORBIDDEN_CALLS:
                    offending.append((path.name, str(alias.asname)))
    assert not offending, offending


def test_the_package_never_names_another_capability() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_SHADOW_DIR):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(
                node.func, "attr", None
            )
            if name in FORBIDDEN_CALLS:
                offending.append((path.name, str(name)))
    assert not offending, offending


def test_the_orchestrator_never_reaches_a_window_attribute() -> None:
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
    for path in _python_files(_SHADOW_DIR):
        for node in ast.walk(_tree(path)):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
                and node.attr in forbidden
            ):
                offending.append((path.name, node.attr))
    assert not offending, offending


# -- Guard C: Qt boundary ------------------------------------------------


def test_the_orchestrator_touches_no_widget_and_no_timer() -> None:
    """Facts and signals out; the window paints.  No widget mutation here."""

    offending: list[tuple[str, str]] = []
    for path in _python_files(_SHADOW_DIR):
        names = _called_names(path)
        imported: set[str] = set()
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ImportFrom):
                imported.update(alias.name for alias in node.names)
        for name in FORBIDDEN_QT_NAMES:
            if name in names or name in imported:
                offending.append((path.name, name))
    assert not offending, offending


def test_the_orchestrator_imports_only_qtcore() -> None:
    """``QtWidgets`` / ``QtGui`` would be the widget boundary being crossed."""

    offending: list[tuple[str, str]] = []
    for path in _python_files(_SHADOW_DIR):
        for module in _imports(path):
            if module.startswith("PySide6") and module != "PySide6.QtCore":
                offending.append((path.name, module))
    assert not offending, offending


def test_the_queries_and_models_modules_are_qt_free() -> None:
    """These two carry rules and wording; neither has any business importing Qt."""

    for path in (_QUERIES_PATH, _MODELS_PATH):
        modules = _imports(path)
        assert not any(
            module == "PySide6" or module.startswith("PySide6.")
            for module in modules
        ), path.name


@pytest.mark.parametrize("name", ("queries.py", "models.py"))
def test_the_qt_free_modules_load_in_a_fresh_interpreter(name: str) -> None:
    """Loaded in a fresh interpreter with the package's ``__init__`` bypassed.

    Both modules import sibling modules by relative path, so loading the file
    alone would fail on the import rather than telling us anything.  A bare parent
    package is planted first, and ``__init__`` is deliberately *not* executed: it
    imports the orchestrator, which is allowed to import QtCore, so going through
    the real package would make this test pass for the wrong reason.
    """

    snippet = (
        "import importlib.util, sys, types; "
        "pkg = types.ModuleType('us_quant.desktop_v2.orchestration.shadow'); "
        f"pkg.__path__ = [r'{_SHADOW_DIR}']; "
        "sys.modules['us_quant.desktop_v2.orchestration.shadow'] = pkg; "
        f"spec = importlib.util.spec_from_file_location('probe', r'{_SHADOW_DIR / name}'); "
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
    assert completed.returncode == 0, (
        f"{name} must load without Qt\n{completed.stderr}"
    )


# -- Guard D: no second Shadow truth -------------------------------------


def test_the_orchestrator_holds_no_second_engine_state() -> None:
    """It stores a reference and a snapshot -- never the engine's own fields.

    ``session_id``, ``cash``, ``realized_pnl``, ``position`` and ``fills`` are the
    engine's.  A local copy would be the second truth this extraction exists to
    prevent, and the two would disagree the first time one path forgot to update
    the other.

    Checked as *assignments* rather than as text: the module docstring legitimately
    names these fields while explaining that the engine owns them, so a substring
    search would fire on its own explanation.
    """

    stored: set[str] = set()
    for node in ast.walk(_tree(_ORCHESTRATOR_PATH)):
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
                stored.add(target.attr)

    banned = {
        "_session_id",
        "_cash",
        "_realized_pnl",
        "_daily_realized_pnl",
        "_position",
        "_fills",
        "_trades_today",
        "_marks",
    }
    assert not (stored & banned), sorted(stored & banned)
    # The only engine state kept is the reference and its last reading.
    assert stored & {"_engine", "_snapshot"} == {"_engine", "_snapshot"}


def test_recent_fills_delegates_rather_than_caching() -> None:
    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert "return self._store.recent_fills(limit)" in source


def test_the_engine_is_built_from_the_frozen_request() -> None:
    """Every gate input comes from the request, so nothing is re-read mid-build."""

    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    for field in (
        "request.parameters",
        "request.initial_cash",
        "request.daily_loss_limit",
        "request.strategy_version_id",
        "request.parameter_hash",
        "request.target_symbol",
    ):
        assert field in source, field


def test_the_provenance_is_read_at_build_time_not_at_gate_time() -> None:
    """A refused start must never touch the portfolio, as the retired one did not."""

    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    build_start = source.index("def _build_engine")
    assert "self._account_alias_provider()" in source[build_start:]


# -- Guard E: the public surface is exactly the declared one -------------


def test_the_orchestrator_exposes_exactly_the_declared_surface() -> None:
    public = _public_members(_ORCHESTRATOR_PATH, "ShadowOrchestrator")
    assert public == set(PUBLIC_SURFACE), sorted(
        public ^ set(PUBLIC_SURFACE)
    )


def test_the_orchestrator_exposes_the_declared_signals() -> None:
    signals = _signal_members(_ORCHESTRATOR_PATH, "ShadowOrchestrator")
    assert signals == set(PUBLIC_SIGNALS), sorted(
        signals ^ set(PUBLIC_SIGNALS)
    )


def test_the_package_exports_only_the_capability() -> None:
    """``__init__`` is a stable entry point, not a re-export swamp."""

    namespace: dict[str, object] = {}
    exec(  # noqa: S102 - a tiny, self-contained source read
        (_SHADOW_DIR / "__init__.py").read_text(encoding="utf-8"),
        namespace,
    )
    assert sorted(namespace["__all__"]) == ["ShadowOrchestrator"]


# -- Guard F: no God object, and line budgets ----------------------------


def test_no_new_desktop_manager_or_context_was_introduced() -> None:
    """The spec forbids a new master object, and Shadow must not grow one."""

    for banned in (
        "DesktopManager",
        "ApplicationContext",
        "ShadowManager",
        "ShadowCoordinator",
        "ServiceRegistry",
        "DependencyContainer",
    ):
        for path in _python_files(_SHADOW_DIR):
            assert banned not in path.read_text(encoding="utf-8"), (
                f"{banned} in {path.name}"
            )


@pytest.mark.parametrize(("name", "budget"), sorted(LINE_BUDGETS.items()))
def test_each_shadow_file_stays_inside_its_budget(name: str, budget: int) -> None:
    lines = len((_SHADOW_DIR / name).read_text(encoding="utf-8").splitlines())
    assert lines <= budget, f"{name} is {lines} lines, budget {budget}"
