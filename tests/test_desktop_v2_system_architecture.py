"""Architecture guards for the Desktop System v2 aggregate.

These read the source and, for the ownership guards, inspect a real
``MainWindow``.  They exist so the migration cannot silently regress: if a
retired builder, a raw System ``QTabWidget``, a ``settings_*`` alias or a
service import reappears, the guard goes red rather than the route quietly
growing a second owner.
"""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_SYSTEM_DIR = _SRC / "desktop_v2" / "pages" / "system"
_PAGE_PATH = _SYSTEM_DIR / "page.py"
_NAV_PATH = _SYSTEM_DIR / "navigation.py"
_INIT_PATH = _SYSTEM_DIR / "__init__.py"
_RUNTIME_DIR = _SYSTEM_DIR / "runtime_events"
_SETTINGS_DIR = _SYSTEM_DIR / "settings"
_DESKTOP_PATH = _SRC / "desktop.py"
_PANEL_PATH = _SRC / "desktop_settings_panel.py"
_PREVIEW_PATH = _REPO_ROOT / "scripts" / "render_desktop_preview.py"

# Guard A: the transitional route surface must be gone.
_RETIRED_METHODS = ("_runtime_tab", "_settings_tab")
_RETIRED_ROUTE_ATTR = "v2_system_tabs"

# Guard D: MainWindow must not own these runtime widgets any more.
_RETIRED_RUNTIME_ATTRS = (
    "runtime_error_card",
    "runtime_warning_card",
    "runtime_task_card",
    "runtime_export_card",
    "runtime_empty_label",
    "runtime_event_table",
    "runtime_info_text",
)

# Guard E: MainWindow must not own any settings widget alias.
_RETIRED_SETTINGS_ATTRS = (
    "settings_theme_combo",
    "settings_provider_combo",
    "settings_switch_provider_button",
    "settings_api_provider_combo",
    "settings_finnhub_key",
    "settings_alpaca_key",
    "settings_alpaca_secret",
    "settings_save_credentials_button",
    "settings_clear_credentials_button",
    "settings_credential_status",
    "settings_ibkr_host",
    "settings_ibkr_port",
    "settings_ibkr_client_id",
    "settings_ibkr_timeout",
    "settings_paper_order_capability",
    "settings_extended_hours_paper",
    "settings_save_button",
    "settings_panel",
)

# Guard F: no reach-through into the settings page's internals.
_SETTINGS_REACH_THROUGH = (
    "settings_page.theme_combo",
    "settings_page.credentials.",
    "settings_page.connection.",
    "settings_page.appearance.",
    "settings_page.credentials_status",
)

# Guard H: the System aggregate imports nothing from the business layer.
_FORBIDDEN_AGGREGATE_IMPORTS = (
    "us_quant.desktop",
    "us_quant.desktop_settings",
    "us_quant.desktop_credentials",
    "us_quant.credential_store",
    "us_quant.runtime_events",
    "us_quant.trading",
    "sqlite3",
)
_FORBIDDEN_AGGREGATE_NAMES = {
    "RuntimeEventStore",
    "DesktopSettingsService",
    "DesktopCredentialService",
    "MarketDataApplication",
    "BrokerAccountApplication",
    "TradingRuntime",
    "ExecutionApplication",
    "UserPreferences",
}

# Guard G: the aggregate must not import the child page classes.
_CHILD_IMPORTS = ("runtime_events.page", "settings.page", "runtime_events", "settings")
_CHILD_NAMES = {"RuntimeEventsPage", "SettingsPage"}

# Guard I: the runtime page/table cannot open the store.
_FORBIDDEN_RUNTIME_IMPORTS = (
    "us_quant.runtime_events",
    "sqlite3",
)
_FORBIDDEN_RUNTIME_NAMES = {"RuntimeEventStore", "sqlite3"}

# Guard J: the whole settings UI package stays service-free.
_FORBIDDEN_SETTINGS_IMPORTS = (
    "us_quant.desktop_settings",
    "us_quant.desktop_credentials",
    "us_quant.credential_store",
    "us_quant.user_settings",
    "us_quant.trading",
)
_FORBIDDEN_SETTINGS_NAMES = {
    "DesktopSettingsService",
    "DesktopCredentialService",
    "CredentialStore",
    "MarketDataApplication",
    "BrokerAccountApplication",
    "UserPreferences",
    "UserPreferencesStore",
}

_QT_FREE_FILES = (
    _NAV_PATH,
    _RUNTIME_DIR / "models.py",
    _RUNTIME_DIR / "presenter.py",
    _SETTINGS_DIR / "models.py",
)

_LINE_BUDGETS = {
    "__init__.py": 35,
    "navigation.py": 90,
    "page.py": 160,
    "runtime_events/__init__.py": 30,
    "runtime_events/models.py": 160,
    "runtime_events/presenter.py": 220,
    "runtime_events/table.py": 200,
    "runtime_events/page.py": 240,
    "settings/__init__.py": 30,
    "settings/models.py": 200,
    "settings/appearance.py": 180,
    "settings/credentials.py": 240,
    "settings/connection.py": 240,
    "settings/page.py": 300,
}


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


def _top_level_imports(path: pathlib.Path) -> set[str]:
    modules: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imported_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def _module_imports(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _main_window_method(path: pathlib.Path, name: str) -> ast.FunctionDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name == name:
                    return member
    raise AssertionError(f"MainWindow.{name} not found")


def _method_source(path: pathlib.Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    return ast.get_source_segment(
        source, _main_window_method(path, name)
    ) or ""


def _pages_dict_value(path: pathlib.Path, method_name: str, key: str) -> str:
    for node in ast.walk(_main_window_method(path, method_name)):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "pages"
            for target in targets
        ):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for item_key, item_value in zip(node.value.keys, node.value.values):
            if isinstance(item_key, ast.Constant) and item_key.value == key:
                return ast.unparse(item_value)
    raise AssertionError(f"pages[{key!r}] not found in {method_name}")


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


def _window():
    from PySide6.QtWidgets import QApplication

    from us_quant.desktop import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    app.processEvents()
    return window


# -- A / B / C: the route is native and the builders are gone ----------


def test_legacy_system_builders_are_deleted() -> None:
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            methods = {
                member.name
                for member in node.body
                if isinstance(member, ast.FunctionDef)
            }
            for name in _RETIRED_METHODS:
                assert name not in methods, name
    assert _RETIRED_ROUTE_ATTR not in source


def test_build_v2_pages_has_no_raw_system_tabs() -> None:
    method = _method_source(_DESKTOP_PATH, "_build_v2_pages")
    assert "system = QTabWidget()" not in method
    assert "system.addTab(" not in method
    assert "_runtime_tab()" not in method
    assert "_settings_tab()" not in method
    assert "SystemPage(" in method


def test_route_table_points_at_the_system_page() -> None:
    assert _pages_dict_value(_DESKTOP_PATH, "_build_v2_pages", "system") == (
        "self.system_page"
    )


# -- D / E / F: no widget ownership, no reach-through ------------------


def test_main_window_owns_no_runtime_widgets() -> None:
    assigned = _assigned_self_attrs(_DESKTOP_PATH)
    for name in _RETIRED_RUNTIME_ATTRS:
        assert name not in assigned, name


def test_main_window_owns_no_settings_widget_aliases() -> None:
    assigned = _assigned_self_attrs(_DESKTOP_PATH)
    for name in _RETIRED_SETTINGS_ATTRS:
        assert name not in assigned, name


def test_main_window_does_not_reach_into_the_settings_page() -> None:
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    for token in _SETTINGS_REACH_THROUGH:
        assert token not in source, token
    for token in (
        "settings_page.save_button",
        "settings_page._",
    ):
        assert token not in source, token


def test_main_window_does_not_reach_into_the_runtime_page() -> None:
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    for token in (
        "runtime_events_page.table",
        "runtime_events_page._",
        "runtime_events_page.error_card",
        "runtime_events_page.export_card",
    ):
        assert token not in source, token


def test_a_real_window_exposes_the_system_page_and_no_aliases() -> None:
    window = _window()
    try:
        assert window.system_page is window.shell.page("system")
        assert hasattr(window, "runtime_events_page")
        assert hasattr(window, "settings_page")
        for name in _RETIRED_RUNTIME_ATTRS + _RETIRED_SETTINGS_ATTRS:
            assert not hasattr(window, name), name
    finally:
        window.close()
        window.deleteLater()


# -- G / H: the aggregate is containment only -------------------------


def test_system_navigation_identity_is_semantic() -> None:
    """Secondary navigation is keyed by the enum, never by 0 / 1."""

    from us_quant.desktop_v2.pages.system.navigation import (
        SYSTEM_NAVIGATION_ITEMS,
        SystemWorkspace,
    )

    assert {workspace.value for workspace in SystemWorkspace} == {
        "runtime_events",
        "settings",
    }
    assert [item.workspace for item in SYSTEM_NAVIGATION_ITEMS] == [
        SystemWorkspace.RUNTIME_EVENTS,
        SystemWorkspace.SETTINGS,
    ]
    for item in SYSTEM_NAVIGATION_ITEMS:
        assert isinstance(item.workspace, SystemWorkspace)


def test_aggregate_imports_no_child_page_classes() -> None:
    source = _PAGE_PATH.read_text(encoding="utf-8")
    names = _imported_names(_PAGE_PATH)
    assert not (names & _CHILD_NAMES), sorted(names)
    for token in ("RuntimeEventsPage", "SettingsPage"):
        assert token not in source


def test_aggregate_imports_no_business_layer() -> None:
    modules = _imports(_PAGE_PATH)
    for forbidden in _FORBIDDEN_AGGREGATE_IMPORTS:
        assert not any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for module in modules
        ), sorted(modules)
    assert not (_imported_names(_PAGE_PATH) & _FORBIDDEN_AGGREGATE_NAMES)


def test_aggregate_public_surface_is_small() -> None:
    for node in _tree(_PAGE_PATH).body:
        if isinstance(node, ast.ClassDef) and node.name == "SystemPage":
            methods = {
                member.name
                for member in node.body
                if isinstance(member, ast.FunctionDef)
            }
            assert methods == {
                "__init__",
                "set_active_workspace",
                "active_workspace",
            }
            break
    else:
        raise AssertionError("SystemPage not found")


# -- I / J: the pages never reach for a service -----------------------


def test_runtime_page_and_table_do_not_open_the_store() -> None:
    for path in (_RUNTIME_DIR / "page.py", _RUNTIME_DIR / "table.py"):
        modules = _imports(path)
        names = _imported_names(path)
        for forbidden in _FORBIDDEN_RUNTIME_IMPORTS:
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in modules
            ), (path.name, sorted(modules))
        assert not (names & _FORBIDDEN_RUNTIME_NAMES), (path.name, sorted(names))


def test_settings_package_is_service_free() -> None:
    for path in sorted(_SETTINGS_DIR.glob("*.py")):
        modules = _imports(path)
        names = _imported_names(path)
        for forbidden in _FORBIDDEN_SETTINGS_IMPORTS:
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in modules
            ), (path.name, sorted(modules))
        assert not (names & _FORBIDDEN_SETTINGS_NAMES), (
            path.name,
            sorted(names),
        )


# -- K: the Qt-free contracts stay actually Qt-free --------------------


def test_qt_free_contracts_import_no_qt() -> None:
    for path in _QT_FREE_FILES:
        modules = _imports(path)
        assert not any(
            module == "PySide6" or module.startswith("PySide6.")
            for module in modules
        ), (path.name, sorted(modules))


# -- L: the package initializers stay lazy -----------------------------


def test_system_initializers_are_lazy() -> None:
    for path in (
        _INIT_PATH,
        _RUNTIME_DIR / "__init__.py",
        _SETTINGS_DIR / "__init__.py",
    ):
        modules = _top_level_imports(path)
        assert not any(
            module == "PySide6" or module.startswith("PySide6.")
            for module in modules
        ), path.name
        assert not any(module.endswith(".page") for module in modules), (
            path.name
        )
    code = '''
import importlib
import sys

sys.modules["PySide6"] = None
importlib.import_module("us_quant.desktop_v2.pages.system")
importlib.import_module("us_quant.desktop_v2.pages.system.navigation")
assert "us_quant.desktop_v2.pages.system.page" not in sys.modules
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": str(_REPO_ROOT / "src"),
            "PATH": os.environ.get("PATH", ""),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr


# -- M: the old panel is deleted ---------------------------------------


def test_the_old_settings_panel_is_deleted() -> None:
    assert not _PANEL_PATH.exists()
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert "desktop_settings_panel" not in source
    assert "DesktopSettingsPanel" not in source
    assert "DesktopSettingsCallbacks" not in source


# -- preview uses semantic system navigation ---------------------------


def test_preview_uses_semantic_system_navigation() -> None:
    source = _PREVIEW_PATH.read_text(encoding="utf-8")
    assert "def select_system(" in source
    assert "SystemWorkspace.RUNTIME_EVENTS" in source
    assert "SystemWorkspace.SETTINGS" in source
    for forbidden in (
        "select_system(window, 0)",
        "select_system(window, 1)",
        ".page(\"system\").setCurrentIndex",
        "settings_theme_combo",
    ):
        assert forbidden not in source, forbidden


# -- N: line budgets ---------------------------------------------------


def test_system_files_stay_inside_budgets() -> None:
    for rel, budget in _LINE_BUDGETS.items():
        lines = len((_SYSTEM_DIR / rel).read_text(encoding="utf-8").splitlines())
        assert lines <= budget, f"{rel} is {lines}, budget {budget}"


def test_system_files_stay_inside_the_hard_ceiling() -> None:
    for path in _SYSTEM_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 400, f"{path.name} is {lines} lines"
