"""Research secondary-navigation aggregate."""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from .navigation import RESEARCH_NAVIGATION_ITEMS, ResearchWorkspace


class ResearchPage(QWidget):
    """Contain six existing child pages and switch between their workspaces."""

    def __init__(
        self,
        pages: Mapping[ResearchWorkspace, QWidget],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pages = dict(pages)

        expected = set(ResearchWorkspace)
        actual = set(self._pages)
        missing = expected - actual
        if missing:
            names = ", ".join(
                sorted(workspace.value for workspace in missing)
            )
            raise ValueError(f"missing research workspaces: {names}")
        unknown = actual - expected
        if unknown:
            names = ", ".join(sorted(repr(workspace) for workspace in unknown))
            raise ValueError(f"unknown research workspaces: {names}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabWidget(self)
        self._tabs.setObjectName("workflowSecondaryTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.setTabPosition(QTabWidget.North)
        for item in RESEARCH_NAVIGATION_ITEMS:
            self._tabs.addTab(self._pages[item.workspace], item.label)
        layout.addWidget(self._tabs)

        self.set_active_workspace(ResearchWorkspace.TARGETED)

    def set_active_workspace(self, workspace: ResearchWorkspace) -> None:
        """Show ``workspace`` or raise for an unknown semantic key."""

        if not isinstance(workspace, ResearchWorkspace):
            raise ValueError(f"unknown research workspace: {workspace!r}")
        widget = self._pages.get(workspace)
        if widget is None:
            raise ValueError(f"unknown research workspace: {workspace!r}")
        self._tabs.setCurrentWidget(widget)

    def active_workspace(self) -> ResearchWorkspace:
        """Return the semantic key for the visible child page."""

        current = self._tabs.currentWidget()
        for item in RESEARCH_NAVIGATION_ITEMS:
            if self._pages[item.workspace] is current:
                return item.workspace
        raise RuntimeError("current research workspace is not registered")