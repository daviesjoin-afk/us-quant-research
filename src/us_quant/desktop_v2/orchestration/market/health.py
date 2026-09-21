"""The market route's global-header projection.

The market and handshake badges speak for the whole workbench, so they are not
the page's and not the runtime's: they are a projection from one market state
into the facts the shell paints.  Keeping that projection here rather than in
the orchestrator keeps two concerns apart -- the orchestrator owns the feed, and
this owns the throttled narration of it.

The throttle is the reason this is a stateful object rather than a function.  A
connected feed produces a snapshot every few hundred milliseconds, and one
status line per snapshot would make the footer useless; the legacy rule was
"log when the state key changes, otherwise at most once every thirty seconds",
and that rule needs the previous key and timestamp.
"""

from __future__ import annotations

from time import monotonic

from us_quant.desktop_v2.orchestration.market.models import MarketShellHealthView
from us_quant.trading.application.market_data import (
    PUSH_LISTENER_SOURCES,
    SOURCE_ALPACA_IEX,
)

#: How long an unchanged feed state may go without repeating its status line.
STATUS_LOG_INTERVAL_SECONDS = 30.0


class ShellHealthPublisher:
    """Turns one market state into the shell facts, with a throttled status line."""

    def __init__(self) -> None:
        self._last_status_key: tuple[object, ...] | None = None
        self._last_status_log_at = 0.0

    def reset(self) -> None:
        """Forget the throttle, so the next snapshot logs unconditionally."""

        self._last_status_key = None
        self._last_status_log_at = 0.0

    def publish(self, snapshot, readiness) -> MarketShellHealthView:
        """Project one snapshot into the badges and the throttled status line.

        The handshake badge is only ever *promoted*: the legacy rule left it
        alone unless the protocol had handshaken, so the view carries ``None``
        rather than a value that would quietly demote it.
        """

        handshake_text: str | None = None
        handshake_state: str | None = None
        if snapshot.ready:
            handshake_text = (
                f"{snapshot.source_label} · 已认证"
                if snapshot.source_id in PUSH_LISTENER_SOURCES
                else "协议 · 已握手"
            )
            handshake_state = "ok"
        if snapshot.realtime_ready:
            market_text = (
                (
                    "行情 · IEX 实时"
                    if snapshot.source_id == SOURCE_ALPACA_IEX
                    else "行情 · Finnhub 成交"
                )
                if snapshot.source_id in PUSH_LISTENER_SOURCES
                else "行情 · 实时"
            )
            market_state = "ok"
        elif snapshot.error_code is not None:
            market_text = f"行情 · 错误 {snapshot.error_code}"
            market_state = "error"
        else:
            market_text = "行情 · 未达日内门槛"
            market_state = "warn"
        return MarketShellHealthView(
            market_text=market_text,
            market_state=market_state,
            handshake_text=handshake_text,
            handshake_state=handshake_state,
            status_log=self._status_log(snapshot, readiness),
        )

    def stopped(self, reason: str) -> MarketShellHealthView:
        """The header facts for an invalidated feed."""

        return MarketShellHealthView(
            market_text="行情 · 已停止",
            market_state="warn",
        )

    def failed(self, message: str) -> MarketShellHealthView:
        """The header facts for a feed that failed outright."""

        return MarketShellHealthView(
            market_text="行情 · 流服务失败",
            market_state="error",
            status_log=f"流服务失败：{message}",
        )

    def _status_log(self, snapshot, readiness) -> str | None:
        status_key = (
            snapshot.source_id,
            snapshot.generation,
            snapshot.ready,
            snapshot.error_code,
        )
        now_monotonic = monotonic()
        if (
            status_key == self._last_status_key
            and now_monotonic - self._last_status_log_at
            < STATUS_LOG_INTERVAL_SECONDS
        ):
            return None
        self._last_status_key = status_key
        self._last_status_log_at = now_monotonic
        if snapshot.error_code is not None:
            return (
                f"{snapshot.source_label} 行情错误 "
                f"{snapshot.error_code}：{snapshot.message}"
            )
        if snapshot.ready:
            return (
                f"{snapshot.source_label} 已连接 · 可下单候选 "
                f"{readiness.candidate_current_count}/"
                f"{readiness.candidate_count} · 市场参考 "
                f"{readiness.reference_current_count}/"
                f"{readiness.reference_count} · 订阅合计 "
                f"{readiness.subscription_current_count}/"
                f"{readiness.subscription_count}"
            )
        return (
            f"{snapshot.source_label} 正在连接（第 "
            f"{snapshot.reconnect_attempt} 次）…"
        )


__all__ = ["ShellHealthPublisher"]
