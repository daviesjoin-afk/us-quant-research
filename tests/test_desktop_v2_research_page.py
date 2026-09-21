"""Unit tests for the ResearchPage secondary-navigation aggregate."""

from __future__ import annotations

import os
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from us_quant.desktop_v2.pages.research import (
    ResearchPage,
    ResearchWorkspace,
)
from us_quant.desktop_v2.pages.research.navigation import (
    RESEARCH_NAVIGATION_ITEMS,
)


_APP = QApplication.instance() or QApplication([])


def _pages() -> dict[ResearchWorkspace, QWidget]:
    return {workspace: QWidget() for workspace in ResearchWorkspace}


def _page() -> ResearchPage:
    return ResearchPage(_pages())


def test_navigation_table_has_the_frozen_order_and_labels() -> None:
    assert tuple(
        item.workspace for item in RESEARCH_NAVIGATION_ITEMS
    ) == (
        ResearchWorkspace.TARGETED,
        ResearchWorkspace.UNIVERSE,
        ResearchWorkspace.HISTORY,
        ResearchWorkspace.SCANNER,
        ResearchWorkspace.BACKTEST,
        ResearchWorkspace.CROSS_SECTION,
    )
    assert tuple(
        item.label for item in RESEARCH_NAVIGATION_ITEMS
    ) == (
        "针对性验证",
        "广域标的池",
        "历史数据",
        "市场扫描",
        "回测",
        "横截面研究",
    )


def test_page_adds_the_six_children_in_navigation_order() -> None:
    pages = _pages()
    page = ResearchPage(pages)

    assert tuple(
        page._tabs.tabText(index) for index in range(page._tabs.count())
    ) == tuple(item.label for item in RESEARCH_NAVIGATION_ITEMS)
    assert tuple(
        page._tabs.widget(index) for index in range(page._tabs.count())
    ) == tuple(pages[item.workspace] for item in RESEARCH_NAVIGATION_ITEMS)


def test_page_preserves_child_identity() -> None:
    pages = _pages()
    page = ResearchPage(pages)
    for workspace, child in pages.items():
        assert page._tabs.widget(
            next(
                index
                for index, item in enumerate(RESEARCH_NAVIGATION_ITEMS)
                if item.workspace is workspace
            )
        ) is child


def test_default_workspace_is_targeted() -> None:
    page = _page()
    assert page.active_workspace() is ResearchWorkspace.TARGETED


def test_semantic_navigation_switches_between_workspaces() -> None:
    page = _page()
    for workspace in (
        ResearchWorkspace.BACKTEST,
        ResearchWorkspace.SCANNER,
        ResearchWorkspace.CROSS_SECTION,
    ):
        page.set_active_workspace(workspace)
        assert page.active_workspace() is workspace


def test_child_state_survives_workspace_switches() -> None:
    pages = _pages()
    child = pages[ResearchWorkspace.BACKTEST]
    child.setProperty("marker", "kept")
    page = ResearchPage(pages)

    page.set_active_workspace(ResearchWorkspace.CROSS_SECTION)
    page.set_active_workspace(ResearchWorkspace.BACKTEST)

    assert page._tabs.currentWidget() is child
    assert child.property("marker") == "kept"


def test_missing_workspace_fails_closed() -> None:
    pages = _pages()
    del pages[ResearchWorkspace.CROSS_SECTION]
    with pytest.raises(ValueError, match="missing research workspaces"):
        ResearchPage(pages)


def test_unknown_workspace_key_fails_closed() -> None:
    pages = _pages()
    pages[cast(ResearchWorkspace, "bogus")] = QWidget()
    with pytest.raises(ValueError, match="unknown research workspaces"):
        ResearchPage(pages)


def test_unknown_workspace_request_does_not_half_apply() -> None:
    page = _page()
    page.set_active_workspace(ResearchWorkspace.BACKTEST)

    with pytest.raises(ValueError, match="unknown research workspace"):
        page.set_active_workspace(
            cast(ResearchWorkspace, "not-a-workspace")
        )

    assert page.active_workspace() is ResearchWorkspace.BACKTEST