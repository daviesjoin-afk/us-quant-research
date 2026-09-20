"""Pure presentation projection for the execution page.

This module turns facts the window has *already fetched* into the cards and the
control state the page draws.  It is deliberately Qt-free and service-free: it
imports no PySide6, no ``PaperTradingService``, no repository, no runtime and no
broker.  A presenter that could reach a service would be a second place that
assembles truth, and a presenter that imported Qt could not be tested without a
widget.

The table rows live one module away, in ``rows``: the two halves have different
readers, and a module holding both would have to be read through the half you
did not want.

The convention carried over unchanged from the legacy builder, because it is
operator-visible behaviour rather than implementation detail: **broker truth
wins, then the read-only account snapshot, then the local estimate.**  Each
card's note names which of the three the number came from, so a stale local
estimate can never be mistaken for a broker figure.
"""

from __future__ import annotations

from typing import Callable, Mapping, Sequence

from us_quant.desktop_v2.pages.execution.models import (
    ExecutionCandidatesView,
    ExecutionControlState,
    ExecutionRuntimeView,
    MetricView,
)
from us_quant.desktop_v2.pages.execution.rows import (
    candidate_realtime,
    candidate_rows,
    candidate_static_key,
    fill_rows,
    latency_rows,
    money,
    order_rows,
    position_rows,
    shadow_rows,
)


def _prefer(*options: tuple[object, str]) -> tuple[object, str]:
    """The first present value and its note; the last option is the fallback."""

    for value, note in options[:-1]:
        if value is not None:
            return value, note
    return options[-1]


def runtime_status(snapshot: object) -> MetricView:
    """The session's status card: what it is doing, and the session's own word."""

    active = bool(getattr(snapshot, "active", False))
    paused = bool(getattr(snapshot, "entries_paused", False))
    stopping = bool(getattr(snapshot, "stop_requested", False))
    if stopping and active:
        value = "停止处理中"
    elif paused and active:
        value = "仅管理持仓"
    elif active:
        value = "运行中"
    else:
        value = "已停止"
    return MetricView(value, str(getattr(snapshot, "status", "")))


def account_metrics(
    snapshot: object, account: object | None, broker_state: object | None
) -> tuple[MetricView, MetricView, MetricView, MetricView]:
    """The four account cards, each labelled with where its number came from."""

    equity_value, equity_note = _prefer(
        (getattr(broker_state, "net_liquidation", None), "IBKR Paper 订单会话实时账户摘要"),
        (getattr(account, "net_liquidation", None), "IBKR Paper 只读快照"),
        (getattr(snapshot, "estimated_equity", None), "等待券商刷新；显示本地估算"),
    )
    realized_value, realized_note = _prefer(
        (getattr(broker_state, "realized_pnl", None), "IBKR reqPnL（订单会话）"),
        (getattr(account, "realized_pnl", None), str(getattr(account, "pnl_source", ""))),
        (getattr(snapshot, "estimated_realized_pnl", None), "本地估算"),
    )
    unrealized_value, unrealized_note = _prefer(
        (getattr(broker_state, "unrealized_pnl", None), "IBKR Paper 优先"),
        (getattr(account, "unrealized_pnl", None), "IBKR Paper 优先"),
        (getattr(snapshot, "estimated_unrealized_pnl", None), "IBKR Paper 优先"),
    )
    return (
        MetricView(money(equity_value), equity_note),
        MetricView(money(realized_value, signed=True), realized_note),
        MetricView(money(unrealized_value, signed=True), unrealized_note),
        position_metric(snapshot, broker_state),
    )


def position_metric(snapshot: object, broker_state: object | None) -> MetricView:
    """The position count: broker holdings when there are any, else local."""

    broker_positions = tuple(getattr(broker_state, "positions", ()) or ())
    local_positions = tuple(getattr(snapshot, "positions", ()) or ())
    count = len(broker_positions) if broker_positions else len(local_positions)
    pending = len(tuple(getattr(snapshot, "pending_orders", ()) or ()))
    return MetricView(
        str(count),
        f"在途 {pending} · 完成交易 {getattr(snapshot, 'trades_today', 0)}",
    )


def summary_text(snapshot: object) -> str:
    """The one-line session summary under the controls."""

    session_id = getattr(snapshot, "session_id", None)
    return (
        f"{getattr(snapshot, 'status', '')} · "
        f"候选 {getattr(snapshot, 'candidate_count', 0)} · "
        f"会话风险资金 {money(getattr(snapshot, 'initial_equity', None))} · "
        f"会话 {str(session_id)[:8] if session_id else '无'} · "
        "券商订单与持仓必须以 IBKR Paper 回报为准"
    )


def control_state(
    *,
    launch_locked: bool,
    session_running: bool,
    session_paused: bool,
    reconcile_available: bool,
    resume_ready: bool,
    stream_running: bool,
) -> ExecutionControlState:
    """Translate session facts into which controls may be offered.

    The caller supplies booleans rather than a lifecycle phase so that no module
    in the page package has to know the phase vocabulary, and so this mapping can
    be tested without constructing a workflow controller.

    ``reconcile_available`` and ``resume_ready`` are deliberately separate facts
    rather than one "is the session halted" flag: starting a reconciliation is
    available while halted, and confirming one is available only once the halted
    session has produced a proof and is waiting for a human to look at it.
    """

    idle = not launch_locked
    return ExecutionControlState(
        prepare_enabled=idle,
        start_enabled=idle,
        channel_check_enabled=idle,
        strategy_combo_enabled=idle,
        candidate_limit_enabled=idle,
        capital_limit_enabled=idle,
        arm_confirm_enabled=idle,
        pause_enabled=session_running,
        resume_enabled=session_paused,
        stop_enabled=session_running or session_paused,
        stop_stream_enabled=stream_running and idle,
        reconcile_enabled=reconcile_available,
        resume_reconciliation_enabled=resume_ready,
    )


def build_candidates_view(
    *,
    candidates: Sequence[object],
    quotes: Mapping[str, object],
    recently_ready: Callable[[str], bool],
) -> ExecutionCandidatesView:
    """Project the candidate table alone, for a route with no session yet."""

    return ExecutionCandidatesView(
        candidates=candidate_rows(candidates),
        realtime=candidate_realtime(candidates, quotes, recently_ready),
        static_key=candidate_static_key(candidates),
    )


def build_runtime_view(
    *,
    snapshot: object,
    account: object | None,
    broker_state: object | None,
    broker_positions: Sequence[object],
    quotes: Mapping[str, object],
    pending_by_symbol: Mapping[str, object],
    latency: Sequence[Mapping[str, object]],
    reconciliations: Sequence[object],
    audit_by_intent: Mapping[str, Mapping[str, object]],
    candidates: Sequence[object],
    recently_ready: Callable[[str], bool],
) -> ExecutionRuntimeView:
    """Project one set of already-fetched facts into one renderable view."""

    equity, realized, unrealized, position_count = account_metrics(
        snapshot, account, broker_state
    )
    return ExecutionRuntimeView(
        status=runtime_status(snapshot),
        equity=equity,
        realized=realized,
        unrealized=unrealized,
        position_count=position_count,
        summary=summary_text(snapshot),
        positions=position_rows(snapshot, broker_positions, quotes),
        fills=fill_rows(snapshot),
        shadow=shadow_rows(candidates, quotes, pending_by_symbol),
        latency=latency_rows(latency),
        candidates=build_candidates_view(
            candidates=candidates,
            quotes=quotes,
            recently_ready=recently_ready,
        ),
        orders=order_rows(reconciliations, audit_by_intent),
    )