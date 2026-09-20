"""Coverage for the market route's quote table.

These tests moved with ``QuoteTableModel`` from ``desktop_widgets`` to
``desktop_v2/pages/market/tables.py``, where the model became the market
route's alone.  They still pin the same behaviour an operator depends on,
and that behaviour is the reason the model is not a plain
``ImmutableRowsTableModel``:

* a changed symbol set resets, while the same symbols updating in place
  patch row by row.  A grid that reset every tick would lose the
  operator's selection and scroll position;
* the user's sort survives those patches, and numeric columns sort
  numerically rather than lexically;
* the READY/STALE tint covers exactly the documented columns, and it
  comes from the palette the window hands over.

The model is fed presentation rows now rather than a ``MarketSnapshot``:
interpreting the mode, the readiness flag and the stale flag is the
Qt-free ``rows`` module's job, so a fixture here is a snapshot plus the
one call that projects it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.market.rows import quote_rows
from us_quant.desktop_v2.pages.market.tables import QuoteTableModel
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)


_APP = QApplication.instance() or QApplication([])


def _dec(text: str) -> Decimal:
    return Decimal(text)


def _quote(symbol: str, **overrides):
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


def _rows(*quotes):
    return quote_rows(_snapshot(list(quotes)))


def test_quote_table_headers_are_exactly_the_fourteen_columns() -> None:
    assert QuoteTableModel.HEADERS == (
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

    model = QuoteTableModel()
    assert model.columnCount() == 14
    # every section, not just the first: ``HEADERS[0]`` passes a section-0-only
    # check, so the whole tuple has to be walked.
    for section, expected in enumerate(model.HEADERS):
        assert model.headerData(section, Qt.Horizontal, Qt.DisplayRole) == expected
    assert model.headerData(0, Qt.Vertical, Qt.DisplayRole) is None
    assert model.headerData(0, Qt.Horizontal, Qt.ToolTipRole) is None


def test_quote_table_first_update_resets_and_fills_every_column() -> None:
    model = QuoteTableModel()
    assert model.rowCount() == 0
    assert model.reset_count == 0
    assert model.changed_row_count == 0
    model.update_rows(_rows(_quote("AAPL")))
    assert model.rowCount() == 1
    assert model.reset_count == 1
    assert model.changed_row_count == 0, "a reset is not an incremental change"
    for column in range(len(model.HEADERS)):
        assert model.index(0, column).data() is not None, f"column {column} empty"


def test_quote_table_same_symbols_only_patches_changed_rows() -> None:
    model = QuoteTableModel()
    model.update_rows(_rows(_quote("AAPL"), _quote("MSFT")))
    assert model.reset_count == 1
    after_first = model.changed_row_count

    # two rows change, not one: counting rows instead of *changes* still
    # satisfies a single-row fixture.
    model.update_rows(
        _rows(
            _quote("AAPL", last=_dec("9.99")),
            _quote("MSFT", last=_dec("11.11")),
        )
    )
    assert model.reset_count == 1, "an identical symbol set must not reset"
    assert model.changed_row_count - after_first == 2, "both rows actually changed"


def test_quote_table_untouched_rows_keep_their_object_identity() -> None:
    """An incremental patch must not rebuild rows that did not change."""

    model = QuoteTableModel()
    model.update_rows(_rows(_quote("AAPL"), _quote("MSFT")))
    msft_before = model._rows[1]
    model.update_rows(_rows(_quote("AAPL", last=_dec("9.99")), _quote("MSFT")))
    assert model._rows[1] is msft_before, "MSFT row was rebuilt by an AAPL-only change"


def test_quote_table_identical_snapshot_changes_nothing() -> None:
    model = QuoteTableModel()
    rows = _rows(_quote("AAPL"), _quote("MSFT"))
    model.update_rows(rows)
    after_first = model.changed_row_count
    model.update_rows(rows)
    assert model.changed_row_count == after_first, "no-op update must not emit changes"


def test_quote_table_changed_symbol_set_forces_a_reset() -> None:
    model = QuoteTableModel()
    model.update_rows(_rows(_quote("AAPL"), _quote("MSFT")))
    assert model.reset_count == 1
    model.update_rows(_rows(_quote("AAPL"), _quote("NVDA")))
    assert model.reset_count == 2


def test_quote_table_keeps_existing_row_order() -> None:
    """A reordered snapshot must not reorder the grid."""

    model = QuoteTableModel()
    model.update_rows(_rows(_quote("AAPL"), _quote("MSFT")))
    model.update_rows(_rows(_quote("MSFT"), _quote("AAPL")))
    symbols = [model.index(row, 0).data() for row in range(model.rowCount())]
    assert symbols == ["AAPL", "MSFT"]


def test_quote_table_sorts_numerically_not_lexically() -> None:
    from PySide6.QtCore import Qt

    from us_quant.desktop_widgets import _sortable_number

    model = QuoteTableModel()
    model.update_rows(
        _rows(
            _quote("A", last=_dec("9.0")),
            _quote("B", last=_dec("100.0")),
            _quote("C", last=_dec("20.0")),
        )
    )
    model.sort(3, Qt.AscendingOrder)
    values = [model.index(row, 3).data() for row in range(model.rowCount())]
    numeric = [_sortable_number(v) for v in values]
    assert numeric == sorted(numeric), f"lexical sort leaked in: {values}"
    assert values == ["9", "20", "100"]


def test_quote_table_descending_sort_reverses_the_order() -> None:
    from PySide6.QtCore import Qt

    model = QuoteTableModel()
    model.update_rows(
        _rows(
            _quote("A", last=_dec("9.0")),
            _quote("B", last=_dec("100.0")),
            _quote("C", last=_dec("20.0")),
        )
    )
    model.sort(3, Qt.DescendingOrder)
    values = [model.index(row, 3).data() for row in range(model.rowCount())]
    assert values == ["100", "20", "9"]


def test_quote_table_colours_exactly_the_documented_columns() -> None:
    from PySide6.QtCore import Qt

    model = QuoteTableModel()
    # a realtime-ready quote: effective type 1, live bid/ask, not stale
    ready = _quote(
        "AAPL", mode=MarketDataMode.REALTIME, stale=False
    )
    model.update_rows(_rows(ready))
    assert model.index(0, 12).data() == "READY"
    for column in range(len(model.HEADERS)):
        colour = model.index(0, column).data(Qt.ForegroundRole)
        if column in (0, 6, 10, 12):
            assert colour is not None, f"column {column} should be coloured"
        else:
            assert colour is None, f"column {column} must not be coloured"


def test_quote_table_colours_column_thirteen_only_when_stale() -> None:
    from PySide6.QtCore import Qt

    model = QuoteTableModel()
    model.update_rows(
        _rows(_quote("AAPL", stale=True, stale_reason="订阅失效"))
    )
    assert model.index(0, 12).data() == "STALE"
    assert model.index(0, 13).data(Qt.ForegroundRole) is not None, "stale reason coloured"

    model.update_rows(
        _rows(
            _quote(
                "AAPL",
                mode=MarketDataMode.REALTIME,
                stale=False,
            )
        )
    )
    assert model.index(0, 12).data() == "READY"
    assert model.index(0, 13).data(Qt.ForegroundRole) is None


def test_quote_table_stale_colour_comes_from_the_palette_error_slot() -> None:
    from PySide6.QtCore import Qt

    from us_quant.ui_theme import theme_palette

    model = QuoteTableModel()
    model.update_rows(
        _rows(_quote("AAPL", stale=True, stale_reason="订阅失效"))
    )
    colour = model.index(0, 0).data(Qt.ForegroundRole)
    assert colour.name().lower() == theme_palette("dark").error.lower()


def test_quote_table_ready_colour_comes_from_the_palette_success_slot() -> None:
    from PySide6.QtCore import Qt

    from us_quant.ui_theme import theme_palette

    model = QuoteTableModel()
    model.update_rows(
        _rows(
            _quote(
                "AAPL",
                mode=MarketDataMode.REALTIME,
                stale=False,
            )
        )
    )
    colour = model.index(0, 0).data(Qt.ForegroundRole)
    assert colour.name().lower() == theme_palette("dark").success.lower()


def test_quote_table_set_palette_swaps_the_colour() -> None:
    from PySide6.QtCore import Qt

    from us_quant.ui_theme import theme_palette

    model = QuoteTableModel()
    model.update_rows(
        _rows(_quote("AAPL", stale=True, stale_reason="订阅失效"))
    )
    assert model.index(0, 0).data(Qt.ForegroundRole).name().lower() == (
        theme_palette("dark").error.lower()
    )
    model.set_palette(theme_palette("light"))
    colour = model.index(0, 0).data(Qt.ForegroundRole)
    assert colour.name().lower() == theme_palette("light").error.lower()
    assert colour.name().lower() != theme_palette("dark").error.lower()


def test_quote_table_sorts_percentage_text_numerically() -> None:
    """A percentage column must sort by value, not by the literal string."""

    from PySide6.QtCore import Qt

    model = QuoteTableModel()
    model.update_rows(
        _rows(
            _quote("A", stale_reason="9.00%"),
            _quote("B", stale_reason="100.00%"),
            _quote("C", stale_reason="20.00%"),
        )
    )
    model.sort(13, Qt.AscendingOrder)
    values = [model.index(row, 13).data() for row in range(model.rowCount())]
    assert values == ["9.00%", "20.00%", "100.00%"]


def test_quote_table_sorts_non_numeric_text_case_insensitively() -> None:
    from PySide6.QtCore import Qt

    model = QuoteTableModel()
    # ``apple`` must land before ``Banana``: a case-sensitive sort would put
    # the capital first (``B`` < ``a``) and silently pass a friendlier fixture.
    model.update_rows(
        _rows(
            _quote("A", source_label="cherry"),
            _quote("B", source_label="Banana"),
            _quote("C", source_label="apple"),
        )
    )
    model.sort(10, Qt.AscendingOrder)
    values = [model.index(row, 10).data() for row in range(model.rowCount())]
    assert values == ["apple", "Banana", "cherry"]


def test_quote_table_sort_puts_unparseable_values_last() -> None:
    from PySide6.QtCore import Qt

    model = QuoteTableModel()
    model.update_rows(
        _rows(
            _quote("A", last=_dec("5.0")),
            _quote("B", last=None),
            _quote("C", last=_dec("1.0")),
        )
    )
    model.sort(3, Qt.AscendingOrder)
    values = [model.index(row, 3).data() for row in range(model.rowCount())]
    assert values == ["1", "5", "—"], "the None bucket must sort last"


def test_quote_table_sorting_before_any_data_is_safe() -> None:
    from PySide6.QtCore import Qt

    model = QuoteTableModel()
    model.sort(3, Qt.AscendingOrder)  # no rows yet
    assert model.rowCount() == 0


def test_quote_table_invalid_index_returns_none() -> None:
    from PySide6.QtCore import QModelIndex

    model = QuoteTableModel()
    model.update_rows(_rows(_quote("AAPL")))
    assert model.data(QModelIndex()) is None
    # an invalid parent means "top level", so the full row count is correct
    assert model.rowCount(QModelIndex()) == 1
    assert model.columnCount(QModelIndex()) == 14
    # a *valid* parent means "no children below a cell"
    assert model.rowCount(model.index(0, 0)) == 0
    assert model.columnCount(model.index(0, 0)) == 0


def test_the_counter_attributes_the_page_reads_still_exist() -> None:
    model = QuoteTableModel()
    assert isinstance(model.reset_count, int)
    assert isinstance(model.changed_row_count, int)
