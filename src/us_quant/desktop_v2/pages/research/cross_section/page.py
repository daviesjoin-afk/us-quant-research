"""Native Desktop UI v2 cross-section research page."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.research.cross_section.controls import (
    CrossSectionControls,
)
from us_quant.desktop_v2.pages.research.cross_section.models import (
    CrossSectionResearchDraft,
    CrossSectionResearchView,
)
from us_quant.desktop_v2.pages.research.cross_section.tables import (
    CrossSectionCandidateTable,
    CrossSectionFoldTable,
)
from us_quant.desktop_widgets import EquityComparisonChart, MetricCard
from us_quant.ui_theme import ThemePalette, theme_palette


class CrossSectionResearchPage(QWidget):
    """Renders cross-section research facts and emits operator intent."""

    run_requested = Signal(object)
    capital_changed = Signal(int)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        research_capital: int,
        palette: ThemePalette | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette or theme_palette("dark")
        self._build(research_capital=research_capital)

    def _build(self, *, research_capital: int) -> None:
        layout = QVBoxLayout(self)
        cards = QHBoxLayout()
        self.gate_card = MetricCard(
            "晋级门", "硬阻断", "研究代理不得进入影子或实盘"
        )
        self.return_card = MetricCard(
            "复权价 OOS 代理", "—", "不是历史整股可执行收益"
        )
        self.drawdown_card = MetricCard(
            "代理最大回撤", "—", "复权价研究曲线"
        )
        self.cost_stress_card = MetricCard(
            "2×成本压力", "—", "佣金与滑点同时翻倍"
        )
        self.folds_card = MetricCard(
            "测试折数", "—", "锚定走样本外"
        )
        self.metric_cards = (
            self.gate_card,
            self.return_card,
            self.drawdown_card,
            self.cost_stress_card,
            self.folds_card,
        )
        for card in self.metric_cards:
            cards.addWidget(card)
        layout.addLayout(cards)

        self.controls = CrossSectionControls(
            research_capital=research_capital,
            palette=self._palette,
        )
        self.controls.capital_changed.connect(self.capital_changed.emit)
        self.controls.run_requested.connect(self._emit_run)
        layout.addWidget(self.controls)

        splitter = QSplitter(Qt.Vertical)
        self.chart = EquityComparisonChart()
        splitter.addWidget(self.chart)
        tables = QSplitter(Qt.Horizontal)
        self.candidate_table = CrossSectionCandidateTable()
        self.fold_table = CrossSectionFoldTable()
        tables.addWidget(self.candidate_table)
        tables.addWidget(self.fold_table)
        tables.setSizes([600, 760])
        splitter.addWidget(tables)
        splitter.setSizes([330, 280])
        layout.addWidget(splitter)

    def render(self, view: CrossSectionResearchView) -> None:
        summary = view.summary
        self.gate_card.set_value(summary.gate.value, summary.gate.note)
        self.return_card.set_value(
            summary.return_proxy.value, summary.return_proxy.note
        )
        self.drawdown_card.set_value(
            summary.drawdown.value, summary.drawdown.note
        )
        self.cost_stress_card.set_value(
            summary.cost_stress.value, summary.cost_stress.note
        )
        self.folds_card.set_value(summary.folds.value, summary.folds.note)
        self.chart.set_rows(
            [
                {
                    "date": row.date,
                    "strategy_equity": row.strategy_equity,
                    "cost_2x_equity": row.cost_2x_equity,
                }
                for row in view.chart_rows
            ]
        )
        self.candidate_table.render(
            view.candidates, has_report=view.has_report
        )
        self.fold_table.render(view.folds, has_report=view.has_report)

    def current_draft(self) -> CrossSectionResearchDraft:
        return self.controls.current_draft()

    def set_research_capital(
        self,
        value: int,
        *,
        emit_change: bool = False,
    ) -> None:
        self.controls.set_research_capital(
            value, emit_change=emit_change
        )

    def set_palette(self, palette: ThemePalette) -> None:
        self._palette = palette
        self.controls.set_palette(palette)
        self.chart.update()

    def _emit_run(self) -> None:
        self.run_requested.emit(self.current_draft())


__all__ = ["CrossSectionResearchPage"]