"""Qt-free projections for the native cross-section research page."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from us_quant.desktop_v2.pages.research.cross_section.models import (
    CrossSectionCandidateRow,
    CrossSectionChartRow,
    CrossSectionFoldRow,
    CrossSectionMetricView,
    CrossSectionResearchView,
    CrossSectionSummaryView,
)


def build_cross_section_view(
    report: Mapping[str, Any] | None,
) -> CrossSectionResearchView:
    """Project a raw research report into immutable display facts."""

    if report is None:
        return _empty_view()

    out = _mapping(report.get("out_of_sample"))
    strategy = _mapping(out.get("strategy"))
    stress = _mapping(out.get("cost_2x"))
    folds = _sequence(out.get("folds"))
    scope = _mapping(report.get("scope"))
    gate = _mapping(report.get("promotion_gate"))

    return CrossSectionResearchView(
        has_report=True,
        summary=_summary(strategy, stress, folds, scope, gate),
        chart_rows=_chart_rows(_sequence(report.get("chart_data"))),
        candidates=tuple(_candidate_row(row) for row in folds),
        folds=tuple(_fold_row(row) for row in folds),
    )


def _empty_view() -> CrossSectionResearchView:
    return CrossSectionResearchView(
        has_report=False,
        summary=CrossSectionSummaryView(
            gate=CrossSectionMetricView(
                "硬阻断", "研究代理不得进入影子或实盘"
            ),
            return_proxy=CrossSectionMetricView(
                "—", "不是历史整股可执行收益"
            ),
            drawdown=CrossSectionMetricView("—", "复权价研究曲线"),
            cost_stress=CrossSectionMetricView(
                "—", "佣金与滑点同时翻倍"
            ),
            folds=CrossSectionMetricView("—", "锚定走样本外"),
        ),
        chart_rows=(),
        candidates=(),
        folds=(),
    )


def _summary(
    strategy: Mapping[str, Any],
    stress: Mapping[str, Any],
    folds: Sequence[Any],
    scope: Mapping[str, Any],
    gate: Mapping[str, Any],
) -> CrossSectionSummaryView:
    scenario_equity = scope.get("initial_equity")
    return CrossSectionSummaryView(
        gate=CrossSectionMetricView(
            "通过" if gate.get("passed") else "硬阻断",
            _gate_note(gate),
        ),
        return_proxy=CrossSectionMetricView(
            f"{float(strategy['total_return']):+.1%}",
            _scenario_note(scenario_equity, float(strategy["final_equity"])),
        ),
        drawdown=CrossSectionMetricView(
            f"{float(strategy['max_drawdown']):.1%}",
            f"最差日 {float(strategy['worst_day']):.1%}",
        ),
        cost_stress=CrossSectionMetricView(
            f"{float(stress['total_return']):+.1%}",
            f"期末 ${float(stress['final_equity']):,.0f}",
        ),
        folds=CrossSectionMetricView(
            str(len(folds)), "只计完整 126 日测试折"
        ),
    )


def _gate_note(gate: Mapping[str, Any]) -> str:
    reasons = _sequence(gate.get("reasons"))
    return (
        "；".join(str(reason) for reason in reasons[:2])
        or "缺少可验证的晋级证据"
    )


def _scenario_note(scenario_equity: object, final_equity: float) -> str:
    if isinstance(scenario_equity, (int, float)) and not isinstance(
        scenario_equity, bool
    ):
        return (
            f"情景资金 ${scenario_equity:,.0f} · "
            f"期末 ${final_equity:,.0f} · 仍属探索性"
        )
    return f"期末 ${final_equity:,.0f} · 仍属探索性"


def _chart_rows(rows: Sequence[Any]) -> tuple[CrossSectionChartRow, ...]:
    return tuple(
        CrossSectionChartRow(
            date=str(row["date"]),
            strategy_equity=float(row["strategy_equity"]),
            cost_2x_equity=float(row["cost_2x_equity"]),
        )
        for row in rows
    )


def _candidate_row(row: Mapping[str, Any]) -> CrossSectionCandidateRow:
    return CrossSectionCandidateRow(
        selected=str(row["selected"]),
        oos_return=f"{float(row['oos_return']):+.1%}",
        oos_drawdown=f"{float(row['oos_max_drawdown']):.1%}",
        training_sharpe=f"{float(row['training_sharpe']):.2f}",
        trade_count=str(row["oos_trade_count"]),
        cost_2x_return=f"{float(row['cost_2x_return']):+.1%}",
    )


def _fold_row(row: Mapping[str, Any]) -> CrossSectionFoldRow:
    return CrossSectionFoldRow(
        fold=str(row["fold"]),
        test_interval=f"{row['test_start']} → {row['test_end']}",
        selected=str(row["selected"]),
        oos_return=f"{float(row['oos_return']):+.1%}",
        cost_2x_return=f"{float(row['cost_2x_return']):+.1%}",
        max_risk_exposure=f"{float(row['max_risk_exposure_pct']):.1%}",
        average_cash=f"{float(row['average_cash_pct']):.1%}",
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes)
    ) else ()


__all__ = ["build_cross_section_view"]