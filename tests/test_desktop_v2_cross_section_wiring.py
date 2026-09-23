"""Real-MainWindow wiring tests for the Cross Section capability.

These drive the *real* window: the page's buttons and spinbox emit, the
capability's signals connect to the window's bridges, and the assertions are
made against the canonical objects the production code uses.  Nothing here
monkeypatches a retired window handler, because there is none to patch -- that
is the property v2O-C4 establishes.

What this file cannot prove (that the capability holds no second truth, that
the service imports no Qt) belongs to
``tests/test_desktop_research_cross_section_orchestration.py``, and the
capability's own rules are driven without a window in
``tests/test_desktop_cross_section_orchestrator.py``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from us_quant.desktop import MainWindow
from us_quant.desktop_v2.pages.research.cross_section.models import (
    CrossSectionResearchDraft,
)
from us_quant.paths import STATE_ROOT_ENV
from us_quant.universe import UniverseRecord, UniverseSnapshot


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


def _universe(symbol: str = "AAPL") -> UniverseSnapshot:
    """A real snapshot: adoption renders, so the presenter reads every field."""

    return UniverseSnapshot(
        generated_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
        source_timestamps={"test": "now"},
        records=(
            UniverseRecord(
                symbol=symbol,
                name=symbol,
                exchange="NASDAQ",
                security_type="STK",
                eligible_for_research=True,
            ),
        ),
    )


# -- the canonical scalar and the cross-workflow fan-out -----------------


def test_the_window_starts_with_the_configured_research_scenario_capital(
    window: MainWindow,
) -> None:
    """One truth, three consistent projections -- and only the state is truth.

    ``config.initial_equity`` initialises the state; the Cross Section control
    and the Account card are both projections of it, so all three agree at
    startup without either page owning the number.
    """

    expected = int(window.config.initial_equity)
    assert window.research_scenario_capital.value == expected
    assert window.cross_section_page.controls.capital_spin.value() == expected
    card = window.account_page.research_capital_card
    assert card.value_label.text() == f"${expected:,.0f}"


def test_a_capital_change_moves_the_state_and_the_account_card(
    window: MainWindow,
) -> None:
    window.cross_section_page.capital_changed.emit(2500)

    assert window.research_scenario_capital.value == 2500
    assert window.research_scenario_capital.decimal_value == Decimal(2500)
    card = window.account_page.research_capital_card
    assert card.value_label.text() == "$2,500"
    assert "历史研究情景" in card.note_label.text()
    assert "不是 Paper/Live 账户余额" in card.note_label.text()


def test_the_account_card_change_does_not_touch_broker_truth(
    window: MainWindow,
) -> None:
    """v2O-C4: the scenario figure is presentation, never an account fact."""

    portfolio = window.account_orchestrator.portfolio

    window.cross_section_page.capital_changed.emit(7777)

    assert window.account_page.research_capital_card.value_label.text() == (
        "$7,777"
    )
    assert window.account_orchestrator.portfolio is portfolio


def test_the_capital_change_is_published_exactly_once(
    window: MainWindow,
) -> None:
    """A no-op edit must not repaint: the state reports whether it moved."""

    seen: list[int] = []
    window.cross_section_orchestrator.capital_changed.connect(seen.append)
    renders: list[int] = []
    original = window.account_orchestrator.render_current

    def counting_render() -> None:
        renders.append(1)
        original()

    window.account_orchestrator.render_current = counting_render

    window.cross_section_page.capital_changed.emit(2500)
    window.cross_section_page.capital_changed.emit(2500)

    assert seen == [2500]
    assert renders == [1]


def test_programmatic_capital_set_is_silent(window: MainWindow) -> None:
    seen: list[int] = []
    window.cross_section_page.capital_changed.connect(seen.append)
    window.cross_section_page.set_research_capital(3000)
    assert window.cross_section_page.controls.capital_spin.value() == 3000
    assert seen == []


# -- every consumer reads the canonical owner ----------------------------


def test_run_button_delivers_the_current_immutable_draft(
    window: MainWindow, monkeypatch
) -> None:
    captured: list[CrossSectionResearchDraft] = []
    monkeypatch.setattr(
        window.cross_section_orchestrator, "request_run", captured.append
    )
    window.cross_section_page.set_research_capital(2750)
    window.cross_section_page.controls.run_button.click()
    assert captured == [CrossSectionResearchDraft(2750)]


def test_scanner_reads_the_updated_canonical_capital(
    window: MainWindow, monkeypatch
) -> None:
    window.cross_section_page.capital_changed.emit(2500)
    window.universe_orchestrator.restore_snapshot(_universe())
    seen: dict[str, object] = {}

    def fake_start(task, **kwargs):
        seen["result"] = task(lambda message: None)
        return True

    def fake_scan(universe, *, capital, **kwargs):
        seen["capital"] = capital
        return "SCAN"

    monkeypatch.setattr(
        window.scanner_orchestrator, "_submit_task", fake_start
    )
    monkeypatch.setattr(window.market_scan_service, "scan", fake_scan)
    window.scanner_orchestrator.request_scan()
    assert seen["capital"] == Decimal(2500)
    assert seen["result"] == "SCAN"


def test_auto_quant_preparation_reads_the_updated_canonical_capital(
    window: MainWindow, monkeypatch
) -> None:
    """The AutoQuant scan request freezes the canonical capital.

    Driven through the real ``_prepare_auto_quant_candidates`` with the Paper
    workflow stubbed, then the captured task is executed against a faked
    ``scan_market``.  Asserting the value the *worker* receives is what proves
    the read happened on the request thread against the canonical state, not
    against a copy.
    """

    window.cross_section_page.capital_changed.emit(3300)
    window.universe_orchestrator.restore_snapshot(_universe())
    captured: dict[str, object] = {}

    # Only the two workflow transitions are stubbed: replacing the whole
    # controller would leave the window's own teardown gate with an object
    # that is not a workflow, and this test is about the capital, not Paper.
    monkeypatch.setattr(
        window.paper_workflow, "begin_preparing", lambda: None
    )
    monkeypatch.setattr(
        window.paper_workflow, "cancel_preparing", lambda: None
    )
    monkeypatch.setattr(window, "_set_launch_busy", lambda *a: None)

    def fake_start(task, **kwargs):
        captured["task"] = task
        return False

    monkeypatch.setattr(window, "_start_task", fake_start)
    monkeypatch.setattr(
        "us_quant.desktop.scan_market",
        lambda universe, **kwargs: captured.setdefault(
            "capital", kwargs["capital"]
        )
        or "SCAN",
    )
    monkeypatch.setattr("us_quant.desktop.save_market_scan", lambda *a: None)

    window._prepare_auto_quant_candidates()

    # The task reports the capital the scan ran with, threaded through the
    # real closure rather than a re-read of the state.
    assert window.research_scenario_capital.value == 3300
    captured["task"](lambda message: None)
    assert captured["capital"] == Decimal(3300)


def test_targeted_replay_reads_the_updated_canonical_capital(
    window: MainWindow, monkeypatch
) -> None:
    """The replay request freezes the canonical capital into ``initial_equity``.

    The request goes through the real path the button takes: the *capability*
    freezes the capital on this thread and submits a task; the task is then
    executed with a stubbed minute store and session grouping, so the assertion
    is on the value the *executor* receives.  The executor raises a sentinel
    immediately after recording, which short-circuits the save and the result
    handling -- this round is not an occasion to rebuild a real replay.
    """

    from types import SimpleNamespace

    from us_quant.trading.domain.strategy import StrategyStatus

    window.cross_section_page.capital_changed.emit(4200)
    window.universe_orchestrator.restore_snapshot(_universe())
    window.targeted_validation_page.session_panel.controls.target_symbol_input.setText(
        "AAPL"
    )
    seen: dict[str, object] = {}

    class Strategy:
        version_id = "v1"
        semver = "1.0.0"
        parameter_hash = "ph"
        parameters: dict = {}
        status = StrategyStatus.RESEARCH

    class Sentinel(Exception):
        pass

    def fake_replay(rows, **kwargs):
        seen.update(kwargs)
        raise Sentinel

    monkeypatch.setattr(
        window, "_selected_shadow_strategy_record", lambda: Strategy()
    )
    monkeypatch.setattr(
        window.minute_quote_store,
        "load",
        lambda symbol, **kwargs: (SimpleNamespace(provider="p"),),
    )
    monkeypatch.setattr(
        "us_quant.desktop_targeted_evidence_service.group_regular_sessions",
        lambda rows: (("2026-07-20", tuple(rows)),),
    )
    monkeypatch.setattr(
        "us_quant.desktop_targeted_evidence_service.run_targeted_replay",
        fake_replay,
    )
    # The capability captured ``_start_task`` when the window built it, so the
    # stub goes on the orchestrator's own dependency rather than on the window
    # attribute a late monkeypatch could no longer reach.
    monkeypatch.setattr(
        window.targeted_evidence_orchestrator,
        "_submit_task",
        lambda task, **kwargs: seen.setdefault("task", task) or False,
    )

    window.targeted_evidence_orchestrator.request_replay()

    with pytest.raises(Sentinel):
        seen["task"](lambda message: None)

    assert seen["initial_equity"] == Decimal(4200)


def test_targeted_robustness_reads_the_updated_canonical_capital(
    window: MainWindow, monkeypatch
) -> None:
    """Robustness shares the same canonical capital.  Not split, not copied."""

    from types import SimpleNamespace

    from us_quant.trading.domain.strategy import StrategyStatus

    window.cross_section_page.capital_changed.emit(5100)
    window.universe_orchestrator.restore_snapshot(_universe())
    window.targeted_validation_page.session_panel.controls.target_symbol_input.setText(
        "AAPL"
    )
    seen: dict[str, object] = {}

    class Strategy:
        version_id = "v1"
        semver = "1.0.0"
        parameter_hash = "ph"
        parameters: dict = {}
        status = StrategyStatus.RESEARCH

    class Sentinel(Exception):
        pass

    def fake_robustness(rows, **kwargs):
        seen.update(kwargs)
        raise Sentinel

    monkeypatch.setattr(
        window, "_selected_shadow_strategy_record", lambda: Strategy()
    )
    monkeypatch.setattr(
        window.minute_quote_store,
        "load",
        lambda symbol, **kwargs: (SimpleNamespace(provider="p"),),
    )
    monkeypatch.setattr(
        "us_quant.desktop_targeted_evidence_service.run_targeted_robustness",
        fake_robustness,
    )
    monkeypatch.setattr(
        window.targeted_evidence_orchestrator,
        "_submit_task",
        lambda task, **kwargs: seen.setdefault("task", task) or False,
    )

    window.targeted_evidence_orchestrator.request_robustness()

    with pytest.raises(Sentinel):
        seen["task"](lambda message: None)

    assert seen["initial_equity"] == Decimal(5100)


def test_market_watchlist_falls_back_to_the_canonical_capital(
    window: MainWindow, monkeypatch
) -> None:
    """Fresh Paper capital wins; the scenario figure is only the fallback."""

    window.cross_section_page.capital_changed.emit(2500)
    window.universe_orchestrator.restore_snapshot(_universe())
    window.scanner_orchestrator.adopt_external_scan(_fake_scan())
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        window.account_orchestrator,
        "fresh_paper_net_liquidation",
        lambda: None,
    )
    monkeypatch.setattr(
        "us_quant.desktop.select_intraday_watchlist",
        _record_capital(seen),
    )
    window._apply_intraday_watchlist()

    assert seen["capital"] == Decimal(2500)


def test_market_watchlist_prefers_fresh_paper_capital(
    window: MainWindow, monkeypatch
) -> None:
    """The policy is unchanged: research scenario dollars never become Paper."""

    window.cross_section_page.capital_changed.emit(2500)
    window.universe_orchestrator.restore_snapshot(_universe())
    window.scanner_orchestrator.adopt_external_scan(_fake_scan())
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        window.account_orchestrator,
        "fresh_paper_net_liquidation",
        lambda: Decimal("9999"),
    )
    monkeypatch.setattr(
        "us_quant.desktop.select_intraday_watchlist",
        _record_capital(seen),
    )
    window._apply_intraday_watchlist()

    assert seen["capital"] == Decimal("9999")


def _record_capital(seen: dict) -> object:
    """A ``select_intraday_watchlist`` stand-in that records the capital.

    It must return an empty symbol tuple: the caller feeds the returned
    symbols into the market subscription control, and returning the capital
    itself would make the test fail in the wrong layer.
    """

    def fake(scan, *, capital):
        seen["capital"] = capital
        return ()

    return fake

    from us_quant.scanner import MarketScan

def _fake_scan():
    from us_quant.scanner import MarketScan

    return MarketScan(
        generated_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
        capital=2500.0,
        data_date=None,
        results=(),
        skipped={},
        max_position_risk_pct=0.1,
    )


# -- refusal, success, restore -------------------------------------------


def test_missing_universe_is_refused_before_any_task(
    window: MainWindow, monkeypatch
) -> None:
    assert window.universe_orchestrator.snapshot is None
    started: list[object] = []
    shown: list[tuple] = []
    monkeypatch.setattr(
        window, "_start_task", lambda *args, **kwargs: started.append(args)
    )
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args: shown.append(args)
    )

    window.cross_section_page.run_requested.emit(
        CrossSectionResearchDraft(research_capital=2500)
    )

    assert started == []
    assert shown[0][1] == "缺少标的池"


def test_the_service_receives_the_frozen_capital_and_live_universe(
    window: MainWindow, monkeypatch
) -> None:
    from dataclasses import replace

    import us_quant.desktop_cross_section_service as service_module

    window.universe_orchestrator.restore_snapshot(_universe("AAA"))
    captured: dict[str, object] = {}
    seen: dict[str, object] = {}

    def fake_start(task, **kwargs):
        captured["task"] = task
        captured["kwargs"] = kwargs
        return True

    def fake_run(universe, *, research_capital):
        seen["universe"] = universe
        seen["capital"] = research_capital
        return {"status": "research_exploratory"}

    monkeypatch.setattr(
        window.cross_section_orchestrator, "_submit_task", fake_start
    )
    monkeypatch.setattr(window.cross_section_service, "run", fake_run)

    window.cross_section_page.run_requested.emit(
        CrossSectionResearchDraft(research_capital=2500)
    )

    # A refresh lands while the task is queued; the worker must read the live
    # snapshot, not the one captured at request time.
    live = _universe("BBB")
    window.universe_orchestrator.restore_snapshot(live)

    assert window.research_scenario_capital.value == 2500
    result = captured["task"](lambda message: None)
    assert result == {"status": "research_exploratory"}
    assert seen["universe"] is live
    assert seen["capital"] == 2500
    assert captured["kwargs"]["resource_group"] == "strategy"
    assert captured["kwargs"]["start_message"] == "组合走样本外研究开始…"
    assert service_module is not None


def test_success_stores_the_report_and_refreshes_the_artifacts(
    window: MainWindow, monkeypatch
) -> None:
    import us_quant.desktop as desktop_module

    dashboard_published: list[None] = []
    monkeypatch.setattr(
        window,
        "_publish_dashboard_view",
        lambda: dashboard_published.append(None),
    )
    monkeypatch.setattr(
        desktop_module, "load_artifact_catalog", lambda root: "CATALOG"
    )

    window.cross_section_orchestrator._report_finished(_report())

    assert window.cross_section_page.return_card.value_label.text() == "+20.0%"
    assert window.cross_section_page.candidate_table.rowCount() == 1
    assert window.artifact_catalog == "CATALOG"
    assert dashboard_published == [None]


def test_a_malformed_result_keeps_the_last_good_report(
    window: MainWindow,
) -> None:
    """Projection happens before the commit, so a bad report cannot land.

    A schema-incomplete dict is *valid* Python and passes the ``isinstance``
    check, so the failure surfaces from the presenter -- normalised into a
    ``TypeError`` at the capability's boundary.  Projecting first is what makes
    that safe: it runs against the candidate while the last good report is still
    in place, so the failure leaves the truth, the page and the artifact bridge
    untouched rather than poisoning them.
    """

    orchestrator = window.cross_section_orchestrator
    orchestrator._report_finished(_report())
    last_good = orchestrator._report
    published: list[None] = []
    orchestrator.report_changed.connect(lambda: published.append(None))

    with pytest.raises(TypeError):
        orchestrator._report_finished({"status": "research_exploratory"})

    # The page still draws the last good projection, not an empty one.
    assert orchestrator._report is last_good
    assert published == []
    assert window.cross_section_page.return_card.value_label.text() == "+20.0%"
    assert window.cross_section_page.candidate_table.rowCount() == 1


def test_numeric_string_metrics_still_refresh_the_artifact_bridge(
    window: MainWindow, monkeypatch
) -> None:
    """Regression: a projectable report must not fail after the commit.

    A report carrying numeric strings projects fine (the presenter coerces with
    ``float``), but the completion log formats the same values with a raw
    ``{:+.1%}``.  Building that message *after* the commit used to mean a
    "successful" run left the truth moved, the page repainted and the Dashboard
    catalogue reloaded, with the failure surfacing from a logger.

    Driven through the real window so the bridge is exercised: the assertion is
    that the artifact catalogue really was reloaded, i.e. the whole success path
    completed rather than half of it.
    """

    report = _report()
    report["out_of_sample"]["strategy"]["total_return"] = "0.2"
    report["out_of_sample"]["strategy"]["max_drawdown"] = "0.1"

    calls: list[Path] = []
    monkeypatch.setattr(
        "us_quant.desktop.load_artifact_catalog",
        lambda root: calls.append(root) or "RELOADED",
    )
    logged: list[str] = []
    monkeypatch.setattr(window, "_log", logged.append)

    window.cross_section_orchestrator._report_finished(report)

    assert calls == [window.paths.research_results_root]
    assert window.artifact_catalog == "RELOADED"
    assert logged and "OOS +20.0%" in logged[0]


def test_a_wrong_result_type_fails_loudly_without_committing(
    window: MainWindow,
) -> None:
    """A non-dict result is rejected before it reaches the truth."""

    orchestrator = window.cross_section_orchestrator
    orchestrator._report_finished(_report())
    last_good = orchestrator._report

    with pytest.raises(TypeError):
        orchestrator._report_finished(["not", "a", "report"])

    assert orchestrator._report is last_good
    assert window.cross_section_page.return_card.value_label.text() == "+20.0%"


def test_saved_report_is_restored_and_painted_once(
    window: MainWindow,
) -> None:
    report = _report()
    window.cross_section_service._report_path.write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )

    window.cross_section_orchestrator.restore_saved()

    assert window.cross_section_orchestrator._report == report
    assert window.cross_section_page.return_card.value_label.text() == "+20.0%"
    assert window.cross_section_page.candidate_table.rowCount() == 1


def test_restore_paints_exactly_once(
    window: MainWindow, monkeypatch
) -> None:
    """v2O-C4: the startup restore is the page's first and only paint.

    The build stage deliberately does not paint the cross-section page, so a
    later load cannot double-render -- the defect the Universe round had to
    remove.  Counting the calls rather than trusting the absence of an early
    ``render`` is what makes that a fact.
    """

    window.cross_section_service._report_path.write_text(
        json.dumps(_report(), ensure_ascii=False), encoding="utf-8"
    )
    renders: list[object] = []
    monkeypatch.setattr(
        window.cross_section_page, "render", renders.append
    )

    window.cross_section_orchestrator.restore_saved()

    assert len(renders) == 1


def test_a_missing_report_paints_empty_without_failing(
    window: MainWindow,
) -> None:
    window.cross_section_service._report_path.unlink(missing_ok=True)

    window.cross_section_orchestrator.restore_saved()

    assert window.cross_section_orchestrator._report is None
    assert window.cross_section_page.return_card.value_label.text() == "—"
    assert window.cross_section_page.candidate_table.rowCount() == 0


def test_restore_publishes_no_report_changed(
    window: MainWindow,
) -> None:
    """Re-reading a local file is not new research, so nothing fans out."""

    published: list[None] = []
    window.cross_section_orchestrator.report_changed.connect(
        lambda: published.append(None)
    )
    window.cross_section_service._report_path.write_text(
        json.dumps(_report(), ensure_ascii=False), encoding="utf-8"
    )

    window.cross_section_orchestrator.restore_saved()

    assert published == []


def test_a_malformed_report_is_logged_and_paints_empty(
    window: MainWindow, monkeypatch
) -> None:
    logged: list[str] = []
    monkeypatch.setattr(window, "_log", logged.append)
    window.cross_section_service._report_path.write_text(
        "{bad-json", encoding="utf-8"
    )

    window.cross_section_orchestrator.restore_saved()

    assert window.cross_section_orchestrator._report is None
    assert window.cross_section_page.return_card.value_label.text() == "—"
    assert logged and "风险一致研究产物读取失败" in logged[0]


def test_a_schema_incomplete_report_is_tolerated_at_startup(
    window: MainWindow, monkeypatch
) -> None:
    """Valid JSON can still be an incompatible report; startup must survive.

    The projection is inside the load guard for this reason, and it is the
    historical defect this file's earliest guard protected: a bad artifact
    threw *outside* the guard and could block the desktop from opening.
    """

    logged: list[str] = []
    monkeypatch.setattr(window, "_log", logged.append)
    window.cross_section_service._report_path.write_text(
        json.dumps({"status": "research_exploratory"}), encoding="utf-8"
    )

    window.cross_section_orchestrator.restore_saved()

    assert window.cross_section_orchestrator._report is None
    assert window.cross_section_page.return_card.value_label.text() == "—"
    assert window.cross_section_page.candidate_table.rowCount() == 0
    assert logged and "风险一致研究产物读取失败" in logged[0]


def test_a_success_reloads_the_artifact_catalogue_through_the_bridge(
    window: MainWindow, monkeypatch
) -> None:
    """Success -> ``report_changed`` -> the window reloads the catalogue.

    The capability must not import the Dashboard or the catalogue, so the
    fan-out is the window's.  Asserting a real catalogue fact -- not merely
    that a callback ran -- is what proves the bridge is wired.
    """

    calls: list[Path] = []
    monkeypatch.setattr(
        "us_quant.desktop.load_artifact_catalog",
        lambda root: calls.append(root) or "RELOADED",
    )
    monkeypatch.setattr(window, "_publish_dashboard_view", lambda: None)

    window.cross_section_orchestrator._report_finished(_report())

    assert calls == [window.paths.research_results_root]
    assert window.artifact_catalog == "RELOADED"


def test_theme_switch_does_not_emit_cross_section_intent(
    window: MainWindow,
) -> None:
    seen: list[object] = []
    window.cross_section_page.capital_changed.connect(seen.append)
    window.cross_section_page.run_requested.connect(seen.append)
    window._apply_theme("light")
    assert seen == []
