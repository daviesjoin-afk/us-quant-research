"""Architecture guards for the Desktop Research v2R-F aggregate."""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_RESEARCH_DIR = _SRC / "desktop_v2" / "pages" / "research"
_PAGE_PATH = _RESEARCH_DIR / "page.py"
_NAV_PATH = _RESEARCH_DIR / "navigation.py"
_INIT_PATH = _RESEARCH_DIR / "__init__.py"
_DESKTOP_PATH = _SRC / "desktop.py"
_PREVIEW_PATH = _REPO_ROOT / "scripts" / "render_desktop_preview.py"

_CHILD_MODULES = (
    "research.targeted",
    "research.universe",
    "research.history",
    "research.scanner",
    "research.backtest",
    "research.cross_section",
)
_CHILD_NAMES = {
    "TargetedValidationPage",
    "UniversePage",
    "HistoryPage",
    "ScannerPage",
    "BacktestPage",
    "CrossSectionResearchPage",
}
_FORBIDDEN_IMPORTS = (
    "us_quant.desktop",
    "us_quant.desktop_backtest_service",
    "us_quant.desktop_history_service",
    "us_quant.desktop_market_scan_service",
    "us_quant.desktop_universe_service",
    "us_quant.desktop_workers",
    "us_quant.trading",
    "os",
    "sqlite3",
    "tempfile",
    "pathlib",
)
_FORBIDDEN_NAMES = {
    "BacktestRequest",
    "DesktopBacktestService",
    "ExecutionApplication",
    "MarketScan",
    "RiskApplication",
    "ShadowPaperEngine",
    "StrategySelectionService",
    "TaskThread",
    "TradingRuntime",
    "UniverseSnapshot",
}
_FORBIDDEN_CHILD_CALLS = {
    "current_draft",
    "render",
    "set_palette",
    "set_strategy_options",
}
_LINE_BUDGETS = {
    "__init__.py": 35,
    "navigation.py": 120,
    "page.py": 200,
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


def _main_window_method(path: pathlib.Path, name: str) -> ast.FunctionDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name == name:
                    return member
    raise AssertionError(f"MainWindow.{name} not found")


def _method_source(path: pathlib.Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    return ast.get_source_segment(source, _main_window_method(path, name)) or ""


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


def test_main_window_has_no_raw_research_tabs() -> None:
    source = _DESKTOP_PATH.read_text(encoding="utf-8")
    method = _method_source(_DESKTOP_PATH, "_build_v2_pages")
    assert "v2_research_tabs" not in source
    assert "research = QTabWidget()" not in method
    assert "research.addTab(" not in method


def test_main_window_routes_research_to_the_aggregate() -> None:
    assert _pages_dict_value(_DESKTOP_PATH, "_build_v2_pages", "research") == (
        "self.research_page"
    )


def test_main_window_uses_the_semantic_targeted_workspace() -> None:
    source = _method_source(_DESKTOP_PATH, "_targeted_robustness_finished")
    assert 'self.shell.navigate_to("research")' in source
    assert "self.research_page.set_active_workspace" in source
    assert "ResearchWorkspace.TARGETED" in source
    for forbidden in (
        "v2_research_tabs.setCurrentIndex",
        "research_page.setCurrentIndex",
    ):
        assert forbidden not in _DESKTOP_PATH.read_text(encoding="utf-8")


def test_preview_uses_semantic_research_navigation() -> None:
    source = _PREVIEW_PATH.read_text(encoding="utf-8")
    for forbidden in (
        'select("research", 0)',
        'select("research", 3)',
        'select("research", 4)',
        'select("research", 5)',
        '.page("research").setCurrentIndex',
    ):
        assert forbidden not in source
    assert "def select_research(" in source
    assert "ResearchWorkspace.BACKTEST" in source
    assert "ResearchWorkspace.SCANNER" in source
    assert "ResearchWorkspace.CROSS_SECTION" in source


def test_aggregate_imports_no_business_layer() -> None:
    modules = _imports(_PAGE_PATH)
    for forbidden in _FORBIDDEN_IMPORTS:
        assert not any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for module in modules
        ), sorted(modules)
    assert not (_imported_names(_PAGE_PATH) & _FORBIDDEN_NAMES)


def test_aggregate_does_not_import_child_page_classes() -> None:
    source = _PAGE_PATH.read_text(encoding="utf-8")
    modules = _imports(_PAGE_PATH)
    names = _imported_names(_PAGE_PATH)
    assert not any(token in module for module in modules for token in _CHILD_MODULES)
    assert not (names & _CHILD_NAMES)
    for token in _CHILD_MODULES:
        assert token not in source


def test_aggregate_calls_only_tab_containment_not_child_business_apis() -> None:
    offending: list[str] = []
    for node in ast.walk(_tree(_PAGE_PATH)):
        if not isinstance(node, ast.Call) or not isinstance(
            node.func, ast.Attribute
        ):
            continue
        if node.func.attr not in _FORBIDDEN_CHILD_CALLS:
            continue
        receiver = ast.unparse(node.func.value)
        if "_pages" in receiver or receiver == "self":
            offending.append(f"{receiver}.{node.func.attr}")
    assert not offending, offending


def test_aggregate_public_surface_is_small() -> None:
    for node in _tree(_PAGE_PATH).body:
        if not isinstance(node, ast.ClassDef) or node.name != "ResearchPage":
            continue
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
        raise AssertionError("ResearchPage not found")


def test_navigation_contract_is_qt_free() -> None:
    assert not any(
        module == "PySide6" or module.startswith("PySide6.")
        for module in _imports(_NAV_PATH)
    )


def test_research_initializer_is_lazy() -> None:
    modules = _top_level_imports(_INIT_PATH)
    assert not any(
        module == "PySide6" or module.startswith("PySide6.")
        for module in modules
    )
    assert not any(module.endswith(".page") for module in modules)
    code = '''
import importlib
import sys

sys.modules["PySide6"] = None
importlib.import_module("us_quant.desktop_v2.pages.research")
importlib.import_module("us_quant.desktop_v2.pages.research.navigation")
assert "us_quant.desktop_v2.pages.research.page" not in sys.modules
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


def test_research_aggregate_files_stay_inside_budgets() -> None:
    for name, budget in _LINE_BUDGETS.items():
        lines = len((_RESEARCH_DIR / name).read_text(encoding="utf-8").splitlines())
        assert lines <= budget, f"{name} is {lines}, budget {budget}"


def test_research_aggregate_files_stay_inside_hard_ceiling() -> None:
    for path in (_INIT_PATH, _NAV_PATH, _PAGE_PATH):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 400, f"{path.name} is {lines} lines"