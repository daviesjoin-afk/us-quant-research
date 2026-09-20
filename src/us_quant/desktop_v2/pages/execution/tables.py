"""The execution page's five detail tabs.

The tabs own the tables, the execution-health line and the reconcile button, and
nothing else.  They render rows they are handed: no service, no repository, no
runtime, no broker.  A table that could fetch its own rows would make the page a
second place where truth is assembled, which is exactly what this migration
removes.

Two things are worth naming:

* the candidate table keeps the legacy optimisation -- its six scan columns are
  rebuilt only when the candidate set changes, while the realtime column is
  rewritten on every tick.  The state that decides this belongs here, next to the
  table it protects, rather than on the window;
* a row carries a *tone*, and this module knows which of its own columns the tone
  colours.  Choosing the colour from the palette is the only place a theme
  reaches a row.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.execution.models import (
    ExecutionRuntimeView,
    Tone,
)
from us_quant.desktop_v2.pages.execution.rows import display_values, row_tones
from us_quant.desktop_widgets import configure_table
from us_quant.table_models import ImmutableRowsTableModel
from us_quant.ui_theme import ThemePalette, theme_palette

#: The status column of each toned table, in the table's own column order.
SHADOW_TONE_COLUMN = 6
LATENCY_TONE_COLUMN = 3
ORDER_TONE_COLUMN = 0
CANDIDATE_TONE_COLUMN = 6

POSITION_HEADERS = ("代码", "整股", "成交均价", "最新估值", "未实现P&L", "持仓时间", "来源")
FILL_HEADERS = ("时间", "代码", "方向", "整股", "Paper成交价", "估算费用", "本笔已实现")
SHADOW_HEADERS = ("代码", "Bid", "Ask", "影子买价", "影子卖价", "策略限价", "状态")
LATENCY_HEADERS = ("intent_id", "symbol", "side", "提交延迟 ms", "生成时间")
CANDIDATE_HEADERS = ("代码", "名称", "板块", "层级", "扫描分", "日线信号", "实时状态")
ORDER_HEADERS = ("状态", "代码", "方向", "订单/成交", "限价", "对账说明", "Order")

SHADOW_NOTE = (
    "这里只展示研究态影子限价带：ask+slippage、bid−slippage 与当前 "
    "策略 limit_price 的相对位置。Paper 真实订单仍以本地 intent 与券商 "
    "逐笔成交对账为准。"
)
LATENCY_NOTE = (
    "展示最近订单从本地生成到 IBKR Paper placeOrder 的延迟分布；"
    "用于识别网络/API 抖动与重试策略效果。"
)
ORDER_NOTE = (
    "这里只显示整理后的会话订单。原始 IBKR 回调写入本地审计库，"
    "Live、市场单、碎股、做空、全局撤单和期权接口均不存在。"
)

#: The execution-health line before any session has been published.
DISCONNECTED_HEALTH = "执行对账：未连接。券商状态、逐笔成交和本地持仓将在这里汇总。"
FINALIZED_HEALTH = "执行对账：会话已安全结束，券商持仓和订单均已核对。"


class ExecutionDetailTabs(QWidget):
    """Renders the execution detail tables and reports the reconcile intent."""

    reconcile_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        palette: ThemePalette | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("executionDetailTabs")
        self._palette = palette or theme_palette("light")
        self._candidates_static_key: tuple[tuple[str, ...], ...] | None = None
        self._build()

    # -- construction ---------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMinimumHeight(130)
        self.tabs.addTab(self._build_portfolio_tab(), "组合与盈亏")
        self.tabs.addTab(
            self._build_note_tab("影子执行带", SHADOW_NOTE, self._shadow_table()),
            "影子执行带",
        )
        self.tabs.addTab(
            self._build_note_tab("提交延迟观测", LATENCY_NOTE, self._latency_table()),
            "提交延迟",
        )
        self.tabs.addTab(self._build_candidates_tab(), "候选与信号")
        self.tabs.addTab(self._build_orders_tab(), "Paper订单")
        layout.addWidget(self.tabs)

    def _build_portfolio_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.position_model = ImmutableRowsTableModel(POSITION_HEADERS)
        self.position_table = QTableView()
        self.position_table.setModel(self.position_model)
        configure_table(self.position_table)
        self.fill_model = ImmutableRowsTableModel(FILL_HEADERS)
        self.fill_table = QTableView()
        self.fill_table.setModel(self.fill_model)
        configure_table(self.fill_table)
        layout.addWidget(QLabel("当前组合"))
        layout.addWidget(self.position_table)
        layout.addWidget(QLabel("最近成交"))
        layout.addWidget(self.fill_table)
        return page

    def _build_note_tab(
        self, title: str, note: str, table: QTableWidget
    ) -> QWidget:
        """A tab that is a section title, an explanatory note and one table."""

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label(title, "sectionTitle"))
        layout.addWidget(_label(note, "subtitle"))
        layout.addWidget(table)
        return page

    def _shadow_table(self) -> QTableWidget:
        self.shadow_table = _grid_table(SHADOW_HEADERS)
        return self.shadow_table

    def _latency_table(self) -> QTableWidget:
        self.latency_table = _grid_table(LATENCY_HEADERS)
        return self.latency_table

    def _build_candidates_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.candidate_table = _grid_table(CANDIDATE_HEADERS)
        layout.addWidget(self.candidate_table)
        return page

    def _build_orders_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label(ORDER_NOTE, "subtitle"))
        self.health_label = _label(DISCONNECTED_HEALTH, "subtitle")
        self.reconcile_button = QPushButton("断线后重新连接对账")
        self.reconcile_button.setEnabled(False)
        self.reconcile_button.clicked.connect(self.reconcile_requested.emit)
        self.order_table = _grid_table(ORDER_HEADERS)
        layout.addWidget(self.health_label)
        layout.addWidget(self.reconcile_button, alignment=Qt.AlignLeft)
        layout.addWidget(self.order_table)
        return page

    # -- rendering ------------------------------------------------------

    def render(self, view: ExecutionRuntimeView) -> None:
        """Draw every table from one view, in the tables' own column order."""

        self.position_model.set_rows(display_values(view.positions))
        self.fill_model.set_rows(display_values(view.fills))
        self._render_candidates(view)
        self._render_toned(self.shadow_table, view.shadow, SHADOW_TONE_COLUMN)
        self._render_toned(self.latency_table, view.latency, LATENCY_TONE_COLUMN)
        self._render_toned(self.order_table, view.orders, ORDER_TONE_COLUMN)

    def _render_toned(
        self, table: QTableWidget, rows: Sequence[object], tone_column: int
    ) -> None:
        """Replace every cell, colouring the tone column where a tone applies.

        A row may have fewer values than its table has columns -- the candidate
        rows carry only their scan columns -- so the realtime column is written
        by its own pass and is deliberately left untouched here.
        """

        values = display_values(rows)
        tones = row_tones(rows)
        table.setRowCount(len(values))
        for row_index, cells in enumerate(values):
            for column, value in enumerate(cells):
                tone = tones[row_index] if column == tone_column else Tone.NEUTRAL
                self._set_cell(table, row_index, column, value, tone)

    def _render_candidates(self, view: ExecutionRuntimeView) -> None:
        """Rebuild the scan columns only when the candidate set changed."""

        if view.candidates_static_key != self._candidates_static_key:
            self._candidates_static_key = view.candidates_static_key
            self.candidate_table.setSortingEnabled(False)
            self._render_toned(
                self.candidate_table, view.candidates, CANDIDATE_TONE_COLUMN
            )
        for index, realtime in enumerate(view.candidate_realtime):
            self._set_cell(
                self.candidate_table,
                index,
                CANDIDATE_TONE_COLUMN,
                realtime.status,
                realtime.tone,
            )
        self.candidate_table.setSortingEnabled(True)

    def _set_cell(
        self,
        table: QTableWidget,
        row: int,
        column: int,
        value: str,
        tone: Tone,
    ) -> None:
        item = QTableWidgetItem(value)
        item.setToolTip(value)
        colour = _tone_colour(self._palette, tone)
        if colour is not None:
            item.setForeground(QColor(colour))
        table.setItem(row, column, item)

    # -- public surface -------------------------------------------------

    def set_execution_health_text(self, text: str) -> None:
        """Publish the one execution-health line the orders tab shows."""

        self.health_label.setText(text)

    @property
    def execution_health_text(self) -> str:
        """The line currently shown, for callers that need to read it back."""

        return self.health_label.text()

    def set_reconcile_enabled(self, enabled: bool) -> None:
        self.reconcile_button.setEnabled(enabled)

    # -- theme ----------------------------------------------------------

    def set_palette(self, palette: ThemePalette) -> None:
        """Adopt the window's palette; rows already drawn keep their text."""

        self._palette = palette


def _grid_table(headers: Sequence[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    configure_table(table)
    return table


def _label(text: str, object_name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(True)
    return label


def _tone_colour(palette: ThemePalette, tone: Tone) -> str | None:
    """The palette colour for a tone, or ``None`` for the default text colour."""

    if tone is Tone.SUCCESS:
        return palette.success
    if tone is Tone.WARNING:
        return palette.warning
    if tone is Tone.ERROR:
        return palette.error
    return None