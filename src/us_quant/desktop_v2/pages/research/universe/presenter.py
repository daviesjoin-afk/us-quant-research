"""Qt-free projection for the Universe page."""

from __future__ import annotations

from us_quant.desktop_v2.pages.research.universe.models import (
    UniverseControlView,
    UniverseFilterMode,
    UniversePageView,
    UniverseRowView,
)
from us_quant.universe import UniverseRecord, UniverseSnapshot


UNIVERSE_DISPLAY_CAP = 2_500


def _row(record: UniverseRecord) -> UniverseRowView:
    return UniverseRowView(
        symbol=record.symbol,
        name=record.name,
        exchange=record.exchange,
        security_type=record.security_type,
        sector=record.sector,
        leader_tier=str(record.leader_tier or "—"),
        country_evidence=(
            f"{record.country_status} [{record.country_evidence_level}]"
        ),
        eligibility=(
            "研究+交易"
            if record.eligible_for_trading
            else "仅研究"
            if record.eligible_for_research
            else "关闭"
        ),
        note=record.exclusion_reason or "已通过",
        research_eligible=record.eligible_for_research,
        trading_eligible=record.eligible_for_trading,
    )


def universe_rows(snapshot: UniverseSnapshot | None) -> tuple[UniverseRowView, ...]:
    if snapshot is None:
        return ()
    return tuple(_row(record) for record in snapshot.records)


def filter_universe_rows(
    rows: tuple[UniverseRowView, ...],
    mode: UniverseFilterMode,
    search: str,
) -> tuple[UniverseRowView, ...]:
    needle = search.strip().lower()
    filtered: list[UniverseRowView] = []
    for row in rows:
        if mode is UniverseFilterMode.RESEARCH and not row.research_eligible:
            continue
        if mode is UniverseFilterMode.TRADING and not row.trading_eligible:
            continue
        if mode is UniverseFilterMode.EXCLUDED and row.research_eligible:
            continue
        if needle:
            haystack = f"{row.symbol} {row.name} {row.sector}".lower()
            if needle not in haystack:
                continue
        filtered.append(row)
    return tuple(filtered)


def universe_count_text(matched_count: int, visible_count: int) -> str:
    text = f"显示 {visible_count:,} / 匹配 {matched_count:,}"
    if matched_count > UNIVERSE_DISPLAY_CAP:
        text += f"（界面上限 {UNIVERSE_DISPLAY_CAP:,}）"
    return text


def control_view(
    *,
    refreshing: bool,
    cancel_requested: bool,
) -> UniverseControlView:
    if cancel_requested:
        return UniverseControlView(
            refresh_enabled=False,
            cancel_enabled=False,
            refresh_label="官方标的刷新中…",
            cancel_label="正在取消…",
        )
    if refreshing:
        return UniverseControlView(
            refresh_enabled=False,
            cancel_enabled=True,
            refresh_label="官方标的刷新中…",
            cancel_label="取消刷新",
        )
    return UniverseControlView(
        refresh_enabled=True,
        cancel_enabled=False,
        refresh_label="刷新官方标的",
        cancel_label="取消刷新",
    )


def build_universe_view(
    snapshot: UniverseSnapshot | None,
    *,
    refreshing: bool,
    cancel_requested: bool,
) -> UniversePageView:
    rows = universe_rows(snapshot)
    visible = min(len(rows), UNIVERSE_DISPLAY_CAP)
    return UniversePageView(
        rows=rows,
        controls=control_view(
            refreshing=refreshing,
            cancel_requested=cancel_requested,
        ),
        count_text=universe_count_text(len(rows), visible),
    )


__all__ = [
    "UNIVERSE_DISPLAY_CAP",
    "build_universe_view",
    "control_view",
    "filter_universe_rows",
    "universe_count_text",
    "universe_rows",
]
