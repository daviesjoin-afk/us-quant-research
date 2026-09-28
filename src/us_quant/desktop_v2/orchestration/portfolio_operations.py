"""Portfolio plan commands and operations presentation for the Desktop."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from us_quant.desktop_v2.orchestration.execution.portfolio_projection import (
    build_portfolio_operations_view,
    build_unavailable_portfolio_operations_view,
)


class PortfolioOperationsOrchestrator(QObject):
    """Translate plan UI intents and composed facts into page presentation."""

    information_requested = Signal(str, str)
    warning_requested = Signal(str, str)

    def __init__(self, *, application, page, active_session) -> None:
        super().__init__()
        self._application = application
        self._page = page
        self._active_session = active_session
        page.portfolio_plan_save_requested.connect(self.save_plan)

    def refresh_editor(self) -> None:
        try:
            options, plan = self._application.editor_state()
            self._page.render_portfolio_plan_editor(options=options, plan=plan)
        except Exception as error:  # noqa: BLE001 - unreadable plan stays visibly blocked
            self._page.render_portfolio_plan_editor(
                options=(),
                plan=None,
                error=f"策略目录或计划读取失败，Paper start 已关闭：{error}",
            )
        self._page.set_portfolio_plan_editable(not self._active_session())

    def save_plan(self, command: object) -> None:
        try:
            self._application.save_editor(command)
        except Exception as error:  # noqa: BLE001 - report a refused configuration edit
            self.warning_requested.emit("Portfolio 计划未保存", str(error))
            return
        self.refresh_editor()
        self.information_requested.emit(
            "Portfolio 计划已保存",
            "新计划版本已持久化；下一次 Paper start 将冻结此版本。",
        )

    def render_runtime_facts(
        self, *, snapshot, reconciliation, decisions, plan, runtime_state: str
    ) -> None:
        if (
            snapshot is None
            or reconciliation is None
            or getattr(reconciliation, "blockers", ())
        ):
            view = build_unavailable_portfolio_operations_view(
                policy=plan.policy,
                runtime_state=runtime_state,
                reconciliation=reconciliation,
            )
        else:
            view = build_portfolio_operations_view(
                snapshot=snapshot,
                policy=plan.policy,
                decisions=decisions,
                reconciliation=reconciliation,
                runtime_state=runtime_state,
            )
        self._page.render_portfolio_operations(view)


__all__ = ["PortfolioOperationsOrchestrator"]
