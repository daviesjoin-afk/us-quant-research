"""Desktop UI v2 Dashboard page: widgets, rendering and intents only."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.dashboard.artifact_table import (
    DashboardArtifactTable,
)
from us_quant.desktop_v2.pages.dashboard.models import DashboardView
from us_quant.desktop_widgets import MetricCard, PriceChart
from us_quant.ui_theme import ThemePalette, theme_palette


class DashboardPage(QWidget):
    """Owns the Dashboard widgets and reports the Gateway probe intent."""

    gateway_probe_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        palette: ThemePalette | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("dashboardPage")
        self._palette = palette or theme_palette("dark")
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        cards = QHBoxLayout()
        self._net_liquidation_card = MetricCard(
            "IBKR Paper 净值", "未读取", "到账户与持仓页执行只读刷新"
        )
        self._daily_pnl_card = MetricCard(
            "账户当日盈亏", "不可用", "不会以研究收益代替"
        )
        self._positions_card = MetricCard(
            "真实持仓", "未读取", "券商空仓与未读取严格区分"
        )
        self._intraday_market_card = MetricCard(
            "日内行情", "不可用", "尚未启动流行情"
        )
        for card in (
            self._net_liquidation_card,
            self._daily_pnl_card,
            self._positions_card,
            self._intraday_market_card,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        provenance_panel = QFrame()
        provenance_panel.setObjectName("panel")
        provenance_layout = QVBoxLayout(provenance_panel)
        provenance_header = QVBoxLayout()
        provenance_header.setSpacing(2)
        provenance_title = QLabel("数据与研究产物真值")
        provenance_title.setObjectName("sectionTitle")
        provenance_note = QLabel(
            "账户、实时行情、历史研究严格分区；失效结果不得部署"
        )
        provenance_note.setObjectName("subtitle")
        provenance_header.addWidget(provenance_title)
        provenance_header.addWidget(provenance_note)
        provenance_layout.addLayout(provenance_header)
        self._artifact_table = DashboardArtifactTable(self._palette)
        self._artifact_table.setFixedHeight(150)
        provenance_layout.addWidget(self._artifact_table)
        provenance_panel.setMaximumHeight(215)
        layout.addWidget(provenance_panel)

        toolbar = QHBoxLayout()
        self._gateway_button = QPushButton("仅检查 Gateway 端口")
        self._gateway_button.clicked.connect(
            self.gateway_probe_requested.emit
        )
        toolbar.addWidget(self._gateway_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        body = QSplitter(Qt.Horizontal)
        self._chart = PriceChart()
        body.addWidget(self._chart)
        insight_panel = QFrame()
        insight_panel.setObjectName("panel")
        insight_layout = QVBoxLayout(insight_panel)
        insight_title = QLabel("当前研究边界")
        insight_title.setObjectName("sectionTitle")
        self._notes = QTextEdit()
        self._notes.setReadOnly(True)
        insight_layout.addWidget(insight_title)
        insight_layout.addWidget(self._notes)
        body.addWidget(insight_panel)
        body.setSizes([900, 360])
        layout.addWidget(body)

    def render(self, view: DashboardView) -> None:
        """Draw one immutable view; this method performs no business reads."""

        self._net_liquidation_card.set_value(
            view.net_liquidation.value, view.net_liquidation.note
        )
        self._daily_pnl_card.set_value(
            view.daily_pnl.value, view.daily_pnl.note
        )
        self._positions_card.set_value(
            view.positions.value, view.positions.note
        )
        self._intraday_market_card.set_value(
            view.intraday_market.value, view.intraday_market.note
        )
        self._artifact_table.render(view.artifacts)
        if view.chart.symbol is None:
            self._chart.set_series("", ())
        else:
            self._chart.set_series(view.chart.symbol, view.chart.points)
        self._notes.setPlainText(view.research_boundary_text)

    def set_palette(self, palette: ThemePalette) -> None:
        """Adopt a new palette without reading or emitting business intent."""

        self._palette = palette
        self._artifact_table.set_palette(palette)
        self._chart.update()


__all__ = ["DashboardPage"]
