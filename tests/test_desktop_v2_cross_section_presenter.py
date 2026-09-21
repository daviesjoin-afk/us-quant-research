"""Qt-free projection tests for the native cross-section page."""

from __future__ import annotations

from copy import deepcopy

from us_quant.desktop_v2.pages.research.cross_section.presenter import (
    build_cross_section_view,
)


def _fold(number: int = 1) -> dict:
    return {
        "fold": number,
        "train_start": "2022-01-03",
        "train_end": "2023-12-29",
        "test_start": "2024-01-02",
        "test_end": "2024-06-28",
        "selected": "momentum_63_weekly_top3",
        "training_sharpe": 1.25,
        "oos_return": 0.1,
        "oos_max_drawdown": 0.05,
        "oos_commission": 12.5,
        "oos_trade_count": 12,
        "average_cash_pct": 0.2,
        "max_risk_exposure_pct": 0.6,
        "unaffordable_signal_count": 1,
        "substitution_forced_exit_count": 0,
        "max_substitution_holding_days_observed": 2,
        "cost_2x_return": 0.08,
    }


def _report() -> dict:
    return {
        "scope": {"initial_equity": 2500.0},
        "out_of_sample": {
            "strategy": {
                "initial_equity": 2500.0,
                "final_equity": 3000.0,
                "total_return": 0.2,
                "max_drawdown": 0.1,
                "annualized_sharpe": 1.2,
                "worst_day": -0.03,
            },
            "cost_2x": {
                "final_equity": 2800.0,
                "total_return": 0.12,
            },
            "folds": [_fold(1), _fold(2)],
        },
        "chart_data": [
            {
                "date": "2024-01-02",
                "strategy_equity": 2500.0,
                "cost_2x_equity": 2500.0,
            },
            {
                "date": "2024-01-03",
                "strategy_equity": 2525.0,
                "cost_2x_equity": 2510.0,
            },
        ],
        "promotion_gate": {
            "passed": False,
            "reasons": ["第一理由", "第二理由", "第三理由"],
        },
    }


def test_none_report_builds_the_legacy_empty_view() -> None:
    view = build_cross_section_view(None)
    assert view.has_report is False
    assert view.summary.gate.value == "硬阻断"
    assert view.summary.return_proxy.value == "—"
    assert view.summary.drawdown.note == "复权价研究曲线"
    assert view.summary.cost_stress.note == "佣金与滑点同时翻倍"
    assert view.summary.folds.note == "锚定走样本外"
    assert view.chart_rows == ()
    assert view.candidates == ()
    assert view.folds == ()


def test_gate_uses_the_first_two_reasons() -> None:
    view = build_cross_section_view(_report())
    assert view.summary.gate.value == "硬阻断"
    assert view.summary.gate.note == "第一理由；第二理由"


def test_gate_passed_renders_passed() -> None:
    report = _report()
    report["promotion_gate"]["passed"] = True
    view = build_cross_section_view(report)
    assert view.summary.gate.value == "通过"


def test_gate_without_reasons_uses_the_fallback_note() -> None:
    report = _report()
    report["promotion_gate"]["reasons"] = []
    view = build_cross_section_view(report)
    assert view.summary.gate.note == "缺少可验证的晋级证据"


def test_scenario_capital_is_shown_when_present() -> None:
    view = build_cross_section_view(_report())
    assert view.summary.return_proxy.value == "+20.0%"
    assert view.summary.return_proxy.note == (
        "情景资金 $2,500 · 期末 $3,000 · 仍属探索性"
    )


def test_scenario_capital_note_omits_capital_when_absent() -> None:
    report = _report()
    del report["scope"]["initial_equity"]
    view = build_cross_section_view(report)
    assert view.summary.return_proxy.note == "期末 $3,000 · 仍属探索性"


def test_drawdown_and_cost_stress_formatting() -> None:
    view = build_cross_section_view(_report())
    assert view.summary.drawdown.value == "10.0%"
    assert view.summary.drawdown.note == "最差日 -3.0%"
    assert view.summary.cost_stress.value == "+12.0%"
    assert view.summary.cost_stress.note == "期末 $2,800"


def test_fold_count_is_the_number_of_report_folds() -> None:
    view = build_cross_section_view(_report())
    assert view.summary.folds.value == "2"
    assert view.summary.folds.note == "只计完整 126 日测试折"


def test_chart_rows_are_typed_immutable_facts() -> None:
    view = build_cross_section_view(_report())
    assert view.chart_rows[1].date == "2024-01-03"
    assert view.chart_rows[1].strategy_equity == 2525.0
    assert view.chart_rows[1].cost_2x_equity == 2510.0


def test_candidate_rows_match_the_legacy_display_columns() -> None:
    view = build_cross_section_view(_report())
    row = view.candidates[0]
    assert row.selected == "momentum_63_weekly_top3"
    assert row.oos_return == "+10.0%"
    assert row.oos_drawdown == "5.0%"
    assert row.training_sharpe == "1.25"
    assert row.trade_count == "12"
    assert row.cost_2x_return == "+8.0%"


def test_fold_rows_match_the_legacy_display_columns() -> None:
    view = build_cross_section_view(_report())
    row = view.folds[0]
    assert row.fold == "1"
    assert row.test_interval == "2024-01-02 → 2024-06-28"
    assert row.selected == "momentum_63_weekly_top3"
    assert row.oos_return == "+10.0%"
    assert row.cost_2x_return == "+8.0%"
    assert row.max_risk_exposure == "60.0%"
    assert row.average_cash == "20.0%"


def test_projection_does_not_mutate_the_source_report() -> None:
    report = _report()
    before = deepcopy(report)
    build_cross_section_view(report)
    assert report == before