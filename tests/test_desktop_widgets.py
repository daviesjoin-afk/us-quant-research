"""Coverage for the presentation layer that moved out of ``desktop.py``.

Three widgets and two formatting helpers used to live in the 9.9k-line
window module.  Moving them is only worth something if the rendering
rules still hold, so these tests pin the behaviour that the extraction
froze: the two helpers' text output, ``MetricCard``'s object names, and
the two charts' public surface.

``QuoteTableModel`` has since moved on again, to
``desktop_v2/pages/market/tables.py``: it was only ever the market
route's table, so its own coverage lives beside it in
``test_desktop_v2_market_tables.py``.

Structural guards read the source by path instead of importing it.  That
matters here: the failure they exist to catch is ``desktop_widgets``
importing back up into ``desktop``, which would make the whole module
fail to import -- and a collection error reports no test name at all, so
an import-based guard would go silent exactly when it is needed.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from PySide6.QtWidgets import QApplication


_APP = QApplication.instance() or QApplication([])

WIDGETS_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "us_quant" / "desktop_widgets.py"
)
DESKTOP_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "us_quant" / "desktop.py"
)

MOVED_NAMES = (
    "_sortable_number",
    "_price",
    "MetricCard",
    "PriceChart",
    "EquityComparisonChart",
)

FORBIDDEN_IMPORTS = (
    "desktop",
    "desktop_workers",
    "runtime_supervisor",
    "trading.application",
    "auto_quant",
    "risk",
    "strategy",
    "ibkr_paper_orders",
    "paper_trading_service",
    "paper_workflow",
    "paper_session",
    "workflow_state",
    "account_ledger",
)

ALLOWED_IMPORTS = {
    "__future__",
    "re",
    "datetime",
    "decimal",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "us_quant.ui_theme",
}

ALLOWED_QT_NAMES = {
    "QPointF",
    "QRectF",
    "Qt",
    "QColor",
    "QFont",
    "QLinearGradient",
    "QPainter",
    "QPainterPath",
    "QPen",
    "QFrame",
    "QLabel",
    "QSizePolicy",
    "QVBoxLayout",
    "QWidget",
    # Desktop Execution v2: the shared table and combo configuration lives here,
    # so the execution page and the window apply one implementation of it.  The
    # four names below are the types those two helpers name.
    "QAbstractItemView",
    "QComboBox",
    "QHeaderView",
    "QTableView",
}


def _widgets():
    import us_quant.desktop_widgets as widgets

    return widgets


def _top_level_names(raw: bytes) -> dict[str, int]:
    """Top-level defined names -> count, read by path (never imported)."""

    tree = ast.parse(raw.decode("utf-8"))
    counts: dict[str, int] = {}
    for node in tree.body:
        name = getattr(node, "name", None)
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _imported_modules(raw: bytes) -> set[str]:
    tree = ast.parse(raw.decode("utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.add(node.module)
    return out


# --------------------------------------------------------------------------
# structure: the module boundary itself
# --------------------------------------------------------------------------


def test_widgets_module_defines_each_moved_symbol_exactly_once() -> None:
    counts = _top_level_names(WIDGETS_PATH.read_bytes())
    for name in MOVED_NAMES:
        assert counts.get(name, 0) == 1, f"{name} count={counts.get(name, 0)}"


def test_desktop_defines_none_of_the_moved_symbols() -> None:
    counts = _top_level_names(DESKTOP_PATH.read_bytes())
    for name in MOVED_NAMES:
        assert counts.get(name, 0) == 0, f"{name} is still defined in desktop.py"


def test_helpers_have_exactly_one_definition_in_the_source_tree() -> None:
    """A leftover copy kept "for compatibility" is the thing to catch."""

    src_root = DESKTOP_PATH.parent
    for helper in MOVED_NAMES:
        holders = [
            path.name
            for path in sorted(src_root.rglob("*.py"))
            if _top_level_names(path.read_bytes()).get(helper, 0)
        ]
        assert holders == ["desktop_widgets.py"], f"{helper} defined in {holders}"


def test_widgets_module_imports_no_layer_above_it() -> None:
    imported = _imported_modules(WIDGETS_PATH.read_bytes())
    for forbidden in FORBIDDEN_IMPORTS:
        for module in imported:
            assert not (
                module == f"us_quant.{forbidden}"
                or module.startswith(f"us_quant.{forbidden}.")
            ), f"desktop_widgets imports {module}"


def test_widgets_module_allows_only_the_expected_dependencies() -> None:
    imported = _imported_modules(WIDGETS_PATH.read_bytes())
    assert imported == ALLOWED_IMPORTS, f"unexpected: {sorted(imported ^ ALLOWED_IMPORTS)}"


def _code_identifiers(raw: bytes) -> set[str]:
    """Identifiers actually used in code -- docstrings and comments excluded."""

    tree = ast.parse(raw.decode("utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                out.add(a.asname or a.name)
    return out


def test_widgets_module_imports_no_threading_plumbing() -> None:
    imported = _imported_modules(WIDGETS_PATH.read_bytes())
    assert "us_quant.desktop_workers" not in imported
    identifiers = _code_identifiers(WIDGETS_PATH.read_bytes())
    for name in ("QThread", "DesktopTaskController", "RuntimeSupervisor", "Signal"):
        assert name not in identifiers, f"desktop_widgets uses {name}"


def test_widgets_module_knows_no_application_services() -> None:
    identifiers = _code_identifiers(WIDGETS_PATH.read_bytes())
    for name in (
        "MainWindow",
        "PaperTradingService",
        "WorkflowController",
        "MarketDataService",
        "AutoQuantEngine",
        "IBKRPaperOrderService",
    ):
        assert name not in identifiers, f"desktop_widgets uses {name}"


def test_widgets_module_imports_no_qt_name_outside_the_allowed_set() -> None:
    tree = ast.parse(WIDGETS_PATH.read_text(encoding="utf-8"))
    seen: set[str] = set()
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("PySide6")
        ):
            for a in node.names:
                seen.add(a.asname or a.name)
    assert seen == ALLOWED_QT_NAMES, f"unexpected: {sorted(seen ^ ALLOWED_QT_NAMES)}"


def test_desktop_reexports_the_same_objects() -> None:
    """Identity, not equality: a wrapper would break every isinstance."""

    import us_quant.desktop as desktop
    import us_quant.desktop_widgets as widgets

    for name in MOVED_NAMES:
        assert getattr(desktop, name) is getattr(widgets, name), name


def test_desktop_keeps_no_unused_qt_imports_from_the_move() -> None:
    """Every Qt name desktop.py imports must still be referenced."""

    raw = DESKTOP_PATH.read_bytes()
    tree = ast.parse(raw.decode("utf-8"))
    imported: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("PySide6")
        ):
            for a in node.names:
                imported[a.asname or a.name] = node.module
    used = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    dead = sorted(name for name in imported if name not in used)
    assert dead == [], f"desktop.py no longer references {dead}"


# --------------------------------------------------------------------------
# _sortable_number / _price
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234", 1234.0),
        ("$12.50", 12.5),
        ("¥8", 8.0),
        ("25%", 0.25),
        ("-2.5%", -0.025),
        ("+3.5", 3.5),
        ("1,234.50", 1234.5),
        (" 12 ", 12.0),
        ("—", None),
        ("", None),
        ("abc", None),
        ("1.2.3", None),
    ],
)
def test_sortable_number_parses_the_expected_formats(text, expected) -> None:
    result = _widgets()._sortable_number(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_sortable_number_returns_floats_not_strings() -> None:
    result = _widgets()._sortable_number("1,234")
    assert isinstance(result, float)


def test_price_keeps_its_exact_text_output() -> None:
    from decimal import Decimal

    price = _widgets()._price
    assert price(None) == "—"
    assert price(Decimal("1")) == "1"
    assert price(Decimal("1.2")) == "1.2"
    assert price(Decimal("1234.5")) == "1,234.5"
    assert price(Decimal("1.23456")) == "1.2346"


# --------------------------------------------------------------------------
# the migrated widgets' public surface
# --------------------------------------------------------------------------


def test_public_attributes_survive_the_move() -> None:
    """MainWindow and tests read these directly."""

    from datetime import date

    widgets = _widgets()

    card = widgets.MetricCard("t", "v", "n")
    assert card.value_label.text() == "v"
    assert card.note_label.text() == "n"

    chart = widgets.PriceChart()
    assert chart.symbol == ""
    assert chart.points == ()
    assert chart.display_title == ""
    chart.set_series("AAPL", ((date(2026, 1, 1), 1.0),), title="t")
    assert chart.symbol == "AAPL"
    assert chart.display_title == "t"

    equity = widgets.EquityComparisonChart()
    assert equity.rows == []
    assert equity.secondary_key == "spy_equity"
    assert equity.secondary_label == "SPY 整股"


# --------------------------------------------------------------------------
# MetricCard
# --------------------------------------------------------------------------


def test_metric_card_keeps_its_object_names_and_size_policy() -> None:
    from PySide6.QtWidgets import QSizePolicy

    card = _widgets().MetricCard("标题", "1.23", "备注")
    assert card.objectName() == "metricCard"
    assert card.value_label.objectName() == "metricValue"
    assert card.note_label.objectName() == "metricNote"
    assert card.sizePolicy().horizontalPolicy() == QSizePolicy.Expanding
    assert card.sizePolicy().verticalPolicy() == QSizePolicy.Preferred


def test_metric_card_keeps_its_margins_and_spacing() -> None:
    from PySide6.QtWidgets import QSizePolicy

    card = _widgets().MetricCard("标题", "1.23", "备注")
    margins = card.layout().contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (12, 10, 12, 10)
    assert card.layout().spacing() == 4
    # the three labels are Ignored/Preferred so long values never widen the card
    for label in (card.value_label, card.note_label):
        assert label.sizePolicy().horizontalPolicy() == QSizePolicy.Ignored
        assert label.sizePolicy().verticalPolicy() == QSizePolicy.Preferred


def test_metric_card_starts_with_the_given_value_and_note() -> None:
    card = _widgets().MetricCard("标题", "1.23", "备注")
    assert card.value_label.text() == "1.23"
    assert card.note_label.text() == "备注"
    assert card.note_label.toolTip() == "备注"


def test_metric_card_set_value_without_note_keeps_the_old_note() -> None:
    card = _widgets().MetricCard("标题", "1.23", "旧备注")
    card.set_value("new")
    assert card.value_label.text() == "new"
    assert card.note_label.text() == "旧备注", "note=None must not clear the note"


def test_metric_card_set_value_with_note_updates_text_and_tooltip() -> None:
    card = _widgets().MetricCard("标题", "1.23", "旧备注")
    card.set_value("new2", "new-note")
    assert card.value_label.text() == "new2"
    assert card.note_label.text() == "new-note"
    assert card.note_label.toolTip() == "new-note"


# --------------------------------------------------------------------------
# PriceChart / EquityComparisonChart
# --------------------------------------------------------------------------


def test_price_chart_keeps_only_the_last_180_points() -> None:
    from datetime import date

    chart = _widgets().PriceChart()
    assert chart.minimumHeight() == 260
    points = tuple((date(2026, 1, 1), float(i)) for i in range(200))
    chart.set_series("AAPL", points, title="AAPL 标题")
    assert len(chart.points) == 180
    assert chart.symbol == "AAPL"
    assert chart.display_title == "AAPL 标题"
    assert chart.points[-1][1] == 199.0


def test_price_chart_keeps_every_point_when_under_the_limit() -> None:
    from datetime import date

    chart = _widgets().PriceChart()
    points = tuple((date(2026, 1, 1), float(i)) for i in range(5))
    chart.set_series("AAPL", points)
    assert len(chart.points) == 5
    assert chart.display_title == ""


def test_price_chart_default_empty_message() -> None:
    assert _widgets().PriceChart().empty_message == "选择扫描结果后显示最近 180 根日 K 收盘曲线"


def test_price_chart_paints_empty_and_populated_series() -> None:
    from datetime import date

    from PySide6.QtGui import QPixmap

    chart = _widgets().PriceChart()
    chart.resize(400, 300)
    pixmap = QPixmap(400, 300)
    chart.render(pixmap)  # empty series must not raise
    chart.set_series(
        "AAPL", tuple((date(2026, 1, 1), 1.0 + i * 0.01) for i in range(200)), title="AAPL"
    )
    chart.render(pixmap)  # populated must not raise


def test_equity_chart_defaults_to_the_spy_series() -> None:
    chart = _widgets().EquityComparisonChart()
    assert chart.rows == []
    assert chart.secondary_key == "spy_equity"
    assert chart.secondary_label == "SPY 整股"
    assert chart.minimumHeight() == 260


def test_equity_chart_switches_to_the_cost_series_when_present() -> None:
    chart = _widgets().EquityComparisonChart()
    chart.set_rows([{"date": "2026-01-01", "strategy_equity": 1.0, "spy_equity": 1.0}])
    assert chart.secondary_key == "spy_equity"
    assert chart.secondary_label == "SPY 整股"

    chart.set_rows(
        [
            {
                "date": "2026-01-01",
                "strategy_equity": 1.0,
                "cost_2x_equity": 1.0,
                "spy_equity": 1.0,
            }
        ]
    )
    assert chart.secondary_key == "cost_2x_equity"
    assert chart.secondary_label == "2×成本"


def test_equity_chart_series_choice_can_switch_back() -> None:
    """The switch must be re-evaluated per call, not latched on first use."""

    chart = _widgets().EquityComparisonChart()
    chart.set_rows([{"date": "2026-01-01", "strategy_equity": 1.0, "cost_2x_equity": 1.0}])
    assert chart.secondary_key == "cost_2x_equity"
    chart.set_rows([{"date": "2026-01-01", "strategy_equity": 1.0, "spy_equity": 1.0}])
    assert chart.secondary_key == "spy_equity"


def test_equity_chart_only_inspects_the_first_row() -> None:
    """The rule is `rows[0]`, not a scan of every row -- keep it that way."""

    chart = _widgets().EquityComparisonChart()
    chart.set_rows(
        [
            {"date": "2026-01-01", "strategy_equity": 1.0, "spy_equity": 1.0},
            {"date": "2026-01-02", "strategy_equity": 1.1, "cost_2x_equity": 1.0},
        ]
    )
    assert chart.secondary_key == "spy_equity", "a later row must not switch the series"
    assert chart.secondary_label == "SPY 整股"


def _method_numeric_literals(path: pathlib.Path, class_name: str, method: str) -> list[float]:
    """Sorted numeric constants of one class's method, via AST (not substring).

    A multiset, not a set: with a set, nudging a live constant onto a value
    that already occurs elsewhere in the method (``105 -> 0``) is invisible.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == method:
                    return sorted(
                        float(n.value)
                        for n in ast.walk(sub)
                        if isinstance(n, ast.Constant)
                        and isinstance(n.value, (int, float))
                        and not isinstance(n.value, bool)
                    )
    raise AssertionError(f"{class_name}.{method} not found")


def test_price_chart_paint_geometry_is_unchanged() -> None:
    """§18: the paint magic numbers are frozen, not to be tuned in passing."""

    literals = _method_numeric_literals(WIDGETS_PATH, "PriceChart", "paintEvent")
    assert literals == [
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0001,
        1.0, 1.0, 1.0, 1.0, 1.0, 2.0, 2.2, 4.0, 5.0, 5.0, 6.0, 6.0, 6.0,
        9.0, 10.0, 10.0, 11.0, 12.0, 12.0, 18.0, 22.0, 30.0, 56.0, 70.0,
        82.0, 82.0, 105.0,
    ]


def test_equity_chart_paint_geometry_is_unchanged() -> None:
    literals = _method_numeric_literals(
        WIDGETS_PATH, "EquityComparisonChart", "paintEvent"
    )
    assert literals == [
        0.0, 0.0, 0.0001, 1.0, 1.0, 1.0, 1.8, 2.0, 2.2, 4.0, 4.0, 5.0,
        8.0, 8.0, 9.0, 10.0, 10.0, 10.0, 12.0, 12.0, 18.0, 22.0, 22.0,
        38.0, 58.0, 76.0, 82.0, 84.0, 105.0,
    ]


def test_chart_paint_uses_painter_primitives_not_a_new_engine() -> None:
    """§16: still raw QPainter -- no charting library may creep in."""

    imported = _imported_modules(WIDGETS_PATH.read_bytes())
    for banned in ("matplotlib", "pyqtgraph", "pandas", "numpy"):
        assert banned not in imported, f"a chart engine crept in: {banned}"
    source = WIDGETS_PATH.read_text(encoding="utf-8")
    for primitive in ("QPainter", "QPainterPath", "QLinearGradient", "QPointF", "QRectF"):
        assert primitive in source, f"paint primitive {primitive} disappeared"


def test_equity_chart_paints_empty_single_and_multi_row_data() -> None:
    from PySide6.QtGui import QPixmap

    chart = _widgets().EquityComparisonChart()
    chart.resize(400, 300)
    pixmap = QPixmap(400, 300)
    chart.render(pixmap)  # 0 rows

    chart.set_rows([{"date": "2026-01-01", "strategy_equity": 1.0, "spy_equity": 1.0}])
    chart.render(pixmap)  # 1 row: still the empty branch

    chart.set_rows(
        [
            {"date": "2026-01-01", "strategy_equity": 1.0, "spy_equity": 1.0},
            {"date": "2026-01-02", "strategy_equity": 1.1, "spy_equity": 1.0},
        ]
    )
    chart.render(pixmap)  # 2 rows: real path
