"""System secondary-navigation aggregate.

``SystemPage`` is containment and navigation only.  It receives the child pages
already built, adds them in the frozen order and switches between them by
semantic key.  It deliberately cannot construct a child, cannot call one and
cannot reach a store, a settings service or a credential service: if any of
those appear here the aggregate has become an orchestrator.

Child identity is preserved -- the exact widget handed in is the widget shown --
so per-page state survives navigation.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from .navigation import SYSTEM_NAVIGATION_ITEMS, SystemWorkspace


class SystemPage(QWidget):
    """Contain the two System child pages and switch between their workspaces."""

    def __init__(
        self,
        pages: Mapping[SystemWorkspace, QWidget],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pages = dict(pages)

        expected = set(SystemWorkspace)
        actual = set(self._pages)
        missing = expected - actual
        if missing:
            names = ", ".join(
                sorted(workspace.value for workspace in missing)
            )
            raise ValueError(f"missing system workspaces: {names}")
        unknown = actual - expected
        if unknown:
            names = ", ".join(sorted(repr(workspace) for workspace in unknown))
            raise ValueError(f"unknown system workspaces: {names}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabWidget(self)
        self._tabs.setObjectName("workflowSecondaryTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.setTabPosition(QTabWidget.North)
        for item in SYSTEM_NAVIGATION_ITEMS:
            self._tabs.addTab(self._pages[item.workspace], item.label)
        layout.addWidget(self._tabs)

        self.set_active_workspace(SystemWorkspace.RUNTIME_EVENTS)

    def set_active_workspace(self, workspace: SystemWorkspace) -> None:
        """Show ``workspace`` or raise for an unknown semantic key."""

        if not isinstance(workspace, SystemWorkspace):
            raise ValueError(f"unknown system workspace: {workspace!r}")
        widget = self._pages.get(workspace)
        if widget is None:
            raise ValueError(f"unknown system workspace: {workspace!r}")
        self._tabs.setCurrentWidget(widget)

    def active_workspace(self) -> SystemWorkspace:
        """Return the semantic key for the visible child page."""

        current = self._tabs.currentWidget()
        for item in SYSTEM_NAVIGATION_ITEMS:
            if self._pages[item.workspace] is current:
                return item.workspace
        raise RuntimeError("current system workspace is not registered")
