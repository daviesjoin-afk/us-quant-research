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
    QWidget,
)

from us_quant.desktop import MainWindow  # noqa: E402


SIZES = (
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
    for top_index in range(window.tabs.count()):
        window.tabs.setCurrentIndex(top_index)
        QApplication.processEvents()
        workspace = window.tabs.currentWidget()
        if top_index == window.tabs.count() - 1:
            yield window.tabs.tabText(top_index), workspace
            continue
        for page_index in range(workspace.count()):
            workspace.setCurrentIndex(page_index)
            QApplication.processEvents()
            yield (
                f"{window.tabs.tabText(top_index)}/"
                f"{workspace.tabText(page_index)}",
                workspace.widget(page_index),
            )


def audit() -> list[str]:
    failures: list[str] = []
    with TemporaryDirectory(prefix="usquant-ui-audit-") as state_root:
        os.environ["US_QUANT_STATE_ROOT"] = state_root
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.show()
        for width, height in SIZES:
            window.resize(width, height)
            app.processEvents()
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
                            f"{width}x{height} {page_name} "
                            f"{type(widget).__name__} "
                            f"{_widget_text(widget)!r}: "
                            f"width={widget.width()} "
                            f"minimum={minimum} hint={hint}"
                        )
        window.close()
        app.quit()
    return failures


def main() -> int:
    failures = audit()
    if failures:
        print("Desktop layout audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(
        "Desktop layout audit passed: "
        + ", ".join(f"{width}x{height}" for width, height in SIZES)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
