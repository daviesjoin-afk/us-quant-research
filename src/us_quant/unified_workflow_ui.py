"""Reusable widgets for the desktop's single-page workflow shell.

The widgets in this module deliberately contain no application service calls.
They provide presentation-only composition primitives so the desktop can render
workflow snapshots and route user intent through its controller layer.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QTabWidget,
)


class CollapsibleSection(QWidget):
    """A titled, optionally bounded content panel that can be expanded inline."""

    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        expanded: bool = False,
        boundary_text: str | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("workflowSection")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header_button = QPushButton(title)
        self.header_button.setObjectName("workflowSectionHeader")
        self.header_button.setCheckable(True)
        self.header_button.setAccessibleName(title)
        self.header_button.setProperty("collapsible", True)
        layout.addWidget(self.header_button)

        self.boundary_label: QLabel | None = None
        if boundary_text:
            self.boundary_label = QLabel(boundary_text)
            self.boundary_label.setObjectName("workflowBoundary")
            self.boundary_label.setWordWrap(True)
            layout.addWidget(self.boundary_label)

        self.content_container = QFrame()
        self.content_container.setObjectName("workflowSectionContent")
        content_layout = QVBoxLayout(self.content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(content)
        layout.addWidget(self.content_container)

        self.header_button.toggled.connect(self._apply_expanded)
        self.set_expanded(expanded)

    def _apply_expanded(self, expanded: bool) -> None:
        self.content_container.setVisible(expanded)
        if self.boundary_label is not None:
            self.boundary_label.setVisible(expanded)
        self.setProperty("expanded", expanded)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_expanded(self, expanded: bool) -> None:
        """Set inline visibility without affecting neighbouring sections."""
        self.header_button.setChecked(expanded)

    def is_expanded(self) -> bool:
        return self.header_button.isChecked()


class WorkflowModulePage(QFrame):
    """A persistent workflow module shown by :class:`UnifiedWorkflowPage`.

    This intentionally is *not* a collapsible section.  The page heading and
    safety boundary stay fixed while the selected module owns its own
    second-level navigation.  ``is_expanded`` and ``set_expanded`` remain as small
    compatibility shims for callers that previously treated workflow modules
    as expandable sections.
    """

    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        boundary_text: str | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("workflowModulePage")
        self._selected = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.heading_label = QLabel(title)
        self.heading_label.setObjectName("workflowModuleHeading")
        self.heading_label.setAccessibleName(title)
        layout.addWidget(self.heading_label)

        self.boundary_label: QLabel | None = None
        if boundary_text:
            self.boundary_label = QLabel(boundary_text)
            self.boundary_label.setObjectName("workflowBoundary")
            self.boundary_label.setWordWrap(True)
            layout.addWidget(self.boundary_label)

        self.content_container = QFrame()
        self.content_container.setObjectName("workflowModuleContent")
        content_layout = QVBoxLayout(self.content_container)
        content_layout.setContentsMargins(0, 12, 12, 12)
        content_layout.setSpacing(0)
        content_layout.addWidget(content)
        layout.addWidget(self.content_container, 1)

    def set_expanded(self, expanded: bool) -> None:
        """Compatibility state used by the workflow stack, not a disclosure."""
        self._selected = expanded

    def is_expanded(self) -> bool:
        """Whether this module is the active page in its workflow stack."""
        return self._selected


class UnifiedWorkflowPage(QWidget):
    """Left-rail navigation with one persistent module page visible at a time."""

    COMPACT_RAIL_THRESHOLD = 1240
    _FULL_RAIL_WIDTH = 176
    _COMPACT_RAIL_WIDTH = 52

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("unifiedWorkflowPage")
        self._sections: dict[str, WorkflowModulePage] = {}
        self._anchors: dict[str, QPushButton] = {}
        self._anchor_titles: dict[str, str] = {}
        self._active_key: str | None = None
        self._rail_is_compact = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.anchor_rail = QFrame()
        self.anchor_rail.setObjectName("workflowAnchorRail")
        self.anchor_rail.setFixedWidth(self._FULL_RAIL_WIDTH)
        rail_layout = QVBoxLayout(self.anchor_rail)
        rail_layout.setContentsMargins(8, 8, 8, 8)
        rail_layout.setSpacing(6)
        rail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.anchor_rail)

        self.module_stack = QStackedWidget()
        self.module_stack.setObjectName("workflowModuleStack")
        self.module_stack.setAccessibleName("工作模块")
        layout.addWidget(self.module_stack, 1)

    def add_section(
        self,
        key: str,
        title: str,
        widget: QWidget,
        *,
        expanded: bool = True,
        boundary_text: str | None = None,
    ) -> WorkflowModulePage:
        """Append one persistent module page and its mutually-exclusive rail item."""
        if key in self._sections:
            raise ValueError(f"Duplicate workflow section key: {key}")

        section = WorkflowModulePage(
            title,
            widget,
            boundary_text=boundary_text,
        )
        section.setProperty("workflowKey", key)
        self._sections[key] = section
        self.module_stack.addWidget(section)

        anchor = QPushButton(title)
        anchor.setObjectName("workflowAnchor")
        anchor.setFlat(True)
        anchor.setAccessibleName(title)
        anchor.setToolTip(title)
        anchor.installEventFilter(self)
        anchor.clicked.connect(
            lambda _checked=False, section_key=key: self.navigate_to(section_key)
        )
        self.anchor_rail.layout().addWidget(anchor)
        self._anchors[key] = anchor
        self._anchor_titles[key] = title
        if self._active_key is None or expanded:
            self.navigate_to(key)
        self._apply_rail_mode()
        return section

    def section(self, key: str) -> WorkflowModulePage:
        """Return a registered workflow section."""
        return self._sections[key]

    def scroll_to(self, key: str) -> None:
        """Backward-compatible alias for selecting a workflow module."""
        self.navigate_to(key)

    def navigate_to(self, key: str) -> None:
        """Select one module without rebuilding its widget tree or controls."""
        section = self.section(key)
        for candidate_key, candidate in self._sections.items():
            candidate.set_expanded(candidate_key == key)
        self.module_stack.setCurrentWidget(section)
        self._set_active_anchor(key)

    @property
    def active_key(self) -> str | None:
        """The section currently represented by the active rail anchor."""
        return self._active_key

    @property
    def rail_is_compact(self) -> bool:
        """Whether the rail is using its narrow, tooltip-backed form."""
        return self._rail_is_compact

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_rail_mode()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.KeyPress:
            key_event = event
            if isinstance(key_event, QKeyEvent) and key_event.key() in {
                Qt.Key.Key_Home,
                Qt.Key.Key_End,
            }:
                keys = tuple(self._sections)
                if keys:
                    target = keys[0] if key_event.key() == Qt.Key.Key_Home else keys[-1]
                    self.navigate_to(target)
                    self._anchors[target].setFocus()
                return True
        return super().eventFilter(watched, event)

    def _apply_rail_mode(self) -> None:
        compact = self.width() < self.COMPACT_RAIL_THRESHOLD
        if compact == self._rail_is_compact:
            return
        self._rail_is_compact = compact
        self.anchor_rail.setFixedWidth(
            self._COMPACT_RAIL_WIDTH if compact else self._FULL_RAIL_WIDTH
        )
        for key, anchor in self._anchors.items():
            title = self._anchor_titles[key]
            anchor.setText("•" if compact else title)
            anchor.setAccessibleName(title)
            anchor.setToolTip(title)
            anchor.setProperty("compact", compact)
            self._restyle(anchor)

    def _set_active_anchor(self, key: str) -> None:
        if key == self._active_key:
            return
        self._active_key = key
        for candidate_key, anchor in self._anchors.items():
            anchor.setProperty("active", candidate_key == key)
            self._restyle(anchor)

    @staticmethod
    def _restyle(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()


def tab_widget_to_accordion(
    tab_widget: QTabWidget,
    *,
    expanded_index: int = 0,
) -> QWidget:
    """Transfer all tab pages to inline sections, including nested tab widgets.

    Page widgets themselves are retained rather than copied, so existing signal
    connections and callbacks stay attached to the original controls.
    """
    container = QWidget()
    container.setObjectName("accordionContainer")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.setAlignment(Qt.AlignmentFlag.AlignTop)

    pages = _take_tab_pages(tab_widget)
    for index, (title, page) in enumerate(pages):
        _replace_descendant_tabs(page)
        layout.addWidget(
            CollapsibleSection(title, page, expanded=index == expanded_index)
        )
    return container


def _take_tab_pages(tab_widget: QTabWidget) -> list[tuple[str, QWidget]]:
    pages: list[tuple[str, QWidget]] = []
    while tab_widget.count():
        page = tab_widget.widget(0)
        title = tab_widget.tabText(0)
        if page is None:
            raise RuntimeError("QTabWidget contains an empty page")
        tab_widget.removeTab(0)
        pages.append((title, page))
    return pages


def _replace_descendant_tabs(root: QWidget) -> None:
    """Replace nested tab widgets from the deepest level upward."""
    while nested_tabs := root.findChildren(QTabWidget):
        deepest = next(
            tab for tab in nested_tabs if not tab.findChildren(QTabWidget)
        )
        replacement = tab_widget_to_accordion(deepest)
        parent = deepest.parentWidget()
        if parent is None or parent.layout() is None:
            raise RuntimeError("Nested QTabWidget must belong to a widget layout")
        _replace_layout_widget(parent.layout(), deepest, replacement)
        deepest.setParent(None)
        deepest.deleteLater()


def _replace_layout_widget(layout: QLayout, old: QWidget, new: QWidget) -> None:
    if layout.indexOf(old) < 0:
        raise RuntimeError("Nested QTabWidget is not managed by its parent layout")
    layout.replaceWidget(old, new)
