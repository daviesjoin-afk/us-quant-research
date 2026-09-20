"""Native Desktop UI v2 targeted validation page."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.research.targeted.evidence_panel import (
    TargetedEvidencePanel,
)
from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetPreflightView,
    TargetedControlView,
    TargetedStrategyOption,
    TargetedValidationView,
)
from us_quant.desktop_v2.pages.research.targeted.session_panel import (
    TargetedSessionPanel,
)
from us_quant.desktop_v2.pages.research.targeted.tables import TargetedTable
from us_quant.ui_theme import ThemePalette, theme_palette


class TargetedValidationPage(QWidget):
    """Renders the targeted workspace and emits operator intent."""

    strategy_selected = Signal(str)
    target_apply_requested = Signal(str)
    target_subscribe_requested = Signal(str)
    shadow_start_requested = Signal()
    shadow_stop_requested = Signal()
    replay_requested = Signal()
    robustness_requested = Signal()
    robustness_run_selected = Signal(str)
    review_run_selected = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        palette: ThemePalette | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("targetedValidationPage")
        self._palette = palette or theme_palette("dark")
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(self._header())
        self.session_panel = TargetedSessionPanel()
        self.evidence_panel = TargetedEvidencePanel()
        self.session_panel.set_palette(self._palette)
        self.evidence_panel.set_palette(self._palette)
        self._connect_controls()
        self._connect_evidence()
        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setDocumentMode(True)
        self.workspace_tabs.addTab(self.session_panel.console_panel, "策略")
        self.workspace_tabs.addTab(self.session_panel.position_panel, "持仓")
        self.workspace_tabs.addTab(self.session_panel.fill_panel, "委托")
        self.workspace_tabs.addTab(self.evidence_panel, "档案")
        self.workspace_tabs.addTab(self._preflight_panel(), "风控")
        layout.addWidget(self.session_panel)
        layout.addWidget(self.workspace_tabs)

    def _header(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        title = QLabel("内部策略仿真工作台")
        title.setObjectName("sectionTitle")
        boundary = QLabel(
            "仅验证“行情 → 信号 → 成本后模拟成交 → 持仓 → 盈亏”链路；"
            "不会向 IBKR 或任何券商发送订单。"
        )
        boundary.setObjectName("subtitle")
        boundary.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(boundary)
        return panel

    def _preflight_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        self.preflight_summary = QLabel(
            "输入标的后，这里汇总身份、非中概、行情、资金、整股与策略检查。"
        )
        self.preflight_summary.setObjectName("subtitle")
        self.preflight_summary.setWordWrap(True)
        self.preflight_table = TargetedTable(
            ("检查", "结果", "当前值", "要求", "类别", "影响"),
            palette=self._palette,
            column_widths=(180, 90, 320, 280, 90, 110),
            tone_columns=(1,),
        )
        layout.addWidget(self.preflight_summary)
        layout.addWidget(self.preflight_table)
        return panel

    def _connect_controls(self) -> None:
        controls = self.session_panel.controls
        controls.strategy_selected.connect(self.strategy_selected.emit)
        controls.target_apply_requested.connect(self.target_apply_requested.emit)
        controls.target_subscribe_requested.connect(self.target_subscribe_requested.emit)
        controls.shadow_start_requested.connect(self.shadow_start_requested.emit)
        controls.shadow_stop_requested.connect(self.shadow_stop_requested.emit)
        controls.replay_requested.connect(self.replay_requested.emit)
        controls.robustness_requested.connect(self.robustness_requested.emit)

    def _connect_evidence(self) -> None:
        self.evidence_panel.robustness_run_selected.connect(
            self.robustness_run_selected.emit
        )
        self.evidence_panel.review_run_selected.connect(self.review_run_selected.emit)

    def render(self, view: TargetedValidationView) -> None:
        self.session_panel.render(view.session)
        self.evidence_panel.render(view.evidence)
        self._render_preflight(view.session.preflight)
        if view.active_workspace is not None:
            self.workspace_tabs.setCurrentIndex(view.active_workspace)
        if view.active_evidence_tab is not None:
            self.evidence_panel.tabs.setCurrentIndex(view.active_evidence_tab)

    def _render_preflight(self, preflight: TargetPreflightView) -> None:
        self.preflight_summary.setText(preflight.summary)
        self.preflight_table.render_rows(preflight.rows)

    def set_palette(self, palette: ThemePalette) -> None:
        self._palette = palette
        self.session_panel.set_palette(palette)
        self.evidence_panel.set_palette(palette)
        self.preflight_table.set_palette(palette)

    def set_strategy_options(
        self,
        options: tuple[TargetedStrategyOption, ...],
        selected_version_id: str | None = None,
    ) -> None:
        self.session_panel.controls.set_strategy_options(
            options, selected_version_id
        )

    def set_selected_strategy_version(self, version_id: str) -> None:
        self.session_panel.controls.set_selected_strategy_version(version_id)

    def selected_strategy_version_id(self) -> str:
        return self.session_panel.controls.selected_strategy_version_id()

    def set_target_symbol(self, symbol: str) -> None:
        self.session_panel.controls.set_target_symbol(symbol)

    def target_symbol(self) -> str:
        return self.session_panel.controls.target_symbol()

    def set_control_view(self, controls: TargetedControlView) -> None:
        self.session_panel.controls.render(controls)


__all__ = ["TargetedValidationPage"]
