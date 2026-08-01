from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from us_quant.auto_quant import AutoQuantSnapshot
from us_quant.ibkr_paper_orders import (
    PaperBrokerState,
    PaperOrderConnection,
    PaperOrderReconciliation,
)


@dataclass(frozen=True, slots=True)
class PaperExecutionIssue:
    code: str
    severity: str
    message: str


@dataclass(frozen=True, slots=True)
class PaperExecutionHealth:
    status: str
    safe_to_continue: bool
    connected: bool
    broker_position_count: int
    local_position_count: int
    pending_order_count: int
    unreconciled_order_count: int
    issues: tuple[PaperExecutionIssue, ...]
    observed_at: str


def evaluate_paper_execution_health(
    *,
    connection: PaperOrderConnection,
    broker_state: PaperBrokerState,
    engine_snapshot: AutoQuantSnapshot,
    reconciliations: tuple[PaperOrderReconciliation, ...],
    candidate_symbols: frozenset[str],
    now: datetime | None = None,
) -> PaperExecutionHealth:
    observed_now = _utc(now)
    issues: list[PaperExecutionIssue] = []
    if not connection.connected:
        issues.append(
            PaperExecutionIssue(
                "broker_disconnected",
                "halt",
                "IBKR Paper 订单连接已断开",
            )
        )
    if connection.open_broker_orders and not engine_snapshot.pending_orders:
        issues.append(
            PaperExecutionIssue(
                "orphan_open_orders",
                "halt",
                "券商存在本地引擎未跟踪的开放订单",
            )
        )
    try:
        broker_age = (
            observed_now
            - _utc(datetime.fromisoformat(
                broker_state.observed_at.replace("Z", "+00:00")
            ))
        ).total_seconds()
    except (TypeError, ValueError):
        broker_age = float("inf")
    if broker_age > 30:
        issues.append(
            PaperExecutionIssue(
                "broker_state_stale",
                "halt",
                f"券商账户/持仓快照已过期 {broker_age:.0f} 秒",
            )
        )

    broker_positions = {
        row.symbol: row.quantity
        for row in broker_state.positions
        if row.quantity != 0
    }
    local_positions = {
        row.symbol: Decimal(row.quantity)
        for row in engine_snapshot.positions
    }
    for symbol, quantity in broker_positions.items():
        if symbol not in candidate_symbols:
            issues.append(
                PaperExecutionIssue(
                    "unexpected_symbol",
                    "halt",
                    f"券商持仓 {symbol} 不属于当前候选会话",
                )
            )
        if quantity <= 0 or quantity != quantity.to_integral_value():
            issues.append(
                PaperExecutionIssue(
                    "invalid_broker_position",
                    "halt",
                    f"券商持仓 {symbol}={quantity} 不是正整股",
                )
            )

    unresolved = tuple(
        row for row in reconciliations if not row.reconciled
    )
    if broker_positions != local_positions:
        recent_order_event = any(
            _age_seconds(row.observed_at, observed_now) <= 15
            for row in reconciliations
        )
        severity = (
            "waiting"
            if (
                engine_snapshot.pending_orders
                or unresolved
                or recent_order_event
            )
            else "halt"
        )
        issues.append(
            PaperExecutionIssue(
                "position_mismatch",
                severity,
                "IBKR Paper 持仓与本地成交账本尚未一致",
            )
        )
    terminal_mismatch = tuple(
        row
        for row in unresolved
        if row.terminal
    )
    if terminal_mismatch:
        issues.append(
            PaperExecutionIssue(
                "terminal_execution_mismatch",
                "halt",
                f"{len(terminal_mismatch)} 笔终态订单与逐笔成交未对齐",
            )
        )
    elif unresolved:
        issues.append(
            PaperExecutionIssue(
                "orders_in_flight",
                "waiting",
                f"{len(unresolved)} 笔订单仍在券商确认/成交对账中",
            )
        )

    halt = any(issue.severity == "halt" for issue in issues)
    waiting = any(issue.severity == "waiting" for issue in issues)
    status = "HALT" if halt else "WAITING" if waiting else "HEALTHY"
    return PaperExecutionHealth(
        status=status,
        safe_to_continue=not halt,
        connected=connection.connected,
        broker_position_count=len(broker_positions),
        local_position_count=len(local_positions),
        pending_order_count=len(engine_snapshot.pending_orders),
        unreconciled_order_count=len(unresolved),
        issues=tuple(issues),
        observed_at=observed_now.isoformat(),
    )


def _utc(value: datetime | None) -> datetime:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(timezone.utc)


def _age_seconds(timestamp: str, now: datetime) -> float:
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return float("inf")
    return max(0.0, (now - _utc(parsed)).total_seconds())
