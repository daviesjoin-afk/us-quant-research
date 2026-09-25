"""Architecture guards for MainWindow Composition Closure G1.

The round's subject is *generic runtime and shell ownership*: who owns the
background-worker collection, who owns the shutdown admission fact, what the
close path may look at, where the Dashboard render lives, and what the Gateway
probe is.  Most guards read the source as an AST rather than as text, for the
same reason the F1/F2 guards do: ``self.task_controller`` *contains* the word
``task``, and "which attributes does this class touch on itself?" is the
question ownership actually asks.

The numbered properties, matching the round brief:

1. the window holds no mutable ``self.workers`` alias;
2. the task controller's internal collection is not modified from outside;
3. the Runtime Events task-count provider reads the controller;
4. ``_closing`` / ``_close_admission_gate`` / the ``closing_gate`` component
   are gone;
5. ``_start_task``'s admission truth is ``RuntimeSupervisor.shutting_down``;
6. the shutdown-essential exemption still exists and is still narrow;
7. a refused close lowers the gate through ``cancel_shutdown`` only;
8. ``closeEvent`` reads no Paper phase and no Paper internal;
9. ``closeEvent`` touches no lease and no Paper trading service release;
10. ``closeEvent``'s only Paper question is ``prepare_shutdown``;
11. the Gateway probe appears in no capability package;
12. no ``GatewayOrchestrator`` exists;
13. ``_probe_gateway`` stores no second gateway fact;
14. ``DashboardPage.render`` has exactly one caller, and it is not the window;
15. the Dashboard owner imports no other orchestrator;
16. the Runtime Events (F1) ownership has not regressed;
17. the Settings (F2) ownership has not regressed;
18. no aggregate System orchestrator exists;
19. none of the forbidden god-object names exists;
20. cross-capability bridges reach no private member of another capability.
"""

from __future__ import annotations

import ast
import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP = _SRC / "desktop.py"
_TASKS = _SRC / "desktop_tasks.py"
_SUPERVISOR = _SRC / "runtime_supervisor.py"

_ORCH_DIR = _SRC / "desktop_v2" / "orchestration"
_DASHBOARD_ORCH = _ORCH_DIR / "dashboard"
_SETTINGS_DIR = _ORCH_DIR / "system" / "settings"
_RUNTIME_EVENTS_DIR = _ORCH_DIR / "system" / "runtime_events"
_PAGES_DIR = _SRC / "desktop_v2" / "pages"

#: The window methods that are composition seams for the generic runtime.
_RUNTIME_METHODS = (
    "_register_runtime_components",
    "_cancel_close_drain",
    "_request_worker_stops",
    "_join_background_workers",
    "_start_task",
    "closeEvent",
    "_report_runtime_shutdown",
)

#: The only member of the task controller the window may name.
_CONTROLLER_SURFACE = {
    "task_controller",
    "can_start",
    "register",
    "finish",
    "active_count",
    "running_workers",
    "has_running_workers",
}

#: Components the supervisor may be handed: generic lifecycle only.
_ALLOWED_COMPONENTS = {
    "paper_order_heartbeat",
    "extended_session_heartbeat",
    "stream_snapshot_timer",
    "market_data_stream",
    "background_workers",
}

_GOD_OBJECTS = {
    "DesktopManager",
    "DesktopCoordinator",
    "ApplicationContext",
    "DesktopContext",
    "WorkbenchManager",
    "GlobalController",
    "RuntimeManager",
    "ShellManager",
    "ServiceBag",
    "CompositionService",
    "MainWindowController",
    "GatewayOrchestrator",
    "SystemOrchestrator",
    "SystemManager",
    "SettingsManager",
    "ShutdownCoordinator",
    "CrossCapabilityCoordinator",
    "EventBus",
    "Mediator",
    "GlobalStore",
    "RuntimeShutdownPresenter",
}

#: What ``closeEvent`` may touch on the Paper capability: the verdict, and
#: nothing else.  The capability owns *whether* a close is safe.
_CLOSE_PAPER_SURFACE = {"prepare_shutdown", "manual_recovery_required"}


# -- helpers -------------------------------------------------------------


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _python_files(root: pathlib.Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _imports(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def _identifiers(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _class_node(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _methods(node: ast.ClassDef) -> dict[str, ast.FunctionDef]:
    return {
        member.name: member
        for member in node.body
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _self_attrs_assigned(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
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


def _statements_without_docstring(node: ast.FunctionDef) -> list[ast.stmt]:
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body


def _method_source(tree: ast.Module, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(
                ast.unparse(statement)
                for statement in _statements_without_docstring(node)
            )
    raise AssertionError(f"method {name} not found")


def _main_window() -> ast.ClassDef:
    return _class_node(_tree(_DESKTOP), "MainWindow")


def _window_methods() -> dict[str, ast.FunctionDef]:
    return _methods(_main_window())


# -- 1 / 2 / 3: the worker collection has one owner ----------------------


def test_the_window_holds_no_worker_collection_alias() -> None:
    tree = _tree(_DESKTOP)
    assigned = _self_attrs_assigned(tree)
    assert "workers" not in assigned, "the window owns a worker list again"

    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    assert "workers" not in used, "the window still reads a worker list"

    controller = _class_node(_tree(_TASKS), "DesktopTaskController")
    members = {
        member.name
        for member in controller.body
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    # The compatibility property that handed out the mutable list is retired:
    # a caller who wants to know what is running asks the narrow API.
    assert "workers" not in members


def test_the_worker_collection_is_only_mutated_by_the_controller() -> None:
    """No append/remove on the collection from outside the controller."""

    tree = _tree(_DESKTOP)
    offending: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "self"
            and owner.attr == "task_controller"
        ):
            assert node.attr in _CONTROLLER_SURFACE, node.attr
        if node.attr in {"_workers"}:
            receiver = ast.unparse(node.value)
            if receiver != "self":
                offending.append(receiver)
    assert offending == [], offending

    # And the controller itself is still the only mutator of its list.
    controller_tree = _tree(_TASKS)
    for node in ast.walk(controller_tree):
        if isinstance(node, ast.Attribute) and node.attr == "_workers":
            receiver = node.value
            assert (
                isinstance(receiver, ast.Name) and receiver.id == "self"
            ), ast.unparse(node)


def test_the_runtime_events_count_provider_reads_the_controller() -> None:
    source = _DESKTOP.read_text(encoding="utf-8")
    assert "active_task_count=lambda: self.task_controller.active_count" in source
    assert "active_task_count=lambda: len(" not in source


def test_the_controller_reports_running_workers_as_immutable_values() -> None:
    controller = _class_node(_tree(_TASKS), "DesktopTaskController")
    methods = _methods(controller)
    running = methods["running_workers"]
    returns = [
        node
        for node in ast.walk(running)
        if isinstance(node, ast.Return) and node.value is not None
    ]
    assert returns, "running_workers must return something"
    # A fresh tuple built from a comprehension -- never the internal list
    # object itself.
    assert all(
        isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "tuple"
        for node in returns
    ), [ast.unparse(node) for node in returns]
    assert "has_running_workers" in methods


# -- 4 / 5 / 6 / 7: one shutdown admission truth -------------------------


def test_the_window_holds_no_second_shutdown_flag() -> None:
    assigned = _self_attrs_assigned(_tree(_DESKTOP))
    for name in ("_closing", "_shutting_down", "_close_admission_gate"):
        assert name not in assigned, name

    methods = _window_methods()
    assert "_close_admission_gate" not in methods

    supervisor = _class_node(_tree(_SUPERVISOR), "RuntimeSupervisor")
    source = _SUPERVISOR.read_text(encoding="utf-8")
    # The supervisor itself owns the fact and its reversibility.
    assert "def shutting_down" in source or "shutting_down" in source
    assert "def cancel_shutdown" in source
    assert "_release_entered" in source

    registered = {
        node.func.value.id if isinstance(node.func.value, ast.Name) else None
        for node in ast.walk(_tree(_DESKTOP))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "register"
    }
    assert "supervisor" in registered, "the registration moved"


def test_start_task_reads_the_supervisors_admission_fact() -> None:
    body = _method_source(_tree(_DESKTOP), "_start_task")
    assert "self.runtime_supervisor.shutting_down" in body
    assert "self._closing" not in body
    assert "shutdown_essential" in body
    # The exemption is still there, still on the right side of the gate.
    assert "not shutdown_essential" in body


def test_a_refused_close_lowers_the_gate_through_cancel_shutdown_only() -> None:
    body = _method_source(_tree(_DESKTOP), "_cancel_close_drain")
    assert "cancel_shutdown()" in body
    assert "self._closing" not in body
    # It is a bookkeeping undo: no restart, no I/O.
    for forbidden in ("start(", "open(", "connect(", "disconnect("):
        assert forbidden not in body, forbidden


def test_the_registration_is_generic_lifecycle_only() -> None:
    tree = _tree(_DESKTOP)
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "supervisor"
        ):
            if node.args and isinstance(node.args[0], ast.Constant):
                names.add(str(node.args[0].value))
    assert names == _ALLOWED_COMPONENTS, sorted(names ^ _ALLOWED_COMPONENTS)

    body = _method_source(_tree(_DESKTOP), "_register_runtime_components")
    for forbidden in (
        "PaperWorkflowPhase",
        "release_lease",
        "reconcil",
        "record(",
        "render",
        "_publish",
    ):
        assert forbidden not in body, forbidden


# -- 8 / 9 / 10: the close path is composition only ----------------------


def _close_event_identifiers() -> set[str]:
    return _identifiers_of_method("closeEvent")


def _identifiers_of_method(name: str) -> set[str]:
    method = _window_methods()[name]
    names: set[str] = set()
    for node in ast.walk(method):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_close_event_reads_no_paper_internal() -> None:
    identifiers = _close_event_identifiers()
    for token in (
        "PaperWorkflowPhase",
        "phase",
        "result",
        "lease",
        "ExecutionLease",
        "PaperTradingService",
        "release",
        "disconnect",
        "paper_workflow",
        "paper_trading",
        "_finalization_inflight",
    ):
        assert token not in identifiers, token


def test_close_event_asks_the_capability_one_question() -> None:
    """The safe-shutdown verdict has exactly one owner and one call site."""

    tree = _tree(_DESKTOP)
    method = _window_methods()["closeEvent"]
    paper_calls: set[str] = set()
    for node in ast.walk(method):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr == "paper_orchestrator"
        ):
            paper_calls.add(node.attr)
    assert paper_calls == {"prepare_shutdown"}, sorted(paper_calls)

    # And the verdict the window reads is the capability's own API.
    orchestrator = _ORCH_DIR / "paper" / "orchestrator.py"
    assert "def prepare_shutdown" in orchestrator.read_text(encoding="utf-8")


def test_close_event_uses_public_runtime_and_market_facts() -> None:
    method = _window_methods()["closeEvent"]
    supervisor_attrs: set[str] = set()
    market_attrs: set[str] = set()
    for node in ast.walk(method):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
        ):
            if node.value.attr == "runtime_supervisor":
                supervisor_attrs.add(node.attr)
            if node.value.attr == "market_orchestrator":
                market_attrs.add(node.attr)
    assert supervisor_attrs <= {
        "begin_shutdown",
        "shutdown",
        "errors",
        "snapshot",
        "shutting_down",
        "cancel_shutdown",
    }, sorted(supervisor_attrs)
    assert market_attrs <= {"worker_running"}, sorted(market_attrs)


# -- 11 / 12 / 13: the Gateway probe is a shell diagnostic ---------------


def test_the_gateway_probe_is_in_no_capability_package() -> None:
    for package in (_SETTINGS_DIR, _RUNTIME_EVENTS_DIR, _DASHBOARD_ORCH):
        for path in _python_files(package):
            text = path.read_text(encoding="utf-8").lower()
            assert "probe_ibkr_socket" not in text, path
            assert "gateway_badge" not in text, path


def test_no_gateway_orchestrator_exists() -> None:
    for path in _python_files(_SRC):
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert node.name != "GatewayOrchestrator", path
    assert not (_ORCH_DIR / "gateway").exists()


def test_the_gateway_probe_is_a_pure_shell_diagnostic() -> None:
    body = _method_source(_tree(_DESKTOP), "_probe_gateway")
    # Reads the global config, asks the socket, paints the badge: nothing else.
    assert "probe_ibkr_socket(self.config.ibkr)" in body
    assert "self.gateway_badge" in body
    for forbidden in (
        "self._gateway",
        "connect(",
        "start(",
        "broker_account",
        "settings_orchestrator",
        "record(",
        "_worker",
    ):
        assert forbidden not in body, forbidden


# -- 14 / 15: the Dashboard render has one owner -------------------------


def test_the_window_never_renders_the_dashboard_page() -> None:
    calls = [
        node
        for node in ast.walk(_tree(_DESKTOP))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "render"
        and "dashboard_page" in ast.unparse(node.func.value)
    ]
    assert calls == [], [ast.unparse(call) for call in calls]


def test_the_dashboard_render_caller_is_the_capability() -> None:
    orchestrator = _DASHBOARD_ORCH / "orchestrator.py"
    source = orchestrator.read_text(encoding="utf-8")
    assert "self._page.render(" in source
    # And it is the only caller in the whole source tree.
    for path in _python_files(_SRC):
        if path == orchestrator:
            continue
        assert "dashboard_page.render(" not in path.read_text(
            encoding="utf-8"
        ), path


def test_the_dashboard_owner_imports_no_other_capability() -> None:
    for path in _python_files(_DASHBOARD_ORCH):
        modules = _imports(_tree(path))
        for module in modules:
            if module.startswith("us_quant.desktop_v2.orchestration"):
                # The only allowed intra-orchestration import is its own package.
                assert module.startswith(
                    "us_quant.desktop_v2.orchestration.dashboard"
                ), (path.name, module)
        assert not any(
            module == "PySide6" or module.startswith("PySide6.")
            for module in modules
        ), path.name
        names = _imported_names(_tree(path))
        for forbidden in (
            "AccountOrchestrator",
            "MarketOrchestrator",
            "PaperOrchestrator",
            "ShadowOrchestrator",
            "SettingsOrchestrator",
            "RuntimeEventsOrchestrator",
            "ResearchOrchestrator",
            "ScannerOrchestrator",
        ):
            assert forbidden not in names, (path.name, forbidden)


def test_the_dashboard_owner_caches_only_its_own_fact() -> None:
    orchestrator = _class_node(
        _tree(_DASHBOARD_ORCH / "orchestrator.py"), "DashboardOrchestrator"
    )
    state = {
        target.attr
        for node in ast.walk(orchestrator)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            [node.target]
            if isinstance(node, ast.AnnAssign)
            else list(node.targets)
        )
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    }
    assert state == {"_page", "_providers", "_chart"}, sorted(state)
    # The four facts are read at paint time, not cached.
    view = ast.unparse(_methods(orchestrator)["build_view"])
    for provider in ("portfolio", "snapshot", "artifacts", "market_stop_reason"):
        assert provider + "()" in view, provider


# -- 16 / 17 / 18 / 19 / 20: neighbours, aggregates, bridges -------------


def test_the_runtime_events_ownership_has_not_regressed() -> None:
    tree = _tree(_DESKTOP)
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    assert "runtime_events" not in used
    assert "runtime_events_orchestrator" in used
    source = _DESKTOP.read_text(encoding="utf-8")
    assert source.count("RuntimeEventStore(") == 1
    assert "runtime_events_page.render(" not in source


def test_the_settings_ownership_has_not_regressed() -> None:
    tree = _tree(_DESKTOP)
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    for name in (
        "_settings_api_provider",
        "_connection_settings_enabled",
        "_publish_settings_view",
        "_settings_draft",
    ):
        assert name not in used, name
    assert "settings_page.render(" not in _DESKTOP.read_text(encoding="utf-8")
    assert (_SETTINGS_DIR / "orchestrator.py").is_file()
    assert not (_ORCH_DIR / "system" / "orchestrator.py").exists()


def test_no_aggregate_or_god_object_was_created() -> None:
    for path in _python_files(_SRC):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ClassDef):
                assert node.name not in _GOD_OBJECTS, (path, node.name)
    assert not (_ORCH_DIR / "orchestrator.py").exists()


def test_bridges_reach_no_private_member_of_another_capability() -> None:
    """``self.<orchestrator>._<attr>`` is a reach-through, not a bridge."""

    offending: list[tuple[str, str]] = []
    for node in ast.walk(_tree(_DESKTOP)):
        if (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("_")
            and not node.attr.startswith("__")
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr.endswith("_orchestrator")
        ):
            offending.append((node.value.attr, node.attr))
    # Tests may reach privates; production composition may not.
    assert offending == [], sorted(set(offending))


def test_the_runtime_composition_methods_still_exist() -> None:
    methods = set(_window_methods())
    for name in _RUNTIME_METHODS:
        assert name in methods, name


def test_shutdown_error_reporting_is_presentation_only() -> None:
    body = _method_source(_tree(_DESKTOP), "_report_runtime_shutdown")
    for forbidden in (
        "self.task_controller",
        "_stop_market_data",
        "prepare_shutdown",
        "lease",
        "retry",
        "runtime_supervisor.shutdown",
        "RuntimeShutdownPresenter",
    ):
        assert forbidden not in body, forbidden
    # The aggregation itself is the pure query, not a second presenter class.
    assert "runtime_shutdown_messages(" in body
    for path in _python_files(_SRC):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ClassDef):
                assert node.name != "RuntimeShutdownPresenter", path


# -- the real window -----------------------------------------------------


def _window():
    from PySide6.QtWidgets import QApplication

    from us_quant.desktop import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    app.processEvents()
    return window


def test_a_real_window_holds_only_the_surviving_runtime_facts() -> None:
    window = _window()
    try:
        assert hasattr(window, "task_controller")
        assert hasattr(window, "runtime_supervisor")
        assert hasattr(window, "dashboard_orchestrator")
        for name in (
            "workers",
            "_closing",
            "_close_admission_gate",
            "_running_workers",
            "_publish_dashboard_view",
            "_dashboard_chart_view",
        ):
            assert not hasattr(window, name), name
        assert window.runtime_supervisor.shutting_down is False
        assert window.task_controller.active_count == 0
        assert window.task_controller.running_workers() == ()
    finally:
        window.close()
        window.deleteLater()
