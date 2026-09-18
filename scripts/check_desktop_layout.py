from __future__ import annotations

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QWidget,
)

from us_quant.desktop import MainWindow  # noqa: E402


SIZES = (
    (1024, 720),
    (1120, 720),
    (1280, 800),
    (1440, 900),
    (1920, 1080),
)
AUDITED_TYPES = (
    QComboBox,
    QPushButton,
    QCheckBox,
    QLineEdit,
    QLabel,
    QSpinBox,
    QDoubleSpinBox,
    QDateEdit,
)


def _widget_text(widget: QWidget) -> str:
    if isinstance(widget, QComboBox):
        return widget.currentText()
    text_method = getattr(widget, "text", None)
    return str(text_method()) if callable(text_method) else ""


def _visible_pages(window: MainWindow):
    """Yield every first-level route and, for grouped routes, its sub-pages.

    Desktop UI v2 has eight first-level routes.  ``research`` and ``system``
    hold a second-level ``QTabWidget``; the rest are single pages.
    """
    shell = window.shell
    for route in shell.routes:
        shell.navigate_to(route)
        QApplication.processEvents()
        page = shell.page(route)
        secondary = page.findChild(QTabWidget, "workflowSecondaryTabs")
        if secondary is None:
            yield f"route/{route}", page
            continue
        for page_index in range(secondary.count()):
            secondary.setCurrentIndex(page_index)
            QApplication.processEvents()
            yield (
                f"route/{route}/{secondary.tabText(page_index)}",
                secondary.currentWidget(),
            )


def _audit_mode() -> list[str]:
    """Audit the single Desktop UI v2 shell across the size matrix."""

    failures: list[str] = []
    with TemporaryDirectory(prefix="usquant-ui-audit-") as state_root:
        os.environ["US_QUANT_STATE_ROOT"] = state_root
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.show()
        for width, height in SIZES:
            window.resize(width, height)
            app.processEvents()
            shell = window.shell
            expected_compact = width < shell.COMPACT_RAIL_THRESHOLD
            if shell.rail_is_compact != expected_compact:
                failures.append(
                    f"v2 {width}x{height} compact rail state mismatch"
                )
            expected_width = 52 if expected_compact else 176
            if shell.anchor_rail.width() != expected_width:
                failures.append(
                    f"v2 {width}x{height} rail width="
                    f"{shell.anchor_rail.width()} expected={expected_width}"
                )
            for route, anchor in shell._anchors.items():
                if not anchor.accessibleName() or not anchor.toolTip():
                    failures.append(
                        f"v2 {width}x{height} anchor {route} lacks accessible text"
                    )
            badges = (
                window.gateway_badge,
                window.handshake_badge,
                window.account_badge,
                window.market_badge,
                window.safety_badge,
            )
            badge_rects = []
            central = window.centralWidget()
            for badge in badges:
                origin = badge.mapTo(central, QPoint(0, 0))
                rect = badge.geometry()
                rect.moveTopLeft(origin)
                badge_rects.append((badge, rect))
                if (
                    rect.left() < 0
                    or rect.right() >= central.width()
                    or rect.top() < 0
                    or rect.bottom() >= central.height()
                ):
                    failures.append(
                        f"{width}x{height} header badge "
                        f"{badge.text()!r} outside central widget: "
                        f"{rect.getRect()} vs "
                        f"{central.width()}x{central.height()}"
                    )
            for index, (badge, rect) in enumerate(badge_rects):
                for other, other_rect in badge_rects[index + 1 :]:
                    if rect.intersects(other_rect):
                        failures.append(
                            f"{width}x{height} header badges overlap: "
                            f"{badge.text()!r} and {other.text()!r}"
                        )
            for page_name, page in _visible_pages(window):
                for widget in page.findChildren(QWidget):
                    if (
                        not isinstance(widget, AUDITED_TYPES)
                        or not widget.isVisible()
                        or widget.width() <= 0
                    ):
                        continue
                    minimum = widget.minimumSizeHint().width()
                    hint = widget.sizeHint().width()
                    below_minimum = widget.width() + 2 < minimum
                    severely_compacted = (
                        isinstance(
                            widget,
                            (QComboBox, QPushButton, QCheckBox),
                        )
                        and widget.width() < hint * 0.82
                    )
                    if below_minimum or severely_compacted:
                        failures.append(
                            f"v2 "
                            f"{width}x{height} {page_name} "
                            f"{type(widget).__name__} "
                            f"{_widget_text(widget)!r}: "
                            f"width={widget.width()} "
                            f"minimum={minimum} hint={hint}"
                        )
        window.close()
        window.deleteLater()
        app.processEvents()
    return failures


def audit() -> list[str]:
    return _audit_mode()


def main() -> int:
    failures = audit()
    if failures:
        print("Desktop layout audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(
        "Desktop UI v2 layout audit passed: "
        + ", ".join(f"{width}x{height}" for width, height in SIZES)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
