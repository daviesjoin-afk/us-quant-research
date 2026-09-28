from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from us_quant.trading.adapters.sqlite.live_safety_repository import (
    SQLiteLiveSafetyRepository,
)
from us_quant.trading.composition import live_operator_controls as composition
from us_quant.trading.domain.live_safety import (
    LiveAccountFingerprint,
    LiveCanaryLimits,
    LiveOperatorAuthorization,
    LiveSafetyRecord,
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
    assert page.scroll_area.widgetResizable()
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


def test_unavailable_live_safety_store_keeps_desktop_controls_fail_closed(
    monkeypatch, tmp_path
):
    def fail_store(_path):
        raise OSError("store unavailable")

    monkeypatch.setattr(composition, "SQLiteLiveSafetyRepository", fail_store)
    application = composition.build_live_operator_controls_application(
        tmp_path / "unavailable.sqlite3"
    )
    page = ExecutionPage()
    orchestrator = LiveOperatorControlsOrchestrator(
        page=page,
        application=application,
        environment=lambda: "live",
        feature_enabled=lambda: True,
    )

    orchestrator.refresh()

    assert page.live_operator_controls.fact_labels["environment"].text() == "LIVE"
    assert "fail-closed" in page.live_operator_controls.fact_labels[
        "authorization"
    ].text()
    assert not page.live_operator_controls.arm_button.isEnabled()
    page.deleteLater()
    _APP.processEvents()


def test_strategy_display_uses_the_intersection_of_both_authorization_lists(
    tmp_path,
):
    repository = SQLiteLiveSafetyRepository(tmp_path / "live_safety.sqlite3")
    fingerprint = LiveAccountFingerprint.from_identity(
        provider="ibkr",
        environment="live",
        account_id="DU123456",
        endpoint_identity="live-primary",
    )
    limits = LiveCanaryLimits(
        capital_limit=Decimal("1000"),
        max_order_notional=Decimal("250"),
        max_daily_loss=Decimal("50"),
        max_positions=1,
        max_open_orders=1,
        allowed_symbols=("AAPL",),
        allowed_strategy_versions=("strategy-v2", "strategy-v3"),
    )
    authorization = LiveOperatorAuthorization(
        authorization_id="auth-intersection",
        created_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
        expires_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
        expected_account_fingerprint=fingerprint,
        approved_strategy_version_ids=("strategy-v1", "strategy-v2"),
        approved_canary_limits=limits,
    )
    repository.save(
        expected_revision=0,
        replacement=LiveSafetyRecord(revision=1, authorization=authorization),
    )
    page = ExecutionPage()
    orchestrator = LiveOperatorControlsOrchestrator(
        page=page,
        application=LiveOperatorControlsApplication(repository),
        environment=lambda: "live",
        feature_enabled=lambda: True,
        now=lambda: datetime(2026, 9, 28, tzinfo=timezone.utc),
    )

    orchestrator.refresh()

    assert (
        page.live_operator_controls.fact_labels["allowed_strategies"].text()
        == "strategy-v2"
    )
    page.deleteLater()
    _APP.processEvents()
