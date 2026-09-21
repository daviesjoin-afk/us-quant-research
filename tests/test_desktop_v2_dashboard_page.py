"""Dashboard page ownership, rendering and theme-repaint tests."""

from __future__ import annotations

import os
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.dashboard.models import (
    DashboardArtifactRowView,
    DashboardArtifactTone,
    DashboardChartView,
    DashboardMetricView,
    DashboardView,
)
from us_quant.desktop_v2.pages.dashboard.page import DashboardPage
from us_quant.ui_theme import theme_palette


_APP = QApplication.instance() or QApplication([])


def _view() -> DashboardView:
    return DashboardView(
        net_liquidation=DashboardMetricView("$12,345.67", "IBKR Paper"),
        daily_pnl=DashboardMetricView("+$12.50", "券商 reqPnL"),
        positions=DashboardMetricView("2", "当前券商持仓"),
        intraday_market=DashboardMetricView("可用", "Alpaca IEX"),
        artifacts=(
            DashboardArtifactRowView(
                "market_scan",
                "旧结果·已失效",
                "2026-09-18",
                "2026-09-19T00:00:00Z",
                "local",
                "1234567890ab",
                "a；b",
                DashboardArtifactTone.WARNING,
            ),
            DashboardArtifactRowView(
                "other",
                "读取失败",
                "未知",
                "未知",
                "unavailable",
                "error",
                "bad",
                DashboardArtifactTone.ERROR,
            ),
        ),
        chart=DashboardChartView(
            "SPY", ((date(2026, 9, 18), 10.0), (date(2026, 9, 19), 11.0))
        ),
        research_boundary_text="边界文本",
    )


def _page() -> DashboardPage:
    return DashboardPage(palette=theme_palette("dark"))


def _artifact_tone_view() -> DashboardView:
    """Three artifacts whose initial order deliberately differs from sorting."""

    return DashboardView(
        net_liquidation=DashboardMetricView("未读取", "刷新"),
        daily_pnl=DashboardMetricView("不可用", "不会代替"),
        positions=DashboardMetricView("未读取", "严格区分"),
        intraday_market=DashboardMetricView("不可用", "尚未启动"),
        artifacts=(
            DashboardArtifactRowView(
                "z_neutral",
                "探索性研究",
                "未知",
                "未知",
                "local",
                "neutral",
                "无",
                DashboardArtifactTone.NEUTRAL,
            ),
            DashboardArtifactRowView(
                "a_warning",
                "旧结果·已失效",
                "2026-09-18",
                "2026-09-19T00:00:00Z",
                "local",
                "warning",
                "legacy",
                DashboardArtifactTone.WARNING,
            ),
            DashboardArtifactRowView(
                "m_error",
                "读取失败",
                "未知",
                "未知",
                "unavailable",
                "error",
                "bad",
                DashboardArtifactTone.ERROR,
            ),
        ),
        chart=DashboardChartView(None, ()),
        research_boundary_text="边界文本",
    )


def _row_by_artifact_type(table, artifact_type: str) -> int:
    rows = [
        row
        for row in range(table.rowCount())
        if table.item(row, 0).text() == artifact_type
    ]
    assert len(rows) == 1, (artifact_type, rows)
    return rows[0]


def _colour_name(table, row: int, column: int) -> str:
    return table.item(row, column).foreground().color().name()


def test_page_owns_widgets_and_exposes_no_widget_surface() -> None:
    page = _page()
    try:
        assert page._net_liquidation_card is not None
        assert page._artifact_table.rowCount() == 0
        assert page._chart.symbol == ""
        assert page._notes.toPlainText() == ""
        for public_name in (
            "artifact_table",
            "chart",
            "notes",
            "account_card",
        ):
            assert not hasattr(page, public_name)
    finally:
        page.deleteLater()


def test_render_writes_every_view_fact_to_page_widgets() -> None:
    page = _page()
    try:
        page.render(_view())
        assert page._net_liquidation_card.value_label.text() == "$12,345.67"
        assert page._daily_pnl_card.value_label.text() == "+$12.50"
        assert page._positions_card.value_label.text() == "2"
        assert page._intraday_market_card.value_label.text() == "可用"
        assert page._artifact_table.rowCount() == 2
        artifact_types = {
            page._artifact_table.item(row, 0).text()
            for row in range(page._artifact_table.rowCount())
        }
        assert artifact_types == {"market_scan", "other"}
        statuses = {
            page._artifact_table.item(row, 1).text()
            for row in range(page._artifact_table.rowCount())
        }
        assert statuses == {"旧结果·已失效", "读取失败"}
        assert page._chart.symbol == "SPY"
        assert page._chart.points[-1] == (date(2026, 9, 19), 11.0)
        assert page._notes.toPlainText() == "边界文本"
    finally:
        page.deleteLater()


def test_tone_follows_artifact_after_sort_and_palette_change() -> None:
    page = _page()
    try:
        page.render(_artifact_tone_view())
        table = page._artifact_table
        table.sortItems(0, Qt.SortOrder.AscendingOrder)
        order_before = tuple(
            table.item(row, 0).text() for row in range(table.rowCount())
        )
        assert order_before == ("a_warning", "m_error", "z_neutral")

        warning_row = _row_by_artifact_type(table, "a_warning")
        error_row = _row_by_artifact_type(table, "m_error")
        neutral_row = _row_by_artifact_type(table, "z_neutral")
        assert table.item(warning_row, 1).text() == "旧结果·已失效"
        assert table.item(error_row, 1).text() == "读取失败"
        assert table.item(neutral_row, 1).text() == "探索性研究"

        dark = theme_palette("dark")
        assert _colour_name(table, warning_row, 0) == QColor(dark.warning).name()
        assert _colour_name(table, warning_row, 1) == QColor(dark.warning).name()
        assert _colour_name(table, warning_row, 2) != QColor(dark.warning).name()
        for column in range(table.columnCount()):
            assert _colour_name(table, error_row, column) == QColor(
                dark.error
            ).name()
        assert _colour_name(table, neutral_row, 0) not in {
            QColor(dark.warning).name(),
            QColor(dark.error).name(),
        }

        page.set_palette(theme_palette("light"))
        order_after = tuple(
            table.item(row, 0).text() for row in range(table.rowCount())
        )
        assert order_after == order_before
        light = theme_palette("light")
        assert _colour_name(table, warning_row, 0) == QColor(
            light.warning
        ).name()
        assert _colour_name(table, warning_row, 1) == QColor(
            light.warning
        ).name()
        for column in range(table.columnCount()):
            assert _colour_name(table, error_row, column) == QColor(
                light.error
            ).name()
        assert _colour_name(table, neutral_row, 0) not in {
            QColor(light.warning).name(),
            QColor(light.error).name(),
        }
    finally:
        page.deleteLater()


def test_gateway_button_only_emits_the_intent_signal() -> None:
    page = _page()
    fired: list[int] = []
    page.gateway_probe_requested.connect(lambda: fired.append(1))
    try:
        page._gateway_button.click()
        assert fired == [1]
    finally:
        page.deleteLater()


def test_palette_change_recolours_rows_without_changing_content() -> None:
    page = _page()
    emitted: list[int] = []
    page.gateway_probe_requested.connect(lambda: emitted.append(1))
    try:
        page.render(_view())
        before = tuple(
            page._artifact_table.item(row, column).text()
            for row in range(page._artifact_table.rowCount())
            for column in range(page._artifact_table.columnCount())
        )
        page.set_palette(theme_palette("light"))
        after = tuple(
            page._artifact_table.item(row, column).text()
            for row in range(page._artifact_table.rowCount())
            for column in range(page._artifact_table.columnCount())
        )
        assert after == before
        assert emitted == []
        table = page._artifact_table
        warning_row = _row_by_artifact_type(table, "market_scan")
        error_row = _row_by_artifact_type(table, "other")
        warning = QColor(theme_palette("light").warning).name()
        error = QColor(theme_palette("light").error).name()
        assert _colour_name(table, warning_row, 0) == warning
        assert _colour_name(table, warning_row, 1) == warning
        assert _colour_name(table, warning_row, 2) != warning
        for column in range(table.columnCount()):
            assert _colour_name(table, error_row, column) == error
    finally:
        page.deleteLater()
