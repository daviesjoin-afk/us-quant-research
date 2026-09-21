"""Real-Qt tests for the native CrossSectionResearchPage."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.research.cross_section.models import (
    CrossSectionResearchDraft,
)
from us_quant.desktop_v2.pages.research.cross_section.page import (
    CrossSectionResearchPage,
)
from us_quant.desktop_v2.pages.research.cross_section.presenter import (
    build_cross_section_view,
)
from us_quant.ui_theme import theme_palette


_APP = QApplication.instance() or QApplication([])


def _report() -> dict:
    return {
        "scope": {"initial_equity": 2500.0},
        "out_of_sample": {
            "strategy": {
                "final_equity": 3000.0,
                "total_return": 0.2,
                "max_drawdown": 0.1,
                "worst_day": -0.03,
            },
            "cost_2x": {"final_equity": 2800.0, "total_return": 0.12},
            "folds": [
                {
                    "fold": 1,
                    "test_start": "2024-01-02",
                    "test_end": "2024-06-28",
                    "selected": "momentum_63_weekly_top3",
                    "training_sharpe": 1.25,
                    "oos_return": 0.1,
                    "oos_max_drawdown": 0.05,
                    "oos_trade_count": 12,
                    "average_cash_pct": 0.2,
                    "max_risk_exposure_pct": 0.6,
                    "cost_2x_return": 0.08,
                }
            ],
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
        "promotion_gate": {"passed": False, "reasons": ["第一理由"]},
    }


def _page() -> CrossSectionResearchPage:
    return CrossSectionResearchPage(research_capital=1500)


def test_page_owns_five_metric_cards() -> None:
    page = _page()
    assert len(page.metric_cards) == 5
    assert page.gate_card.value_label.text() == "硬阻断"


def test_page_mounts_controls_chart_and_tables() -> None:
    page = _page()
    assert page.controls.capital_spin.value() == 1500
    assert page.chart is not None
    assert page.candidate_table.columnCount() == 6
    assert page.fold_table.columnCount() == 7


def test_render_empty_view_restores_the_legacy_copy() -> None:
    page = _page()
    page.render(build_cross_section_view(None))
    assert page.gate_card.value_label.text() == "硬阻断"
    assert page.return_card.value_label.text() == "—"
    assert page.drawdown_card.note_label.text() == "复权价研究曲线"
    assert page.candidate_table.rowCount() == 0
    assert page.fold_table.rowCount() == 0


def test_render_report_view_fills_cards_and_tables() -> None:
    page = _page()
    page.render(build_cross_section_view(_report()))
    assert page.return_card.value_label.text() == "+20.0%"
    assert page.drawdown_card.value_label.text() == "10.0%"
    assert page.cost_stress_card.value_label.text() == "+12.0%"
    assert page.folds_card.value_label.text() == "1"
    assert page.candidate_table.rowCount() == 1
    assert page.fold_table.rowCount() == 1


def test_render_report_view_projects_chart_rows() -> None:
    page = _page()
    page.render(build_cross_section_view(_report()))
    assert page.chart.rows[1]["date"] == "2024-01-03"
    assert page.chart.rows[1]["strategy_equity"] == 2525.0
    assert page.chart.rows[1]["cost_2x_equity"] == 2510.0


def test_current_draft_reads_the_capital_widget() -> None:
    page = _page()
    page.set_research_capital(2750)
    assert page.current_draft() == CrossSectionResearchDraft(2750)


def test_set_research_capital_is_silent_by_default() -> None:
    page = _page()
    seen: list[int] = []
    page.capital_changed.connect(seen.append)
    page.set_research_capital(3000)
    assert page.controls.capital_spin.value() == 3000
    assert seen == []


def test_set_research_capital_can_emit_when_requested() -> None:
    page = _page()
    seen: list[int] = []
    page.capital_changed.connect(seen.append)
    page.set_research_capital(3000, emit_change=True)
    assert seen == [3000]


def test_set_palette_has_no_intent_side_effect() -> None:
    page = _page()
    seen: list[object] = []
    page.capital_changed.connect(seen.append)
    page.run_requested.connect(seen.append)
    page.set_palette(theme_palette("light"))
    assert seen == []