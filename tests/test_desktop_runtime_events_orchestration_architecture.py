"""Architecture guards for v2O-F1 Runtime Events orchestration.

Each guard names one property the round establishes, and most of them read the
source as an AST rather than as text: ``self.runtime_events_orchestrator``
*contains* ``self.runtime_events``, so a substring test would be either vacuous
or wrong, while "which attributes does this class touch on itself?" is exactly
the question ownership asks.

The properties, in the round's own numbering:

1. the window holds no ``RuntimeEventStore`` alias;
2. the window holds no refresh stamp, pending flag or last-export fact;
3. the window never calls ``add`` / ``resolve`` / ``list_recent`` on the store;
4. the window never renders the Runtime Events page and never builds its view;
5. the Runtime Events view has exactly one caller, and it is the orchestrator;
6. the orchestrator imports no other capability;
7. the Runtime Events page and table still never open the store;
8. ``SystemPage`` is still containment only, and knows nothing of this round;
9. the export provider is composition only, not a second orchestration;
10. Settings orchestration is still the window's (v2O-F2 has not started);
11. the Gateway probe's ownership is unchanged;
12. the orchestrator caches no event list;
13. the orchestrator does not import the generic task lifecycle;
14. no per-capability runtime-event adapter writes the store any more.
"""

from __future__ import annotations

import ast
import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP = _SRC / "desktop.py"

_ORCH_DIR = _SRC / "desktop_v2" / "orchestration" / "system"
_RUNTIME_DIR = _ORCH_DIR / "runtime_events"
_ORCHESTRATOR = _RUNTIME_DIR / "orchestrator.py"
_ORCH_MODELS = _RUNTIME_DIR / "models.py"
_ORCH_INIT = _RUNTIME_DIR / "__init__.py"
_SYSTEM_INIT = _ORCH_DIR / "__init__.py"

_PAGE_DIR = _SRC / "desktop_v2" / "pages" / "system" / "runtime_events"
_SYSTEM_PAGE = _SRC / "desktop_v2" / "pages" / "system" / "page.py"

_ORCH_PACKAGE = (
    _ORCH_INIT,
    _SYSTEM_INIT,
    _ORCH_MODELS,
    _ORCHESTRATOR,
)

#: The store method names no window may call.
_STORE_METHODS = {"add", "resolve", "list_recent"}

#: What the window may still read off itself about Runtime Events: the page it
#: composes and the orchestrator it composes.  Anything else starting with
#: ``runtime_event`` is a second owner by another name.
_ALLOWED_WINDOW_EVENT_ATTRS = {
    "runtime_events_page",
    "runtime_events_orchestrator",
}

#: The state this round retired, and the names a forwarding property would use.
_RETIRED_WINDOW_NAMES = (
    "_last_runtime_events_refresh",
    "_runtime_events_refresh_pending",
    "_last_runtime_export",
)

#: The state the orchestrator is allowed to hold.  An exact set on purpose:
#: adding a field here is a decision about who owns runtime-event truth, and it
#: should cost a guard update rather than pass unnoticed.
_ORCHESTRATOR_STATE = {
    "_store",
    "_page",
    "_environment",
    "_active_task_count",
    "_export_bundle",
    "_clock",
    "_last_export",
    "_last_refresh_at",
    "_refresh_pending",
    "_timer",
}

#: Nothing in this file may be cached as a second runtime-event truth.
_FORBIDDEN_ORCHESTRATOR_STATE = (
    "_events",
    "_recent_events",
    "_rows",
    "_event_rows",
    "_view",
    "_views",
    "_cache",
    "_pending_events",
)

#: The module prefixes the orchestrator may not import.
_FORBIDDEN_ORCH_IMPORTS = (
    "us_quant.desktop",
    "us_quant.desktop_workers",
    "us_quant.desktop_tasks",
    "us_quant.trading",
    "us_quant.shadow",
    "us_quant.export_service",
    "us_quant.paths",
    "us_quant.desktop_settings",
    "us_quant.desktop_credentials",
    "us_quant.credential_store",
    "us_quant.user_settings",
    "us_quant.desktop_v2.orchestration.account",
    "us_quant.desktop_v2.orchestration.market",
    "us_quant.desktop_v2.orchestration.paper",
    "us_quant.desktop_v2.orchestration.shadow",
    "us_quant.desktop_v2.orchestration.research",
    "us_quant.desktop_v2.pages.strategy",
    "us_quant.desktop_v2.pages.risk",
    "us_quant.desktop_v2.pages.execution",
    "sqlite3",
)

#: The names the orchestrator may not import, whatever module they came from.
_FORBIDDEN_ORCH_NAMES = {
    "MainWindow",
    "AccountOrchestrator",
    "MarketOrchestrator",
    "PaperOrchestrator",
    "ShadowOrchestrator",
    "StrategyApplication",
    "RiskApplication",
    "ExecutionApplication",
    "OrderRepository",
    "TaskThread",
    "DesktopTaskController",
    "RuntimeSupervisor",
    "export_terminal_bundle",
}

#: The forbidden god objects the round's brief names explicitly.
_GOD_OBJECTS = {
    "SystemOrchestrator",
    "SystemManager",
    "DesktopSystemManager",
    "SystemContext",
    "ApplicationContext",
    "ServiceBag",
    "RuntimeManager",
}

#: Settings orchestration: v2O-F1 left it on the window and v2O-F2 moved it to
#: ``orchestration/system/settings/``.  Both halves of the guard are kept: the
#: retired handlers must be gone, and the composition-only remainder must be
#: exactly what the composition root still needs.
_RETIRED_SETTINGS_WINDOW_METHODS = (
    "_preview_theme_changed",
    "_settings_provider_selected",
    "_stream_provider_selected",
    "_switch_to_settings_provider",
    "_api_provider_changed",
    "_save_api_credentials",
    "_clear_selected_api_credentials",
    "_clear_saved_finnhub_key",
    "_paper_order_capability_toggled",
    "_extended_hours_paper_toggled",
    "_save_user_preferences",
    "_publish_settings_view",
    "_settings_draft",
    "_settings_storage_view",
    "_set_connection_settings_enabled",
)

#: What the window still does for Settings after v2O-F2: composition only.
_SETTINGS_COMPOSITION_METHODS = (
    "_connect_settings_page",
    "_on_market_provider_selected",
    "_on_market_switch_requested",
    "_on_settings_committed",
    "_show_settings_information",
    "_show_settings_warning",
    "_confirm_paper_order_capability",
    "_confirm_extended_hours_paper",
)

#: The per-capability adapters v2O-F1 retired, plus the one it replaced.
_RETIRED_ROUTING_METHODS = (
    "_record_runtime_event",
    "_record_market_runtime_event",
    "_record_account_runtime_event",
    "_record_shadow_runtime_event",
    "_record_paper_runtime_event",
    "_record_targeted_evidence_runtime_event",
)


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


def _class_node(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _methods(node: ast.ClassDef) -> dict[str, ast.FunctionDef]:
    return {
        member.name: member
        for member in node.body
        if isinstance(member, ast.FunctionDef)
    }


def _self_attrs_used(tree: ast.Module) -> set[str]:
    """Every ``self.<name>`` the module reads, assigns or passes on."""

    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            names.add(node.attr)
    return names


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


def _attribute_calls(tree: ast.Module) -> list[tuple[str, str]]:
    """Every ``receiver.method(...)`` as ``(method, unparsed receiver)``."""

    calls: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            calls.append((node.func.attr, ast.unparse(node.func.value)))
    return calls


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


def _identifiers(tree: ast.Module) -> set[str]:
    """Every identifier the code actually mentions.

    Docstrings that *name* something they do not own ("the page holds no
    ``RuntimeEventStore``") are the house style, so an ownership guard has to
    read code rather than prose.
    """

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


def _desktop() -> ast.Module:
    return _tree(_DESKTOP)


def _main_window() -> ast.ClassDef:
    return _class_node(_desktop(), "MainWindow")


def _window():
    from PySide6.QtWidgets import QApplication

    from us_quant.desktop import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    app.processEvents()
    return window


class _RegisteredWorkerStub:
    """A duck-typed ``DesktopWorker`` the controller can register and release.

    Since G1 the worker collection has no window-held alias, so a test that
    wants a non-zero task count goes through the controller's own admission --
    which is also the only path production code can take.
    """

    resource_group = "research"

    def isRunning(self) -> bool:
        return True


# -- 1 / 2 / 3: no store, no sequencing state, no store call -------------


def test_main_window_holds_no_runtime_event_store_alias() -> None:
    tree = _desktop()
    used = _self_attrs_used(tree)
    assert "runtime_events" not in used, sorted(
        name for name in used if name.startswith("runtime_event")
    )
    assert "runtime_events" not in _self_attrs_assigned(tree)
    # Exactly one construction, and it is handed straight to the orchestrator.
    source = _DESKTOP.read_text(encoding="utf-8")
    assert source.count("RuntimeEventStore(") == 1
    assert "store=RuntimeEventStore(" in source


def test_main_window_holds_no_runtime_events_sequencing_state() -> None:
    tree = _desktop()
    assigned = _self_attrs_assigned(tree)
    used = _self_attrs_used(tree)
    source = _DESKTOP.read_text(encoding="utf-8")
    for name in _RETIRED_WINDOW_NAMES:
        assert name not in assigned, name
        assert name not in used, name
        assert f"self.{name}" not in source, name
        # No forwarding property either: a property is how a retired attribute
        # stays alive under a new name.
        assert f"def {name}(" not in source, name


def test_main_window_reads_only_the_page_and_the_orchestrator() -> None:
    names = {
        name
        for name in _self_attrs_used(_desktop())
        if name.startswith("runtime_event")
    }
    assert names == _ALLOWED_WINDOW_EVENT_ATTRS, sorted(names)


def test_main_window_never_calls_a_store_method() -> None:
    source = _DESKTOP.read_text(encoding="utf-8")
    assert "self.runtime_events." not in source
    assert ".list_recent(" not in source

    offenders = [
        (method, receiver)
        for method, receiver in _attribute_calls(_desktop())
        if method in _STORE_METHODS and "runtime_event" in receiver
    ]
    assert offenders == []


# -- 4 / 5: the window never paints the page ----------------------------


def test_main_window_never_renders_the_runtime_events_page() -> None:
    source = _DESKTOP.read_text(encoding="utf-8")
    for token in (
        "runtime_events_page.render(",
        "RuntimeEventsPageView",
        "build_runtime_events_view",
        "runtime_info_text",
    ):
        assert token not in source, token

    page_calls = {
        method
        for method, receiver in _attribute_calls(_desktop())
        if "runtime_events_page" in receiver
    }
    # ``set_palette`` is a repaint of already-drawn rows, not a view build.
    assert page_calls == {"set_palette"}, sorted(page_calls)


def test_the_orchestrator_is_the_only_caller_of_the_view_projection() -> None:
    orchestrator = _ORCHESTRATOR.read_text(encoding="utf-8")
    assert "self._page.render(" in orchestrator
    assert "build_runtime_events_view(" in orchestrator
    assert "runtime_info_text(" in orchestrator

    for path in _python_files(_SRC):
        text = path.read_text(encoding="utf-8")
        assert "runtime_events_page.render(" not in text, path
        if "build_runtime_events_view" in text:
            assert path in {
                _PAGE_DIR / "presenter.py",
                _ORCHESTRATOR,
            }, path
        if "runtime_info_text" in text:
            assert path in {
                _PAGE_DIR / "presenter.py",
                _ORCHESTRATOR,
            }, path


# -- 6 / 12 / 13: the orchestrator's dependency and state boundaries ------


def test_the_orchestrator_imports_no_other_capability() -> None:
    for path in _ORCH_PACKAGE:
        tree = _tree(path)
        modules = _imports(tree)
        for forbidden in _FORBIDDEN_ORCH_IMPORTS:
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in modules
            ), (path.name, sorted(modules))
        assert not (_imported_names(tree) & _FORBIDDEN_ORCH_NAMES), (
            path.name,
            sorted(_imported_names(tree)),
        )


def test_the_orchestrator_caches_no_event_list() -> None:
    orchestrator = _class_node(_tree(_ORCHESTRATOR), "RuntimeEventsOrchestrator")
    state = _self_attrs_assigned(orchestrator)
    assert state == _ORCHESTRATOR_STATE, sorted(state)
    for name in _FORBIDDEN_ORCHESTRATOR_STATE:
        assert name not in state, name

    build = _methods(orchestrator)["_build_view"]
    source = ast.unparse(build)
    assert "self._store.list_recent(" in source
    assert "self._active_task_count()" in source


def test_the_orchestrator_imports_no_generic_task_lifecycle() -> None:
    for path in _ORCH_PACKAGE:
        tree = _tree(path)
        identifiers = _identifiers(tree)
        for token in ("TaskThread", "DesktopTaskController", "workers"):
            assert token not in identifiers, (path.name, token)
        modules = _imports(tree)
        for forbidden in ("us_quant.desktop_workers", "us_quant.desktop_tasks"):
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in modules
            ), (path.name, sorted(modules))


def test_no_god_object_was_created_for_the_system_route() -> None:
    forbidden = _GOD_OBJECTS
    for path in _python_files(_SRC):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ClassDef):
                assert node.name not in forbidden, (path, node.name)
    # Two sibling capability packages and no aggregate owner above them: a
    # ``system/orchestrator.py`` would be the god object in a new costume, and
    # neither capability is allowed to own the other.
    assert (_ORCH_DIR / "runtime_events").is_dir()
    assert (_ORCH_DIR / "settings").is_dir()
    assert not (_ORCH_DIR / "orchestrator.py").exists()
    # Each sibling has its own owner and its own pure rules; neither grows the
    # other's module.
    assert (_ORCH_DIR / "runtime_events" / "orchestrator.py").is_file()
    assert (_ORCH_DIR / "settings" / "orchestrator.py").is_file()
    assert (_ORCH_DIR / "settings" / "queries.py").is_file()
    assert not (_ORCH_DIR / "settings" / "runtime_events.py").exists()
    assert not (_ORCH_DIR / "runtime_events" / "settings.py").exists()


# -- 7 / 8: the page and the aggregate are unchanged --------------------


def test_the_runtime_page_and_table_never_open_the_store() -> None:
    for path in _python_files(_PAGE_DIR):
        tree = _tree(path)
        modules = _imports(tree)
        # ``presenter.py`` names the event type under ``TYPE_CHECKING`` only:
        # a type annotation is not a handle, and the modules that could open a
        # database -- the page and the table -- import neither module at all.
        if path not in (_PAGE_DIR / "page.py", _PAGE_DIR / "table.py", _PAGE_DIR / "models.py"):
            continue
        for forbidden in ("us_quant.runtime_events", "sqlite3"):
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in modules
            ), (path.name, sorted(modules))
        assert not ({"RuntimeEventStore", "sqlite3"} & _imported_names(tree))

    for path in _python_files(_PAGE_DIR):
        assert "RuntimeEventStore" not in _identifiers(_tree(path)), path


def test_the_runtime_page_emits_exactly_its_three_intents() -> None:
    page = _class_node(_tree(_PAGE_DIR / "page.py"), "RuntimeEventsPage")
    signals = {
        target.id
        for member in page.body
        if isinstance(member, ast.Assign)
        for target in member.targets
        if isinstance(target, ast.Name)
        and isinstance(member.value, ast.Call)
        and ast.unparse(member.value.func) == "Signal"
    }
    assert signals == {
        "refresh_requested",
        "resolve_requested",
        "export_requested",
    }


def test_system_page_is_still_containment_only() -> None:
    system = _class_node(_tree(_SYSTEM_PAGE), "SystemPage")
    assert set(_methods(system)) == {
        "__init__",
        "set_active_workspace",
        "active_workspace",
    }
    source = _SYSTEM_PAGE.read_text(encoding="utf-8")
    assert "RuntimeEventsOrchestrator" not in source
    assert "runtime_events_orchestrator" not in source
    for path in _python_files(_SRC / "desktop_v2" / "pages" / "system"):
        text = path.read_text(encoding="utf-8")
        assert "orchestration.system" not in text, path
        assert "RuntimeEventsOrchestrator" not in text, path


# -- 9: the export provider is composition, not orchestration -----------


def test_the_export_provider_is_composition_only() -> None:
    """It gathers capabilities' facts; it sequences nothing."""

    method = _methods(_main_window())["_export_runtime_bundle"]
    body = "\n".join(ast.unparse(node) for node in _statements_without_docstring(method))

    assert "export_terminal_bundle(" in body
    for forbidden in (
        "runtime_events_orchestrator",
        "EXPORT_OK",
        "runtime_events_page",
        "QMessageBox",
        "last_export",
        "refresh",
        "record(",
        "resolve(",
        "list_recent",
        "RuntimeEventStore",
    ):
        assert forbidden not in body, forbidden

    # It reads the canonical capability facts, and it holds none of them.
    for required in (
        "self.account_orchestrator.portfolio",
        "self.market_orchestrator.snapshot",
        "self.strategies.list_versions()",
        "self.shadow_orchestrator.recent_fills(500)",
        "self.targeted_evidence_orchestrator.snapshot",
        "self.order_repository.audit_rows()",
        "self.order_repository.execution_rows()",
    ):
        assert required in body, required
    assert "self.targeted_results" not in body


# -- 10 / 11: Settings moved in F2, and the Gateway probe is untouched ---
#
# v2O-F1 asserted the settings half was *still* the window's, because that round
# was not allowed to touch it.  v2O-F2 moved it, so the assertion inverts rather
# than disappearing: the retired handlers must be absent and the composition
# remainder must be exactly the composition root's.  The stronger F2 guards --
# ownership, live reads, signal loops, the transaction -- live in
# ``tests/test_desktop_settings_orchestration_architecture.py``.


def test_settings_orchestration_has_left_the_window() -> None:
    methods = set(_methods(_main_window()))
    for name in _RETIRED_SETTINGS_WINDOW_METHODS:
        assert name not in methods, name
    for name in _SETTINGS_COMPOSITION_METHODS:
        assert name in methods, name

    classes: set[str] = set()
    for path in _python_files(_SRC):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ClassDef):
                classes.add(node.name)
    assert "SettingsOrchestrator" in classes
    assert "SettingsManager" not in classes
    assert "DesktopSettingsOrchestrator" not in classes


def test_the_gateway_probe_ownership_is_unchanged() -> None:
    methods = set(_methods(_main_window()))
    assert "_probe_gateway" in methods
    source = _DESKTOP.read_text(encoding="utf-8")
    assert "probe_ibkr_socket" in source
    assert "gateway_badge" in source

    for path in (_ORCHESTRATOR, _ORCH_MODELS):
        text = path.read_text(encoding="utf-8")
        assert "probe" not in text.lower(), path
        assert "gateway" not in text.lower(), path


# -- 14: one routing adapter, and it only forwards ----------------------


def test_no_capability_specific_runtime_event_adapter_remains() -> None:
    methods = _methods(_main_window())
    for name in _RETIRED_ROUTING_METHODS:
        assert name not in methods, name
    assert "_route_runtime_event" in methods


def test_the_routing_adapter_only_forwards_the_four_fields() -> None:
    method = _methods(_main_window())["_route_runtime_event"]
    statements = _statements_without_docstring(method)
    assert len(statements) == 1, ast.unparse(method)
    assert ast.unparse(statements[0]) == (
        "self.runtime_events_orchestrator.record("
        "severity=event.severity, component=event.component, "
        "code=event.code, message=event.message)"
    )


def test_every_capability_signal_reaches_the_one_adapter() -> None:
    """Five capabilities, one adapter -- checked on the wiring itself."""

    source = _DESKTOP.read_text(encoding="utf-8")
    assert source.count("runtime_event_requested.connect(") == 5
    assert source.count("self._route_runtime_event") == 5
    for name in _RETIRED_ROUTING_METHODS:
        assert name not in source, name


# -- the real window, for the guards that need one ----------------------


def test_a_real_window_holds_no_runtime_events_state_of_its_own() -> None:
    window = _window()
    try:
        assert hasattr(window, "runtime_events_page")
        assert hasattr(window, "runtime_events_orchestrator")
        for name in (
            "runtime_events",
            *(_RETIRED_WINDOW_NAMES),
            "_runtime_info_text",
            "_refresh_runtime_events",
            "_resolve_runtime_event",
            "_export_terminal_state",
            "_record_runtime_event",
        ):
            assert not hasattr(window, name), name
        for name in _RETIRED_ROUTING_METHODS:
            assert not hasattr(window, name), name
        assert window.runtime_events_orchestrator.last_export is None
    finally:
        window.close()
        window.deleteLater()


def test_the_window_still_composes_the_orchestrators_dependencies() -> None:
    """The composition root's remaining job, asserted rather than described."""

    window = _window()
    try:
        orchestrator = window.runtime_events_orchestrator
        assert orchestrator._page is window.runtime_events_page
        assert orchestrator._export_bundle == window._export_runtime_bundle
        # The count comes from the controller (G1), not from a window-held
        # worker list.
        assert (
            orchestrator._active_task_count()
            == window.task_controller.active_count
        )
        assert orchestrator._environment.exports_root == window.paths.exports_root
        # A provider, not a value: the count follows the controller's
        # collection, which is the only place a worker is registered or
        # released.  The stub is released in its own finally: a registered
        # worker left behind would make the window's close see a running task
        # and block the suite on a dialog no offscreen run can answer.
        stub = _RegisteredWorkerStub()
        try:
            window.task_controller.register(stub)
            assert orchestrator._active_task_count() == 1
            assert window.task_controller.finish(stub) is True
            assert orchestrator._active_task_count() == 0
        finally:
            window.task_controller.finish(stub)
    finally:
        window.close()
        window.deleteLater()
