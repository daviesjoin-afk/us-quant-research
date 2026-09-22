"""Architecture guards for Desktop Research v2E CrossSectionResearchPage."""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP_PATH = _SRC / "desktop.py"
_CROSS_SECTION_DIR = (
    _SRC / "desktop_v2" / "pages" / "research" / "cross_section"
)

LEGACY_METHODS = (
    "_strategy_tab",
    "_populate_strategy_report",
    "_run_strategy_research",
    "_strategy_finished",
    "_load_strategy_report",
)
LEGACY_STATE_ATTRIBUTES = (
    "strategy_report",
    "strategy_path",
)
CROSS_SECTION_WIDGET_ATTRIBUTES = (
    "strategy_gate_card",
    "strategy_return_card",
    "strategy_dd_card",
    "spy_return_card",
    "strategy_fold_card",
    "research_capital_input",
    "strategy_chart",
    "candidate_table",
    "fold_table",
)
FORBIDDEN_NAMES = frozenset(
    {
        "AppConfig",
        "ExecutionApplication",
        "RiskApplication",
        "ShadowPaperEngine",
        "TaskThread",
        "TradingRuntime",
        "UniverseSnapshot",
        "run_cross_sectional_research",
        "run_executable_cross_sectional_research",
        "save_executable_research",
    }
)
FORBIDDEN_STDLIB_IMPORTS = frozenset(
    {"json", "os", "pathlib", "tempfile"}
)
FORBIDDEN_IMPORT_ROOTS = (
    "us_quant.config",
    "us_quant.cross_sectional",
    "us_quant.executable_research",
    "us_quant.trading",
    "us_quant.universe",
)
QT_FREE_FILES = (
    _CROSS_SECTION_DIR / "models.py",
    _CROSS_SECTION_DIR / "presenter.py",
)
LINE_BUDGETS = {
    "__init__.py": 30,
    "models.py": 220,
    "presenter.py": 300,
    "controls.py": 220,
    "tables.py": 300,
    "page.py": 300,
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


def _forbidden_module(module: str) -> bool:
    if module in FORBIDDEN_STDLIB_IMPORTS:
        return True
    if module == "us_quant.desktop":
        return True
    if module.startswith("us_quant.desktop_"):
        return not (
            module.startswith("us_quant.desktop_v2")
            or module == "us_quant.desktop_widgets"
        )
    return module in FORBIDDEN_IMPORT_ROOTS or any(
        module.startswith(f"{root}.") for root in FORBIDDEN_IMPORT_ROOTS
    )


def test_legacy_cross_section_surface_is_retired() -> None:
    methods = _method_names(_DESKTOP_PATH)
    for name in LEGACY_METHODS:
        assert name not in methods
    attributes = _self_attributes(_DESKTOP_PATH)
    for name in LEGACY_STATE_ATTRIBUTES:
        assert name not in attributes


def test_window_no_longer_owns_cross_section_widgets() -> None:
    attributes = _self_attributes(_DESKTOP_PATH)
    for name in CROSS_SECTION_WIDGET_ATTRIBUTES:
        assert name not in attributes


def test_the_window_no_longer_reads_a_local_research_capital() -> None:
    """v2O-C4: the research scenario scalar has one canonical owner.

    ``_research_scenario_capital`` was the window's convenience reader.  It is
    retired rather than kept as a wrapper: a forwarding method would let every
    consumer keep working without ever naming the owner, so "who reads the
    research scenario capital?" would stop being one grep.  The consumer
    migration, the Decimal precision and the full ownership guard live in
    ``tests/test_desktop_research_cross_section_orchestration.py``.
    """

    methods = _method_names(_DESKTOP_PATH)
    assert "_research_scenario_capital" not in methods
    assert "_research_capital_changed" not in methods
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    assert "_research_capital_value" not in source


def test_cross_section_package_imports_no_executor() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_CROSS_SECTION_DIR):
        for module in _imports(path):
            if _forbidden_module(module):
                offending.append((path.name, module))
    assert not offending, offending


def test_cross_section_package_never_names_an_executor() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_CROSS_SECTION_DIR):
        for name in _imported_names(path):
            if name in FORBIDDEN_NAMES:
                offending.append((path.name, name))
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(
                node.func, "attr", None
            )
            if name in FORBIDDEN_NAMES:
                offending.append((path.name, name))
    assert not offending, offending


def test_models_and_presenter_are_qt_free() -> None:
    for path in QT_FREE_FILES:
        assert not any(
            module == "PySide6" or module.startswith("PySide6.")
            for module in _imports(path)
        ), path


def test_cross_section_initializer_is_lazy() -> None:
    modules = _top_level_imports(_CROSS_SECTION_DIR / "__init__.py")
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
    "us_quant.desktop_v2.pages.research.cross_section",
    "us_quant.desktop_v2.pages.research.cross_section.models",
    "us_quant.desktop_v2.pages.research.cross_section.presenter",
):
    importlib.import_module(name)
assert "us_quant.desktop_v2.pages.research.cross_section.page" not in sys.modules
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


def test_window_uses_only_cross_section_page_surface() -> None:
    """v2O-C4: the window composes the page and does not paint or edit it.

    ``render`` and ``set_research_capital`` are gone from the window's own
    surface: the capability is the page's only render owner and the page's
    control is the only capital editor.  ``capital_changed`` and
    ``run_requested`` remain because they are the two *signals* the window
    connects, and connecting a signal is composition.
    """

    assert _page_methods(_DESKTOP_PATH, "cross_section_page") <= {
        "current_draft",
        "set_palette",
        "run_requested",
        "capital_changed",
    }
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    for private_surface in (
        "cross_section_page.render",
        "cross_section_page.set_research_capital",
        "cross_section_page.controls",
        "cross_section_page.chart",
        "cross_section_page.candidate_table",
        "cross_section_page.fold_table",
    ):
        assert private_surface not in source


def test_cross_section_files_stay_inside_budgets() -> None:
    for name, budget in LINE_BUDGETS.items():
        path = _CROSS_SECTION_DIR / name
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= budget, f"{name} is {lines}, budget {budget}"


def test_cross_section_files_stay_inside_hard_ceiling() -> None:
    for path in _python_files(_CROSS_SECTION_DIR):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 400, f"{path.name} is {lines} lines"