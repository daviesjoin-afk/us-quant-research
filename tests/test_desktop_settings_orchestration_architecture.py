"""Architecture guards for v2O-F2 Settings orchestration.

Most of these read the source as an AST rather than as text, for the reason the
F1 guards do: ``self.settings_orchestrator`` *contains* ``self.settings``, and a
substring test would be either vacuous or wrong, while "which attributes does
this class touch on itself?" is exactly the question ownership asks.

The properties, in the round's own numbering:

1. the window holds no ``_settings_api_provider`` and no
   ``_connection_settings_enabled``;
2. the window never renders the Settings page;
3. the Settings render has exactly one orchestration caller: the orchestrator;
4. the window never calls the credential service or ``settings_service.commit``;
5. the orchestrator imports no other capability;
6. the Settings page package stays service-free;
7. the orchestrator caches no application configuration or other capability's
   state;
8. ``connection_settings_enabled`` has one Settings owner;
9. the selected API provider has one owner;
10. the live market source is read through a provider, never cached;
11. programmatic provider syncs are silent;
12. theme application does not enter Settings;
13. the market switch interlocks do not enter Settings;
14. ``SystemPage`` is still containment/navigation only;
15. the Runtime Events (v2O-F1) ownership has not regressed;
16. the Gateway probe's ownership is unchanged;
17. no ``SystemOrchestrator`` / ``SettingsManager`` / ``ApplicationContext``;
18. the dead ``_clear_saved_finnhub_key`` handler is gone and has no shim.
"""

from __future__ import annotations

import ast
import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP = _SRC / "desktop.py"

_ORCH_DIR = _SRC / "desktop_v2" / "orchestration" / "system"
_SETTINGS_DIR = _ORCH_DIR / "settings"
_ORCHESTRATOR = _SETTINGS_DIR / "orchestrator.py"
_QUERIES = _SETTINGS_DIR / "queries.py"
_MODELS = _SETTINGS_DIR / "models.py"
_INIT = _SETTINGS_DIR / "__init__.py"

_PAGE_DIR = _SRC / "desktop_v2" / "pages" / "system" / "settings"
_PAGE = _PAGE_DIR / "page.py"
_SYSTEM_PAGE = _SRC / "desktop_v2" / "pages" / "system" / "page.py"

_SETTINGS_PACKAGE = (_INIT, _MODELS, _QUERIES, _ORCHESTRATOR)

#: The store and service calls the window must not make any more.
_FORBIDDEN_WINDOW_CALLS = (
    ("credential_service", "status"),
    ("credential_service", "save_provider"),
    ("credential_service", "clear_provider"),
    ("settings_service", "commit"),
)

#: What the orchestrator may import.  ``us_quant.trading.ports`` is allowed and
#: nothing else under ``us_quant.trading`` is: the transaction's declared
#: failures come from ports, and no implementation may be named here.
_FORBIDDEN_ORCH_IMPORTS = (
    "us_quant.desktop",
    "us_quant.desktop_workers",
    "us_quant.desktop_tasks",
    "us_quant.shadow",
    "us_quant.market_data_service",
    "us_quant.desktop_v2.orchestration.account",
    "us_quant.desktop_v2.orchestration.market",
    "us_quant.desktop_v2.orchestration.paper",
    "us_quant.desktop_v2.orchestration.shadow",
    "us_quant.desktop_v2.orchestration.research",
    "us_quant.desktop_v2.orchestration.system.runtime_events",
    "us_quant.desktop_v2.pages.market",
    "us_quant.desktop_v2.pages.execution",
    "us_quant.desktop_v2.pages.risk",
    "us_quant.desktop_v2.pages.strategy",
    "us_quant.trading.application",
    "us_quant.trading.adapters",
    "us_quant.trading.runtime",
    "us_quant.trading.domain",
    "sqlite3",
)

_FORBIDDEN_ORCH_NAMES = {
    "MainWindow",
    "MarketOrchestrator",
    "AccountOrchestrator",
    "PaperOrchestrator",
    "ShadowOrchestrator",
    "ScannerOrchestrator",
    "BacktestOrchestrator",
    "CrossSectionOrchestrator",
    "UniverseOrchestrator",
    "HistoryOrchestrator",
    "TargetedSessionOrchestrator",
    "TargetedEvidenceOrchestrator",
    "RiskApplication",
    "ExecutionApplication",
    "StrategyApplication",
    "BrokerAccountApplication",
    "PaperTradingService",
    "OrderRepository",
    "TaskThread",
    "DesktopTaskController",
    "RuntimeEventsOrchestrator",
    "BrokerAccountPort",
}

#: The state the orchestrator is allowed to hold.  An exact set on purpose:
#: adding a field here is a decision about who owns settings truth, and it
#: should cost a guard update rather than pass unnoticed.
_ORCHESTRATOR_STATE = {
    "_page",
    "_settings_service",
    "_credential_service",
    "_current_config",
    "_broker_config",
    "_runtime_guards",
    "_active_market_source_id",
    "_selected_api_provider",
    "_connection_settings_enabled",
}

#: Nothing that would be a second copy of application state.
_FORBIDDEN_ORCHESTRATOR_STATE = (
    "_config",
    "_preferences",
    "_preferences_store",
    "_market",
    "_snapshot",
    "_portfolio",
    "_paper",
    "_events",
    "_rows",
    "_view",
    "_cache",
    "_active_source",
    "_active_source_id",
    "_status",
    "_credentials",
)

#: The public surface, and nothing else.
_ORCHESTRATOR_API = {
    "selected_api_provider",
    "connection_settings_enabled",
    "render_current",
    "select_api_provider",
    "select_market_provider",
    "adopt_market_provider",
    "set_connection_settings_enabled",
    "preview_theme",
    "save_credentials",
    "clear_credentials",
    "save_preferences",
    "request_provider_switch",
    "request_paper_order_capability_toggle",
    "confirm_paper_order_capability",
    "request_extended_hours_toggle",
    "confirm_extended_hours",
}

#: The page's intent surface, which this round did not change.
_PAGE_SIGNALS = {
    "theme_preview_requested",
    "market_provider_selected",
    "switch_provider_requested",
    "api_provider_selected",
    "save_credentials_requested",
    "clear_credentials_requested",
    "paper_order_capability_toggled",
    "extended_hours_paper_toggled",
    "save_preferences_requested",
}

#: Methods retired from the window by this round.
_RETIRED_WINDOW_METHODS = (
    "_settings_api_provider",
    "_connection_settings_enabled",
    "_settings_draft",
    "_settings_storage_view",
    "_publish_settings_view",
    "_credential_status_text",
    "_settings_provider_selected",
    "_stream_provider_selected",
    "_switch_to_settings_provider",
    "_api_provider_changed",
    "_set_connection_settings_enabled",
    "_save_user_preferences",
    "_save_api_credentials",
    "_clear_selected_api_credentials",
    "_clear_saved_finnhub_key",
    "_preview_theme_changed",
    "_paper_order_capability_toggled",
    "_extended_hours_paper_toggled",
)

#: Settings files the page may never import, and the names it may not mention.
_FORBIDDEN_PAGE_IMPORTS = (
    "us_quant.desktop_settings",
    "us_quant.desktop_credentials",
    "us_quant.credential_store",
    "us_quant.user_settings",
    "us_quant.market_data_service",
    "us_quant.shadow",
    "us_quant.desktop_v2.orchestration",
)
_FORBIDDEN_PAGE_NAMES = {
    "DesktopSettingsService",
    "DesktopCredentialService",
    "CredentialStore",
    "UserPreferences",
    "UserPreferencesStore",
    "MarketOrchestrator",
    "BrokerAccountApplication",
    "MarketDataApplication",
    "PaperTradingService",
    "RiskApplication",
    "ExecutionApplication",
    "SettingsOrchestrator",
}

_GOD_OBJECTS = {
    "SystemOrchestrator",
    "SystemManager",
    "DesktopSystemManager",
    "SystemContext",
    "SettingsManager",
    "DesktopManager",
    "ApplicationContext",
    "ServiceBag",
    "GlobalController",
}


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


def _self_attrs_used(tree: ast.Module) -> set[str]:
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


# -- 1 / 8 / 9: the presentation facts have one owner --------------------


def test_the_window_holds_no_settings_presentation_state() -> None:
    tree = _desktop()
    used = _self_attrs_used(tree)
    assigned = _self_attrs_assigned(tree)
    for name in ("_settings_api_provider", "_connection_settings_enabled"):
        assert name not in assigned, name
        assert name not in used, name
        assert f"def {name}(" not in _DESKTOP.read_text(encoding="utf-8")
    # The window may name the orchestrator and the page, and nothing else that
    # starts with ``settings_``.
    allowed = {"settings_page", "settings_orchestrator", "settings_service"}
    named = {name for name in used if name.startswith("settings")}
    assert named <= allowed, sorted(named - allowed)


def test_both_presentation_facts_have_exactly_one_owner() -> None:
    orchestrator = _class_node(_tree(_ORCHESTRATOR), "SettingsOrchestrator")
    state = _self_attrs_assigned(orchestrator)
    assert "_selected_api_provider" in state
    assert "_connection_settings_enabled" in state

    # And no other production module assigns them.
    for path in _python_files(_SRC):
        if path in _SETTINGS_PACKAGE:
            continue
        assigned = _self_attrs_assigned(_tree(path))
        for name in ("_selected_api_provider", "_connection_settings_enabled"):
            assert name not in assigned, (path, name)


# -- 2 / 3 / 4: one render caller, and no window-side service calls ------


def test_the_window_never_renders_the_settings_page() -> None:
    tree = _desktop()
    source = _DESKTOP.read_text(encoding="utf-8")
    assert "settings_page.render(" not in source
    # Read as identifiers, not prose: the window's own comments are allowed to
    # *name* the view it no longer builds.
    assert "SettingsPageView" not in _identifiers(tree)

    renders = [
        receiver
        for method, receiver in _attribute_calls(tree)
        if method == "render" and "settings_page" in receiver
    ]
    assert renders == []


def test_the_orchestrator_is_the_only_caller_of_the_settings_render() -> None:
    orchestrator = _ORCHESTRATOR.read_text(encoding="utf-8")
    assert "self._page.render(" in orchestrator

    for path in _python_files(_SRC):
        assert "settings_page.render(" not in path.read_text(
            encoding="utf-8"
        ), path
        if "SettingsPageView" in _identifiers(_tree(path)):
            # The view is defined by the page's models, imported by the page,
            # and constructed by exactly one orchestration caller.
            assert path in {
                _PAGE_DIR / "models.py",
                _PAGE_DIR / "page.py",
                _ORCHESTRATOR,
            }, path


def test_the_window_calls_no_settings_service() -> None:
    """The credential service and the transaction are the capability's."""

    calls = _attribute_calls(_desktop())
    for receiver_fragment, method in _FORBIDDEN_WINDOW_CALLS:
        offenders = [
            (method_name, receiver)
            for method_name, receiver in calls
            if method_name == method and receiver_fragment in receiver
        ]
        assert offenders == [], (receiver_fragment, method, offenders)

    source = _DESKTOP.read_text(encoding="utf-8")
    assert "settings_service.commit(" not in source
    assert "credential_service.status(" not in source
    assert "credential_service.save_provider(" not in source
    assert "credential_service.clear_provider(" not in source


def _caught_names(handler: ast.ExceptHandler) -> set[str]:
    """Every exception name one handler catches, tuple form included."""

    node = handler.type
    if node is None:
        raise AssertionError("a bare ``except`` was found")
    if isinstance(node, ast.Tuple):
        return {ast.unparse(element) for element in node.elts}
    return {ast.unparse(node)}


def test_the_commit_adapter_catches_only_the_declared_failures() -> None:
    orchestrator = _class_node(_tree(_ORCHESTRATOR), "SettingsOrchestrator")
    commit = _methods(orchestrator)["_commit"]
    handlers = [
        node
        for node in ast.walk(commit)
        if isinstance(node, ast.ExceptHandler)
    ]
    assert len(handlers) == 1
    caught: set[str] = set()
    for handler in handlers:
        caught |= _caught_names(handler)
    assert caught == {
        "UserSettingsError",
        "MarketDataActiveError",
        "BrokerAccountError",
    }, caught
    # No bare ``except`` anywhere in the package: only the transaction's
    # declared failures may be reported as "not saved".
    for path in _SETTINGS_PACKAGE:
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ExceptHandler):
                assert node.type is not None, path


# -- 5 / 6 / 7: the dependency and state boundaries ----------------------


def test_the_orchestrator_imports_no_other_capability() -> None:
    for path in _SETTINGS_PACKAGE:
        tree = _tree(path)
        modules = _imports(tree)
        for forbidden in _FORBIDDEN_ORCH_IMPORTS:
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in modules
            ), (path.name, sorted(modules))
        assert not (_imported_names(tree) & _FORBIDDEN_ORCH_NAMES), (
            path.name,
            sorted(_imported_names(tree) & _FORBIDDEN_ORCH_NAMES),
        )
        # The only allowed ``us_quant.trading`` import is the ports contract.
        for module in modules:
            if module.startswith("us_quant.trading"):
                assert module.startswith("us_quant.trading.ports"), module


def test_the_settings_page_package_stays_service_free() -> None:
    for path in _python_files(_PAGE_DIR):
        tree = _tree(path)
        modules = _imports(tree)
        for forbidden in _FORBIDDEN_PAGE_IMPORTS:
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in modules
            ), (path.name, sorted(modules))
        assert not (_imported_names(tree) & _FORBIDDEN_PAGE_NAMES), (
            path.name,
            sorted(_imported_names(tree) & _FORBIDDEN_PAGE_NAMES),
        )


def test_the_orchestrator_caches_no_application_state() -> None:
    orchestrator = _class_node(_tree(_ORCHESTRATOR), "SettingsOrchestrator")
    state = _self_attrs_assigned(orchestrator)
    assert state == _ORCHESTRATOR_STATE, sorted(state)
    for name in _FORBIDDEN_ORCHESTRATOR_STATE:
        assert name not in state, name

    # The config, preferences and the market source arrive as callables and are
    # called where they are needed.
    commit = ast.unparse(_methods(orchestrator)["_commit"])
    assert "self._current_config()" in commit
    assert "self._broker_config()" in commit
    assert "self._runtime_guards()" in commit
    render = ast.unparse(_methods(orchestrator)["render_current"])
    assert "self._active_market_source_id()" in render


def test_the_orchestrator_public_surface_is_small() -> None:
    orchestrator = _class_node(_tree(_ORCHESTRATOR), "SettingsOrchestrator")
    public = {
        name
        for name, member in _methods(orchestrator).items()
        if not name.startswith("_")
    }
    properties = {
        member.name
        for member in orchestrator.body
        if isinstance(member, ast.FunctionDef)
        and any(
            isinstance(decorator, ast.Name) and decorator.id == "property"
            for decorator in member.decorator_list
        )
    }
    assert public | properties == _ORCHESTRATOR_API, sorted(
        (public | properties) ^ _ORCHESTRATOR_API
    )


def test_the_settings_page_intent_surface_is_unchanged() -> None:
    page = _class_node(_tree(_PAGE), "SettingsPage")
    signals = {
        target.id
        for member in page.body
        if isinstance(member, ast.Assign)
        for target in member.targets
        if isinstance(target, ast.Name)
        and isinstance(member.value, ast.Call)
        and ast.unparse(member.value.func) == "Signal"
    }
    assert signals == _PAGE_SIGNALS, sorted(signals ^ _PAGE_SIGNALS)


# -- 10 / 11 / 12 / 13: live reads and the composition boundaries --------


def test_the_live_market_source_is_never_cached() -> None:
    for path in _SETTINGS_PACKAGE:
        identifiers = _identifiers(_tree(path))
        for token in ("active_source_id", "active_source", "is_live"):
            assert token not in identifiers, (path.name, token)


def test_every_programmatic_page_write_is_silent() -> None:
    """A set that emitted would look like a second operator intent."""

    orchestrator = _class_node(_tree(_ORCHESTRATOR), "SettingsOrchestrator")
    setters = {
        name: member
        for name, member in _methods(orchestrator).items()
        if name.startswith(("select_", "adopt_", "confirm_"))
    }
    assert setters, "the programmatic paths exist"
    for name, member in setters.items():
        for call in ast.walk(member):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr
                in {
                    "set_api_provider",
                    "set_market_provider",
                    "set_paper_order_capability",
                    "set_extended_hours_paper",
                }
            ):
                keywords = {kw.arg: kw.value for kw in call.keywords}
                assert "emit_change" in keywords, (name, call.func.attr)
                assert ast.unparse(keywords["emit_change"]) == "False", (
                    name,
                    call.func.attr,
                )


def test_theme_application_does_not_enter_settings() -> None:
    for path in _SETTINGS_PACKAGE:
        text = path.read_text(encoding="utf-8")
        for token in ("_apply_theme", "set_palette", "build_stylesheet"):
            assert token not in text, (path.name, token)
        modules = _imports(_tree(path))
        assert not any(
            module == "PySide6.QtWidgets" or module.startswith("PySide6.QtWidgets")
            for module in modules
        ), (path.name, sorted(modules))


def test_the_market_switch_interlocks_do_not_enter_settings() -> None:
    for path in _SETTINGS_PACKAGE:
        identifiers = _identifiers(_tree(path))
        for token in (
            "request_switch",
            "_request_market_switch",
            "_request_automatic_market_switch",
            "subscription_symbols",
            "has_runtime_obligations",
            "paper_orchestrator",
            "shadow_orchestrator",
            "stop_market_data",
        ):
            assert token not in identifiers, (path.name, token)


def test_the_window_keeps_the_global_composition_it_owns() -> None:
    """The theme fan-out, the fan-out after a commit, and the dialogs."""

    methods = set(_methods(_main_window()))
    for name in (
        "_apply_theme",
        "_on_settings_committed",
        "_on_market_switch_requested",
        "_on_market_provider_selected",
        "_confirm_paper_order_capability",
        "_confirm_extended_hours_paper",
        "_show_settings_information",
        "_show_settings_warning",
        "_probe_gateway",
    ):
        assert name in methods, name

    body = "\n".join(
        ast.unparse(statement)
        for statement in _statements_without_docstring(
            _methods(_main_window())["_on_settings_committed"]
        )
    )
    # Adopt the finished commit and fan out -- nothing else.
    for forbidden in (
        "settings_service.commit",
        "credential_service",
        "preferences_store",
        "validated(",
        "SettingsPageView",
        "settings_page.render",
    ):
        assert forbidden not in body, forbidden
    assert "self.preferences = saved" in body
    assert "self.config = commit.config" in body
    # The provider is restored through the route's owner, never by poking the
    # page's combo: the render path belongs to the market capability.
    assert "self.market_orchestrator.set_selected_provider(" in body
    assert "market_page.set_selected_provider" not in body


def test_the_window_only_touches_the_market_page_palette() -> None:
    page_calls = {
        method
        for method, receiver in _attribute_calls(_desktop())
        if "market_page" in receiver
    }
    assert page_calls == {"set_palette"}, sorted(page_calls)


# -- 14 / 15 / 16 / 17 / 18: neighbours and leftovers --------------------


def test_system_page_is_still_containment_only() -> None:
    system = _class_node(_tree(_SYSTEM_PAGE), "SystemPage")
    assert set(_methods(system)) == {
        "__init__",
        "set_active_workspace",
        "active_workspace",
    }
    # The System pages must not *reference* either capability's owner: naming one
    # in prose is documentation, importing or calling one would be a dependency.
    for path in _python_files(_SRC / "desktop_v2" / "pages" / "system"):
        identifiers = _identifiers(_tree(path))
        assert "SettingsOrchestrator" not in identifiers, path
        assert "RuntimeEventsOrchestrator" not in identifiers, path


def test_the_runtime_events_ownership_has_not_regressed() -> None:
    """v2O-F1's boundary is still the boundary F2 left behind."""

    tree = _desktop()
    used = _self_attrs_used(tree)
    assert "runtime_events" not in used
    assert "runtime_events_orchestrator" in used
    source = _DESKTOP.read_text(encoding="utf-8")
    assert source.count("RuntimeEventStore(") == 1
    assert "store=RuntimeEventStore(" in source
    assert "runtime_events_page.render(" not in source
    for name in (
        "_last_runtime_events_refresh",
        "_runtime_events_refresh_pending",
        "_last_runtime_export",
    ):
        assert name not in _self_attrs_assigned(tree), name

    runtime = _ORCHESTRATOR.parent.parent / "runtime_events" / "orchestrator.py"
    runtime_source = runtime.read_text(encoding="utf-8")
    assert "self._store.list_recent(" in runtime_source
    # The two capabilities are siblings: neither imports the other.
    assert "orchestration.system.settings" not in runtime_source


def test_the_gateway_probe_ownership_is_unchanged() -> None:
    methods = set(_methods(_main_window()))
    assert "_probe_gateway" in methods
    source = _DESKTOP.read_text(encoding="utf-8")
    assert "probe_ibkr_socket" in source
    assert "gateway_badge" in source

    for path in _SETTINGS_PACKAGE:
        text = path.read_text(encoding="utf-8").lower()
        assert "probe" not in text, path
        assert "gateway_badge" not in text, path


def test_no_god_object_was_created() -> None:
    for path in _python_files(_SRC):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ClassDef):
                assert node.name not in _GOD_OBJECTS, (path, node.name)
    assert not (_ORCH_DIR / "orchestrator.py").exists()


def test_the_dead_finnhub_clear_handler_is_gone_without_a_shim() -> None:
    """Zero callers, so F2 retires it rather than carrying it forward."""

    methods = set(_methods(_main_window()))
    for name in (
        "_clear_saved_finnhub_key",
        "_clear_finnhub_key",
        "_clear_saved_key",
    ):
        assert name not in methods, name
    source = _DESKTOP.read_text(encoding="utf-8")
    assert "_clear_saved_finnhub_key" not in source
    # One clear path only: the capability's.
    for path in _python_files(_SRC):
        identifiers = _identifiers(_tree(path))
        assert "clear_saved_finnhub_key" not in identifiers, path


def test_the_retired_window_methods_are_absent() -> None:
    methods = set(_methods(_main_window()))
    for name in _RETIRED_WINDOW_METHODS:
        assert name not in methods, name


# -- the real window -----------------------------------------------------


def test_a_real_window_holds_no_settings_state_of_its_own() -> None:
    window = _window()
    try:
        assert hasattr(window, "settings_page")
        assert hasattr(window, "settings_orchestrator")
        for name in _RETIRED_WINDOW_METHODS + (
            "_settings_api_provider",
            "_connection_settings_enabled",
        ):
            assert not hasattr(window, name), name
        # The application configuration still lives here, deliberately.
        assert window.preferences is not None
        assert window.config is not None
    finally:
        window.close()
        window.deleteLater()
