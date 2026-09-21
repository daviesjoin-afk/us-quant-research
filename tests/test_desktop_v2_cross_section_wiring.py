"""Real-MainWindow wiring tests for CrossSectionResearchPage."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

import us_quant.desktop as desktop_module
from us_quant.desktop import MainWindow
from us_quant.desktop_v2.pages.research.cross_section.models import (
    CrossSectionResearchDraft,
)
from us_quant.paths import STATE_ROOT_ENV


_APP = QApplication.instance() or QApplication([])


@pytest.fixture()
def window(monkeypatch, tmp_path):
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    widget = MainWindow()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


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



def test_capital_change_updates_window_scalar_and_account_card(
    window: MainWindow,
) -> None:
    window.cross_section_page.capital_changed.emit(2500)
    assert window._research_capital_value == 2500
    assert window._research_scenario_capital() == Decimal(2500)
    card = window.account_page.research_capital_card
    assert card.value_label.text() == "$2,500"
    assert "历史研究情景" in card.note_label.text()


def test_programmatic_setter_is_silent_but_changes_the_widget(
    window: MainWindow,
) -> None:
    seen: list[int] = []
    window.cross_section_page.capital_changed.connect(seen.append)
    window.cross_section_page.set_research_capital(3000)
    assert window.cross_section_page.controls.capital_spin.value() == 3000
    assert seen == []


def test_run_button_delivers_the_current_immutable_draft(
    window: MainWindow, monkeypatch
) -> None:
    captured: list[CrossSectionResearchDraft] = []
    monkeypatch.setattr(window, "_run_cross_section_research", captured.append)
    window.cross_section_page.run_requested.disconnect()
    window._connect_cross_section_page()
    window.cross_section_page.set_research_capital(2750)
    window.cross_section_page.controls.run_button.click()
    assert captured == [CrossSectionResearchDraft(2750)]


def test_missing_universe_is_rejected_before_task(
    window: MainWindow, monkeypatch
) -> None:
    window.universe = None
    started: list[object] = []
    shown: list[tuple] = []
    monkeypatch.setattr(
        window, "_start_task", lambda *args, **kwargs: started.append(args)
    )
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args: shown.append(args)
    )
    window._run_cross_section_research(
        CrossSectionResearchDraft(research_capital=2500)
    )
    assert started == []
    assert shown[0][1] == "缺少标的池"


def test_research_config_receives_the_draft_capital(
    window: MainWindow, monkeypatch
) -> None:
    window.universe = SimpleNamespace()
    captured: dict[str, object] = {}
    configs: list[object] = []
    saved: list[Path] = []

    def fake_start(task, **kwargs):
        captured["task"] = task
        captured["kwargs"] = kwargs
        return True

    def fake_run(config, universe, **kwargs):
        configs.append(config)
        return {"status": "research_exploratory"}

    monkeypatch.setattr(window, "_start_task", fake_start)
    monkeypatch.setattr(
        desktop_module, "run_executable_cross_sectional_research", fake_run
    )
    monkeypatch.setattr(
        desktop_module,
        "save_executable_research",
        lambda result, path: saved.append(path),
    )

    window._run_cross_section_research(
        CrossSectionResearchDraft(research_capital=2500)
    )
    result = captured["task"](lambda message: None)

    assert window._research_capital_value == 2500
    assert configs[0].initial_equity == Decimal(2500)
    assert saved == [Path(window.cross_section_path)]
    assert result == {"status": "research_exploratory"}
    assert captured["kwargs"]["resource_group"] == "strategy"


def test_success_stores_report_publishes_and_refreshes_artifacts(
    window: MainWindow, monkeypatch
) -> None:
    published: list[None] = []
    populated: list[None] = []
    monkeypatch.setattr(
        window, "_publish_cross_section_view", lambda: published.append(None)
    )
    monkeypatch.setattr(
        window, "_populate_artifact_table", lambda: populated.append(None)
    )
    monkeypatch.setattr(
        desktop_module, "load_artifact_catalog", lambda root: "CATALOG"
    )
    report = _report()
    window._cross_section_finished(report)
    assert window.cross_section_report is report
    assert published == [None]
    assert populated == [None]
    assert window.artifact_catalog == "CATALOG"


def test_saved_report_load_publishes_the_page(
    window: MainWindow,
) -> None:
    report = _report()
    window.cross_section_path.write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )
    window._load_cross_section_report()
    assert window.cross_section_report == report
    assert window.cross_section_page.return_card.value_label.text() == "+20.0%"
    assert window.cross_section_page.candidate_table.rowCount() == 1


def test_load_failure_clears_report_logs_and_publishes_empty(
    window: MainWindow, monkeypatch
) -> None:
    window.cross_section_report = {"old": True}
    window.cross_section_path.write_text("{bad-json", encoding="utf-8")
    logged: list[str] = []
    monkeypatch.setattr(window, "_log", logged.append)
    window._load_cross_section_report()
    assert window.cross_section_report is None
    assert window.cross_section_page.return_card.value_label.text() == "—"
    assert logged and "风险一致研究产物读取失败" in logged[0]


def test_valid_json_but_malformed_schema_falls_back_to_empty(
    window: MainWindow, monkeypatch
) -> None:
    window.cross_section_report = {"old": True}
    window.cross_section_path.write_text(
        json.dumps({"status": "research_exploratory"}), encoding="utf-8"
    )
    logged: list[str] = []
    monkeypatch.setattr(window, "_log", logged.append)
    window._load_cross_section_report()
    assert window.cross_section_report is None
    assert window.cross_section_page.return_card.value_label.text() == "—"
    assert window.cross_section_page.candidate_table.rowCount() == 0
    assert logged and "风险一致研究产物读取失败" in logged[0]

def test_scanner_reads_the_updated_central_capital(
    window: MainWindow, monkeypatch
) -> None:
    window.cross_section_page.capital_changed.emit(2500)
    window.universe = SimpleNamespace()
    seen: dict[str, object] = {}

    def fake_start(task, **kwargs):
        seen["result"] = task(lambda message: None)
        return True

    def fake_scan(universe, *, capital, **kwargs):
        seen["capital"] = capital
        return "SCAN"

    monkeypatch.setattr(window, "_start_task", fake_start)
    monkeypatch.setattr(window.market_scan_service, "scan", fake_scan)
    window._run_scan()
    assert seen["capital"] == Decimal(2500)
    assert seen["result"] == "SCAN"


def test_theme_switch_does_not_emit_cross_section_intent(
    window: MainWindow,
) -> None:
    seen: list[object] = []
    window.cross_section_page.capital_changed.connect(seen.append)
    window.cross_section_page.run_requested.connect(seen.append)
    window._apply_theme("light")
    assert seen == []