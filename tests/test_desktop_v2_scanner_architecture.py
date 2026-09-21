"""Architecture guards for Research v2C ScannerPage."""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP_PATH = _SRC / "desktop.py"
_SCANNER_DIR = _SRC / "desktop_v2" / "pages" / "research" / "scanner"

LEGACY_METHODS = (
    "_scanner_tab",
    "_populate_scan_table",
    "_scan_selection_changed",
)
SCANNER_WIDGET_ATTRIBUTES = (
    "scan_search",
    "scan_filter",
    "scan_coverage_label",
    "scan_table",
    "scan_chart",
)
FORBIDDEN_IMPORTS = (
    "us_quant.desktop",
    "us_quant.desktop_market_scan_service",
    "us_quant.history_queue",
    "us_quant.desktop_workers",
    "us_quant.trading",
    "us_quant.risk",
    "us_quant.execution",
    "us_quant.shadow",
    "ibapi",
)
FORBIDDEN_CALLS = (
    "DesktopMarketScanService",
    "scan_market",
    "save_market_scan",
    "load_close_series",
    "TaskThread",
    "HistoryJobStore",
    "MarketDataApplication",
    "TradingRuntime",
    "ExecutionApplication",
    "RiskApplication",
    "ShadowPaperEngine",
)
QT_FREE_FILES = (
    _SCANNER_DIR / "models.py",
    _SCANNER_DIR / "presenter.py",
)
LINE_BUDGETS = {
    "__init__.py": 30,
    "models.py": 180,
    "presenter.py": 240,
    "table.py": 260,
    "page.py": 280,
}


def _python_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts
    )


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


def _self_attributes(path: pathlib.Path) -> set[str]:
    attributes: set[str] = set()
    for node in ast.walk(_main_window(path)):
        if not isinstance(node, ast.Attribute):
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            attributes.add(node.attr)
    return attributes


def _method_source(path: pathlib.Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    for node in _main_window(path).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} not found")


def _page_methods(path: pathlib.Path, page_name: str) -> set[str]:
    methods: set[str] = set()
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Attribute):
            continue
        if (
            isinstance(node.value, ast.Attribute)
            and node.value.attr == page_name
        ):
            methods.add(node.attr)
    return methods


def test_legacy_scanner_surface_is_retired() -> None:
    methods = _method_names(_DESKTOP_PATH)
    for name in LEGACY_METHODS:
        assert name not in methods


def test_window_no_longer_owns_scanner_widgets() -> None:
    attributes = _self_attributes(_DESKTOP_PATH)
    for name in SCANNER_WIDGET_ATTRIBUTES:
        assert name not in attributes


def test_dashboard_no_longer_offers_manual_scan() -> None:
    source = _method_source(_DESKTOP_PATH, "_dashboard_tab")
    assert "运行市场扫描" not in source
    assert "_run_scan" not in source


def test_scanner_package_imports_no_executor() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_SCANNER_DIR):
        for module in _imports(path):
            if any(
                module == prefix or module.startswith(f"{prefix}.")
                for prefix in FORBIDDEN_IMPORTS
            ):
                offending.append((path.name, module))
    assert not offending, offending


def test_scanner_package_never_names_an_executor() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_SCANNER_DIR):
        for name in _imported_names(path):
            if name in FORBIDDEN_CALLS:
                offending.append((path.name, name))
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(
                node.func, "attr", None
            )
            if name in FORBIDDEN_CALLS:
                offending.append((path.name, name))
    assert not offending, offending


def test_models_and_presenter_are_qt_free() -> None:
    for path in QT_FREE_FILES:
        assert not any(
            module == "PySide6" or module.startswith("PySide6.")
            for module in _imports(path)
        ), path


def test_scanner_initializer_is_lazy() -> None:
    modules = _top_level_imports(_SCANNER_DIR / "__init__.py")
    assert not any(
        module == "PySide6" or module.startswith("PySide6.")
        for module in modules
    )
    assert not any(module.endswith(".page") for module in modules)


def test_projection_imports_work_without_desktop_environment() -> None:
    code = '''
import importlib
import sys

sys.modules["PySide6"] = None
for name in (
    "us_quant.desktop_v2.pages.research.scanner",
    "us_quant.desktop_v2.pages.research.scanner.models",
    "us_quant.desktop_v2.pages.research.scanner.presenter",
):
    importlib.import_module(name)
assert "us_quant.desktop_v2.pages.research.scanner.page" not in sys.modules
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": str(_REPO_ROOT / "src"),
            "PATH": __import__("os").environ.get("PATH", ""),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_window_uses_only_scanner_page_surface() -> None:
    assert _page_methods(_DESKTOP_PATH, "scanner_page") <= {
        "render",
        "render_chart",
        "set_palette",
        "scan_requested",
        "symbol_selected",
    }


def test_business_paths_do_not_read_displayed_rows() -> None:
    for method in (
        "_apply_intraday_watchlist",
        "_select_auto_quant_candidates",
    ):
        source = _method_source(_DESKTOP_PATH, method)
        assert "scanner_page" not in source
        assert "scan_table" not in source


def test_scanner_files_stay_inside_budgets() -> None:
    for name, budget in LINE_BUDGETS.items():
        path = _SCANNER_DIR / name
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= budget, f"{name} is {lines}, budget {budget}"


def test_scanner_files_stay_inside_hard_ceiling() -> None:
    for path in _python_files(_SCANNER_DIR):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 400, f"{path.name} is {lines} lines"
