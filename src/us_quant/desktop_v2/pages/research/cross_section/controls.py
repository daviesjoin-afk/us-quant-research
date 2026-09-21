"""Controls for the native cross-section research page."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from us_quant.desktop_v2.pages.research.cross_section.models import (
    CrossSectionResearchDraft,
)
from us_quant.ui_theme import ThemePalette, theme_palette


CAPITAL_MINIMUM = 100
CAPITAL_MAXIMUM = 100_000_000
WARNING_TEXT = (
    "⚠ 事后复权价 + 当前上市池；结果仅供研究，晋级门硬阻断"
)


class CrossSectionControls(QWidget):
    """Owns the research-capital widget and run intent."""

    capital_changed = Signal(int)
    run_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        research_capital: int,
        palette: ThemePalette | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette or theme_palette("dark")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.capital_spin = QSpinBox()
        self.capital_spin.setRange(CAPITAL_MINIMUM, CAPITAL_MAXIMUM)
        self.capital_spin.setValue(int(research_capital))
        self.capital_spin.setPrefix("$")
        self.capital_spin.setSuffix(" 历史研究情景")
        self.capital_spin.valueChanged.connect(self.capital_changed.emit)
        self.run_button = QPushButton("运行复权价研究代理")
        self.run_button.clicked.connect(self.run_requested.emit)
        self.warning_label = QLabel(WARNING_TEXT)
        self.warning_label.setObjectName("subtitle")
        layout.addWidget(self.capital_spin)
        layout.addWidget(self.run_button)
        layout.addWidget(self.warning_label)
        layout.addStretch()

    def current_draft(self) -> CrossSectionResearchDraft:
        return CrossSectionResearchDraft(
            research_capital=int(self.capital_spin.value())
        )

    def set_research_capital(
        self,
        value: int,
        *,
        emit_change: bool = False,
    ) -> None:
        if emit_change:
            self.capital_spin.setValue(int(value))
            return
        self.capital_spin.blockSignals(True)
        try:
            self.capital_spin.setValue(int(value))
        finally:
            self.capital_spin.blockSignals(False)

    def set_palette(self, palette: ThemePalette) -> None:
        self._palette = palette


__all__ = [
    "CAPITAL_MAXIMUM",
    "CAPITAL_MINIMUM",
    "WARNING_TEXT",
    "CrossSectionControls",
]