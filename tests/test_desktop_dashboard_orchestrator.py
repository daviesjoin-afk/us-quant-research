"""Behaviour tests for ``DashboardOrchestrator`` (G1).

Dashboard owns one presentation problem: the page is a projection of four other
capabilities' *published* facts plus one retained chart fact.  These tests pin
the properties that make that ownership safe -- every repaint re-reads its
providers, nothing is cached, the chart fact has one owner, and one chart
adoption is exactly one page render.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from us_quant.desktop_v2.orchestration.dashboard import (
    DashboardOrchestrator,
    DashboardProviders,
    EMPTY_CHART,
)
from us_quant.desktop_v2.pages.dashboard.models import DashboardChartView


class _FakePage:
    """Records every render it was asked to draw."""

    def __init__(self) -> None:
        self.views: list = []

    def render(self, view) -> None:
        self.views.append(view)


class _Facts:
    """Mutable stand-ins for the four published facts.

    Shaped the way the presenter reads them: a portfolio carrying an account
    and positions, a market snapshot with the readiness fields the intraday
    card reads, and artifact facts the provenance table can translate.
    """

    def __init__(self) -> None:
        self.portfolio = SimpleNamespace(
            account=SimpleNamespace(
                net_liquidation=1234.5,
                daily_pnl=-1.5,
                environment=SimpleNamespace(value="paper"),
                account_alias="DU***67",
            ),
            positions=(object(),),
        )
        self.snapshot = SimpleNamespace(
            realtime_ready=False,
            message="尚未启动流行情",
            source_id="finnhub_trades",
        )
        self.artifacts = ()
        self.market_stop_reason = None


def _orchestrator(facts: _Facts, page: _FakePage) -> DashboardOrchestrator:
    return DashboardOrchestrator(
        page=page,
        providers=DashboardProviders(
            portfolio=lambda: facts.portfolio,
            snapshot=lambda: facts.snapshot,
            artifacts=lambda: facts.artifacts,
            market_stop_reason=lambda: facts.market_stop_reason,
        ),
    )


def test_render_current_reads_every_provider_every_time() -> None:
    facts = _Facts()
    page = _FakePage()
    orchestrator = _orchestrator(facts, page)

    orchestrator.render_current()
    facts.portfolio.account.net_liquidation = 9999.0
    facts.snapshot = SimpleNamespace(
        realtime_ready=True,
        message="",
        source_id="finnhub_trades",
    )
    orchestrator.render_current()

    assert len(page.views) == 2
    assert page.views[0] is not page.views[1]
    assert page.views[1].net_liquidation.value == "$9,999.00"
    assert page.views[1].intraday_market.value == "可用"


def test_no_canonical_fact_is_cached_between_repaints() -> None:
    """A stale portfolio or snapshot must never be painted twice."""

    facts = _Facts()
    page = _FakePage()
    orchestrator = _orchestrator(facts, page)

    orchestrator.render_current()
    first = page.views[-1]
    facts.portfolio.account.net_liquidation = 7777.0
    orchestrator.render_current()

    assert first.net_liquidation.value == "$1,234.50"
    assert page.views[-1].net_liquidation.value == "$7,777.00"


def test_the_chart_fact_has_one_owner_and_starts_empty() -> None:
    facts = _Facts()
    page = _FakePage()
    orchestrator = _orchestrator(facts, page)

    assert orchestrator.chart is EMPTY_CHART
    chart = DashboardChartView("SPY", ((1, 100.0), (2, 101.0)))
    orchestrator.set_chart(chart)
    assert orchestrator.chart is chart
    assert page.views[-1].chart is chart


def test_one_chart_adoption_is_exactly_one_render() -> None:
    facts = _Facts()
    page = _FakePage()
    orchestrator = _orchestrator(facts, page)

    before = len(page.views)
    orchestrator.set_chart(DashboardChartView("QQQ", ((1, 50.0),)))

    assert len(page.views) == before + 1


def test_the_projection_is_built_from_the_live_facts() -> None:
    facts = _Facts()
    page = _FakePage()
    orchestrator = _orchestrator(facts, page)

    view = orchestrator.build_view()
    assert view.chart is orchestrator.chart
    # The cards carry the projection of the facts the providers returned.
    assert view.net_liquidation.value == "$1,234.50"
    assert view.positions.value == "1"
    assert view.artifacts == ()

    # And the live fact flows through: the same snapshot read is projected.
    facts.market_stop_reason = "行情流正在停止"
    stopped = orchestrator.build_view()
    assert stopped.intraday_market.value == "不可用"
    assert stopped.intraday_market.note == "行情流正在停止"


def test_the_providers_are_callables_not_values() -> None:
    """A value provider would freeze the dashboard at composition time."""

    facts = _Facts()
    page = _FakePage()
    reads: list[str] = []

    def counting(name: str, value):
        def read():
            reads.append(name)
            return value

        return read

    orchestrator = DashboardOrchestrator(
        page=page,
        providers=DashboardProviders(
            portfolio=counting("portfolio", facts.portfolio),
            snapshot=counting("snapshot", facts.snapshot),
            artifacts=counting("artifacts", facts.artifacts),
            market_stop_reason=counting("reason", facts.market_stop_reason),
        ),
    )

    orchestrator.render_current()
    orchestrator.render_current()

    assert reads.count("portfolio") == 2
    assert reads.count("snapshot") == 2


def test_the_gateway_probe_is_not_a_dashboard_fact() -> None:
    """The probe is a shell diagnostic and stays out of this capability."""

    import pathlib

    source = pathlib.Path(
        pathlib.Path(__file__).resolve().parents[1]
        / "src"
        / "us_quant"
        / "desktop_v2"
        / "orchestration"
        / "dashboard"
    ).glob("*.py")
    for path in source:
        text = path.read_text(encoding="utf-8").lower()
        assert "probe_ibkr_socket" not in text, path
        assert "gateway_badge" not in text, path
