"""Universe presenter and page tests."""

from __future__ import annotations

import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop_v2.pages.research.universe.models import (
    UniverseControlView,
    UniverseFilterMode,
    UniversePageView,
)
from us_quant.desktop_v2.pages.research.universe.page import (
    UNIVERSE_HEADERS,
    UniversePage,
)
from us_quant.desktop_v2.pages.research.universe.presenter import (
    UNIVERSE_DISPLAY_CAP,
    build_universe_view,
    filter_universe_rows,
    universe_count_text,
    universe_rows,
)
from us_quant.universe import UniverseRecord, UniverseSnapshot


_APP = QApplication.instance() or QApplication([])


def _record(
    symbol: str,
    *,
    name: str = "Example",
    sector: str = "Technology",
    research: bool = True,
    trading: bool = False,
    country_status: str = "美国核验",
    country_evidence_level: str = "official",
    leader_tier: int = 1,
    exclusion_reason: str = "",
) -> UniverseRecord:
    return UniverseRecord(
        symbol=symbol,
        name=name,
        exchange="NASDAQ",
        security_type="STK",
        sector=sector,
        leader_tier=leader_tier,
        country_status=country_status,
        country_evidence_level=country_evidence_level,
        eligible_for_research=research,
        eligible_for_trading=trading,
        exclusion_reason=exclusion_reason,
    )


def _snapshot(records: tuple[UniverseRecord, ...]) -> UniverseSnapshot:
    return UniverseSnapshot(
        generated_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        source_timestamps={"test": "now"},
        records=records,
    )


@pytest.fixture()
def page() -> UniversePage:
    widget = UniversePage()
    yield widget
    widget.deleteLater()


def test_universe_rows_format_legacy_columns() -> None:
    rows = universe_rows(
        _snapshot(
            (
                _record("AAPL", trading=True),
                _record("MSFT", research=True, trading=False),
                _record(
                    "CN",
                    research=False,
                    trading=False,
                    country_status="中概排除",
                    country_evidence_level="denylist",
                    exclusion_reason="中概排除",
                    leader_tier=0,
                ),
            )
        )
    )
    assert rows[0].eligibility == "研究+交易"
    assert rows[1].eligibility == "仅研究"
    assert rows[2].eligibility == "关闭"
    assert rows[2].leader_tier == "—"
    assert rows[2].country_evidence == "中概排除 [denylist]"
    assert rows[2].note == "中概排除"


def test_universe_filters_and_searches_are_local() -> None:
    rows = universe_rows(
        _snapshot(
            (
                _record("AAPL", name="Apple", sector="Technology", trading=True),
                _record("MSFT", name="Microsoft", sector="Software"),
                _record("CN", name="China", sector="Energy", research=False),
            )
        )
    )
    assert [row.symbol for row in filter_universe_rows(rows, UniverseFilterMode.RESEARCH, "")] == ["AAPL", "MSFT"]
    assert [row.symbol for row in filter_universe_rows(rows, UniverseFilterMode.TRADING, "")] == ["AAPL"]
    assert [row.symbol for row in filter_universe_rows(rows, UniverseFilterMode.ALL, "")] == ["AAPL", "MSFT", "CN"]
    assert [row.symbol for row in filter_universe_rows(rows, UniverseFilterMode.EXCLUDED, "")] == ["CN"]
    assert [row.symbol for row in filter_universe_rows(rows, UniverseFilterMode.ALL, "soft")] == ["MSFT"]
    assert [row.symbol for row in filter_universe_rows(rows, UniverseFilterMode.ALL, "energy")] == ["CN"]


def test_universe_display_cap_does_not_change_matched_count() -> None:
    records = tuple(_record(f"S{index:04d}") for index in range(UNIVERSE_DISPLAY_CAP + 1))
    rows = universe_rows(_snapshot(records))
    matched = filter_universe_rows(rows, UniverseFilterMode.RESEARCH, "")
    visible = matched[:UNIVERSE_DISPLAY_CAP]
    assert len(visible) == UNIVERSE_DISPLAY_CAP
    assert "界面上限 2,500" in universe_count_text(len(matched), len(visible))


def test_universe_view_has_idle_refreshing_and_cancelling_controls() -> None:
    idle = build_universe_view(None, refreshing=False, cancel_requested=False)
    refreshing = build_universe_view(None, refreshing=True, cancel_requested=False)
    cancelling = build_universe_view(None, refreshing=True, cancel_requested=True)
    assert idle.controls.refresh_enabled is True
    assert idle.controls.cancel_enabled is False
    assert refreshing.controls.refresh_label == "官方标的刷新中…"
    assert refreshing.controls.cancel_enabled is True
    assert cancelling.controls.cancel_label == "正在取消…"
    assert cancelling.controls.cancel_enabled is False


def test_refresh_and_cancel_buttons_only_emit(page: UniversePage) -> None:
    seen: list[str] = []
    page.refresh_requested.connect(lambda: seen.append("refresh"))
    page.cancel_refresh_requested.connect(lambda: seen.append("cancel"))
    page.refresh_button.click()
    page.cancel_button.setEnabled(True)
    page.cancel_button.click()
    assert seen == ["refresh", "cancel"]


def test_filter_combo_uses_stable_ids(page: UniversePage) -> None:
    keys = [page.filter_combo.itemData(index) for index in range(page.filter_combo.count())]
    assert keys == [mode.value for mode in UniverseFilterMode]


def test_search_and_filter_apply_to_local_rows(page: UniversePage) -> None:
    view = UniversePageView(
        rows=universe_rows(
            _snapshot(
                (
                    _record("AAPL", name="Apple", sector="Technology"),
                    _record("MSFT", name="Microsoft", sector="Software"),
                )
            )
        ),
        controls=UniverseControlView(True, False, "刷新官方标的", "取消刷新"),
    )
    page.render(view)
    assert page.table.rowCount() == 2
    page.search_input.setText("soft")
    assert page.table.rowCount() == 1
    assert page.table.item(0, 0).text() == "MSFT"
    page.search_input.clear()
    page.filter_combo.setCurrentIndex(page.filter_combo.findData("excluded"))
    assert page.table.rowCount() == 0


def test_universe_page_has_frozen_headers_and_no_service_attributes(page: UniversePage) -> None:
    assert page.table.columnCount() == len(UNIVERSE_HEADERS)
    assert tuple(page.table.horizontalHeaderItem(i).text() for i in range(9)) == UNIVERSE_HEADERS
    for forbidden in ("universe_service", "universe", "refresh_official_universe"):
        assert not hasattr(page, forbidden)


def test_universe_render_updates_controls(page: UniversePage) -> None:
    page.render(
        UniversePageView(
            rows=(),
            controls=UniverseControlView(
                refresh_enabled=False,
                cancel_enabled=False,
                refresh_label="官方标的刷新中…",
                cancel_label="正在取消…",
            ),
        )
    )
    assert page.refresh_button.isEnabled() is False
    assert page.cancel_button.isEnabled() is False
    assert page.refresh_button.text() == "官方标的刷新中…"
    assert page.cancel_button.text() == "正在取消…"
