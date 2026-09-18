"""Desktop UI v2 shell tests.

The shell is a view container: it registers routes, shows one page at a time
and switches between them.  These tests pin exactly that contract and nothing
more -- no broker, no stream, no order.

Two properties get extra attention because they are what keep the shell from
quietly turning back into an orchestrator:

* it must not import a business service (checked structurally over the source);
* it must not copy a page -- the widget placed in the stack is the *same
  object* the caller passed in, so per-page state survives navigation.
"""

from __future__ import annotations

import ast
import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from us_quant.desktop_v2.navigation import (
    DEFAULT_ROUTE,
    NAVIGATION_ITEMS,
    ROUTES,
    label_for,
)
from us_quant.desktop_v2.shell import DesktopShellV2


_APP = QApplication.instance() or QApplication([])

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SHELL_PATH = _REPO_ROOT / "src" / "us_quant" / "desktop_v2" / "shell.py"
_NAV_PATH = _REPO_ROOT / "src" / "us_quant" / "desktop_v2" / "navigation.py"

EXPECTED_ROUTES = (
    "dashboard",
    "market",
    "account",
    "strategy",
    "risk",
    "execution",
    "research",
    "system",
)

# Services the shell must never reach for.  The shell renders state; the
# runtime owns it.
FORBIDDEN_IMPORTS = (
    "us_quant.market_data_service",
    "us_quant.paper_trading_service",
    "us_quant.paper_workflow",
    "us_quant.paper_session",
    "us_quant.auto_quant",
    "us_quant.risk",
    "us_quant.strategy_registry",
    "us_quant.ibkr",
    "us_quant.ibkr_readonly",
    "us_quant.ibkr_stream",
    "us_quant.ibkr_paper_orders",
    "us_quant.ibkr_paper_gateway",
    "us_quant.alpaca_stream",
    "us_quant.finnhub_stream",
)


def _pages() -> dict[str, QWidget]:
    return {route: QWidget() for route in EXPECTED_ROUTES}


# -- navigation table -----------------------------------------------------


def test_navigation_defines_exactly_the_eight_routes_in_order() -> None:
    assert ROUTES == EXPECTED_ROUTES
    assert len(NAVIGATION_ITEMS) == 8


def test_navigation_labels_are_fixed() -> None:
    assert tuple(item.label for item in NAVIGATION_ITEMS) == (
        "总览",
        "行情",
        "账户",
        "策略",
        "风控",
        "订单与成交",
        "研究",
        "系统",
    )


def test_default_route_is_dashboard() -> None:
    assert DEFAULT_ROUTE == "dashboard"


def test_label_for_rejects_an_unknown_route() -> None:
    assert label_for("market") == "行情"
    with pytest.raises(KeyError):
        label_for("nope")


# -- shell behaviour ------------------------------------------------------


def test_shell_registers_all_routes_and_opens_on_dashboard() -> None:
    shell = DesktopShellV2(_pages())
    assert shell.routes == EXPECTED_ROUTES
    assert shell.current_route == "dashboard"
    assert shell.page_stack.count() == 8


def test_navigate_to_market_switches_the_visible_page() -> None:
    pages = _pages()
    shell = DesktopShellV2(pages)
    shell.navigate_to("market")
    assert shell.current_route == "market"
    assert shell.page_stack.currentWidget() is pages["market"]


def test_navigate_to_execution_switches_the_visible_page() -> None:
    pages = _pages()
    shell = DesktopShellV2(pages)
    shell.navigate_to("execution")
    assert shell.current_route == "execution"
    assert shell.page_stack.currentWidget() is pages["execution"]


def test_unknown_route_fails_closed_and_leaves_the_current_page_alone() -> None:
    pages = _pages()
    shell = DesktopShellV2(pages)
    shell.navigate_to("market")
    with pytest.raises(KeyError):
        shell.navigate_to("does-not-exist")
    # A rejected route must not half-apply.
    assert shell.current_route == "market"
    assert shell.page_stack.currentWidget() is pages["market"]


def test_page_identity_is_preserved_so_state_is_not_discarded() -> None:
    pages = _pages()
    shell = DesktopShellV2(pages)
    for route in EXPECTED_ROUTES:
        assert shell.page(route) is pages[route]
        assert shell.page_stack.widget(EXPECTED_ROUTES.index(route)) is pages[
            route
        ]


def test_navigation_does_not_recreate_or_duplicate_a_page() -> None:
    pages = _pages()
    shell = DesktopShellV2(pages)
    market = pages["market"]
    for _ in range(5):
        shell.navigate_to("market")
        shell.navigate_to("account")
    shell.navigate_to("market")
    assert shell.page("market") is market
    assert shell.page_stack.count() == 8
    # The stack holds each page exactly once.
    widgets = [
        shell.page_stack.widget(index)
        for index in range(shell.page_stack.count())
    ]
    assert len(set(map(id, widgets))) == 8


def test_page_state_survives_a_round_trip_between_routes() -> None:
    """Identity is not enough on its own -- prove the state is really kept."""

    pages = _pages()
    label = QLabel("初始")
    pages["market"].setObjectName("marketPage")
    pages["market"].setToolTip("kept")
    shell = DesktopShellV2(pages)
    label.setText("已更新")
    shell.navigate_to("account")
    shell.navigate_to("market")
    assert shell.page("market").toolTip() == "kept"
    assert label.text() == "已更新"


def test_missing_route_fails_closed_at_construction() -> None:
    pages = _pages()
    del pages["risk"]
    with pytest.raises(ValueError, match="missing pages"):
        DesktopShellV2(pages)


def test_unknown_route_in_the_page_table_fails_closed() -> None:
    pages = _pages()
    pages["bogus"] = QWidget()
    with pytest.raises(ValueError, match="unknown routes"):
        DesktopShellV2(pages)


def test_rail_compacts_below_the_threshold_and_restores_labels() -> None:
    shell = DesktopShellV2(_pages())
    shell.resize(DesktopShellV2.COMPACT_RAIL_THRESHOLD - 1, 720)
    shell.show()
    _APP.processEvents()
    assert shell.rail_is_compact
    assert shell.anchor_rail.width() == 52
    assert shell._anchors["dashboard"].text() == "•"
    assert shell._anchors["dashboard"].accessibleName() == "总览"
    assert shell._anchors["dashboard"].toolTip() == "总览"

    shell.resize(DesktopShellV2.COMPACT_RAIL_THRESHOLD, 720)
    _APP.processEvents()
    assert not shell.rail_is_compact
    assert shell.anchor_rail.width() == 176
    assert shell._anchors["dashboard"].text() == "总览"
    shell.close()


def test_clicking_an_anchor_navigates() -> None:
    pages = _pages()
    shell = DesktopShellV2(pages)
    shell._anchors["research"].click()
    _APP.processEvents()
    assert shell.current_route == "research"
    assert shell.page_stack.currentWidget() is pages["research"]


# -- structural guards ----------------------------------------------------


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                modules.add(node.module)
    return modules


@pytest.mark.parametrize("forbidden", FORBIDDEN_IMPORTS)
def test_shell_does_not_import_a_business_service(forbidden: str) -> None:
    modules = _imported_modules(_SHELL_PATH)
    offending = {
        module
        for module in modules
        if module == forbidden or module.startswith(f"{forbidden}.")
    }
    assert not offending, f"shell imports {sorted(offending)}"


def test_shell_does_not_reach_for_broker_or_order_verbs() -> None:
    """The shell must not connect a broker, stream or submit anything.

    ``connect`` needs care: Qt spells signal wiring ``signal.connect(slot)``,
    which is exactly what a view shell should do.  So the rule is that
    ``connect`` may only be called on a known Qt signal, never on a service
    object -- ``port.connect()`` or ``service.connect()`` would fail here.
    """

    tree = ast.parse(_SHELL_PATH.read_text(encoding="utf-8"))
    qt_signals = {
        "clicked",
        "toggled",
        "triggered",
        "pressed",
        "released",
        "textChanged",
        "currentIndexChanged",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        assert func.attr not in {"submit", "cancel", "disconnect"}, (
            f"shell calls {func.attr}() at line {node.lineno}"
        )
        if func.attr == "connect":
            receiver = (
                func.value.attr
                if isinstance(func.value, ast.Attribute)
                else None
            )
            assert receiver in qt_signals, (
                "shell calls connect() on a non-signal object at line "
                f"{node.lineno}: {ast.unparse(func.value)}"
            )
    # And it must not import sqlite or ibapi either.
    modules = _imported_modules(_SHELL_PATH)
    assert "sqlite3" not in modules
    assert "ibapi" not in modules


def test_navigation_module_is_the_single_source_of_routes() -> None:
    """Route strings must not be duplicated across the v2 package."""

    nav_source = _NAV_PATH.read_text(encoding="utf-8")
    assert "NAVIGATION_ITEMS" in nav_source
    shell_source = _SHELL_PATH.read_text(encoding="utf-8")
    # The shell reads the table instead of restating the routes.
    assert "NAVIGATION_ITEMS" in shell_source
    for route in EXPECTED_ROUTES:
        assert f'"{route}"' not in shell_source
