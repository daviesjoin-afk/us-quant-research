"""Desktop orchestration for durable Live operator controls."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from PySide6.QtCore import QObject, Signal

from us_quant.desktop_v2.pages.execution.live_operator_models import (
    LiveOperatorControlView,
)
from us_quant.trading.application.live_operator_controls import (
    LiveOperatorControlsApplication,
)
from us_quant.trading.domain.live_safety import LiveSafetyRecord


ARM_UNAVAILABLE = (
    "Live session arm 已关闭：当前 Desktop 尚未接入 Live broker、账户与启动证明 providers，"
    "不能验证真实账户和新鲜行情/对账事实。"
)


class LiveOperatorControlsOrchestrator(QObject):
    """Projects Live safety state and handles the panel's narrow intents."""

    warning_requested = Signal(str, str)
    log_requested = Signal(str)

    def __init__(
        self,
        *,
        page: object,
        application: LiveOperatorControlsApplication,
        environment: Callable[[], str],
        feature_enabled: Callable[[], bool],
        now: Callable[[], datetime] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._page = page
        self._application = application
        self._environment = environment
        self._feature_enabled = feature_enabled
        self._now = now or (lambda: datetime.now(timezone.utc))

    def refresh(self) -> None:
        """Read current durable safety and deployment facts for display."""

        try:
            record = self._application.snapshot()
            view = self._view(record)
        except Exception as error:
            view = LiveOperatorControlView(
                environment=self._environment().upper(),
                feature_flag="未知",
                bound_account="不可读取",
                fingerprint="不可读取",
                authorization="不可读取 · fail-closed",
                session_arm="未武装",
                kill_latch="状态不可读取",
                startup_proof="不可用",
                reconciliation="不可用 · 必须人工检查",
                capital_limit="—",
                order_limit="—",
                daily_loss_limit="—",
                position_limit="—",
                open_order_limit="—",
                allowed_strategies="—",
                allowed_symbols="—",
                broker_connection="未知",
                arm_enabled=False,
                arm_block_reason=f"Live safety 数据不可读取，已 fail-closed：{error}",
            )
        self._page.render_live_operator(view)

    def request_arm(self) -> None:
        """Fail closed until Desktop has real Live startup/account providers."""

        self.warning_requested.emit("Live session arm 不可用", ARM_UNAVAILABLE)

    def request_kill(self) -> None:
        """Persist the emergency kill latch and immediately repaint its state."""

        try:
            record = self._application.engage_kill(
                reason="Desktop Live operator emergency kill"
            )
        except Exception as error:
            self.warning_requested.emit(
                "Live Kill 未能持久化",
                f"安全状态写入失败；必须停止 Live 操作并人工检查：{error}",
            )
            self.refresh()
            return
        self._page.render_live_operator(self._view(record))
        self.log_requested.emit("Live kill latch 已持久化；新增风险已阻断")

    def _view(self, record: LiveSafetyRecord) -> LiveOperatorControlView:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Live operator clock must be timezone-aware")
        authorization = record.authorization
        if authorization is None:
            authorization_text = "未配置"
            account = "未绑定"
            fingerprint = "未建立"
            limits = None
        else:
            limits = authorization.approved_canary_limits
            if authorization.revoked_at is not None:
                authorization_text = "已撤销"
            elif not authorization.is_valid_at(now):
                authorization_text = "已过期或尚未生效"
            else:
                authorization_text = (
                    f"有效 · ID {authorization.authorization_id} · "
                    f"至 {authorization.expires_at.isoformat()}"
                )
            account = authorization.expected_account_fingerprint.masked_account
            fingerprint = (
                "已绑定 · SHA-256 …"
                + authorization.expected_account_fingerprint.sha256[-8:]
                + "；当前 broker 未核验"
            )
            effective_strategies = tuple(
                strategy
                for strategy in authorization.approved_strategy_version_ids
                if strategy in set(limits.allowed_strategy_versions)
            )

        return LiveOperatorControlView(
            environment=str(self._environment()).upper(),
            feature_flag="启用" if self._feature_enabled() else "关闭",
            bound_account=account,
            fingerprint=fingerprint,
            authorization=authorization_text,
            session_arm="未武装（进程级）",
            kill_latch="已触发" if record.kill_latch.is_latched else "未触发",
            startup_proof="不可用 · Live provider 未接入",
            reconciliation=(
                f"需要人工对账 · {record.recovery_latch.reason}"
                if record.recovery_latch.is_required
                else "未取得当前进程的 Live 对账证明"
            ),
            capital_limit="0 · 无持久授权" if limits is None else str(limits.capital_limit),
            order_limit="0 · 无持久授权" if limits is None else str(limits.max_order_notional),
            daily_loss_limit="0 · 无持久授权" if limits is None else str(limits.max_daily_loss),
            position_limit="0 · 无持久授权" if limits is None else str(limits.max_positions),
            open_order_limit="0 · 无持久授权" if limits is None else str(limits.max_open_orders),
            allowed_strategies=(
                "—"
                if authorization is None
                else ", ".join(effective_strategies) or "无交集"
            ),
            allowed_symbols=(
                "—" if limits is None else ", ".join(limits.allowed_symbols)
            ),
            broker_connection="未连接 · Live provider 未接入",
            arm_enabled=False,
            arm_block_reason=ARM_UNAVAILABLE,
        )


__all__ = ["ARM_UNAVAILABLE", "LiveOperatorControlsOrchestrator"]
