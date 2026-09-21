"""Architecture guards for Research v2B Universe/History pages."""

from __future__ import annotations

import ast
import importlib
import os
import pathlib
import subprocess
import sys

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP_PATH = _SRC / "desktop.py"
_UNIVERSE_DIR = _SRC / "desktop_v2" / "pages" / "research" / "universe"
_HISTORY_DIR = _SRC / "desktop_v2" / "pages" / "research" / "history"

UNIVERSE_WIDGET_ATTRIBUTES = (
    "universe_search",
    "universe_filter",
    "universe_count_label",
    "universe_table",
    "universe_refresh_button",
    "universe_cancel_button",
)
HISTORY_WIDGET_ATTRIBUTES = (
    "batch_size",
    "history_queue_summary",
    "queue_progress",
    "queue_table",
)
FORBIDDEN_IMPORTS = (
    "us_quant.desktop",
    "us_quant.history_queue",
    "us_quant.ibkr_history",
    "us_quant.public_history",
    "us_quant.ibkr",
    "ibapi",
)
FORBIDDEN_CALLS = (
    "DesktopUniverseService",
    "DesktopHistoryService",
    "HistoryJobStore",
    "TaskThread",
    "refresh_official_universe",
    "enrich_us_profiles",
    "run_history_queue",
    "run_public_history_queue",
)
QT_FREE_FILES = (
    (_UNIVERSE_DIR, "models.py"),
    (_UNIVERSE_DIR, "presenter.py"),
    (_HISTORY_DIR, "models.py"),
    (_HISTORY_DIR, "presenter.py"),
)
LINE_BUDGETS = {
    _UNIVERSE_DIR: {
        "__init__.py": 30,
        "models.py": 140,
        "presenter.py": 220,
        "page.py": 300,
    },
    _HISTORY_DIR: {
        "__init__.py": 30,
        "models.py": 160,
        "presenter.py": 220,
        "page.py": 320,
    },
}


def _python_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _top_level_imports(path: pathlib.Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imports(path: pathlib.Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imported_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def _matches(modules: set[str], prefixes: tuple[str, ...]) -> set[str]:
    return {
        module
        for module in modules
        for prefix in prefixes
        if module == prefix or module.startswith(f"{prefix}.")
    }


def _main_window(path: pathlib.Path) -> ast.ClassDef:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise AssertionError("MainWindow not found")


def _method_names(path: pathlib.Path) -> set[str]:
    return {
        node.name
        for node in _main_window(path).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _self_attributes(class_node: ast.ClassDef) -> set[str]:
    attributes: set[str] = set()
    for node in ast.walk(class_node):
        if not isinstance(node, ast.Attribute):
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            attributes.add(node.attr)
    return attributes


def _method_source(path: pathlib.Path, name: str) -> str:
    for node in _main_window(path).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
    raise AssertionError(f"{name} not found")


def _page_methods(path: pathlib.Path, page_name: str) -> set[str]:
    methods: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Attribute):
            continue
        if node.value.attr == page_name:
            methods.add(node.attr)
    return methods


def test_legacy_universe_and_history_builders_are_retired() -> None:
    methods = _method_names(_DESKTOP_PATH)
    assert "_universe_tab" not in methods
    assert "_data_tab" not in methods


@pytest.mark.parametrize("attribute", UNIVERSE_WIDGET_ATTRIBUTES)
def test_window_no_longer_owns_universe_widget(attribute: str) -> None:
    assert attribute not in _self_attributes(_main_window(_DESKTOP_PATH))


@pytest.mark.parametrize("attribute", HISTORY_WIDGET_ATTRIBUTES)
def test_window_no_longer_owns_history_widget(attribute: str) -> None:
    assert attribute not in _self_attributes(_main_window(_DESKTOP_PATH))


def test_dashboard_no_longer_owns_universe_actions() -> None:
    source = (
        _SRC / "desktop_v2" / "pages" / "dashboard" / "page.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "universe_refresh_button",
        "universe_cancel_button",
        "_refresh_universe",
        "_cancel_universe_refresh",
    ):
        assert forbidden not in source


@pytest.mark.parametrize("directory", (_UNIVERSE_DIR, _HISTORY_DIR))
def test_page_package_imports_no_business_executor(
    directory: pathlib.Path,
) -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(directory):
        for module in _matches(_imports(path), FORBIDDEN_IMPORTS):
            offending.append((path.name, module))
    assert not offending, offending


@pytest.mark.parametrize("directory", (_UNIVERSE_DIR, _HISTORY_DIR))
def test_page_package_imports_no_executor_name(
    directory: pathlib.Path,
) -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(directory):
        for name in _imported_names(path):
            if name in FORBIDDEN_CALLS:
                offending.append((path.name, name))
    assert not offending, offending


@pytest.mark.parametrize("directory", (_UNIVERSE_DIR, _HISTORY_DIR))
def test_page_package_never_calls_an_executor(
    directory: pathlib.Path,
) -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(directory):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in FORBIDDEN_CALLS:
                offending.append((path.name, name))
    assert not offending, offending


@pytest.mark.parametrize(("directory", "name"), QT_FREE_FILES)
def test_projection_modules_import_no_qt(
    directory: pathlib.Path, name: str
) -> None:
    modules = _imports(directory / name)
    assert not any(
        module == "PySide6" or module.startswith("PySide6.")
        for module in modules
    ), sorted(modules)


@pytest.mark.parametrize(("directory", "name"), QT_FREE_FILES)
def test_projection_modules_import_without_a_widget(
    directory: pathlib.Path, name: str
) -> None:
    package = directory.name
    module = importlib.import_module(
        f"us_quant.desktop_v2.pages.research.{package}.{name[:-3]}"
    )
    assert module is not None


@pytest.mark.parametrize("directory", (_UNIVERSE_DIR, _HISTORY_DIR))
def test_initializers_are_lazy_and_do_not_import_page_or_qt(
    directory: pathlib.Path,
) -> None:
    modules = _top_level_imports(directory / "__init__.py")
    assert not any(
        module == "PySide6" or module.startswith("PySide6.")
        for module in modules
    ), sorted(modules)
    assert not any(module.endswith(".page") for module in modules), sorted(modules)


def test_projection_imports_work_without_a_desktop_environment() -> None:
    code = '''
import importlib
import sys

sys.modules["PySide6"] = None
for name in (
    "us_quant.desktop_v2.pages.research.universe",
    "us_quant.desktop_v2.pages.research.universe.models",
    "us_quant.desktop_v2.pages.research.universe.presenter",
    "us_quant.desktop_v2.pages.research.history",
    "us_quant.desktop_v2.pages.research.history.models",
    "us_quant.desktop_v2.pages.research.history.presenter",
):
    importlib.import_module(name)
assert "us_quant.desktop_v2.pages.research.universe.page" not in sys.modules
assert "us_quant.desktop_v2.pages.research.history.page" not in sys.modules
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO_ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_lazy_exports_still_resolve_for_desktop() -> None:
    from us_quant.desktop_v2.pages.research.universe import UniversePage
    from us_quant.desktop_v2.pages.research.history import HistoryPage

    assert UniversePage.__name__ == "UniversePage"
    assert HistoryPage.__name__ == "HistoryPage"


def test_window_uses_only_universe_page_public_api() -> None:
    assert _page_methods(_DESKTOP_PATH, "universe_page") <= {
        "render",
        "set_palette",
    }


def test_window_uses_only_history_page_public_api() -> None:
    assert _page_methods(_DESKTOP_PATH, "history_page") <= {
        "render",
        "set_palette",
    }


@pytest.mark.parametrize(
    ("directory", "budgets"),
    sorted(LINE_BUDGETS.items(), key=lambda item: str(item[0])),
)
def test_page_files_stay_inside_their_budgets(
    directory: pathlib.Path, budgets: dict[str, int]
) -> None:
    for name, budget in budgets.items():
        lines = len((directory / name).read_text(encoding="utf-8").splitlines())
        assert lines <= budget, f"{directory.name}/{name} is {lines}, budget {budget}"


@pytest.mark.parametrize("directory", (_UNIVERSE_DIR, _HISTORY_DIR))
def test_page_files_stay_inside_the_hard_ceiling(
    directory: pathlib.Path,
) -> None:
    for path in _python_files(directory):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 400, f"{path.name} is {lines} lines"
