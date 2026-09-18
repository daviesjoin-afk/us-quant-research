"""Coverage for the presentation layer that moved out of ``desktop.py``.

Four widgets and two formatting helpers used to live in the 9.9k-line
window module.  Moving them is only worth something if the rendering
rules still hold, so these tests pin the behaviour that the extraction
froze: the exact quote-grid columns, the incremental-versus-reset update
rule, numeric sorting, the READY/STALE colour columns, and the two
helpers' text output.

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

from us_quant.trading.domain.market import MarketDataMode


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
    "QuoteTableModel",
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
    "us_quant.trading.domain.market",
    "us_quant.ui_theme",
}

ALLOWED_QT_NAMES = {
    "QAbstractTableModel",
    "QModelIndex",
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


def _quote(symbol: str, **overrides):
    from datetime import datetime, timezone
    from decimal import Decimal

    from us_quant.trading.domain.market import (
        MarketDataMode,
        MarketQuote,
    )

    base = dict(
        symbol=symbol,
        bid=Decimal("1.00"),
        ask=Decimal("1.10"),
        last=Decimal("1.05"),
        close=Decimal("1.00"),
        bid_size=None,
        ask_size=None,
        mode=MarketDataMode.DELAYED,
        updated_at=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc),
        age_seconds=1.5,
        stale=False,
        stale_reason=None,
        generation=1,
        source_id="test_feed",
        source_label="TestFeed",
        coverage="unit-test",
    )
    base.update(overrides)
    return MarketQuote(**base)


def _snapshot(quotes):
    from datetime import datetime, timezone

    from us_quant.trading.domain.market import MarketSnapshot

    return MarketSnapshot(
        generation=1,
        connected=True,
        ready=True,
        reconnect_attempt=0,
        quotes=tuple(quotes),
        error_code=None,
        message="",
        observed_at=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc),
        source_id="test_feed",
        source_label="TestFeed",
        coverage="unit-test",
    )


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
# QuoteTableModel
# --------------------------------------------------------------------------


def test_quote_table_headers_are_exactly_the_fourteen_columns() -> None:
    assert _widgets().QuoteTableModel.HEADERS == (
        "代码",
        "Bid",
        "Ask",
        "Last",
        "Close",
        "点差",
        "有效类型",
        "更新时间",
        "Age(s)",
        "代次",
        "来源",
        "覆盖",
        "状态",
        "原因",
    )


def test_quote_table_reports_the_header_row() -> None:
    from PySide6.QtCore import Qt

    model = _widgets().QuoteTableModel()
    assert model.columnCount() == 14
    # every section, not just the first: ``HEADERS[0]`` passes a section-0-only
    # check, so the whole tuple has to be walked.
    for section, expected in enumerate(model.HEADERS):
        assert model.headerData(section, Qt.Horizontal, Qt.DisplayRole) == expected
    assert model.headerData(0, Qt.Vertical, Qt.DisplayRole) is None
    assert model.headerData(0, Qt.Horizontal, Qt.ToolTipRole) is None


def test_quote_table_first_update_resets_and_fills_every_column() -> None:
    model = _widgets().QuoteTableModel()
    assert model.rowCount() == 0
    assert model.reset_count == 0
    assert model.changed_row_count == 0
    model.update_snapshot(_snapshot([_quote("AAPL")]))
    assert model.rowCount() == 1
    assert model.reset_count == 1
    assert model.changed_row_count == 0, "a reset is not an incremental change"
    for column in range(len(model.HEADERS)):
        assert model.index(0, column).data() is not None, f"column {column} empty"


def test_quote_table_same_symbols_only_patches_changed_rows() -> None:
    model = _widgets().QuoteTableModel()
    model.update_snapshot(_snapshot([_quote("AAPL"), _quote("MSFT")]))
    assert model.reset_count == 1
    after_first = model.changed_row_count

    # two rows change, not one: counting rows instead of *changes* still
    # satisfies a single-row fixture.
    model.update_snapshot(
        _snapshot(
            [
                _quote("AAPL", last=_dec("9.99")),
                _quote("MSFT", last=_dec("11.11")),
            ]
        )
    )
    assert model.reset_count == 1, "an identical symbol set must not reset"
    assert model.changed_row_count - after_first == 2, "both rows actually changed"


def test_quote_table_untouched_rows_keep_their_object_identity() -> None:
    """An incremental patch must not rebuild rows that did not change."""

    model = _widgets().QuoteTableModel()
    model.update_snapshot(_snapshot([_quote("AAPL"), _quote("MSFT")]))
    msft_before = model._rows[1]
    model.update_snapshot(_snapshot([_quote("AAPL", last=_dec("9.99")), _quote("MSFT")]))
    assert model._rows[1] is msft_before, "MSFT row was rebuilt by an AAPL-only change"


def test_quote_table_identical_snapshot_changes_nothing() -> None:
    model = _widgets().QuoteTableModel()
    snapshot = _snapshot([_quote("AAPL"), _quote("MSFT")])
    model.update_snapshot(snapshot)
    after_first = model.changed_row_count
    model.update_snapshot(snapshot)
    assert model.changed_row_count == after_first, "no-op update must not emit changes"


def test_quote_table_changed_symbol_set_forces_a_reset() -> None:
    model = _widgets().QuoteTableModel()
    model.update_snapshot(_snapshot([_quote("AAPL"), _quote("MSFT")]))
    assert model.reset_count == 1
    model.update_snapshot(_snapshot([_quote("AAPL"), _quote("NVDA")]))
    assert model.reset_count == 2


def test_quote_table_keeps_existing_row_order() -> None:
    """A reordered snapshot must not reorder the grid."""

    model = _widgets().QuoteTableModel()
    model.update_snapshot(_snapshot([_quote("AAPL"), _quote("MSFT")]))
    model.update_snapshot(_snapshot([_quote("MSFT"), _quote("AAPL")]))
    symbols = [model.index(row, 0).data() for row in range(model.rowCount())]
    assert symbols == ["AAPL", "MSFT"]


def test_quote_table_sorts_numerically_not_lexically() -> None:
    from PySide6.QtCore import Qt

    model = _widgets().QuoteTableModel()
    model.update_snapshot(
        _snapshot(
            [
                _quote("A", last=_dec("9.0")),
                _quote("B", last=_dec("100.0")),
                _quote("C", last=_dec("20.0")),
            ]
        )
    )
    model.sort(3, Qt.AscendingOrder)
    values = [model.index(row, 3).data() for row in range(model.rowCount())]
    numeric = [_widgets()._sortable_number(v) for v in values]
    assert numeric == sorted(numeric), f"lexical sort leaked in: {values}"
    assert values == ["9", "20", "100"]


def test_quote_table_descending_sort_reverses_the_order() -> None:
    from PySide6.QtCore import Qt

    model = _widgets().QuoteTableModel()
    model.update_snapshot(
        _snapshot(
            [
                _quote("A", last=_dec("9.0")),
                _quote("B", last=_dec("100.0")),
                _quote("C", last=_dec("20.0")),
            ]
        )
    )
    model.sort(3, Qt.DescendingOrder)
    values = [model.index(row, 3).data() for row in range(model.rowCount())]
    assert values == ["100", "20", "9"]


def test_quote_table_colours_exactly_the_documented_columns() -> None:
    from PySide6.QtCore import Qt

    model = _widgets().QuoteTableModel()
    # a realtime-ready quote: effective type 1, live bid/ask, not stale
    ready = _quote(
        "AAPL", mode=MarketDataMode.REALTIME, stale=False
    )
    model.update_snapshot(_snapshot([ready]))
    assert model.index(0, 12).data() == "READY"
    for column in range(len(model.HEADERS)):
        colour = model.index(0, column).data(Qt.ForegroundRole)
        if column in (0, 6, 10, 12):
            assert colour is not None, f"column {column} should be coloured"
        else:
            assert colour is None, f"column {column} must not be coloured"


def test_quote_table_colours_column_thirteen_only_when_stale() -> None:
    from PySide6.QtCore import Qt

    model = _widgets().QuoteTableModel()
    model.update_snapshot(_snapshot([_quote("AAPL", stale=True, stale_reason="订阅失效")]))
    assert model.index(0, 12).data() == "STALE"
    assert model.index(0, 13).data(Qt.ForegroundRole) is not None, "stale reason coloured"

    model.update_snapshot(
        _snapshot(
            [
                _quote(
                    "AAPL",
                    mode=MarketDataMode.REALTIME,
                    stale=False,
                )
            ]
        )
    )
    assert model.index(0, 12).data() == "READY"
    assert model.index(0, 13).data(Qt.ForegroundRole) is None


def test_quote_table_stale_colour_comes_from_the_palette_error_slot() -> None:
    from PySide6.QtCore import Qt

    from us_quant.ui_theme import theme_palette

    model = _widgets().QuoteTableModel()
    model.update_snapshot(_snapshot([_quote("AAPL", stale=True, stale_reason="订阅失效")]))
    colour = model.index(0, 0).data(Qt.ForegroundRole)
    assert colour.name().lower() == theme_palette("dark").error.lower()


def test_quote_table_ready_colour_comes_from_the_palette_success_slot() -> None:
    from PySide6.QtCore import Qt

    from us_quant.ui_theme import theme_palette

    model = _widgets().QuoteTableModel()
    model.update_snapshot(
        _snapshot(
            [
                _quote(
                    "AAPL",
                    mode=MarketDataMode.REALTIME,
                    stale=False,
                )
            ]
        )
    )
    colour = model.index(0, 0).data(Qt.ForegroundRole)
    assert colour.name().lower() == theme_palette("dark").success.lower()


def test_quote_table_set_theme_swaps_the_palette() -> None:
    from PySide6.QtCore import Qt

    from us_quant.ui_theme import theme_palette

    model = _widgets().QuoteTableModel()
    model.update_snapshot(_snapshot([_quote("AAPL", stale=True, stale_reason="订阅失效")]))
    assert model.index(0, 0).data(Qt.ForegroundRole).name().lower() == (
        theme_palette("dark").error.lower()
    )
    model.set_theme("light")
    colour = model.index(0, 0).data(Qt.ForegroundRole)
    assert colour.name().lower() == theme_palette("light").error.lower()
    assert colour.name().lower() != theme_palette("dark").error.lower()


def test_quote_table_sorts_percentage_text_numerically() -> None:
    """A percentage column must sort by value, not by the literal string."""

    from PySide6.QtCore import Qt

    model = _widgets().QuoteTableModel()
    model.update_snapshot(
        _snapshot(
            [
                _quote("A", stale_reason="9.00%"),
                _quote("B", stale_reason="100.00%"),
                _quote("C", stale_reason="20.00%"),
            ]
        )
    )
    model.sort(13, Qt.AscendingOrder)
    values = [model.index(row, 13).data() for row in range(model.rowCount())]
    assert values == ["9.00%", "20.00%", "100.00%"]


def test_quote_table_sorts_non_numeric_text_case_insensitively() -> None:
    from PySide6.QtCore import Qt

    model = _widgets().QuoteTableModel()
    # ``apple`` must land before ``Banana``: a case-sensitive sort would put
    # the capital first (``B`` < ``a``) and silently pass a friendlier fixture.
    model.update_snapshot(
        _snapshot(
            [
                _quote("A", source_label="cherry"),
                _quote("B", source_label="Banana"),
                _quote("C", source_label="apple"),
            ]
        )
    )
    model.sort(10, Qt.AscendingOrder)
    values = [model.index(row, 10).data() for row in range(model.rowCount())]
    assert values == ["apple", "Banana", "cherry"]


def test_quote_table_sort_puts_unparseable_values_last() -> None:
    from PySide6.QtCore import Qt

    model = _widgets().QuoteTableModel()
    model.update_snapshot(
        _snapshot(
            [
                _quote("A", last=_dec("5.0")),
                _quote("B", last=None),
                _quote("C", last=_dec("1.0")),
            ]
        )
    )
    model.sort(3, Qt.AscendingOrder)
    values = [model.index(row, 3).data() for row in range(model.rowCount())]
    assert values == ["1", "5", "—"], "the None bucket must sort last"


def test_quote_table_sorting_before_any_data_is_safe() -> None:
    from PySide6.QtCore import Qt

    model = _widgets().QuoteTableModel()
    model.sort(3, Qt.AscendingOrder)  # no rows yet
    assert model.rowCount() == 0


def test_public_attributes_survive_the_move() -> None:
    """§59: MainWindow and tests read these directly."""

    from datetime import date

    widgets = _widgets()

    model = widgets.QuoteTableModel()
    assert isinstance(model.reset_count, int)
    assert isinstance(model.changed_row_count, int)

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


def test_quote_table_invalid_index_returns_none() -> None:
    from PySide6.QtCore import QModelIndex

    model = _widgets().QuoteTableModel()
    model.update_snapshot(_snapshot([_quote("AAPL")]))
    assert model.data(QModelIndex()) is None
    # an invalid parent means "top level", so the full row count is correct
    assert model.rowCount(QModelIndex()) == 1
    assert model.columnCount(QModelIndex()) == 14
    # a *valid* parent means "no children below a cell"
    assert model.rowCount(model.index(0, 0)) == 0
    assert model.columnCount(model.index(0, 0)) == 0


def _dec(text: str):
    from decimal import Decimal

    return Decimal(text)


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
