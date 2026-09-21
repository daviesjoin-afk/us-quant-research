"""Native Desktop UI v2 backtest page."""

from __future__ import annotations

from decimal import Decimal
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.research.backtest.controls import BacktestControls
from us_quant.desktop_v2.pages.research.backtest.models import (
    BacktestFormDraft,
    BacktestPageView,
    BacktestStrategyOption,
)
from us_quant.desktop_v2.pages.research.backtest.tables import (
    BacktestComparisonTable,
    BacktestTradesTable,
)
from us_quant.desktop_widgets import MetricCard, PriceChart
from us_quant.ui_theme import ThemePalette, theme_palette


class BacktestPage(QWidget):
    """Renders backtest facts and emits operator intent."""

    run_selected_requested = Signal(object)
    compare_all_requested = Signal(object)
    run_selected = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        per_share_commission: Decimal | float | int,
        minimum_commission: Decimal | float | int,
        slippage_bps: Decimal | float | int,
        palette: ThemePalette | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette or theme_palette("dark")
        self._build(
            per_share_commission=per_share_commission,
            minimum_commission=minimum_commission,
            slippage_bps=slippage_bps,
        )

    def _build(
        self,
        *,
        per_share_commission: Decimal | float | int,
        minimum_commission: Decimal | float | int,
        slippage_bps: Decimal | float | int,
    ) -> None:
        layout = QVBoxLayout(self)
        cards = QHBoxLayout()
        self.return_card = MetricCard("总收益", "—", "运行后显示")
        self.cagr_card = MetricCard(
            "年化收益", "—", "按 252 个交易日估算"
        )
        self.sharpe_card = MetricCard(
            "年化 Sharpe", "—", "无风险利率暂按 0"
        )
        self.drawdown_card = MetricCard(
            "最大回撤", "—", "收盘权益序列"
        )
        self.trade_card = MetricCard(
            "交易与成本", "—", "整股、佣金与滑点"
        )
        self.metric_cards = (
            self.return_card,
            self.cagr_card,
            self.sharpe_card,
            self.drawdown_card,
            self.trade_card,
        )
        for card in self.metric_cards:
            cards.addWidget(card)
        layout.addLayout(cards)

        self.controls = BacktestControls(
            per_share_commission=per_share_commission,
            minimum_commission=minimum_commission,
            slippage_bps=slippage_bps,
        )
        self.controls.run_selected_requested.connect(
            self.run_selected_requested.emit
        )
        self.controls.compare_all_requested.connect(
            self.compare_all_requested.emit
        )
        layout.addWidget(self.controls)

        self.evidence_label = QLabel(
            "研究代理：信号在收盘生成、次日开盘成交；默认复权日 K，"
            "不等于历史可执行整股成交。"
        )
        self.evidence_label.setObjectName("subtitle")
        self.evidence_label.setWordWrap(True)
        layout.addWidget(self.evidence_label)

        splitter = QSplitter(Qt.Vertical)
        self.chart = PriceChart()
        self.chart.empty_message = "运行回测后显示最近 180 个交易日权益曲线"
        splitter.addWidget(self.chart)
        tables = QSplitter(Qt.Horizontal)
        self.comparison_table = BacktestComparisonTable()
        self.comparison_table.run_selected.connect(self.run_selected.emit)
        self.trades_table = BacktestTradesTable()
        tables.addWidget(self.comparison_table)
        tables.addWidget(self.trades_table)
        tables.setSizes([840, 520])
        splitter.addWidget(tables)
        splitter.setSizes([300, 330])
        layout.addWidget(splitter)

    def render(self, view: BacktestPageView) -> None:
        summary = view.detail.summary
        self.return_card.set_value(
            summary.total_return.value, summary.total_return.note
        )
        self.cagr_card.set_value(summary.cagr.value, summary.cagr.note)
        self.sharpe_card.set_value(
            summary.sharpe.value, summary.sharpe.note
        )
        self.drawdown_card.set_value(
            summary.drawdown.value, summary.drawdown.note
        )
        self.trade_card.set_value(
            summary.trades.value, summary.trades.note
        )
        self.controls.set_controls(view.controls)
        self.comparison_table.render(
            view.runs,
            selected_run_id=view.selected_run_id,
        )
        self.trades_table.render(view.detail.trades)
        chart = view.detail.chart
        if chart is None:
            self.chart.set_series("", (), title="")
        else:
            self.chart.set_series(
                chart.symbol,
                chart.points,
                title=chart.title,
            )
        self.evidence_label.setText(view.detail.evidence)

    def current_draft(self) -> BacktestFormDraft:
        """Return the current immutable backtest form state."""
        return self.controls.draft()

    def set_strategy_options(
        self,
        options: tuple[BacktestStrategyOption, ...],
    ) -> None:
        self.controls.set_strategy_options(options)

    def set_palette(self, palette: ThemePalette) -> None:
        self._palette = palette
        self.chart.update()
        self.update()


__all__ = ["BacktestPage"]
