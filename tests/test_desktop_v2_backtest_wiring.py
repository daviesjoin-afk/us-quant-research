"""Real-MainWindow wiring tests for the native BacktestPage."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from us_quant.backtest_workspace import STRATEGY_SPECS
from us_quant.desktop import MainWindow
from us_quant.desktop_v2.pages.research.backtest.page import BacktestPage
from us_quant.desktop_workers import TaskThread
from us_quant.paths import STATE_ROOT_ENV
from us_quant.trading.application.strategy_selection import (
    StrategySelectionPurpose,
)
from us_quant.trading.domain.strategy import StrategyStatus


_APP = QApplication.instance() or QApplication([])


@pytest.fixture()
def window(monkeypatch, tmp_path):
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    widget = MainWindow()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


def test_research_tab_index_four_is_backtest_page(window: MainWindow) -> None:
    assert isinstance(window.v2_research_tabs.widget(4), BacktestPage)
    assert window.v2_research_tabs.widget(4) is window.backtest_page


def test_run_intents_reach_window_handlers(
    window: MainWindow, monkeypatch
) -> None:
    selected: list = []
    compared: list = []
    monkeypatch.setattr(window, "_run_selected_backtest", selected.append)
    monkeypatch.setattr(window, "_run_all_backtests", compared.append)
    window.backtest_page.run_selected_requested.disconnect()
    window.backtest_page.compare_all_requested.disconnect()
    window._connect_backtest_page()
    window.backtest_page.controls.run_selected_button.click()
    window.backtest_page.controls.compare_all_button.click()
    assert selected[0].strategy_version_id
    assert compared[0].strategy_version_id


def test_strategy_options_come_from_backtest_selection_purpose(
    window: MainWindow,
) -> None:
    offered = {
        window.backtest_page.controls.strategy_combo.itemData(index)
        for index in range(window.backtest_page.controls.strategy_combo.count())
    }
    expected = {
        version.version_id
        for version in window.strategy_selection.options(
            StrategySelectionPurpose.BACKTEST
        )
    }
    assert offered == expected
    assert offered


def test_single_run_uses_the_requested_version_id(window: MainWindow) -> None:
    chosen = window.backtest_page.controls.strategy_combo.currentData()
    records = window._backtest_records(False, str(chosen))
    assert len(records) == 1
    assert records[0].version_id == chosen
    assert window._backtest_records(False, "missing") == []


def test_compare_all_uses_latest_per_strategy(window: MainWindow) -> None:
    records = window._backtest_records(True, "ignored")
    assert records
    assert len({record.strategy_id for record in records}) == len(records)
    assert all(
        record.status is StrategyStatus.RESEARCH for record in records
    )
    assert [record.strategy_id for record in records] == [
        spec.strategy_id
        for spec in STRATEGY_SPECS
        if spec.strategy_id in {row.strategy_id for row in records}
    ]


def test_invalid_date_is_rejected_before_service(
    window: MainWindow, monkeypatch
) -> None:
    draft = replace(
        window.backtest_page.controls.draft(),
        start_date=date(2030, 1, 1),
        end_date=date(2029, 1, 1),
    )
    started: list = []
    monkeypatch.setattr(
        window, "_start_task", lambda *args, **kwargs: started.append(args) or True
    )
    shown: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: shown.append(args))
    window._run_backtest_workspace(False, draft)
    assert started == []
    assert shown[0][1] == "日期无效"


def test_no_eligible_strategy_is_rejected_before_service(
    window: MainWindow, monkeypatch
) -> None:
    draft = replace(
        window.backtest_page.controls.draft(),
        strategy_version_id="missing",
    )
    started: list = []
    monkeypatch.setattr(
        window, "_start_task", lambda *args, **kwargs: started.append(args) or True
    )
    shown: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: shown.append(args))
    window._run_backtest_workspace(False, draft)
    assert started == []
    assert shown[0][1] == "没有可运行版本"


def test_success_stores_runs_selects_first_and_publishes(
    window: MainWindow, monkeypatch
) -> None:
    published: list[None] = []
    monkeypatch.setattr(window, "_publish_backtest_view", lambda: published.append(None))
    runs = (SimpleNamespace(run_id="run-A"), SimpleNamespace(run_id="run-B"))
    window._backtest_workspace_finished(runs)
    assert window._backtest_busy is False
    assert window.backtest_runs == list(runs)
    assert window._selected_backtest_run_id == "run-A"
    assert len(published) == 1


def test_run_selection_updates_detail_by_full_run_id(
    window: MainWindow, monkeypatch
) -> None:
    published: list[None] = []
    monkeypatch.setattr(window, "_publish_backtest_view", lambda: published.append(None))
    window._backtest_run_selected("run-B-full-id")
    assert window._selected_backtest_run_id == "run-B-full-id"
    assert len(published) == 1


def test_failure_clears_busy(window: MainWindow, monkeypatch) -> None:
    window._backtest_busy = True
    published: list[None] = []
    monkeypatch.setattr(window, "_publish_backtest_view", lambda: published.append(None))
    window._backtest_task_failed("boom")
    assert window._backtest_busy is False
    assert len(published) == 1


def test_start_task_refusal_clears_busy(
    window: MainWindow, monkeypatch
) -> None:
    draft = window.backtest_page.controls.draft()
    monkeypatch.setattr(window, "_start_task", lambda *args, **kwargs: False)
    seen: list[bool] = []
    monkeypatch.setattr(
        window,
        "_publish_backtest_view",
        lambda: seen.append(window._backtest_busy),
    )
    window._run_backtest_workspace(False, draft)
    assert window._backtest_busy is False
    assert seen == [True, False]


def test_unrelated_worker_finish_does_not_unlock_active_backtest(
    window: MainWindow,
) -> None:
    window._backtest_busy = True
    window._publish_backtest_view()
    assert window.backtest_page.controls.run_selected_button.isEnabled() is False
    worker = TaskThread(lambda: None, resource_group="history")
    window.task_controller.register(worker)
    window._worker_finished(worker)
    assert window._backtest_busy is True
    assert window.backtest_page.controls.run_selected_button.isEnabled() is False
