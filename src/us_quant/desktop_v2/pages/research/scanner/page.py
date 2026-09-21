"""Native market scanner page."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.research.scanner.models import (
    ScannerChartView,
    ScannerCoverageFacts,
    ScannerFilterMode,
    ScannerPageView,
)
from us_quant.desktop_v2.pages.research.scanner.presenter import (
    coverage_text,
    filter_scanner_rows,
)
from us_quant.desktop_v2.pages.research.scanner.table import ScannerTable
from us_quant.desktop_widgets import PriceChart
from us_quant.ui_theme import ThemePalette, theme_palette


FILTER_OPTIONS = (
    ("全部", ScannerFilterMode.ALL.value),
    ("趋势候选", ScannerFilterMode.TREND.value),
    ("可交易资格", ScannerFilterMode.TRADE_ELIGIBLE.value),
    ("仅龙头", ScannerFilterMode.LEADERS.value),
)


class ScannerPage(QWidget):
    """Renders scan facts and reports scan/selection intent."""

    scan_requested = Signal()
    symbol_selected = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        palette: ThemePalette | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette or theme_palette("dark")
        self._view = ScannerPageView(
            rows=(),
            coverage=ScannerCoverageFacts(0, 0, 0),
        )
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("筛选代码、名称或板块")
        self.search_input.textChanged.connect(self._apply_filter)
        self.filter_combo = QComboBox()
        for label, key in FILTER_OPTIONS:
            self.filter_combo.addItem(label, key)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        self.scan_button = QPushButton("重新扫描")
        self.scan_button.clicked.connect(self.scan_requested.emit)
        controls.addWidget(self.search_input)
        controls.addWidget(self.filter_combo)
        controls.addWidget(self.scan_button)
        layout.addLayout(controls)

        self.coverage_label = QLabel("等待读取研究池和历史日 K 覆盖")
        self.coverage_label.setObjectName("subtitle")
        self.coverage_label.setWordWrap(True)
        layout.addWidget(self.coverage_label)

        splitter = QSplitter(Qt.Vertical)
        self.table = ScannerTable(palette=self._palette)
        self.table.symbol_selected.connect(self.symbol_selected.emit)
        splitter.addWidget(self.table)
        self.chart = PriceChart()
        splitter.addWidget(self.chart)
        splitter.setSizes([380, 300])
        layout.addWidget(splitter)

    def render(self, view: ScannerPageView) -> None:
        self._view = view
        self._apply_filter()

    def render_chart(self, view: ScannerChartView) -> None:
        self.chart.set_series(view.symbol, view.points, title=view.title)

    def set_palette(self, palette: ThemePalette) -> None:
        self._palette = palette
        self.table.set_palette(palette)
        self.chart.update()

    def _apply_filter(self, *_args: object) -> None:
        mode = ScannerFilterMode(
            str(self.filter_combo.currentData() or ScannerFilterMode.ALL.value)
        )
        visible = filter_scanner_rows(
            self._view.rows,
            mode,
            self.search_input.text(),
        )
        self.coverage_label.setText(
            coverage_text(
                self._view.coverage,
                visible_count=len(visible),
                has_scan=self._view.has_scan,
            )
        )
        self.table.render(visible)


__all__ = ["FILTER_OPTIONS", "ScannerPage"]
