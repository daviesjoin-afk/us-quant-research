"""The targeted validation session panel and its three workspace pages."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.research.targeted.controls import TargetedControls
from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedSessionView,
)
from us_quant.desktop_v2.pages.research.targeted.tables import TargetedTable
from us_quant.desktop_widgets import MetricCard
from us_quant.ui_theme import ThemePalette, theme_palette


POSITION_HEADERS = (
    "代码", "整股数量", "入场价", "入场时间", "最高价", "行情来源", "覆盖", "环境"
)
FILL_HEADERS = (
    "时间", "代码", "方向", "数量", "模拟成交价", "佣金", "本笔净P&L",
    "原因", "来源", "覆盖", "会话",
)


class TargetedSessionPanel(QWidget):
    """Five Shadow cards plus the console, position and fill workspace pages."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = theme_palette("dark")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        cards = QHBoxLayout()
        cards.setSpacing(8)
        self.status_card = MetricCard("内部策略仿真", "未启动", "不进入 IBKR 模拟账户")
        self.equity_card = MetricCard("影子净值", "—", "启动时读取 IBKR Paper 净值")
        self.realized_card = MetricCard("已实现盈亏", "$0.00", "已扣双边模拟佣金")
        self.unrealized_card = MetricCard("未实现盈亏", "$0.00", "按最新有效 mark")
        self.trade_card = MetricCard("完成交易", "0 / 4", "整股；最多一笔持仓")
        for card in (
            self.status_card, self.equity_card, self.realized_card,
            self.unrealized_card, self.trade_card,
        ):
            cards.addWidget(card, 1)
        layout.addLayout(cards)
        self.controls = TargetedControls()
        self.console_panel = self._build_console()
        self.position_table = TargetedTable(POSITION_HEADERS, palette=self._palette)
        self.position_panel = self._build_position()
        self.fill_table = TargetedTable(
            FILL_HEADERS,
            tone_columns=(1, 2),
            palette=self._palette,
        )
        self.fill_explanation = QLabel(
            "状态：未启动。该工具只验证行情→信号→成本后模拟成交→"
            "持仓→盈亏→平仓链路，不以单晚收益证明策略有效。"
        )
        self.fill_explanation.setWordWrap(True)
        self.fill_panel = self._build_fill()

    def _build_console(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)
        title = QLabel("会话控制台")
        title.setObjectName("sectionTitle")
        note = QLabel(
            "启动前依次确认策略、标的与行情。启动时仅读取 IBKR Paper "
            "净值；10% 单仓、整股、$0.35/单模拟佣金和 2bps 滑点均在内部仿真中计算。"
        )
        note.setObjectName("subtitle")
        note.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(note)
        layout.addWidget(self.controls)
        return panel

    def _build_position(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        title = QLabel("持仓（实时内部仿真）")
        title.setObjectName("sectionTitle")
        note = QLabel("未启动或尚无成交时保持为空；所有持仓均为内部影子记录。")
        note.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(note)
        layout.addWidget(self.position_table)
        return panel

    def _build_fill(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        title = QLabel("内部模拟成交记录（不发送 IBKR 订单）")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        layout.addWidget(self.fill_table)
        layout.addWidget(self.fill_explanation)
        return panel

    def render(self, view: TargetedSessionView) -> None:
        for card, metric in (
            (self.status_card, view.status),
            (self.equity_card, view.equity),
            (self.realized_card, view.realized),
            (self.unrealized_card, view.unrealized),
            (self.trade_card, view.trades),
        ):
            card.set_value(metric.value, metric.note)
        self.controls.set_target_status(view.target_status)
        self.controls.set_minute_status(view.minute_status)
        self.controls.render(view.controls)
        self.position_table.render_rows(view.positions)
        self.fill_table.render_rows(view.fills)
        self.fill_explanation.setText(view.explanation)

    def set_palette(self, palette: ThemePalette) -> None:
        self._palette = palette
        self.position_table.set_palette(palette)
        self.fill_table.set_palette(palette)


__all__ = ["TargetedSessionPanel"]
