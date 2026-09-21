"""Immutable model contract for the Dashboard v2 route."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from us_quant.desktop_v2.pages.dashboard.models import (
    DashboardArtifactRowView,
    DashboardArtifactTone,
    DashboardChartView,
    DashboardMetricView,
    DashboardView,
)


def test_metric_and_chart_views_are_frozen_and_slotted() -> None:
    metric = DashboardMetricView("可用", "fresh 实时 + bid/ask")
    chart = DashboardChartView("SPY", ((date(2026, 9, 18), 10.0),))

    with pytest.raises(FrozenInstanceError):
        metric.value = "不可用"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        chart.symbol = "QQQ"  # type: ignore[misc]
    assert not hasattr(metric, "__dict__")
    assert not hasattr(chart, "__dict__")


def test_artifact_tone_has_the_three_render_tones() -> None:
    assert {tone.value for tone in DashboardArtifactTone} == {
        "neutral",
        "warning",
        "error",
    }


def test_dashboard_view_carries_every_render_fact() -> None:
    row = DashboardArtifactRowView(
        artifact_type="market_scan",
        status_text="探索性研究",
        data_as_of="2026-09-18",
        generated_at="2026-09-19T00:00:00Z",
        source="local",
        run_id="abc",
        limitations="无",
        tone=DashboardArtifactTone.NEUTRAL,
    )
    view = DashboardView(
        net_liquidation=DashboardMetricView("未读取", "刷新"),
        daily_pnl=DashboardMetricView("不可用", "不会代替"),
        positions=DashboardMetricView("未读取", "严格区分"),
        intraday_market=DashboardMetricView("不可用", "尚未启动"),
        artifacts=(row,),
        chart=DashboardChartView(None, ()),
        research_boundary_text="边界",
    )
    assert view.artifacts == (row,)
    assert view.chart.symbol is None
