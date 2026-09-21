"""Desktop UI v2 Runtime Events workspace.

This page replaced the pieces ``MainWindow._runtime_tab`` built and the window
then mutated from four handlers.  The page owns the metric cards, the three
buttons, the table and the info panel, and reports only what the operator asked
for:

* ``refresh_requested`` -- rebuild the view from the store;
* ``resolve_requested`` -- confirm the selected event, carrying its full integer
  id, or ``None`` when nothing is selected so the window can decide the prompt;
* ``export_requested`` -- export the terminal bundle.

It holds no ``RuntimeEventStore``, opens no database and reads no filesystem:
the window queries the store and hands the page an immutable view to render.
"""

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

from us_quant.desktop_widgets import MetricCard
from us_quant.ui_theme import ThemePalette, theme_palette

from .models import RuntimeEventsPageView
from .table import RuntimeEventTable

#: Card title and the refresh-time note, migrated verbatim.
CARD_NOTES = (
    ("错误事件", "未确认错误"),
    ("警告事件", "未确认警告"),
    ("活动任务", "当前后台任务"),
)
EXPORT_CARD_TITLE = "最近导出"

EMPTY_TEXT = (
    "暂无运行事件。启动行情、刷新账户或运行研究后，"
    "故障与恢复记录会在此保留并可确认。"
)


class RuntimeEventsPage(QWidget):
    """Renders the Runtime Events workspace and reports operator intent."""

    refresh_requested = Signal()
    resolve_requested = Signal(object)
    export_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        palette: ThemePalette | None = None,
        theme_name: str = "dark",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("runtimeEventsPage")
        self._palette = palette or theme_palette(theme_name)
        self._build()

    # -- construction ---------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.addLayout(self._build_cards())

        controls = QHBoxLayout()
        self._refresh_button = QPushButton("刷新事件")
        self._refresh_button.clicked.connect(self.refresh_requested.emit)
        self._resolve_button = QPushButton("确认所选事件")
        self._resolve_button.clicked.connect(self._resolve_clicked)
        self._export_button = QPushButton("导出当前终端状态")
        self._export_button.clicked.connect(self.export_requested.emit)
        controls.addWidget(self._refresh_button)
        controls.addWidget(self._resolve_button)
        controls.addWidget(self._export_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.empty_label = QLabel(EMPTY_TEXT)
        self.empty_label.setObjectName("emptyState")
        self.empty_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty_label)

        splitter = QSplitter(Qt.Vertical)
        self.table = RuntimeEventTable(self._palette)
        splitter.addWidget(self.table)
        info_panel = QFrame()
        info_panel.setObjectName("panel")
        info_layout = QVBoxLayout(info_panel)
        info_title = QLabel("版本、路径与恢复信息")
        info_title.setObjectName("sectionTitle")
        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        info_layout.addWidget(info_title)
        info_layout.addWidget(self.info_text)
        splitter.addWidget(info_panel)
        splitter.setSizes([480, 180])
        layout.addWidget(splitter)

    def _build_cards(self) -> QHBoxLayout:
        cards = QHBoxLayout()
        self.error_card = MetricCard(
            CARD_NOTES[0][0], "0", CARD_NOTES[0][1]
        )
        self.warning_card = MetricCard(
            CARD_NOTES[1][0], "0", CARD_NOTES[1][1]
        )
        self.task_card = MetricCard(
            CARD_NOTES[2][0], "0", CARD_NOTES[2][1]
        )
        self.export_card = MetricCard(EXPORT_CARD_TITLE, "无", "脱敏 CSV / JSON")
        for card in (
            self.error_card,
            self.warning_card,
            self.task_card,
            self.export_card,
        ):
            cards.addWidget(card)
        return cards
    # -- rendering ------------------------------------------------------

    def render(self, view: RuntimeEventsPageView) -> None:
        """Draw one immutable view; the page owns no truth of its own."""

        self.error_card.set_value(view.error_count, CARD_NOTES[0][1])
        self.warning_card.set_value(view.warning_count, CARD_NOTES[1][1])
        self.task_card.set_value(view.active_task_count, CARD_NOTES[2][1])
        self.export_card.set_value(view.last_export_value, view.last_export_note)
        self.empty_label.setVisible(not view.rows)
        self.table.render(view.rows)
        self.info_text.setPlainText(view.info_text)

    def set_palette(self, palette: ThemePalette) -> None:
        """Adopt the window's palette and recolour the rows already drawn.

        This is a pure repaint: it preserves the selected event id and emits no
        refresh, resolve or export intent.
        """

        self._palette = palette
        self.table.set_palette(palette)

    # -- intents --------------------------------------------------------

    def _resolve_clicked(self) -> None:
        self.resolve_requested.emit(self.table.selected_event_id())


__all__ = ["CARD_NOTES", "EXPORT_CARD_TITLE", "RuntimeEventsPage"]
