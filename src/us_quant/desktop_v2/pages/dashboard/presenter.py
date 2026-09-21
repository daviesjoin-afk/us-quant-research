"""Qt-free projection for the Dashboard route.

The window owns broker, market, research and chart facts.  This module turns
those facts into an immutable :class:`DashboardView`; it imports no widget
toolkit and calls no service, loader, repository or runtime.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol, Sequence

from us_quant.desktop_v2.pages.dashboard.models import (
    DashboardArtifactRowView,
    DashboardArtifactTone,
    DashboardChartView,
    DashboardMetricView,
    DashboardView,
)
from us_quant.trading.domain.account import BrokerAccountPortfolio
from us_quant.trading.domain.market import MarketSnapshot


#: Stable source ids used by the market-data domain.  The Dashboard is a
#: presentation surface, so this mapping lives in the presenter rather than in
#: an application service.
_PUSH_LISTENER_SOURCES = frozenset({"alpaca_iex", "finnhub_trades"})
_ALPACA_IEX_SOURCE = "alpaca_iex"

_STATUS_TRANSLATIONS = {
    "research_exploratory": "探索性研究",
    "legacy_invalidated": "旧结果·已失效",
    "load_error": "读取失败",
}

RESEARCH_BOUNDARY_TEXT = (
    "账户实况：尚未连接，只显示离线研究资源\n"
    "旧 +205.6%：已封存为不可部署结果\n\n"
    "• 中国概念股：全部关闭\n"
    "• 交易单位：只允许整股\n"
    "• 核心：板块龙头；优质二线可观察\n"
    "• 广域后排：研究样本，不直接进入交易池\n"
    "• 杠杆 ETF：单独折算风险，仅限短期研究\n"
    "• 自动下单：关闭"
)


class ArtifactFact(Protocol):
    """The artifact fields the Dashboard presents."""

    artifact_type: str
    status: str
    data_as_of: str | None
    generated_at: str | None
    source: str
    run_id: str
    limitations: tuple[str, ...]


def _money(
    value: Decimal | float | int | None,
    *,
    signed: bool = False,
) -> str:
    if value is None:
        return "不可用"
    number = float(value)
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}${number:,.2f}"


def account_metrics(
    portfolio: BrokerAccountPortfolio | None,
) -> tuple[DashboardMetricView, DashboardMetricView, DashboardMetricView]:
    """Project broker account truth onto the three summary cards."""

    if portfolio is None:
        return (
            DashboardMetricView("未读取", "到账户与持仓页执行只读刷新"),
            DashboardMetricView("不可用", "不会以研究收益代替"),
            DashboardMetricView("未读取", "券商空仓与未读取严格区分"),
        )
    account = portfolio.account
    return (
        DashboardMetricView(
            _money(account.net_liquidation),
            f"IBKR {account.environment.value.title()} · "
            f"{account.account_alias}",
        ),
        DashboardMetricView(
            _money(account.daily_pnl, signed=True),
            "券商 reqPnL；不含回测",
        ),
        DashboardMetricView(
            str(len(portfolio.positions)),
            "当前券商持仓",
        ),
    )


def market_metric(
    snapshot: MarketSnapshot | None,
    *,
    stopped_reason: str | None = None,
) -> DashboardMetricView:
    """Project the current market snapshot onto the intraday-availability card."""

    if stopped_reason is not None:
        return DashboardMetricView("不可用", stopped_reason)
    if snapshot is None:
        return DashboardMetricView("不可用", "尚未启动流行情")
    if not snapshot.realtime_ready:
        return DashboardMetricView("不可用", snapshot.message[:42])
    if snapshot.source_id in _PUSH_LISTENER_SOURCES:
        note = (
            "Alpaca IEX 单交易所实时"
            if snapshot.source_id == _ALPACA_IEX_SOURCE
            else "Finnhub 实时成交+明确模拟执行带"
        )
    else:
        note = "fresh 实时 + bid/ask"
    return DashboardMetricView("可用", note)


def artifact_row(artifact: ArtifactFact) -> DashboardArtifactRowView:
    """Translate one artifact-provenance fact into one table row."""

    if artifact.status == "legacy_invalidated":
        tone = DashboardArtifactTone.WARNING
    elif artifact.status == "load_error":
        tone = DashboardArtifactTone.ERROR
    else:
        tone = DashboardArtifactTone.NEUTRAL
    return DashboardArtifactRowView(
        artifact_type=artifact.artifact_type,
        status_text=_STATUS_TRANSLATIONS.get(
            artifact.status, artifact.status
        ),
        data_as_of=artifact.data_as_of or "未知",
        generated_at=artifact.generated_at or "未知",
        source=artifact.source,
        run_id=artifact.run_id[:12],
        limitations="；".join(artifact.limitations[:3]) or "无",
        tone=tone,
    )


def build_dashboard_view(
    *,
    portfolio: BrokerAccountPortfolio | None,
    snapshot: MarketSnapshot | None,
    artifacts: Sequence[ArtifactFact],
    chart: DashboardChartView,
    market_stop_reason: str | None = None,
) -> DashboardView:
    """Project already-computed facts into one immutable renderable view."""

    net_liquidation, daily_pnl, positions = account_metrics(portfolio)
    return DashboardView(
        net_liquidation=net_liquidation,
        daily_pnl=daily_pnl,
        positions=positions,
        intraday_market=market_metric(
            snapshot, stopped_reason=market_stop_reason
        ),
        artifacts=tuple(artifact_row(item) for item in artifacts),
        chart=chart,
        research_boundary_text=RESEARCH_BOUNDARY_TEXT,
    )


__all__ = [
    "ArtifactFact",
    "RESEARCH_BOUNDARY_TEXT",
    "account_metrics",
    "artifact_row",
    "build_dashboard_view",
    "market_metric",
]
