"""The Desktop UI v2 shell: a navigation rail plus a page stack.

The shell is deliberately ignorant of the business.  It receives already-built
page widgets, shows one at a time, and switches between them.  It must never
import a market-data service, the Paper stack, AutoQuant, the risk engine, the
strategy registry or any IBKR module, and it must never connect a broker,
start a stream or submit an order.  If any of those appear here, the shell has
become an orchestrator again -- which is the failure mode this rewrite exists
to prevent.

Page identity is preserved: the exact widget object handed in is the object
placed in the stack.  The shell never copies, re-wraps or rebuilds a page, so
per-page state cannot be silently discarded by navigation.

Unknown routes fail closed.  An unregistered route is a programming error, so
it raises rather than silently falling back to another page -- a fallback
would show the user the wrong screen while looking like it worked.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from us_quant.desktop_v2.navigation import (
    DEFAULT_ROUTE,
    NAVIGATION_ITEMS,
    ROUTES,
    label_for,
)


class DesktopShellV2(QWidget):
    """Left-rail navigation with one persistent page visible at a time."""

    COMPACT_RAIL_THRESHOLD = 1240
    _FULL_RAIL_WIDTH = 176
    _COMPACT_RAIL_WIDTH = 52

    def __init__(
        self,
        pages: Mapping[str, QWidget],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("desktopShellV2")

        # Fail closed on an incomplete or over-specified route table.  A
        # missing page would otherwise surface later as a blank screen, and an
        # extra key would be a route the navigation does not own.
        missing = [route for route in ROUTES if route not in pages]
        if missing:
            raise ValueError(f"missing pages for routes: {sorted(missing)}")
        unknown = [route for route in pages if route not in ROUTES]
        if unknown:
            raise ValueError(f"unknown routes: {sorted(unknown)}")

        self._pages: dict[str, QWidget] = dict(pages)
        self._anchors: dict[str, QPushButton] = {}
        self._active_route: str | None = None
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

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("workflowModuleStack")
        self.page_stack.setAccessibleName("一级导航页面")
        layout.addWidget(self.page_stack, 1)

        for item in NAVIGATION_ITEMS:
            page = self._pages[item.route]
            self.page_stack.addWidget(page)

            anchor = QPushButton(item.label)
            anchor.setObjectName("workflowAnchor")
            anchor.setFlat(True)
            anchor.setAccessibleName(item.label)
            anchor.setToolTip(item.label)
            anchor.clicked.connect(
                lambda _checked=False, route=item.route: self.navigate_to(route)
            )
            rail_layout.addWidget(anchor)
            self._anchors[item.route] = anchor

        self.navigate_to(DEFAULT_ROUTE)
        self._apply_rail_mode()

    @property
    def routes(self) -> tuple[str, ...]:
        """The registered routes, in navigation order."""
        return ROUTES

    @property
    def current_route(self) -> str | None:
        """The route currently displayed."""
        return self._active_route

    @property
    def rail_is_compact(self) -> bool:
        """Whether the rail is using its narrow, tooltip-backed form."""
        return self._rail_is_compact

    def page(self, route: str) -> QWidget:
        """Return the widget registered for ``route``."""
        try:
            return self._pages[route]
        except KeyError:
            raise KeyError(f"unknown route: {route!r}") from None

    def navigate_to(self, route: str) -> None:
        """Show ``route`` without rebuilding or re-creating any page.

        Raises ``KeyError`` for an unregistered route.  The current page is
        left untouched on failure, so a bad route cannot half-apply.
        """
        if route not in self._pages:
            raise KeyError(f"unknown route: {route!r}")

        self._active_route = route
        self.page_stack.setCurrentWidget(self._pages[route])
        for candidate, anchor in self._anchors.items():
            anchor.setProperty("active", candidate == route)
            self._restyle(anchor)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_rail_mode()

    def _apply_rail_mode(self) -> None:
        compact = self.width() < self.COMPACT_RAIL_THRESHOLD
        if compact == self._rail_is_compact:
            return
        self._rail_is_compact = compact
        self.anchor_rail.setFixedWidth(
            self._COMPACT_RAIL_WIDTH if compact else self._FULL_RAIL_WIDTH
        )
        for route, anchor in self._anchors.items():
            label = label_for(route)
            anchor.setText("•" if compact else label)
            anchor.setAccessibleName(label)
            anchor.setToolTip(label)
            anchor.setProperty("compact", compact)
            self._restyle(anchor)

    @staticmethod
    def _restyle(widget: QWidget) -> None:
        """Re-evaluate style-sheet property selectors after a property change."""
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()
