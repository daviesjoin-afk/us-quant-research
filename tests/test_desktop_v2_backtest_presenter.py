"""Qt-free tests for the native backtest presenter."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from us_quant.backtest import BacktestResult, BacktestTrade
from us_quant.backtest_workspace import (
    BacktestMetrics,
    BacktestRequest,
    BacktestRun,
    StrategySpec,
)
from us_quant.desktop_v2.pages.research.backtest.presenter import (
    EMPTY_EVIDENCE,
    build_backtest_view,
    build_control_view,
)
from us_quant.trading.domain.orders import Side


def _run(
    run_id: str,
    *,
    symbol: str = "AAPL",
    version_id: str = "version-1234567890",
    total_return: str = "0.25",
    annualized_return: str = "0.20",
    sharpe: str = "1.25",
    sortino: str = "1.75",
    calmar: str = "0.80",
    drawdown: str = "0.12",
    turnover: str = "3.5",
    commission: str = "2.50",
) -> BacktestRun:
    request = BacktestRequest(
        strategy_id="dual-ma-trend",
        strategy_version_id=version_id,
        parameter_hash="parameter-hash-123456",
        code_hash="code-hash",
        parameters={"short_window": 20},
        symbol=symbol,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        initial_equity=Decimal("1500"),
        target_weight=Decimal("1"),
        per_share_commission=Decimal("0.005"),
        minimum_commission=Decimal("1.00"),
        slippage_bps=Decimal("4.0"),
    )
    trades = (
        BacktestTrade(
            signal_timestamp=datetime(2025, 1, 2, tzinfo=timezone.utc),
            timestamp=datetime(2025, 1, 3, tzinfo=timezone.utc),
            signal_symbol=symbol,
            execution_symbol=symbol,
            side=Side.BUY,
            quantity=5,
            raw_price=Decimal("100.1234"),
            fill_price=Decimal("100.1634"),
            notional=Decimal("500.817"),
            slippage_cost=Decimal("0.20"),
            commission=Decimal("1.50"),
            position_after=5,
            cash_after=Decimal("998.00"),
            reason="趋势入场",
            used_substitution=True,
        ),
        BacktestTrade(
            signal_timestamp=datetime(2025, 2, 2, tzinfo=timezone.utc),
            timestamp=datetime(2025, 2, 3, tzinfo=timezone.utc),
            signal_symbol=symbol,
            execution_symbol=symbol,
            side=Side.SELL,
            quantity=5,
            raw_price=Decimal("101.0000"),
            fill_price=Decimal("100.9596"),
            notional=Decimal("504.798"),
            slippage_cost=Decimal("0.20"),
            commission=Decimal("1.00"),
            position_after=0,
            cash_after=Decimal("1501.00"),
            reason="趋势退出",
            used_substitution=False,
        ),
    )
    result = BacktestResult(
        initial_equity=Decimal("1500"),
        final_equity=Decimal("1875.00"),
        total_return=Decimal(total_return),
        max_drawdown=Decimal(drawdown),
        total_commission=Decimal(commission),
        trades=trades,
        equity_curve=(
            (datetime(2025, 1, 2, tzinfo=timezone.utc), Decimal("1500")),
            (datetime(2025, 1, 3, tzinfo=timezone.utc), Decimal("1510.5")),
        ),
    )
    metrics = BacktestMetrics(
        annualized_return=Decimal(annualized_return),
        annualized_sharpe=Decimal(sharpe),
        annualized_sortino=Decimal(sortino),
        calmar_ratio=Decimal(calmar),
        annualized_volatility=Decimal("0.20"),
        turnover=Decimal(turnover),
        worst_day=Decimal("-0.035"),
        positive_day_ratio=Decimal("0.575"),
    )
    return BacktestRun(
        run_id=run_id,
        request=request,
        strategy=StrategySpec(
            strategy_id="dual-ma-trend",
            name="双均线趋势",
            description="test",
            default_parameters={},
        ),
        data_source="IBKR",
        data_hash="datahash-1234567890",
        price_basis="adjusted",
        first_date=date(2025, 1, 2),
        last_date=date(2025, 12, 31),
        result=result,
        metrics=metrics,
    )


def test_empty_runs_project_empty_detail_and_frozen_notes() -> None:
    view = build_backtest_view((), None, busy=False)
    assert view.runs == ()
    assert view.selected_run_id is None
    assert view.detail.run_id is None
    assert view.detail.chart is None
    assert view.detail.trades == ()
    assert view.detail.evidence == EMPTY_EVIDENCE
    assert view.detail.summary.total_return.value == "—"
    assert view.detail.summary.total_return.note == "运行后显示"
    assert view.detail.summary.cagr.note == "按 252 个交易日估算"
    assert view.detail.summary.sharpe.note == "无风险利率暂按 0"
    assert view.detail.summary.drawdown.note == "收盘权益序列"
    assert view.detail.summary.trades.note == "整股、佣金与滑点"


def test_comparison_row_formatting_matches_legacy() -> None:
    view = build_backtest_view((_run("run-1234567890"),), None, busy=False)
    row = view.runs[0]
    assert row.run_id == "run-1234567890"
    assert row.run_id_display == "run-1234"
    assert row.strategy == "双均线趋势 · version-"
    assert row.symbol == "AAPL"
    assert row.interval == "2025-01-02 → 2025-12-31"
    assert row.total_return == "+25.00%"
    assert row.annualized_return == "+20.00%"
    assert row.sharpe == "1.25"
    assert row.sortino == "1.75"
    assert row.calmar == "0.80"
    assert row.max_drawdown == "12.00%"
    assert row.turnover == "3.50x"
    assert row.trade_count == "2"
    assert row.commission == "$2.50"


def test_selected_run_detail_and_invalid_fallback() -> None:
    first = _run("run-A", symbol="AAPL")
    second = _run("run-B", symbol="MSFT")
    selected = build_backtest_view((first, second), "run-B", busy=False)
    assert selected.selected_run_id == "run-B"
    assert selected.detail.run_id == "run-B"
    assert selected.detail.chart is not None
    assert selected.detail.chart.symbol == "MSFT"

    fallback = build_backtest_view((first, second), "missing", busy=False)
    assert fallback.selected_run_id == "run-A"
    assert fallback.detail.run_id == "run-A"


def test_metric_semantics_are_frozen() -> None:
    view = build_backtest_view((_run("run-A"),), "run-A", busy=False)
    summary = view.detail.summary
    assert summary.total_return.value == "+25.00%"
    assert summary.total_return.note == "期末 $1,875.00"
    assert summary.cagr.value == "+20.00%"
    assert summary.cagr.note == "2025-01-02 → 2025-12-31"
    assert summary.sharpe.value == "1.25"
    assert summary.sharpe.note == "最差日 -3.50%"
    assert summary.drawdown.value == "12.00%"
    assert summary.drawdown.note == "正收益日 57.5%"
    assert summary.trades.value == "2"
    assert summary.trades.note == "佣金 $2.50"


def test_equity_curve_projection_and_title() -> None:
    view = build_backtest_view((_run("run-abcdefgh"),), None, busy=False)
    chart = view.detail.chart
    assert chart is not None
    assert chart.symbol == "AAPL"
    assert chart.points == (
        (date(2025, 1, 2), 1500.0),
        (date(2025, 1, 3), 1510.5),
    )
    assert chart.title == "双均线趋势 · 权益曲线 · Run run-abcd"


def test_trade_rows_translate_side_and_substitution() -> None:
    view = build_backtest_view((_run("run-A"),), None, busy=False)
    buy, sell = view.detail.trades
    assert buy.signal_date == "2025-01-02"
    assert buy.fill_date == "2025-01-03"
    assert buy.side == "买入"
    assert buy.raw_price == "$100.1234"
    assert buy.fill_price == "$100.1634"
    assert buy.slippage_cost == "$0.20"
    assert buy.commission == "$1.50"
    assert buy.reason == "趋势入场 · 替代映射"
    assert sell.side == "卖出"
    assert sell.reason == "趋势退出"


def test_evidence_hashes_are_limited_to_twelve_chars() -> None:
    view = build_backtest_view((_run("run-A"),), None, busy=False)
    assert view.detail.evidence == (
        "数据：IBKR · adjusted · data hash datahash-123 · "
        "parameter hash parameter-ha；研究代理，不代表历史可成交表现。"
    )


def test_control_view_only_tracks_busy() -> None:
    assert build_control_view(busy=False).run_selected_enabled is True
    assert build_control_view(busy=False).compare_all_enabled is True
    assert build_control_view(busy=True).run_selected_enabled is False
    assert build_control_view(busy=True).compare_all_enabled is False
