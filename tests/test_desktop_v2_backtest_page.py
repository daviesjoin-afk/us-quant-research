"""Real-Qt tests for the native BacktestPage."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.research.backtest.models import (
    BacktestChartView,
    BacktestComparisonRow,
    BacktestControlView,
    BacktestDetailView,
    BacktestFormDraft,
    BacktestMetricView,
    BacktestPageView,
    BacktestStrategyOption,
    BacktestSummaryView,
    BacktestTradeRow,
)
from us_quant.desktop_v2.pages.research.backtest.page import BacktestPage
from us_quant.ui_theme import DARK_THEME, LIGHT_THEME


_APP = QApplication.instance() or QApplication([])


def _summary(value: str = "+25.00%") -> BacktestSummaryView:
    return BacktestSummaryView(
        total_return=BacktestMetricView(value, "期末 $1,875.00"),
        cagr=BacktestMetricView("+20.00%", "2025-01-02 → 2025-12-31"),
        sharpe=BacktestMetricView("1.25", "最差日 -3.50%"),
        drawdown=BacktestMetricView("12.00%", "正收益日 57.5%"),
        trades=BacktestMetricView("2", "佣金 $2.50"),
    )


def _comparison(run_id: str) -> BacktestComparisonRow:
    return BacktestComparisonRow(
        run_id=run_id,
        run_id_display=run_id[:8],
        strategy="双均线趋势 · version-",
        symbol="AAPL",
        interval="2025-01-02 → 2025-12-31",
        total_return="+25.00%",
        annualized_return="+20.00%",
        sharpe="1.25",
        sortino="1.75",
        calmar="0.80",
        max_drawdown="12.00%",
        turnover="3.50x",
        trade_count="2",
        commission="$2.50",
    )


def _trade() -> BacktestTradeRow:
    return BacktestTradeRow(
        signal_date="2025-01-02",
        fill_date="2025-01-03",
        signal_symbol="AAPL",
        execution_symbol="AAPL",
        side="买入",
        quantity="5",
        raw_price="$100.1234",
        fill_price="$100.1634",
        slippage_cost="$0.20",
        commission="$1.50",
        position_after="5",
        cash_after="$998.00",
        reason="趋势入场 · 替代映射",
    )


def _view(
    *,
    selected_run_id: str | None = "run-A",
    detail_run_id: str | None = "run-A",
    busy: bool = False,
) -> BacktestPageView:
    return BacktestPageView(
        controls=BacktestControlView(
            run_selected_enabled=not busy,
            compare_all_enabled=not busy,
        ),
        runs=(_comparison("run-A"), _comparison("run-B")),
        selected_run_id=selected_run_id,
        detail=BacktestDetailView(
            run_id=detail_run_id,
            summary=_summary(),
            chart=BacktestChartView(
                "AAPL",
                (
                    (date(2025, 1, 2), 1500.0),
                    (date(2025, 1, 3), 1510.5),
                ),
                "双均线趋势 · 权益曲线 · Run run-A",
            ),
            trades=(_trade(),),
            evidence="数据：IBKR · adjusted；研究代理，不代表历史可成交表现。",
        ),
    )


def _page() -> BacktestPage:
    return BacktestPage(
        per_share_commission=Decimal("0.005"),
        minimum_commission=Decimal("1.25"),
        slippage_bps=Decimal("4.0"),
        palette=DARK_THEME,
    )


def test_page_owns_five_metric_cards_and_workspace_widgets() -> None:
    page = _page()
    assert len(page.metric_cards) == 5
    assert page.controls is not None
    assert page.chart is not None
    assert page.comparison_table is not None
    assert page.trades_table is not None


def test_render_empty_state() -> None:
    page = _page()
    page.render(
        BacktestPageView(
            controls=BacktestControlView(True, True),
            runs=(),
            selected_run_id=None,
            detail=BacktestDetailView(
                run_id=None,
                summary=BacktestSummaryView(
                    BacktestMetricView("—", "运行后显示"),
                    BacktestMetricView("—", "按 252 个交易日估算"),
                    BacktestMetricView("—", "无风险利率暂按 0"),
                    BacktestMetricView("—", "收盘权益序列"),
                    BacktestMetricView("—", "整股、佣金与滑点"),
                ),
                chart=None,
                trades=(),
                evidence="初始证据",
            ),
        )
    )
    assert page.return_card.value_label.text() == "—"
    assert page.comparison_table.rowCount() == 0
    assert page.trades_table.rowCount() == 0
    assert page.chart.points == ()
    assert page.evidence_label.text() == "初始证据"


def test_render_selected_result() -> None:
    page = _page()
    page.render(_view())
    assert page.return_card.value_label.text() == "+25.00%"
    assert page.comparison_table.rowCount() == 2
    assert page.comparison_table.selected_run_id() == "run-A"
    assert page.trades_table.rowCount() == 1
    assert page.chart.symbol == "AAPL"
    assert page.chart.points[-1][1] == 1510.5


def test_selected_comparison_row_emits_full_run_id() -> None:
    page = _page()
    page.render(_view(selected_run_id="run-A"))
    seen: list[str] = []
    page.run_selected.connect(seen.append)
    row = next(
        index
        for index in range(page.comparison_table.rowCount())
        if page.comparison_table.item(index, 0).data(Qt.UserRole) == "run-B"
    )
    page.comparison_table.selectRow(row)
    assert seen == ["run-B"]


def test_controls_forward_form_intent() -> None:
    page = _page()
    page.set_strategy_options(
        (BacktestStrategyOption("version-A", "A · 1.0.0"),)
    )
    selected: list[BacktestFormDraft] = []
    compared: list[BacktestFormDraft] = []
    page.run_selected_requested.connect(selected.append)
    page.compare_all_requested.connect(compared.append)
    page.controls.run_selected_button.click()
    page.controls.compare_all_button.click()
    assert selected[0].strategy_version_id == "version-A"
    assert compared[0].strategy_version_id == "version-A"


def test_busy_render_disables_only_run_buttons() -> None:
    page = _page()
    page.render(_view(busy=True))
    assert page.controls.run_selected_button.isEnabled() is False
    assert page.controls.compare_all_button.isEnabled() is False
    assert page.controls.symbol_input.isEnabled() is True


def test_set_palette_preserves_selection_without_run_intent() -> None:
    page = _page()
    page.render(_view(selected_run_id="run-B"))
    emitted: list[str] = []
    page.run_selected.connect(emitted.append)
    page.set_palette(LIGHT_THEME)
    assert page.comparison_table.selected_run_id() == "run-B"
    assert emitted == []
