"""Real-MainWindow wiring tests for the native ScannerPage."""

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



def test_scan_intent_reaches_run_scan(window: MainWindow, monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(window, "_run_scan", lambda: seen.append("scan"))
    window.scanner_page.scan_requested.disconnect()
    window._connect_scanner_page()
    window.scanner_page.scan_button.click()
    assert seen == ["scan"]


def test_symbol_intent_reaches_window_handler(
    window: MainWindow, monkeypatch
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        window, "_scanner_symbol_selected", lambda symbol: seen.append(symbol)
    )
    window.scanner_page.symbol_selected.disconnect()
    window._connect_scanner_page()
    window.scanner_page.symbol_selected.emit("MSFT")
    assert seen == ["MSFT"]


def test_chart_handler_loads_and_renders_symbol(
    window: MainWindow, monkeypatch
) -> None:
    points = ((date(2026, 9, 18), 10.0), (date(2026, 9, 19), 11.0))
    monkeypatch.setattr(
        "us_quant.desktop.load_close_series",
        lambda symbol, **_kwargs: points,
    )
    window._scanner_symbol_selected("AAPL")
    assert window.scanner_page.chart.symbol == "AAPL"
    assert window.scanner_page.chart.points == points


def test_chart_load_failure_only_logs(window: MainWindow, monkeypatch) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        "us_quant.desktop.load_close_series",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("x")),
    )
    monkeypatch.setattr(window, "_log", messages.append)
    window._scanner_symbol_selected("AAPL")
    assert window.scanner_page.chart.symbol == ""
    assert messages == ["AAPL 图表读取失败：x"]


def test_scan_finished_refreshes_scanner_page(
    window: MainWindow, monkeypatch
) -> None:
    monkeypatch.setattr(window, "_refresh_cards", lambda: None)
    monkeypatch.setattr(window, "_refresh_market_scope_summary", lambda: None)
    window._scan_finished(_scan())
    assert window.scan is not None
    assert window.scanner_page.table.rowCount() == 2


def test_load_scan_file_refreshes_scanner_page(
    window: MainWindow, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(window, "_refresh_cards", lambda: None)
    scan = _scan()
    window.scan_path = tmp_path / "market_scan.json"
    save_market_scan(scan, window.scan_path)
    window._load_scan_file()
    assert window.scan is not None
    assert window.scanner_page.table.rowCount() == 2


def test_auto_market_scan_finished_refreshes_scanner_page(
    window: MainWindow, monkeypatch
) -> None:
    window.universe = _universe()
    monkeypatch.setattr(window, "_refresh_cards", lambda: None)
    monkeypatch.setattr(window, "_refresh_market_scope_summary", lambda: None)
    monkeypatch.setattr(window, "_select_auto_quant_candidates", lambda: None)
    window._auto_market_scan_finished(_scan())
    assert window.scan is not None
    assert window.scanner_page.table.rowCount() == 2


def test_page_filter_does_not_change_business_scan_truth(
    window: MainWindow,
) -> None:
    scan = _scan()
    window.scan = scan
    window._publish_scanner_view()
    window.scanner_page.filter_combo.setCurrentIndex(3)
    window.scanner_page.search_input.setText("MSFT")
    assert window.scan is scan
    assert len(window.scan.results) == 2
    assert window.scanner_page.table.rowCount() == 1


def test_scanner_chart_view_is_presentational_only() -> None:
    view = ScannerChartView("AAPL", ())
    assert view.symbol == "AAPL"
    assert view.points == ()
