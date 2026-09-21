"""Unit tests for the SystemPage secondary-navigation aggregate."""

from __future__ import annotations

import os
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from us_quant.desktop_v2.pages.system import (
    SystemPage,
    SystemWorkspace,
)
from us_quant.desktop_v2.pages.system.navigation import (
    SYSTEM_NAVIGATION_ITEMS,
)


_APP = QApplication.instance() or QApplication([])


def _pages() -> dict[SystemWorkspace, QWidget]:
    return {workspace: QWidget() for workspace in SystemWorkspace}


def _page() -> SystemPage:
    return SystemPage(_pages())


def test_navigation_table_has_the_frozen_order_and_labels() -> None:
    assert tuple(
        item.workspace for item in SYSTEM_NAVIGATION_ITEMS
    ) == (
        SystemWorkspace.RUNTIME_EVENTS,
        SystemWorkspace.SETTINGS,
    )
    assert tuple(
        item.label for item in SYSTEM_NAVIGATION_ITEMS
    ) == (
        "运行事件",
        "系统设置",
    )


def test_page_adds_the_two_children_in_navigation_order() -> None:
    pages = _pages()
    page = SystemPage(pages)

    assert tuple(
        page._tabs.tabText(index) for index in range(page._tabs.count())
    ) == tuple(item.label for item in SYSTEM_NAVIGATION_ITEMS)
    assert tuple(
        page._tabs.widget(index) for index in range(page._tabs.count())
    ) == tuple(pages[item.workspace] for item in SYSTEM_NAVIGATION_ITEMS)


def test_page_preserves_child_identity() -> None:
    pages = _pages()
    page = SystemPage(pages)
    for index, item in enumerate(SYSTEM_NAVIGATION_ITEMS):
        assert page._tabs.widget(index) is pages[item.workspace]


def test_default_workspace_is_runtime_events() -> None:
    page = _page()
    assert page.active_workspace() is SystemWorkspace.RUNTIME_EVENTS


def test_semantic_navigation_switches_between_workspaces() -> None:
    page = _page()
    for workspace in (
        SystemWorkspace.SETTINGS,
        SystemWorkspace.RUNTIME_EVENTS,
    ):
        page.set_active_workspace(workspace)
        assert page.active_workspace() is workspace


def test_child_state_survives_workspace_switches() -> None:
    pages = _pages()
    child = pages[SystemWorkspace.SETTINGS]
    child.setProperty("marker", "kept")
    page = SystemPage(pages)

    page.set_active_workspace(SystemWorkspace.RUNTIME_EVENTS)
    page.set_active_workspace(SystemWorkspace.SETTINGS)

    assert page._tabs.currentWidget() is child
    assert child.property("marker") == "kept"


def test_missing_workspace_fails_closed() -> None:
    pages = _pages()
    del pages[SystemWorkspace.SETTINGS]
    with pytest.raises(ValueError, match="missing system workspaces"):
        SystemPage(pages)


def test_unknown_workspace_key_fails_closed() -> None:
    pages = _pages()
    pages[cast(SystemWorkspace, "bogus")] = QWidget()
    with pytest.raises(ValueError, match="unknown system workspaces"):
        SystemPage(pages)


def test_unknown_workspace_request_does_not_half_apply() -> None:
    page = _page()
    page.set_active_workspace(SystemWorkspace.SETTINGS)

    with pytest.raises(ValueError, match="unknown system workspace"):
        page.set_active_workspace(
            cast(SystemWorkspace, "not-a-workspace")
        )

    assert page.active_workspace() is SystemWorkspace.SETTINGS
