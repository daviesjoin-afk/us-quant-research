"""The execution page's control strip: the launch inputs and the session buttons.

Split out of ``page`` because the page had grown two jobs -- composing the route
and owning a nineteen-widget control strip -- and the strip is the half that is
reached into by the launch flow.  The page composes; this module owns the
controls.

It is a widget, not a controller.  Every button emits an intent and nothing more:
no service is called, no workflow phase is read or written, nothing is armed and
nothing is connected.  The one write it accepts from outside is the launch
confirmation (``set_arm_confirmed``), which exists so the window's dialog can
record its answer here rather than so the strip can ask the question.

The strategy combo is a *view* of the selection service, never a source of
truth: ``set_strategy_options`` refills it from the service's options and points
it at the service's selection, with signals blocked so a repaint cannot be
mistaken for the operator choosing something.  The chosen id travels out as an
intent and the window records it into the service.
"""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.pages.execution.models import ExecutionControlState
from us_quant.desktop_widgets import configure_combo_width

#: The input defaults the legacy builder shipped, pinned by characterization.
DEFAULT_CANDIDATE_LIMIT = 30
MINIMUM_CANDIDATE_LIMIT = 3
MAXIMUM_CANDIDATE_LIMIT = 30
MAXIMUM_CAPITAL_LIMIT = 100_000_000

PIPELINE = (
    "自动 Paper：① 全市场扫描→实时短名单　② 确认启动　③ 可暂停新开仓或停止并平仓"
)
PIPELINE_TOOLTIP = (
    "完整保护链：广域扫描 → 非中概与龙头门 → 实时信号 → 风控 → "
    "IBKR Paper DAY 限价单 → 券商成交与持仓对账。"
)
SUMMARY = (
    "第 1 步会重新扫描全部非中概研究池，再从有合格历史数据的标的中"
    "选出最多 30 个实时候选；第 2 步弹窗确认后启动 Paper 会话。运行中可只暂停新开仓，"
    "不会强制卖出现有持仓。"
)
SCOPE = "市场范围：等待载入官方标的池和最近扫描。"

#: The four labelled inputs, in the order the legacy grid placed them, and the
#: column stretches it used.
CONTROL_FIELDS = ("策略版本", "实时轮动候选上限", "会话资金上限", "候选与行情")
CONTROL_STRETCH = (4, 1, 2, 2)

STOP_STREAM_TOOLTIP = (
    "停止当前只读行情。第 1 步切换候选时会自动安全停止旧行情，通常不需要手动点击。"
)
PAUSE_TOOLTIP = (
    "禁止新的买入意图并撤销未成交买单；已有持仓继续执行止损、止盈和时段退出。"
)
RESUME_RECONCILIATION_TOOLTIP = (
    "仅用于对账完成后恢复已停机会话；"
    "不会自动重新下单，需要人工确认当前持仓与订单状态。"
)
STOP_TOOLTIP = (
    "撤销未成交买单，并按实时行情为已有 Paper 持仓提交限价卖出。"
)


class ExecutionControls(QWidget):
    """Owns the execution route's inputs and session buttons; reports intent."""

    strategy_selected = Signal(object)
    preflight_inputs_changed = Signal()

    prepare_requested = Signal()
    channel_check_requested = Signal()
    start_requested = Signal()
    stop_stream_requested = Signal()

    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()
    resume_reconciliation_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("executionControls")
        self._arm_confirmed = False
        self._build()

    # -- construction ---------------------------------------------------

    def _build(self) -> None:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setMinimumHeight(230)
        layout = QVBoxLayout(panel)
        layout.addWidget(_labelled(PIPELINE, "sectionTitle", tooltip=PIPELINE_TOOLTIP))
        self.session_label = _labelled("", "subtitle")
        self.scope_label = _labelled(SCOPE, "subtitle")
        layout.addWidget(self.session_label)
        layout.addWidget(self.scope_label)
        layout.addLayout(self._build_inputs())
        layout.addLayout(self._build_session_actions())
        layout.addLayout(self._build_risk_actions())
        self.summary_label = _labelled(SUMMARY, "subtitle")
        layout.addWidget(self.summary_label)
        self.preflight_label = _labelled("", "emptyState")
        layout.addWidget(self.preflight_label)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

    def _build_inputs(self) -> QGridLayout:
        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(6)
        self.strategy_combo = QComboBox()
        configure_combo_width(
            self.strategy_combo, minimum_width=485, minimum_contents=30
        )
        # The combo is a view: the choice is recorded into the selection service
        # before the preflight reads it, so the preflight judges the version the
        # runtime would actually use.
        self.strategy_combo.currentIndexChanged.connect(self._strategy_index_changed)
        self.candidate_limit_input = QSpinBox()
        self.candidate_limit_input.setRange(
            MINIMUM_CANDIDATE_LIMIT, MAXIMUM_CANDIDATE_LIMIT
        )
        self.candidate_limit_input.setValue(DEFAULT_CANDIDATE_LIMIT)
        self.candidate_limit_input.setMinimumWidth(115)
        self.candidate_limit_input.valueChanged.connect(self._inputs_changed)
        self.capital_limit_input = QSpinBox()
        self.capital_limit_input.setRange(0, MAXIMUM_CAPITAL_LIMIT)
        self.capital_limit_input.setSpecialValueText("使用 Paper 可用现金")
        self.capital_limit_input.setPrefix("$")
        self.capital_limit_input.setValue(0)
        self.capital_limit_input.setMinimumWidth(250)
        self.capital_limit_input.setToolTip(
            "0 表示使用 IBKR Paper 净值与现金中的较小值；"
            "填写金额可限制本次策略使用的模拟资金，不修改券商账户。"
        )
        self.capital_limit_input.valueChanged.connect(self._inputs_changed)
        self.prepare_button = QPushButton("第 1 步：准备并检查")
        self.prepare_button.clicked.connect(self.prepare_requested.emit)
        self.arm_confirm = QCheckBox("仅供启动弹窗写入的 Paper 确认")
        self.arm_confirm.setVisible(False)
        self.arm_confirm.toggled.connect(self._arm_toggled)
        self.channel_check_button = QPushButton("可选：测试 Paper 通道（不下单）")
        self.channel_check_button.clicked.connect(self.channel_check_requested.emit)
        self.channel_check_button.setVisible(False)
        widgets = (
            self.strategy_combo,
            self.candidate_limit_input,
            self.capital_limit_input,
            self.prepare_button,
        )
        for column, (label, widget) in enumerate(zip(CONTROL_FIELDS, widgets)):
            controls.addWidget(_labelled(label, "fieldLabel"), 0, column)
            controls.addWidget(widget, 1, column)
        for column, stretch in enumerate(CONTROL_STRETCH):
            controls.setColumnStretch(column, stretch)
        return controls

    def _build_session_actions(self) -> QHBoxLayout:
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.start_button = QPushButton("第 2 步：确认并启动 Paper 会话")
        self.start_button.clicked.connect(self.start_requested.emit)
        actions.addWidget(self.start_button)
        return actions

    def _build_risk_actions(self) -> QHBoxLayout:
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.stop_stream_button = _action_button(
            "停止当前行情", STOP_STREAM_TOOLTIP, self.stop_stream_requested
        )
        self.pause_button = _action_button(
            "暂停新开仓（保留持仓）", PAUSE_TOOLTIP, self.pause_requested
        )
        self.resume_button = _action_button("恢复新开仓", None, self.resume_requested)
        self.resume_reconciliation_button = _action_button(
            "恢复会话（对账后人工复核）",
            RESUME_RECONCILIATION_TOOLTIP,
            self.resume_reconciliation_requested,
        )
        self.stop_button = _action_button(
            "停止会话并请求平仓", STOP_TOOLTIP, self.stop_requested
        )
        for button in (
            self.stop_stream_button,
            self.pause_button,
            self.resume_button,
            self.resume_reconciliation_button,
            self.stop_button,
        ):
            actions.addWidget(button)
        return actions

    # -- rendering ------------------------------------------------------

    def render_context(
        self,
        *,
        summary: str | None = None,
        scope: str | None = None,
        session: str | None = None,
    ) -> None:
        """Replace the context lines written outside a session render.

        A launch in progress has no snapshot to build a whole view from, so these
        are written individually; leaving one out means "do not change it".
        """

        for label, text in (
            (self.summary_label, summary),
            (self.scope_label, scope),
            (self.session_label, session),
        ):
            if text is not None:
                label.setText(text)

    def render_summary(self, text: str) -> None:
        self.summary_label.setText(text)

    def render_preflight(self, ready: int, total: int, details: str) -> None:
        """Show the preflight tally and its detail.

        The strip is told the outcome; whether a launch is permitted is the
        application layer's judgement, not the strip's.
        """

        self.preflight_label.setText(
            f"准备检查 {ready}/{total}"
            " · 第 2 步会弹窗确认仅使用 DU 模拟账户。\n"
            f"{details}"
        )

    def set_control_state(self, state: ExecutionControlState) -> None:
        """Apply exactly the controls the caller says are available.

        The two recovery controls are not here: they belong to the detail tabs
        and the page, which is where their state is rendered.
        """

        for widget, enabled in (
            (self.prepare_button, state.prepare_enabled),
            (self.start_button, state.start_enabled),
            (self.channel_check_button, state.channel_check_enabled),
            (self.strategy_combo, state.strategy_combo_enabled),
            (self.candidate_limit_input, state.candidate_limit_enabled),
            (self.capital_limit_input, state.capital_limit_enabled),
            (self.arm_confirm, state.arm_confirm_enabled),
            (self.pause_button, state.pause_enabled),
            (self.resume_button, state.resume_enabled),
            (self.stop_button, state.stop_enabled),
            (self.stop_stream_button, state.stop_stream_enabled),
        ):
            widget.setEnabled(enabled)

    def set_strategy_options(
        self, options: list[tuple[str, str]], selected_version_id: str | None
    ) -> None:
        """Point the combo at the selection service's options and choice.

        The combo carries no truth of its own: it is cleared, refilled and aimed
        at whatever the service selected, with signals blocked so a repaint
        cannot be mistaken for the operator choosing something.
        """

        self.strategy_combo.blockSignals(True)
        self.strategy_combo.clear()
        for label, version_id in options:
            self.strategy_combo.addItem(label, version_id)
        index = (
            self.strategy_combo.findData(selected_version_id)
            if selected_version_id
            else -1
        )
        self.strategy_combo.setCurrentIndex(max(0, index))
        self.strategy_combo.blockSignals(False)

    def set_arm_confirmed(self, value: bool) -> None:
        """Write the launch confirmation the window's dialog obtained."""

        self._arm_confirmed = bool(value)
        self.arm_confirm.setChecked(self._arm_confirmed)

    # -- queries --------------------------------------------------------

    def selected_strategy_version_id(self) -> str | None:
        """The version the combo displays; the service still owns the truth."""

        data = self.strategy_combo.currentData()
        return None if data is None else str(data)

    def candidate_limit(self) -> int:
        return self.candidate_limit_input.value()

    def capital_limit(self) -> Decimal:
        return Decimal(self.capital_limit_input.value())

    def arm_confirmed(self) -> bool:
        return self._arm_confirmed

    # -- internals ------------------------------------------------------

    def _strategy_index_changed(self, *_args: object) -> None:
        self.strategy_selected.emit(self.selected_strategy_version_id())
        self.preflight_inputs_changed.emit()

    def _inputs_changed(self, *_args: object) -> None:
        """One of the three inputs the preflight reads moved."""

        self.preflight_inputs_changed.emit()

    def _arm_toggled(self, checked: bool) -> None:
        self._arm_confirmed = bool(checked)
        self.preflight_inputs_changed.emit()


def _action_button(text: str, tooltip: str | None, signal: Signal) -> QPushButton:
    """One session button: disabled until the session state allows it."""

    button = QPushButton(text)
    if tooltip is not None:
        button.setToolTip(tooltip)
    button.setEnabled(False)
    button.clicked.connect(signal.emit)
    return button


def _labelled(
    text: str, object_name: str, *, tooltip: str | None = None
) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(True)
    if tooltip is not None:
        label.setToolTip(tooltip)
    return label