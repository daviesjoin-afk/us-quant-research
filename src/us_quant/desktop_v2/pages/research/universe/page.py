"""Native Universe page."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem as _QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.research.universe.models import (
    UniverseControlView,
    UniverseFilterMode,
    UniversePageView,
    UniverseRowView,
)
from us_quant.desktop_v2.pages.research.universe.presenter import (
    UNIVERSE_DISPLAY_CAP,
    filter_universe_rows,
    universe_count_text,
)
from us_quant.desktop_widgets import _sortable_number, configure_table
from us_quant.ui_theme import ThemePalette, theme_palette


class _NumericTableWidgetItem(_QTableWidgetItem):
    """Keep display text stable while sorting numeric cells numerically."""

    def __lt__(self, other: _QTableWidgetItem) -> bool:
        left = _sortable_number(self.text())
        right = _sortable_number(other.text())
        if left is not None and right is not None:
            return left < right
        return self.text().casefold() < other.text().casefold()


UNIVERSE_HEADERS = (
    "代码",
    "名称",
    "交易所",
    "类型",
    "板块",
    "层级",
    "中概/国别证据",
    "资格",
    "排除/说明",
)
FILTER_OPTIONS = (
    ("非中概研究池", UniverseFilterMode.RESEARCH.value),
    ("可交易核心池", UniverseFilterMode.TRADING.value),
    ("全部官方标的", UniverseFilterMode.ALL.value),
    ("已排除", UniverseFilterMode.EXCLUDED.value),
)


class UniversePage(QWidget):
    """Renders the official universe and reports refresh intent."""

    refresh_requested = Signal()
    cancel_refresh_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        palette: ThemePalette | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette or theme_palette("dark")
        self._rows: tuple[UniverseRowView, ...] = ()
        self._controls = UniverseControlView(
            refresh_enabled=True,
            cancel_enabled=False,
            refresh_label="刷新官方标的",
            cancel_label="取消刷新",
        )
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索代码、名称或板块")
        self.search_input.textChanged.connect(self._apply_filter)
        self.filter_combo = QComboBox()
        for label, key in FILTER_OPTIONS:
            self.filter_combo.addItem(label, key)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        self.count_label = QLabel("显示 0 / 0")
        self.count_label.setObjectName("subtitle")
        self.refresh_button = QPushButton("刷新官方标的")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.cancel_button = QPushButton("取消刷新")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_refresh_requested.emit)
        controls.addWidget(self.search_input)
        controls.addWidget(self.filter_combo)
        controls.addWidget(self.count_label)
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.cancel_button)
        layout.addLayout(controls)

        self.table = QTableWidget(0, len(UNIVERSE_HEADERS))
        self.table.setHorizontalHeaderLabels(UNIVERSE_HEADERS)
        configure_table(self.table)
        layout.addWidget(self.table)

    def render(self, view: UniversePageView) -> None:
        self._rows = view.rows
        self._controls = view.controls
        self.refresh_button.setEnabled(view.controls.refresh_enabled)
        self.refresh_button.setText(view.controls.refresh_label)
        self.cancel_button.setEnabled(view.controls.cancel_enabled)
        self.cancel_button.setText(view.controls.cancel_label)
        self._apply_filter()

    def _apply_filter(self, *_args: object) -> None:
        mode = UniverseFilterMode(
            str(self.filter_combo.currentData() or UniverseFilterMode.RESEARCH.value)
        )
        matched = filter_universe_rows(self._rows, mode, self.search_input.text())
        visible = matched[:UNIVERSE_DISPLAY_CAP]
        self.count_label.setText(universe_count_text(len(matched), len(visible)))
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(visible))
        for index, row in enumerate(visible):
            values = (
                row.symbol,
                row.name,
                row.exchange,
                row.security_type,
                row.sector,
                row.leader_tier,
                row.country_evidence,
                row.eligibility,
                row.note,
            )
            for column, value in enumerate(values):
                self.table.setItem(index, column, _NumericTableWidgetItem(value))
        self.table.setSortingEnabled(True)

    def set_palette(self, palette: ThemePalette) -> None:
        self._palette = palette


__all__ = ["UNIVERSE_HEADERS", "UniversePage"]
