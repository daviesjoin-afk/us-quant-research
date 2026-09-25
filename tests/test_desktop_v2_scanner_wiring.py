"""Real-MainWindow wiring tests for the native ScannerPage.

v2O-C2 moved the scanner runtime into
``desktop_v2/orchestration/research/scanner``.  These tests therefore assert the
*wiring*: a click on the page reaches the capability, a symbol selection reaches
the chart read, and the window is no longer in the path.  What the capability
decides is asserted in ``test_desktop_scanner_orchestrator.py``.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.desktop_v2.pages.research.scanner.models import ScannerChartView
from us_quant.paths import STATE_ROOT_ENV
from us_quant.scanner import MarketScan, ScanResult, save_market_scan
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
        generated_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
        capital=1500.0,
        data_date=date(2026, 9, 18),
        results=(_result("AAPL", 50.0), _result("MSFT", 80.0)),
        skipped={},
    )


def _universe() -> UniverseSnapshot:
    return UniverseSnapshot(
        generated_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
        source_timestamps={"test": "now"},
        records=(
            UniverseRecord(
                symbol="AAPL",
                name="Apple",
                exchange="NASDAQ",
                security_type="STK",
                eligible_for_research=True,
            ),
            UniverseRecord(
                symbol="MSFT",
                name="Microsoft",
                exchange="NASDAQ",
                security_type="STK",
                eligible_for_research=True,
            ),
        ),
    )


def test_scan_intent_reaches_the_orchestrator(
    window: MainWindow, monkeypatch
) -> None:
    """A click, not a direct method call: the signal wiring is the subject."""

    seen: list[str] = []
    monkeypatch.setattr(
        window.scanner_orchestrator,
        "request_scan",
        lambda: seen.append("scan"),
    )

    window.scanner_page.scan_button.click()
    _APP.processEvents()

    assert seen == ["scan"]


def test_symbol_intent_reaches_the_orchestrator(
    window: MainWindow, monkeypatch
) -> None:
    """A selection, not a direct method call: the wiring is the subject."""

    seen: list[str] = []
    monkeypatch.setattr(
        window.scanner_orchestrator,
        "request_chart",
        lambda symbol: seen.append(symbol),
    )

    window.scanner_page.symbol_selected.emit("MSFT")
    _APP.processEvents()

    assert seen == ["MSFT"]


def test_the_window_no_longer_handles_the_chart(
    window: MainWindow, monkeypatch
) -> None:
    """The legacy handler is gone, and nothing replaces it on the window."""

    assert not hasattr(window, "_scanner_symbol_selected")
    assert not hasattr(window, "_run_scan")
    assert not hasattr(window, "_scan_finished")
    assert not hasattr(window, "_load_scan_file")
    assert not hasattr(window, "_publish_scanner_view")


def test_chart_load_failure_only_logs(window: MainWindow, monkeypatch) -> None:
    """A failed read is a status line and leaves the chart alone."""

    messages: list[str] = []
    monkeypatch.setattr(
        window.market_scan_service,
        "load_chart",
        lambda symbol: (_ for _ in ()).throw(FileNotFoundError("x")),
    )
    monkeypatch.setattr(window, "_log", messages.append)

    window.scanner_orchestrator.request_chart("AAPL")

    assert window.scanner_page.chart.symbol == ""
    assert messages == ["AAPL 图表读取失败：x"]


def test_the_chart_is_rendered_through_the_capability(
    window: MainWindow, monkeypatch
) -> None:
    points = ((date(2026, 9, 18), 10.0), (date(2026, 9, 19), 11.0))
    monkeypatch.setattr(
        window.market_scan_service,
        "load_chart",
        lambda symbol: points,
    )

    window.scanner_orchestrator.request_chart("AAPL")

    assert window.scanner_page.chart.symbol == "AAPL"
    assert window.scanner_page.chart.points == points


def test_scan_finished_refreshes_scanner_page(
    window: MainWindow, monkeypatch
) -> None:
    """The capability publishes a finished scan; the page follows."""

    window.scanner_orchestrator.adopt_external_scan(_scan())

    assert window.scanner_orchestrator.scan is not None
    assert window.scanner_page.table.rowCount() == 2


def test_startup_restore_refreshes_scanner_page(
    window: MainWindow, monkeypatch, tmp_path
) -> None:
    scan = _scan()
    window.scan_path = tmp_path / "market_scan.json"
    window.market_scan_service.scan_path = window.scan_path
    save_market_scan(scan, window.scan_path)

    window.scanner_orchestrator.restore_saved()

    assert window.scanner_orchestrator.scan is not None
    assert window.scanner_page.table.rowCount() == 2


def test_auto_market_scan_finished_refreshes_scanner_page(
    window: MainWindow, monkeypatch
) -> None:
    """The route's finished preparation reaches the Scanner capability once.

    G2-B moved the AutoQuant completion path onto ``ExecutionOrchestrator``: the
    deleted ``_auto_market_scan_finished`` is ``_preparation_finished``, which
    adopts the scan it just produced and hands it to the capability that owns
    scan truth.  This drives the real ``request_prepare`` through a synchronous
    task boundary -- rather than calling the retired callback by hand -- so the
    fact that reaches the capability is the one the route's own task returned.
    The shortlist build is stubbed out: sizing the *candidates* is a different
    subject, and it is not what this test asserts.
    """

    from dataclasses import replace

    scan = _scan()
    window.universe_orchestrator.restore_snapshot(_universe())
    monkeypatch.setattr(window, "_refresh_market_scope_summary", lambda: None)
    monkeypatch.setattr(
        window.execution_orchestrator, "_build_shortlist", lambda: None
    )
    monkeypatch.setattr(
        window.paper_orchestrator, "begin_preparation", lambda: None
    )
    monkeypatch.setattr(
        "us_quant.desktop.scan_market", lambda universe, **kwargs: scan
    )
    monkeypatch.setattr("us_quant.desktop.save_market_scan", lambda *a: None)

    # The adoption is counted at the route's own provider: the orchestrator
    # captured it when the window built it, so the spy replaces the provider
    # rather than the capability's method.
    orchestrator = window.execution_orchestrator
    adopted: list[object] = []
    original_adopt = orchestrator._providers.adopt_scan

    def adopt(finished: object) -> None:
        adopted.append(finished)
        original_adopt(finished)

    monkeypatch.setattr(
        orchestrator,
        "_providers",
        replace(orchestrator._providers, adopt_scan=adopt),
    )

    logged: list[str] = []
    monkeypatch.setattr(window, "_log", logged.append)

    def submit(task, *, on_success, **_kwargs):
        on_success(task(lambda message: None))
        return True

    monkeypatch.setattr(window.execution_orchestrator, "_submit_task", submit)

    window.execution_orchestrator.request_prepare()
    _APP.processEvents()

    # Exactly one adoption, and the capability's scan *is* the finished scan.
    assert adopted == [scan]
    assert window.scanner_orchestrator.scan is scan
    assert window.scanner_page.table.rowCount() == 2
    # Nobody clicked 扫描, so the adoption writes no manual completion line.
    assert not [line for line in logged if "扫描完成" in line]


def test_page_filter_does_not_change_business_scan_truth(
    window: MainWindow,
) -> None:
    """Presentation is never business truth.

    The page owns a search box and a filter combo; neither may reach the
    canonical scan.  Driven through the real adoption path, because that is
    what puts a scan on the page in production.
    """

    scan = _scan()
    window.scanner_orchestrator.adopt_external_scan(scan)
    window.scanner_page.filter_combo.setCurrentIndex(3)
    window.scanner_page.search_input.setText("MSFT")

    assert window.scanner_orchestrator.scan is scan
    assert len(window.scanner_orchestrator.scan.results) == 2
    assert window.scanner_page.table.rowCount() == 1


def test_scanner_chart_view_is_presentational_only() -> None:
    view = ScannerChartView("AAPL", ())
    assert view.symbol == "AAPL"
    assert view.points == ()
