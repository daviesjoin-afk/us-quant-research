"""Session-side projection for the targeted validation page.

The window hands over already-collected facts.  This module turns them into the
five cards, the two session tables, the Shadow explanation and the preflight
workspace.  It never starts an engine, evaluates eligibility or reads a store.
"""

from __future__ import annotations

from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetPreflightRow,
    TargetPreflightView,
    TargetedControlView,
    TargetedFillRow,
    TargetedMetricView,
    TargetedPositionRow,
    TargetedRowTone,
    TargetedSessionView,
)
from us_quant.desktop_v2.pages.research.targeted.rows import money, price
from us_quant.shadow.models import ShadowSnapshot
from us_quant.targeted_preflight import TargetPreflightResult


def _shadow_metrics(snapshot: ShadowSnapshot | None) -> tuple[TargetedMetricView, ...]:
    if snapshot is None:
        return (
            TargetedMetricView("未启动", "不进入 IBKR 模拟账户"),
            TargetedMetricView("—", "启动时读取 IBKR Paper 净值"),
            TargetedMetricView("$0.00", "已扣双边模拟佣金"),
            TargetedMetricView("$0.00", "按最新有效 mark"),
            TargetedMetricView("0 / 4", "整股；最多一笔持仓"),
        )
    return (
        TargetedMetricView(
            "运行中" if snapshot.active else "已停止",
            f"{snapshot.target_symbol or '未选标的'} · 内部影子成交；券商订单 0",
        ),
        TargetedMetricView(
            money(snapshot.equity),
            f"现金 {money(snapshot.cash)} · 初始 {money(snapshot.initial_cash)}",
        ),
        TargetedMetricView(
            money(snapshot.realized_pnl, signed=True),
            f"累计；当日 {money(snapshot.daily_realized_pnl, signed=True)}",
        ),
        TargetedMetricView(
            money(snapshot.unrealized_pnl, signed=True),
            "不等于 IBKR 账户盈亏",
        ),
        TargetedMetricView(
            f"{snapshot.trades_today} / 4",
            f"影子成交 {len(snapshot.fills)} 笔",
        ),
    )


def _position_rows(
    snapshot: ShadowSnapshot | None,
) -> tuple[TargetedPositionRow, ...]:
    if snapshot is None:
        return ()
    return tuple(
        TargetedPositionRow(
            key=position.symbol,
            values=(
                position.symbol, str(position.quantity), price(position.entry_price),
                position.opened_at, price(position.high_water), position.provider,
                position.coverage, "内部影子",
            ),
        )
        for position in snapshot.positions
    )


def _fill_rows(snapshot: ShadowSnapshot | None) -> tuple[TargetedFillRow, ...]:
    if snapshot is None:
        return ()
    fills = tuple(reversed(snapshot.fills[-200:]))
    rows: list[TargetedFillRow] = []
    for fill in fills:
        tone = TargetedRowTone.NEUTRAL
        if fill.side == "BUY":
            tone = TargetedRowTone.SUCCESS
        elif fill.side == "SELL":
            tone = TargetedRowTone.WARNING
        rows.append(
            TargetedFillRow(
                key=fill.session_id,
                values=(
                    fill.occurred_at, fill.symbol, fill.side, str(fill.quantity),
                    price(fill.price), money(fill.commission),
                    money(fill.realized_pnl, signed=True)
                    if fill.realized_pnl is not None else "—",
                    fill.reason, fill.provider, fill.coverage, fill.session_id[:10],
                ),
                tone=tone,
            )
        )
    return tuple(rows)


def preflight_view(result: TargetPreflightResult | None) -> TargetPreflightView:
    if result is None:
        return TargetPreflightView(
            "输入标的后，这里汇总身份、非中概、行情、资金、整股与策略检查。",
            (),
        )
    optional_failures = sum(
        not gate.passed for gate in result.gates if not gate.blocking
    )
    identity = " · ".join(
        value
        for value in (
            result.symbol, result.company_name, result.security_type,
            f"龙头层级 {result.leader_tier}"
            if result.leader_tier is not None else "",
        )
        if value
    ) or "尚未指定标的"
    summary = (
        f"{'可启动内部策略仿真' if result.shadow_ready else '暂不可启动'} · "
        f"硬门 {result.hard_gates_passed}/{result.hard_gate_count} · {identity}"
        + (f" · {optional_failures} 项研究证据待补" if optional_failures else "")
    )
    rows = tuple(
        TargetPreflightRow(
            key=gate.code,
            values=(
                gate.name, "通过" if gate.passed else "未通过", gate.observed,
                gate.required, gate.category,
                "阻断启动" if gate.blocking else "研究提示",
            ),
            tone=(
                TargetedRowTone.SUCCESS if gate.passed
                else TargetedRowTone.ERROR if gate.blocking
                else TargetedRowTone.WARNING
            ),
        )
        for gate in result.gates
    )
    return TargetPreflightView(summary, rows)


def session_view(
    *,
    snapshot: ShadowSnapshot | None,
    target_status: str,
    minute_status: str,
    preflight: TargetPreflightResult | None,
    controls: TargetedControlView,
) -> TargetedSessionView:
    status, equity, realized, unrealized, trades = _shadow_metrics(snapshot)
    explanation = (
        "状态：未启动。该工具只验证行情→信号→成本后模拟成交→"
        "持仓→盈亏→平仓链路，不以单晚收益证明策略有效。"
        if snapshot is None
        else (
            f"状态：{snapshot.status}\n"
            f"会话：{snapshot.session_id or '无'} · "
            f"策略版本：{snapshot.strategy_version_id} · "
            f"参数：{snapshot.parameter_hash[:12]} · "
            f"目标：{snapshot.target_symbol or '未设置'} · "
            f"交易日：{snapshot.trading_day or '未开始'} · "
            f"资金来源：{snapshot.capital_source} · "
            "环境：internal_shadow · 券商订单接口：不存在"
        )
    )
    return TargetedSessionView(
        status=status,
        equity=equity,
        realized=realized,
        unrealized=unrealized,
        trades=trades,
        explanation=explanation,
        positions=_position_rows(snapshot),
        fills=_fill_rows(snapshot),
        target_status=target_status,
        minute_status=minute_status,
        preflight=preflight_view(preflight),
        controls=controls,
    )


__all__ = ["preflight_view", "session_view"]
