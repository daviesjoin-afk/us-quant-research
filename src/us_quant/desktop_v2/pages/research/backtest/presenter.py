"""Qt-free projections for the native backtest page."""

from __future__ import annotations

from collections.abc import Sequence

from us_quant.backtest_workspace import BacktestRun

from us_quant.desktop_v2.pages.research.backtest.models import (
    BacktestChartView,
    BacktestComparisonRow,
    BacktestControlView,
    BacktestDetailView,
    BacktestMetricView,
    BacktestPageView,
    BacktestSummaryView,
    BacktestTradeRow,
)


EMPTY_EVIDENCE = (
    "研究代理：信号在收盘生成、次日开盘成交；默认复权日 K，"
    "不等于历史可执行整股成交。"
)


def build_control_view(*, busy: bool) -> BacktestControlView:
    enabled = not busy
    return BacktestControlView(
        run_selected_enabled=enabled,
        compare_all_enabled=enabled,
    )


def build_backtest_view(
    runs: Sequence[BacktestRun],
    selected_run_id: str | None,
    *,
    busy: bool,
) -> BacktestPageView:
    ordered = tuple(runs)
    selected = _selected_run(ordered, selected_run_id)
    return BacktestPageView(
        controls=build_control_view(busy=busy),
        runs=tuple(_comparison_row(run) for run in ordered),
        selected_run_id=selected.run_id if selected is not None else None,
        detail=_detail_view(selected),
    )


def comparison_rows(
    runs: Sequence[BacktestRun],
) -> tuple[BacktestComparisonRow, ...]:
    return tuple(_comparison_row(run) for run in runs)


def _selected_run(
    runs: tuple[BacktestRun, ...],
    selected_run_id: str | None,
) -> BacktestRun | None:
    for run in runs:
        if run.run_id == selected_run_id:
            return run
    return runs[0] if runs else None


def _comparison_row(run: BacktestRun) -> BacktestComparisonRow:
    return BacktestComparisonRow(
        run_id=run.run_id,
        run_id_display=run.run_id[:8],
        strategy=(
            f"{run.strategy.name} · "
            f"{run.request.strategy_version_id[:8]}"
        ),
        symbol=run.request.symbol,
        interval=f"{run.first_date} → {run.last_date}",
        total_return=f"{run.result.total_return:+.2%}",
        annualized_return=f"{run.metrics.annualized_return:+.2%}",
        sharpe=f"{run.metrics.annualized_sharpe:.2f}",
        sortino=f"{run.metrics.annualized_sortino:.2f}",
        calmar=f"{run.metrics.calmar_ratio:.2f}",
        max_drawdown=f"{run.result.max_drawdown:.2%}",
        turnover=f"{run.metrics.turnover:.2f}x",
        trade_count=str(len(run.result.trades)),
        commission=f"${run.result.total_commission:,.2f}",
    )


def _detail_view(run: BacktestRun | None) -> BacktestDetailView:
    if run is None:
        return BacktestDetailView(
            run_id=None,
            summary=_empty_summary(),
            chart=None,
            trades=(),
            evidence=EMPTY_EVIDENCE,
        )
    return BacktestDetailView(
        run_id=run.run_id,
        summary=_summary(run),
        chart=_chart(run),
        trades=tuple(_trade_row(trade) for trade in run.result.trades),
        evidence=_evidence(run),
    )


def _empty_summary() -> BacktestSummaryView:
    return BacktestSummaryView(
        total_return=BacktestMetricView("—", "运行后显示"),
        cagr=BacktestMetricView("—", "按 252 个交易日估算"),
        sharpe=BacktestMetricView("—", "无风险利率暂按 0"),
        drawdown=BacktestMetricView("—", "收盘权益序列"),
        trades=BacktestMetricView("—", "整股、佣金与滑点"),
    )


def _summary(run: BacktestRun) -> BacktestSummaryView:
    return BacktestSummaryView(
        total_return=BacktestMetricView(
            f"{run.result.total_return:+.2%}",
            f"期末 ${run.result.final_equity:,.2f}",
        ),
        cagr=BacktestMetricView(
            f"{run.metrics.annualized_return:+.2%}",
            f"{run.first_date} → {run.last_date}",
        ),
        sharpe=BacktestMetricView(
            f"{run.metrics.annualized_sharpe:.2f}",
            f"最差日 {run.metrics.worst_day:.2%}",
        ),
        drawdown=BacktestMetricView(
            f"{run.result.max_drawdown:.2%}",
            f"正收益日 {run.metrics.positive_day_ratio:.1%}",
        ),
        trades=BacktestMetricView(
            str(len(run.result.trades)),
            f"佣金 ${run.result.total_commission:,.2f}",
        ),
    )


def _chart(run: BacktestRun) -> BacktestChartView:
    return BacktestChartView(
        symbol=run.request.symbol,
        points=tuple(
            (timestamp.date(), float(equity))
            for timestamp, equity in run.result.equity_curve
        ),
        title=(
            f"{run.strategy.name} · 权益曲线 · "
            f"Run {run.run_id[:8]}"
        ),
    )


def _trade_row(trade: object) -> BacktestTradeRow:
    side = getattr(trade.side, "value", trade.side)
    return BacktestTradeRow(
        signal_date=trade.signal_timestamp.date().isoformat(),
        fill_date=trade.timestamp.date().isoformat(),
        signal_symbol=trade.signal_symbol,
        execution_symbol=trade.execution_symbol,
        side="买入" if side == "buy" else "卖出",
        quantity=str(trade.quantity),
        raw_price=f"${trade.raw_price:,.4f}",
        fill_price=f"${trade.fill_price:,.4f}",
        slippage_cost=f"${trade.slippage_cost:,.2f}",
        commission=f"${trade.commission:,.2f}",
        position_after=str(trade.position_after),
        cash_after=f"${trade.cash_after:,.2f}",
        reason=(
            f"{trade.reason}"
            + (" · 替代映射" if trade.used_substitution else "")
        ),
    )


def _evidence(run: BacktestRun) -> str:
    return (
        f"数据：{run.data_source} · {run.price_basis} · "
        f"data hash {run.data_hash[:12]} · "
        f"parameter hash {run.request.parameter_hash[:12]}；"
        "研究代理，不代表历史可成交表现。"
    )


__all__ = [
    "EMPTY_EVIDENCE",
    "build_backtest_view",
    "build_control_view",
    "comparison_rows",
]
