"""Qt-free presenter tests for the ScannerPage migration."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from us_quant.desktop_v2.pages.research.scanner.models import (
    ScannerFilterMode,
)
from us_quant.desktop_v2.pages.research.scanner.presenter import (
    NO_SCAN_TEXT,
    build_scanner_view,
    coverage_text,
    filter_scanner_rows,
    scanner_rows,
)
from us_quant.scanner import MarketScan, ScanResult


def _result(
    symbol: str,
    *,
    name: str = "Example Corp",
    sector: str = "Technology",
    leader_tier: int = 2,
    signal: str = "观察",
    score: float = 50.0,
    close: float = 12.345,
    capacity: int = 10,
    return_20d: float = 0.01234,
    return_63d: float = -0.0456,
    volatility: float = 0.2345,
    rsi: float = 56.78,
    trade_eligible: bool = False,
) -> ScanResult:
    return ScanResult(
        symbol=symbol,
        execution_symbol=symbol,
        name=name,
        sector=sector,
        leader_tier=leader_tier,
        security_type="STK",
        trading_date=date(2026, 9, 18),
        close=close,
        execution_price=close,
        whole_share_capacity=capacity,
        average_dollar_volume_20d=1_000_000.0,
        return_20d=return_20d,
        return_63d=return_63d,
        volatility_20d=volatility,
        drawdown_252d=-0.1,
        rsi_14d=rsi,
        atr_pct_14d=0.02,
        above_sma_50=True,
        above_sma_200=True,
        score=score,
        signal=signal,
        research_eligible=True,
        trade_eligible=trade_eligible,
        reason="test",
    )


def _scan(*rows: ScanResult, skipped: dict[str, str] | None = None) -> MarketScan:
    return MarketScan(
        generated_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
        capital=1500.0,
        data_date=date(2026, 9, 18),
        results=rows,
        skipped=skipped or {},
    )


def _rows() -> tuple:
    rows = scanner_rows(
        _scan(
            _result("AAPL", name="Apple", sector="Tech", leader_tier=1),
            _result(
                "MSFT",
                name="Microsoft",
                sector="Software",
                signal="趋势候选",
                trade_eligible=True,
            ),
            _result("TSLA", name="Tesla", sector="Auto", leader_tier=1),
        )
    )
    return rows


def test_no_scan_projects_empty_rows_and_exact_copy() -> None:
    view = build_scanner_view(None, research_count=3000)
    assert view.rows == ()
    assert view.has_scan is False
    assert view.coverage.scanned_count == 0
    assert view.coverage.skipped_count == 0
    assert view.coverage.research_count == 3000
    assert coverage_text(
        view.coverage, visible_count=0, has_scan=view.has_scan
    ) == NO_SCAN_TEXT


def test_all_rows_are_projected_without_business_objects() -> None:
    rows = _rows()
    assert len(rows) == 3
    assert [row.symbol for row in rows] == ["AAPL", "MSFT", "TSLA"]
    assert not hasattr(rows[0], "trading_date")


@pytest.mark.parametrize(
    ("mode", "expected"),
    (
        (ScannerFilterMode.ALL, ["AAPL", "MSFT", "TSLA"]),
        (ScannerFilterMode.TREND, ["MSFT"]),
        (ScannerFilterMode.TRADE_ELIGIBLE, ["MSFT"]),
        (ScannerFilterMode.LEADERS, ["AAPL", "TSLA"]),
    ),
)
def test_filter_modes_are_stable(mode, expected) -> None:
    actual = filter_scanner_rows(_rows(), mode, "")
    assert [row.symbol for row in actual] == expected


@pytest.mark.parametrize(
    ("search", "expected"),
    (
        ("aapl", ["AAPL"]),
        ("APPLE", ["AAPL"]),
        ("soft", ["MSFT"]),
        ("auto", ["TSLA"]),
    ),
)
def test_search_is_case_insensitive_across_symbol_name_and_sector(
    search: str, expected: list[str]
) -> None:
    actual = filter_scanner_rows(_rows(), ScannerFilterMode.ALL, search)
    assert [row.symbol for row in actual] == expected


def test_formatting_matches_legacy_precision() -> None:
    row = scanner_rows(
        _scan(
            _result(
                "AAPL",
                leader_tier=1,
                score=50.0,
                close=1234.5,
                capacity=7,
                return_20d=0.01234,
                return_63d=-0.0456,
                volatility=0.23451,
                rsi=56.78,
            )
        )
    )[0]
    assert row.leader_tier == "1"
    assert row.score == "50.0"
    assert row.close == "$1,234.50"
    assert row.whole_share_capacity == "7"
    assert row.return_20d == "+1.2%"
    assert row.return_63d == "-4.6%"
    assert row.volatility_20d == "23.5%"
    assert row.rsi_14d == "56.8"
    assert row.trend_candidate is False
    assert row.leader is True


def test_coverage_counts_and_caller_fallback() -> None:
    scan = _scan(_result("AAPL"), skipped={"MSFT": "missing"})
    view = build_scanner_view(scan, research_count=12)
    assert view.has_scan is True
    assert view.coverage.scanned_count == 1
    assert view.coverage.skipped_count == 1
    assert view.coverage.research_count == 12
    text = coverage_text(
        view.coverage, visible_count=1, has_scan=view.has_scan
    )
    assert text == (
        "当前显示 1 · 最近实际扫描 1 · 缺少/不足 200 根日 K 1 · "
        "非中概研究池 12。筛选器只改变显示，不改变扫描范围。"
    )
