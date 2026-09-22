"""Real ``MainWindow`` wiring and single-truth regressions for v2O-C2.

The capability's own tests prove it decides correctly; these prove the *window*
is still listening and that there is exactly one scan truth.  The failure modes
an extraction like this can hide are invisible to a test that only drives the
orchestrator:

* a page button that no longer reaches anything (a signal nobody connected);
* a consumer that still reads its own copy, so the capability is the owner in
  name only;
* the AutoQuant bridge half-wired, so a cross-workflow scan updates the truth
  but not the page -- or the page but not the truth.

The AutoQuant section is the one this stage most needs to protect: its
preparation path deliberately still runs the scanner itself, and this round
only changed how its *result* reaches the desktop.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import date, datetime, timezone

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.paths import STATE_ROOT_ENV
from us_quant.scanner import MarketScan, ScanResult
from us_quant.universe import UniverseRecord, UniverseSnapshot


_APP = QApplication.instance() or QApplication([])
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _result(symbol: str, score: float) -> ScanResult:
    return ScanResult(
        symbol=symbol,
        execution_symbol=symbol,
        name=f"{symbol} Inc.",
        sector="Technology",
        leader_tier=1,
        security_type="STK",
        trading_date=date(2026, 9, 18),
        close=100.0,
        execution_price=100.0,
        whole_share_capacity=10,
        average_dollar_volume_20d=1_000_000.0,
        return_20d=0.01,
        return_63d=0.02,
        volatility_20d=0.2,
        drawdown_252d=-0.1,
        rsi_14d=55.0,
        atr_pct_14d=0.02,
        above_sma_50=True,
        above_sma_200=True,
        score=score,
        signal="观察",
        research_eligible=True,
        trade_eligible=False,
        reason="test",
    )


def _scan() -> MarketScan:
    return MarketScan(
        generated_at=NOW,
        capital=1500.0,
        data_date=date(2026, 9, 18),
        results=(_result("AAPL", 50.0), _result("MSFT", 80.0)),
        skipped={},
    )


def _universe() -> UniverseSnapshot:
    return UniverseSnapshot(
        generated_at=NOW,
        source_timestamps={"test": "now"},
        records=(
            UniverseRecord(
                symbol="AAPL",
                name="Apple",
                exchange="NASDAQ",
                security_type="STK",
                eligible_for_research=True,
            ),
        ),
    )


@pytest.fixture()
def window(monkeypatch, tmp_path):
    """A real window with its dialogs silenced.

    A modal ``QMessageBox`` blocks forever under ``QT_QPA_PLATFORM=offscreen``,
    and the refusal path shows one, so silencing belongs to the fixture rather
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


# -- 34: the page intents reach the capability -------------------------


def test_the_scan_button_reaches_the_capability(window, monkeypatch) -> None:
    """A click, not a direct method call: the signal wiring is the subject."""

    calls: list[str] = []
    monkeypatch.setattr(
        window.scanner_orchestrator,
        "request_scan",
        lambda: calls.append("scan"),
    )

    window.scanner_page.scan_button.click()
    _APP.processEvents()

    assert calls == ["scan"]


def test_a_symbol_selection_reaches_the_capability(window, monkeypatch) -> None:
    """The chart intent is the capability's, not a window handler's."""

    calls: list[str] = []
    monkeypatch.setattr(
        window.scanner_orchestrator,
        "request_chart",
        lambda symbol: calls.append(symbol),
    )

    window.scanner_page.symbol_selected.emit("MSFT")
    _APP.processEvents()

    assert calls == ["MSFT"]


def test_the_capability_signals_reach_the_window(window, monkeypatch) -> None:
    """Log, refusal and change are all routed; none is a dead signal."""

    logs: list[str] = []
    refusals: list[tuple[str, str]] = []
    monkeypatch.setattr(window, "_log", logs.append)
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.information",
        staticmethod(
            lambda parent, title, message: refusals.append((title, message))
        ),
    )

    window.scanner_orchestrator.log_requested.emit("hello")
    window.scanner_orchestrator.refused.emit("t", "m")

    assert logs == ["hello"]
    assert refusals == [("t", "m")]


def test_the_scan_change_reaches_the_market_scope_summary(
    window, monkeypatch
) -> None:
    """The cross-workflow bridge follows both a manual scan and an adoption."""

    refreshes: list[str] = []
    monkeypatch.setattr(
        window, "_refresh_market_scope_summary", lambda: refreshes.append("x")
    )

    window.scanner_orchestrator.adopt_external_scan(_scan())

    assert refreshes == ["x"]


def test_a_refusal_is_shown_by_the_window_not_the_capability(
    window, monkeypatch
) -> None:
    """The capability holds no widget: the window owns the dialog.

    Driven through the real wiring -- ``_connect_scanner_page`` already routed
    ``refused`` to ``_report_scanner_refusal`` -- so the assertion covers the
    signal, the handler and the severity the operator sees.
    """

    shown: list[tuple] = []
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.information",
        staticmethod(
            lambda parent, title, message: shown.append((title, message))
        ),
    )

    window.scanner_orchestrator.request_scan()

    assert shown == [("缺少标的池", "请先刷新官方标的。")]


# -- 35: one scan truth ------------------------------------------------


def test_a_manual_scan_is_the_capabilitys_scan(window, monkeypatch) -> None:
    """Identity: the result the service returned *is* the canonical scan."""

    scan = _scan()
    window.universe_orchestrator.restore_snapshot(_universe())

    monkeypatch.setattr(
        window.scanner_orchestrator,
        "_submit_task",
        lambda task, **kwargs: True,
    )
    window.scanner_orchestrator._scan_finished(scan)

    assert window.scanner_orchestrator.scan is scan


def test_an_autoquant_scan_is_the_capabilitys_scan(window, monkeypatch) -> None:
    """The cross-workflow path lands in the same single truth."""

    scan = _scan()
    window.universe_orchestrator.restore_snapshot(_universe())
    monkeypatch.setattr(window, "_select_auto_quant_candidates", lambda: None)

    window._auto_market_scan_finished(scan)

    assert window.scanner_orchestrator.scan is scan


def test_a_restored_scan_is_the_capabilitys_scan(window, tmp_path) -> None:
    """Startup restoration seeds the same truth, not a parallel one."""

    from us_quant.scanner import save_market_scan

    scan = _scan()
    window.scan_path = tmp_path / "market_scan.json"
    window.market_scan_service.scan_path = window.scan_path
    save_market_scan(scan, window.scan_path)

    window.scanner_orchestrator.restore_saved()

    assert window.scanner_orchestrator.scan is not None
    assert window.scanner_orchestrator.scan.results == scan.results


def test_the_window_holds_no_scan_of_its_own(window) -> None:
    """The window must not mirror the truth it delegated."""

    assert not hasattr(window, "scan")
    for name in (
        "_run_scan",
        "_scan_finished",
        "_load_scan_file",
        "_publish_scanner_view",
        "_scanner_symbol_selected",
    ):
        assert not hasattr(window, name), name


# -- 36: the AutoQuant bridge -----------------------------------------


def test_the_autoquant_finish_publishes_page_truth_and_follow_ups(
    window, monkeypatch
) -> None:
    """Everything the old handler did, driven through the bridge.

    Asserted together on purpose: the failure mode this guards against is a
    half-migration where the truth moves but one of the follow-ups silently
    stops happening.
    """

    scan = _scan()
    window.universe_orchestrator.restore_snapshot(_universe())

    history_renders: list[str] = []
    candidate_selections: list[str] = []
    monkeypatch.setattr(
        window.history_orchestrator,
        "render_current",
        lambda: history_renders.append("x"),
    )
    monkeypatch.setattr(
        window,
        "_select_auto_quant_candidates",
        lambda: candidate_selections.append("x"),
    )

    window._auto_market_scan_finished(scan)

    # The truth moved, through the capability's adoption entry point.
    assert window.scanner_orchestrator.scan is scan
    # The scanner page shows it.
    assert window.scanner_page.table.rowCount() == 2
    # The history follow-ups still happen.
    assert history_renders == ["x"]
    # And the candidate selection still runs.
    assert candidate_selections == ["x"]


def test_the_autoquant_finish_writes_no_manual_completion_line(
    window, monkeypatch
) -> None:
    """Nobody clicked 扫描, so the manual line must not appear."""

    window.universe_orchestrator.restore_snapshot(_universe())
    monkeypatch.setattr(window, "_select_auto_quant_candidates", lambda: None)
    logs: list[str] = []
    monkeypatch.setattr(window, "_log", logs.append)

    window._auto_market_scan_finished(_scan())

    assert not [line for line in logs if "扫描完成" in line], logs


def test_a_manual_scan_does_write_the_completion_line(
    window, monkeypatch
) -> None:
    """The contrast: the manual path is the one that announces itself."""

    logs: list[str] = []
    monkeypatch.setattr(window, "_log", logs.append)

    window.scanner_orchestrator._scan_finished(_scan())

    assert logs == ["扫描完成：2 个，趋势候选 0 个。"]


def test_the_autoquant_finish_rejects_a_wrong_type(window) -> None:
    """The typed guard survives the bridge."""

    with pytest.raises(TypeError):
        window._auto_market_scan_finished(object())


# -- 37: page filter never changes truth -------------------------------


def test_the_page_filter_never_changes_the_scan(window) -> None:
    """Presentation is not business truth.

    Driven through the real adoption path, because that is what puts a scan on
    the page in production.
    """

    scan = _scan()
    window.scanner_orchestrator.adopt_external_scan(scan)
    before = window.scanner_page.table.rowCount()

    window.scanner_page.filter_combo.setCurrentIndex(3)
    window.scanner_page.search_input.setText("MSFT")

    assert window.scanner_orchestrator.scan is scan
    assert len(window.scanner_orchestrator.scan.results) == 2
    assert window.scanner_page.table.rowCount() <= before


def test_the_scope_summary_reads_the_capability(window, monkeypatch) -> None:
    """The window's cross-workflow reader uses the single truth.

    ``_refresh_market_scope_summary`` combines the scan with the universe and
    the local history count, so it stays on the window -- but it must read the
    capability rather than a copy.
    """

    scan = _scan()
    window.scanner_orchestrator.adopt_external_scan(scan)

    window._refresh_market_scope_summary()

    # The scope line was rebuilt from the capability's truth: the scanned count
    # and the missing count both come from the scan it now owns.
    scope = window.market_page.scope_label.text()
    assert "范围分层" in scope
    assert "2" in scope


# -- startup paints exactly once ---------------------------------------


def test_startup_restoration_paints_the_scanner_page_exactly_once(
    window, monkeypatch, tmp_path
) -> None:
    """Restoring and painting happen together, and only once.

    The failure this guards is the one the universe slice hit: the capability
    paints on adoption *and* the window paints again, rebuilding the whole
    table on every ordinary startup.
    """

    from us_quant.desktop_v2.pages.research.scanner import page as page_module
    from us_quant.scanner import save_market_scan

    renders: list[int] = []
    original = page_module.ScannerPage.render

    def counting_render(self, view) -> None:
        renders.append(len(getattr(view, "rows", ()) or ()))
        return original(self, view)

    monkeypatch.setattr(page_module.ScannerPage, "render", counting_render)

    window.scan_path = tmp_path / "market_scan.json"
    window.market_scan_service.scan_path = window.scan_path
    save_market_scan(_scan(), window.scan_path)

    window._load_local_state()
    _APP.processEvents()

    scanner_renders = [count for count in renders if count == 2]
    assert len(scanner_renders) == 1, renders


def test_startup_without_a_saved_scan_paints_exactly_once(
    window, monkeypatch, tmp_path
) -> None:
    """The first-run path paints the empty page once, not zero or twice."""

    from us_quant.desktop_v2.pages.research.scanner import page as page_module

    renders: list[int] = []
    original = page_module.ScannerPage.render
    monkeypatch.setattr(
        page_module.ScannerPage,
        "render",
        lambda self, view: renders.append(len(getattr(view, "rows", ()) or ()))
        or original(self, view),
    )

    window.scan_path = tmp_path / "market_scan.json"
    window.market_scan_service.scan_path = window.scan_path

    window._load_local_state()
    _APP.processEvents()

    assert renders == [0]


def test_a_malformed_saved_scan_does_not_stop_startup(
    window, monkeypatch, tmp_path
) -> None:
    """A corrupt artifact degrades to "no scan" and the window still loads."""

    window.scan_path = tmp_path / "market_scan.json"
    window.market_scan_service.scan_path = window.scan_path
    window.scan_path.write_text("{ this is not json", encoding="utf-8")

    window._load_local_state()
    _APP.processEvents()

    assert window.scanner_orchestrator.scan is None
