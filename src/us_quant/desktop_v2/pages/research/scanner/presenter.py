"""Qt-free projection for the market scanner page."""

from __future__ import annotations

from us_quant.desktop_v2.pages.research.scanner.models import (
    ScannerCoverageFacts,
    ScannerFilterMode,
    ScannerPageView,
    ScannerRowView,
)
from us_quant.scanner import MarketScan, ScanResult


NO_SCAN_TEXT = (
    "尚无扫描结果。先在“数据任务”把全部研究池加入"
    "历史队列并分批补齐日 K，再运行扫描。"
)


def _row(result: ScanResult) -> ScannerRowView:
    return ScannerRowView(
        symbol=result.symbol,
        execution_symbol=result.execution_symbol,
        name=result.name,
        sector=result.sector,
        leader_tier=str(result.leader_tier),
        signal=result.signal,
        score=f"{result.score:.1f}",
        close=f"${result.close:,.2f}",
        whole_share_capacity=str(result.whole_share_capacity),
        return_20d=f"{result.return_20d:+.1%}",
        return_63d=f"{result.return_63d:+.1%}",
        volatility_20d=f"{result.volatility_20d:.1%}",
        rsi_14d=f"{result.rsi_14d:.1f}",
        reason=result.reason,
        trade_eligible=result.trade_eligible,
        trend_candidate=result.signal == "趋势候选",
        leader=result.leader_tier == 1,
    )


def scanner_rows(scan: MarketScan | None) -> tuple[ScannerRowView, ...]:
    if scan is None:
        return ()
    return tuple(_row(result) for result in scan.results)


def filter_scanner_rows(
    rows: tuple[ScannerRowView, ...],
    mode: ScannerFilterMode,
    search: str,
) -> tuple[ScannerRowView, ...]:
    needle = search.strip().casefold()
    filtered: list[ScannerRowView] = []
    for row in rows:
        if mode is ScannerFilterMode.TREND and not row.trend_candidate:
            continue
        if (
            mode is ScannerFilterMode.TRADE_ELIGIBLE
            and not row.trade_eligible
        ):
            continue
        if mode is ScannerFilterMode.LEADERS and not row.leader:
            continue
        if needle:
            haystack = f"{row.symbol} {row.name} {row.sector}".casefold()
            if needle not in haystack:
                continue
        filtered.append(row)
    return tuple(filtered)


def coverage_text(
    coverage: ScannerCoverageFacts,
    *,
    visible_count: int,
    has_scan: bool,
) -> str:
    if not has_scan:
        return NO_SCAN_TEXT
    return (
        f"当前显示 {visible_count:,} · 最近实际扫描 "
        f"{coverage.scanned_count:,} · 缺少/不足 200 根日 K "
        f"{coverage.skipped_count:,} · 非中概研究池 "
        f"{coverage.research_count:,}。筛选器只改变显示，不改变扫描范围。"
    )


def build_scanner_view(
    scan: MarketScan | None,
    *,
    research_count: int,
) -> ScannerPageView:
    coverage = ScannerCoverageFacts(
        scanned_count=len(scan.results) if scan is not None else 0,
        skipped_count=len(scan.skipped) if scan is not None else 0,
        research_count=research_count,
    )
    return ScannerPageView(
        rows=scanner_rows(scan),
        coverage=coverage,
        has_scan=scan is not None,
    )


__all__ = [
    "NO_SCAN_TEXT",
    "build_scanner_view",
    "coverage_text",
    "filter_scanner_rows",
    "scanner_rows",
]
