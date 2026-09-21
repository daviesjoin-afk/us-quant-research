"""Architecture guards for Research v2D BacktestPage."""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP_PATH = _SRC / "desktop.py"
_BACKTEST_DIR = _SRC / "desktop_v2" / "pages" / "research" / "backtest"

LEGACY_METHODS = (
    "_backtest_tab",
    "_refresh_backtest_strategy_combo",
    "_backtest_result_selection_changed",
    "_show_backtest_run",
)
BACKTEST_WIDGET_ATTRIBUTES = (
    "backtest_return_card",
    "backtest_cagr_card",
    "backtest_sharpe_card",
    "backtest_drawdown_card",
    "backtest_trade_card",
    "backtest_strategy_combo",
    "backtest_symbol",
    "backtest_start",
    "backtest_end",
    "backtest_capital",
    "backtest_weight",
    "backtest_run_button",
    "backtest_compare_button",
    "backtest_per_share_cost",
    "backtest_minimum_cost",
    "backtest_slippage",
    "backtest_evidence",
    "backtest_chart",
    "backtest_comparison_table",
    "backtest_trades_table",
)
FORBIDDEN_IMPORTS = (
    "us_quant.desktop",
    "us_quant.desktop_backtest_service",
    "us_quant.desktop_workers",
    "us_quant.trading.application.strategy_selection",
    "us_quant.trading.composition",
    "us_quant.trading.domain",
    "pathlib",
    "os",
    "tempfile",
    "ibapi",
)
FORBIDDEN_CALLS = (
    "BacktestRequest",
    "DesktopBacktestService",
    "run_backtest",
    "save_backtest_run",
    "TaskThread",
    "load_latest_normalized_series",
    "BacktestEngine",
    "StrategySelectionService",
    "StrategyApplication",
)
QT_FREE_FILES = (
    _BACKTEST_DIR / "models.py",
    _BACKTEST_DIR / "presenter.py",
)
LINE_BUDGETS = {
    "__init__.py": 30,
    "models.py": 220,
    "presenter.py": 320,
    "controls.py": 300,
    "tables.py": 320,
    "page.py": 340,
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


def test_legacy_backtest_surface_is_retired() -> None:
    methods = _method_names(_DESKTOP_PATH)
    for name in LEGACY_METHODS:
        assert name not in methods


def test_window_no_longer_owns_backtest_widgets() -> None:
    attributes = _self_attributes(_DESKTOP_PATH)
    for name in BACKTEST_WIDGET_ATTRIBUTES:
        assert name not in attributes


def test_worker_lifecycle_does_not_touch_backtest_ui() -> None:
    source = _method_source(_DESKTOP_PATH, "_worker_finished")
    assert "backtest" not in source.casefold()


def test_backtest_package_imports_no_executor() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_BACKTEST_DIR):
        for module in _imports(path):
            if any(
                module == prefix or module.startswith(f"{prefix}.")
                for prefix in FORBIDDEN_IMPORTS
            ):
                offending.append((path.name, module))
    assert not offending, offending


def test_backtest_package_never_names_an_executor() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_BACKTEST_DIR):
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


def test_backtest_package_never_constructs_backtest_request() -> None:
    for path in _python_files(_BACKTEST_DIR):
        assert "BacktestRequest(" not in path.read_text(encoding="utf-8")


def test_models_and_presenter_are_qt_free() -> None:
    for path in QT_FREE_FILES:
        assert not any(
            module == "PySide6" or module.startswith("PySide6.")
            for module in _imports(path)
        ), path


def test_backtest_initializer_is_lazy() -> None:
    modules = _top_level_imports(_BACKTEST_DIR / "__init__.py")
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
    "us_quant.desktop_v2.pages.research.backtest",
    "us_quant.desktop_v2.pages.research.backtest.models",
    "us_quant.desktop_v2.pages.research.backtest.presenter",
):
    importlib.import_module(name)
assert "us_quant.desktop_v2.pages.research.backtest.page" not in sys.modules
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


def test_window_uses_only_backtest_page_surface() -> None:
    assert _page_methods(_DESKTOP_PATH, "backtest_page") <= {
        "render",
        "set_strategy_options",
        "set_palette",
        "run_selected_requested",
        "compare_all_requested",
        "run_selected",
    }


def test_backtest_files_stay_inside_budgets() -> None:
    for name, budget in LINE_BUDGETS.items():
        path = _BACKTEST_DIR / name
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= budget, f"{name} is {lines}, budget {budget}"


def test_backtest_files_stay_inside_hard_ceiling() -> None:
    for path in _python_files(_BACKTEST_DIR):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 400, f"{path.name} is {lines} lines"
