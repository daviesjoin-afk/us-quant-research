"""Architecture guards for the Desktop Research v2A migration."""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP_PATH = _SRC / "desktop.py"
_TARGETED_DIR = _SRC / "desktop_v2" / "pages" / "research" / "targeted"

RETIRED_WINDOW_ATTRIBUTES = (
    "shadow_status_card",
    "shadow_equity_card",
    "shadow_realized_card",
    "shadow_unrealized_card",
    "shadow_trade_card",
    "shadow_strategy_combo",
    "target_symbol_input",
    "target_symbol_apply_button",
    "target_symbol_subscribe_button",
    "target_symbol_status",
    "shadow_start_button",
    "shadow_stop_button",
    "targeted_replay_button",
    "targeted_robustness_button",
    "minute_data_status",
    "shadow_position_table",
    "shadow_fill_table",
    "shadow_explanation",
    "target_preflight_summary",
    "target_preflight_table",
    "targeted_replay_table",
    "targeted_robustness_summary",
    "targeted_robustness_runs_table",
    "targeted_robustness_scenario_table",
    "targeted_validation_summary",
    "targeted_validation_table",
    "targeted_overfit_summary",
    "targeted_overfit_table",
    "targeted_quality_summary",
    "targeted_quality_table",
    "targeted_stress_summary",
    "targeted_stress_table",
    "targeted_review_summary",
    "targeted_review_history_table",
    "targeted_review_gate_table",
)

FORBIDDEN_TARGETED_IMPORTS = (
    "us_quant.desktop",
    "us_quant.desktop_v2.pages.market",
    "us_quant.shadow.engine",
    "us_quant.shadow.store",
    "us_quant.trading.runtime",
    "us_quant.trading.application",
    "us_quant.trading.adapters",
    "us_quant.trading.composition",
    "us_quant.ibkr",
    "ibapi",
)

FORBIDDEN_TARGETED_CALLS = (
    "ShadowPaperEngine",
    "ShadowPaperStore",
    "build_targeted_shadow_config",
    "run_targeted_replay",
    "run_targeted_robustness",
    "run_targeted_walk_forward",
    "run_targeted_overfit_diagnostics",
    "run_targeted_data_quality",
    "run_targeted_execution_stress",
    "run_targeted_review",
)

QT_FREE_TARGETED_FILES = (
    "models.py",
    "rows.py",
    "session_presenter.py",
    "evidence_presenter.py",
)

LINE_BUDGETS = {
    "__init__.py": 40,
    "models.py": 260,
    "rows.py": 340,
    "session_presenter.py": 280,
    "evidence_presenter.py": 360,
    "controls.py": 260,
    "tables.py": 280,
    "session_panel.py": 320,
    "evidence_panel.py": 380,
    "page.py": 320,
}

ALLOWED_PAGE_METHODS = {
    "render",
    "set_palette",
    "set_target_symbol",
    "set_strategy_options",
    "set_selected_strategy_version",
    "selected_strategy_version_id",
    "target_symbol",
}

FORBIDDEN_TRADING_NAMES = (
    "PlaceOrder",
    "manual_order",
    "market_order",
    "cancel_order",
    "global_cancel",
    "quantity_input",
    "risk_override",
)


def _python_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts
    )


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


def _top_level_imports(path: pathlib.Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


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


def _page_methods(path: pathlib.Path) -> set[str]:
    methods: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Attribute):
            continue
        if node.value.attr == "targeted_validation_page":
            methods.add(node.attr)
    return methods


# -- Guard A: the legacy builder is gone ---------------------------------


def test_the_legacy_simulation_tab_builder_is_retired() -> None:
    assert "_simulation_tab" not in _method_names(_DESKTOP_PATH)


# -- Guard B: MainWindow owns no targeted widgets ------------------------


@pytest.mark.parametrize("attribute", RETIRED_WINDOW_ATTRIBUTES)
def test_the_window_no_longer_owns_a_targeted_widget(attribute: str) -> None:
    assert attribute not in _self_attributes(_main_window(_DESKTOP_PATH))


# -- Guard C/E: the package has no business/runtime edge ----------------


def test_the_targeted_package_imports_no_business_layer() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_TARGETED_DIR):
        for module in _matches(_imports(path), FORBIDDEN_TARGETED_IMPORTS):
            offending.append((path.name, module))
    assert not offending, offending


def test_the_targeted_package_imports_no_research_executor() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_TARGETED_DIR):
        for name in _imported_names(path):
            if name.startswith(("run_targeted_", "save_targeted_", "load_targeted_")):
                offending.append((path.name, name))
    assert not offending, offending


def test_the_targeted_package_never_constructs_a_runtime_or_executor() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_TARGETED_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in FORBIDDEN_TARGETED_CALLS:
                offending.append((path.name, name))
    assert not offending, offending


# -- Guard D: the projection is Qt-free ----------------------------------


@pytest.mark.parametrize("name", QT_FREE_TARGETED_FILES)
def test_the_targeted_projection_modules_import_no_qt(name: str) -> None:
    modules = _imports(_TARGETED_DIR / name)
    assert not any(
        module == "PySide6" or module.startswith("PySide6.")
        for module in modules
    ), sorted(modules)


@pytest.mark.parametrize("name", QT_FREE_TARGETED_FILES)
def test_the_targeted_projection_modules_import_without_a_widget(name: str) -> None:
    module = importlib.import_module(
        f"us_quant.desktop_v2.pages.research.targeted.{name[:-3]}"
    )
    assert module is not None


def test_the_targeted_package_initializer_is_lazy_and_qt_free() -> None:
    modules = _top_level_imports(_TARGETED_DIR / "__init__.py")
    assert not any(
        module == "PySide6" or module.startswith("PySide6.")
        for module in modules
    ), sorted(modules)
    assert not any(
        module.endswith(".page") or module.endswith("targeted.page")
        for module in modules
    ), sorted(modules)


# -- Guard F: the window reaches the page only through its public API ----


def test_the_window_uses_only_the_targeted_page_public_api() -> None:
    methods = _page_methods(_DESKTOP_PATH)
    assert methods <= ALLOWED_PAGE_METHODS, sorted(methods - ALLOWED_PAGE_METHODS)


def test_the_window_still_holds_the_targeted_page() -> None:
    assert "targeted_validation_page" in _self_attributes(_main_window(_DESKTOP_PATH))


# -- Guard G: line budgets -----------------------------------------------


@pytest.mark.parametrize(("name", "budget"), sorted(LINE_BUDGETS.items()))
def test_each_targeted_file_stays_inside_its_budget(name: str, budget: int) -> None:
    lines = len((_TARGETED_DIR / name).read_text(encoding="utf-8").splitlines())
    assert lines <= budget, f"{name} is {lines} lines, budget {budget}"


@pytest.mark.parametrize("name", sorted(LINE_BUDGETS))
def test_no_targeted_file_approaches_the_hard_ceiling(name: str) -> None:
    lines = len((_TARGETED_DIR / name).read_text(encoding="utf-8").splitlines())
    assert lines <= 400, f"{name} is {lines} lines"


# -- Guard H: no new capability ------------------------------------------


def test_the_targeted_package_adds_no_order_capability() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_TARGETED_DIR):
        identifiers = {
            node.id
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Attribute)
        }
        for name in FORBIDDEN_TRADING_NAMES:
            if name in identifiers:
                offending.append((path.name, name))
    assert not offending, offending
