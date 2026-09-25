"""Real-MainWindow wiring and single-truth regressions for the backtest route.

The capability's own tests prove it decides correctly; these prove the *window*
is still listening and that there is exactly one backtest truth.  Three failure
modes an extraction like this can hide are invisible to a test that only drives
the orchestrator:

* a page button that no longer reaches anything (a signal nobody connected);
* a consumer that still reads its own copy, so the capability is the owner in
  name only;
* the strategy-catalogue refresh half-wired, so the combo stops following the
  catalogue while everything else keeps working.

The busy section is the one this stage most needs to protect: the flag is the
capability's own now, so an unrelated worker finishing must not be able to
release it.  Before the extraction ``_worker_finished`` read the worker list,
which is exactly how that bug was possible.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.backtest import BacktestResult
from us_quant.backtest_workspace import (
    BacktestMetrics,
    BacktestRequest,
    BacktestRun,
    StrategySpec,
)
from us_quant.desktop import MainWindow
from us_quant.desktop_workers import TaskThread
from us_quant.paths import STATE_ROOT_ENV
from us_quant.trading.application.strategy_selection import (
    StrategySelectionPurpose,
)


_APP = QApplication.instance() or QApplication([])
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def window(monkeypatch, tmp_path):
    """A real window with its dialogs silenced.

    A modal ``QMessageBox`` blocks forever under ``QT_QPA_PLATFORM=offscreen``,
    and every refusal path shows one, so silencing belongs to the fixture rather
    than to individual tests.
    """

    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.information",
        staticmethod(lambda *args, **kwargs: None),
    )
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.warning",
        staticmethod(lambda *args, **kwargs: None),
    )
    widget = MainWindow()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


def _draft(window):
    return window.backtest_page.controls.draft()


def _runs() -> tuple[BacktestRun, ...]:
    """Two real runs, so the presenter gets the types it declares."""

    request = BacktestRequest(
        strategy_id="buy-hold",
        strategy_version_id="buy-hold-1.0.0",
        parameter_hash="ph",
        code_hash="ch",
        parameters={},
        symbol="XLF",
        start_date=date(2018, 1, 1),
        end_date=date(2024, 1, 1),
        initial_equity=Decimal("1500"),
        target_weight=Decimal("1"),
        per_share_commission=Decimal("0.005"),
        minimum_commission=Decimal("1.0"),
        slippage_bps=Decimal("2.5"),
    )
    result = BacktestResult(
        initial_equity=Decimal("1500"),
        final_equity=Decimal("1600"),
        total_return=Decimal("0.0667"),
        max_drawdown=Decimal("-0.10"),
        total_commission=Decimal("3.00"),
        trades=(),
        equity_curve=(),
    )
    metrics = BacktestMetrics(
        annualized_return=Decimal("0.01"),
        annualized_sharpe=Decimal("0.5"),
        annualized_sortino=Decimal("0.6"),
        calmar_ratio=Decimal("0.7"),
        annualized_volatility=Decimal("0.1"),
        turnover=Decimal("1.0"),
        worst_day=Decimal("-0.02"),
        positive_day_ratio=Decimal("0.55"),
    )
    spec = StrategySpec(
        strategy_id="buy-hold",
        name="买入并持有基准",
        description="",
        default_parameters={},
    )
    return tuple(
        BacktestRun(
            run_id=run_id,
            request=request,
            strategy=spec,
            data_source="local",
            data_hash="dh",
            price_basis="adjusted",
            first_date=date(2018, 1, 2),
            last_date=date(2023, 12, 29),
            result=result,
            metrics=metrics,
        )
        for run_id in ("run-A", "run-B")
    )


# -- the page intents reach the capability -----------------------------


def test_the_run_buttons_reach_the_capability(window, monkeypatch) -> None:
    """Clicks, not direct method calls: the signal wiring is the subject."""

    calls: list[str] = []
    monkeypatch.setattr(
        window.backtest_orchestrator,
        "request_selected",
        lambda draft: calls.append("selected"),
    )
    monkeypatch.setattr(
        window.backtest_orchestrator,
        "request_compare_all",
        lambda draft: calls.append("compare_all"),
    )

    window.backtest_page.controls.run_selected_button.click()
    window.backtest_page.controls.compare_all_button.click()
    _APP.processEvents()

    assert calls == ["selected", "compare_all"]


def test_a_row_selection_reaches_the_capability(window, monkeypatch) -> None:
    """The table's selection is intent, and it goes to the capability."""

    calls: list[str] = []
    monkeypatch.setattr(
        window.backtest_orchestrator,
        "select_run",
        lambda run_id: calls.append(run_id),
    )

    window.backtest_page.run_selected.emit("run-B-full-id")
    _APP.processEvents()

    assert calls == ["run-B-full-id"]


def test_the_capability_signals_reach_the_window(window, monkeypatch) -> None:
    """Log and refusal are both routed; neither is a dead signal."""

    logs: list[str] = []
    refusals: list[tuple[str, str]] = []
    monkeypatch.setattr(window, "_log", logs.append)
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.warning",
        staticmethod(
            lambda parent, title, message: refusals.append((title, message))
        ),
    )

    window.backtest_orchestrator.log_requested.emit("hello")
    window.backtest_orchestrator.refused.emit("warning", "t", "m")

    assert logs == ["hello"]
    assert refusals == [("t", "m")]


def test_the_refusal_severity_is_the_one_the_capability_chose(
    window, monkeypatch
) -> None:
    """``information`` for busy, ``warning`` for the validation refusals.

    Driven through the real wiring, so the assertion covers the signal, the
    handler and the dialog the operator actually sees.
    """

    shown: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.information",
        staticmethod(
            lambda parent, title, message: shown.append(
                ("information", title, message)
            )
        ),
    )
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.warning",
        staticmethod(
            lambda parent, title, message: shown.append(
                ("warning", title, message)
            )
        ),
    )

    window.backtest_orchestrator.refused.emit(
        "information", "任务忙", "请等待当前数据或研究任务完成后再运行回测。"
    )
    window.backtest_orchestrator.refused.emit(
        "warning", "日期无效", "起始日期不能晚于结束日期。"
    )

    assert shown == [
        (
            "information",
            "任务忙",
            "请等待当前数据或研究任务完成后再运行回测。",
        ),
        ("warning", "日期无效", "起始日期不能晚于结束日期。"),
    ]


def test_the_console_reports_the_busy_refusal_before_a_worker_starts(
    window, monkeypatch
) -> None:
    """A busy resource group stops at the dialog, not at a worker."""

    started: list = []
    monkeypatch.setattr(
        window, "_start_task", lambda *a, **k: started.append(a) or True
    )
    ran: list = []
    monkeypatch.setattr(
        window.backtest_service, "run", lambda *a, **k: ran.append(a) or ()
    )
    monkeypatch.setattr(
        window.backtest_orchestrator, "_task_available", lambda group: False
    )
    shown: list[tuple] = []
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.information",
        staticmethod(
            lambda parent, title, message: shown.append((title, message))
        ),
    )

    window.backtest_orchestrator.request_selected(_draft(window))

    assert shown == [("任务忙", "请等待当前数据或研究任务完成后再运行回测。")]
    assert started == []
    assert ran == []


def test_an_inverted_date_range_stops_before_a_worker(window, monkeypatch) -> None:
    started: list = []
    monkeypatch.setattr(
        window, "_start_task", lambda *a, **k: started.append(a) or True
    )
    ran: list = []
    monkeypatch.setattr(
        window.backtest_service, "run", lambda *a, **k: ran.append(a) or ()
    )
    shown: list[tuple] = []
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.warning",
        staticmethod(
            lambda parent, title, message: shown.append((title, message))
        ),
    )

    window.backtest_orchestrator.request_selected(
        replace(
            _draft(window),
            start_date=date(2030, 1, 1),
            end_date=date(2029, 1, 1),
        )
    )

    assert shown == [("日期无效", "起始日期不能晚于结束日期。")]
    assert started == []
    assert ran == []


# -- one backtest truth ------------------------------------------------


def test_a_successful_batch_becomes_the_capabilitys_runs(window) -> None:
    """The runs the page shows are the capability's own tuple, by identity."""

    runs = _runs()
    window.backtest_orchestrator._runs_finished(runs)

    assert window.backtest_orchestrator._runs == runs
    assert window.backtest_orchestrator._selected_run_id == "run-A"


def test_the_window_holds_no_backtest_runs_of_its_own(window) -> None:
    """The window must not mirror the truth it delegated."""

    assert not hasattr(window, "backtest_runs")
    assert not hasattr(window, "_selected_backtest_run_id")
    assert not hasattr(window, "_backtest_busy")


def test_the_page_is_not_the_truth(window) -> None:
    """A selection change moves the capability's state, not just the widget."""

    runs = _runs()
    window.backtest_orchestrator._runs = runs
    window.backtest_orchestrator._selected_run_id = "run-A"

    window.backtest_page.run_selected.emit("run-B")
    _APP.processEvents()

    assert window.backtest_orchestrator._selected_run_id == "run-B"
    assert window.backtest_orchestrator._runs == runs


def test_a_failed_batch_keeps_the_last_good_runs(window) -> None:
    """Failure is not evidence that the previous batch was wrong."""

    runs = _runs()
    window.backtest_orchestrator._runs = runs
    window.backtest_orchestrator._selected_run_id = "run-A"

    window.backtest_orchestrator._runs_failed("boom")

    assert window.backtest_orchestrator._runs == runs
    assert window.backtest_orchestrator._selected_run_id == "run-A"
    assert window.backtest_orchestrator._busy is False


def test_the_page_shows_the_runs_the_capability_holds(window) -> None:
    """The render path is real: the table is rebuilt from the truth."""

    window.backtest_orchestrator._runs_finished(_runs())
    _APP.processEvents()

    assert window.backtest_page.comparison_table.rowCount() == 2


# -- busy belongs to the capability ------------------------------------


def test_an_active_backtest_disables_the_page_controls(window) -> None:
    window.backtest_orchestrator._busy = True
    window.backtest_orchestrator.render_current()

    assert (
        window.backtest_page.controls.run_selected_button.isEnabled() is False
    )
    assert (
        window.backtest_page.controls.compare_all_button.isEnabled() is False
    )


def test_an_unrelated_worker_finish_does_not_unlock_an_active_backtest(
    window,
) -> None:
    """The regression this extraction exists to make impossible.

    The retired handler derived busy from the worker list, so a history worker
    finishing could release the backtest controls.  The flag is the
    capability's own now, and ``_worker_finished`` cannot reach it.
    """

    window.backtest_orchestrator._busy = True
    window.backtest_orchestrator.render_current()
    assert (
        window.backtest_page.controls.run_selected_button.isEnabled() is False
    )

    worker = TaskThread(lambda: None, resource_group="history")
    window.task_controller.register(worker)
    window._worker_finished(worker)

    assert window.backtest_orchestrator._busy is True
    assert (
        window.backtest_page.controls.run_selected_button.isEnabled() is False
    )


def test_a_wrong_result_restores_the_real_page_controls(window) -> None:
    """The wrong-result path, driven against the real page.

    The capability's own test asserts the view objects; this one asserts the
    actual widgets, because "the controls come back" is a claim about the page
    the operator sees.  The state is the real one: a batch in flight and a
    previous good result already displayed.
    """

    runs = _runs()
    orchestrator = window.backtest_orchestrator
    orchestrator._runs_finished(runs)
    _APP.processEvents()
    assert window.backtest_page.comparison_table.rowCount() == 2

    orchestrator._busy = True
    orchestrator.render_current()

    with pytest.raises(TypeError):
        orchestrator._runs_finished(object())

    assert orchestrator._busy is False
    assert window.backtest_page.controls.run_selected_button.isEnabled() is True
    assert window.backtest_page.controls.compare_all_button.isEnabled() is True
    # The last good result is still on the page, not blanked.
    assert orchestrator._runs == runs
    assert window.backtest_page.comparison_table.rowCount() == 2


def test_a_rejected_admission_restores_the_controls(window, monkeypatch) -> None:
    """A refused submission rolls the optimistic busy flag back.

    The window's admission gate is closed here, which is the one state where
    the capability's UI pre-check passes but ``_start_task`` still refuses:
    there is no live backtest worker, so ``task_available`` says yes, and the
    authoritative admission then says no.  That is precisely the gap between
    the pre-check and admission that spec 17 describes, and the rollback exists
    for it.

    Driven through the real path rather than by patching ``_start_task``: the
    capability captured the bound method at construction time, so replacing the
    attribute on the window would not reach it.
    """

    # G1: the admission fact is the supervisor's; raising it is the real close
    # path's first step, and this is how a production close sets it.
    window.runtime_supervisor.begin_shutdown()

    window.backtest_orchestrator.request_selected(_draft(window))

    assert window.backtest_orchestrator._busy is False
    assert window.backtest_page.controls.run_selected_button.isEnabled() is True


# -- the strategy catalogue refresh ------------------------------------


def test_the_strategy_options_come_from_the_backtest_purpose(window) -> None:
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


def test_a_catalogue_refresh_repoints_the_backtest_combo(
    window, monkeypatch
) -> None:
    """The window delegates the option order; it does not recompute it."""

    calls: list[str] = []
    monkeypatch.setattr(
        window.backtest_orchestrator,
        "refresh_strategy_options",
        lambda: calls.append("refresh"),
    )

    window._populate_strategy_selection_combos()

    assert calls == ["refresh"]


def test_the_refresh_actually_updates_the_real_combo(
    window, monkeypatch
) -> None:
    """A wiring test, not a spy test: the widget is the subject."""

    combo = window.backtest_page.controls.strategy_combo
    combo.clear()
    assert combo.count() == 0

    window._populate_strategy_selection_combos()

    assert combo.count() > 0
    assert all(combo.itemData(index) for index in range(combo.count()))
