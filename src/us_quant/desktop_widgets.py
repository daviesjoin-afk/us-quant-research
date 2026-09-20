"""Presentation widgets for the desktop shell.

These widgets and formatting helpers are pure presentation: they render a value
they are handed and own no application state.  They live apart from
:mod:`us_quant.desktop` so the rendering rules can be read and tested without
importing the 9k-line window module.

``QuoteTableModel`` used to live here and has moved to
``desktop_v2/pages/market/tables.py``: it was only ever the market route's table,
and leaving it behind would have kept two table models alive for one route.

``QTableWidgetItem`` deliberately stays in ``desktop.py``: it is a table
cell used throughout ``MainWindow``, not a standalone widget.  It imports
``_sortable_number`` from here.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from PySide6.QtCore import (
    QPointF,
    QRectF,
    Qt,
)

from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from us_quant.ui_theme import theme_palette


def configure_table(table: QTableView) -> None:
    """Apply the workbench's shared read-only table behaviour.

    One implementation, because two would be free to drift and the drift would
    show up as one table looking different from its neighbours.  It takes
    ``QTableView`` rather than ``QTableWidget`` so the model-backed and
    item-backed tables share it: every call below is defined on the base class,
    and the selection constants the window used to spell as ``QTableWidget.*``
    are the same values on ``QAbstractItemView``.
    """

    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.ElideRight)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.Interactive)
    header.setMinimumSectionSize(72)
    header.setDefaultSectionSize(128)
    header.setStretchLastSection(True)
    table.setSortingEnabled(True)


def configure_combo_width(
    combo: QComboBox,
    *,
    minimum_width: int,
    minimum_contents: int,
) -> None:
    """Size a runtime-selection combo the way every combo in the workbench is.

    Shared for the same reason ``configure_table`` is: the execution page's
    strategy combo and the window's other selection combos must not drift into
    two different widths or two different tooltip behaviours.
    """

    combo.setMinimumWidth(minimum_width)
    combo.setMinimumContentsLength(minimum_contents)
    combo.setSizeAdjustPolicy(
        QComboBox.AdjustToMinimumContentsLengthWithIcon
    )
    combo.setSizePolicy(
        QSizePolicy.Expanding,
        QSizePolicy.Fixed,
    )
    combo.currentTextChanged.connect(combo.setToolTip)
    combo.setToolTip(combo.currentText())


def _sortable_number(value: str) -> float | None:
    cleaned = (
        value.strip()
        .replace(",", "")
        .replace("$", "")
        .replace("¥", "")
    )
    percent = cleaned.endswith("%")
    if percent:
        cleaned = cleaned[:-1]
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", cleaned):
        return None
    number = float(cleaned)
    return number / 100 if percent else number


def _price(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.4f}".rstrip("0").rstrip(".")


class MetricCard(QFrame):
    def __init__(self, title: str, value: str, note: str) -> None:
        super().__init__()
        self.setObjectName("metricCard")
        self.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Preferred,
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("metricTitle")
        title_label.setWordWrap(True)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        self.note_label = QLabel(note)
        self.note_label.setObjectName("metricNote")
        self.note_label.setWordWrap(True)
        for label in (
            title_label,
            self.value_label,
            self.note_label,
        ):
            label.setSizePolicy(
                QSizePolicy.Ignored,
                QSizePolicy.Preferred,
            )
            label.setMinimumWidth(0)
        title_label.setToolTip(title)
        self.note_label.setToolTip(note)
        layout.addWidget(title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.note_label)

    def set_value(self, value: str, note: str | None = None) -> None:
        self.value_label.setText(value)
        if note is not None:
            self.note_label.setText(note)
            self.note_label.setToolTip(note)


class PriceChart(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(260)
        self.symbol = ""
        self.points: tuple[tuple[date, float], ...] = ()
        self.display_title = ""
        self.empty_message = "选择扫描结果后显示最近 180 根日 K 收盘曲线"

    def set_series(
        self,
        symbol: str,
        points: tuple[tuple[date, float], ...],
        *,
        title: str = "",
    ) -> None:
        self.symbol = symbol
        self.points = points[-180:]
        self.display_title = title
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = theme_palette(
            str(self.window().property("uiTheme") or "dark")
        )
        painter.fillRect(self.rect(), QColor(palette.surface))
        bounds = QRectF(
            56,
            30,
            max(10, self.width() - 82),
            max(10, self.height() - 70),
        )
        painter.setPen(QPen(QColor(palette.grid), 1))
        for index in range(5):
            y = bounds.top() + bounds.height() * index / 4
            painter.drawLine(
                QPointF(bounds.left(), y),
                QPointF(bounds.right(), y),
            )
        if len(self.points) < 2:
            painter.setPen(QColor(palette.muted))
            painter.drawText(
                self.rect(),
                Qt.AlignCenter,
                self.empty_message,
            )
            return
        values = [value for _, value in self.points]
        low = min(values)
        high = max(values)
        spread = max(0.0001, high - low)
        path = QPainterPath()
        coordinates: list[QPointF] = []
        for index, value in enumerate(values):
            x = bounds.left() + bounds.width() * index / (
                len(values) - 1
            )
            y = bounds.bottom() - bounds.height() * (
                value - low
            ) / spread
            coordinates.append(QPointF(x, y))
        path.moveTo(coordinates[0])
        for point in coordinates[1:]:
            path.lineTo(point)
        fill = QPainterPath(path)
        fill.lineTo(bounds.right(), bounds.bottom())
        fill.lineTo(bounds.left(), bounds.bottom())
        fill.closeSubpath()
        gradient = QLinearGradient(
            0,
            bounds.top(),
            0,
            bounds.bottom(),
        )
        gradient_start = QColor(palette.accent)
        gradient_start.setAlpha(105)
        gradient_end = QColor(palette.accent)
        gradient_end.setAlpha(6)
        gradient.setColorAt(0, gradient_start)
        gradient.setColorAt(1, gradient_end)
        painter.fillPath(fill, gradient)
        painter.setPen(QPen(QColor(palette.accent), 2.2))
        painter.drawPath(path)
        painter.setPen(QColor(palette.text))
        painter.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        painter.drawText(
            18,
            22,
            self.display_title
            or f"{self.symbol} · 最近 180 个交易日",
        )
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.setPen(QColor(palette.muted))
        painter.drawText(
            6,
            int(bounds.top() + 5),
            f"{high:.2f}",
        )
        painter.drawText(
            6,
            int(bounds.bottom()),
            f"{low:.2f}",
        )
        painter.drawText(
            int(bounds.left()),
            self.height() - 12,
            self.points[0][0].isoformat(),
        )
        painter.drawText(
            int(bounds.right() - 82),
            self.height() - 12,
            self.points[-1][0].isoformat(),
        )


class EquityComparisonChart(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(260)
        self.rows: list[dict] = []
        self.secondary_key = "spy_equity"
        self.secondary_label = "SPY 整股"

    def set_rows(self, rows: list[dict]) -> None:
        self.rows = rows
        if rows and "cost_2x_equity" in rows[0]:
            self.secondary_key = "cost_2x_equity"
            self.secondary_label = "2×成本"
        else:
            self.secondary_key = "spy_equity"
            self.secondary_label = "SPY 整股"
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = theme_palette(
            str(self.window().property("uiTheme") or "dark")
        )
        painter.fillRect(self.rect(), QColor(palette.surface))
        bounds = QRectF(
            58,
            38,
            max(10, self.width() - 84),
            max(10, self.height() - 76),
        )
        painter.setPen(QPen(QColor(palette.grid), 1))
        for index in range(5):
            y = bounds.top() + bounds.height() * index / 4
            painter.drawLine(
                QPointF(bounds.left(), y),
                QPointF(bounds.right(), y),
            )
        if len(self.rows) < 2:
            painter.setPen(QColor(palette.muted))
            painter.drawText(
                self.rect(),
                Qt.AlignCenter,
                "运行组合走样本外研究后显示权益曲线",
            )
            return
        strategy = [float(row["strategy_equity"]) for row in self.rows]
        spy = [
            float(row[self.secondary_key]) for row in self.rows
        ]
        low = min(strategy + spy)
        high = max(strategy + spy)
        spread = max(0.0001, high - low)

        def path_for(values: list[float]) -> QPainterPath:
            path = QPainterPath()
            for index, value in enumerate(values):
                point = QPointF(
                    bounds.left()
                    + bounds.width() * index / (len(values) - 1),
                    bounds.bottom()
                    - bounds.height() * (value - low) / spread,
                )
                if index == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            return path

        painter.setPen(QPen(QColor(palette.accent), 2.2))
        painter.drawPath(path_for(strategy))
        painter.setPen(QPen(QColor(palette.accent_alt), 1.8))
        painter.drawPath(path_for(spy))
        painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        painter.setPen(QColor(palette.accent))
        painter.drawText(18, 22, "复权价研究代理")
        painter.setPen(QColor(palette.accent_alt))
        painter.drawText(105, 22, self.secondary_label)
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.setPen(QColor(palette.muted))
        painter.drawText(8, int(bounds.top() + 4), f"${high:,.0f}")
        painter.drawText(8, int(bounds.bottom()), f"${low:,.0f}")
        painter.drawText(
            int(bounds.left()),
            self.height() - 12,
            str(self.rows[0]["date"]),
        )
        painter.drawText(
            int(bounds.right() - 82),
            self.height() - 12,
            str(self.rows[-1]["date"]),
        )
