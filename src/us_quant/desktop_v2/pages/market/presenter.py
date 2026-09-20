"""Pure presentation projection for the market page.

This module turns facts the window has *already computed* into the cards, the
rows and the control state the page draws.  It is deliberately Qt-free and
service-free: it imports no PySide6, no ``MarketDataApplication``, no
``StreamWorker``, no credential store and no adapter.  A presenter that could
start a feed would be a second orchestration point, and a presenter that
imported Qt could not be tested without a widget.

Two boundaries are load-bearing here:

* the readiness counts and the scope line arrive as finished facts.  Both depend
  on things this route does not own -- auto-quant candidates, market reference
  symbols, the universe and the scan -- so the window computes them and hands
  over the numbers;
* whether a control is enabled is a *fact* the window supplies, not something
  derived here.  The page must not be able to work out that a stop is allowed,
  because that answer depends on Paper and Shadow session state.

The table rows live one module away, in ``rows``: the two halves have different
readers, and the projection rules that produce a display string belong with the
other projections.
"""

from __future__ import annotations

from typing import Sequence

from us_quant.desktop_v2.pages.market.models import (
    MarketControlView,
    MarketMetricView,
    MarketPageView,
    MarketQuoteRow,
    MarketReadinessFacts,
)
from us_quant.desktop_v2.pages.market.rows import (
    TRADE_FEED_SOURCES,
    connection_label,
    feed_label,
    price,
)


def connection_metric(snapshot: object | None) -> MarketMetricView:
    """The connection card: how far the handshake got, and at which generation."""

    if snapshot is None:
        return MarketMetricView("未启动", "外部或 IBKR 独立只读 client")
    return MarketMetricView(
        connection_label(snapshot),
        f"连接代次 {snapshot.generation} · "
        f"尝试 {snapshot.reconnect_attempt}",
    )


def feed_metric(snapshot: object | None) -> MarketMetricView:
    """The feed-type card: what kind of feed this is, and what it covers."""

    if snapshot is None:
        return MarketMetricView("未知", "以 marketDataType 回调为准")
    modes = {quote.mode for quote in getattr(snapshot, "quotes", ()) or ()}
    return MarketMetricView(
        feed_label(source_id=snapshot.source_id, modes=tuple(modes)),
        snapshot.coverage,
    )


def readiness_metric(facts: MarketReadinessFacts | None) -> MarketMetricView:
    """The intraday-readiness card.

    With candidates the card leads on them, because that is the number that
    decides whether a session can trade; without candidates it falls back to the
    subscription tally, which is all there is to report.
    """

    if facts is None:
        return MarketMetricView("否", "必须 fresh Type 1 + bid/ask")
    if facts.candidate_count:
        return MarketMetricView(
            f"可下单候选 {facts.candidate_current_count}/"
            f"{facts.candidate_count} · 市场参考 "
            f"{facts.reference_current_count}/{facts.reference_count}",
            f"近30秒候选 {facts.candidate_recent_count}/"
            f"{facts.candidate_count} · 参考 "
            f"{facts.reference_recent_count}/{facts.reference_count} · "
            f"订阅合计 {facts.subscription_current_count}/"
            f"{facts.subscription_count}",
        )
    return MarketMetricView(
        f"订阅合计 {facts.subscription_current_count}/"
        f"{facts.subscription_count}",
        f"当前 fresh；近30秒 {facts.subscription_recent_count}/"
        f"{facts.subscription_count}",
    )


def watchlist_metric(
    *, symbol_count: int, note: str | None = None
) -> MarketMetricView:
    """The subscription-subset card."""

    return MarketMetricView(
        str(symbol_count),
        note or "最多 30；不等于全市场研究池",
    )


def health_text(snapshot: object | None) -> str:
    """The health panel's body for a snapshot, or the idle preface."""

    if snapshot is None:
        return IDLE_HEALTH
    error_line = (
        f"最近错误：{snapshot.source_label} {snapshot.error_code}"
        if snapshot.error_code is not None
        else "最近错误：无"
    )
    return (
        f"连接代次：{snapshot.generation}\n"
        f"来源：{snapshot.source_label}\n"
        f"覆盖：{snapshot.coverage}\n"
        f"Socket：{'连接' if snapshot.connected else '断开'}\n"
        f"协议握手：{'完成' if snapshot.ready else '未完成'}\n"
        f"{error_line}\n"
        f"最近事件：{snapshot.message}\n\n"
        f"{gate_explanation(snapshot.source_id)}"
    )


def failure_text(message: str) -> str:
    """The health panel's body after the feed failed."""

    return f"流服务失败：{message}\n\n可继续离线研究。"


def gate_explanation(source_id: str) -> str:
    """Which gate this source is subject to, spelled out for the operator."""

    if source_id in TRADE_FEED_SOURCES:
        return (
            "门控：Finnhub 只把 fresh 实时成交用于信号；"
            "显示的 bid/ask 是 ±5bps 影子执行带，不是市场盘口。"
        )
    return (
        "门控：只有 fresh Type 1 且 bid/ask 完整的行情，"
        "才可被标记为日内可用。Type 2/3/4 不会静默升级。"
    )


def control_view(
    *,
    worker_running: bool,
    stop_pending: bool,
    symbols_enabled: bool,
    provider_enabled: bool,
) -> MarketControlView:
    """Translate the window's stream facts into which controls are offered.

    ``stop_enabled`` follows the worker rather than the snapshot: a stop must be
    offered exactly while there is a thread to stop, and must close the moment
    the stop has been requested and the thread has not yet confirmed exit, or a
    second stop would be admitted against a dying worker.
    """

    return MarketControlView(
        start_enabled=True,
        stop_enabled=worker_running and not stop_pending,
        symbols_enabled=symbols_enabled,
        provider_enabled=provider_enabled,
        load_watchlist_enabled=not worker_running,
        start_label=(
            "切换 / 重连行情" if worker_running else "启动只读流行情"
        ),
    )


def build_market_view(
    *,
    snapshot: object | None,
    readiness: MarketReadinessFacts | None,
    scope: str,
    rows: Sequence[MarketQuoteRow],
    controls: MarketControlView,
    watchlist_note: str | None = None,
) -> MarketPageView:
    """Project one set of already-computed facts into one renderable view."""

    empty_message = None
    if snapshot is None:
        empty_message = IDLE_EMPTY
    elif not snapshot.quotes:
        empty_message = WAITING_EMPTY
    return MarketPageView(
        connection=connection_metric(snapshot),
        feed=feed_metric(snapshot),
        readiness=readiness_metric(readiness),
        watchlist=watchlist_metric(
            symbol_count=len(snapshot.quotes) if snapshot is not None else 0,
            note=watchlist_note,
        ),
        scope=scope,
        empty_message=empty_message,
        rows=tuple(rows),
        health_text=health_text(snapshot),
        controls=controls,
    )


#: The health panel before any snapshot exists, migrated verbatim.
IDLE_HEALTH = (
    "• 行情类型只相信 IBKR marketDataType 回调\n"
    "• 外部首选 Alpaca IEX 免费实时，明确标注单交易所覆盖\n"
    "• Finnhub 是实时成交；±5bps 影子带不是市场 bid/ask\n"
    "• Type 2/3/4 只可观察，不进入日内信号\n"
    "• 10197、1100、1300、缺 bid/ask、超时均硬性 stale\n"
    "• 客户端硬禁下单、撤单、全撤、行权和 FA 修改\n"
    "• 当前未启动流服务"
)

IDLE_EMPTY = (
    "尚未启动行情。选择数据源并在设置页保存凭据后启动；"
    "表格会明确区分 READY、STALE、延迟和模拟执行带。"
)
WAITING_EMPTY = "已连接，等待首个 fresh bid/ask。"


__all__ = [
    "IDLE_EMPTY",
    "IDLE_HEALTH",
    "WAITING_EMPTY",
    "build_market_view",
    "connection_metric",
    "control_view",
    "failure_text",
    "feed_metric",
    "gate_explanation",
    "health_text",
    "price",
    "readiness_metric",
    "watchlist_metric",
]
