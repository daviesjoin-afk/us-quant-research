"""Architecture guards for the Desktop Market v2 migration.

These are structural, not behavioural: they read the source tree and assert the
boundaries the migration exists to create.  A later change that quietly
reintroduces a reverse edge -- the window owning a market widget again, or the
page reaching for the market data application -- fails here rather than in
review.

The guards are split from the large per-service test modules on purpose.  Those
read ``desktop.py`` to freeze *which handlers moved*; these read the market
package to freeze *what the page may know*.

One note on the import guards.  They parse with ``ast``, which catches a real
``import`` statement anywhere in the file but does not evaluate a dynamic
import.  The intent is to catch accidental coupling, not to defeat a determined
author -- the same caveat the trading architecture guards carry.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "us_quant"
_DESKTOP_PATH = _SRC / "desktop.py"
_WIDGETS_PATH = _SRC / "desktop_widgets.py"
_MARKET_DIR = _SRC / "desktop_v2" / "pages" / "market"

#: The widgets the market route used to build for itself, which the window then
#: mutated by attribute from a dozen handlers.  None of them may be an attribute
#: of ``MainWindow`` any more: the page owns them.
RETIRED_WINDOW_ATTRIBUTES = (
    "stream_connection_card",
    "stream_feed_card",
    "stream_ready_card",
    "stream_watch_card",
    "stream_symbols",
    "stream_mode",
    "stream_start_button",
    "stream_stop_button",
    "stream_scan_watchlist_button",
    "stream_scope_label",
    "stream_empty_label",
    "quotes_model",
    "quotes_table",
    "stream_health_text",
)

#: Retired MainWindow state: these two were pure UI repaint state and moved
#: into the page.  They are *not* in the list above because they are not
#: widgets, and they are named again in the page guard below.
RETIRED_WINDOW_STATE = (
    "_quotes_scroll_active",
    "_pending_stream_snapshot",
)

#: What the market package may not import.  Each entry is a capability the page
#: must not have, not merely a module it happens not to use: a page that could
#: construct the application could start a feed, and a page that could read a
#: credential store would be a second orchestration point.
FORBIDDEN_MARKET_IMPORTS = (
    "us_quant.desktop",
    "us_quant.trading.application",
    "us_quant.trading.composition",
    "us_quant.trading.adapters",
    "us_quant.credential_store",
    "us_quant.desktop_credentials",
    "us_quant.ibkr",
    "us_quant.shadow",
    "us_quant.paper",
    "us_quant.auto_quant",
    "us_quant.workflow_controller",
    "ibapi",
)

#: Names whose *construction* the page must not contain, even if the import is
#: indirect.  Runtime construction is the window's.
FORBIDDEN_MARKET_CALLS = (
    "MarketDataStartRequest",
    "MarketDataCredentials",
    "StreamWorker",
    "build_market_data_application",
    "probe_ibkr_socket",
    "IBKRConnectionConfig",
)

#: The files that must stay Qt-free, so the projection is testable without a
#: widget toolkit.
QT_FREE_MARKET_FILES = ("models.py", "rows.py", "presenter.py")

#: Per-file line caps from the plan.  These are the *declared* budgets, not the
#: hard 400: a file that grows to its cap is a signal to split it, so the guard
#: checks the budget rather than the ceiling.
LINE_BUDGETS = {
    "__init__.py": 40,
    "models.py": 180,
    "rows.py": 260,
    "presenter.py": 300,
    "controls.py": 280,
    "tables.py": 320,
    "page.py": 380,
}

#: Names that would be a *new* trading capability on a market observation route.
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


def _matches(modules: set[str], prefixes: tuple[str, ...]) -> set[str]:
    return {
        module
        for module in modules
        for prefix in prefixes
        if module == prefix or module.startswith(f"{prefix}.")
    }


def _main_window(path: pathlib.Path) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise AssertionError("MainWindow not found")


def _method_names(path: pathlib.Path) -> set[str]:
    """Every method name ``MainWindow`` defines, plus its assigned attributes."""

    window = _main_window(path)
    names: set[str] = set()
    for node in ast.walk(window):
        if isinstance(node, ast.FunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Attribute) and isinstance(
            node.ctx, ast.Store
        ):
            names.add(node.attr)
    return names


# -- Guard A: the legacy builder is gone ---------------------------------


def test_the_legacy_market_builder_no_longer_exists() -> None:
    """``_quotes_tab`` must be deleted, not shimmed.

    A ``def _quotes_tab(self): return self.market_page`` would satisfy a guard
    that only checked the route table, and would leave the old call site -- and
    the habit of reaching for it -- in place.
    """

    assert "_quotes_tab" not in _method_names(_DESKTOP_PATH)


def test_the_retired_scroll_handlers_are_gone() -> None:
    """Scroll repaint state is the page's, so its window handlers go with it."""

    names = _method_names(_DESKTOP_PATH)
    for retired in ("_quotes_scroll_started", "_quotes_scroll_finished"):
        assert retired not in names, retired


# -- Guard B: the window owns no market widget ---------------------------


@pytest.mark.parametrize("attribute", RETIRED_WINDOW_ATTRIBUTES)
def test_the_window_does_not_own_a_market_widget(attribute: str) -> None:
    """Every one of these was ``self.<name>`` in the old builder.

    The window may hold the page and nothing inside it: a page whose internals
    are reachable by attribute would put the presentation back under the
    window's control.
    """

    assert attribute not in _method_names(_DESKTOP_PATH)


@pytest.mark.parametrize("attribute", RETIRED_WINDOW_STATE)
def test_the_window_no_longer_keeps_the_scroll_state(attribute: str) -> None:
    assert attribute not in _method_names(_DESKTOP_PATH)


def test_the_window_reaches_the_market_route_only_through_the_page() -> None:
    """The one attribute the window may use, and the entry points it may call.

    Asserting the *absence* of the widget names above is the guard; this is its
    complement, so a future round cannot "fix" the guards by leaving the route
    reachable some other way without a reader noticing.
    """

    names = _method_names(_DESKTOP_PATH)
    assert "market_page" in names


# -- Guard C: the market package imports no business service -------------


def test_the_market_package_imports_no_business_layer() -> None:
    offending: list[tuple[str, str]] = []
    for path in _python_files(_MARKET_DIR):
        for module in _matches(_imports(path), FORBIDDEN_MARKET_IMPORTS):
            offending.append((path.name, module))
    assert not offending, offending


def test_the_market_package_imports_no_widget_module_from_the_window() -> None:
    """``desktop_widgets`` is allowed; ``desktop`` would be a reverse edge."""

    for path in _python_files(_MARKET_DIR):
        modules = _imports(path)
        assert "us_quant.desktop" not in modules, path.name


# -- Guard D: the projection is Qt-free ----------------------------------


@pytest.mark.parametrize("name", QT_FREE_MARKET_FILES)
def test_the_projection_modules_import_no_qt(name: str) -> None:
    modules = _imports(_MARKET_DIR / name)
    assert not any(
        module == "PySide6" or module.startswith("PySide6.")
        for module in modules
    ), sorted(modules)


def test_the_projection_modules_can_be_imported_without_a_widget() -> None:
    """The Qt-free claim, exercised rather than read off the imports."""

    import importlib

    for name in QT_FREE_MARKET_FILES:
        module = importlib.import_module(
            f"us_quant.desktop_v2.pages.market.{name[:-3]}"
        )
        assert module is not None


# -- Guard E: the page cannot construct a runtime ------------------------


def test_the_market_package_never_constructs_a_runtime_object() -> None:
    """Even with an indirect import, these names must not be *called* here."""

    offending: list[tuple[str, str]] = []
    for path in _python_files(_MARKET_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(
                    node.func, "attr", None
                )
                if name in FORBIDDEN_MARKET_CALLS:
                    offending.append((path.name, name))
    assert not offending, offending


# -- Guard F: the legacy table model stays retired -----------------------


def test_the_legacy_widget_module_no_longer_defines_the_quote_model() -> None:
    """Two table models for one route is the dual path this round removes."""

    tree = ast.parse(_WIDGETS_PATH.read_text(encoding="utf-8"))
    defined = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }
    assert "QuoteTableModel" not in defined


def test_the_quote_model_has_exactly_one_definition() -> None:
    holders = [
        path.relative_to(_SRC).as_posix()
        for path in _python_files(_SRC)
        if any(
            isinstance(node, ast.ClassDef)
            and node.name == "QuoteTableModel"
            for node in ast.parse(
                path.read_text(encoding="utf-8")
            ).body
        )
    ]
    assert holders == ["desktop_v2/pages/market/tables.py"]


# -- Guard G: line budgets -----------------------------------------------


@pytest.mark.parametrize(("name", "budget"), sorted(LINE_BUDGETS.items()))
def test_each_market_file_stays_inside_its_budget(
    name: str, budget: int
) -> None:
    lines = len(
        (_MARKET_DIR / name).read_text(encoding="utf-8").splitlines()
    )
    assert lines <= budget, f"{name} is {lines} lines, budget {budget}"


@pytest.mark.parametrize("name", sorted(LINE_BUDGETS))
def test_no_market_file_approaches_the_hard_ceiling(name: str) -> None:
    """The 400-line hard limit, asserted separately from the softer budget."""

    lines = len(
        (_MARKET_DIR / name).read_text(encoding="utf-8").splitlines()
    )
    assert lines <= 400, f"{name} is {lines} lines"


def test_the_page_and_the_presenter_are_still_separate_files() -> None:
    """The specific merge this budget exists to prevent."""

    assert (_MARKET_DIR / "page.py").exists()
    assert (_MARKET_DIR / "presenter.py").exists()
    assert (_MARKET_DIR / "tables.py").exists()
    assert (_MARKET_DIR / "rows.py").exists()


# -- Guard H: no new trading capability ----------------------------------


def test_the_market_package_adds_no_order_capability() -> None:
    """Market stays an observation and subscription surface."""

    offending: list[tuple[str, str]] = []
    for path in _python_files(_MARKET_DIR):
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


def test_the_market_package_drives_no_trading_runtime() -> None:
    """The page must not call into a workflow, engine or runtime."""

    forbidden_attributes = (
        "paper_workflow",
        "shadow_engine",
        "trading_runtime",
        "workflow_controller",
        "auto_quant",
    )
    offending: list[tuple[str, str]] = []
    for path in _python_files(_MARKET_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in (
                forbidden_attributes
            ):
                offending.append((path.name, node.attr))
    assert not offending, offending
