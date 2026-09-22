"""Desktop UI v2 execution page: the native execution route.

This page replaced ``MainWindow._auto_quant_tab``, a builder that both assembled
the widgets and was reached into by name from a dozen handlers elsewhere in the
window.  The page now owns its controls outright, and the window owns the
orchestration: every button emits an intent and none of them calls a service, a
workflow or a window method.

Three boundaries are load-bearing.  It **renders and reports intent**: it holds
no service, workflow, runtime, repository or broker import, so it draws a view
model and emits a signal while everything else is decided elsewhere.  It
**cannot arm, connect or start anything**: the launch confirmation stays in the
window, because a page that could arm itself would make the confirmation
advisory, and ``set_arm_confirmed`` exists so the dialog can write its answer
here rather than so the page can ask the question.  It **cannot change a
lifecycle phase**: it is told which controls to enable (``set_control_state``)
rather than which phase the session is in, so it has no phase vocabulary to act
on.

The page is a thin composition: the metric cards, the control strip (``controls``)
and the detail tables (``tables``) are separate modules, and this one wires their
signals to its own and forwards the small read/write surface the window uses.  The
window talks to ``self.execution_page`` and never to a widget inside it.

The five execution-health cards are migrated as they were -- static placeholders
-- because this round moves the UI boundary and does not re-derive the health
verdicts; changing what they show would be a behaviour change smuggled into a
move.
"""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from us_quant.desktop_v2.pages.execution.controls import ExecutionControls
from us_quant.desktop_v2.pages.execution.models import (
    ExecutionCandidatesView,
    ExecutionControlState,
    ExecutionDetailWorkspace,
    ExecutionRuntimeView,
)
from us_quant.desktop_v2.pages.execution.tables import ExecutionDetailTabs
from us_quant.desktop_widgets import MetricCard
from us_quant.ui_theme import ThemePalette, theme_palette

#: The five health cards, as the legacy page shipped them: placeholders whose
#: values were never populated there either.
HEALTH_CARDS = (
    ("执行健康", "未评估", "HEALTHY / WAITING / HALT"),
    ("券商持仓/本地持仓", "0/0", "以 IBKR Paper 为准"),
    ("在途订单", "0", "本地 pending + 券商开放订单"),
    ("未对账订单", "0", "终态但成交未对齐或仍缺状态"),
    ("提交延迟", "—", "intent 生成 → placeOrder 耗时"),
)

#: The page's signals, and the control-strip signal each one re-publishes.  The
#: page owns the surface the window connects to; the strip is an implementation
#: detail, so a signal is forwarded rather than reached through.
FORWARDED_SIGNALS = (
    ("strategy_selected", "strategy_selected"),
    ("preflight_inputs_changed", "preflight_inputs_changed"),
    ("prepare_requested", "prepare_requested"),
    ("channel_check_requested", "channel_check_requested"),
    ("start_requested", "start_requested"),
    ("stop_stream_requested", "stop_stream_requested"),
    ("pause_requested", "pause_requested"),
    ("resume_requested", "resume_requested"),
    ("stop_requested", "stop_requested"),
    ("resume_reconciliation_requested", "resume_reconciliation_requested"),
)


class ExecutionPage(QWidget):
    """Renders the execution route and reports what the operator asked for."""

    strategy_selected = Signal(object)
    preflight_inputs_changed = Signal()

    prepare_requested = Signal()
    channel_check_requested = Signal()
    start_requested = Signal()
    stop_stream_requested = Signal()

    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()

    reconcile_requested = Signal()
    resume_reconciliation_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        palette: ThemePalette | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("executionPage")
        self._palette = palette or theme_palette("light")
        self._build()

    # -- construction ---------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.addLayout(self._build_cards())
        layout.addLayout(self._build_health_cards())
        self.controls = ExecutionControls()
        for own, forwarded in FORWARDED_SIGNALS:
            getattr(self.controls, forwarded).connect(getattr(self, own))
        layout.addWidget(self.controls)
        self.details = ExecutionDetailTabs(palette=self._palette)
        self.details.reconcile_requested.connect(self.reconcile_requested.emit)
        layout.addWidget(self.details)

    def _build_cards(self) -> QHBoxLayout:
        cards = QHBoxLayout()
        self.status_card = MetricCard("自动量化", "未启动", "IBKR Paper 订单")
        self.equity_card = MetricCard("模拟账户净值", "—", "以 IBKR Paper 为准")
        self.realized_card = MetricCard("当日已实现", "—", "券商 P&L 优先")
        self.unrealized_card = MetricCard("未实现盈亏", "—", "券商 P&L 优先")
        self.position_card = MetricCard("当前持仓", "0", "当前研究版最多一只")
        for card in (
            self.status_card,
            self.equity_card,
            self.realized_card,
            self.unrealized_card,
            self.position_card,
        ):
            cards.addWidget(card)
        return cards

    def _build_health_cards(self) -> QHBoxLayout:
        """The five execution-health cards, migrated as static placeholders."""

        cards = QHBoxLayout()
        self.health_cards = tuple(
            MetricCard(title, value, note) for title, value, note in HEALTH_CARDS
        )
        for card in self.health_cards:
            cards.addWidget(card)
        return cards

    # -- rendering ------------------------------------------------------

    def render(self, view: ExecutionRuntimeView) -> None:
        """Draw one view; the page keeps no copy of the facts behind it."""

        self.status_card.set_value(view.status.value, view.status.note)
        self.equity_card.set_value(view.equity.value, view.equity.note)
        self.realized_card.set_value(view.realized.value, view.realized.note)
        self.unrealized_card.set_value(view.unrealized.value, view.unrealized.note)
        self.position_card.set_value(
            view.position_count.value, view.position_count.note
        )
        self.controls.render_summary(view.summary)
        self.details.render(view)

    def render_candidates(self, view: ExecutionCandidatesView) -> None:
        """Draw the candidate table while no session exists yet.

        The shortlist an operator approves is the one thing on this route that
        has content before a launch: the session cards are empty and the other
        tables have no session to read from, but the candidates must be
        inspectable or the operator is asked to arm something unseen.
        """

        self.details.render_candidates(view)

    def render_context(self, **lines: str | None) -> None:
        """Replace the context lines written outside a session render."""

        self.controls.render_context(**lines)

    def render_preflight(self, ready: int, total: int, details: str) -> None:
        """Show the preflight tally and its detail."""

        self.controls.render_preflight(ready, total, details)

    def render_execution_health(self, text: str) -> None:
        self.details.set_execution_health_text(text)

    def set_active_detail(self, workspace: ExecutionDetailWorkspace) -> None:
        """Show one of the page's own detail sections by its semantic key.

        Presentation navigation only: the tab position stops being part of the
        page's public contract, so re-laying the details out -- a sidebar, a
        stacked widget -- leaves this call and its callers unchanged.
        """

        self.details.tabs.setCurrentIndex(int(workspace))

    def set_control_state(self, state: ExecutionControlState) -> None:
        """Apply exactly the controls the caller says are available."""

        self.controls.set_control_state(state)
        self.details.set_reconcile_enabled(state.reconcile_enabled)
        self.controls.resume_reconciliation_button.setEnabled(
            state.resume_reconciliation_enabled
        )

    def set_strategy_options(
        self, options: list[tuple[str, str]], selected_version_id: str | None
    ) -> None:
        """Point the combo at the selection service's options and choice."""

        self.controls.set_strategy_options(options, selected_version_id)

    def set_arm_confirmed(self, value: bool) -> None:
        """Write the launch confirmation the window's dialog obtained."""

        self.controls.set_arm_confirmed(value)

    # -- queries --------------------------------------------------------

    def selected_strategy_version_id(self) -> str | None:
        """The version the combo displays; the service still owns the truth."""

        return self.controls.selected_strategy_version_id()

    def candidate_limit(self) -> int:
        return self.controls.candidate_limit()

    def capital_limit(self) -> Decimal:
        return self.controls.capital_limit()

    def arm_confirmed(self) -> bool:
        return self.controls.arm_confirmed()

    # -- theme ----------------------------------------------------------

    def set_palette(self, palette: ThemePalette) -> None:
        self._palette = palette
        self.details.set_palette(palette)