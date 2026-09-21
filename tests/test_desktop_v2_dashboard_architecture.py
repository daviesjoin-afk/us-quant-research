"""Architecture guards for the Dashboard v2 ownership migration."""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP_PATH = _SRC / "desktop.py"
_DASHBOARD_DIR = _SRC / "desktop_v2" / "pages" / "dashboard"

RETIRED_METHODS = (
    "_dashboard_tab",
    "_populate_dashboard_account_cards",
    "_populate_artifact_table",
    "_refresh_cards",
)
RETIRED_WINDOW_WIDGETS = (
    "universe_card",
    "verified_card",
    "history_card",
    "signal_card",
    "artifact_table",
    "dashboard_chart",
    "dashboard_notes",
)
FORBIDDEN_DASHBOARD_IMPORTS = (
    "us_quant.desktop",
    "us_quant.trading.application",
    "us_quant.trading.composition",
    "us_quant.trading.adapters",
    "us_quant.artifact_state",
    "us_quant.runtime_events",
    "us_quant.desktop_workers",
    "sqlite3",
    "pathlib",
    "os",
)
FORBIDDEN_PAGE_CALLS = (
    "load_close_series",
    "probe_ibkr_socket",
    "load_artifact_catalog",
    "open",
    "read_text",
    "read_bytes",
)
LINE_BUDGETS = {
    "__init__.py": 20,
    "models.py": 120,
    "presenter.py": 230,
    "artifact_table.py": 160,
    "page.py": 230,
}


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _main_window(path: pathlib.Path) -> ast.ClassDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise AssertionError("MainWindow not found")


def _method_source(path: pathlib.Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    for node in _main_window(path).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} not found")


def _imports(path: pathlib.Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


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


def _pages_value(path: pathlib.Path, key: str) -> str:
    source = path.read_text(encoding="utf-8")
    for node in _main_window(path).body:
        if not isinstance(node, ast.FunctionDef) or node.name != "_build_v2_pages":
            continue
        for item in ast.walk(node):
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(item, ast.Assign):
                targets = list(item.targets)
                value = item.value
            elif isinstance(item, ast.AnnAssign):
                targets = [item.target]
                value = item.value
            if not any(
                isinstance(target, ast.Name) and target.id == "pages"
                for target in targets
            ):
                continue
            if not isinstance(value, ast.Dict):
                continue
            for item_key, item_value in zip(
                value.keys, value.values
            ):
                if isinstance(item_key, ast.Constant) and item_key.value == key:
                    return ast.get_source_segment(source, item_value) or ""
    raise AssertionError(f"pages[{key!r}] not found")


def _python_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def test_retired_dashboard_methods_and_widgets_are_gone() -> None:
    window = _main_window(_DESKTOP_PATH)
    methods = {
        node.name for node in window.body if isinstance(node, ast.FunctionDef)
    }
    for name in RETIRED_METHODS:
        assert name not in methods, name
    assigned = _assigned_self_attrs(_DESKTOP_PATH)
    for name in RETIRED_WINDOW_WIDGETS:
        assert name not in assigned, name


def test_dashboard_route_points_at_the_native_page() -> None:
    assert _pages_value(_DESKTOP_PATH, "dashboard") == "self.dashboard_page"
    assert "_dashboard_tab" not in _DESKTOP_PATH.read_text(encoding="utf-8")


def test_main_window_does_not_reach_into_dashboard_widgets() -> None:
    for node in ast.walk(_tree(_DESKTOP_PATH)):
        if not isinstance(node, ast.Attribute):
            continue
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "self"
            and owner.attr == "dashboard_page"
        ):
            assert node.attr in {
                "gateway_probe_requested",
                "render",
                "set_palette",
            }, node.attr


def test_main_window_uses_no_direct_dashboard_cell_or_series_mutation() -> None:
    offending: list[str] = []
    for node in ast.walk(_tree(_DESKTOP_PATH)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None)
        if name in {"setItem", "setForeground", "set_series", "setPlainText"}:
            offending.append(name)
    assert not offending, offending


def test_dashboard_package_imports_no_business_executor_or_filesystem() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_DASHBOARD_DIR):
        for module in _imports(path):
            if any(
                module == prefix or module.startswith(f"{prefix}.")
                for prefix in FORBIDDEN_DASHBOARD_IMPORTS
            ):
                offending.append((path.name, module))
    assert not offending, offending


def test_page_never_calls_a_business_loader_or_probe() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_DASHBOARD_DIR):
        tree = _tree(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(
                node.func, "attr", None
            )
            if name in FORBIDDEN_PAGE_CALLS:
                offending.append((path.name, str(name)))
    assert not offending, offending


def test_models_and_presenter_are_qt_free() -> None:
    for name in ("models.py", "presenter.py"):
        modules = _imports(_DASHBOARD_DIR / name)
        assert not any(
            module == "PySide6" or module.startswith("PySide6.")
            for module in modules
        ), name


def test_package_initializer_does_not_force_load_the_qt_page() -> None:
    initializer = (_DASHBOARD_DIR / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "dashboard.page" not in initializer
    snippet = (
        "import sys; "
        "import us_quant.desktop_v2.pages.dashboard.models; "
        "import us_quant.desktop_v2.pages.dashboard.presenter; "
        "raise SystemExit(1 if 'PySide6' in sys.modules else 0)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_gateway_and_theme_are_wired_at_page_level() -> None:
    connect = _method_source(_DESKTOP_PATH, "_connect_dashboard_page")
    assert "gateway_probe_requested" in connect
    assert "_probe_gateway" in connect
    theme = _method_source(_DESKTOP_PATH, "_apply_theme")
    assert "dashboard_page.set_palette(self.theme)" in theme
    assert "dashboard_chart" not in theme


@pytest.mark.parametrize(
    "method",
    (
        "_load_local_state",
        "_cross_section_finished",
        "_refresh_account_surfaces",
        "_invalidate_stream_snapshot",
        "_stream_snapshot_received",
    ),
)
def test_fact_change_paths_publish_the_dashboard(method: str) -> None:
    assert "_publish_dashboard_view()" in _method_source(_DESKTOP_PATH, method)


@pytest.mark.parametrize(("name", "budget"), sorted(LINE_BUDGETS.items()))
def test_dashboard_file_stays_inside_its_line_budget(
    name: str, budget: int
) -> None:
    lines = len(
        (_DASHBOARD_DIR / name).read_text(encoding="utf-8").splitlines()
    )
    assert lines <= budget, f"{name} is {lines} lines, budget {budget}"
