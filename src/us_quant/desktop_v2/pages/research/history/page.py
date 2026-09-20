"""Native History page."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.research.history.models import HistoryPageView
from us_quant.desktop_widgets import configure_table
from us_quant.ui_theme import ThemePalette, theme_palette


HISTORY_HEADERS = ("代码", "周期", "优先级", "状态", "尝试", "K线数", "说明")


class HistoryPage(QWidget):
    """Renders the history queue and reports download intent."""

    schedule_requested = Signal()
    run_ibkr_requested = Signal(int)
    run_public_requested = Signal(int)
    retry_failed_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        palette: ThemePalette | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette or theme_palette("dark")
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.schedule_button = QPushButton("将全部非中概研究池加入队列")
        self.schedule_button.clicked.connect(self.schedule_requested.emit)
        self.run_ibkr_button = QPushButton("下载下一批日 K")
        self.run_ibkr_button.clicked.connect(
            lambda: self.run_ibkr_requested.emit(self.batch_size.value())
        )
        self.run_public_button = QPushButton("备用免费日 K（仅研究）")
        self.run_public_button.clicked.connect(
            lambda: self.run_public_requested.emit(self.batch_size.value())
        )
        self.retry_button = QPushButton("重试失败任务")
        self.retry_button.clicked.connect(self.retry_failed_requested.emit)
        self.batch_size = QSpinBox()
        self.batch_size.setRange(1, 100)
        self.batch_size.setValue(25)
        self.batch_size.setSuffix(" 个/批")
        controls.addWidget(self.schedule_button)
        controls.addWidget(self.run_ibkr_button)
        controls.addWidget(self.run_public_button)
        controls.addWidget(self.retry_button)
        controls.addWidget(self.batch_size)
        controls.addStretch()
        layout.addLayout(controls)

        self.summary_label = QLabel(
            "队列按龙头、优质二线、其余研究样本排序；下载仍按所选批量执行。"
        )
        self.summary_label.setObjectName("subtitle")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.table = QTableWidget(0, len(HISTORY_HEADERS))
        self.table.setHorizontalHeaderLabels(HISTORY_HEADERS)
        configure_table(self.table)
        layout.addWidget(self.table)

    def render(self, view: HistoryPageView) -> None:
        self.summary_label.setText(view.summary)
        self.progress.setValue(view.controls.progress_percent)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(view.rows))
        for index, row in enumerate(view.rows):
            values = (
                row.symbol,
                row.duration,
                row.priority,
                row.status,
                row.attempts,
                row.row_count,
                row.note,
            )
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))
        self.table.setSortingEnabled(True)

    def set_palette(self, palette: ThemePalette) -> None:
        self._palette = palette


__all__ = ["HISTORY_HEADERS", "HistoryPage"]
