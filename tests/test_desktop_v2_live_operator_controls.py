from __future__ import annotations

import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from us_quant.trading.adapters.sqlite.live_safety_repository import (
    SQLiteLiveSafetyRepository,
)
from us_quant.desktop_v2.orchestration.execution.live_operator import (
    ARM_UNAVAILABLE,
    LiveOperatorControlsOrchestrator,
)
from us_quant.desktop_v2.pages.execution import ExecutionPage
from us_quant.trading.application.live_operator_controls import (
    LiveOperatorControlsApplication,
)


_APP = QApplication.instance() or QApplication([])


def test_execution_page_displays_live_state_and_emits_operator_intents(tmp_path):
    page = ExecutionPage()
    safety_repository = SQLiteLiveSafetyRepository(
        tmp_path / "live_safety.sqlite3"
    )
    application = LiveOperatorControlsApplication(
        safety_repository,
        now=lambda: datetime(2026, 9, 28, tzinfo=timezone.utc),
    )
    orchestrator = LiveOperatorControlsOrchestrator(
        page=page,
        application=application,
        environment=lambda: "paper",
        feature_enabled=lambda: False,
        now=lambda: datetime(2026, 9, 28, tzinfo=timezone.utc),
    )
    warnings: list[tuple[str, str]] = []
    logs: list[str] = []
    orchestrator.warning_requested.connect(
        lambda title, body: warnings.append((title, body))
    )
    orchestrator.log_requested.connect(logs.append)
    page.live_arm_requested.connect(orchestrator.request_arm)
    page.live_kill_requested.connect(orchestrator.request_kill)
    page.live_status_refresh_requested.connect(orchestrator.refresh)

    orchestrator.refresh()

    panel = page.live_operator_controls
    assert panel.fact_labels["environment"].text() == "PAPER"
    assert panel.fact_labels["feature_flag"].text() == "关闭"
    assert panel.fact_labels["authorization"].text() == "未配置"
    assert panel.fact_labels["session_arm"].text() == "未武装（进程级）"
    assert panel.fact_labels["capital_limit"].text() == "0 · 无持久授权"
    assert not panel.arm_button.isEnabled()
    assert ARM_UNAVAILABLE in panel.arm_button.toolTip()

    panel.arm_button.setEnabled(True)
    panel.arm_button.click()
    assert warnings == [("Live session arm 不可用", ARM_UNAVAILABLE)]
    assert not safety_repository.load().kill_latch.is_latched

    panel.kill_button.click()
    durable = safety_repository.load()
    assert durable.kill_latch.is_latched
    assert durable.kill_latch.reason == "Desktop Live operator emergency kill"
    assert panel.fact_labels["kill_latch"].text() == "已触发"
    assert "LIVE HALTED" in panel.status_label.text()
    assert "新增风险已阻断" in logs[-1]

    page.deleteLater()
    _APP.processEvents()
