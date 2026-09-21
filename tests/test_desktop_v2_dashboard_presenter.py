"""Pure projection tests for the Dashboard presenter."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from us_quant.desktop_v2.pages.dashboard.models import (
    DashboardArtifactTone,
    DashboardChartView,
)
from us_quant.desktop_v2.pages.dashboard.presenter import (
    RESEARCH_BOUNDARY_TEXT,
    account_metrics,
    artifact_row,
    build_dashboard_view,
    market_metric,
)
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
    BrokerPositionSnapshot,
)
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def _account() -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        environment=Environment.PAPER,
        account_alias="DU***67",
        net_liquidation=Decimal("12345.67"),
        cash=Decimal("5000"),
        available_funds=Decimal("4000"),
        buying_power=Decimal("8000"),
        gross_position_value=Decimal("1234"),
        excess_liquidity=Decimal("3000"),
        maintenance_margin=Decimal("1000"),
        cushion=Decimal("0.75"),
        daily_pnl=Decimal("-12.5"),
        unrealized_pnl=Decimal("3"),
        realized_pnl=Decimal("-1"),
        observed_at=NOW,
        pnl_source="IBKR reqPnL",
    )


def _position() -> BrokerPositionSnapshot:
    return BrokerPositionSnapshot(
        account_alias="DU***67",
        con_id=1,
        symbol="AAPL",
        local_symbol="AAPL",
        security_type="STK",
        exchange="SMART",
        currency="USD",
        quantity=Decimal("2"),
        average_cost=Decimal("36"),
        market_value=Decimal("72"),
        daily_pnl=Decimal("1.5"),
        unrealized_pnl=Decimal("0.5"),
        realized_pnl=Decimal("0"),
        observed_at=NOW,
    )


def _portfolio() -> BrokerAccountPortfolio:
    return BrokerAccountPortfolio(_account(), (_position(),))


def _quote(**overrides) -> MarketQuote:
    values = dict(
        symbol="SPY",
        bid=Decimal("100"),
        ask=Decimal("100.1"),
        last=Decimal("100.05"),
        close=Decimal("99"),
        bid_size=None,
        ask_size=None,
        mode=MarketDataMode.REALTIME,
        updated_at=NOW,
        age_seconds=0.2,
        stale=False,
        stale_reason=None,
        generation=1,
        source_id="alpaca_iex",
        source_label="Alpaca",
        coverage="IEX",
    )
    values.update(overrides)
    return MarketQuote(**values)  # type: ignore[arg-type]


def _snapshot(*, quotes=(), source_id="alpaca_iex", message="") -> MarketSnapshot:
    return MarketSnapshot(
        generation=1,
        connected=True,
        ready=True,
        reconnect_attempt=0,
        quotes=tuple(quotes),
        error_code=None,
        message=message,
        observed_at=NOW,
        source_id=source_id,
        source_label=source_id,
        coverage="test",
    )


def _artifact(**overrides):
    values = dict(
        artifact_type="market_scan",
        status="research_exploratory",
        data_as_of=None,
        generated_at=None,
        source="local",
        run_id="1234567890abcdef",
        limitations=(),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_no_portfolio_keeps_unread_and_unavailable_distinct() -> None:
    net, pnl, positions = account_metrics(None)
    assert (net.value, net.note) == (
        "未读取",
        "到账户与持仓页执行只读刷新",
    )
    assert (pnl.value, pnl.note) == ("不可用", "不会以研究收益代替")
    assert (positions.value, positions.note) == (
        "未读取",
        "券商空仓与未读取严格区分",
    )


def test_portfolio_metrics_are_broker_truth_not_research_returns() -> None:
    net, pnl, positions = account_metrics(_portfolio())
    assert net.value == "$12,345.67"
    assert net.note == "IBKR Paper · DU***67"
    assert pnl.value == "$-12.50"
    assert pnl.note == "券商 reqPnL；不含回测"
    assert positions.value == "1"
    assert positions.note == "当前券商持仓"


def test_market_metric_preserves_no_snapshot_and_not_ready_copy() -> None:
    assert market_metric(None).value == "不可用"
    assert market_metric(None).note == "尚未启动流行情"
    stopped = market_metric(None, stopped_reason="行情流已停止")
    assert (stopped.value, stopped.note) == ("不可用", "行情流已停止")
    not_ready = market_metric(_snapshot(message="门控未满足" * 20))
    assert not_ready.value == "不可用"
    assert not_ready.note == ("门控未满足" * 20)[:42]


def test_market_metric_preserves_provider_specific_ready_copy() -> None:
    alpaca = market_metric(_snapshot(quotes=(_quote(),)))
    assert (alpaca.value, alpaca.note) == (
        "可用",
        "Alpaca IEX 单交易所实时",
    )
    finnhub = market_metric(
        _snapshot(
            quotes=(_quote(source_id="finnhub_trades"),),
            source_id="finnhub_trades",
        )
    )
    assert (finnhub.value, finnhub.note) == (
        "可用",
        "Finnhub 实时成交+明确模拟执行带",
    )
    ibkr = market_metric(
        _snapshot(
            quotes=(_quote(source_id="ibkr"),),
            source_id="ibkr",
        )
    )
    assert (ibkr.value, ibkr.note) == ("可用", "fresh 实时 + bid/ask")


def test_artifact_rows_preserve_translation_truncation_and_tone() -> None:
    legacy = artifact_row(
        _artifact(
            status="legacy_invalidated",
            limitations=("a", "b", "c", "d"),
        )
    )
    assert legacy.status_text == "旧结果·已失效"
    assert legacy.run_id == "1234567890ab"
    assert legacy.limitations == "a；b；c"
    assert legacy.tone is DashboardArtifactTone.WARNING

    error = artifact_row(_artifact(status="load_error"))
    assert error.status_text == "读取失败"
    assert error.tone is DashboardArtifactTone.ERROR

    exploratory = artifact_row(
        _artifact(status="research_exploratory", limitations=())
    )
    assert exploratory.status_text == "探索性研究"
    assert exploratory.data_as_of == "未知"
    assert exploratory.generated_at == "未知"
    assert exploratory.limitations == "无"
    assert exploratory.tone is DashboardArtifactTone.NEUTRAL


def test_build_view_carries_the_research_boundary_verbatim() -> None:
    view = build_dashboard_view(
        portfolio=None,
        snapshot=None,
        artifacts=(_artifact(),),
        chart=DashboardChartView("SPY", ()),
    )
    assert view.research_boundary_text == RESEARCH_BOUNDARY_TEXT
    assert "中国概念股：全部关闭" in view.research_boundary_text
