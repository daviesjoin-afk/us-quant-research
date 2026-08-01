from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QTabWidget, QVBoxLayout, QWidget

from us_quant.unified_workflow_ui import UnifiedWorkflowPage, tab_widget_to_accordion


_APP = QApplication.instance() or QApplication([])


def _content(text: str) -> QWidget:
    widget = QWidget()
    widget.setMinimumHeight(100)
    layout = QVBoxLayout(widget)
    layout.addWidget(QLabel(text))
    return widget


def test_workflow_page_keeps_persistent_modules_and_selects_target() -> None:
    page = UnifiedWorkflowPage()
    for key, title in (
        ("today", "Today"),
        ("shadow", "Shadow"),
        ("research", "Research"),
        ("audit", "Audit"),
    ):
        page.add_section(key, title, _content(title), expanded=False)

    page.resize(800, 280)
    page.show()
    _APP.processEvents()
    research = page.section("research")
    page.scroll_to("research")
    _APP.processEvents()

    assert len(page._sections) == 4
    assert page.active_key == "research"
    assert page.module_stack.currentWidget() is research
    assert research.is_expanded()
    assert sum(section.is_expanded() for section in page._sections.values()) == 1
    assert all(
        section.parentWidget() is page.module_stack
        for section in page._sections.values()
    )
    assert not hasattr(page, "scroll_area")


def test_anchor_click_switches_exclusively_without_recreating_modules() -> None:
    page = UnifiedWorkflowPage()
    first = page.add_section("first", "First", _content("first"), expanded=True)
    second = page.add_section("second", "Second", _content("second"), expanded=False)
    first_identity = id(first)
    second_identity = id(second)
    page.show()
    _APP.processEvents()

    page._anchors["second"].click()
    _APP.processEvents()

    assert not first.is_expanded()
    assert second.is_expanded()
    assert page.module_stack.currentWidget() is second
    assert id(page.section("first")) == first_identity
    assert id(page.section("second")) == second_identity


def test_boundary_belongs_to_its_module_and_stays_fixed_above_content() -> None:
    page = UnifiedWorkflowPage()
    today = page.add_section("today", "Today", _content("today"), expanded=True)
    shadow = page.add_section(
        "shadow",
        "Shadow",
        _content("shadow"),
        boundary_text="Internal simulation only",
        expanded=False,
    )
    page.show()
    _APP.processEvents()

    assert shadow.boundary_label is not None
    assert shadow.boundary_label.parentWidget() is shadow
    assert not shadow.boundary_label.isVisible()
    page.navigate_to("shadow")
    _APP.processEvents()
    assert shadow.boundary_label.isVisible()
    assert page.module_stack.currentWidget() is shadow
    assert today.content_container is not shadow.content_container


def test_responsive_rail_compacts_with_accessible_anchor_names() -> None:
    page = UnifiedWorkflowPage()
    page.add_section("today", "Today", _content("today"))
    page.add_section("research", "Research", _content("research"))
    page.resize(UnifiedWorkflowPage.COMPACT_RAIL_THRESHOLD - 1, 360)
    page.show()
    _APP.processEvents()

    assert page.rail_is_compact
    assert page.anchor_rail.width() == 52
    assert page._anchors["today"].text() == "•"
    assert page._anchors["today"].accessibleName() == "Today"
    assert page._anchors["today"].toolTip() == "Today"

    page.resize(UnifiedWorkflowPage.COMPACT_RAIL_THRESHOLD, 360)
    _APP.processEvents()
    assert not page.rail_is_compact
    assert page.anchor_rail.width() == 176
    assert page._anchors["today"].text() == "Today"


def test_navigation_and_home_end_keep_one_active_module() -> None:
    page = UnifiedWorkflowPage()
    for key in ("today", "shadow", "research", "audit"):
        page.add_section(key, key, _content(key), expanded=True)
    page.resize(900, 180)
    page.show()
    _APP.processEvents()

    page.navigate_to("research")
    _APP.processEvents()
    assert page.active_key == "research"
    assert page._anchors["research"].property("active") is True
    assert sum(anchor.property("active") is True for anchor in page._anchors.values()) == 1
    assert sum(section.is_expanded() for section in page._sections.values()) == 1

    page._anchors["research"].setFocus()
    QTest.keyClick(page._anchors["research"], Qt.Key.Key_Home)
    _APP.processEvents()
    assert page.active_key == "today"

    QTest.keyClick(page._anchors["today"], Qt.Key.Key_End)
    _APP.processEvents()
    assert page.active_key == "audit"


def test_tab_widget_conversion_preserves_order_callbacks_and_removes_nested_tabs() -> None:
    outer = QTabWidget()
    first = _content("first")
    second = QWidget()
    second_layout = QVBoxLayout(second)
    nested = QTabWidget()
    nested.addTab(_content("nested first"), "Nested one")
    nested.addTab(_content("nested second"), "Nested two")
    second_layout.addWidget(nested)
    outer.addTab(first, "First")
    outer.addTab(second, "Second")

    accordion = tab_widget_to_accordion(outer, expanded_index=1)

    sections = accordion.findChildren(QWidget, "workflowSection")
    top_level_sections = [
        accordion.layout().itemAt(index).widget()
        for index in range(accordion.layout().count())
    ]
    assert [section.header_button.text() for section in top_level_sections] == ["First", "Second"]
    assert top_level_sections[0].content_container.findChild(QLabel).text() == "first"
    assert top_level_sections[1].is_expanded()
    assert not top_level_sections[0].is_expanded()
    assert not accordion.findChildren(QTabWidget)
    assert len(sections) == 4
