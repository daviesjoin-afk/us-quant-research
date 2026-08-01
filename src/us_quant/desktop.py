from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from threading import Event
from time import monotonic
from typing import Callable

from PySide6.QtCore import (
    QAbstractTableModel,
    QDate,
    QModelIndex,
    QPointF,
    QRectF,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem as _QTableWidgetItem,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class QTableWidgetItem(_QTableWidgetItem):
    """Keep display formatting while sorting numeric cells numerically."""

    def __lt__(self, other: _QTableWidgetItem) -> bool:
        left_order = self.data(Qt.UserRole + 1)
        right_order = other.data(Qt.UserRole + 1)
        if left_order is not None and right_order is not None:
            return left_order < right_order
        left = _sortable_number(self.text())
        right = _sortable_number(other.text())
        if left is not None and right is not None:
            return left < right
        return self.text().casefold() < other.text().casefold()


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

from us_quant.config import load_config
from us_quant.credential_store import (
    CredentialStoreError,
    WindowsCredentialStore,
)
from us_quant.artifact_state import (
    ArtifactCatalog,
    load_artifact_catalog,
)
from us_quant.paths import ApplicationPaths
from us_quant.account_ledger import AccountLedger
from us_quant.history_queue import HistoryJobStore, run_history_queue
from us_quant.ibkr import IBKRConnectionConfig, probe_ibkr_socket
from us_quant.ibkr_readonly import (
    IBKRReadOnlySnapshot,
    collect_readonly_snapshot,
    intraday_market_data_reasons,
)
from us_quant.ibkr_stream import (
    IBKRReadOnlyStream,
    MARKET_DATA_TYPE_NAMES,
    StreamSnapshot,
)
from us_quant.alpaca_stream import (
    AlpacaCredentialsMissing,
    AlpacaIEXStream,
)
from us_quant.finnhub_stream import (
    FinnhubCredentialsMissing,
    FinnhubTradeStream,
)
from us_quant.extended_hours import (
    ibkr_market_data_exchange,
    paper_order_routing,
)
from us_quant.portfolio_view import (
    AccountView,
    PortfolioView,
    build_portfolio_view,
)
from us_quant.scanner import (
    MarketScan,
    load_close_series,
    save_market_scan,
    scan_market,
)
from us_quant.strategy_registry import (
    StrategyRecord,
    StrategyRegistry,
    StrategyRegistryError,
)
from us_quant.runtime_events import RuntimeEventStore
from us_quant.export_service import export_terminal_bundle
from us_quant.shadow_paper import (
    ShadowPaperEngine,
    ShadowPaperStore,
    ShadowSnapshot,
    _with_slippage,
)
from us_quant.targeted_intraday import build_targeted_shadow_config
from us_quant.auto_intraday import (
    build_auto_rotation_config,
    resolve_paper_session_capital,
)
from us_quant.auto_quant import (
    AutoQuantCandidate,
    AutoQuantEngine,
    AutoQuantPreflight,
    AutoQuantSnapshot,
    evaluate_auto_quant_preflight,
)
from us_quant.risk import LayeredRiskLimits
from us_quant.ibkr_paper_orders import (
    IBKRPaperOrderError,
    IBKRPaperOrderUncertainError,
    IBKRPaperOrderService,
    PaperOrderJournal,
    PaperOrderReconciliation,
)
from us_quant.paper_execution_health import (
    PaperExecutionHealth,
    PaperExecutionIssue,
    evaluate_paper_execution_health,
)
from us_quant.minute_data import MinuteQuoteStore
from us_quant.targeted_preflight import (
    TargetPreflightResult,
    evaluate_target_preflight,
)
from us_quant.targeted_replay import (
    TargetedReplayResult,
    load_targeted_replays,
    run_targeted_replay,
    save_targeted_replay,
)
from us_quant.targeted_robustness import (
    TargetedRobustnessResult,
    group_regular_sessions,
    load_targeted_robustness,
    run_targeted_robustness,
    save_targeted_robustness,
)
from us_quant.targeted_validation import (
    TargetedWalkForwardResult,
    load_targeted_walk_forwards,
    run_targeted_walk_forward,
    save_targeted_walk_forward,
)
from us_quant.targeted_overfit import (
    TargetedOverfitResult,
    load_targeted_overfits,
    run_targeted_overfit_diagnostics,
    save_targeted_overfit,
)
from us_quant.targeted_review import (
    TargetedReviewResult,
    load_targeted_reviews,
    run_targeted_review,
    save_targeted_review,
)
from us_quant.targeted_data_quality import (
    TargetedDataQualityResult,
    load_targeted_data_quality,
    run_targeted_data_quality,
    save_targeted_data_quality,
)
from us_quant.targeted_execution_stress import (
    TargetedExecutionStressResult,
    load_targeted_execution_stress,
    run_targeted_execution_stress,
    save_targeted_execution_stress,
)
from us_quant.intraday_universe import (
    select_intraday_watchlist,
    select_paper_rotation_rows,
)
from us_quant.public_history import run_public_history_queue
from us_quant.cross_sectional import (
    run_cross_sectional_research,
    save_cross_sectional_research,
)
from us_quant.executable_research import (
    run_executable_cross_sectional_research,
    save_executable_research,
)
from us_quant.universe import (
    UniverseRefreshCancelled,
    UniverseSnapshot,
    enrich_us_profiles,
    load_universe_snapshot,
    prioritized_research_symbols,
    refresh_official_universe,
)
from us_quant.ui_theme import (
    ThemePalette,
    build_stylesheet,
    theme_palette,
)
from us_quant.user_settings import (
    UserPreferences,
    UserPreferencesStore,
    UserSettingsError,
)
from us_quant.backtest_workspace import (
    STRATEGY_SPECS,
    BacktestRequest,
    BacktestRun,
    run_backtest,
    save_backtest_run,
)
from us_quant.strategy_schema import strategy_schema_summary


APP_TITLE = "美股量化研究台"


def _money(
    value: Decimal | float | int | None,
    *,
    signed: bool = False,
) -> str:
    if value is None:
        return "不可用"
    number = float(value)
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}${number:,.2f}"


def _price(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.4f}".rstrip("0").rstrip(".")


class QuoteTableModel(QAbstractTableModel):
    """Small incremental model for the live quote grid."""

    HEADERS = (
        "代码",
        "Bid",
        "Ask",
        "Last",
        "Close",
        "点差",
        "有效类型",
        "更新时间",
        "Age(s)",
        "代次",
        "来源",
        "覆盖",
        "状态",
        "原因",
    )

    def __init__(self, theme_name: str = "dark") -> None:
        super().__init__()
        self._rows: list[tuple[str, ...]] = []
        self._states: list[tuple[bool, bool]] = []
        self._theme = theme_palette(theme_name)
        self._sort_column = -1
        self._sort_order = Qt.AscendingOrder
        self.reset_count = 0
        self.changed_row_count = 0

    def rowCount(
        self, parent: QModelIndex = QModelIndex()
    ) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(
        self, parent: QModelIndex = QModelIndex()
    ) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.DisplayRole,
    ):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None

    def data(
        self, index: QModelIndex, role: int = Qt.DisplayRole
    ):
        if not index.isValid():
            return None
        value = self._rows[index.row()][index.column()]
        if role in {Qt.DisplayRole, Qt.ToolTipRole}:
            return value
        if role == Qt.ForegroundRole:
            realtime_ready, stale = self._states[index.row()]
            if stale and index.column() in {0, 6, 10, 12, 13}:
                return QColor(self._theme.error)
            if realtime_ready and index.column() in {0, 6, 10, 12}:
                return QColor(self._theme.success)
        return None

    def sort(
        self, column: int, order: Qt.SortOrder = Qt.AscendingOrder
    ) -> None:
        self._sort_column = column
        self._sort_order = order
        self._resort()

    def set_theme(self, theme_name: str) -> None:
        self._theme = theme_palette(theme_name)
        if self._rows:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(
                    len(self._rows) - 1,
                    len(self.HEADERS) - 1,
                ),
                [Qt.ForegroundRole],
            )

    def update_snapshot(self, snapshot: StreamSnapshot) -> None:
        materialized: list[
            tuple[tuple[str, ...], tuple[bool, bool]]
        ] = []
        for quote in snapshot.quotes:
            values = (
                quote.symbol,
                _price(quote.bid),
                _price(quote.ask),
                _price(quote.last),
                _price(quote.close),
                _price(quote.spread),
                MARKET_DATA_TYPE_NAMES.get(
                    quote.effective_market_data_type, "未知"
                ),
                quote.updated_at or "未收到",
                (
                    f"{quote.age_seconds:.1f}"
                    if quote.age_seconds is not None
                    else "—"
                ),
                str(quote.generation),
                quote.provider,
                quote.coverage,
                "READY" if quote.realtime_ready else "STALE",
                quote.stale_reason or "可用于日内观察",
            )
            materialized.append(
                (values, (quote.realtime_ready, quote.stale))
            )
        current_symbols = tuple(row[0] for row in self._rows)
        incoming_symbols = tuple(row[0][0] for row in materialized)
        if set(current_symbols) != set(incoming_symbols):
            self.beginResetModel()
            self._rows = [row for row, _ in materialized]
            self._states = [state for _, state in materialized]
            self.endResetModel()
            self.reset_count += 1
            if self._sort_column >= 0:
                self._resort()
            return
        incoming_by_symbol = {
            row[0]: (row, state) for row, state in materialized
        }
        materialized = [
            incoming_by_symbol[symbol] for symbol in current_symbols
        ]

        changed: list[int] = []
        for index, (row, state) in enumerate(materialized):
            if row != self._rows[index] or state != self._states[index]:
                self._rows[index] = row
                self._states[index] = state
                changed.append(index)
        self.changed_row_count += len(changed)
        for row_index in changed:
            self.dataChanged.emit(
                self.index(row_index, 0),
                self.index(row_index, len(self.HEADERS) - 1),
                [
                    Qt.DisplayRole,
                    Qt.ToolTipRole,
                    Qt.ForegroundRole,
                ],
            )
        if changed and self._sort_column >= 0:
            self._resort()

    def _resort(self) -> None:
        if self._sort_column < 0 or len(self._rows) < 2:
            return
        combined = list(zip(self._rows, self._states))
        column = self._sort_column

        def key(item):  # type: ignore[no-untyped-def]
            value = item[0][column]
            numeric = _sortable_number(value)
            return (
                numeric is None,
                numeric if numeric is not None else value.casefold(),
            )

        self.layoutAboutToBeChanged.emit()
        combined.sort(
            key=key,
            reverse=self._sort_order == Qt.DescendingOrder,
        )
        self._rows = [row for row, _ in combined]
        self._states = [state for _, state in combined]
        self.layoutChanged.emit()


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def configure_chinese_font(application: QApplication) -> None:
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / (
        "Fonts/msyh.ttc"
    )
    if font_path.exists():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            application.setFont(QFont(families[-1], 10))


class TaskThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(str)

    def __init__(
        self,
        task: Callable[[Callable[[str], None]], object],
        resource_group: str = "research",
    ) -> None:
        super().__init__()
        self.task = task
        self.resource_group = resource_group

    def run(self) -> None:
        try:
            result = self.task(self.progress.emit)
        except UniverseRefreshCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(" ".join(str(error).split()))
        else:
            self.succeeded.emit(result)


class StreamWorker(QThread):
    snapshot_ready = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        config,
        *,
        symbols: tuple[str, ...],
        provider: str = "ibkr",
        api_key: str = "",
        api_secret: str = "",
        finnhub_key: str = "",
        market_exchange: str = "SMART",
    ) -> None:
        super().__init__()
        self.provider = provider
        self.symbols = symbols
        self.market_exchange = market_exchange
        if provider == "alpaca_iex":
            self.service = AlpacaIEXStream(
                symbols=symbols,
                api_key=api_key,
                api_secret=api_secret,
                stale_after_seconds=8,
            )
        elif provider == "finnhub_trades":
            self.service = FinnhubTradeStream(
                symbols=symbols,
                api_key=finnhub_key,
                stale_after_seconds=20,
            )
        else:
            is_extended = provider == "ibkr_extended"
            self.service = IBKRReadOnlyStream(
                config,
                symbols=symbols,
                requested_market_data_type=1,
                stale_after_seconds=8,
                market_exchange=market_exchange,
                provider_label=(
                    "IBKR 5×24" if is_extended else "IBKR"
                ),
                coverage=(
                    "IBKR 5×24：盘前/盘后 SMART；隔夜直接 OVERNIGHT；"
                    "实际权限与标的资格以券商回调为准"
                    if is_extended
                    else "由 IBKR 订阅权限决定"
                ),
            )

    def run(self) -> None:
        try:
            self.service.run()
        except Exception as error:
            self.failed.emit(
                f"{type(error).__name__}: {error}"
            )

    def request_stop(self) -> None:
        self.service.stop()


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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.paths = ApplicationPaths.discover()
        self.paths.ensure_state_directories()
        self.paths.seed_research_results()
        self.credential_store = WindowsCredentialStore(
            self.paths.state_root / "credentials"
        )
        self.root = self.paths.resource_root
        self.config_path = self.paths.config_path
        self.data_root = self.paths.user_data_root
        self.bundled_data_root = self.paths.bundled_data_root
        self.reference_root = self.data_root / "reference"
        writable_universe = self.reference_root / "universe.json"
        bundled_universe = (
            self.bundled_data_root / "reference" / "universe.json"
        )
        self.universe_path = (
            writable_universe
            if writable_universe.exists()
            else bundled_universe
        )
        self.queue_path = (
            self.paths.runtime_root / "history_jobs.sqlite3"
        )
        self.scan_path = (
            self.paths.research_results_root / "market_scan.json"
        )
        self.strategy_path = (
            self.paths.research_results_root
            / "cross_sectional_executable_research.json"
        )
        baseline_config = load_config(self.config_path)
        self.preferences_store = UserPreferencesStore(
            self.paths.state_root / "settings" / "preferences.json"
        )
        defaults = UserPreferences(
            ibkr_host=baseline_config.ibkr.host,
            ibkr_port=baseline_config.ibkr.port,
            ibkr_client_id=baseline_config.ibkr.client_id,
            connection_timeout_seconds=(
                baseline_config.ibkr.connection_timeout_seconds
            ),
        )
        self.preferences = self.preferences_store.load(defaults)
        self.current_theme_name = self.preferences.theme
        self.theme = theme_palette(self.current_theme_name)
        self.config = replace(
            baseline_config,
            ibkr=IBKRConnectionConfig(
                host=self.preferences.ibkr_host,
                port=self.preferences.ibkr_port,
                client_id=self.preferences.ibkr_client_id,
                api_read_only=True,
                paper_order_submission_enabled=False,
                connection_timeout_seconds=(
                    self.preferences.connection_timeout_seconds
                ),
            ),
        )
        self.artifact_catalog: ArtifactCatalog = load_artifact_catalog(
            self.paths.research_results_root
        )
        self.account_ledger = AccountLedger(
            self.paths.runtime_root / "account_equity.sqlite3"
        )
        self.strategy_registry = StrategyRegistry(
            self.paths.runtime_root / "strategies.sqlite3"
        )
        self.strategy_registry.seed_defaults()
        self.runtime_events = RuntimeEventStore(
            self.paths.runtime_root / "runtime_events.sqlite3"
        )
        self.shadow_store = ShadowPaperStore(
            self.paths.runtime_root / "shadow_paper.sqlite3"
        )
        self.paper_order_journal = PaperOrderJournal(
            self.paths.runtime_root / "ibkr_paper_orders.sqlite3"
        )
        self.minute_quote_store = MinuteQuoteStore(
            self.paths.runtime_root / "minute_quotes.sqlite3"
        )
        self._minute_recorded_keys: dict[
            tuple[str, str, str], bool
        ] = {}
        self._last_stream_event_key: tuple[int, int] | None = None
        self._quote_last_ready_monotonic: dict[str, float] = {}
        self._last_stream_status_key: tuple[object, ...] | None = None
        self._last_stream_status_log_at = 0.0
        self._quotes_scroll_active = False
        self._pending_stream_snapshot: StreamSnapshot | None = None
        self.portfolio_view: PortfolioView | None = None
        self.stream_worker: StreamWorker | None = None
        self._pending_stream_switch: (
            tuple[str, tuple[str, ...]] | None
        ) = None
        self.stream_snapshot: StreamSnapshot | None = None
        self.shadow_engine: ShadowPaperEngine | None = None
        self.shadow_snapshot: ShadowSnapshot | None = None
        self.target_preflight_result: TargetPreflightResult | None = None
        self.paper_order_service: IBKRPaperOrderService | None = None
        self.auto_quant_engine: AutoQuantEngine | None = None
        self.auto_quant_snapshot: AutoQuantSnapshot | None = None
        self.paper_execution_health: PaperExecutionHealth | None = None
        self.auto_quant_candidates: tuple[
            AutoQuantCandidate, ...
        ] = ()
        self.targeted_replay_results: list[
            TargetedReplayResult
        ] = []
        self.targeted_robustness_results: list[
            TargetedRobustnessResult
        ] = []
        self.targeted_walk_forward_results: list[
            TargetedWalkForwardResult
        ] = []
        self.targeted_overfit_results: list[
            TargetedOverfitResult
        ] = []
        self.targeted_review_results: list[
            TargetedReviewResult
        ] = []
        self.targeted_data_quality_results: list[
            TargetedDataQualityResult
        ] = []
        self.targeted_execution_stress_results: list[
            TargetedExecutionStressResult
        ] = []
        self.universe: UniverseSnapshot | None = None
        self.scan: MarketScan | None = None
        self.strategy_report: dict | None = None
        self.workers: list[TaskThread] = []
        self.universe_refresh_cancel_event: Event | None = None
        self.universe_refresh_worker: TaskThread | None = None

        self.setWindowTitle(APP_TITLE)
        self.resize(1440, 900)
        self.setMinimumSize(1120, 720)
        self._build_ui()
        self._finalize_layout_behavior()
        self._apply_style()
        self._load_local_state()

    def _build_ui(self) -> None:
        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(16, 12, 16, 14)
        root_layout.setSpacing(10)

        header = QVBoxLayout()
        header.setSpacing(8)
        title_box = QVBoxLayout()
        title = QLabel(APP_TITLE)
        title.setObjectName("appTitle")
        subtitle = QLabel(
            "广域市场扫描 · 龙头优先 · 整股约束 · IBKR Paper 分层安全门"
        )
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        self.gateway_badge = QLabel("端口 · 未检查")
        self.gateway_badge.setObjectName("statusBadge")
        self.handshake_badge = QLabel("协议 · 未握手")
        self.handshake_badge.setObjectName("statusBadge")
        self.account_badge = QLabel("账户 · 未验证")
        self.account_badge.setObjectName("statusBadge")
        self.market_badge = QLabel("行情 · 未订阅")
        self.market_badge.setObjectName("statusBadge")
        self.safety_badge = QLabel(
            (
                "Paper下单能力 · 未武装"
                if self.preferences.paper_order_capability_enabled
                else "只读 · 自动下单关闭"
            )
        )
        self.safety_badge.setObjectName("safetyBadge")
        status_grid = QGridLayout()
        status_grid.setHorizontalSpacing(8)
        status_grid.setVerticalSpacing(6)
        for column, badge in enumerate(
            (
                self.gateway_badge,
                self.handshake_badge,
                self.account_badge,
                self.market_badge,
                self.safety_badge,
            )
        ):
            badge.setAlignment(Qt.AlignCenter)
            badge.setWordWrap(True)
            badge.setSizePolicy(
                QSizePolicy.Expanding,
                QSizePolicy.Preferred,
            )
            badge.setMinimumWidth(0)
            status_grid.addWidget(badge, 0, column)
        for column in range(5):
            status_grid.setColumnStretch(column, 1)
        header.addLayout(status_grid)
        root_layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.North)
        self.tabs.setDocumentMode(True)
        self.monitor_tabs = self._workspace_tabs(
            (
                ("总览", self._dashboard_tab()),
                ("自动量化", self._auto_quant_tab()),
                ("账户与持仓", self._account_tab()),
                ("行情监控", self._quotes_tab()),
                ("针对性日内 T", self._simulation_tab()),
            )
        )
        self.strategy_tabs = self._workspace_tabs(
            (
                ("策略目录与版本", self._strategy_manager_tab()),
                ("回测工作区", self._backtest_tab()),
                ("横截面研究", self._strategy_tab()),
            )
        )
        self.research_tabs = self._workspace_tabs(
            (
                ("广域标的池", self._universe_tab()),
                ("市场扫描", self._scanner_tab()),
                ("数据任务", self._data_tab()),
            )
        )
        self.operations_tabs = self._workspace_tabs(
            (
                ("运行事件", self._runtime_tab()),
                ("风险与权限", self._safety_tab()),
            )
        )
        self.tabs.addTab(self.monitor_tabs, "监控台")
        self.tabs.addTab(self.strategy_tabs, "策略与回测")
        self.tabs.addTab(self.research_tabs, "市场研究")
        self.tabs.addTab(self.operations_tabs, "运维与安全")
        self.tabs.addTab(self._settings_tab(), "系统设置")
        root_layout.addWidget(self.tabs)

        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("footer")
        self.status_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse
        )
        root_layout.addWidget(self.status_label)
        self.setCentralWidget(central)
        self.stream_timer = QTimer(self)
        self.stream_timer.setInterval(500)
        self.stream_timer.timeout.connect(
            self._poll_stream_snapshot
        )
        self.extended_session_timer = QTimer(self)
        self.extended_session_timer.setInterval(15_000)
        self.extended_session_timer.timeout.connect(
            self._maybe_rotate_extended_ibkr_session
        )
        self.extended_session_timer.start()
        self._refresh_extended_hours_status()

    @staticmethod
    def _workspace_tabs(
        pages: tuple[tuple[str, QWidget], ...],
    ) -> QTabWidget:
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.setTabPosition(QTabWidget.North)
        for title, page in pages:
            tabs.addTab(page, title)
        return tabs

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    @staticmethod
    def _configure_combo_width(
        combo: QComboBox,
        *,
        minimum_width: int,
        minimum_contents: int,
    ) -> None:
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

    def _finalize_layout_behavior(self) -> None:
        for splitter in self.findChildren(QSplitter):
            splitter.setOpaqueResize(False)
            splitter.setChildrenCollapsible(False)
            splitter.setHandleWidth(6)
        for label in self.findChildren(QLabel):
            if label.objectName() in {
                "subtitle",
                "emptyState",
                "metricNote",
            }:
                label.setWordWrap(True)

    def _auto_quant_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        cards = QHBoxLayout()
        self.auto_status_card = MetricCard(
            "自动量化", "未启动", "IBKR Paper 订单"
        )
        self.auto_equity_card = MetricCard(
            "模拟账户净值", "—", "以 IBKR Paper 为准"
        )
        self.auto_realized_card = MetricCard(
            "当日已实现", "—", "券商 P&L 优先"
        )
        self.auto_unrealized_card = MetricCard(
            "未实现盈亏", "—", "券商 P&L 优先"
        )
        self.auto_position_card = MetricCard(
            "当前持仓", "0", "当前研究版最多一只"
        )
        for card in (
            self.auto_status_card,
            self.auto_equity_card,
            self.auto_realized_card,
            self.auto_unrealized_card,
            self.auto_position_card,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        health_cards = QHBoxLayout()
        self.auto_health_status_card = MetricCard(
            "执行健康", "未评估", "HEALTHY / WAITING / HALT"
        )
        self.auto_health_broker_card = MetricCard(
            "券商持仓/本地持仓", "0/0", "以 IBKR Paper 为准"
        )
        self.auto_health_pending_card = MetricCard(
            "在途订单", "0", "本地 pending + 券商开放订单"
        )
        self.auto_health_unreconciled_card = MetricCard(
            "未对账订单", "0", "终态但成交未对齐或仍缺状态"
        )
        self.auto_health_latency_card = MetricCard(
            "提交延迟", "—", "intent 生成 → placeOrder 耗时"
        )
        for card in (
            self.auto_health_status_card,
            self.auto_health_broker_card,
            self.auto_health_pending_card,
            self.auto_health_unreconciled_card,
            self.auto_health_latency_card,
        ):
            health_cards.addWidget(card)
        layout.addLayout(health_cards)

        action_panel = QFrame()
        action_panel.setObjectName("panel")
        action_panel.setMinimumHeight(230)
        action_layout = QVBoxLayout(action_panel)
        self.auto_pipeline_label = QLabel(
            "自动 Paper：① 全市场扫描→实时短名单　② 确认启动　"
            "③ 可暂停新开仓或停止并平仓"
        )
        self.auto_pipeline_label.setObjectName("sectionTitle")
        self.auto_pipeline_label.setWordWrap(True)
        self.auto_pipeline_label.setToolTip(
            "完整保护链：广域扫描 → 非中概与龙头门 → 实时信号 → 风控 → "
            "IBKR Paper DAY 限价单 → 券商成交与持仓对账。"
        )
        self.auto_session_label = QLabel()
        self.auto_session_label.setObjectName("subtitle")
        self.auto_session_label.setWordWrap(True)
        self.auto_scope_label = QLabel(
            "市场范围：等待载入官方标的池和最近扫描。"
        )
        self.auto_scope_label.setObjectName("subtitle")
        self.auto_scope_label.setWordWrap(True)
        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(6)
        self.auto_strategy_combo = QComboBox()
        self._configure_combo_width(
            self.auto_strategy_combo,
            minimum_width=485,
            minimum_contents=30,
        )
        self.auto_strategy_combo.currentIndexChanged.connect(
            self._refresh_auto_quant_preflight
        )
        self.auto_candidate_limit = QSpinBox()
        self.auto_candidate_limit.setRange(3, 30)
        self.auto_candidate_limit.setValue(20)
        self.auto_candidate_limit.setMinimumWidth(115)
        self.auto_candidate_limit.valueChanged.connect(
            self._refresh_auto_quant_preflight
        )
        self.auto_capital_limit = QSpinBox()
        self.auto_capital_limit.setRange(0, 100_000_000)
        self.auto_capital_limit.setSpecialValueText(
            "使用 Paper 可用现金"
        )
        self.auto_capital_limit.setPrefix("$")
        self.auto_capital_limit.setValue(0)
        self.auto_capital_limit.setMinimumWidth(250)
        self.auto_capital_limit.setToolTip(
            "0 表示使用 IBKR Paper 净值与现金中的较小值；"
            "填写金额可限制本次策略使用的模拟资金，不修改券商账户。"
        )
        self.auto_capital_limit.valueChanged.connect(
            self._refresh_auto_quant_preflight
        )
        self.auto_prepare_button = QPushButton(
            "第 1 步：全市场扫描并准备行情"
        )
        self.auto_prepare_button.clicked.connect(
            self._prepare_auto_quant_candidates
        )
        self.auto_arm_confirm = QCheckBox(
            "仅供启动弹窗写入的 Paper 确认"
        )
        self.auto_arm_confirm.setVisible(False)
        self.auto_arm_confirm.toggled.connect(
            self._refresh_auto_quant_preflight
        )
        self.auto_channel_check_button = QPushButton(
            "可选：测试 Paper 通道（不下单）"
        )
        self.auto_channel_check_button.clicked.connect(
            self._check_auto_order_channel
        )
        self.auto_start_button = QPushButton(
            "第 2 步：开始 Paper 模拟下单"
        )
        self.auto_start_button.clicked.connect(
            self._confirm_and_start_auto_quant
        )
        self.auto_stop_stream_button = QPushButton("停止当前行情")
        self.auto_stop_stream_button.setToolTip(
            "停止当前只读行情。第 1 步切换候选时会自动安全停止旧行情，通常不需要手动点击。"
        )
        self.auto_stop_stream_button.clicked.connect(
            self._stop_auto_market_data
        )
        self.auto_stop_stream_button.setEnabled(False)
        self.auto_pause_button = QPushButton("暂停新开仓（保留持仓）")
        self.auto_pause_button.setToolTip(
            "禁止新的买入意图并撤销未成交买单；已有持仓继续执行止损、"
            "止盈和时段退出。"
        )
        self.auto_pause_button.clicked.connect(
            self._pause_auto_quant_entries
        )
        self.auto_pause_button.setEnabled(False)
        self.auto_resume_button = QPushButton("恢复新开仓")
        self.auto_resume_button.clicked.connect(
            self._resume_auto_quant_entries
        )
        self.auto_resume_button.setEnabled(False)
        self.auto_stop_button = QPushButton("停止会话并请求平仓")
        self.auto_stop_button.setToolTip(
            "撤销未成交买单，并按实时行情为已有 Paper 持仓提交限价卖出。"
        )
        self.auto_stop_button.clicked.connect(
            self._stop_auto_quant
        )
        self.auto_stop_button.setEnabled(False)
        self.auto_resume_from_reconciliation_button = QPushButton(
            "恢复会话（对账后人工复核）"
        )
        self.auto_resume_from_reconciliation_button.setToolTip(
            "仅用于对账完成后恢复已停机会话；"
            "不会自动重新下单，需要人工确认当前持仓与订单状态。"
        )
        self.auto_resume_from_reconciliation_button.clicked.connect(
            self._resume_auto_quant_from_reconciliation
        )
        self.auto_resume_from_reconciliation_button.setEnabled(False)
        controls.addWidget(
            self._field_label("策略版本"),
            0,
            0,
        )
        controls.addWidget(
            self._field_label("实时轮动候选上限"),
            0,
            1,
        )
        controls.addWidget(
            self._field_label("会话资金上限"),
            0,
            2,
        )
        controls.addWidget(
            self._field_label("候选与行情"),
            0,
            3,
        )
        controls.addWidget(self.auto_strategy_combo, 1, 0)
        controls.addWidget(self.auto_candidate_limit, 1, 1)
        controls.addWidget(self.auto_capital_limit, 1, 2)
        controls.addWidget(self.auto_prepare_button, 1, 3)
        controls.setColumnStretch(0, 4)
        controls.setColumnStretch(1, 1)
        controls.setColumnStretch(2, 2)
        controls.setColumnStretch(3, 2)

        session_actions = QHBoxLayout()
        session_actions.setSpacing(10)
        session_actions.addWidget(self.auto_stop_stream_button)
        session_actions.addWidget(self.auto_channel_check_button)
        session_actions.addWidget(self.auto_start_button)
        risk_actions = QHBoxLayout()
        risk_actions.setSpacing(10)
        risk_actions.addWidget(self.auto_pause_button)
        risk_actions.addWidget(self.auto_resume_button)
        risk_actions.addWidget(self.auto_resume_from_reconciliation_button)
        risk_actions.addWidget(self.auto_stop_button)
        self.auto_summary_label = QLabel(
            "第 1 步会重新扫描全部非中概研究池，再从有合格历史数据的标的中"
            "选出最多 30 个实时候选；第 2 步启动 Paper。运行中可只暂停新开仓，"
            "不会强制卖出现有持仓。"
        )
        self.auto_summary_label.setObjectName("subtitle")
        self.auto_summary_label.setWordWrap(True)
        self.auto_preflight_label = QLabel()
        self.auto_preflight_label.setObjectName("emptyState")
        self.auto_preflight_label.setWordWrap(True)
        action_layout.addWidget(self.auto_pipeline_label)
        action_layout.addWidget(self.auto_session_label)
        action_layout.addWidget(self.auto_scope_label)
        action_layout.addLayout(controls)
        action_layout.addLayout(session_actions)
        action_layout.addLayout(risk_actions)
        action_layout.addWidget(self.auto_summary_label)
        action_layout.addWidget(self.auto_preflight_label)
        layout.addWidget(action_panel)

        self.auto_detail_tabs = QTabWidget()
        self.auto_detail_tabs.setDocumentMode(True)
        self.auto_detail_tabs.setMinimumHeight(130)

        portfolio_page = QWidget()
        portfolio_layout = QVBoxLayout(portfolio_page)
        self.auto_position_table = QTableWidget(0, 7)
        self.auto_position_table.setHorizontalHeaderLabels(
            [
                "代码",
                "整股",
                "成交均价",
                "最新估值",
                "未实现P&L",
                "持仓时间",
                "来源",
            ]
        )
        self._configure_table(self.auto_position_table)
        self.auto_recent_fill_table = QTableWidget(0, 7)
        self.auto_recent_fill_table.setHorizontalHeaderLabels(
            [
                "时间",
                "代码",
                "方向",
                "整股",
                "Paper成交价",
                "估算费用",
                "本笔已实现",
            ]
        )
        self._configure_table(self.auto_recent_fill_table)
        portfolio_layout.addWidget(QLabel("当前组合"))
        portfolio_layout.addWidget(self.auto_position_table)
        portfolio_layout.addWidget(QLabel("最近成交"))
        portfolio_layout.addWidget(self.auto_recent_fill_table)
        self.auto_detail_tabs.addTab(portfolio_page, "组合与盈亏")

        shadow_page = QWidget()
        shadow_layout = QVBoxLayout(shadow_page)
        shadow_title = QLabel("影子执行带")
        shadow_title.setObjectName("sectionTitle")
        shadow_layout.addWidget(shadow_title)
        shadow_note = QLabel(
            "这里只展示研究态影子限价带：ask+slippage、bid−slippage 与当前 "
            "策略 limit_price 的相对位置。Paper 真实订单仍以本地 intent 与券商 "
            "逐笔成交对账为准。"
        )
        shadow_note.setObjectName("subtitle")
        shadow_note.setWordWrap(True)
        shadow_layout.addWidget(shadow_note)
        self.auto_shadow_table = QTableWidget(0, 7)
        self.auto_shadow_table.setHorizontalHeaderLabels(
            [
                "代码",
                "Bid",
                "Ask",
                "影子买价",
                "影子卖价",
                "策略限价",
                "状态",
            ]
        )
        self._configure_table(self.auto_shadow_table)
        shadow_layout.addWidget(self.auto_shadow_table)
        self.auto_detail_tabs.addTab(shadow_page, "影子执行带")

        latency_page = QWidget()
        latency_layout = QVBoxLayout(latency_page)
        latency_title = QLabel("提交延迟观测")
        latency_title.setObjectName("sectionTitle")
        latency_layout.addWidget(latency_title)
        latency_note = QLabel(
            "展示最近订单从本地生成到 IBKR Paper placeOrder 的延迟分布；"
            "用于识别网络/API 抖动与重试策略效果。"
        )
        latency_note.setObjectName("subtitle")
        latency_note.setWordWrap(True)
        latency_layout.addWidget(latency_note)
        self.auto_latency_table = QTableWidget(0, 5)
        self.auto_latency_table.setHorizontalHeaderLabels(
            [
                "intent_id",
                "symbol",
                "side",
                "提交延迟 ms",
                "生成时间",
            ]
        )
        self._configure_table(self.auto_latency_table)
        latency_layout.addWidget(self.auto_latency_table)
        self.auto_detail_tabs.addTab(latency_page, "提交延迟")

        candidates_page = QWidget()
        candidates_layout = QVBoxLayout(candidates_page)
        self.auto_candidate_table = QTableWidget(0, 7)
        self.auto_candidate_table.setHorizontalHeaderLabels(
            [
                "代码",
                "名称",
                "板块",
                "层级",
                "扫描分",
                "日线信号",
                "实时状态",
            ]
        )
        self._configure_table(self.auto_candidate_table)
        candidates_layout.addWidget(self.auto_candidate_table)
        self.auto_detail_tabs.addTab(candidates_page, "候选与信号")

        orders_page = QWidget()
        orders_layout = QVBoxLayout(orders_page)
        order_note = QLabel(
            "这里只显示整理后的会话订单。原始 IBKR 回调写入本地审计库，"
            "Live、市场单、碎股、做空、全局撤单和期权接口均不存在。"
        )
        order_note.setObjectName("subtitle")
        self.auto_execution_health_label = QLabel(
            "执行对账：未连接。券商状态、逐笔成交和本地持仓将在这里汇总。"
        )
        self.auto_execution_health_label.setObjectName("subtitle")
        self.auto_execution_health_label.setWordWrap(True)
        self.auto_reconcile_button = QPushButton(
            "断线后重新连接对账"
        )
        self.auto_reconcile_button.setEnabled(False)
        self.auto_reconcile_button.clicked.connect(
            self._reconnect_auto_order_service
        )
        self.auto_order_table = QTableWidget(0, 7)
        self.auto_order_table.setHorizontalHeaderLabels(
            [
                "状态",
                "代码",
                "方向",
                "订单/成交",
                "限价",
                "对账说明",
                "Order",
            ]
        )
        self._configure_table(self.auto_order_table)
        orders_layout.addWidget(order_note)
        orders_layout.addWidget(self.auto_execution_health_label)
        orders_layout.addWidget(
            self.auto_reconcile_button,
            alignment=Qt.AlignLeft,
        )
        orders_layout.addWidget(self.auto_order_table)
        self.auto_detail_tabs.addTab(orders_page, "Paper订单")
        layout.addWidget(self.auto_detail_tabs)
        return page

    def _account_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        cards = QHBoxLayout()
        self.account_nlv_card = MetricCard(
            "IBKR 模拟账户净值", "—", "尚未读取"
        )
        self.research_budget_card = MetricCard(
            "历史研究资金情景", "$1,500", "可调整；不是账户真值"
        )
        self.account_day_pnl_card = MetricCard(
            "当日盈亏", "不可用", "IBKR reqPnL"
        )
        self.account_unrealized_card = MetricCard(
            "未实现盈亏", "不可用", "IBKR reqPnL"
        )
        self.account_cash_card = MetricCard(
            "现金", "—", "券商账户摘要"
        )
        for card in (
            self.account_nlv_card,
            self.research_budget_card,
            self.account_day_pnl_card,
            self.account_unrealized_card,
            self.account_cash_card,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        self.shadow_gate_label = QLabel(
            "策略证据门：硬阻断。必须先在“策略·版本”中绑定"
            "独立验证且状态为 Paper Shadow 的版本。"
        )
        self.shadow_gate_label.setObjectName("emptyState")
        self.shadow_gate_label.setWordWrap(True)
        layout.addWidget(self.shadow_gate_label)

        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(6)
        refresh_button = QPushButton("只读刷新账户 / 持仓 / P&L")
        refresh_button.clicked.connect(self._refresh_account_snapshot)
        self.account_status_label = QLabel(
            "尚未读取 IBKR；账户号只显示脱敏别名"
        )
        self.account_status_label.setObjectName("subtitle")
        controls.addWidget(refresh_button, 0, 0)
        controls.addWidget(self.account_status_label, 0, 1)
        controls.setColumnStretch(1, 1)
        layout.addLayout(controls)

        split = QSplitter(Qt.Vertical)
        position_panel = QFrame()
        position_panel.setObjectName("panel")
        position_layout = QVBoxLayout(position_panel)
        position_header = QVBoxLayout()
        position_header.setSpacing(2)
        position_title = QLabel("券商持仓")
        position_title.setObjectName("sectionTitle")
        self.positions_empty_label = QLabel(
            "尚未读取持仓；读取后若为空，会明确显示“当前账户无持仓”"
        )
        self.positions_empty_label.setObjectName("subtitle")
        position_header.addWidget(position_title)
        position_header.addWidget(self.positions_empty_label)
        position_layout.addLayout(position_header)
        self.positions_table = QTableWidget(0, 13)
        self.positions_table.setHorizontalHeaderLabels(
            [
                "代码",
                "数量",
                "均价",
                "最新价",
                "市值",
                "风险敞口",
                "当日P&L",
                "未实现P&L",
                "已实现P&L",
                "行情类型",
                "Mark来源",
                "状态",
                "账户",
            ]
        )
        self._configure_table(self.positions_table)
        position_layout.addWidget(self.positions_table)
        split.addWidget(position_panel)

        ledger_panel = QFrame()
        ledger_panel.setObjectName("panel")
        ledger_layout = QVBoxLayout(ledger_panel)
        ledger_header = QVBoxLayout()
        ledger_header.setSpacing(2)
        ledger_title = QLabel("账户权益账本（Paper 与 Live 永久隔离）")
        ledger_title.setObjectName("sectionTitle")
        self.account_detail_label = QLabel(
            "净值、现金和三类 P&L 均保留来源与采集时间"
        )
        self.account_detail_label.setObjectName("subtitle")
        ledger_header.addWidget(ledger_title)
        ledger_header.addWidget(self.account_detail_label)
        ledger_layout.addLayout(ledger_header)
        self.account_ledger_table = QTableWidget(0, 7)
        self.account_ledger_table.setHorizontalHeaderLabels(
            [
                "时间",
                "环境",
                "账户",
                "净值",
                "现金",
                "当日P&L",
                "未实现P&L",
            ]
        )
        self._configure_table(self.account_ledger_table)
        ledger_layout.addWidget(self.account_ledger_table)
        split.addWidget(ledger_panel)
        split.setSizes([420, 220])
        layout.addWidget(split)
        return page

    def _quotes_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        cards = QHBoxLayout()
        self.stream_connection_card = MetricCard(
            "行情流连接", "未启动", "外部或 IBKR 独立只读 client"
        )
        self.stream_feed_card = MetricCard(
            "行情类型", "未知", "以 marketDataType 回调为准"
        )
        self.stream_ready_card = MetricCard(
            "日内可用", "否", "必须 fresh Type 1 + bid/ask"
        )
        self.stream_watch_card = MetricCard(
            "实时订阅子集", "0", "最多 30；不等于全市场研究池"
        )
        for card in (
            self.stream_connection_card,
            self.stream_feed_card,
            self.stream_ready_card,
            self.stream_watch_card,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(8)
        self.stream_symbols = QLineEdit()
        self.stream_symbols.setPlaceholderText(
            "实时订阅子集（最多 30；不是研究池或交易白名单）"
        )
        self.stream_symbols.setClearButtonEnabled(True)
        self.stream_mode = QComboBox()
        self.stream_mode.addItem(
            "Alpaca IEX 免费实时（单交易所）",
            "alpaca_iex",
        )
        self.stream_mode.addItem(
            "Finnhub 实时成交（模拟执行带）",
            "finnhub_trades",
        )
        self.stream_mode.addItem(
            "IBKR 实时优先 / 延迟回退",
            "ibkr",
        )
        self.stream_mode.addItem(
            "IBKR 5×24（盘前 / 盘后 / 隔夜）",
            "ibkr_extended",
        )
        preferred_mode = self.stream_mode.findData(
            self.preferences.market_provider
        )
        self.stream_mode.setCurrentIndex(max(0, preferred_mode))
        self.stream_mode.currentIndexChanged.connect(
            self._stream_provider_selected
        )
        self._configure_combo_width(
            self.stream_mode,
            minimum_width=320,
            minimum_contents=24,
        )
        self.stream_start_button = QPushButton("启动只读流行情")
        self.stream_start_button.clicked.connect(self._start_stream)
        self.stream_stop_button = QPushButton("停止")
        self.stream_stop_button.clicked.connect(self._stop_stream)
        self.stream_stop_button.setEnabled(False)
        self.stream_scan_watchlist_button = QPushButton(
            "载入扫描候选（最多 30）"
        )
        self.stream_scan_watchlist_button.clicked.connect(
            self._apply_intraday_watchlist
        )
        controls.addWidget(
            self._field_label("实时行情订阅子集"),
            0,
            0,
            1,
            4,
        )
        controls.addWidget(self.stream_symbols, 1, 0, 1, 4)
        controls.addWidget(
            self._field_label("行情数据源"),
            2,
            0,
        )
        controls.addWidget(self.stream_mode, 3, 0)
        controls.addWidget(self.stream_scan_watchlist_button, 3, 1)
        controls.addWidget(self.stream_start_button, 3, 2)
        controls.addWidget(self.stream_stop_button, 3, 3)
        controls.setRowMinimumHeight(3, 40)
        controls.setColumnStretch(0, 4)
        controls.setColumnStretch(1, 2)
        controls.setColumnStretch(2, 2)
        controls.setColumnStretch(3, 1)
        layout.addLayout(controls)

        self.stream_scope_label = QLabel()
        self.stream_scope_label.setObjectName("emptyState")
        self.stream_scope_label.setWordWrap(True)
        self.stream_scope_label.setMinimumHeight(40)
        layout.addWidget(self.stream_scope_label)

        credential_note = QLabel(
            "研究池、历史扫描和实时订阅是三层范围：上方代码只控制本次"
            " Level I 行情连接，不会限制广域研究或自动候选生成。"
            "API 凭据与默认连接参数已移至“系统·设置”；"
            "Alpaca=IEX盘口，Finnhub=实时成交+明确模拟带，均非SIP/NBBO。"
        )
        credential_note.setObjectName("subtitle")
        credential_note.setWordWrap(True)
        layout.addWidget(credential_note)

        self.stream_empty_label = QLabel(
            "尚未启动行情。选择数据源并在设置页保存凭据后启动；"
            "表格会明确区分 READY、STALE、延迟和模拟执行带。"
        )
        self.stream_empty_label.setObjectName("emptyState")
        self.stream_empty_label.setAlignment(Qt.AlignCenter)
        self.stream_empty_label.setMinimumHeight(48)
        layout.addWidget(self.stream_empty_label)

        splitter = QSplitter(Qt.Vertical)
        self.quotes_model = QuoteTableModel(self.current_theme_name)
        self.quotes_table = QTableView()
        self.quotes_table.setModel(self.quotes_model)
        self.quotes_table.setAlternatingRowColors(True)
        self.quotes_table.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )
        self.quotes_table.setSelectionMode(
            QAbstractItemView.SingleSelection
        )
        self.quotes_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers
        )
        self.quotes_table.verticalHeader().setVisible(False)
        self.quotes_table.setSortingEnabled(True)
        self.quotes_table.setHorizontalScrollMode(
            QAbstractItemView.ScrollPerPixel
        )
        self.quotes_table.setVerticalScrollMode(
            QAbstractItemView.ScrollPerPixel
        )
        header = self.quotes_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        widths = (
            82, 96, 96, 96, 96, 96, 112,
            168, 78, 70, 96, 420, 92, 360,
        )
        for column, width in enumerate(widths):
            self.quotes_table.setColumnWidth(column, width)
        for scrollbar in (
            self.quotes_table.horizontalScrollBar(),
            self.quotes_table.verticalScrollBar(),
        ):
            scrollbar.sliderPressed.connect(
                self._quotes_scroll_started
            )
            scrollbar.sliderReleased.connect(
                self._quotes_scroll_finished
            )
        splitter.addWidget(self.quotes_table)

        health_panel = QFrame()
        health_panel.setObjectName("panel")
        health_layout = QVBoxLayout(health_panel)
        health_title = QLabel("运行健康与安全门")
        health_title.setObjectName("sectionTitle")
        self.stream_health_text = QTextEdit()
        self.stream_health_text.setReadOnly(True)
        self.stream_health_text.setPlainText(
            "• 行情类型只相信 IBKR marketDataType 回调\n"
            "• 外部首选 Alpaca IEX 免费实时，明确标注单交易所覆盖\n"
            "• Finnhub 是实时成交；±5bps 影子带不是市场 bid/ask\n"
            "• Type 2/3/4 只可观察，不进入日内信号\n"
            "• 10197、1100、1300、缺 bid/ask、超时均硬性 stale\n"
            "• 客户端硬禁下单、撤单、全撤、行权和 FA 修改\n"
            "• 当前未启动流服务"
        )
        health_layout.addWidget(health_title)
        health_layout.addWidget(self.stream_health_text)
        splitter.addWidget(health_panel)
        splitter.setSizes([480, 180])
        layout.addWidget(splitter)
        return page

    def _simulation_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        cards = QHBoxLayout()
        self.shadow_status_card = MetricCard(
            "内部策略仿真", "未启动", "不进入 IBKR 模拟账户"
        )
        self.shadow_equity_card = MetricCard(
            "影子净值", "—", "启动时读取 IBKR Paper 净值"
        )
        self.shadow_realized_card = MetricCard(
            "已实现盈亏", "$0.00", "已扣双边模拟佣金"
        )
        self.shadow_unrealized_card = MetricCard(
            "未实现盈亏", "$0.00", "按最新有效 mark"
        )
        self.shadow_trade_card = MetricCard(
            "完成交易", "0 / 4", "整股；最多一笔持仓"
        )
        for card in (
            self.shadow_status_card,
            self.shadow_equity_card,
            self.shadow_realized_card,
            self.shadow_unrealized_card,
            self.shadow_trade_card,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(6)
        note = QLabel(
            "使用“实时行情”页的 fresh bid/ask；启动时读取 IBKR "
            "Paper 净值、10%单仓、整股、$0.35/单模拟佣金、2bps滑点；"
            "不会发送券商订单"
        )
        note.setObjectName("subtitle")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.shadow_strategy_combo = QComboBox()
        self._configure_combo_width(
            self.shadow_strategy_combo,
            minimum_width=360,
            minimum_contents=24,
        )
        self.shadow_strategy_combo.setToolTip(
            "选择驱动本次实时影子会话的不可变策略版本"
        )
        self.shadow_strategy_combo.currentIndexChanged.connect(
            self._refresh_target_preflight
        )
        self.target_symbol_input = QLineEdit()
        self.target_symbol_input.setPlaceholderText(
            "输入本次做 T 的美股或 ETF 代码"
        )
        self.target_symbol_input.setMaxLength(10)
        self.target_symbol_input.setMinimumWidth(220)
        self.target_symbol_input.setClearButtonEnabled(True)
        self.target_symbol_input.returnPressed.connect(
            self._apply_target_symbol
        )
        self.target_symbol_apply_button = QPushButton("应用标的")
        self.target_symbol_apply_button.clicked.connect(
            self._apply_target_symbol
        )
        self.target_symbol_subscribe_button = QPushButton(
            "订阅该标的行情"
        )
        self.target_symbol_subscribe_button.clicked.connect(
            self._sync_targeted_symbol_to_stream
        )
        self.target_symbol_status = QLabel("未指定")
        self.target_symbol_status.setObjectName("subtitle")
        self.shadow_start_button = QPushButton("启动内部仿真")
        self.shadow_start_button.clicked.connect(self._start_shadow)
        self.shadow_stop_button = QPushButton("停止内部仿真")
        self.shadow_stop_button.clicked.connect(self._stop_shadow)
        self.shadow_stop_button.setEnabled(False)
        self.targeted_replay_button = QPushButton("回放已录分钟数据")
        self.targeted_replay_button.clicked.connect(
            self._run_targeted_replay
        )
        self.targeted_robustness_button = QPushButton(
            "多日稳健性评估"
        )
        self.targeted_robustness_button.clicked.connect(
            self._run_targeted_robustness
        )
        controls.addWidget(
            self._field_label("策略版本"),
            0,
            0,
        )
        controls.addWidget(
            self._field_label("本次指定标的"),
            0,
            1,
        )
        controls.addWidget(
            self._field_label("标的与行情"),
            0,
            2,
            1,
            2,
        )
        controls.addWidget(self.shadow_strategy_combo, 1, 0)
        controls.addWidget(self.target_symbol_input, 1, 1)
        controls.addWidget(self.target_symbol_apply_button, 1, 2)
        controls.addWidget(
            self.target_symbol_subscribe_button,
            1,
            3,
        )
        controls.setColumnStretch(0, 4)
        controls.setColumnStretch(1, 2)
        controls.setColumnStretch(2, 1)
        controls.setColumnStretch(3, 2)

        simulation_actions = QHBoxLayout()
        simulation_actions.setSpacing(10)
        simulation_actions.addWidget(self.shadow_start_button)
        simulation_actions.addWidget(self.shadow_stop_button)
        simulation_actions.addStretch()
        simulation_actions.addWidget(self.targeted_replay_button)
        simulation_actions.addWidget(
            self.targeted_robustness_button
        )
        layout.addLayout(controls)
        layout.addLayout(simulation_actions)
        layout.addWidget(self.target_symbol_status)
        self.minute_data_status = QLabel(
            "分钟证据：输入代码后显示本地已录数据；只回放 fresh bid/ask。"
        )
        self.minute_data_status.setObjectName("subtitle")
        layout.addWidget(self.minute_data_status)

        split = QSplitter(Qt.Vertical)
        position_panel = QFrame()
        position_panel.setObjectName("panel")
        position_layout = QVBoxLayout(position_panel)
        position_title = QLabel("当前内部仿真持仓")
        position_title.setObjectName("sectionTitle")
        self.shadow_position_table = QTableWidget(0, 8)
        self.shadow_position_table.setHorizontalHeaderLabels(
            [
                "代码",
                "整股数量",
                "入场价",
                "入场时间",
                "最高价",
                "行情来源",
                "覆盖",
                "环境",
            ]
        )
        self._configure_table(self.shadow_position_table)
        position_layout.addWidget(position_title)
        position_layout.addWidget(self.shadow_position_table)
        split.addWidget(position_panel)

        fill_panel = QFrame()
        fill_panel.setObjectName("panel")
        fill_layout = QVBoxLayout(fill_panel)
        fill_title = QLabel("内部仿真成交与净盈亏")
        fill_title.setObjectName("sectionTitle")
        self.shadow_fill_table = QTableWidget(0, 11)
        self.shadow_fill_table.setHorizontalHeaderLabels(
            [
                "时间",
                "代码",
                "方向",
                "数量",
                "模拟成交价",
                "佣金",
                "本笔净P&L",
                "原因",
                "来源",
                "覆盖",
                "会话",
            ]
        )
        self._configure_table(self.shadow_fill_table)
        self.shadow_explanation = QLabel(
            "状态：未启动。该工具只验证行情→信号→成本后模拟成交→"
            "持仓→盈亏→平仓链路，不以单晚收益证明策略有效。"
        )
        self.shadow_explanation.setWordWrap(True)
        fill_layout.addWidget(fill_title)
        fill_layout.addWidget(self.shadow_fill_table)
        fill_layout.addWidget(self.shadow_explanation)
        split.addWidget(fill_panel)

        self.targeted_research_tabs = QTabWidget()
        self.targeted_research_tabs.setDocumentMode(True)
        replay_panel = QFrame()
        replay_panel.setObjectName("panel")
        replay_layout = QVBoxLayout(replay_panel)
        replay_title = QLabel("分钟回放结果与证据")
        replay_title.setObjectName("sectionTitle")
        self.targeted_replay_table = QTableWidget(0, 12)
        self.targeted_replay_table.setHorizontalHeaderLabels(
            [
                "Run ID",
                "代码",
                "策略版本",
                "行情源",
                "分钟区间",
                "有效行",
                "缺口",
                "总收益",
                "最大回撤",
                "已实现P&L",
                "成交",
                "佣金",
            ]
        )
        self._configure_table(self.targeted_replay_table)
        replay_layout.addWidget(replay_title)
        replay_layout.addWidget(self.targeted_replay_table)
        self.targeted_research_tabs.addTab(
            replay_panel, "单会话回放"
        )

        robustness_panel = QFrame()
        robustness_panel.setObjectName("panel")
        robustness_layout = QVBoxLayout(robustness_panel)
        self.targeted_robustness_summary = QLabel(
            "尚未运行多日稳健性评估；结果不会自动晋级策略。"
        )
        self.targeted_robustness_summary.setObjectName("subtitle")
        self.targeted_robustness_summary.setWordWrap(True)
        robustness_layout.addWidget(self.targeted_robustness_summary)
        self.targeted_robustness_detail_tabs = QTabWidget()
        self.targeted_robustness_detail_tabs.setDocumentMode(True)
        self.targeted_robustness_runs_table = QTableWidget(0, 8)
        self.targeted_robustness_runs_table.setHorizontalHeaderLabels(
            [
                "Run ID",
                "代码",
                "策略版本",
                "行情源",
                "会话区间",
                "有效/总计",
                "收益方向一致",
                "证据等级",
            ]
        )
        self._configure_table(self.targeted_robustness_runs_table)
        self.targeted_robustness_runs_table.itemSelectionChanged.connect(
            self._robustness_selection_changed
        )
        self.targeted_robustness_detail_tabs.addTab(
            self.targeted_robustness_runs_table,
            "评估历史",
        )
        self.targeted_robustness_scenario_table = QTableWidget(0, 10)
        self.targeted_robustness_scenario_table.setHorizontalHeaderLabels(
            [
                "参数场景",
                "会话",
                "复合收益",
                "平均",
                "中位数",
                "最差会话",
                "盈利会话",
                "最大回撤",
                "成交",
                "佣金",
            ]
        )
        self._configure_table(
            self.targeted_robustness_scenario_table
        )
        self.targeted_robustness_detail_tabs.addTab(
            self.targeted_robustness_scenario_table,
            "参数扰动",
        )
        validation_panel = QWidget()
        validation_layout = QVBoxLayout(validation_panel)
        self.targeted_validation_summary = QLabel(
            "时间隔离验证至少需要 20 个完整有效会话；"
            "测试集永不参与参数选择。"
        )
        self.targeted_validation_summary.setObjectName("subtitle")
        self.targeted_validation_summary.setWordWrap(True)
        self.targeted_validation_table = QTableWidget(0, 12)
        self.targeted_validation_table.setHorizontalHeaderLabels(
            [
                "Run ID",
                "折",
                "训练选中",
                "训练区间",
                "验证区间",
                "验证策略",
                "验证基准",
                "验证门",
                "测试区间",
                "测试策略",
                "测试基准",
                "测试超额",
            ]
        )
        self._configure_table(self.targeted_validation_table)
        validation_layout.addWidget(self.targeted_validation_summary)
        validation_layout.addWidget(self.targeted_validation_table)
        robustness_layout.addWidget(
            self.targeted_robustness_detail_tabs
        )
        self.targeted_research_tabs.addTab(
            robustness_panel, "多日稳健性"
        )
        self.targeted_research_tabs.addTab(
            validation_panel, "时间隔离验证"
        )
        overfit_panel = QFrame()
        overfit_panel.setObjectName("panel")
        overfit_layout = QVBoxLayout(overfit_panel)
        self.targeted_overfit_summary = QLabel(
            "PBO/CSCV 与 DSR 至少需要 20 个同步完整会话；"
            "统计条件不足时明确显示不可估计。"
        )
        self.targeted_overfit_summary.setObjectName("subtitle")
        self.targeted_overfit_summary.setWordWrap(True)
        self.targeted_overfit_table = QTableWidget(0, 12)
        self.targeted_overfit_table.setHorizontalHeaderLabels(
            [
                "Run ID",
                "代码",
                "有效/总计",
                "候选",
                "CSCV分区",
                "组合",
                "PBO",
                "样本外亏损",
                "平均退化",
                "DSR概率",
                "DSR候选",
                "证据等级",
            ]
        )
        self._configure_table(self.targeted_overfit_table)
        overfit_layout.addWidget(self.targeted_overfit_summary)
        overfit_layout.addWidget(self.targeted_overfit_table)
        self.targeted_research_tabs.addTab(
            overfit_panel, "过拟合诊断"
        )
        quality_panel = QFrame()
        quality_panel.setObjectName("panel")
        quality_layout = QVBoxLayout(quality_panel)
        self.targeted_quality_summary = QLabel(
            "数据质量报告检查每个会话的 346 个预期分钟、"
            "连续缺口、异常报价、行情年龄和一档数量覆盖。"
        )
        self.targeted_quality_summary.setObjectName("subtitle")
        self.targeted_quality_summary.setWordWrap(True)
        self.targeted_quality_table = QTableWidget(0, 11)
        self.targeted_quality_table.setHorizontalHeaderLabels(
            [
                "交易日",
                "原始行",
                "可用行",
                "完整率",
                "缺失",
                "最长缺口",
                "Stale",
                "异常报价",
                "Age P95",
                "一档数量覆盖",
                "状态",
            ]
        )
        self._configure_table(self.targeted_quality_table)
        quality_layout.addWidget(self.targeted_quality_summary)
        quality_layout.addWidget(self.targeted_quality_table)
        self.targeted_research_tabs.addTab(
            quality_panel, "数据质量"
        )

        stress_panel = QFrame()
        stress_panel.setObjectName("panel")
        stress_layout = QVBoxLayout(stress_panel)
        self.targeted_stress_summary = QLabel(
            "执行压力测试将配置成本与 5bps、"
            "10bps+双倍佣金场景对比，并检查最优价一档参与率。"
        )
        self.targeted_stress_summary.setObjectName("subtitle")
        self.targeted_stress_summary.setWordWrap(True)
        self.targeted_stress_table = QTableWidget(0, 9)
        self.targeted_stress_table.setHorizontalHeaderLabels(
            [
                "成本场景",
                "滑点",
                "单笔佣金",
                "会话",
                "复合收益",
                "相对退化",
                "最大回撤",
                "成交",
                "总佣金",
            ]
        )
        self._configure_table(self.targeted_stress_table)
        stress_layout.addWidget(self.targeted_stress_summary)
        stress_layout.addWidget(self.targeted_stress_table)
        self.targeted_research_tabs.addTab(
            stress_panel, "执行压力"
        )

        review_panel = QFrame()
        review_panel.setObjectName("panel")
        review_layout = QVBoxLayout(review_panel)
        self.targeted_review_summary = QLabel(
            "独立评审汇总真实流来源、时间隔离、过拟合、"
            "序列相关性、成本与成交硬门；不会自动批准策略。"
        )
        self.targeted_review_summary.setObjectName("subtitle")
        self.targeted_review_summary.setWordWrap(True)
        review_layout.addWidget(self.targeted_review_summary)
        self.targeted_review_detail_tabs = QTabWidget()
        self.targeted_review_detail_tabs.setDocumentMode(True)
        self.targeted_review_history_table = QTableWidget(0, 10)
        self.targeted_review_history_table.setHorizontalHeaderLabels(
            [
                "Run ID",
                "代码",
                "行情源",
                "证据来源",
                "完整会话",
                "测试会话",
                "有效样本",
                "HAC为正",
                "通过门",
                "结论",
            ]
        )
        self._configure_table(self.targeted_review_history_table)
        self.targeted_review_history_table.itemSelectionChanged.connect(
            self._targeted_review_selection_changed
        )
        self.targeted_review_detail_tabs.addTab(
            self.targeted_review_history_table, "评审历史"
        )
        self.targeted_review_gate_table = QTableWidget(0, 7)
        self.targeted_review_gate_table.setHorizontalHeaderLabels(
            [
                "硬门",
                "状态",
                "观测值",
                "要求",
                "证据",
                "级别",
                "代码",
            ]
        )
        self._configure_table(self.targeted_review_gate_table)
        self.targeted_review_detail_tabs.addTab(
            self.targeted_review_gate_table, "硬门明细"
        )
        review_layout.addWidget(self.targeted_review_detail_tabs)
        self.targeted_research_tabs.addTab(
            review_panel, "独立评审"
        )
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([240, 340])
        self.targeted_workspace_tabs = QTabWidget()
        self.targeted_workspace_tabs.setDocumentMode(True)
        self.targeted_workspace_tabs.addTab(
            split, "实时影子持仓与成交"
        )
        self.targeted_workspace_tabs.addTab(
            self.targeted_research_tabs, "研究证据与独立评审"
        )
        preflight_panel = QFrame()
        preflight_panel.setObjectName("panel")
        preflight_layout = QVBoxLayout(preflight_panel)
        self.target_preflight_summary = QLabel(
            "输入标的后，这里汇总身份、非中概、行情、资金、整股与策略检查。"
        )
        self.target_preflight_summary.setObjectName("subtitle")
        self.target_preflight_summary.setWordWrap(True)
        self.target_preflight_table = QTableWidget(0, 6)
        self.target_preflight_table.setHorizontalHeaderLabels(
            ["检查", "结果", "当前值", "要求", "类别", "影响"]
        )
        self._configure_table(self.target_preflight_table)
        self.target_preflight_table.setColumnWidth(0, 180)
        self.target_preflight_table.setColumnWidth(1, 90)
        self.target_preflight_table.setColumnWidth(2, 320)
        self.target_preflight_table.setColumnWidth(3, 280)
        self.target_preflight_table.setColumnWidth(4, 90)
        self.target_preflight_table.setColumnWidth(5, 110)
        preflight_layout.addWidget(self.target_preflight_summary)
        preflight_layout.addWidget(self.target_preflight_table)
        self.targeted_workspace_tabs.addTab(
            preflight_panel, "执行前检查"
        )
        layout.addWidget(self.targeted_workspace_tabs)
        return page

    def _strategy_manager_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        cards = QHBoxLayout()
        self.strategy_total_card = MetricCard(
            "策略版本", "0", "不可变版本"
        )
        self.strategy_research_card = MetricCard(
            "研究中", "0", "尚未通过晋级门"
        )
        self.strategy_shadow_card = MetricCard(
            "Paper Shadow", "0", "仅观察，不下单"
        )
        self.strategy_blocked_card = MetricCard(
            "已失效", "0", "保留审计，不可恢复"
        )
        for card in (
            self.strategy_total_card,
            self.strategy_research_card,
            self.strategy_shadow_card,
            self.strategy_blocked_card,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        self.strategy_registry_table = QTableWidget(0, 11)
        self.strategy_registry_table.setHorizontalHeaderLabels(
            [
                "策略 ID",
                "名称",
                "版本",
                "状态",
                "模式",
                "风险预算",
                "参数Hash",
                "股票池Hash",
                "研究门",
                "更新时间",
                "说明",
            ]
        )
        self._configure_table(self.strategy_registry_table)
        self.strategy_registry_table.itemSelectionChanged.connect(
            self._strategy_registry_selection_changed
        )
        layout.addWidget(self.strategy_registry_table, 2)

        lower = QSplitter(Qt.Horizontal)
        editor_panel = QFrame()
        editor_panel.setObjectName("panel")
        editor_layout = QVBoxLayout(editor_panel)
        editor_title = QLabel("参数版本编辑器")
        editor_title.setObjectName("sectionTitle")
        version_row = QHBoxLayout()
        version_label = QLabel("新版本号")
        self.strategy_new_semver = QLineEdit()
        self.strategy_new_semver.setPlaceholderText(
            "例如 2.0.1-research"
        )
        version_row.addWidget(version_label)
        version_row.addWidget(self.strategy_new_semver)
        self.strategy_parameter_editor = QTextEdit()
        self.strategy_parameter_editor.setPlaceholderText(
            "选择策略后显示 JSON 参数；保存会创建新版本，不会覆盖旧版"
        )
        self.strategy_clone_button = QPushButton("从当前参数创建新版本")
        self.strategy_clone_button.clicked.connect(
            self._clone_strategy_version
        )
        editor_layout.addWidget(editor_title)
        editor_layout.addLayout(version_row)
        editor_layout.addWidget(self.strategy_parameter_editor)
        editor_layout.addWidget(self.strategy_clone_button)
        lower.addWidget(editor_panel)

        governance_panel = QFrame()
        governance_panel.setObjectName("panel")
        governance_layout = QVBoxLayout(governance_panel)
        governance_title = QLabel("生命周期与晋级门")
        governance_title.setObjectName("sectionTitle")
        self.strategy_governance_text = QTextEdit()
        self.strategy_governance_text.setReadOnly(True)
        self.strategy_governance_text.setPlainText(
            "选择策略查看研究门、版本哈希与安全状态。"
        )
        buttons = QHBoxLayout()
        self.strategy_shadow_button = QPushButton(
            "申请进入 Paper Shadow"
        )
        self.strategy_shadow_button.clicked.connect(
            lambda: self._transition_selected_strategy(
                "paper_shadow"
            )
        )
        self.strategy_pause_button = QPushButton("暂停")
        self.strategy_pause_button.clicked.connect(
            lambda: self._transition_selected_strategy("paused")
        )
        self.strategy_stop_button = QPushButton("停止")
        self.strategy_stop_button.clicked.connect(
            lambda: self._transition_selected_strategy("stopped")
        )
        buttons.addWidget(self.strategy_shadow_button)
        buttons.addWidget(self.strategy_pause_button)
        buttons.addWidget(self.strategy_stop_button)
        governance_layout.addWidget(governance_title)
        governance_layout.addWidget(self.strategy_governance_text)
        governance_layout.addLayout(buttons)
        lower.addWidget(governance_panel)
        lower.setSizes([690, 690])
        layout.addWidget(lower, 2)
        self._populate_strategy_registry()
        return page

    def _runtime_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        cards = QHBoxLayout()
        self.runtime_error_card = MetricCard(
            "错误事件", "0", "未解决与历史事件"
        )
        self.runtime_warning_card = MetricCard(
            "警告事件", "0", "行情、数据和策略门"
        )
        self.runtime_task_card = MetricCard(
            "活动任务", "0", "关闭时安全等待"
        )
        self.runtime_export_card = MetricCard(
            "最近导出", "无", "脱敏 CSV / JSON"
        )
        for card in (
            self.runtime_error_card,
            self.runtime_warning_card,
            self.runtime_task_card,
            self.runtime_export_card,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        controls = QHBoxLayout()
        refresh_button = QPushButton("刷新事件")
        refresh_button.clicked.connect(self._refresh_runtime_events)
        resolve_button = QPushButton("确认所选事件")
        resolve_button.clicked.connect(
            self._resolve_selected_runtime_event
        )
        export_button = QPushButton("导出当前终端状态")
        export_button.clicked.connect(self._export_terminal_state)
        controls.addWidget(refresh_button)
        controls.addWidget(resolve_button)
        controls.addWidget(export_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.runtime_empty_label = QLabel(
            "暂无运行事件。启动行情、刷新账户或运行研究后，"
            "故障与恢复记录会在此保留并可确认。"
        )
        self.runtime_empty_label.setObjectName("emptyState")
        self.runtime_empty_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.runtime_empty_label)

        splitter = QSplitter(Qt.Vertical)
        self.runtime_event_table = QTableWidget(0, 7)
        self.runtime_event_table.setHorizontalHeaderLabels(
            [
                "ID",
                "时间",
                "级别",
                "组件",
                "代码",
                "消息",
                "状态",
            ]
        )
        self._configure_table(self.runtime_event_table)
        splitter.addWidget(self.runtime_event_table)
        info_panel = QFrame()
        info_panel.setObjectName("panel")
        info_layout = QVBoxLayout(info_panel)
        info_title = QLabel("版本、路径与恢复信息")
        info_title.setObjectName("sectionTitle")
        self.runtime_info_text = QTextEdit()
        self.runtime_info_text.setReadOnly(True)
        self.runtime_info_text.setPlainText(
            "版本：0.19.0\n"
            f"只读资源：{self.paths.resource_root}\n"
            f"用户状态：{self.paths.state_root}\n"
            f"日志/数据库：{self.paths.runtime_root}\n"
            f"脱敏导出：{self.paths.exports_root}\n\n"
            "关闭流程：停止行情流 → 等待网络线程 → 保存本地数据库。"
        )
        info_layout.addWidget(info_title)
        info_layout.addWidget(self.runtime_info_text)
        splitter.addWidget(info_panel)
        splitter.setSizes([480, 180])
        layout.addWidget(splitter)
        self._refresh_runtime_events()
        return page

    def _dashboard_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        cards = QHBoxLayout()
        self.universe_card = MetricCard(
            "IBKR Paper 净值", "未读取", "不是回测收益"
        )
        self.verified_card = MetricCard(
            "账户当日盈亏", "不可用", "IBKR reqPnL"
        )
        self.history_card = MetricCard(
            "真实持仓", "未读取", "整股数量"
        )
        self.signal_card = MetricCard(
            "日内行情", "不可用", "必须 fresh Type 1"
        )
        for card in (
            self.universe_card,
            self.verified_card,
            self.history_card,
            self.signal_card,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        provenance_panel = QFrame()
        provenance_panel.setObjectName("panel")
        provenance_layout = QVBoxLayout(provenance_panel)
        provenance_header = QVBoxLayout()
        provenance_header.setSpacing(2)
        provenance_title = QLabel("数据与研究产物真值")
        provenance_title.setObjectName("sectionTitle")
        provenance_note = QLabel(
            "账户、实时行情、历史研究严格分区；失效结果不得部署"
        )
        provenance_note.setObjectName("subtitle")
        provenance_header.addWidget(provenance_title)
        provenance_header.addWidget(provenance_note)
        provenance_layout.addLayout(provenance_header)
        self.artifact_table = QTableWidget(0, 7)
        self.artifact_table.setHorizontalHeaderLabels(
            [
                "产物",
                "状态",
                "数据截至",
                "生成时间",
                "来源",
                "Run ID",
                "限制",
            ]
        )
        self._configure_table(self.artifact_table)
        self.artifact_table.setFixedHeight(150)
        provenance_layout.addWidget(self.artifact_table)
        provenance_panel.setMaximumHeight(215)
        layout.addWidget(provenance_panel)

        toolbar = QHBoxLayout()
        self.universe_refresh_button = QPushButton("刷新官方标的")
        self.universe_refresh_button.clicked.connect(self._refresh_universe)
        self.universe_cancel_button = QPushButton("取消刷新")
        self.universe_cancel_button.setEnabled(False)
        self.universe_cancel_button.clicked.connect(
            self._cancel_universe_refresh
        )
        scan_button = QPushButton("运行市场扫描")
        scan_button.clicked.connect(self._run_scan)
        gateway_button = QPushButton("仅检查 Gateway 端口")
        gateway_button.clicked.connect(self._probe_gateway)
        toolbar.addWidget(self.universe_refresh_button)
        toolbar.addWidget(self.universe_cancel_button)
        toolbar.addWidget(scan_button)
        toolbar.addWidget(gateway_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        body = QSplitter(Qt.Horizontal)
        self.dashboard_chart = PriceChart()
        body.addWidget(self.dashboard_chart)
        insight_panel = QFrame()
        insight_panel.setObjectName("panel")
        insight_layout = QVBoxLayout(insight_panel)
        insight_title = QLabel("当前研究边界")
        insight_title.setObjectName("sectionTitle")
        self.dashboard_notes = QTextEdit()
        self.dashboard_notes.setReadOnly(True)
        self.dashboard_notes.setPlainText(
            "账户实况：尚未连接，只显示离线研究资源\n"
            "旧 +205.6%：已封存为不可部署结果\n\n"
            "• 中国概念股：全部关闭\n"
            "• 交易单位：只允许整股\n"
            "• 核心：板块龙头；优质二线可观察\n"
            "• 广域后排：研究样本，不直接进入交易池\n"
            "• 杠杆 ETF：单独折算风险，仅限短期研究\n"
            "• 自动下单：关闭"
        )
        insight_layout.addWidget(insight_title)
        insight_layout.addWidget(self.dashboard_notes)
        body.addWidget(insight_panel)
        body.setSizes([900, 360])
        layout.addWidget(body)
        return page

    def _universe_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        self.universe_search = QLineEdit()
        self.universe_search.setPlaceholderText("搜索代码、名称或板块")
        self.universe_search.textChanged.connect(
            self._populate_universe_table
        )
        self.universe_filter = QComboBox()
        self.universe_filter.addItems(
            ["非中概研究池", "可交易核心池", "全部官方标的", "已排除"]
        )
        self.universe_filter.currentIndexChanged.connect(
            self._populate_universe_table
        )
        self.universe_count_label = QLabel("显示 0 / 0")
        self.universe_count_label.setObjectName("subtitle")
        controls.addWidget(self.universe_search)
        controls.addWidget(self.universe_filter)
        controls.addWidget(self.universe_count_label)
        layout.addLayout(controls)
        self.universe_table = QTableWidget(0, 9)
        self.universe_table.setHorizontalHeaderLabels(
            [
                "代码", "名称", "交易所", "类型", "板块", "层级",
                "中概/国别证据", "资格", "排除/说明",
            ]
        )
        self._configure_table(self.universe_table)
        layout.addWidget(self.universe_table)
        return page

    def _scanner_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        self.scan_search = QLineEdit()
        self.scan_search.setPlaceholderText("筛选代码、名称或板块")
        self.scan_search.textChanged.connect(self._populate_scan_table)
        self.scan_filter = QComboBox()
        self.scan_filter.addItems(["全部", "趋势候选", "可交易资格", "仅龙头"])
        self.scan_filter.setCurrentIndex(0)
        self.scan_filter.currentIndexChanged.connect(
            self._populate_scan_table
        )
        scan_button = QPushButton("重新扫描")
        scan_button.clicked.connect(self._run_scan)
        controls.addWidget(self.scan_search)
        controls.addWidget(self.scan_filter)
        controls.addWidget(scan_button)
        layout.addLayout(controls)
        self.scan_coverage_label = QLabel(
            "等待读取研究池和历史日 K 覆盖"
        )
        self.scan_coverage_label.setObjectName("subtitle")
        self.scan_coverage_label.setWordWrap(True)
        layout.addWidget(self.scan_coverage_label)
        splitter = QSplitter(Qt.Vertical)
        self.scan_table = QTableWidget(0, 13)
        self.scan_table.setHorizontalHeaderLabels(
            [
                "代码", "执行", "板块", "层级", "信号", "评分",
                "收盘", "整股容量", "20日", "63日", "年化波动",
                "RSI14", "原因",
            ]
        )
        self._configure_table(self.scan_table)
        self.scan_table.itemSelectionChanged.connect(
            self._scan_selection_changed
        )
        splitter.addWidget(self.scan_table)
        self.scan_chart = PriceChart()
        splitter.addWidget(self.scan_chart)
        splitter.setSizes([380, 300])
        layout.addWidget(splitter)
        return page

    def _data_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        schedule_button = QPushButton("将全部非中概研究池加入队列")
        schedule_button.clicked.connect(self._schedule_history)
        run_button = QPushButton("下载下一批日 K")
        run_button.clicked.connect(self._run_history)
        public_button = QPushButton("备用免费日 K（仅研究）")
        public_button.clicked.connect(self._run_public_history)
        retry_button = QPushButton("重试失败任务")
        retry_button.clicked.connect(self._retry_failed)
        self.batch_size = QSpinBox()
        self.batch_size.setRange(1, 100)
        self.batch_size.setValue(25)
        self.batch_size.setSuffix(" 个/批")
        controls.addWidget(schedule_button)
        controls.addWidget(run_button)
        controls.addWidget(public_button)
        controls.addWidget(retry_button)
        controls.addWidget(self.batch_size)
        controls.addStretch()
        layout.addLayout(controls)
        self.history_queue_summary = QLabel(
            "队列按龙头、优质二线、其余研究样本排序；下载仍按所选批量执行。"
        )
        self.history_queue_summary.setObjectName("subtitle")
        self.history_queue_summary.setWordWrap(True)
        layout.addWidget(self.history_queue_summary)
        self.queue_progress = QProgressBar()
        self.queue_progress.setRange(0, 100)
        self.queue_progress.setValue(0)
        layout.addWidget(self.queue_progress)
        self.queue_table = QTableWidget(0, 7)
        self.queue_table.setHorizontalHeaderLabels(
            ["代码", "周期", "优先级", "状态", "尝试", "K线数", "说明"]
        )
        self._configure_table(self.queue_table)
        layout.addWidget(self.queue_table)
        return page

    def _strategy_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        cards = QHBoxLayout()
        self.strategy_gate_card = MetricCard(
            "晋级门", "硬阻断", "研究代理不得进入影子或实盘"
        )
        self.strategy_return_card = MetricCard(
            "复权价 OOS 代理", "—", "不是历史整股可执行收益"
        )
        self.strategy_dd_card = MetricCard(
            "代理最大回撤", "—", "复权价研究曲线"
        )
        self.spy_return_card = MetricCard(
            "2×成本压力", "—", "佣金与滑点同时翻倍"
        )
        self.strategy_fold_card = MetricCard(
            "测试折数", "—", "锚定走样本外"
        )
        for card in (
            self.strategy_gate_card,
            self.strategy_return_card,
            self.strategy_dd_card,
            self.spy_return_card,
            self.strategy_fold_card,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)
        controls = QHBoxLayout()
        self.research_capital_input = QSpinBox()
        self.research_capital_input.setRange(100, 100_000_000)
        self.research_capital_input.setValue(
            int(self.config.initial_equity)
        )
        self.research_capital_input.setPrefix("$")
        self.research_capital_input.setSuffix(" 历史研究情景")
        self.research_capital_input.valueChanged.connect(
            self._research_capital_changed
        )
        run_button = QPushButton("运行复权价研究代理")
        run_button.clicked.connect(self._run_strategy_research)
        warning = QLabel(
            "⚠ 事后复权价 + 当前上市池；结果仅供研究，晋级门硬阻断"
        )
        warning.setObjectName("subtitle")
        controls.addWidget(self.research_capital_input)
        controls.addWidget(run_button)
        controls.addWidget(warning)
        controls.addStretch()
        layout.addLayout(controls)
        splitter = QSplitter(Qt.Vertical)
        self.strategy_chart = EquityComparisonChart()
        splitter.addWidget(self.strategy_chart)
        tables = QSplitter(Qt.Horizontal)
        self.candidate_table = QTableWidget(0, 6)
        self.candidate_table.setHorizontalHeaderLabels(
            ["参数", "收益", "回撤", "Sharpe", "交易数", "佣金"]
        )
        self._configure_table(self.candidate_table)
        self.fold_table = QTableWidget(0, 7)
        self.fold_table.setHorizontalHeaderLabels(
            [
                "折", "测试区间", "选中参数", "策略", "SPY",
                "回撤", "交易数",
            ]
        )
        self._configure_table(self.fold_table)
        tables.addWidget(self.candidate_table)
        tables.addWidget(self.fold_table)
        tables.setSizes([600, 760])
        splitter.addWidget(tables)
        splitter.setSizes([330, 280])
        layout.addWidget(splitter)
        return page

    def _backtest_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        cards = QHBoxLayout()
        self.backtest_return_card = MetricCard(
            "总收益", "—", "运行后显示"
        )
        self.backtest_cagr_card = MetricCard(
            "年化收益", "—", "按 252 个交易日估算"
        )
        self.backtest_sharpe_card = MetricCard(
            "年化 Sharpe", "—", "无风险利率暂按 0"
        )
        self.backtest_drawdown_card = MetricCard(
            "最大回撤", "—", "收盘权益序列"
        )
        self.backtest_trade_card = MetricCard(
            "交易与成本", "—", "整股、佣金与滑点"
        )
        for card in (
            self.backtest_return_card,
            self.backtest_cagr_card,
            self.backtest_sharpe_card,
            self.backtest_drawdown_card,
            self.backtest_trade_card,
        ):
            cards.addWidget(card)
        layout.addLayout(cards)

        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(6)
        self.backtest_strategy_combo = QComboBox()
        self._refresh_backtest_strategy_combo()
        self._configure_combo_width(
            self.backtest_strategy_combo,
            minimum_width=430,
            minimum_contents=26,
        )
        self.backtest_symbol = QLineEdit("XLF")
        self.backtest_symbol.setMinimumWidth(90)
        self.backtest_symbol.setMaximumWidth(140)
        self.backtest_symbol.setClearButtonEnabled(True)
        self.backtest_start = QDateEdit(QDate(2018, 1, 1))
        self.backtest_start.setCalendarPopup(True)
        self.backtest_start.setMinimumWidth(200)
        self.backtest_end = QDateEdit(QDate.currentDate())
        self.backtest_end.setCalendarPopup(True)
        self.backtest_end.setMinimumWidth(200)
        self.backtest_capital = QSpinBox()
        self.backtest_capital.setRange(100, 100_000_000)
        self.backtest_capital.setValue(1_500)
        self.backtest_capital.setPrefix("$")
        self.backtest_capital.setMinimumWidth(220)
        self.backtest_weight = QSpinBox()
        self.backtest_weight.setRange(1, 100)
        self.backtest_weight.setValue(100)
        self.backtest_weight.setSuffix("% 仓位")
        self.backtest_weight.setMinimumWidth(130)
        self.backtest_run_button = QPushButton("运行所选版本")
        self.backtest_run_button.clicked.connect(
            lambda: self._run_backtest_workspace(False)
        )
        self.backtest_compare_button = QPushButton("运行全部策略对比")
        self.backtest_compare_button.clicked.connect(
            lambda: self._run_backtest_workspace(True)
        )
        controls.addWidget(
            self._field_label("策略版本"),
            0,
            0,
        )
        controls.addWidget(
            self._field_label("代码"),
            0,
            1,
        )
        controls.addWidget(
            self._field_label("研究资金"),
            0,
            2,
        )
        controls.addWidget(
            self._field_label("目标仓位"),
            0,
            3,
        )
        controls.addWidget(self.backtest_strategy_combo, 1, 0)
        controls.addWidget(self.backtest_symbol, 1, 1)
        controls.addWidget(self.backtest_capital, 1, 2)
        controls.addWidget(self.backtest_weight, 1, 3)
        controls.addWidget(
            self._field_label("起始日期"),
            2,
            0,
        )
        controls.addWidget(
            self._field_label("结束日期"),
            2,
            1,
        )
        controls.addWidget(self.backtest_start, 3, 0)
        controls.addWidget(self.backtest_end, 3, 1)
        controls.addWidget(self.backtest_run_button, 3, 2)
        controls.addWidget(self.backtest_compare_button, 3, 3)
        controls.setColumnStretch(0, 4)
        controls.setColumnStretch(1, 2)
        controls.setColumnStretch(2, 2)
        controls.setColumnStretch(3, 2)
        layout.addLayout(controls)

        cost_row = QHBoxLayout()
        self.backtest_per_share_cost = QDoubleSpinBox()
        self.backtest_per_share_cost.setRange(0, 10)
        self.backtest_per_share_cost.setDecimals(4)
        self.backtest_per_share_cost.setValue(
            float(self.config.execution.per_share_commission)
        )
        self.backtest_minimum_cost = QDoubleSpinBox()
        self.backtest_minimum_cost.setRange(0, 100)
        self.backtest_minimum_cost.setDecimals(2)
        self.backtest_minimum_cost.setValue(
            float(self.config.execution.minimum_commission)
        )
        self.backtest_slippage = QDoubleSpinBox()
        self.backtest_slippage.setRange(0, 500)
        self.backtest_slippage.setDecimals(1)
        self.backtest_slippage.setValue(
            float(self.config.execution.slippage_bps)
        )
        cost_row.addWidget(QLabel("每股佣金"))
        cost_row.addWidget(self.backtest_per_share_cost)
        cost_row.addWidget(QLabel("最低佣金"))
        cost_row.addWidget(self.backtest_minimum_cost)
        cost_row.addWidget(QLabel("单边滑点(bps)"))
        cost_row.addWidget(self.backtest_slippage)
        self.backtest_evidence = QLabel(
            "研究代理：信号在收盘生成、次日开盘成交；默认复权日 K，"
            "不等于历史可执行整股成交。"
        )
        self.backtest_evidence.setObjectName("subtitle")
        self.backtest_evidence.setWordWrap(True)
        layout.addLayout(cost_row)
        layout.addWidget(self.backtest_evidence)

        splitter = QSplitter(Qt.Vertical)
        self.backtest_chart = PriceChart()
        self.backtest_chart.empty_message = "运行回测后显示最近 180 个交易日权益曲线"
        splitter.addWidget(self.backtest_chart)
        tables = QSplitter(Qt.Horizontal)
        self.backtest_comparison_table = QTableWidget(0, 13)
        self.backtest_comparison_table.setHorizontalHeaderLabels(
            [
                "Run ID", "策略版本", "代码", "区间", "总收益",
                "年化", "Sharpe", "Sortino", "Calmar", "最大回撤",
                "换手", "交易数", "成本",
            ]
        )
        self._configure_table(self.backtest_comparison_table)
        self.backtest_comparison_table.itemSelectionChanged.connect(
            self._backtest_result_selection_changed
        )
        self.backtest_trades_table = QTableWidget(0, 13)
        self.backtest_trades_table.setHorizontalHeaderLabels(
            [
                "信号时间", "成交时间", "信号代码", "执行代码",
                "方向", "整股数量", "原始开盘", "成交价",
                "滑点成本", "佣金", "成交后持仓", "成交后现金",
                "原因",
            ]
        )
        self._configure_table(self.backtest_trades_table)
        tables.addWidget(self.backtest_comparison_table)
        tables.addWidget(self.backtest_trades_table)
        tables.setSizes([840, 520])
        splitter.addWidget(tables)
        splitter.setSizes([300, 330])
        layout.addWidget(splitter)
        self.backtest_runs: list[BacktestRun] = []
        return page

    def _settings_tab(self) -> QWidget:
        page = QWidget()
        page.setMinimumHeight(820)
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        appearance = QFrame()
        appearance.setObjectName("panel")
        appearance_layout = QVBoxLayout(appearance)
        appearance_title = QLabel("外观与默认工作区")
        appearance_title.setObjectName("sectionTitle")
        appearance_row = QGridLayout()
        appearance_row.setHorizontalSpacing(10)
        appearance_row.setVerticalSpacing(6)
        self.settings_theme_combo = QComboBox()
        self.settings_theme_combo.addItem("深色", "dark")
        self.settings_theme_combo.addItem("浅色", "light")
        self.settings_theme_combo.setCurrentIndex(
            max(
                0,
                self.settings_theme_combo.findData(
                    self.preferences.theme
                ),
            )
        )
        self.settings_theme_combo.currentIndexChanged.connect(
            self._preview_theme_changed
        )
        self.settings_provider_combo = QComboBox()
        self.settings_provider_combo.addItem(
            "Finnhub 实时成交", "finnhub_trades"
        )
        self.settings_provider_combo.addItem(
            "Alpaca IEX 免费实时", "alpaca_iex"
        )
        self.settings_provider_combo.addItem(
            "IBKR 实时优先 / 延迟回退", "ibkr"
        )
        self.settings_provider_combo.addItem(
            "IBKR 5×24（盘前 / 盘后 / 隔夜）",
            "ibkr_extended",
        )
        self.settings_provider_combo.setCurrentIndex(
            max(
                0,
                self.settings_provider_combo.findData(
                    self.preferences.market_provider
                ),
            )
        )
        self.settings_provider_combo.currentIndexChanged.connect(
            self._settings_provider_selected
        )
        self._configure_combo_width(
            self.settings_provider_combo,
            minimum_width=300,
            minimum_contents=22,
        )
        self.settings_switch_provider_button = QPushButton(
            "切换 / 重连行情"
        )
        self.settings_switch_provider_button.clicked.connect(
            self._switch_to_settings_provider
        )
        appearance_row.addWidget(
            self._field_label("主题"),
            0,
            0,
        )
        appearance_row.addWidget(
            self._field_label("默认行情源"),
            0,
            1,
        )
        appearance_row.addWidget(self.settings_theme_combo, 1, 0)
        appearance_row.addWidget(
            self.settings_provider_combo,
            1,
            1,
        )
        appearance_row.addWidget(
            self.settings_switch_provider_button,
            1,
            2,
        )
        appearance_row.setColumnStretch(1, 3)
        appearance_row.setColumnStretch(2, 1)
        appearance_layout.addWidget(appearance_title)
        appearance_layout.addLayout(appearance_row)
        layout.addWidget(appearance)

        credentials = QFrame()
        credentials.setObjectName("panel")
        credential_layout = QVBoxLayout(credentials)
        credential_title = QLabel(
            "行情数据凭据（Windows 当前用户加密保存）"
        )
        credential_title.setObjectName("sectionTitle")
        credential_note = QLabel(
            "先选择数据源，再填写该数据源的凭据。输入框不会回填已保存明文；"
            "留空表示不修改。"
            "保存后立即清空输入框，日志和导出不会包含凭据。"
            "IBKR 不使用 API Key，在下方 Gateway 券商连接中管理。"
        )
        credential_note.setObjectName("subtitle")
        credential_note.setWordWrap(True)
        credential_row = QGridLayout()
        credential_row.setHorizontalSpacing(10)
        credential_row.setVerticalSpacing(8)
        self.settings_api_provider_combo = QComboBox()
        self.settings_api_provider_combo.addItem(
            "Finnhub", "finnhub_trades"
        )
        self.settings_api_provider_combo.addItem(
            "Alpaca IEX", "alpaca_iex"
        )
        self.settings_api_provider_combo.addItem(
            "IBKR Gateway（无需 API Key）", "ibkr"
        )
        self.settings_api_provider_combo.setCurrentIndex(
            max(
                0,
                self.settings_api_provider_combo.findData(
                    (
                        "ibkr"
                        if self.preferences.market_provider
                        == "ibkr_extended"
                        else self.preferences.market_provider
                    )
                ),
            )
        )
        self.settings_api_provider_combo.currentIndexChanged.connect(
            self._api_provider_changed
        )
        self._configure_combo_width(
            self.settings_api_provider_combo,
            minimum_width=180,
            minimum_contents=14,
        )
        self.settings_finnhub_key = QLineEdit()
        self.settings_finnhub_key.setEchoMode(QLineEdit.Password)
        self.settings_finnhub_key.setPlaceholderText(
            "Finnhub API Key（留空不修改）"
        )
        self.settings_alpaca_key = QLineEdit()
        self.settings_alpaca_key.setEchoMode(QLineEdit.Password)
        self.settings_alpaca_key.setPlaceholderText(
            "Alpaca API Key（留空不修改）"
        )
        self.settings_alpaca_secret = QLineEdit()
        self.settings_alpaca_secret.setEchoMode(QLineEdit.Password)
        self.settings_alpaca_secret.setPlaceholderText(
            "Alpaca API Secret（留空不修改）"
        )
        self.settings_save_credentials_button = QPushButton(
            "保存所选数据源凭据"
        )
        self.settings_save_credentials_button.clicked.connect(
            self._save_api_credentials
        )
        self.settings_clear_credentials_button = QPushButton(
            "清除所选数据源凭据"
        )
        self.settings_clear_credentials_button.clicked.connect(
            self._clear_selected_api_credentials
        )
        credential_row.addWidget(
            self._field_label("数据源"),
            0,
            0,
        )
        credential_row.addWidget(
            self._field_label("凭据（留空不修改）"),
            0,
            1,
            1,
            2,
        )
        credential_row.addWidget(
            self.settings_api_provider_combo,
            1,
            0,
        )
        credential_row.addWidget(
            self.settings_finnhub_key,
            1,
            1,
            1,
            2,
        )
        credential_row.addWidget(
            self.settings_alpaca_key,
            1,
            1,
        )
        credential_row.addWidget(
            self.settings_alpaca_secret,
            1,
            2,
        )
        credential_row.addWidget(
            self.settings_save_credentials_button,
            2,
            1,
        )
        credential_row.addWidget(
            self.settings_clear_credentials_button,
            2,
            2,
        )
        credential_row.setColumnStretch(1, 3)
        credential_row.setColumnStretch(2, 3)
        self.settings_credential_status = QLabel()
        self.settings_credential_status.setObjectName("subtitle")
        credential_layout.addWidget(credential_title)
        credential_layout.addWidget(credential_note)
        credential_layout.addLayout(credential_row)
        credential_layout.addWidget(
            self.settings_credential_status
        )
        layout.addWidget(credentials)

        gateway = QFrame()
        gateway.setObjectName("panel")
        gateway_layout = QVBoxLayout(gateway)
        gateway_title = QLabel(
            "IBKR Gateway · 券商连接 / 可选行情源"
        )
        gateway_title.setObjectName("sectionTitle")
        gateway_row = QGridLayout()
        gateway_row.setHorizontalSpacing(10)
        gateway_row.setVerticalSpacing(6)
        self.settings_ibkr_host = QLineEdit(
            self.preferences.ibkr_host
        )
        self.settings_ibkr_port = QSpinBox()
        self.settings_ibkr_port.setRange(4002, 4002)
        self.settings_ibkr_port.setValue(self.preferences.ibkr_port)
        self.settings_ibkr_client_id = QSpinBox()
        self.settings_ibkr_client_id.setRange(1, 999_999)
        self.settings_ibkr_client_id.setValue(
            self.preferences.ibkr_client_id
        )
        self.settings_ibkr_timeout = QSpinBox()
        self.settings_ibkr_timeout.setRange(1, 120)
        self.settings_ibkr_timeout.setValue(
            round(self.preferences.connection_timeout_seconds)
        )
        gateway_row.addWidget(self._field_label("Host"), 0, 0)
        gateway_row.addWidget(
            self._field_label("Paper 端口"),
            0,
            1,
        )
        gateway_row.addWidget(
            self._field_label("Client ID"),
            0,
            2,
        )
        gateway_row.addWidget(
            self._field_label("超时（秒）"),
            0,
            3,
        )
        gateway_row.addWidget(self.settings_ibkr_host, 1, 0)
        gateway_row.addWidget(self.settings_ibkr_port, 1, 1)
        gateway_row.addWidget(
            self.settings_ibkr_client_id,
            1,
            2,
        )
        gateway_row.addWidget(self.settings_ibkr_timeout, 1, 3)
        self.settings_paper_order_capability = QCheckBox(
            "允许 IBKR Paper 模拟下单能力"
        )
        self.settings_paper_order_capability.setChecked(
            self.preferences.paper_order_capability_enabled
        )
        self.settings_paper_order_capability.toggled.connect(
            self._paper_order_capability_toggled
        )
        self.settings_extended_hours_paper = QCheckBox(
            "启用 IBKR Paper 5×24 扩展时段（盘前 / 盘后 / 隔夜）"
        )
        self.settings_extended_hours_paper.setChecked(
            self.preferences.extended_hours_paper_enabled
        )
        self.settings_extended_hours_paper.toggled.connect(
            self._extended_hours_paper_toggled
        )
        gateway_row.addWidget(
            self.settings_paper_order_capability,
            2,
            0,
            1,
            4,
        )
        gateway_row.addWidget(
            self.settings_extended_hours_paper,
            3,
            0,
            1,
            4,
        )
        gateway_row.setColumnStretch(0, 3)
        gateway_row.setColumnStretch(1, 1)
        gateway_row.setColumnStretch(2, 1)
        gateway_row.setColumnStretch(3, 1)
        gateway_note = QLabel(
            "角色分离：IBKR 可同时提供行情，但账户、持仓和订单属于券商链路。"
            "端口硬锁 4002；能力开关默认关闭。开启后仍需唯一 DU 账户、"
            "实时行情、候选与策略门、单笔上限和自动量化页逐会话武装，"
            "不能连接 Live。5×24 仅在 Paper 开关开启时，按盘前/盘后 "
            "SMART 限价、隔夜 OVERNIGHT 限价路由；周末、休市和美东 "
            "03:50–04:00 维护窗口不会提交订单。"
        )
        gateway_note.setObjectName("emptyState")
        gateway_note.setWordWrap(True)
        gateway_layout.addWidget(gateway_title)
        gateway_layout.addLayout(gateway_row)
        gateway_layout.addWidget(gateway_note)
        layout.addWidget(gateway)

        storage = QFrame()
        storage.setObjectName("panel")
        storage.setMaximumHeight(230)
        storage_layout = QVBoxLayout(storage)
        storage_title = QLabel("数据、日志与安全边界")
        storage_title.setObjectName("sectionTitle")
        storage_text = QTextEdit()
        storage_text.setReadOnly(True)
        storage_text.setMaximumHeight(130)
        storage_text.setPlainText(
            f"用户设置：{self.paths.state_root / 'settings'}\n"
            f"加密凭据：{self.paths.state_root / 'credentials'}\n"
            f"运行数据库：{self.paths.runtime_root}\n"
            f"脱敏导出：{self.paths.exports_root}\n"
            "整股限制与 Paper 环境为锁定边界；模拟下单能力默认关闭，"
            "且不能绕过 DU 账户、演练上限、策略晋级和会话武装。"
        )
        storage_layout.addWidget(storage_title)
        storage_layout.addWidget(storage_text)
        layout.addWidget(storage)

        action_row = QHBoxLayout()
        self.settings_save_button = QPushButton("应用并保存设置")
        self.settings_save_button.clicked.connect(
            self._save_user_preferences
        )
        action_row.addStretch()
        action_row.addWidget(self.settings_save_button)
        layout.addLayout(action_row)
        layout.addStretch()
        self._api_provider_changed()
        self._refresh_credential_status()
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        scroll.setWidget(page)
        return scroll

    def _safety_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("不可绕过的首期边界")
        title.setObjectName("sectionTitle")
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            "1. IBKR Paper 模拟下单能力默认关闭。设置开关本身不会下单；"
            "只有“自动量化”页核验唯一 DU 账户、实时行情、候选、策略、"
            "金额上限并由用户逐会话确认后，独立适配器才允许发送 DAY 限价单。\n\n"
            "2. 券商链路只允许 IB Gateway 模拟端口 4002 和唯一 DU "
            "账户；Live 端口/账户硬阻断。行情链路可独立使用 Finnhub、"
            "Alpaca 或 IBKR Market Data。\n\n"
            "3. 不做碎股；所有资金可买数量均向下取整为整股。\n\n"
            "4. 中国概念股不进入研究池或交易池。日本、欧洲、加拿大、"
            "拉美等非中国发行人可研究；ADR 或 20-F 本身不构成排除理由。\n\n"
            "5. 龙头和优质二线分层独立于策略分数。后排股票即使技术"
            "指标得分高，也只能作为广域研究样本。\n\n"
            "6. 杠杆或替代执行品按通用风险倍数和最长持有期管理，"
            "不会被当作独立 Alpha 策略或重点标的。\n\n"
            "7. 历史收益不保证未来盈利；大模型不会被放进实时下单链路。"
            "策略优化采用可复现参数、走样本外验证和成本压力测试。"
        )
        layout.addWidget(title)
        layout.addWidget(text)
        return page

    def _configure_table(self, table: QTableWidget) -> None:
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.ElideRight)
        table.setHorizontalScrollMode(
            QAbstractItemView.ScrollPerPixel
        )
        table.setVerticalScrollMode(
            QAbstractItemView.ScrollPerPixel
        )
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setMinimumSectionSize(72)
        header.setDefaultSectionSize(128)
        header.setStretchLastSection(True)
        table.setSortingEnabled(True)

    def _load_local_state(self) -> None:
        if self.universe_path.exists():
            try:
                self.universe = load_universe_snapshot(
                    self.universe_path
                )
            except Exception as error:
                self._log(f"标的快照读取失败：{error}")
        self._populate_universe_table()
        self._populate_artifact_table()
        self._refresh_queue_table()
        self._refresh_cards()
        self._probe_gateway()
        if self.scan_path.exists():
            self._load_scan_file()
        self._refresh_market_scope_summary()
        if self.strategy_path.exists():
            self._load_strategy_report()
        for symbol in ("SPY", "QQQ", "DIA"):
            try:
                points = load_close_series(
                    symbol,
                    data_root=self.data_root,
                    fallback_data_root=self.bundled_data_root,
                )
            except (FileNotFoundError, ValueError):
                continue
            self.dashboard_chart.set_series(symbol, points)
            break
        self.targeted_replay_results = list(
            load_targeted_replays(
                self.paths.research_results_root
                / "targeted_replays"
            )
        )
        self._populate_targeted_replay_results()
        self.targeted_robustness_results = list(
            load_targeted_robustness(
                self.paths.research_results_root
                / "targeted_robustness"
            )
        )
        self._populate_targeted_robustness_results()
        self.targeted_walk_forward_results = list(
            load_targeted_walk_forwards(
                self.paths.research_results_root
                / "targeted_walk_forward"
            )
        )
        self._populate_targeted_walk_forward_results()
        self.targeted_overfit_results = list(
            load_targeted_overfits(
                self.paths.research_results_root
                / "targeted_overfit"
            )
        )
        self._populate_targeted_overfit_results()
        self.targeted_data_quality_results = list(
            load_targeted_data_quality(
                self.paths.research_results_root
                / "targeted_data_quality"
            )
        )
        self._populate_targeted_data_quality_results()
        self.targeted_execution_stress_results = list(
            load_targeted_execution_stress(
                self.paths.research_results_root
                / "targeted_execution_stress"
            )
        )
        self._populate_targeted_execution_stress_results()
        self.targeted_review_results = list(
            load_targeted_reviews(
                self.paths.research_results_root
                / "targeted_review"
            )
        )
        self._populate_targeted_review_results()

    def _refresh_universe(self) -> None:
        cancel_event = Event()

        def task(progress: Callable[[str], None]) -> UniverseSnapshot:
            progress("正在准备可写的用户参考数据目录…")
            reference_root = (
                self.paths.ensure_user_reference_catalog()
            )
            progress("正在下载 Nasdaq Trader 与 SEC 官方标的清单…")
            snapshot = refresh_official_universe(
                cache_root=reference_root,
                leader_seed_path=(
                    self.root / "configs" / "sector_leaders.csv"
                ),
                china_denylist_path=(
                    self.root
                    / "configs"
                    / "china_concept_denylist.csv"
                ),
                should_stop=cancel_event.is_set,
                save_snapshot=False,
            )
            progress("正在增量核验 500 家 SEC 注册地与行业…")
            return enrich_us_profiles(
                snapshot,
                cache_root=reference_root / "sec_profiles",
                max_new_profiles=500,
                progress=lambda done, total, symbol: progress(
                    f"SEC 核验 {done}/{total}：{symbol}"
                ),
                should_stop=cancel_event.is_set,
            )

        started = self._start_task(
            task,
            on_success=self._universe_refreshed,
            start_message="刷新官方标的中…",
            resource_group="universe",
        )
        if not started:
            return
        self.universe_refresh_cancel_event = cancel_event
        self.universe_refresh_worker = self.workers[-1]
        self.universe_refresh_button.setEnabled(False)
        self.universe_refresh_button.setText("官方标的刷新中…")
        self.universe_cancel_button.setEnabled(True)

    def _cancel_universe_refresh(self) -> None:
        if self.universe_refresh_cancel_event is None:
            return
        self.universe_refresh_cancel_event.set()
        self.universe_cancel_button.setEnabled(False)
        self.universe_cancel_button.setText("正在取消…")
        self._log("已请求取消官方标的刷新；当前网络请求最多再等待 8 秒。")

    def _reset_universe_refresh_controls(self) -> None:
        self.universe_refresh_cancel_event = None
        self.universe_refresh_worker = None
        self.universe_refresh_button.setEnabled(True)
        self.universe_refresh_button.setText("刷新官方标的")
        self.universe_cancel_button.setEnabled(False)
        self.universe_cancel_button.setText("取消刷新")

    def _universe_refreshed(self, result: object) -> None:
        self.universe = result  # type: ignore[assignment]
        self.universe_path = (
            self.reference_root / "universe.json"
        )
        self._populate_universe_table()
        self._refresh_cards()
        self._refresh_market_scope_summary()
        summary = self.universe.summary()
        self._log(
            f"官方标的已刷新：{summary['total']:,} 个，"
            f"研究池 {summary['research_eligible']} 个。"
        )

    def _schedule_history(self) -> None:
        if self.universe is None:
            QMessageBox.information(
                self,
                "缺少标的池",
                "请先刷新官方标的。",
            )
            return
        symbols = prioritized_research_symbols(
            self.universe,
            limit=None,
        )
        store = HistoryJobStore(self.queue_path)
        inserted = store.schedule(symbols)
        self._refresh_queue_table()
        self._refresh_market_scope_summary()
        self._log(
            f"全部非中概研究池已加入历史队列：新增 {inserted} 个，"
            f"队列合计 {len(store.list_jobs()):,} 个；"
            "下载仍按页面所选批量执行。"
        )

    def _run_history(self) -> None:
        maximum_jobs = self.batch_size.value()

        def task(progress: Callable[[str], None]) -> dict[str, int]:
            store = HistoryJobStore(self.queue_path)
            return run_history_queue(
                self.config.ibkr,
                store,
                data_root=self.data_root,
                maximum_jobs=maximum_jobs,
                progress=lambda done, total, symbol, status: (
                    progress(f"{done}/{total} {symbol}：{status}")
                ),
            )

        self.queue_progress.setValue(1)
        self._start_task(
            task,
            on_success=self._history_finished,
            start_message="IBKR 历史日 K 下载中…",
            resource_group="history",
        )

    def _history_finished(self, result: object) -> None:
        counts: dict[str, int] = result  # type: ignore[assignment]
        total = sum(counts.values())
        completed = counts.get("completed", 0)
        self.queue_progress.setValue(
            int(completed / total * 100) if total else 0
        )
        self._refresh_queue_table()
        self._refresh_cards()
        self._refresh_market_scope_summary()
        self._log(
            f"本批结束：累计完成 {completed}，"
            f"失败 {counts.get('failed', 0)}。"
        )

    def _run_public_history(self) -> None:
        maximum_jobs = self.batch_size.value()

        def task(progress: Callable[[str], None]) -> dict[str, int]:
            store = HistoryJobStore(self.queue_path)
            store.reset_failed()
            return run_public_history_queue(
                store,
                data_root=self.data_root,
                maximum_jobs=maximum_jobs,
                progress=lambda done, total, symbol, status: (
                    progress(f"{done}/{total} {symbol}：{status}")
                ),
            )

        self.queue_progress.setValue(1)
        self._start_task(
            task,
            on_success=self._history_finished,
            start_message=(
                "备用免费日 K 下载中；只用于历史研究，"
                "不会替代 IBKR 实时行情…"
            ),
            resource_group="history",
        )

    def _retry_failed(self) -> None:
        store = HistoryJobStore(self.queue_path)
        count = store.reset_failed()
        self._refresh_queue_table()
        self._log(f"已将 {count} 个失败任务放回待处理队列。")

    def _run_scan(self) -> None:
        if self.universe is None:
            QMessageBox.information(
                self,
                "缺少标的池",
                "请先刷新官方标的。",
            )
            return

        research_capital = self._research_scenario_capital()

        def task(progress: Callable[[str], None]) -> MarketScan:
            progress("正在读取已通过质量门的本地日 K…")
            result = scan_market(
                self.universe,
                data_root=self.data_root,
                fallback_data_root=self.bundled_data_root,
                capital=research_capital,
                max_position_risk_pct=(
                    self.config.risk_limits.max_position_exposure_pct
                ),
                substitutions=self.config.substitutions,
            )
            save_market_scan(result, self.scan_path)
            return result

        self._start_task(
            task,
            on_success=self._scan_finished,
            start_message="市场扫描中…",
            resource_group="scan",
        )

    def _scan_finished(self, result: object) -> None:
        self.scan = result  # type: ignore[assignment]
        self._populate_scan_table()
        self._refresh_cards()
        self._refresh_market_scope_summary()
        summary = self.scan.summary()
        self._log(
            f"扫描完成：{summary['scanned']} 个，"
            f"趋势候选 {summary['positive_signal']} 个。"
        )

    def _refresh_backtest_strategy_combo(self) -> None:
        if not hasattr(self, "backtest_strategy_combo"):
            return
        supported = {
            spec.strategy_id: spec for spec in STRATEGY_SPECS
        }
        records = [
            record
            for record in self.strategy_registry.list_records()
            if record.strategy_id in supported
            and record.status == "research"
        ]
        order = {
            spec.strategy_id: index
            for index, spec in enumerate(STRATEGY_SPECS)
        }
        records.sort(
            key=lambda record: (
                order.get(record.strategy_id, 999),
                record.semver,
            )
        )
        self.backtest_strategy_combo.clear()
        for record in records:
            self.backtest_strategy_combo.addItem(
                f"{record.name} · {record.semver}",
                record.version_id,
            )

    def _backtest_records(self, compare_all: bool) -> list[StrategyRecord]:
        supported = {spec.strategy_id for spec in STRATEGY_SPECS}
        records = [
            record
            for record in self.strategy_registry.list_records()
            if record.strategy_id in supported
            and record.status == "research"
        ]
        if compare_all:
            latest: dict[str, StrategyRecord] = {}
            for record in records:
                latest.setdefault(record.strategy_id, record)
            return [
                latest[spec.strategy_id]
                for spec in STRATEGY_SPECS
                if spec.strategy_id in latest
            ]
        version_id = str(
            self.backtest_strategy_combo.currentData() or ""
        )
        return [
            record
            for record in records
            if record.version_id == version_id
        ]

    def _run_backtest_workspace(self, compare_all: bool) -> None:
        if any(
            worker.isRunning()
            and worker.resource_group == "backtest"
            for worker in self.workers
        ):
            QMessageBox.information(
                self,
                "任务忙",
                "请等待当前数据或研究任务完成后再运行回测。",
            )
            return
        records = self._backtest_records(compare_all)
        if not records:
            QMessageBox.warning(
                self,
                "没有可运行版本",
                "策略目录中没有与回测工厂匹配的研究版本。",
            )
            return
        symbol = self.backtest_symbol.text().strip().upper()
        start_date = self.backtest_start.date().toPython()
        end_date = self.backtest_end.date().toPython()
        if start_date > end_date:
            QMessageBox.warning(
                self, "日期无效", "起始日期不能晚于结束日期。"
            )
            return
        requests = [
            BacktestRequest(
                strategy_id=record.strategy_id,
                strategy_version_id=record.version_id,
                parameter_hash=record.parameter_hash,
                code_hash=record.code_hash,
                parameters=record.parameters,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                initial_equity=Decimal(
                    self.backtest_capital.value()
                ),
                target_weight=(
                    Decimal(self.backtest_weight.value())
                    / Decimal("100")
                ),
                per_share_commission=Decimal(
                    str(self.backtest_per_share_cost.value())
                ),
                minimum_commission=Decimal(
                    str(self.backtest_minimum_cost.value())
                ),
                slippage_bps=Decimal(
                    str(self.backtest_slippage.value())
                ),
            )
            for record in records
        ]

        def task(progress: Callable[[str], None]) -> tuple[BacktestRun, ...]:
            runs = []
            for index, request in enumerate(requests, start=1):
                progress(
                    f"回测 {index}/{len(requests)}："
                    f"{request.strategy_id} {request.symbol}"
                )
                run = run_backtest(
                    request,
                    data_root=self.data_root,
                    fallback_data_root=self.bundled_data_root,
                )
                save_backtest_run(
                    run,
                    output_root=(
                        self.paths.research_results_root / "backtests"
                    ),
                )
                runs.append(run)
            return tuple(runs)

        self.backtest_run_button.setEnabled(False)
        self.backtest_compare_button.setEnabled(False)
        self._start_task(
            task,
            on_success=self._backtest_workspace_finished,
            start_message=(
                f"正在运行 {len(requests)} 个版本绑定回测…"
            ),
            resource_group="backtest",
        )

    def _backtest_workspace_finished(self, result: object) -> None:
        self.backtest_run_button.setEnabled(True)
        self.backtest_compare_button.setEnabled(True)
        runs = list(result)  # type: ignore[arg-type]
        self.backtest_runs = runs
        self.backtest_comparison_table.setSortingEnabled(False)
        self.backtest_comparison_table.setRowCount(len(runs))
        for row, run in enumerate(runs):
            values = (
                run.run_id[:8],
                (
                    f"{run.strategy.name} · "
                    f"{run.request.strategy_version_id[:8]}"
                ),
                run.request.symbol,
                f"{run.first_date} → {run.last_date}",
                f"{run.result.total_return:+.2%}",
                f"{run.metrics.annualized_return:+.2%}",
                f"{run.metrics.annualized_sharpe:.2f}",
                f"{run.metrics.annualized_sortino:.2f}",
                f"{run.metrics.calmar_ratio:.2f}",
                f"{run.result.max_drawdown:.2%}",
                f"{run.metrics.turnover:.2f}x",
                str(len(run.result.trades)),
                f"${run.result.total_commission:,.2f}",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, run.run_id)
                self.backtest_comparison_table.setItem(
                    row, column, item
                )
        self.backtest_comparison_table.setSortingEnabled(True)
        if runs:
            self.backtest_comparison_table.selectRow(0)
            self._show_backtest_run(runs[0])
        self._log(
            f"回测完成：{len(runs)} 个不可变 run 已保存到用户研究目录"
        )

    def _backtest_result_selection_changed(self) -> None:
        selected = self.backtest_comparison_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        item = self.backtest_comparison_table.item(row, 0)
        run_id = item.data(Qt.UserRole) if item else None
        for run in self.backtest_runs:
            if run.run_id == run_id:
                self._show_backtest_run(run)
                break

    def _show_backtest_run(self, run: BacktestRun) -> None:
        self.backtest_return_card.set_value(
            f"{run.result.total_return:+.2%}",
            f"期末 ${run.result.final_equity:,.2f}",
        )
        self.backtest_cagr_card.set_value(
            f"{run.metrics.annualized_return:+.2%}",
            f"{run.first_date} → {run.last_date}",
        )
        self.backtest_sharpe_card.set_value(
            f"{run.metrics.annualized_sharpe:.2f}",
            f"最差日 {run.metrics.worst_day:.2%}",
        )
        self.backtest_drawdown_card.set_value(
            f"{run.result.max_drawdown:.2%}",
            f"正收益日 {run.metrics.positive_day_ratio:.1%}",
        )
        self.backtest_trade_card.set_value(
            str(len(run.result.trades)),
            f"佣金 ${run.result.total_commission:,.2f}",
        )
        points = tuple(
            (timestamp.date(), float(equity))
            for timestamp, equity in run.result.equity_curve
        )
        self.backtest_chart.set_series(
            run.request.symbol,
            points,
            title=(
                f"{run.strategy.name} · 权益曲线 · "
                f"Run {run.run_id[:8]}"
            ),
        )
        self.backtest_trades_table.setSortingEnabled(False)
        self.backtest_trades_table.setRowCount(
            len(run.result.trades)
        )
        for row, trade in enumerate(run.result.trades):
            values = (
                trade.signal_timestamp.date().isoformat(),
                trade.timestamp.date().isoformat(),
                trade.signal_symbol,
                trade.execution_symbol,
                "买入" if trade.side.value == "buy" else "卖出",
                str(trade.quantity),
                f"${trade.raw_price:,.4f}",
                f"${trade.fill_price:,.4f}",
                f"${trade.slippage_cost:,.2f}",
                f"${trade.commission:,.2f}",
                str(trade.position_after),
                f"${trade.cash_after:,.2f}",
                (
                    f"{trade.reason}"
                    + (
                        " · 替代映射"
                        if trade.used_substitution
                        else ""
                    )
                ),
            )
            for column, value in enumerate(values):
                self.backtest_trades_table.setItem(
                    row, column, QTableWidgetItem(value)
                )
        self.backtest_trades_table.setSortingEnabled(True)
        self.backtest_evidence.setText(
            f"数据：{run.data_source} · {run.price_basis} · "
            f"data hash {run.data_hash[:12]} · "
            f"parameter hash {run.request.parameter_hash[:12]}；"
            "研究代理，不代表历史可成交表现。"
        )

    def _run_strategy_research(self) -> None:
        if self.universe is None:
            QMessageBox.information(
                self,
                "缺少标的池",
                "请先刷新官方标的。",
            )
            return

        research_capital = self._research_scenario_capital()

        def task(progress: Callable[[str], None]) -> dict:
            progress(
                "正在按整股、组合风险预算、替代品风险倍数和买不起回填规则"
                "比较8组参数；预计需要1–2分钟…"
            )
            research_config = replace(
                self.config,
                initial_equity=research_capital,
            )
            result = run_executable_cross_sectional_research(
                research_config,
                self.universe,
                data_root=Path(self.data_root),
                fallback_data_root=Path(
                    self.bundled_data_root
                ),
            )
            save_executable_research(
                result,
                Path(self.strategy_path),
            )
            return result

        self._start_task(
            task,
            on_success=self._strategy_finished,
            start_message="组合走样本外研究开始…",
            resource_group="strategy",
        )

    def _strategy_finished(self, result: object) -> None:
        self.strategy_report = result  # type: ignore[assignment]
        self._populate_strategy_report()
        self.artifact_catalog = load_artifact_catalog(
            self.paths.research_results_root
        )
        self._populate_artifact_table()
        metrics = self.strategy_report["out_of_sample"]["strategy"]
        self._log(
            f"组合研究完成：OOS {metrics['total_return']:+.1%}，"
            f"最大回撤 {metrics['max_drawdown']:.1%}。"
        )

    def _load_strategy_report(self) -> None:
        try:
            self.strategy_report = json.loads(
                self.strategy_path.read_text(encoding="utf-8")
            )
            self._populate_strategy_report()
        except Exception as error:
            self.strategy_report = None
            self._log(
                f"风险一致研究产物读取失败："
                f"{type(error).__name__}: {error}"
            )

    def _populate_strategy_report(self) -> None:
        if self.strategy_report is None:
            return
        out = self.strategy_report["out_of_sample"]
        strategy = out["strategy"]
        stress = out["cost_2x"]
        folds = out["folds"]
        scenario_equity = self.strategy_report.get(
            "scope", {}
        ).get("initial_equity")
        self.strategy_return_card.set_value(
            f"{strategy['total_return']:+.1%}",
            (
                f"情景资金 ${scenario_equity:,.0f} · "
                f"期末 ${strategy['final_equity']:,.0f} · 仍属探索性"
                if isinstance(scenario_equity, (int, float))
                else f"期末 ${strategy['final_equity']:,.0f} · 仍属探索性"
            ),
        )
        gate = self.strategy_report.get("promotion_gate", {})
        gate_reasons = gate.get("reasons", [])
        self.strategy_gate_card.set_value(
            "通过" if gate.get("passed") else "硬阻断",
            (
                "；".join(str(reason) for reason in gate_reasons[:2])
                or "缺少可验证的晋级证据"
            ),
        )
        self.strategy_dd_card.set_value(
            f"{strategy['max_drawdown']:.1%}",
            f"最差日 {strategy['worst_day']:.1%}",
        )
        self.spy_return_card.set_value(
            f"{stress['total_return']:+.1%}",
            f"期末 ${stress['final_equity']:,.0f}",
        )
        self.strategy_fold_card.set_value(
            str(len(folds)),
            "只计完整 126 日测试折",
        )
        self.strategy_chart.set_rows(
            list(self.strategy_report["chart_data"])
        )
        self.candidate_table.setHorizontalHeaderLabels(
            ["选中参数", "OOS", "回撤", "训练Sharpe", "交易数", "2×成本"]
        )
        candidates = folds
        self.candidate_table.setSortingEnabled(False)
        self.candidate_table.setRowCount(len(candidates))
        for index, row in enumerate(candidates):
            values = (
                row["selected"],
                f"{row['oos_return']:+.1%}",
                f"{row['oos_max_drawdown']:.1%}",
                f"{row['training_sharpe']:.2f}",
                str(row["oos_trade_count"]),
                f"{row['cost_2x_return']:+.1%}",
            )
            for column, value in enumerate(values):
                self.candidate_table.setItem(
                    index,
                    column,
                    QTableWidgetItem(value),
                )
        self.candidate_table.setSortingEnabled(True)
        self.fold_table.setHorizontalHeaderLabels(
            [
                "折", "测试区间", "选中参数", "OOS", "2×成本",
                "最高风险", "平均现金",
            ]
        )
        self.fold_table.setSortingEnabled(False)
        self.fold_table.setRowCount(len(folds))
        for index, row in enumerate(folds):
            values = (
                str(row["fold"]),
                f"{row['test_start']} → {row['test_end']}",
                row["selected"],
                f"{row['oos_return']:+.1%}",
                f"{row['cost_2x_return']:+.1%}",
                f"{row['max_risk_exposure_pct']:.1%}",
                f"{row['average_cash_pct']:.1%}",
            )
            for column, value in enumerate(values):
                self.fold_table.setItem(
                    index,
                    column,
                    QTableWidgetItem(value),
                )
        self.fold_table.setSortingEnabled(True)

    def _load_scan_file(self) -> None:
        try:
            payload = json.loads(
                self.scan_path.read_text(encoding="utf-8")
            )
            from us_quant.scanner import ScanResult

            results = []
            for row in payload["results"]:
                row["trading_date"] = date.fromisoformat(
                    row["trading_date"]
                )
                results.append(ScanResult(**row))
            self.scan = MarketScan(
                generated_at=datetime.fromisoformat(
                    payload["generated_at"]
                ),
                capital=float(payload["capital"]),
                data_date=(
                    date.fromisoformat(payload["data_date"])
                    if payload["data_date"]
                    else None
                ),
                results=tuple(results),
                skipped=dict(payload["skipped"]),
                max_position_risk_pct=float(
                    payload.get("max_position_risk_pct", 0.10)
                ),
            )
            self._populate_scan_table()
            self._refresh_cards()
        except Exception:
            self.scan = None

    def _apply_intraday_watchlist(self) -> None:
        if self.scan is None:
            return
        if (
            self.stream_worker is not None
            and self.stream_worker.isRunning()
        ):
            return
        paper_capital = self._paper_simulation_capital()
        selection_capital = (
            paper_capital or self._research_scenario_capital()
        )
        symbols = select_intraday_watchlist(
            self.scan,
            capital=selection_capital,
        )
        if symbols:
            self.stream_symbols.setText(",".join(symbols))
            self.stream_watch_card.set_value(
                str(len(symbols)),
                "实时订阅子集；不限制研究或交易范围",
            )
            self._log(
                f"已从 {len(self.scan.results):,} 个最近扫描结果中选出 "
                f"{len(symbols)} 个实时订阅代码；"
                "30 是行情连接上限，不是广域股票池大小"
            )

    def _auto_quant_preflight(self) -> AutoQuantPreflight:
        strategy = self._selected_auto_strategy_record()
        strategy_eligible = (
            strategy is not None
            and strategy.strategy_id == "intraday-auto-rotation"
            and strategy.status in {"research", "paper_shadow"}
            and (
                strategy.status == "research"
                or strategy.gate_passed
            )
        )
        strategy_detail = (
            f"{strategy.semver} · {strategy.status}"
            if strategy is not None
            else "请选择自动轮动策略版本"
        )
        candidate_symbols = {
            row.symbol for row in self.auto_quant_candidates
        }
        ready_count = sum(
            quote.symbol in candidate_symbols
            and quote.realtime_ready
            for quote in (
                self.stream_snapshot.quotes
                if self.stream_snapshot is not None
                else ()
            )
        )
        recent_ready_count = sum(
            self._quote_was_recently_ready(symbol)
            for symbol in candidate_symbols
        )
        return evaluate_auto_quant_preflight(
            capability_enabled=(
                self.preferences.paper_order_capability_enabled
            ),
            paper_confirmed=self.auto_arm_confirm.isChecked(),
            strategy_eligible=strategy_eligible,
            strategy_detail=strategy_detail,
            candidate_count=len(self.auto_quant_candidates),
            realtime_ready_count=ready_count,
            paper_capital=self._paper_simulation_capital(),
            recent_ready_count=recent_ready_count,
        )

    def _refresh_auto_quant_preflight(
        self, *_args: object
    ) -> None:
        if not hasattr(self, "auto_preflight_label"):
            return
        result = self._auto_quant_preflight()
        displayed_checks = [
            row
            for row in result.checks
            if row.name != "本次确认"
        ]
        ready_count = sum(row.passed for row in displayed_checks)
        details = "  ·  ".join(
            f"{'✓' if row.passed else '✕'} {row.name}：{row.detail}"
            for row in displayed_checks
        )
        self.auto_preflight_label.setText(
            f"准备检查 {ready_count}/{len(displayed_checks)}"
            " · 第 2 步会弹窗确认仅使用 DU 模拟账户。\n"
            f"{details}"
        )

    def _check_auto_order_channel(self) -> None:
        if (
            self.paper_order_service is not None
            or self.auto_quant_engine is not None
        ):
            QMessageBox.information(
                self,
                "Paper 会话正在使用",
                "当前自动量化会话已占用订单通道，无需重复检查。",
            )
            return
        self.auto_channel_check_button.setEnabled(False)
        order_config = IBKRConnectionConfig(
            host=self.preferences.ibkr_host,
            port=4002,
            client_id=min(
                999_999, self.preferences.ibkr_client_id + 100
            ),
            api_read_only=False,
            paper_order_submission_enabled=True,
            connection_timeout_seconds=(
                self.preferences.connection_timeout_seconds
            ),
        )

        def task(progress: Callable[[str], None]):
            progress("连接 IBKR Paper 订单通道并读取账户/订单；不下单…")
            service = IBKRPaperOrderService(
                order_config,
                journal=self.paper_order_journal,
                extended_hours_enabled=(
                    self.preferences.extended_hours_paper_enabled
                ),
            )
            try:
                connection = service.connect()
                broker_state = service.broker_state()
                return connection, broker_state
            finally:
                service.disconnect()

        started = self._start_task(
            task,
            on_success=self._auto_order_channel_checked,
            start_message="正在检查 IBKR Paper 订单通道（不下单）…",
            resource_group="broker",
        )
        if not started:
            self.auto_channel_check_button.setEnabled(True)

    def _auto_order_channel_checked(self, result: object) -> None:
        try:
            connection, broker_state = result  # type: ignore[misc]
            account_alias = connection.account_alias
            open_orders = connection.open_broker_orders
            unresolved = connection.unreconciled_local_orders
            net_liquidation = broker_state.net_liquidation
            cash = broker_state.cash
            positions = len(broker_state.positions)
        except (AttributeError, TypeError, ValueError) as error:
            raise TypeError(
                "unexpected Paper channel check result"
            ) from error
        self.auto_channel_check_button.setEnabled(True)
        detail = (
            f"{account_alias} · 净值 {_money(net_liquidation)} · "
            f"现金 {_money(cash)} · 持仓 {positions} · "
            f"开放 API 订单 {open_orders} · 本地待对账 {unresolved}"
        )
        self.auto_execution_health_label.setText(
            f"执行对账：订单通道检查通过（未下单） · {detail}"
        )
        self._log(f"IBKR Paper 订单通道检查通过（未下单）：{detail}")

    def _prepare_auto_quant_candidates(self) -> None:
        if self.universe is None:
            QMessageBox.information(
                self,
                "缺少官方标的池",
                "请先在总览刷新官方标的池，再执行全市场扫描。",
            )
            return
        if (
            self.auto_quant_engine is not None
            and self.auto_quant_engine.active
        ):
            QMessageBox.information(
                self,
                "自动量化运行中",
                "请先停止并完成 Paper 持仓对账，再更换候选集。",
            )
            return
        self.auto_prepare_button.setEnabled(False)
        self.auto_summary_label.setText(
            "正在扫描全部非中概研究池；只有历史数据质量达标的标的"
            "才会进入实时轮动候选。"
        )
        research_capital = self._research_scenario_capital()

        def task(progress: Callable[[str], None]) -> MarketScan:
            progress(
                "自动量化第 1 步：扫描全部非中概研究池及已有合格日 K…"
            )
            result = scan_market(
                self.universe,
                data_root=self.data_root,
                fallback_data_root=self.bundled_data_root,
                capital=research_capital,
                max_position_risk_pct=(
                    self.config.risk_limits.max_position_exposure_pct
                ),
                substitutions=self.config.substitutions,
            )
            save_market_scan(result, self.scan_path)
            return result

        started = self._start_task(
            task,
            on_success=self._auto_market_scan_finished,
            start_message="全市场扫描与 Paper 候选准备中…",
            resource_group="scan",
        )
        if not started:
            self.auto_prepare_button.setEnabled(True)

    def _auto_market_scan_finished(self, result: object) -> None:
        if not isinstance(result, MarketScan):
            raise TypeError("unexpected full-market scan result")
        self.scan = result
        scheduled = HistoryJobStore(self.queue_path).schedule(
            prioritized_research_symbols(self.universe, limit=None)
            if self.universe is not None
            else ()
        )
        self._refresh_queue_table()
        self._populate_scan_table()
        self._refresh_cards()
        self._refresh_market_scope_summary()
        if scheduled:
            self._log(
                f"全市场历史缺口已自动加入数据任务队列：新增 "
                f"{scheduled:,} 个；后续分批补齐后会自动扩大可评分覆盖。"
            )
        self._select_auto_quant_candidates()

    def _select_auto_quant_candidates(self) -> None:
        if self.scan is None or self.universe is None:
            self.auto_prepare_button.setEnabled(True)
            return
        limit = self.auto_candidate_limit.value()
        paper_capital = self._paper_simulation_capital()
        if paper_capital is None:
            self.auto_prepare_button.setEnabled(True)
            QMessageBox.information(
                self,
                "需要新鲜的 Paper 资金",
                "请先在“账户与持仓”刷新 IBKR Paper 账户。"
                "自动候选会按模拟账户资金筛选，不再套用 1500 美元"
                "历史研究情景。",
            )
            return
        requested_limit = Decimal(self.auto_capital_limit.value())
        if requested_limit > 0:
            paper_capital = min(paper_capital, requested_limit)
        multipliers = self._configured_exposure_multipliers()
        eligible = select_paper_rotation_rows(
            self.scan,
            self.universe,
            capital=paper_capital,
            max_position_fraction=(
                self.config.risk_limits.max_position_exposure_pct
            ),
            limit=limit,
            maximum_per_sector=max(
                2, min(4, (limit + 5) // 6)
            ),
            risk_multipliers=multipliers,
        )
        candidates: list[AutoQuantCandidate] = []
        for row in eligible:
            symbol = row.execution_symbol.strip().upper()
            candidates.append(
                AutoQuantCandidate(
                    symbol=symbol,
                    name=row.name,
                    sector=row.sector,
                    leader_tier=row.leader_tier,
                    scan_score=Decimal(str(row.score)),
                    signal=row.signal,
                )
            )
        if len(candidates) < 3:
            QMessageBox.warning(
                self,
                "合格候选不足",
                (
                    f"最新扫描只有 {len(candidates)} 个满足非中概、"
                    "龙头/优质二线、Paper 整股容量和数据门的候选；"
                    "至少需要 3 个才启动自动轮动。"
                ),
            )
            self.auto_prepare_button.setEnabled(True)
            return
        self.auto_quant_candidates = tuple(candidates)
        self._populate_auto_quant_candidates()
        symbols = tuple(row.symbol for row in candidates)
        research_count = int(
            self.universe.summary()["research_eligible"]
        )
        self.auto_scope_label.setText(
            f"全市场入口：非中概研究池 {research_count:,} · "
            f"本轮有合格日 K 并完成评分 {len(self.scan.results):,} · "
            f"缺数据/不足200根 {len(self.scan.skipped):,} · "
            f"Paper 实时轮动候选 {len(symbols)}。"
        )
        self.auto_summary_label.setText(
            f"已从全市场扫描中整理 {len(symbols)} 个实时轮动候选。"
            "行情订阅只承担分钟信号，不代表扫描范围只有这些代码；"
            "全部订单仍未武装。"
        )
        self.stream_symbols.setText(",".join(symbols))
        self.auto_prepare_button.setEnabled(True)
        if (
            self.stream_worker is not None
            and self.stream_worker.isRunning()
        ):
            self.auto_summary_label.setText(
                f"已整理 {len(symbols)} 个候选，正在安全停止旧行情并切换。"
            )
            self._request_stream_switch(
                str(self.stream_mode.currentData() or "finnhub_trades")
            )
            return
        self._start_stream()

    def _stop_auto_market_data(self) -> None:
        if (
            self.auto_quant_snapshot is not None
            and (
                self.auto_quant_snapshot.active
                or self.auto_quant_snapshot.positions
                or self.auto_quant_snapshot.pending_orders
            )
        ):
            QMessageBox.information(
                self,
                "请先停止模拟下单",
                "当前 Paper 模拟下单会话仍可能有持仓或在途订单。"
                "请先点击“停止会话并请求平仓”，完成券商对账后"
                "才能停止行情。",
            )
            return
        if self._stop_stream():
            self.auto_summary_label.setText(
                "当前行情已停止。可重新点击第 1 步准备新的候选。"
            )

    def _confirm_and_start_auto_quant(self) -> None:
        reply = QMessageBox.question(
            self,
            "确认启动 IBKR Paper 模拟下单",
            "下一步会连接唯一 DU 模拟账户，并可能向 IBKR Paper "
            "提交整股 DAY 限价单。不会连接 Live，也不会动真实资金。\n\n"
            "确认后，程序只会在实时行情、策略和风控检查全部通过时"
            "提交模拟订单。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            self.auto_arm_confirm.setChecked(False)
            return
        self.auto_arm_confirm.setChecked(True)
        self._start_auto_quant()

    def _populate_auto_quant_candidates(self) -> None:
        quotes = {
            quote.symbol: quote
            for quote in (
                self.stream_snapshot.quotes
                if self.stream_snapshot is not None
                else ()
            )
        }
        self.auto_candidate_table.setSortingEnabled(False)
        self.auto_candidate_table.setRowCount(
            len(self.auto_quant_candidates)
        )
        for row_index, candidate in enumerate(
            self.auto_quant_candidates
        ):
            quote = quotes.get(candidate.symbol)
            realtime = (
                "当前 fresh"
                if quote is not None and quote.realtime_ready
                else "近30秒有实时成交"
                if self._quote_was_recently_ready(candidate.symbol)
                else "等待"
            )
            values = (
                candidate.symbol,
                candidate.name,
                candidate.sector,
                (
                    "龙头"
                    if candidate.leader_tier == 1
                    else "优质二线"
                    if candidate.leader_tier == 2
                    else f"层级{candidate.leader_tier}"
                ),
                f"{candidate.scan_score:.1f}",
                candidate.signal,
                realtime,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 6 and realtime == "等待":
                    item.setForeground(QColor(self.theme.warning))
                self.auto_candidate_table.setItem(
                    row_index, column, item
                )
        self.auto_candidate_table.setSortingEnabled(True)
        self._refresh_auto_quant_preflight()
        self._refresh_extended_hours_status()

    def _start_auto_quant(self) -> None:
        if (
            self.shadow_engine is not None
            and self.shadow_engine.active
        ):
            self.auto_arm_confirm.setChecked(False)
            QMessageBox.warning(
                self,
                "内部仿真仍在运行",
                "同一资金真值不能同时运行内部仿真和 IBKR Paper 自动量化。",
            )
            return
        preflight = self._auto_quant_preflight()
        if not preflight.ready:
            self.auto_arm_confirm.setChecked(False)
            failures = "\n".join(
                f"• {row.name}：{row.detail}"
                for row in preflight.checks
                if not row.passed
            )
            QMessageBox.warning(
                self,
                "启动前检查未通过",
                "请先处理以下项目：\n" + failures,
            )
            return
        strategy = self._selected_auto_strategy_record()
        assert strategy is not None
        requested_capital_limit = Decimal(
            self.auto_capital_limit.value()
        )
        self.auto_start_button.setEnabled(False)
        self.auto_prepare_button.setEnabled(False)
        self.auto_channel_check_button.setEnabled(False)
        self.auto_summary_label.setText(
            "正在连接独立 IBKR Paper 订单会话并核验唯一 DU 账户…"
        )
        order_config = IBKRConnectionConfig(
            host=self.preferences.ibkr_host,
            port=4002,
            client_id=min(
                999_999, self.preferences.ibkr_client_id + 100
            ),
            api_read_only=False,
            paper_order_submission_enabled=True,
            connection_timeout_seconds=(
                self.preferences.connection_timeout_seconds
            ),
        )

        def task(progress: Callable[[str], None]):
            progress("连接 IBKR Paper 订单通道…")
            service = IBKRPaperOrderService(
                order_config,
                journal=self.paper_order_journal,
                extended_hours_enabled=(
                    self.preferences.extended_hours_paper_enabled
                ),
            )
            try:
                connection = service.connect()
            except Exception:
                service.disconnect()
                raise
            progress(
                f"已核验 {connection.account_alias}；准备逐会话武装…"
            )
            return (
                service,
                strategy.version_id,
                requested_capital_limit,
            )

        started = self._start_task(
            task,
            on_success=self._auto_order_service_connected,
            start_message="IBKR Paper 自动量化连接中…",
            resource_group="broker",
        )
        if not started:
            self.auto_arm_confirm.setChecked(False)
            self.auto_start_button.setEnabled(True)
            self.auto_prepare_button.setEnabled(True)
            self.auto_channel_check_button.setEnabled(True)

    def _auto_order_service_connected(self, result: object) -> None:
        try:
            service, version_id, requested_limit = result  # type: ignore[misc]
        except (TypeError, ValueError) as error:
            raise TypeError(
                "unexpected auto order connection result"
            ) from error
        if not isinstance(service, IBKRPaperOrderService):
            raise TypeError("unexpected Paper order service")
        try:
            broker_state = service.broker_state()
            if (
                broker_state.net_liquidation is None
                or broker_state.net_liquidation <= 0
            ):
                raise IBKRPaperOrderError(
                    "IBKR Paper 订单会话未返回有效净值"
                )
            if broker_state.positions:
                symbols = ", ".join(
                    row.symbol for row in broker_state.positions
                )
                raise IBKRPaperOrderError(
                    "首期自动量化要求 Paper 账户启动时空仓；"
                    f"当前持仓：{symbols}"
                )
            if broker_state.cash is None:
                raise IBKRPaperOrderError(
                    "IBKR Paper 订单会话未返回现金；"
                    "禁止使用保证金借款代替现金"
                )
            paper_capital = resolve_paper_session_capital(
                net_liquidation=broker_state.net_liquidation,
                cash=broker_state.cash,
                requested_limit=Decimal(requested_limit),
            )
            strategy = self.strategy_registry.get_version(
                str(version_id)
            )
            config = build_auto_rotation_config(
                strategy.parameters,
                initial_cash=Decimal(paper_capital),
                capital_source=(
                    "IBKR Paper "
                    f"{service.connection_snapshot().account_alias} "
                    f"现金约束；会话上限 {paper_capital}"
                ),
                daily_loss_limit=(
                    Decimal(paper_capital) * Decimal("0.01")
                ),
                symbol_risk_multipliers=(
                    self._configured_exposure_multipliers()
                ),
                layered_risk_limits=LayeredRiskLimits(
                    account=self.config.risk_limits,
                ),
            )
            engine = AutoQuantEngine(
                candidates=self.auto_quant_candidates,
                config=config,
                strategy_version_id=strategy.version_id,
                parameter_hash=strategy.parameter_hash,
                order_sink=service.submit,
                symbol_risk_multipliers=(
                    self._configured_exposure_multipliers()
                ),
            )
            snapshot = engine.start()
            assert snapshot.session_id is not None
            service.arm(
                session_id=snapshot.session_id,
                allowed_symbols=tuple(
                    row.symbol
                    for row in self.auto_quant_candidates
                ),
                max_order_notional=(
                    Decimal(paper_capital)
                    * config.max_position_fraction
                ),
                sellable_quantities={},
            )
        except Exception:
            service.disconnect()
            self.auto_arm_confirm.setChecked(False)
            self.auto_start_button.setEnabled(True)
            self.auto_prepare_button.setEnabled(True)
            self.auto_capital_limit.setEnabled(True)
            self.auto_channel_check_button.setEnabled(True)
            raise
        self.paper_order_service = service
        self.auto_quant_engine = engine
        self.auto_quant_snapshot = snapshot
        self.auto_stop_button.setEnabled(True)
        self.auto_pause_button.setEnabled(True)
        self.auto_resume_button.setEnabled(False)
        self.auto_stop_stream_button.setEnabled(False)
        self.auto_strategy_combo.setEnabled(False)
        self.auto_candidate_limit.setEnabled(False)
        self.auto_capital_limit.setEnabled(False)
        self.auto_channel_check_button.setEnabled(False)
        self.auto_arm_confirm.setEnabled(False)
        self._populate_auto_quant_snapshot(snapshot)
        self._record_runtime_event(
            severity="warning",
            component="auto_quant",
            code="PAPER_SESSION_ARMED",
            message=(
                f"IBKR Paper 自动量化会话 {snapshot.session_id[:8]} "
                f"已武装；候选 {snapshot.candidate_count}；Live 永久阻断"
            ),
        )

    def _pause_auto_quant_entries(self) -> None:
        engine = self.auto_quant_engine
        if engine is None:
            return
        self.auto_quant_snapshot = engine.pause_entries()
        self._cancel_stale_auto_entry_orders(force=True)
        self._populate_auto_quant_snapshot(self.auto_quant_snapshot)
        self._log(
            "自动量化已暂停新开仓；现有持仓的止损、止盈和时段退出继续运行。"
        )

    def _resume_auto_quant_entries(self) -> None:
        engine = self.auto_quant_engine
        if engine is None:
            return
        self.auto_quant_snapshot = engine.resume_entries()
        self._populate_auto_quant_snapshot(self.auto_quant_snapshot)
        self._log("自动量化已恢复新开仓。")

    def _stop_auto_quant(self) -> None:
        engine = self.auto_quant_engine
        if engine is None:
            return
        self.auto_quant_snapshot = engine.request_stop()
        self._cancel_stale_auto_entry_orders(force=True)
        self._populate_auto_quant_snapshot(self.auto_quant_snapshot)
        if (
            not self.auto_quant_snapshot.positions
            and not self.auto_quant_snapshot.pending_orders
        ):
            self.auto_quant_snapshot = engine.on_stream(
                self.stream_snapshot
                if self.stream_snapshot is not None
                else StreamSnapshot(
                    generation=0,
                    socket_connected=False,
                    handshake_complete=False,
                    reconnect_attempt=0,
                    quotes=(),
                    last_error_code=None,
                    last_message="stop",
                    observed_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            self._finish_auto_quant_session_if_safe()

    def _resume_auto_quant_from_reconciliation(self) -> None:
        engine = self.auto_quant_engine
        service = self.paper_order_service
        snapshot = self.auto_quant_snapshot
        if engine is None or service is None or snapshot is None:
            return
        if not snapshot.stop_requested or snapshot.active:
            self._log("当前会话未处于对账停机状态，无需恢复。")
            return
        session_id = snapshot.session_id
        if not session_id:
            self._log("会话 ID 缺失，无法恢复。")
            return
        pending_rows = service.pending_orders_for_session_dicts(
            session_id
        )
        if not pending_rows:
            reply = QMessageBox.question(
                self,
                "确认恢复会话",
                "对账后未发现未终态订单，是否仍恢复自动量化？\n"
                "恢复后将保留现有持仓并继续运行。",
            )
            if reply != QMessageBox.Yes:
                return
            self.auto_quant_snapshot = (
                engine.resume_from_reconciliation(
                    session_id=session_id,
                    allow_force_flat_exit=True,
                )
            )
            self._populate_auto_quant_snapshot(
                self.auto_quant_snapshot
            )
            self._log(
                "自动量化会话已恢复；保留现有持仓并继续运行。"
            )
            return
        pending_text = "\n".join(
            f"• {row['symbol']} {row['side']} {row['quantity']} 股 @ {row['limit_price']}  [{row['latest_status'] or '未知状态'}]"
            for row in pending_rows
        )
        reply = QMessageBox.question(
            self,
            "确认恢复并重挂在途订单",
            f"对账后发现 {len(pending_rows)} 笔未终态订单：\n\n"
            f"{pending_text}\n\n"
            f"是否恢复自动量化并重挂这些订单？\n"
            f"重挂将生成新的幂等键，避免重复下单。",
        )
        if reply != QMessageBox.Yes:
            return
        self.auto_quant_snapshot = (
            engine.resume_from_reconciliation(
                session_id=session_id,
                allow_force_flat_exit=True,
            )
        )
        for row in pending_rows:
            try:
                intent = PaperOrderIntent(
                    intent_id=str(row["intent_id"]),
                    session_id=str(row["session_id"]),
                    strategy_version_id=str(
                        row["strategy_version_id"]
                    ),
                    symbol=str(row["symbol"]),
                    side=str(row["side"]),
                    quantity=int(row["quantity"]),
                    limit_price=Decimal(
                        str(row["limit_price"])
                    ),
                    reason=str(row["reason"]),
                    generated_at=str(row["generated_at"]),
                    idempotency_key=row.get(
                        "idempotency_key"
                    ),
                )
            except Exception:
                continue
            new_intent = engine.resubmit_pending_intent(intent)
            if new_intent is None:
                continue
            try:
                order_id = service.submit(new_intent)
            except IBKRPaperOrderUncertainError as error:
                self._log(
                    f"重挂 {new_intent.symbol} 返回不确定；"
                    f"Order {error.broker_order_id} 需人工对账。"
                )
                continue
            except Exception as error:
                self._log(
                    f"重挂 {new_intent.symbol} 被阻断：{error}"
                )
                continue
            self._log(
                f"对账恢复重挂 {new_intent.side} {new_intent.symbol} "
                f"{new_intent.quantity} 股；Order {order_id}"
            )
        self._populate_auto_quant_snapshot(
            self.auto_quant_snapshot
        )
        self._log(
            "自动量化会话已恢复；在途订单已按人工复核结果重挂。"
        )

    def _poll_auto_quant_orders(self) -> None:
        service = self.paper_order_service
        engine = self.auto_quant_engine
        if service is None or engine is None:
            return
        self._cancel_stale_auto_entry_orders()
        for execution in service.poll_executions():
            self.auto_quant_snapshot = engine.on_execution(execution)
        for update in service.poll_updates():
            self.auto_quant_snapshot = engine.on_order_update(update)
        self._evaluate_auto_execution_health()

    def _cancel_stale_auto_entry_orders(
        self, *, force: bool = False
    ) -> None:
        service = self.paper_order_service
        engine = self.auto_quant_engine
        snapshot = self.auto_quant_snapshot
        if service is None or engine is None or snapshot is None:
            return
        now = datetime.now(timezone.utc)
        for intent in snapshot.pending_orders:
            if intent.side != "BUY":
                continue
            try:
                generated = datetime.fromisoformat(
                    intent.generated_at.replace("Z", "+00:00")
                )
                if generated.tzinfo is None:
                    generated = generated.replace(tzinfo=timezone.utc)
                age_seconds = (
                    now - generated.astimezone(timezone.utc)
                ).total_seconds()
            except (TypeError, ValueError):
                age_seconds = float("inf")
            if (
                not force
                and age_seconds
                < engine.config.entry_order_timeout_seconds
            ):
                continue
            try:
                requested = service.cancel_intent(intent.intent_id)
            except IBKRPaperOrderUncertainError as error:
                self.auto_quant_snapshot = (
                    engine.halt_for_reconciliation(str(error))
                )
                self._record_runtime_event(
                    severity="error",
                    component="paper_execution",
                    code="PAPER_CANCEL_UNCERTAIN",
                    message=str(error),
                )
                return
            except IBKRPaperOrderError as error:
                self._record_runtime_event(
                    severity="error",
                    component="paper_execution",
                    code="PAPER_CANCEL_BLOCKED",
                    message=str(error),
                )
                return
            if requested:
                reason = (
                    "用户停止"
                    if force
                    else (
                        f"超过 {engine.config.entry_order_timeout_seconds} "
                        "秒未完成"
                    )
                )
                self._record_runtime_event(
                    severity="warning",
                    component="paper_execution",
                    code="PAPER_ENTRY_CANCEL_REQUESTED",
                    message=(
                        f"{intent.symbol} BUY {intent.quantity} 股："
                        f"{reason}，已请求精确撤销该订单"
                    ),
                )

    def _evaluate_auto_execution_health(
        self,
    ) -> PaperExecutionHealth | None:
        service = self.paper_order_service
        engine = self.auto_quant_engine
        snapshot = self.auto_quant_snapshot
        if service is None or engine is None or snapshot is None:
            return None
        previous = self.paper_execution_health
        connection = service.connection_snapshot()
        reconciliations = service.reconciliation_rows_with_latency(
            session_id=snapshot.session_id,
            limit=200,
        )
        reconciliation_models = [
            PaperOrderReconciliation(
                intent_id=str(row["intent_id"]),
                session_id=str(row.get("session_id", "")),
                broker_order_id=int(row["broker_order_id"]),
                symbol=str(row["symbol"]),
                side=str(row["side"]),
                intended_quantity=Decimal(str(row["intended_quantity"])),
                latest_status=str(row["latest_status"]) if row.get("latest_status") is not None else None,
                reported_filled=Decimal(str(row.get("executed_quantity", 0))),
                reported_remaining=Decimal(str(max(0, row.get("intended_quantity", 0) - row.get("executed_quantity", 0)))),
                executed_quantity=Decimal(str(row.get("executed_quantity", 0))),
                reconciled=bool(row["reconciled"]),
                terminal=bool(row["terminal"]),
                reason=str(row["reason"]),
                observed_at=str(row["observed_at"]),
            )
            for row in reconciliations
        ]
        health = evaluate_paper_execution_health(
            connection=connection,
            broker_state=service.broker_state(),
            engine_snapshot=snapshot,
            reconciliations=tuple(reconciliation_models),
            candidate_symbols=frozenset(
                row.symbol for row in self.auto_quant_candidates
            ),
        )
        self.paper_execution_health = health
        labels = {
            "HEALTHY": "正常",
            "WAITING": "等待券商回报",
            "HALT": "已停机待核对",
        }
        detail = (
            "；".join(issue.message for issue in health.issues[:2])
            or "券商持仓、订单状态与逐笔成交一致"
        )
        self.auto_execution_health_label.setText(
            f"执行对账：{labels.get(health.status, health.status)} · "
            f"券商/本地持仓 {health.broker_position_count}/"
            f"{health.local_position_count} · "
            f"未对账订单 {health.unreconciled_order_count} · "
            f"{detail}"
        )
        self.auto_health_status_card.set_value(
            labels.get(health.status, health.status),
            detail,
        )
        self.auto_health_broker_card.set_value(
            f"{health.broker_position_count}/{health.local_position_count}",
            "券商持仓 vs 本地持仓",
        )
        self.auto_health_pending_card.set_value(
            str(health.pending_order_count),
            "本地 pending + 券商开放订单",
        )
        self.auto_health_unreconciled_card.set_value(
            str(health.unreconciled_order_count),
            "终态或仍在途但未核对",
        )
        latency_rows = [
            row for row in reconciliations if "submit_latency_ms" in row
        ]
        if latency_rows:
            latest_latency = latency_rows[0]["submit_latency_ms"]
            worst_latency = max(row["submit_latency_ms"] for row in latency_rows)
            self.auto_health_latency_card.set_value(
                f"{latest_latency} ms",
                f"最近提交延迟；本会话最慢 {worst_latency} ms",
            )
        else:
            self.auto_health_latency_card.set_value(
                "—",
                "已有成交后这里会显示 intent → placeOrder 延迟",
            )
        self.auto_reconcile_button.setEnabled(
            not health.connected
        )
        if not health.safe_to_continue and engine.active:
            self.auto_quant_snapshot = (
                engine.halt_for_reconciliation(detail)
            )
            if previous is None or previous.status != "HALT":
                self._record_runtime_event(
                    severity="error",
                    component="paper_execution",
                    code="PAPER_RECONCILIATION_HALT",
                    message=detail,
                )
        return health

    def _reconnect_auto_order_service(self) -> None:
        service = self.paper_order_service
        if service is None:
            return
        self.auto_reconcile_button.setEnabled(False)
        self.auto_execution_health_label.setText(
            "执行对账：正在重新连接 IBKR Paper 并读取开放订单、"
            "当日成交和当前持仓；不会自动恢复交易。"
        )

        def task(progress: Callable[[str], None]):
            progress("重新连接 IBKR Paper 并恢复订单快照…")
            return service.connect()

        self._start_task(
            task,
            on_success=self._auto_order_service_reconnected,
            start_message="IBKR Paper 重新对账中…",
            resource_group="broker",
        )

    def _auto_order_service_reconnected(self, result: object) -> None:
        del result
        self._poll_auto_quant_orders()
        if self.auto_quant_snapshot is not None:
            self._populate_auto_quant_snapshot(
                self.auto_quant_snapshot
            )
        self._finish_auto_quant_session_if_safe()

    def _finish_auto_quant_session_if_safe(self) -> None:
        snapshot = self.auto_quant_snapshot
        if (
            snapshot is None
            or snapshot.active
            or snapshot.positions
            or snapshot.pending_orders
        ):
            return
        service = self.paper_order_service
        if service is not None:
            broker_state = service.broker_state()
            reconciliations = (
                self.paper_order_journal.reconciliation_rows(
                    session_id=snapshot.session_id
                )
            )
            if broker_state.positions or any(
                not row.reconciled for row in reconciliations
            ):
                return
            service.disconnect()
        self.paper_order_service = None
        self.paper_execution_health = None
        self.auto_execution_health_label.setText(
            "执行对账：会话已安全结束，券商持仓和订单均已核对。"
        )
        self.auto_reconcile_button.setEnabled(False)
        self.auto_stop_button.setEnabled(False)
        self.auto_pause_button.setEnabled(False)
        self.auto_resume_button.setEnabled(False)
        self.auto_start_button.setEnabled(True)
        self.auto_prepare_button.setEnabled(True)
        self.auto_strategy_combo.setEnabled(True)
        self.auto_candidate_limit.setEnabled(True)
        self.auto_capital_limit.setEnabled(True)
        self.auto_channel_check_button.setEnabled(True)
        self.auto_arm_confirm.setEnabled(True)
        self.auto_arm_confirm.setChecked(False)
        self.auto_stop_stream_button.setEnabled(
            self.stream_worker is not None
            and self.stream_worker.isRunning()
        )

    def _populate_auto_shadow_table(
        self, snapshot: AutoQuantSnapshot
    ) -> None:
        quotes = {
            quote.symbol: quote
            for quote in (
                self.stream_snapshot.quotes
                if self.stream_snapshot is not None
                else ()
            )
        }
        pending_by_symbol = {
            intent.execution_symbol: intent
            for intent in snapshot.pending_orders
        }
        rows: list[tuple[str, str, str, str, str, str, str]] = []
        for candidate in self.auto_quant_candidates:
            quote = quotes.get(candidate.symbol)
            bid = quote.bid if quote is not None else None
            ask = quote.ask if quote is not None else None
            shadow_buy = (
                _price(_with_slippage(ask, Decimal("5"), side="BUY"))
                if ask is not None
                else "—"
            )
            shadow_sell = (
                _price(_with_slippage(bid, Decimal("5"), side="SELL"))
                if bid is not None
                else "—"
            )
            intent = pending_by_symbol.get(candidate.symbol)
            limit_price = _price(intent.limit_price) if intent is not None else "—"
            if bid is None or ask is None:
                status = "等待行情"
            elif intent is None:
                status = "无待挂单"
            else:
                status = "策略限价已挂"
            rows.append(
                (candidate.symbol, _price(bid), _price(ask), shadow_buy, shadow_sell, limit_price, status)
            )
        self.auto_shadow_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 6 and value == "策略限价已挂":
                    item.setForeground(QColor(self.theme.success))
                elif column == 6 and value == "等待行情":
                    item.setForeground(QColor(self.theme.warning))
                self.auto_shadow_table.setItem(row_index, column, item)

    def _populate_auto_latency_table(
        self, snapshot: AutoQuantSnapshot
    ) -> None:
        rows: list[tuple[str, str, str, str, str]] = []
        reconciliations = ()
        if self.paper_order_service is not None:
            reconciliations = (
                self.paper_order_service.reconciliation_rows_with_latency(
                    session_id=snapshot.session_id,
                    limit=100,
                )
            )
        for row in reconciliations:
            latency_ms = row.get("submit_latency_ms")
            if latency_ms is None:
                continue
            rows.append(
                (
                    str(row.get("intent_id", "")),
                    str(row.get("symbol", "")),
                    str(row.get("side", "")),
                    f"{int(latency_ms)} ms",
                    str(row.get("observed_at", ""))[:19].replace("T", " "),
                )
            )
        self.auto_latency_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 3:
                    latency_value = value.replace(" ms", "")
                    try:
                        latency_int = int(latency_value)
                        if latency_int <= 120:
                            item.setForeground(QColor(self.theme.success))
                        elif latency_int <= 500:
                            item.setForeground(QColor(self.theme.warning))
                        else:
                            item.setForeground(QColor(self.theme.error))
                    except ValueError:
                        pass
                self.auto_latency_table.setItem(row_index, column, item)

    def _populate_auto_quant_snapshot(
        self, snapshot: AutoQuantSnapshot
    ) -> None:
        account = (
            self.portfolio_view.account
            if self.portfolio_view is not None
            else None
        )
        broker_state = (
            self.paper_order_service.broker_state()
            if self.paper_order_service is not None
            else None
        )
        candidate_symbols = {
            row.symbol for row in self.auto_quant_candidates
        }
        broker_positions = (
            tuple(
                row
                for row in broker_state.positions
                if row.symbol in candidate_symbols
            )
            if broker_state is not None
            else ()
        )
        self.auto_status_card.set_value(
            (
                "停止处理中"
                if snapshot.stop_requested and snapshot.active
                else "仅管理持仓"
                if snapshot.entries_paused and snapshot.active
                else "运行中"
                if snapshot.active
                else "已停止"
            ),
            snapshot.status,
        )
        self.auto_pause_button.setEnabled(
            snapshot.active
            and not snapshot.entries_paused
            and not snapshot.stop_requested
        )
        self.auto_resume_button.setEnabled(
            snapshot.active
            and snapshot.entries_paused
            and not snapshot.stop_requested
        )
        self.auto_resume_from_reconciliation_button.setEnabled(
            not snapshot.active
            and snapshot.stop_requested
            and bool(snapshot.session_id)
        )
        self.auto_equity_card.set_value(
            _money(
                broker_state.net_liquidation
                if (
                    broker_state is not None
                    and broker_state.net_liquidation is not None
                )
                else account.net_liquidation
                if account is not None
                else snapshot.estimated_equity
            ),
            (
                "IBKR Paper 订单会话实时账户摘要"
                if (
                    broker_state is not None
                    and broker_state.net_liquidation is not None
                )
                else "IBKR Paper 只读快照"
                if account is not None
                else "等待券商刷新；显示本地估算"
            ),
        )
        self.auto_realized_card.set_value(
            _money(
                broker_state.realized_pnl
                if (
                    broker_state is not None
                    and broker_state.realized_pnl is not None
                )
                else account.realized_pnl
                if account is not None
                else snapshot.estimated_realized_pnl,
                signed=True,
            ),
            (
                "IBKR reqPnL（订单会话）"
                if (
                    broker_state is not None
                    and broker_state.realized_pnl is not None
                )
                else account.pnl_source
                if account is not None
                else "本地估算"
            ),
        )
        self.auto_unrealized_card.set_value(
            _money(
                broker_state.unrealized_pnl
                if (
                    broker_state is not None
                    and broker_state.unrealized_pnl is not None
                )
                else account.unrealized_pnl
                if account is not None
                else snapshot.estimated_unrealized_pnl,
                signed=True,
            ),
            "IBKR Paper 优先",
        )
        self.auto_position_card.set_value(
            str(
                len(broker_positions)
                if broker_positions
                else len(snapshot.positions)
            ),
            (
                f"在途 {len(snapshot.pending_orders)} · "
                f"完成交易 {snapshot.trades_today}"
            ),
        )
        self.auto_summary_label.setText(
            f"{snapshot.status} · 候选 {snapshot.candidate_count} · "
            f"会话风险资金 {_money(snapshot.initial_equity)} · "
            f"会话 {snapshot.session_id[:8] if snapshot.session_id else '无'} · "
            "券商订单与持仓必须以 IBKR Paper 回报为准"
        )
        self._populate_auto_shadow_table(snapshot)
        self._populate_auto_latency_table(snapshot)
        quote_map = {
            quote.symbol: quote
            for quote in (
                self.stream_snapshot.quotes
                if self.stream_snapshot is not None
                else ()
            )
        }
        displayed_positions = (
            tuple(
                (
                    row.symbol,
                    int(row.quantity),
                    row.average_cost,
                    "—",
                    "IBKR Paper position",
                )
                for row in broker_positions
                if (
                    row.quantity > 0
                    and row.quantity == int(row.quantity)
                )
            )
            if broker_positions
            else tuple(
                (
                    row.symbol,
                    row.quantity,
                    row.average_price,
                    row.opened_at,
                    row.provider,
                )
                for row in snapshot.positions
            )
        )
        self.auto_position_table.setRowCount(
            len(displayed_positions)
        )
        for row_index, position in enumerate(displayed_positions):
            symbol, quantity, average_price, opened_at, provider = (
                position
            )
            quote = quote_map.get(symbol)
            mark = (
                (quote.bid + quote.ask) / Decimal("2")
                if (
                    quote is not None
                    and quote.bid is not None
                    and quote.ask is not None
                )
                else average_price
            )
            unrealized = (
                mark - average_price
            ) * quantity
            values = (
                symbol,
                str(quantity),
                _price(average_price),
                _price(mark),
                _money(unrealized, signed=True),
                opened_at,
                provider,
            )
            for column, value in enumerate(values):
                self.auto_position_table.setItem(
                    row_index, column, QTableWidgetItem(value)
                )
        recent_fills = tuple(reversed(snapshot.fills[-20:]))
        self.auto_recent_fill_table.setRowCount(len(recent_fills))
        for row_index, fill in enumerate(recent_fills):
            values = (
                fill.occurred_at,
                fill.symbol,
                fill.side,
                str(fill.quantity),
                _price(fill.price),
                _money(fill.estimated_commission),
                _money(fill.realized_pnl, signed=True),
            )
            for column, value in enumerate(values):
                self.auto_recent_fill_table.setItem(
                    row_index, column, QTableWidgetItem(value)
                )
        audit_by_intent = {
            str(row["intent_id"]): row
            for row in self.paper_order_journal.audit_rows(limit=1000)
            if row.get("session_id") == snapshot.session_id
        }
        reconciliations = (
            self.paper_order_journal.reconciliation_rows(
                session_id=snapshot.session_id,
                limit=50,
            )
            if snapshot.session_id
            else ()
        )
        self.auto_order_table.setRowCount(len(reconciliations))
        for row_index, row in enumerate(reconciliations):
            audit = audit_by_intent.get(row.intent_id, {})
            status = (
                "已核对"
                if row.reconciled
                else row.latest_status or "等待首次状态"
            )
            values = (
                status,
                row.symbol,
                row.side,
                (
                    f"{row.intended_quantity}/"
                    f"{row.executed_quantity}"
                ),
                _price(
                    Decimal(str(audit.get("limit_price", "0")))
                ),
                (
                    f"{row.reason} · "
                    f"{str(audit.get('reason') or '')}"
                ).strip(" ·"),
                str(row.broker_order_id),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setForeground(
                        QColor(
                            self.theme.success
                            if row.reconciled
                            else self.theme.error
                            if row.terminal
                            else self.theme.warning
                        )
                    )
                self.auto_order_table.setItem(row_index, column, item)
        self._populate_auto_quant_candidates()

    def _current_target_symbol(self) -> str:
        return self.target_symbol_input.text().strip().upper()

    def _apply_target_symbol(self) -> None:
        symbol = self._current_target_symbol()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol):
            QMessageBox.warning(
                self,
                "代码无效",
                "请输入一个有效的美股或 ETF 代码。",
            )
            return
        if self.shadow_engine is not None and self.shadow_snapshot is not None:
            if self.shadow_snapshot.active:
                QMessageBox.information(
                    self,
                    "影子会话运行中",
                    "请先停止当前影子会话，再切换指定标的。",
                )
                self.target_symbol_input.setText(
                    self.shadow_snapshot.target_symbol
                )
                return
        self.target_symbol_input.setText(symbol)
        eligible = None
        if self.universe is not None:
            eligible = any(
                row.symbol == symbol and row.eligible_for_research
                for row in self.universe.records
            )
        if eligible is False:
            self.target_symbol_status.setText(
                f"{symbol} · 已设置，但尚未通过当前非中概研究资格门"
            )
        else:
            self.target_symbol_status.setText(
                f"{symbol} · 订阅、回放、评估和影子做 T 共用"
            )
        if (
            self.stream_worker is None
            or not self.stream_worker.isRunning()
        ):
            self.stream_symbols.setText(symbol)
        self._refresh_minute_data_status(symbol)
        self._refresh_target_preflight()
        self._log(
            f"当前指定做 T 标的已切换为 {symbol}；"
            "没有默认代码或单一股票专用逻辑。"
        )

    def _sync_targeted_symbol_to_stream(self) -> None:
        symbol = self._current_target_symbol()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol):
            QMessageBox.warning(
                self,
                "代码无效",
                "请输入一个有效的美股或 ETF 代码。",
            )
            return
        if (
            self.stream_worker is not None
            and self.stream_worker.isRunning()
        ):
            QMessageBox.information(
                self,
                "请先停止当前行情流",
                "停止当前行情流后，再切换本次针对性日内 T 标的。",
            )
            return
        self.target_symbol_input.setText(symbol)
        self.stream_symbols.setText(symbol)
        self.stream_watch_card.set_value(
            "1", f"针对性日内 T：{symbol}"
        )
        self._log(
            f"本次针对性日内 T 标的设为 {symbol}；"
            "启动行情后仍需通过实时性与中概排除门。"
        )
        self._refresh_minute_data_status(symbol)
        self._refresh_target_preflight()
        self._start_stream()

    def _refresh_minute_data_status(
        self, symbol: str | None = None
    ) -> None:
        target = (
            symbol or self._current_target_symbol()
        ).strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", target):
            self.minute_data_status.setText(
                "分钟证据：输入代码后显示本地已录数据；只回放 fresh bid/ask。"
            )
            return
        summary = self.minute_quote_store.summary(target)
        providers = " / ".join(summary.providers) or "无"
        origins = " / ".join(summary.evidence_origins) or "缺失"
        data_range = (
            f"{summary.first_minute} → {summary.last_minute}"
            if summary.first_minute and summary.last_minute
            else "尚无"
        )
        self.minute_data_status.setText(
            f"分钟证据 · {target}：可用 {summary.usable_rows} / "
            f"总计 {summary.total_rows} 行 · 来源 {providers} · "
            f"证据类型 {origins} · 区间 {data_range}"
        )

    def _refresh_target_preflight(self, *_args: object) -> None:
        if not hasattr(self, "target_preflight_table"):
            return
        symbol = self._current_target_symbol()
        universe_record = next(
            (
                row
                for row in self.universe.records
                if row.symbol == symbol
            ),
            None,
        ) if self.universe is not None else None
        quote = next(
            (
                row
                for row in self.stream_snapshot.quotes
                if row.symbol == symbol
            ),
            None,
        ) if self.stream_snapshot is not None else None
        account: AccountView | None = (
            self.portfolio_view.account
            if self.portfolio_view is not None
            else None
        )
        summary = self.minute_quote_store.summary(symbol)
        multiplier = self._configured_exposure_multipliers().get(
            symbol, Decimal("1")
        )
        result = evaluate_target_preflight(
            symbol,
            universe_record=universe_record,
            quote=quote,
            account=account,
            minute_summary=summary,
            strategy=self._selected_shadow_strategy_record(),
            exposure_multiplier=multiplier,
            broker_orders_available=False,
        )
        self.target_preflight_result = result
        decision = (
            "可启动内部策略仿真"
            if result.shadow_ready
            else "暂不可启动"
        )
        optional_failures = sum(
            not gate.passed
            for gate in result.gates
            if not gate.blocking
        )
        identity = " · ".join(
            value
            for value in (
                result.symbol,
                result.company_name,
                result.security_type,
                (
                    f"龙头层级 {result.leader_tier}"
                    if result.leader_tier is not None
                    else ""
                ),
            )
            if value
        ) or "尚未指定标的"
        self.target_preflight_summary.setText(
            f"{decision} · 硬门 "
            f"{result.hard_gates_passed}/{result.hard_gate_count} · "
            f"{identity}"
            + (
                f" · {optional_failures} 项研究证据待补"
                if optional_failures
                else ""
            )
        )
        self.target_preflight_table.setSortingEnabled(False)
        self.target_preflight_table.setRowCount(len(result.gates))
        for row_index, gate in enumerate(result.gates):
            values = (
                gate.name,
                "通过" if gate.passed else "未通过",
                gate.observed,
                gate.required,
                gate.category,
                "阻断启动" if gate.blocking else "研究提示",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 1:
                    item.setForeground(
                        QColor(
                            self.theme.success
                            if gate.passed
                            else self.theme.error
                            if gate.blocking
                            else self.theme.warning
                        )
                    )
                self.target_preflight_table.setItem(
                    row_index, column, item
                )
        self.target_preflight_table.setSortingEnabled(True)

    def _run_targeted_replay(self) -> None:
        strategy = self._selected_shadow_strategy_record()
        if strategy is None:
            QMessageBox.warning(
                self, "缺少策略版本", "请选择指定标的日内 T 策略版本。"
            )
            return
        symbol = self._current_target_symbol()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol):
            QMessageBox.warning(
                self, "代码无效", "请输入需要回放的股票或 ETF 代码。"
            )
            return
        if self.universe is not None:
            eligible = {
                row.symbol
                for row in self.universe.records
                if row.eligible_for_research
            }
            if symbol not in eligible:
                QMessageBox.warning(
                    self,
                    "标的门未通过",
                    f"{symbol} 未通过当前非中概研究资格门。",
                )
                return
        initial_equity = self._research_scenario_capital()

        def task(
            progress: Callable[[str], None],
        ) -> TargetedReplayResult:
            progress(f"读取 {symbol} 的本地 fresh 分钟 bid/ask…")
            records = self.minute_quote_store.load(symbol)
            groups: dict[str, list] = {}
            for record in records:
                groups.setdefault(record.provider, []).append(record)
            if not groups:
                raise ValueError(
                    f"{symbol} 尚无可用分钟数据；请先订阅实时行情并录制"
                )
            provider, selected = max(
                groups.items(),
                key=lambda item: (len(item[1]), item[0]),
            )
            sessions = group_regular_sessions(selected)
            if not sessions:
                raise ValueError(
                    f"{symbol} 在纽约常规交易时段内没有可回放分钟"
                )
            session_date, session_rows = sessions[-1]
            progress(
                f"使用 {provider} 的最近独立会话 {session_date}，"
                f"共 {len(session_rows)} 行分钟证据执行回放…"
            )
            result = run_targeted_replay(
                session_rows,
                strategy_version_id=strategy.version_id,
                strategy_semver=strategy.semver,
                parameter_hash=strategy.parameter_hash,
                parameters=strategy.parameters,
                initial_equity=initial_equity,
            )
            save_targeted_replay(
                result,
                self.paths.research_results_root
                / "targeted_replays",
            )
            return result

        self._start_task(
            task,
            on_success=self._targeted_replay_finished,
            start_message=f"{symbol} 分钟回放开始…",
            resource_group="targeted",
        )

    def _targeted_replay_finished(self, result: object) -> None:
        if not isinstance(result, TargetedReplayResult):
            raise TypeError("unexpected targeted replay result")
        self.targeted_replay_results.insert(0, result)
        self._populate_targeted_replay_results()
        self._refresh_minute_data_status(result.symbol)
        self._record_runtime_event(
            severity="info",
            component="targeted_replay",
            code="REPLAY_COMPLETE",
            message=(
                f"{result.symbol} run {result.run_id[:8]} 完成；"
                f"{result.row_count} 行；收益 {result.total_return:.2%}；"
                "券商订单 0"
            ),
        )
        self._log(
            f"{result.symbol} 分钟回放完成：收益 "
            f"{result.total_return:.2%}，最大回撤 "
            f"{result.maximum_drawdown:.2%}，成交 {len(result.fills)} 笔。"
        )

    def _populate_targeted_replay_results(self) -> None:
        rows = self.targeted_replay_results
        self.targeted_replay_table.setSortingEnabled(False)
        self.targeted_replay_table.setRowCount(len(rows))
        for row_index, result in enumerate(rows):
            values = (
                result.run_id[:8],
                result.symbol,
                result.strategy_semver,
                " / ".join(result.providers),
                (
                    f"{result.first_minute[:16]} → "
                    f"{result.last_minute[:16]}"
                ),
                str(result.row_count),
                str(result.gap_count),
                f"{result.total_return:+.2%}",
                f"{result.maximum_drawdown:.2%}",
                _money(result.realized_pnl, signed=True),
                str(len(result.fills)),
                _money(result.commission_cost),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.targeted_replay_table.setItem(
                    row_index, column, item
                )
        self.targeted_replay_table.setSortingEnabled(True)

    def _run_targeted_robustness(self) -> None:
        strategy = self._selected_shadow_strategy_record()
        if strategy is None:
            QMessageBox.warning(
                self, "缺少策略版本", "请选择指定标的日内 T 策略版本。"
            )
            return
        symbol = self._current_target_symbol()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol):
            QMessageBox.warning(
                self, "代码无效", "请输入需要评估的股票或 ETF 代码。"
            )
            return
        if self.universe is not None:
            eligible = {
                row.symbol
                for row in self.universe.records
                if row.eligible_for_research
            }
            if symbol not in eligible:
                QMessageBox.warning(
                    self,
                    "标的门未通过",
                    f"{symbol} 未通过当前非中概研究资格门。",
                )
                return
        initial_equity = self._research_scenario_capital()

        def task(
            progress: Callable[[str], None],
        ) -> tuple[
            TargetedRobustnessResult,
            TargetedWalkForwardResult | None,
            TargetedOverfitResult,
            TargetedDataQualityResult,
            TargetedExecutionStressResult,
            TargetedReviewResult,
        ]:
            progress(f"按行情源读取 {symbol} 的独立分钟会话…")
            records = self.minute_quote_store.load(symbol)
            raw_records = self.minute_quote_store.load(
                symbol, usable_only=False
            )
            groups: dict[str, list] = {}
            for record in records:
                groups.setdefault(record.provider, []).append(record)
            if not groups:
                raise ValueError(
                    f"{symbol} 尚无可用分钟数据；请先录制多个交易日"
                )
            provider, selected = max(
                groups.items(),
                key=lambda item: (len(item[1]), item[0]),
            )
            raw_selected = tuple(
                row
                for row in raw_records
                if row.provider == provider
            )
            progress(
                f"使用单一行情源 {provider}；按纽约交易日分组并运行"
                "基准及四组参数扰动…"
            )
            result = run_targeted_robustness(
                tuple(selected),
                strategy_version_id=strategy.version_id,
                strategy_semver=strategy.semver,
                parameter_hash=strategy.parameter_hash,
                parameters=strategy.parameters,
                initial_equity=initial_equity,
            )
            save_targeted_robustness(
                result,
                self.paths.research_results_root
                / "targeted_robustness",
            )
            progress(
                "按固定候选集运行 CSCV/PBO 与 DSR 过拟合诊断…"
            )
            overfit = run_targeted_overfit_diagnostics(result)
            save_targeted_overfit(
                overfit,
                self.paths.research_results_root
                / "targeted_overfit",
            )
            progress(
                "检查 346 个预期分钟、连续缺口、报价年龄和一档数量…"
            )
            data_quality = run_targeted_data_quality(
                result, raw_selected
            )
            save_targeted_data_quality(
                data_quality,
                self.paths.research_results_root
                / "targeted_data_quality",
            )
            validation = None
            if result.usable_sessions >= 20:
                progress(
                    "有效会话达到 20；执行仅训练集选参、"
                    "验证门和未触碰测试集…"
                )
                validation = run_targeted_walk_forward(
                    result,
                    tuple(selected),
                    parameters=strategy.parameters,
                    initial_equity=initial_equity,
                )
                save_targeted_walk_forward(
                    validation,
                    self.paths.research_results_root
                    / "targeted_walk_forward",
                )
            progress(
                "运行配置成本、5bps、10bps+双倍佣金执行压力…"
            )
            execution_stress = run_targeted_execution_stress(
                result,
                tuple(selected),
                parameters=strategy.parameters,
                initial_equity=initial_equity,
            )
            save_targeted_execution_stress(
                execution_stress,
                self.paths.research_results_root
                / "targeted_execution_stress",
            )
            progress(
                "汇总证据身份、真实流来源、序列相关性与晋级硬门…"
            )
            review = run_targeted_review(
                result,
                validation,
                overfit,
                parameters=strategy.parameters,
                data_quality=data_quality,
                execution_stress=execution_stress,
            )
            save_targeted_review(
                review,
                self.paths.research_results_root
                / "targeted_review",
            )
            return (
                result,
                validation,
                overfit,
                data_quality,
                execution_stress,
                review,
            )

        self._start_task(
            task,
            on_success=self._targeted_robustness_finished,
            start_message=f"{symbol} 多日稳健性评估开始…",
            resource_group="targeted",
        )

    def _targeted_robustness_finished(self, result: object) -> None:
        if not (
            isinstance(result, tuple)
            and len(result) == 6
            and isinstance(result[0], TargetedRobustnessResult)
            and (
                result[1] is None
                or isinstance(result[1], TargetedWalkForwardResult)
            )
            and isinstance(result[2], TargetedOverfitResult)
            and isinstance(result[3], TargetedDataQualityResult)
            and isinstance(
                result[4], TargetedExecutionStressResult
            )
            and isinstance(result[5], TargetedReviewResult)
        ):
            raise TypeError("unexpected targeted robustness result")
        (
            robustness,
            validation,
            overfit,
            data_quality,
            execution_stress,
            review,
        ) = result
        self.targeted_robustness_results.insert(0, robustness)
        if validation is not None:
            self.targeted_walk_forward_results.insert(0, validation)
        self.targeted_overfit_results.insert(0, overfit)
        self.targeted_data_quality_results.insert(
            0, data_quality
        )
        self.targeted_execution_stress_results.insert(
            0, execution_stress
        )
        self.targeted_review_results.insert(0, review)
        self._populate_targeted_robustness_results()
        self._populate_targeted_walk_forward_results()
        self._populate_targeted_overfit_results()
        self._populate_targeted_data_quality_results()
        self._populate_targeted_execution_stress_results()
        self._populate_targeted_review_results()
        self.targeted_workspace_tabs.setCurrentIndex(1)
        self.targeted_research_tabs.setCurrentIndex(6)
        self._record_runtime_event(
            severity="info",
            component="targeted_robustness",
            code="ROBUSTNESS_COMPLETE",
            message=(
                f"{robustness.symbol} run {robustness.run_id[:8]} 完成；"
                f"有效独立会话 {robustness.usable_sessions}/"
                f"{robustness.total_sessions}；"
                f"证据 {robustness.evidence_grade}；"
                f"过拟合诊断 {overfit.evidence_grade}；"
                f"数据质量 {data_quality.evidence_grade}；"
                f"执行压力 {execution_stress.evidence_grade}；"
                f"独立评审 {review.decision}；"
                "自动晋级 0"
            ),
        )
        self._log(
            f"{robustness.symbol} 多日稳健性评估完成："
            f"{robustness.usable_sessions}/"
            f"{robustness.total_sessions} 个有效会话，"
            f"参数收益方向一致率 "
            f"{robustness.sign_stability_fraction:.0%}；"
            + (
                f"时间隔离测试超额 "
                f"{validation.out_of_sample_excess_return:+.2%}。"
                if validation is not None
                else "未达到 20 会话，未运行时间隔离验证。"
            )
            + f" 过拟合诊断：{overfit.evidence_grade}。"
            + (
                f" 独立评审通过门 {review.passed_gates}/"
                f"{len(review.gates)}，结论 {review.decision}。"
            )
        )

    def _populate_targeted_walk_forward_results(self) -> None:
        table = self.targeted_validation_table
        table.setSortingEnabled(False)
        rows = [
            (result_index, result, fold)
            for result_index, result in enumerate(
                self.targeted_walk_forward_results
            )
            for fold in result.folds
        ]
        table.setRowCount(len(rows))
        for row_index, (result_index, result, fold) in enumerate(rows):
            values = (
                result.run_id[:8],
                str(fold.fold_number),
                fold.selected_scenario,
                f"{fold.train_start} → {fold.train_end}",
                (
                    f"{fold.validation_start} → "
                    f"{fold.validation_end}"
                ),
                f"{fold.validation_metrics.compounded_return:+.2%}",
                (
                    f"{fold.validation_benchmark.compounded_return:+.2%}"
                ),
                "通过" if fold.validation_passed else "未通过",
                f"{fold.test_start} → {fold.test_end}",
                f"{fold.test_metrics.compounded_return:+.2%}",
                f"{fold.test_benchmark.compounded_return:+.2%}",
                f"{fold.test_excess_return:+.2%}",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(
                        Qt.UserRole + 1,
                        result_index * 1000 + fold.fold_number,
                    )
                if column == 7 and not fold.validation_passed:
                    item.setForeground(QColor(self.theme.warning))
                table.setItem(row_index, column, item)
        table.setSortingEnabled(True)
        table.sortItems(0, Qt.AscendingOrder)
        if not self.targeted_walk_forward_results:
            self.targeted_validation_summary.setText(
                "时间隔离验证至少需要 20 个完整有效会话；"
                "测试集永不参与参数选择。"
            )
            return
        latest = self.targeted_walk_forward_results[0]
        self.targeted_validation_summary.setText(
            f"{latest.symbol} · {latest.strategy_semver} · "
            f"{len(latest.folds)} 折 · 验证通过 "
            f"{latest.validation_passed_folds}/{len(latest.folds)} · "
            f"未触碰测试集策略 "
            f"{latest.out_of_sample_metrics.compounded_return:+.2%}，"
            f"等风险基准 "
            f"{latest.out_of_sample_benchmark.compounded_return:+.2%}，"
            f"超额 {latest.out_of_sample_excess_return:+.2%} · "
            f"{latest.evidence_grade} · 不自动晋级"
        )

    def _populate_targeted_overfit_results(self) -> None:
        table = self.targeted_overfit_table
        table.setSortingEnabled(False)
        table.setRowCount(len(self.targeted_overfit_results))
        for row_index, result in enumerate(
            self.targeted_overfit_results
        ):
            values = (
                result.run_id[:8],
                result.symbol,
                (
                    f"{result.observations_used}/"
                    f"{result.observations_total}"
                ),
                str(result.candidate_count),
                str(result.cscv_partitions),
                str(result.cscv_combinations),
                (
                    f"{result.pbo:.1%}"
                    if result.pbo is not None
                    else "不可估计"
                ),
                (
                    f"{result.probability_oos_loss:.1%}"
                    if result.probability_oos_loss is not None
                    else "不可估计"
                ),
                (
                    f"{result.average_performance_degradation:+.3%}"
                    if result.average_performance_degradation is not None
                    else "不可估计"
                ),
                (
                    f"{result.dsr_probability:.1%}"
                    if result.dsr_probability is not None
                    else "不可估计"
                ),
                result.dsr_selected_scenario or "—",
                result.evidence_grade,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column in (6, 7, 9, 11) and (
                    "不可" in value
                    or "风险" in value
                    or "未通过" in value
                ):
                    item.setForeground(QColor(self.theme.warning))
                table.setItem(row_index, column, item)
        table.setSortingEnabled(True)
        if not self.targeted_overfit_results:
            self.targeted_overfit_summary.setText(
                "PBO/CSCV 与 DSR 至少需要 20 个同步完整会话；"
                "统计条件不足时明确显示不可估计。"
            )
            return
        latest = self.targeted_overfit_results[0]
        pbo = (
            f"{latest.pbo:.1%}"
            if latest.pbo is not None
            else "不可估计"
        )
        dsr = (
            f"{latest.dsr_probability:.1%}"
            if latest.dsr_probability is not None
            else "不可估计"
        )
        self.targeted_overfit_summary.setText(
            f"{latest.symbol} · 固定候选 {latest.candidate_count} · "
            f"同步会话 {latest.observations_used}/"
            f"{latest.observations_total} · "
            f"CSCV {latest.cscv_partitions} 分区/"
            f"{latest.cscv_combinations} 组合 · "
            f"PBO {pbo} · DSR {dsr} · "
            f"{latest.evidence_grade}。"
            "该页仅诊断研究选择偏差，不构成策略批准。"
        )

    def _populate_targeted_data_quality_results(self) -> None:
        table = self.targeted_quality_table
        table.setSortingEnabled(False)
        latest = (
            self.targeted_data_quality_results[0]
            if self.targeted_data_quality_results
            else None
        )
        sessions = latest.sessions if latest else ()
        table.setRowCount(len(sessions))
        for row_index, session in enumerate(sessions):
            values = (
                session.session_date,
                str(session.raw_rows),
                str(session.usable_rows),
                f"{session.completeness:.1%}",
                str(session.missing_minutes),
                str(session.maximum_consecutive_missing),
                str(session.stale_rows),
                str(session.invalid_quote_rows),
                (
                    f"{session.p95_source_age_seconds:.2f}s"
                    if session.p95_source_age_seconds is not None
                    else "不可估计"
                ),
                f"{session.size_coverage_fraction:.1%}",
                "通过" if session.high_quality else "阻断",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(
                    "；".join(session.failure_reasons)
                    if session.failure_reasons
                    else value
                )
                if column == 10 and not session.high_quality:
                    item.setForeground(QColor(self.theme.warning))
                table.setItem(row_index, column, item)
        table.setSortingEnabled(True)
        if latest is None:
            self.targeted_quality_summary.setText(
                "数据质量报告检查每个会话的 346 个预期分钟、"
                "连续缺口、异常报价、行情年龄和一档数量覆盖。"
            )
            return
        age = (
            f"{latest.p95_source_age_seconds:.2f}s"
            if latest.p95_source_age_seconds is not None
            else "不可估计"
        )
        self.targeted_quality_summary.setText(
            f"{latest.symbol} · 高质量会话 "
            f"{latest.high_quality_sessions}/{latest.session_count} · "
            f"最差完整率 {latest.minimum_completeness:.1%} · "
            f"最长连续缺口 {latest.maximum_consecutive_missing} 分钟 · "
            f"行情年龄 P95 {age} · "
            f"一档数量覆盖 {latest.size_coverage_fraction:.1%} · "
            f"{latest.evidence_grade}"
        )

    def _populate_targeted_execution_stress_results(self) -> None:
        table = self.targeted_stress_table
        table.setSortingEnabled(False)
        latest = (
            self.targeted_execution_stress_results[0]
            if self.targeted_execution_stress_results
            else None
        )
        scenarios = latest.scenarios if latest else ()
        table.setRowCount(len(scenarios))
        for row_index, scenario in enumerate(scenarios):
            values = (
                scenario.scenario,
                f"{scenario.slippage_bps}bps",
                _money(scenario.commission_per_order),
                str(scenario.session_count),
                f"{scenario.compounded_return:+.2%}",
                f"{scenario.degradation_vs_configured:+.2%}",
                f"{scenario.maximum_drawdown:.2%}",
                str(scenario.total_fills),
                _money(scenario.commission_cost),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 4 and scenario.compounded_return <= 0:
                    item.setForeground(QColor(self.theme.warning))
                table.setItem(row_index, column, item)
        table.setSortingEnabled(True)
        if latest is None:
            self.targeted_stress_summary.setText(
                "执行压力测试将配置成本与 5bps、"
                "10bps+双倍佣金场景对比，并检查最优价一档参与率。"
            )
            return
        participation = (
            f"{latest.p95_top_of_book_participation:.1%}"
            if latest.p95_top_of_book_participation is not None
            else "不可估计"
        )
        self.targeted_stress_summary.setText(
            f"{latest.symbol} · 最差压力收益 "
            f"{latest.worst_stressed_return:+.2%} · "
            f"最差相对退化 "
            f"{latest.worst_performance_degradation:+.2%} · "
            f"一档参与率 P95 {participation} · "
            f"{latest.capacity_status} · {latest.evidence_grade}"
        )

    def _populate_targeted_review_results(self) -> None:
        table = self.targeted_review_history_table
        table.setSortingEnabled(False)
        table.setRowCount(len(self.targeted_review_results))
        for row_index, result in enumerate(
            self.targeted_review_results
        ):
            dependence = result.dependence
            values = (
                result.run_id[:8],
                result.symbol,
                result.provider,
                " / ".join(result.evidence_origins) or "缺失",
                next(
                    (
                        gate.observed
                        for gate in result.gates
                        if gate.code == "complete_sessions"
                    ),
                    "—",
                ),
                str(dependence.oos_session_count),
                (
                    f"{dependence.effective_sample_size_ar1:.1f}"
                    if dependence.effective_sample_size_ar1 is not None
                    else "不可估计"
                ),
                (
                    f"{dependence.probability_mean_positive:.1%}"
                    if dependence.probability_mean_positive is not None
                    else "不可估计"
                ),
                f"{result.passed_gates}/{len(result.gates)}",
                (
                    "可进入人工独立评审"
                    if result.eligible_for_independent_review
                    else f"阻断 {result.blocking_failures}"
                ),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(Qt.UserRole, result.run_id)
                if column in (7, 9) and (
                    "不可" in value or "阻断" in value
                ):
                    item.setForeground(QColor(self.theme.warning))
                table.setItem(row_index, column, item)
        table.setSortingEnabled(True)
        if not self.targeted_review_results:
            self.targeted_review_summary.setText(
                "独立评审汇总真实流来源、时间隔离、过拟合、"
                "序列相关性、成本与成交硬门；不会自动批准策略。"
            )
            self._populate_targeted_review_gates(None)
            return
        table.selectRow(0)
        self._populate_targeted_review_gates(
            self.targeted_review_results[0]
        )

    def _targeted_review_selection_changed(self) -> None:
        selected = self.targeted_review_history_table.selectedItems()
        if not selected:
            return
        item = self.targeted_review_history_table.item(
            selected[0].row(), 0
        )
        run_id = item.data(Qt.UserRole) if item is not None else None
        result = next(
            (
                candidate
                for candidate in self.targeted_review_results
                if candidate.run_id == run_id
            ),
            None,
        )
        self._populate_targeted_review_gates(result)

    def _populate_targeted_review_gates(
        self,
        result: TargetedReviewResult | None,
    ) -> None:
        table = self.targeted_review_gate_table
        gates = result.gates if result else ()
        table.setSortingEnabled(False)
        table.setRowCount(len(gates))
        for row_index, gate in enumerate(gates):
            values = (
                gate.name,
                "通过" if gate.passed else "阻断",
                gate.observed,
                gate.required,
                gate.evidence,
                gate.severity,
                gate.code,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 1 and not gate.passed:
                    item.setForeground(QColor(self.theme.warning))
                table.setItem(row_index, column, item)
        table.setSortingEnabled(False)
        if result is None:
            return
        dependence = result.dependence
        hac = (
            f"{dependence.probability_mean_positive:.1%}"
            if dependence.probability_mean_positive is not None
            else "不可估计"
        )
        effective = (
            f"{dependence.effective_sample_size_ar1:.1f}"
            if dependence.effective_sample_size_ar1 is not None
            else "不可估计"
        )
        self.targeted_review_summary.setText(
            f"{result.symbol} · 通过 {result.passed_gates}/"
            f"{len(result.gates)} · 阻断 {result.blocking_failures} · "
            f"测试会话 {dependence.oos_session_count} · "
            f"相关性折算样本 {effective} · HAC均值为正 {hac} · "
            + (
                "仅可进入人工独立评审；尚未批准。"
                if result.eligible_for_independent_review
                else "证据门未满足，不得晋级。"
            )
        )

    def _populate_targeted_robustness_results(self) -> None:
        rows = self.targeted_robustness_results
        table = self.targeted_robustness_runs_table
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for row_index, result in enumerate(rows):
            values = (
                result.run_id[:8],
                result.symbol,
                result.strategy_semver,
                result.provider,
                f"{result.first_session} → {result.last_session}",
                f"{result.usable_sessions}/{result.total_sessions}",
                f"{result.sign_stability_fraction:.0%}",
                result.evidence_grade,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(Qt.UserRole, result.run_id)
                    item.setData(Qt.UserRole + 1, row_index)
                table.setItem(row_index, column, item)
        table.setSortingEnabled(True)
        table.sortItems(0, Qt.AscendingOrder)
        if rows:
            table.selectRow(0)
            self._populate_robustness_scenarios(rows[0])
        else:
            self._populate_robustness_scenarios(None)

    def _robustness_selection_changed(self) -> None:
        selected = self.targeted_robustness_runs_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        item = self.targeted_robustness_runs_table.item(row, 0)
        run_id = item.data(Qt.UserRole) if item is not None else None
        result = next(
            (
                candidate
                for candidate in self.targeted_robustness_results
                if candidate.run_id == run_id
            ),
            None,
        )
        self._populate_robustness_scenarios(result)

    def _populate_robustness_scenarios(
        self,
        result: TargetedRobustnessResult | None,
    ) -> None:
        table = self.targeted_robustness_scenario_table
        summaries = result.scenario_summaries if result else ()
        table.setSortingEnabled(False)
        table.setRowCount(len(summaries))
        for row_index, summary in enumerate(summaries):
            values = (
                summary.scenario,
                str(summary.session_count),
                f"{summary.compounded_return:+.2%}",
                f"{summary.mean_session_return:+.2%}",
                f"{summary.median_session_return:+.2%}",
                f"{summary.worst_session_return:+.2%}",
                f"{summary.profitable_session_fraction:.0%}",
                f"{summary.maximum_drawdown:.2%}",
                str(summary.total_fills),
                _money(summary.commission_cost),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(Qt.UserRole + 1, row_index)
                table.setItem(row_index, column, item)
        table.setSortingEnabled(True)
        table.sortItems(0, Qt.AscendingOrder)
        if result is None:
            self.targeted_robustness_summary.setText(
                "尚未运行多日稳健性评估；结果不会自动晋级策略。"
            )
            return
        skipped = len(result.skipped_sessions)
        readiness = (
            "达到参数稳健性门；仍需时间隔离验证"
            if result.review_ready
            else "未达到参数稳健性门"
        )
        self.targeted_robustness_summary.setText(
            f"{result.symbol} · {result.strategy_semver} · "
            f"{result.provider} · 有效会话 "
            f"{result.usable_sessions}/{result.total_sessions}，"
            f"跳过 {skipped} · 参数收益方向一致 "
            f"{result.sign_stability_fraction:.0%}"
            "（不代表盈利）· "
            f"{result.evidence_grade} · {readiness}"
        )

    def _configured_exposure_multipliers(
        self,
    ) -> dict[str, Decimal]:
        return {
            rule.execution_symbol: rule.exposure_multiplier
            for rule in self.config.substitutions.values()
        }

    def _research_scenario_capital(self) -> Decimal:
        control = getattr(self, "research_capital_input", None)
        if control is None:
            return self.config.initial_equity
        return Decimal(control.value())

    def _research_capital_changed(self, value: int) -> None:
        self.research_budget_card.set_value(
            f"${value:,.0f}",
            "历史研究情景；不是 Paper/Live 账户余额",
        )

    def _paper_simulation_capital(self) -> Decimal | None:
        if self.portfolio_view is None:
            return None
        account = self.portfolio_view.account
        if account.environment != "paper":
            return None
        try:
            observed = datetime.fromisoformat(account.observed_at)
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            age_seconds = (
                datetime.now(timezone.utc)
                - observed.astimezone(timezone.utc)
            ).total_seconds()
        except (TypeError, ValueError):
            return None
        if age_seconds < 0 or age_seconds > 300:
            return None
        value = account.net_liquidation
        if value is None or value <= 0:
            return None
        return value

    def _probe_gateway(self) -> None:
        result = probe_ibkr_socket(self.config.ibkr)
        if result.reachable:
            self.gateway_badge.setText("端口 · 4002 可达")
            self.gateway_badge.setProperty("state", "ok")
        else:
            self.gateway_badge.setText("端口 · 不可达")
            self.gateway_badge.setProperty("state", "warn")
        self.gateway_badge.style().unpolish(self.gateway_badge)
        self.gateway_badge.style().polish(self.gateway_badge)

    def _refresh_account_snapshot(self) -> None:
        def task(
            progress: Callable[[str], None],
        ) -> IBKRReadOnlySnapshot:
            progress(
                "正在进行 IBKR 只读握手并读取账户、持仓和 P&L…"
            )
            return collect_readonly_snapshot(
                self.config.ibkr,
                symbols=("SPY", "QQQ", "XLF", "AAPL"),
                timeout_seconds=20,
            )

        self._start_task(
            task,
            on_success=self._account_snapshot_finished,
            start_message="IBKR 只读账户刷新开始…",
            resource_group="broker",
        )

    def _populate_strategy_registry(self) -> None:
        all_records = self.strategy_registry.list_records()
        records = [
            record
            for record in all_records
            if record.status != "legacy_invalidated"
        ]
        counts = Counter(row.status for row in all_records)
        self.strategy_total_card.set_value(
            str(len(records)), "每次参数变化生成新版本"
        )
        self.strategy_research_card.set_value(
            str(counts["research"]), "仅离线评估"
        )
        self.strategy_shadow_card.set_value(
            str(counts["paper_shadow"]), "无订单提交能力"
        )
        self.strategy_blocked_card.set_value(
            str(counts["legacy_invalidated"]), "永久只读审计"
        )
        translations = {
            "research": "研究",
            "paper_shadow": "Paper影子",
            "paused": "暂停",
            "stopped": "停止",
            "legacy_invalidated": "旧结果已失效",
        }
        self.strategy_registry_table.setSortingEnabled(False)
        self.strategy_registry_table.setRowCount(len(records))
        for index, record in enumerate(records):
            values = (
                record.strategy_id,
                record.name,
                record.semver,
                translations.get(record.status, record.status),
                record.mode,
                f"{record.risk_budget_pct:.1%}",
                record.parameter_hash[:12],
                record.universe_hash[:18],
                "通过" if record.gate_passed else "阻断",
                record.updated_at,
                record.description,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(Qt.UserRole, record.version_id)
                if not record.gate_passed and column in {0, 3, 8}:
                    item.setForeground(QColor(self.theme.warning))
                if (
                    record.status == "legacy_invalidated"
                    and column in {0, 1, 3}
                ):
                    item.setForeground(QColor(self.theme.error))
                self.strategy_registry_table.setItem(
                    index, column, item
                )
        self.strategy_registry_table.setSortingEnabled(True)
        if hasattr(self, "shadow_strategy_combo"):
            selected_version = self.shadow_strategy_combo.currentData()
            self.shadow_strategy_combo.blockSignals(True)
            self.shadow_strategy_combo.clear()
            targeted_records = sorted(
                (
                    record
                    for record in records
                    if (
                        record.strategy_id == "intraday-targeted-t"
                        and record.status in {"research", "paper_shadow"}
                    )
                ),
                key=lambda record: record.created_at,
                reverse=True,
            )
            for record in targeted_records:
                    label = (
                        f"{record.name} · {record.semver} · "
                        f"{translations.get(record.status, record.status)}"
                    )
                    self.shadow_strategy_combo.addItem(
                        label, record.version_id
                    )
            restore_index = self.shadow_strategy_combo.findData(
                selected_version
            )
            self.shadow_strategy_combo.setCurrentIndex(
                max(0, restore_index)
            )
            self.shadow_strategy_combo.blockSignals(False)
        if hasattr(self, "auto_strategy_combo"):
            selected_auto = self.auto_strategy_combo.currentData()
            self.auto_strategy_combo.blockSignals(True)
            self.auto_strategy_combo.clear()
            auto_records = sorted(
                (
                    record
                    for record in records
                    if (
                        record.strategy_id
                        == "intraday-auto-rotation"
                        and record.status
                        in {"research", "paper_shadow"}
                    )
                ),
                key=lambda record: record.created_at,
                reverse=True,
            )
            for record in auto_records:
                label = (
                    f"{record.name} · {record.semver} · "
                    f"{translations.get(record.status, record.status)}"
                )
                self.auto_strategy_combo.addItem(
                    label, record.version_id
                )
            restore_auto = self.auto_strategy_combo.findData(
                selected_auto
            )
            self.auto_strategy_combo.setCurrentIndex(
                max(0, restore_auto)
            )
            self.auto_strategy_combo.blockSignals(False)
            self._refresh_auto_quant_preflight()
        if records:
            self.strategy_registry_table.selectRow(0)

    def _selected_strategy_record(
        self,
    ) -> StrategyRecord | None:
        selected = self.strategy_registry_table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        item = self.strategy_registry_table.item(row, 0)
        if item is None:
            return None
        version_id = item.data(Qt.UserRole)
        if not version_id:
            return None
        try:
            return self.strategy_registry.get_version(str(version_id))
        except KeyError:
            return None

    def _selected_shadow_strategy_record(
        self,
    ) -> StrategyRecord | None:
        version_id = self.shadow_strategy_combo.currentData()
        if not version_id:
            return None
        try:
            return self.strategy_registry.get_version(str(version_id))
        except KeyError:
            return None

    def _selected_auto_strategy_record(
        self,
    ) -> StrategyRecord | None:
        version_id = self.auto_strategy_combo.currentData()
        if not version_id:
            return None
        try:
            return self.strategy_registry.get_version(
                str(version_id)
            )
        except KeyError:
            return None

    def _strategy_registry_selection_changed(self) -> None:
        record = self._selected_strategy_record()
        if record is None:
            return
        self.strategy_parameter_editor.setPlainText(
            json.dumps(
                record.parameters,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        self.strategy_new_semver.setText(
            f"{record.semver}.next"
        )
        self.strategy_governance_text.setPlainText(
            f"策略：{record.name}\n"
            f"Strategy ID：{record.strategy_id}\n"
            f"Version ID：{record.version_id}\n"
            f"状态 / 模式：{record.status} / {record.mode}\n"
            f"参数 Hash：{record.parameter_hash}\n"
            f"股票池 Hash：{record.universe_hash}\n"
            f"代码 Hash：{record.code_hash}\n"
            f"风险预算：{record.risk_budget_pct:.1%}\n"
            f"参数约束：{strategy_schema_summary(record.strategy_id)}\n\n"
            f"晋级门：{'通过' if record.gate_passed else '阻断'}\n"
            f"原因：{record.gate_reason}\n\n"
            "自动下单：关闭；本管理器没有订单提交接口。"
        )
        self.strategy_clone_button.setEnabled(
            record.status != "legacy_invalidated"
        )
        self.strategy_shadow_button.setEnabled(
            record.gate_passed
            and record.status in {"research", "paused"}
        )
        self.strategy_pause_button.setEnabled(
            record.status == "paper_shadow"
        )
        self.strategy_stop_button.setEnabled(
            record.status in {"research", "paper_shadow", "paused"}
        )
        if hasattr(self, "shadow_gate_label"):
            if (
                record.strategy_id == "intraday-targeted-t"
                and record.status == "research"
            ):
                self.shadow_gate_label.setText(
                    f"探索性影子模式：已绑定 {record.strategy_id} "
                    f"{record.semver}。可收集实时模拟证据；"
                    "不代表晋级，不会发送券商订单。"
                )
            elif (
                record.gate_passed
                and record.status == "paper_shadow"
            ):
                self.shadow_gate_label.setText(
                    f"策略证据门：通过；已绑定 {record.strategy_id} "
                    f"{record.semver}。仍需新鲜 Paper 账户与实时行情。"
                )
            else:
                self.shadow_gate_label.setText(
                    "策略证据门：硬阻断。"
                    f"{record.strategy_id} {record.semver}："
                    f"{record.gate_reason}"
                )

    def _clone_strategy_version(self) -> None:
        record = self._selected_strategy_record()
        if record is None:
            QMessageBox.warning(
                self, "无法创建", "请先选择一个策略版本"
            )
            return
        semver = self.strategy_new_semver.text().strip()
        if not semver:
            QMessageBox.warning(
                self, "无法创建", "请输入新版本号"
            )
            return
        try:
            parameters = json.loads(
                self.strategy_parameter_editor.toPlainText()
            )
            if not isinstance(parameters, dict):
                raise ValueError("参数必须是 JSON 对象")
            created = self.strategy_registry.clone_version(
                record.version_id,
                semver=semver,
                parameters=parameters,
            )
        except (
            json.JSONDecodeError,
            ValueError,
            StrategyRegistryError,
        ) as error:
            QMessageBox.warning(self, "创建失败", str(error))
            return
        self._populate_strategy_registry()
        self._log(
            f"已创建 {created.strategy_id} {created.semver}；"
            "状态回到研究，需重新验证"
        )

    def _transition_selected_strategy(
        self, target_status: str
    ) -> None:
        record = self._selected_strategy_record()
        if record is None:
            QMessageBox.warning(
                self, "无法变更", "请先选择一个策略版本"
            )
            return
        try:
            changed = self.strategy_registry.transition(
                record.version_id,
                target_status,
                reason="desktop governance action",
            )
        except StrategyRegistryError as error:
            QMessageBox.warning(
                self,
                "晋级门阻断",
                f"{error}\n\n自动下单仍保持关闭。",
            )
            self._log(f"策略状态变更被阻断：{error}")
            return
        self._populate_strategy_registry()
        self._log(
            f"{changed.strategy_id} 已变更为 {changed.status}"
        )
        self._record_runtime_event(
            severity="info",
            component="strategy",
            code="STATUS_CHANGE",
            message=(
                f"{changed.strategy_id} {changed.semver} -> "
                f"{changed.status}"
            ),
        )

    def _account_snapshot_finished(self, result: object) -> None:
        snapshot = result
        if not isinstance(snapshot, IBKRReadOnlySnapshot):
            raise TypeError("unexpected IBKR account snapshot")
        try:
            view = build_portfolio_view(
                snapshot,
                environment="paper",
                exposure_multipliers=(
                    self._configured_exposure_multipliers()
                ),
            )
        except ValueError as error:
            self.handshake_badge.setText("协议 · 已握手")
            self.handshake_badge.setProperty("state", "ok")
            self.account_badge.setText("账户 · 校验失败")
            self.account_badge.setProperty("state", "error")
            self._repolish_health_badges()
            self._task_failed(str(error))
            return
        self.portfolio_view = view
        self.account_ledger.append(view.account)
        self._record_runtime_event(
            severity="info",
            component="account",
            code="SNAPSHOT_OK",
            message=(
                f"{view.account.account_alias} 只读快照完成；"
                f"{len(view.positions)} 个持仓"
            ),
        )
        self._populate_account_view()
        self._refresh_auto_quant_preflight()
        self.handshake_badge.setText("协议 · 已握手")
        self.handshake_badge.setProperty("state", "ok")
        self.account_badge.setText(
            f"Paper · {view.account.account_alias}"
        )
        self.account_badge.setProperty("state", "ok")
        reasons = intraday_market_data_reasons(
            snapshot,
            required_symbols=("SPY", "QQQ"),
        )
        market_errors = [
            message
            for message in snapshot.messages
            if message.code
            in {10089, 10090, 10091, 10167, 10186, 10197}
        ]
        if not reasons:
            self.market_badge.setText("行情 · 实时 Type 1")
            self.market_badge.setProperty("state", "ok")
        elif market_errors:
            code = market_errors[-1].code
            self.market_badge.setText(f"行情 · 错误 {code}")
            self.market_badge.setToolTip(market_errors[-1].message)
            self.market_badge.setProperty("state", "error")
        else:
            self.market_badge.setText("行情 · 非实时/不完整")
            self.market_badge.setToolTip("；".join(reasons))
            self.market_badge.setProperty("state", "warn")
        self._repolish_health_badges()
        self._log(
            f"已读取 {view.account.account_alias}："
            f"{len(view.positions)} 个持仓；"
            f"行情 {'可用于日内' if not reasons else '不可用于日内'}"
        )

    def _populate_account_view(self) -> None:
        if self.portfolio_view is None:
            return
        account = self.portfolio_view.account
        self.account_nlv_card.set_value(
            _money(account.net_liquidation),
            f"IBKR Paper · {account.account_alias}",
        )
        self.account_cash_card.set_value(
            _money(account.cash),
            f"可用资金 {_money(account.available_funds)}",
        )
        self.account_day_pnl_card.set_value(
            _money(account.daily_pnl, signed=True),
            f"{account.pnl_source} · 不与回测收益混合",
        )
        self.account_unrealized_card.set_value(
            _money(account.unrealized_pnl, signed=True),
            (
                f"已实现 {_money(account.realized_pnl, signed=True)}"
            ),
        )
        self.account_status_label.setText(
            f"{account.environment.upper()} · {account.account_alias} · "
            f"采集 {account.observed_at}"
        )
        self.account_detail_label.setText(
            f"购买力 {_money(account.buying_power)} · "
            f"总持仓 {_money(account.gross_position_value)} · "
            f"维持保证金 {_money(account.maintenance_margin)}"
        )
        self.universe_card.set_value(
            _money(account.net_liquidation),
            f"IBKR Paper · {account.account_alias}",
        )
        self.verified_card.set_value(
            _money(account.daily_pnl, signed=True),
            "券商 reqPnL；不含回测",
        )
        self.history_card.set_value(
            str(len(self.portfolio_view.positions)),
            "当前券商整股持仓",
        )

        positions = self.portfolio_view.positions
        self.positions_empty_label.setText(
            "当前账户无持仓"
            if not positions
            else f"共 {len(positions)} 个持仓；过期/非实时 mark 明确标红"
        )
        self.positions_table.setSortingEnabled(False)
        self.positions_table.setRowCount(len(positions))
        data_type_names = {
            1: "实时",
            2: "冻结",
            3: "延迟",
            4: "延迟冻结",
        }
        for index, position in enumerate(positions):
            values = (
                position.symbol,
                str(position.quantity),
                _money(position.average_cost),
                _money(position.mark),
                _money(position.market_value),
                _money(position.risk_exposure),
                _money(position.broker_daily_pnl, signed=True),
                _money(
                    position.broker_unrealized_pnl,
                    signed=True,
                ),
                _money(position.broker_realized_pnl, signed=True),
                data_type_names.get(
                    position.market_data_type, "未知"
                ),
                position.mark_source,
                "STALE" if position.stale else "FRESH",
                position.account_alias,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if position.stale and column in {0, 3, 9, 11}:
                    item.setForeground(QColor(self.theme.error))
                self.positions_table.setItem(index, column, item)
        self.positions_table.setSortingEnabled(True)

        points = self.account_ledger.list_points(
            environment=account.environment,
            account_alias=account.account_alias,
            limit=100,
        )
        self.account_ledger_table.setSortingEnabled(False)
        self.account_ledger_table.setRowCount(len(points))
        for index, point in enumerate(points):
            values = (
                point.observed_at,
                point.environment,
                point.account_alias,
                _money(point.net_liquidation),
                _money(point.cash),
                _money(point.daily_pnl, signed=True),
                _money(point.unrealized_pnl, signed=True),
            )
            for column, value in enumerate(values):
                self.account_ledger_table.setItem(
                    index,
                    column,
                    QTableWidgetItem(value),
                )
        self.account_ledger_table.setSortingEnabled(True)
        if self.auto_quant_snapshot is not None:
            self._populate_auto_quant_snapshot(
                self.auto_quant_snapshot
            )
        self._refresh_target_preflight()

    def _repolish_health_badges(self) -> None:
        for badge in (
            self.gateway_badge,
            self.handshake_badge,
            self.account_badge,
            self.market_badge,
        ):
            badge.style().unpolish(badge)
            badge.style().polish(badge)

    def _stream_symbols_from_input(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                item.strip().upper()
                for item in self.stream_symbols.text().split(",")
                if item.strip()
            )
        )

    def _settings_provider_selected(
        self, *_args: object
    ) -> None:
        provider = str(
            self.settings_provider_combo.currentData()
            or "finnhub_trades"
        )
        stream_index = self.stream_mode.findData(provider)
        if (
            stream_index >= 0
            and self.stream_mode.currentIndex() != stream_index
        ):
            self.stream_mode.blockSignals(True)
            self.stream_mode.setCurrentIndex(stream_index)
            self.stream_mode.blockSignals(False)
        api_provider = (
            "ibkr" if provider == "ibkr_extended" else provider
        )
        api_index = self.settings_api_provider_combo.findData(api_provider)
        if api_index >= 0:
            self.settings_api_provider_combo.setCurrentIndex(api_index)

    def _stream_provider_selected(
        self, *_args: object
    ) -> None:
        provider = str(
            self.stream_mode.currentData() or "finnhub_trades"
        )
        settings_index = self.settings_provider_combo.findData(
            provider
        )
        if (
            settings_index >= 0
            and self.settings_provider_combo.currentIndex()
            != settings_index
        ):
            self.settings_provider_combo.blockSignals(True)
            self.settings_provider_combo.setCurrentIndex(
                settings_index
            )
            self.settings_provider_combo.blockSignals(False)

    def _switch_to_settings_provider(self) -> None:
        provider = str(
            self.settings_provider_combo.currentData()
            or "finnhub_trades"
        )
        self._save_user_preferences()
        if not self._stream_symbols_from_input():
            target_index = self.stream_mode.findData(provider)
            if target_index >= 0:
                self.stream_mode.blockSignals(True)
                self.stream_mode.setCurrentIndex(target_index)
                self.stream_mode.blockSignals(False)
            self._pending_stream_switch = None
            self._log(
                "默认行情源已切换；当前没有订阅代码。"
                "请在“监控台 → 行情监控”输入任意股票或 ETF 后启动行情。"
            )
            return
        self._request_stream_switch(provider)

    def _request_stream_switch(
        self,
        provider: str,
        *,
        allow_auto_session_switch: bool = False,
    ) -> None:
        symbols = self._stream_symbols_from_input()
        if not symbols:
            QMessageBox.warning(
                self,
                "无法切换行情",
                "请先在“监控台 → 实时行情”填写至少一个代码，"
                "或从广域扫描载入候选。",
            )
            return
        if len(symbols) > 30:
            QMessageBox.warning(
                self, "无法切换行情", "首期最多订阅 30 个代码"
            )
            return
        if (
            self.auto_quant_snapshot is not None
            and (
                self.auto_quant_snapshot.active
                or self.auto_quant_snapshot.positions
                or self.auto_quant_snapshot.pending_orders
            )
            and not allow_auto_session_switch
        ):
            QMessageBox.warning(
                self,
                "自动量化会话仍在运行",
                "Paper 持仓或在途订单存在时禁止切换行情源。"
                "请先停止自动量化并完成券商对账。",
            )
            return
        target_index = self.stream_mode.findData(provider)
        if target_index < 0:
            QMessageBox.warning(
                self, "无法切换行情", f"不支持的数据源：{provider}"
            )
            return
        self.stream_mode.blockSignals(True)
        self.stream_mode.setCurrentIndex(target_index)
        self.stream_mode.blockSignals(False)
        self._pending_stream_switch = (provider, symbols)
        worker = self.stream_worker
        if worker is not None and worker.isRunning():
            self._log(
                f"正在停止 {worker.provider}，随后切换到 {provider}…"
            )
            if not self._stop_stream(
                preserve_pending=True,
                allow_auto_session_switch=allow_auto_session_switch,
            ):
                return
        self._activate_pending_stream_switch()

    def _activate_pending_stream_switch(self) -> None:
        pending = self._pending_stream_switch
        if pending is None:
            return
        if (
            self.stream_worker is not None
            and self.stream_worker.isRunning()
        ):
            return
        provider, symbols = pending
        self._pending_stream_switch = None
        index = self.stream_mode.findData(provider)
        if index >= 0:
            self.stream_mode.blockSignals(True)
            self.stream_mode.setCurrentIndex(index)
            self.stream_mode.blockSignals(False)
        self.stream_symbols.setText(",".join(symbols))
        self._start_stream()

    def _maybe_rotate_extended_ibkr_session(self) -> None:
        self._refresh_extended_hours_status()
        worker = self.stream_worker
        if (
            worker is None
            or not worker.isRunning()
            or worker.provider != "ibkr_extended"
            or self._pending_stream_switch is not None
        ):
            return
        desired_exchange = ibkr_market_data_exchange()
        if desired_exchange == worker.market_exchange:
            return
        self._log(
            "美东时段切换：正在把 IBKR 5×24 行情从 "
            f"{worker.market_exchange} 切换到 {desired_exchange}…"
        )
        self._request_stream_switch(
            "ibkr_extended",
            allow_auto_session_switch=True,
        )

    def _refresh_extended_hours_status(self) -> None:
        if not hasattr(self, "auto_session_label"):
            return
        routing = paper_order_routing(
            extended_hours_enabled=(
                self.preferences.extended_hours_paper_enabled
            )
        )
        if self.preferences.extended_hours_paper_enabled:
            status = "已启用" if routing.allowed else "当前不可交易"
        else:
            status = "未启用（仅常规时段）"
        self.auto_session_label.setText(
            f"当前美东时段：{routing.label} · 5×24 Paper：{status} · "
            f"{routing.reason}"
        )

    def _start_stream(self) -> None:
        if (
            self.stream_worker is not None
            and self.stream_worker.isRunning()
        ):
            self._request_stream_switch(
                str(
                    self.stream_mode.currentData()
                    or "finnhub_trades"
                )
            )
            return
        symbols = self._stream_symbols_from_input()
        if not symbols:
            QMessageBox.warning(
                self, "无法启动", "至少填写一个行情代码"
            )
            return
        if len(symbols) > 30:
            QMessageBox.warning(
                self, "无法启动", "首期最多订阅 30 个代码"
            )
            return
        provider = str(self.stream_mode.currentData() or "ibkr")
        market_exchange = (
            ibkr_market_data_exchange()
            if provider == "ibkr_extended"
            else "SMART"
        )
        try:
            finnhub_key = self._load_stream_credential(
                "finnhub_api_key", "FINNHUB_API_KEY"
            )
            alpaca_key = self._load_stream_credential(
                "alpaca_api_key", "APCA_API_KEY_ID"
            )
            alpaca_secret = self._load_stream_credential(
                "alpaca_api_secret", "APCA_API_SECRET_KEY"
            )
            worker = StreamWorker(
                self.config.ibkr,
                symbols=symbols,
                provider=provider,
                api_key=alpaca_key,
                api_secret=alpaca_secret,
                finnhub_key=finnhub_key,
                market_exchange=market_exchange,
            )
        except (
            AlpacaCredentialsMissing,
            FinnhubCredentialsMissing,
            ValueError,
        ) as error:
            self._task_failed(str(error))
            return
        self.stream_worker = worker
        self._quote_last_ready_monotonic.clear()
        self._last_stream_status_key = None
        self._last_stream_status_log_at = 0.0
        worker.failed.connect(self._stream_failed)
        worker.finished.connect(self._stream_finished)
        self.stream_start_button.setEnabled(True)
        self.stream_start_button.setText("切换 / 重连行情")
        self.stream_stop_button.setEnabled(True)
        if hasattr(self, "auto_stop_stream_button"):
            self.auto_stop_stream_button.setEnabled(True)
        self.stream_symbols.setEnabled(False)
        self.stream_mode.setEnabled(True)
        self.stream_scan_watchlist_button.setEnabled(False)
        self._set_connection_settings_enabled(False)
        self.stream_watch_card.set_value(
            str(len(symbols)), "Level I 持续订阅"
        )
        self.stream_connection_card.set_value(
            "连接中",
            (
                "等待 nextValidId 协议握手"
                if provider in {"ibkr", "ibkr_extended"}
                else "等待 WebSocket 认证/首个事件"
            ),
        )
        worker.start()
        self.stream_timer.start()
        self._record_runtime_event(
            severity="info",
            component="market_data",
            code="STREAM_START",
            message=(
                f"{provider} 只读流启动；{len(symbols)} 个代码；"
                f"IBKR 路由 {market_exchange}"
            ),
        )
        route_note = (
            f" · 当前 IBKR 行情路由 {market_exchange}"
            if provider == "ibkr_extended"
            else ""
        )
        self._log(f"{provider} 只读流行情正在连接…{route_note}")

    def _stop_stream(
        self,
        *_args: object,
        preserve_pending: bool = False,
        allow_auto_session_switch: bool = False,
    ) -> bool:
        if not preserve_pending:
            self._pending_stream_switch = None
        worker = self.stream_worker
        if worker is None:
            return True
        if (
            self.auto_quant_snapshot is not None
            and (
                self.auto_quant_snapshot.active
                or self.auto_quant_snapshot.positions
                or self.auto_quant_snapshot.pending_orders
            )
            and not allow_auto_session_switch
        ):
            QMessageBox.warning(
                self,
                "自动量化会话仍在运行",
                "必须先在“自动量化”点击停止，并等待 Paper 持仓和"
                "在途订单完成对账后才能停止行情。",
            )
            return False
        if self.shadow_engine is not None and self.shadow_engine.active:
            self._stop_shadow()
        self._invalidate_stream_snapshot("行情流正在停止")
        worker.request_stop()
        if not worker.wait(3000):
            self._log("流服务正在退出；等待网络线程关闭…")
            self.stream_timer.stop()
            self.stream_connection_card.set_value(
                "停止中", "网络线程尚未确认退出；禁止重复启动"
            )
            self.stream_stop_button.setEnabled(False)
            if hasattr(self, "auto_stop_stream_button"):
                self.auto_stop_stream_button.setEnabled(False)
            self._record_runtime_event(
                severity="warning",
                component="market_data",
                code="STREAM_STOP_PENDING",
                message="流行情停止超过3秒，保持启动门关闭",
            )
            return False
        self.stream_timer.stop()
        if self.stream_worker is worker:
            self.stream_worker = None
        self.stream_connection_card.set_value(
            "已停止", "最后行情保留为 stale"
        )
        self.stream_start_button.setEnabled(True)
        self.stream_start_button.setText("启动只读流行情")
        self.stream_stop_button.setEnabled(False)
        if hasattr(self, "auto_stop_stream_button"):
            self.auto_stop_stream_button.setEnabled(False)
        self.stream_symbols.setEnabled(True)
        self.stream_mode.setEnabled(True)
        self.stream_scan_watchlist_button.setEnabled(True)
        self._set_connection_settings_enabled(True)
        self._record_runtime_event(
            severity="info",
            component="market_data",
            code="STREAM_STOP",
            message="流行情已请求停止",
        )
        return True

    def _invalidate_stream_snapshot(self, reason: str) -> None:
        snapshot = self.stream_snapshot
        if snapshot is not None:
            invalid_quotes = tuple(
                replace(
                    quote,
                    stale=True,
                    stale_reason=reason,
                )
                for quote in snapshot.quotes
            )
            snapshot = replace(
                snapshot,
                socket_connected=False,
                handshake_complete=False,
                quotes=invalid_quotes,
                last_message=reason,
                observed_at=datetime.now(timezone.utc).isoformat(),
            )
            self.stream_snapshot = snapshot
            self._populate_stream_snapshot(snapshot)
        self.market_badge.setText("行情 · 已停止")
        self.market_badge.setProperty("state", "warn")
        self.signal_card.set_value("不可用", reason)
        self._repolish_health_badges()

    def _stream_snapshot_received(self, result: object) -> None:
        if not isinstance(result, StreamSnapshot):
            return
        self.stream_snapshot = result
        self._record_minute_snapshot(result)
        if result.last_error_code is not None:
            event_key = (
                result.generation,
                result.last_error_code,
            )
            if event_key != self._last_stream_event_key:
                self._last_stream_event_key = event_key
                self._record_runtime_event(
                    severity="error",
                    component="market_data",
                    code=str(result.last_error_code),
                    message=result.last_message,
                )
        self._populate_stream_snapshot(result)
        self._populate_auto_quant_candidates()
        self._refresh_target_preflight()
        self._poll_auto_quant_orders()
        if (
            self.auto_quant_engine is not None
            and self.auto_quant_engine.active
        ):
            self.auto_quant_snapshot = (
                self.auto_quant_engine.on_stream(result)
            )
            self._populate_auto_quant_snapshot(
                self.auto_quant_snapshot
            )
            self._finish_auto_quant_session_if_safe()
        if self.shadow_engine is not None and self.shadow_engine.active:
            self.shadow_snapshot = self.shadow_engine.on_stream(result)
            self._populate_shadow_snapshot(self.shadow_snapshot)

    def _record_minute_snapshot(
        self, snapshot: StreamSnapshot
    ) -> None:
        symbols_to_record: set[str] = set()
        for quote in snapshot.quotes:
            timestamp = quote.updated_at or snapshot.observed_at
            try:
                observed = datetime.fromisoformat(
                    timestamp.replace("Z", "+00:00")
                )
            except ValueError:
                continue
            minute = observed.replace(
                second=0, microsecond=0
            ).isoformat()
            key = (quote.provider, quote.symbol, minute)
            previous_ready = self._minute_recorded_keys.get(key)
            if previous_ready is None or (
                quote.realtime_ready and not previous_ready
            ):
                symbols_to_record.add(quote.symbol)
                self._minute_recorded_keys[key] = quote.realtime_ready
        if len(self._minute_recorded_keys) > 5000:
            self._minute_recorded_keys.clear()
        if not symbols_to_record:
            return
        try:
            self.minute_quote_store.record_snapshot(
                snapshot, symbols=symbols_to_record
            )
        except (OSError, sqlite3.Error) as error:
            self._record_runtime_event(
                severity="warning",
                component="minute_data",
                code="MINUTE_PERSIST_FAILED",
                message=str(error),
            )
            return
        target = self._current_target_symbol()
        if target in symbols_to_record:
            self._refresh_minute_data_status(target)

    def _poll_stream_snapshot(self) -> None:
        worker = self.stream_worker
        if worker is None or not worker.isRunning():
            return
        self._stream_snapshot_received(worker.service.snapshot())

    def _populate_stream_snapshot(
        self, snapshot: StreamSnapshot
    ) -> None:
        if self._quotes_scroll_active:
            self._pending_stream_snapshot = snapshot
            return
        self.stream_empty_label.setVisible(not snapshot.quotes)
        current_ready_count, recent_ready_count = (
            self._update_quote_readiness(snapshot)
        )
        connection_text = (
            "已握手"
            if snapshot.handshake_complete
            else "端口已连"
            if snapshot.socket_connected
            else "已断开"
        )
        self.stream_connection_card.set_value(
            connection_text,
            f"连接代次 {snapshot.generation} · "
            f"尝试 {snapshot.reconnect_attempt}",
        )
        effective_types = {
            quote.effective_market_data_type
            for quote in snapshot.quotes
            if quote.effective_market_data_type is not None
        }
        if snapshot.provider == "Alpaca":
            feed_text = "IEX 实时"
        elif snapshot.provider == "Finnhub":
            feed_text = "实时成交"
        elif effective_types:
            feed_text = " / ".join(
                MARKET_DATA_TYPE_NAMES.get(value, str(value))
                for value in sorted(effective_types)
            )
        else:
            feed_text = "等待回调"
        self.stream_feed_card.set_value(
            feed_text,
            snapshot.coverage,
        )
        self.stream_ready_card.set_value(
            f"{current_ready_count}/{len(snapshot.quotes)}",
            (
                f"当前 fresh；近30秒覆盖 "
                f"{recent_ready_count}/{len(snapshot.quotes)}"
            ),
        )

        self.quotes_model.update_snapshot(snapshot)

        error_line = (
            f"最近错误：{snapshot.provider} {snapshot.last_error_code}"
            if snapshot.last_error_code is not None
            else "最近错误：无"
        )
        gate_explanation = (
            "门控：Finnhub 只把 fresh 实时成交用于信号；"
            "显示的 bid/ask 是 ±5bps 影子执行带，不是市场盘口。"
            if snapshot.provider == "Finnhub"
            else (
                "门控：只有 fresh Type 1 且 bid/ask 完整的行情，"
                "才可被标记为日内可用。Type 2/3/4 不会静默升级。"
            )
        )
        self.stream_health_text.setPlainText(
            f"连接代次：{snapshot.generation}\n"
            f"来源：{snapshot.provider}\n"
            f"覆盖：{snapshot.coverage}\n"
            f"Socket：{'连接' if snapshot.socket_connected else '断开'}\n"
            f"协议握手：{'完成' if snapshot.handshake_complete else '未完成'}\n"
            f"{error_line}\n"
            f"最近事件：{snapshot.last_message}\n\n"
            f"{gate_explanation}"
        )
        if snapshot.handshake_complete:
            self.handshake_badge.setText(
                (
                    f"{snapshot.provider} · 已认证"
                    if snapshot.provider in {"Alpaca", "Finnhub"}
                    else "协议 · 已握手"
                )
            )
            self.handshake_badge.setProperty("state", "ok")
        if snapshot.realtime_ready:
            self.market_badge.setText(
                (
                    (
                        "行情 · IEX 实时"
                        if snapshot.provider == "Alpaca"
                        else "行情 · Finnhub 成交"
                    )
                    if snapshot.provider in {"Alpaca", "Finnhub"}
                    else "行情 · 实时 Type 1"
                )
            )
            self.market_badge.setProperty("state", "ok")
        elif snapshot.last_error_code is not None:
            self.market_badge.setText(
                f"行情 · 错误 {snapshot.last_error_code}"
            )
            self.market_badge.setProperty("state", "error")
        else:
            self.market_badge.setText("行情 · 未达日内门槛")
            self.market_badge.setProperty("state", "warn")
        self.signal_card.set_value(
            "可用" if snapshot.realtime_ready else "不可用",
            (
                (
                    (
                        "Alpaca IEX 单交易所实时"
                        if snapshot.provider == "Alpaca"
                        else "Finnhub 实时成交+明确模拟执行带"
                    )
                    if snapshot.provider in {"Alpaca", "Finnhub"}
                    else "fresh Type 1 + bid/ask"
                )
                if snapshot.realtime_ready
                else snapshot.last_message[:42]
            ),
        )
        self._repolish_health_badges()
        status_key = (
            snapshot.provider,
            snapshot.generation,
            snapshot.handshake_complete,
            snapshot.last_error_code,
        )
        now_monotonic = monotonic()
        if (
            status_key != self._last_stream_status_key
            or now_monotonic - self._last_stream_status_log_at >= 30
        ):
            self._last_stream_status_key = status_key
            self._last_stream_status_log_at = now_monotonic
            if snapshot.last_error_code is not None:
                self._log(
                    f"{snapshot.provider} 行情错误 "
                    f"{snapshot.last_error_code}：{snapshot.last_message}"
                )
            elif snapshot.handshake_complete:
                self._log(
                    f"{snapshot.provider} 已连接 · 当前 fresh "
                    f"{current_ready_count}/{len(snapshot.quotes)} · "
                    f"近30秒覆盖 {recent_ready_count}/"
                    f"{len(snapshot.quotes)}"
                )
            else:
                self._log(
                    f"{snapshot.provider} 正在连接（第 "
                    f"{snapshot.reconnect_attempt} 次）…"
                )

    def _update_quote_readiness(
        self, snapshot: StreamSnapshot
    ) -> tuple[int, int]:
        now_monotonic = monotonic()
        current_symbols = {
            quote.symbol
            for quote in snapshot.quotes
            if quote.realtime_ready
        }
        for symbol in current_symbols:
            self._quote_last_ready_monotonic[symbol] = now_monotonic
        subscribed = {quote.symbol for quote in snapshot.quotes}
        self._quote_last_ready_monotonic = {
            symbol: observed
            for symbol, observed in self._quote_last_ready_monotonic.items()
            if symbol in subscribed
            and now_monotonic - observed <= 30
        }
        return (
            len(current_symbols),
            len(self._quote_last_ready_monotonic),
        )

    def _quote_was_recently_ready(self, symbol: str) -> bool:
        observed = self._quote_last_ready_monotonic.get(symbol)
        return observed is not None and monotonic() - observed <= 30

    def _start_shadow(self) -> None:
        if (
            self.auto_quant_engine is not None
            and self.auto_quant_engine.active
        ):
            QMessageBox.warning(
                self,
                "IBKR Paper 自动量化运行中",
                "同一资金真值不能同时运行自动量化和内部仿真。",
            )
            return
        strategy = self._selected_shadow_strategy_record()
        if strategy is None:
            QMessageBox.warning(
                self,
                "请选择策略版本",
                "请先在“策略目录与版本”中选择指定标的日内 T 版本。",
            )
            return
        if (
            strategy.strategy_id != "intraday-targeted-t"
        ):
            QMessageBox.warning(
                self,
                "策略类型不匹配",
                (
                    "针对性日内 T 必须绑定“指定标的日内 T”策略版本。"
                    "请先在“策略目录与版本”中选择该策略。"
                ),
            )
            return
        if (
            strategy.status not in {"research", "paper_shadow"}
            or (
                strategy.status == "paper_shadow"
                and not strategy.gate_passed
            )
        ):
            QMessageBox.warning(
                self,
                "策略状态不可运行",
                (
                    "只有 research 探索版本或已通过证据门的 Paper Shadow "
                    "版本可以运行内部影子盘。停止或暂停版本不能启动。"
                ),
            )
            return
        paper_capital = self._paper_simulation_capital()
        if paper_capital is None:
            QMessageBox.warning(
                self,
                "缺少 IBKR Paper 资金真值",
                (
                    "请先在“账户与持仓”页读取 IBKR Paper 账户。"
                    "模拟盘必须使用券商返回的 NetLiquidation 建账，"
                    "不会用任何历史研究资金情景代替。"
                ),
            )
            return
        stream = self.stream_snapshot
        stream_running = (
            self.stream_worker is not None
            and self.stream_worker.isRunning()
        )
        if (
            not stream_running
            or stream is None
            or not stream.realtime_ready
        ):
            QMessageBox.warning(
                self,
                "行情门未通过",
                (
                    "请先在“实时行情”页启动外部实时流，并等待至少一个"
                    "代码显示 READY。延迟或 stale 行情不能启动日内影子盘。"
                ),
            )
            return
        if self.universe is None:
            QMessageBox.warning(
                self,
                "标的门未通过",
                "缺少已核验标的池，无法执行“不做中概股”硬过滤。",
            )
            return
        target_symbol = self._current_target_symbol()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", target_symbol):
            QMessageBox.warning(
                self,
                "请输入标的",
                "针对性日内 T 需要输入一个有效的美股或 ETF 代码。",
            )
            return
        research_eligible = {
            row.symbol
            for row in self.universe.records
            if row.eligible_for_research
        }
        if target_symbol not in research_eligible:
            QMessageBox.warning(
                self,
                "标的门未通过",
                (
                    f"{target_symbol} 不在当前已排除中概风险的研究标的池内。"
                    "请先刷新广域标的池与国家证据。"
                ),
            )
            return
        target_quote = next(
            (
                quote
                for quote in stream.quotes
                if quote.symbol == target_symbol
            ),
            None,
        )
        if target_quote is None or not target_quote.realtime_ready:
            QMessageBox.warning(
                self,
                "目标行情未就绪",
                (
                    f"{target_symbol} 尚未获得 fresh bid/ask。"
                    "请先点击“订阅该标的行情”，等行情页显示 READY。"
                ),
            )
            return
        stream_symbols = (target_symbol,)
        engine = ShadowPaperEngine(
            store=self.shadow_store,
            allowed_symbols=stream_symbols,
            config=build_targeted_shadow_config(
                strategy.parameters,
                initial_cash=paper_capital,
                capital_source=(
                    "IBKR Paper "
                    f"{self.portfolio_view.account.account_alias} "
                    "NetLiquidation"
                ),
                daily_loss_limit=paper_capital * Decimal("0.01"),
                symbol_risk_multipliers=(
                    self._configured_exposure_multipliers()
                ),
            ),
            strategy_version_id=strategy.version_id,
            parameter_hash=strategy.parameter_hash,
            target_symbol=target_symbol,
        )
        self.shadow_engine = engine
        self.shadow_snapshot = engine.start()
        self._populate_shadow_snapshot(self.shadow_snapshot)
        self.shadow_start_button.setEnabled(False)
        self.shadow_stop_button.setEnabled(True)
        self.target_symbol_input.setEnabled(False)
        self.target_symbol_apply_button.setEnabled(False)
        self.target_symbol_subscribe_button.setEnabled(False)
        self.shadow_strategy_combo.setEnabled(False)
        self._record_runtime_event(
            severity="info",
            component="shadow_paper",
            code="SHADOW_START",
            message=(
                f"针对性日内 T 影子盘启动；模式 {strategy.status}；"
                f"标的 {target_symbol}；"
                f"初始资金 {_money(paper_capital)} 来自 IBKR Paper "
                "NetLiquidation；无券商订单权限"
            ),
        )
        self._log(
            f"针对性日内 T 影子盘已启动：{target_symbol}，"
            f"策略 {strategy.semver}；资金 {_money(paper_capital)} "
            "来自 IBKR Paper；等待分钟预热。"
        )

    def _stop_shadow(self) -> None:
        engine = self.shadow_engine
        if engine is None:
            return
        self.shadow_snapshot = engine.stop()
        self._populate_shadow_snapshot(self.shadow_snapshot)
        self.shadow_start_button.setEnabled(True)
        self.shadow_stop_button.setEnabled(False)
        self.target_symbol_input.setEnabled(True)
        self.target_symbol_apply_button.setEnabled(True)
        self.target_symbol_subscribe_button.setEnabled(True)
        self.shadow_strategy_combo.setEnabled(True)
        self._record_runtime_event(
            severity="info",
            component="shadow_paper",
            code="SHADOW_STOP",
            message="内部影子盘停止；最后持仓按最后有效 mark 影子平仓",
        )

    def _populate_shadow_snapshot(
        self, snapshot: ShadowSnapshot
    ) -> None:
        self.shadow_status_card.set_value(
            "运行中" if snapshot.active else "已停止",
            (
                f"{snapshot.target_symbol or '未选标的'} · "
                "内部影子成交；券商订单 0"
            ),
        )
        self.shadow_equity_card.set_value(
            _money(snapshot.equity),
            (
                f"现金 {_money(snapshot.cash)} · "
                f"初始 {_money(snapshot.initial_cash)}"
            ),
        )
        self.shadow_realized_card.set_value(
            _money(snapshot.realized_pnl, signed=True),
            (
                "累计；当日 "
                f"{_money(snapshot.daily_realized_pnl, signed=True)}"
            ),
        )
        self.shadow_unrealized_card.set_value(
            _money(snapshot.unrealized_pnl, signed=True),
            "不等于 IBKR 账户盈亏",
        )
        self.shadow_trade_card.set_value(
            f"{snapshot.trades_today} / 4",
            f"影子成交 {len(snapshot.fills)} 笔",
        )
        self.shadow_explanation.setText(
            f"状态：{snapshot.status}\n"
            f"会话：{snapshot.session_id or '无'} · "
            f"策略版本：{snapshot.strategy_version_id} · "
            f"参数：{snapshot.parameter_hash[:12]} · "
            f"目标：{snapshot.target_symbol or '未设置'} · "
            f"交易日：{snapshot.trading_day or '未开始'} · "
            f"资金来源：{snapshot.capital_source} · "
            "环境：internal_shadow · 券商订单接口：不存在"
        )

        self.shadow_position_table.setRowCount(len(snapshot.positions))
        for row_index, position in enumerate(snapshot.positions):
            values = (
                position.symbol,
                str(position.quantity),
                _price(position.entry_price),
                position.opened_at,
                _price(position.high_water),
                position.provider,
                position.coverage,
                "内部影子",
            )
            for column, value in enumerate(values):
                self.shadow_position_table.setItem(
                    row_index, column, QTableWidgetItem(value)
                )

        fills = tuple(reversed(snapshot.fills[-200:]))
        self.shadow_fill_table.setRowCount(len(fills))
        for row_index, fill in enumerate(fills):
            values = (
                fill.occurred_at,
                fill.symbol,
                fill.side,
                str(fill.quantity),
                _price(fill.price),
                _money(fill.commission),
                (
                    _money(fill.realized_pnl, signed=True)
                    if fill.realized_pnl is not None
                    else "—"
                ),
                fill.reason,
                fill.provider,
                fill.coverage,
                fill.session_id[:10],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if fill.side == "BUY" and column in {1, 2}:
                    item.setForeground(QColor(self.theme.success))
                if fill.side == "SELL" and column in {1, 2}:
                    item.setForeground(QColor(self.theme.warning))
                self.shadow_fill_table.setItem(
                    row_index, column, item
                )

    def _stream_failed(self, message: str) -> None:
        self.stream_health_text.setPlainText(
            f"流服务失败：{message}\n\n可继续离线研究。"
        )
        self.market_badge.setText("行情 · 流服务失败")
        self.market_badge.setProperty("state", "error")
        self._repolish_health_badges()
        self._log(f"流服务失败：{message}")
        self._record_runtime_event(
            severity="error",
            component="market_data",
            code="STREAM_FAILED",
            message=message,
        )

    def _stream_finished(self) -> None:
        sender = self.sender()
        if (
            self.stream_worker is not None
            and sender is not self.stream_worker
        ):
            return
        self.stream_worker = None
        self._invalidate_stream_snapshot("行情流已停止")
        self.stream_timer.stop()
        self.stream_start_button.setEnabled(True)
        self.stream_start_button.setText("启动只读流行情")
        self.stream_stop_button.setEnabled(False)
        if hasattr(self, "auto_stop_stream_button"):
            self.auto_stop_stream_button.setEnabled(False)
        self.stream_symbols.setEnabled(True)
        self.stream_mode.setEnabled(True)
        self.stream_scan_watchlist_button.setEnabled(True)
        self._set_connection_settings_enabled(True)
        self._activate_pending_stream_switch()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        running_tasks = [
            worker for worker in self.workers if worker.isRunning()
        ]
        if running_tasks:
            event.ignore()
            QMessageBox.information(
                self,
                "后台任务仍在运行",
                (
                    f"仍有 {len(running_tasks)} 个数据/研究任务运行中。"
                    "为防止半写入产物，任务完成后再关闭程序。"
                ),
            )
            return
        if (
            self.auto_quant_snapshot is not None
            and (
                self.auto_quant_snapshot.active
                or self.auto_quant_snapshot.positions
                or self.auto_quant_snapshot.pending_orders
            )
        ):
            self._stop_auto_quant()
            event.ignore()
            QMessageBox.information(
                self,
                "Paper 自动量化正在安全停止",
                "已登记停止请求。必须等待在途单结束和持仓平仓回报，"
                "客户端不会在未对账时直接退出。",
            )
            return
        if self.paper_order_service is not None:
            self.paper_order_service.disconnect()
            self.paper_order_service = None
        if self.shadow_engine is not None and self.shadow_engine.active:
            self.shadow_engine.stop()
        self._stop_stream()
        if (
            self.stream_worker is not None
            and self.stream_worker.isRunning()
        ):
            event.ignore()
            QMessageBox.information(
                self,
                "行情线程正在停止",
                "行情网络线程尚未确认退出，请稍后再次关闭程序。",
            )
            return
        event.accept()

    def _populate_artifact_table(self) -> None:
        translations = {
            "research_exploratory": "探索性研究",
            "legacy_invalidated": "旧结果·已失效",
            "load_error": "读取失败",
        }
        artifacts = self.artifact_catalog.artifacts
        self.artifact_table.setSortingEnabled(False)
        self.artifact_table.setRowCount(len(artifacts))
        for index, artifact in enumerate(artifacts):
            limitations = "；".join(artifact.limitations[:3]) or "无"
            values = (
                artifact.artifact_type,
                translations.get(artifact.status, artifact.status),
                artifact.data_as_of or "未知",
                artifact.generated_at or "未知",
                artifact.source,
                artifact.run_id[:12],
                limitations,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if artifact.status == "legacy_invalidated" and column in {
                    0,
                    1,
                }:
                    item.setForeground(QColor(self.theme.warning))
                if artifact.status == "load_error":
                    item.setForeground(QColor(self.theme.error))
                self.artifact_table.setItem(index, column, item)
        self.artifact_table.setSortingEnabled(True)

    def _populate_universe_table(self, *args) -> None:  # type: ignore[no-untyped-def]
        del args
        if self.universe is None:
            self.universe_table.setRowCount(0)
            self.universe_count_label.setText("显示 0 / 0")
            return
        mode = self.universe_filter.currentText()
        search = self.universe_search.text().strip().lower()
        rows = []
        for row in self.universe.records:
            if mode == "非中概研究池" and not row.eligible_for_research:
                continue
            if mode == "可交易核心池" and not row.eligible_for_trading:
                continue
            if mode == "已排除" and row.eligible_for_research:
                continue
            haystack = f"{row.symbol} {row.name} {row.sector}".lower()
            if search and search not in haystack:
                continue
            rows.append(row)
        matched_count = len(rows)
        rows = rows[:2500]
        self.universe_count_label.setText(
            f"显示 {len(rows):,} / 匹配 {matched_count:,}"
            + ("（界面上限 2,500）" if matched_count > 2500 else "")
        )
        self.universe_table.setSortingEnabled(False)
        self.universe_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (
                row.symbol,
                row.name,
                row.exchange,
                row.security_type,
                row.sector,
                str(row.leader_tier or "—"),
                (
                    f"{row.country_status} "
                    f"[{row.country_evidence_level}]"
                ),
                (
                    "研究+交易"
                    if row.eligible_for_trading
                    else "仅研究"
                    if row.eligible_for_research
                    else "关闭"
                ),
                row.exclusion_reason or "已通过",
            )
            for column, value in enumerate(values):
                self.universe_table.setItem(
                    index,
                    column,
                    QTableWidgetItem(value),
                )
        self.universe_table.setSortingEnabled(True)

    def _populate_scan_table(self, *args) -> None:  # type: ignore[no-untyped-def]
        del args
        if self.scan is None:
            self.scan_table.setRowCount(0)
            if hasattr(self, "scan_coverage_label"):
                self.scan_coverage_label.setText(
                    "尚无扫描结果。先在“数据任务”把全部研究池加入"
                    "历史队列并分批补齐日 K，再运行扫描。"
                )
            return
        mode = self.scan_filter.currentText()
        search = self.scan_search.text().strip().lower()
        rows = []
        for row in self.scan.results:
            if mode == "趋势候选" and row.signal != "趋势候选":
                continue
            if mode == "可交易资格" and not row.trade_eligible:
                continue
            if mode == "仅龙头" and row.leader_tier != 1:
                continue
            haystack = f"{row.symbol} {row.name} {row.sector}".lower()
            if search and search not in haystack:
                continue
            rows.append(row)
        research_count = (
            int(self.universe.summary()["research_eligible"])
            if self.universe is not None
            else len(self.scan.results) + len(self.scan.skipped)
        )
        if hasattr(self, "scan_coverage_label"):
            self.scan_coverage_label.setText(
                f"当前显示 {len(rows):,} · 最近实际扫描 "
                f"{len(self.scan.results):,} · 缺少/不足 200 根日 K "
                f"{len(self.scan.skipped):,} · 非中概研究池 "
                f"{research_count:,}。筛选器只改变显示，不改变扫描范围。"
            )
        self.scan_table.setSortingEnabled(False)
        self.scan_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (
                row.symbol,
                row.execution_symbol,
                row.sector,
                str(row.leader_tier),
                row.signal,
                f"{row.score:.1f}",
                f"${row.close:,.2f}",
                str(row.whole_share_capacity),
                f"{row.return_20d:+.1%}",
                f"{row.return_63d:+.1%}",
                f"{row.volatility_20d:.1%}",
                f"{row.rsi_14d:.1f}",
                row.reason,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if row.signal == "趋势候选" and column in {0, 4, 5}:
                    item.setForeground(QColor(self.theme.success))
                self.scan_table.setItem(index, column, item)
        self.scan_table.setSortingEnabled(True)
        self.scan_table.sortItems(5, Qt.DescendingOrder)
        if rows:
            self.scan_table.selectRow(0)

    def _scan_selection_changed(self) -> None:
        selected = self.scan_table.selectedItems()
        if not selected:
            return
        symbol = self.scan_table.item(
            selected[0].row(), 0
        ).text()
        try:
            points = load_close_series(
                symbol,
                data_root=self.data_root,
                fallback_data_root=self.bundled_data_root,
            )
            self.scan_chart.set_series(symbol, points)
        except Exception as error:
            self._log(f"{symbol} 图表读取失败：{error}")

    def _refresh_queue_table(self) -> None:
        store = HistoryJobStore(self.queue_path)
        jobs = store.list_jobs()
        counts = store.counts()
        visible_jobs = jobs[:2500]
        if hasattr(self, "history_queue_summary"):
            self.history_queue_summary.setText(
                f"历史队列 {len(jobs):,} · 待处理 "
                f"{counts['pending']:,} · 完成 {counts['completed']:,} · "
                f"失败 {counts['failed']:,}。"
                + (
                    " 表格仅显示前 2,500 条，任务会全部保留并执行。"
                    if len(jobs) > 2500
                    else ""
                )
            )
        self.queue_table.setSortingEnabled(False)
        self.queue_table.setRowCount(len(visible_jobs))
        translations = {
            "pending": "待处理",
            "running": "运行中",
            "completed": "完成",
            "failed": "失败",
        }
        for index, job in enumerate(visible_jobs):
            values = (
                job.symbol,
                job.duration,
                str(job.priority),
                translations.get(job.status, job.status),
                str(job.attempts),
                str(job.row_count or "—"),
                job.last_error,
            )
            for column, value in enumerate(values):
                self.queue_table.setItem(
                    index,
                    column,
                    QTableWidgetItem(value),
                )
        self.queue_table.setSortingEnabled(True)

    def _local_history_symbol_count(self) -> int:
        symbols: set[str] = set()
        for root in {
            Path(self.data_root),
            Path(self.bundled_data_root),
        }:
            directory = root / "normalized" / "ibkr" / "daily"
            if not directory.exists():
                continue
            symbols.update(
                path.name
                for path in directory.iterdir()
                if path.is_dir()
            )
        return len(symbols)

    def _refresh_market_scope_summary(self) -> None:
        if not hasattr(self, "stream_scope_label"):
            return
        universe_summary = (
            self.universe.summary()
            if self.universe is not None
            else {}
        )
        research_count = int(
            universe_summary.get("research_eligible", 0)
        )
        total_count = int(universe_summary.get("total", 0))
        history_count = self._local_history_symbol_count()
        scanned_count = (
            len(self.scan.results) if self.scan is not None else 0
        )
        missing_count = (
            len(self.scan.skipped) if self.scan is not None else 0
        )
        self.stream_scope_label.setText(
            f"范围分层 · 官方美股/ETF {total_count:,} · "
            f"排除中概后的研究池 {research_count:,} · "
            f"已有日 K {history_count:,} · 最近扫描 "
            f"{scanned_count:,}"
            + (
                f"（缺历史或数据不足 {missing_count:,}）"
                if missing_count
                else ""
            )
            + " · 上方实时订阅最多 30，只是行情窗口。"
        )
        if hasattr(self, "auto_scope_label"):
            self.auto_scope_label.setText(
                f"全市场入口：官方美股/ETF {total_count:,} · "
                f"非中概研究池 {research_count:,} · "
                f"本地已有日 K {history_count:,} · "
                f"最近完成评分 {scanned_count:,} · "
                f"当前实时候选 {len(self.auto_quant_candidates)}。"
            )

    def _refresh_cards(self) -> None:
        if self.portfolio_view is None:
            self.universe_card.set_value(
                "未读取", "到账户与持仓页执行只读刷新"
            )
            self.verified_card.set_value(
                "不可用", "不会以研究收益代替"
            )
            self.history_card.set_value(
                "未读取", "券商空仓与未读取严格区分"
            )
        if self.stream_snapshot is None:
            self.signal_card.set_value(
                "不可用", "尚未启动流行情"
            )

    def _start_task(
        self,
        task: Callable[[Callable[[str], None]], object],
        *,
        on_success: Callable[[object], None],
        start_message: str,
        resource_group: str = "research",
    ) -> bool:
        running = [
            worker
            for worker in self.workers
            if worker.isRunning()
            and worker.resource_group == resource_group
        ]
        if running:
            message = (
                f"已有{resource_group}任务在运行。为避免同类文件和数据库"
                "并发写入，请等待当前任务完成。"
            )
            self._log(message)
            QMessageBox.information(self, "任务忙", message)
            return False
        worker = TaskThread(task, resource_group=resource_group)
        self.workers.append(worker)
        if hasattr(self, "runtime_task_card"):
            self.runtime_task_card.set_value(
                str(len(self.workers)), start_message
            )
        worker.progress.connect(self._log)
        worker.failed.connect(self._task_failed)
        worker.cancelled.connect(self._task_cancelled)
        worker.succeeded.connect(on_success)
        worker.finished.connect(
            lambda: self._worker_finished(worker)
        )
        self._log(start_message)
        worker.start()
        return True

    def _worker_finished(self, worker: TaskThread) -> None:
        if worker in self.workers:
            self.workers.remove(worker)
        if worker is self.universe_refresh_worker:
            self._reset_universe_refresh_controls()
        if hasattr(self, "runtime_task_card"):
            self.runtime_task_card.set_value(
                str(len(self.workers)), "后台任务"
            )
        if hasattr(self, "backtest_run_button"):
            self.backtest_run_button.setEnabled(True)
            self.backtest_compare_button.setEnabled(True)
        if (
            hasattr(self, "auto_channel_check_button")
            and self.paper_order_service is None
        ):
            self.auto_channel_check_button.setEnabled(True)

    def _task_cancelled(self) -> None:
        self._log("任务已取消；已保留上一次完整可用的研究结果。")

    def _task_failed(self, message: str) -> None:
        self.queue_progress.setValue(0)
        if (
            hasattr(self, "auto_start_button")
            and self.paper_order_service is None
            and self.auto_quant_engine is None
        ):
            self.auto_arm_confirm.setChecked(False)
            self.auto_start_button.setEnabled(True)
            self.auto_prepare_button.setEnabled(True)
            self.auto_capital_limit.setEnabled(True)
            self.auto_channel_check_button.setEnabled(True)
        self._log(f"任务失败：{message}")
        self._record_runtime_event(
            severity="error",
            component="task",
            code="TASK_FAILED",
            message=message,
        )
        QMessageBox.warning(self, "任务失败", message)

    def _record_runtime_event(
        self,
        *,
        severity: str,
        component: str,
        code: str,
        message: str,
    ) -> None:
        self.runtime_events.add(
            severity=severity,
            component=component,
            code=code,
            message=message,
        )
        if hasattr(self, "runtime_event_table"):
            self._refresh_runtime_events()

    def _refresh_runtime_events(self) -> None:
        events = self.runtime_events.list_recent(500)
        unresolved = [row for row in events if not row.resolved]
        counts = Counter(row.severity for row in unresolved)
        self.runtime_error_card.set_value(
            str(counts["error"]), "未确认错误"
        )
        self.runtime_warning_card.set_value(
            str(counts["warning"]), "未确认警告"
        )
        self.runtime_task_card.set_value(
            str(len(self.workers)), "当前后台任务"
        )
        self.runtime_event_table.setSortingEnabled(False)
        self.runtime_event_table.setRowCount(len(events))
        self.runtime_empty_label.setVisible(not events)
        translations = {
            "info": "信息",
            "warning": "警告",
            "error": "错误",
        }
        for index, event in enumerate(events):
            values = (
                str(event.event_id),
                event.occurred_at,
                translations.get(event.severity, event.severity),
                event.component,
                event.code,
                event.message,
                "已确认" if event.resolved else "待确认",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if event.severity == "error":
                    item.setForeground(QColor(self.theme.error))
                elif event.severity == "warning":
                    item.setForeground(QColor(self.theme.warning))
                self.runtime_event_table.setItem(
                    index, column, item
                )
        self.runtime_event_table.setSortingEnabled(True)

    def _resolve_selected_runtime_event(self) -> None:
        selected = self.runtime_event_table.selectedItems()
        if not selected:
            QMessageBox.information(
                self, "未选择事件", "请先选择一条运行事件。"
            )
            return
        row = selected[0].row()
        event_id_item = self.runtime_event_table.item(row, 0)
        if event_id_item is None:
            return
        self.runtime_events.resolve(int(event_id_item.text()))
        self._refresh_runtime_events()

    def _export_terminal_state(self) -> None:
        try:
            target = export_terminal_bundle(
                self.paths.exports_root,
                portfolio=self.portfolio_view,
                stream=self.stream_snapshot,
                strategies=self.strategy_registry.list_records(),
                events=self.runtime_events.list_recent(500),
                shadow_fills=self.shadow_store.recent_fills(500),
                targeted_replays=tuple(
                    self.targeted_replay_results
                ),
                targeted_robustness=tuple(
                    self.targeted_robustness_results
                ),
                targeted_walk_forward=tuple(
                    self.targeted_walk_forward_results
                ),
                targeted_overfit=tuple(
                    self.targeted_overfit_results
                ),
                targeted_data_quality=tuple(
                    self.targeted_data_quality_results
                ),
                targeted_execution_stress=tuple(
                    self.targeted_execution_stress_results
                ),
                targeted_review=tuple(
                    self.targeted_review_results
                ),
                paper_order_audit=(
                    self.paper_order_journal.audit_rows()
                ),
                paper_execution_audit=(
                    self.paper_order_journal.execution_rows()
                ),
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "导出失败", str(error))
            return
        self.runtime_export_card.set_value(
            target.name, str(target)
        )
        self._record_runtime_event(
            severity="info",
            component="export",
            code="EXPORT_OK",
            message=f"终端状态已脱敏导出到 {target}",
        )
        QMessageBox.information(
            self,
            "导出完成",
            f"已导出脱敏 CSV / JSON：\n{target}",
        )

    def _log(self, message: str) -> None:
        self.status_label.setText(message)

    def _preview_theme_changed(self) -> None:
        theme_name = str(
            self.settings_theme_combo.currentData() or "dark"
        )
        self._apply_theme(theme_name)

    def _paper_order_capability_toggled(self, checked: bool) -> None:
        if not checked:
            return
        answer = QMessageBox.warning(
            self,
            "开启 IBKR Paper 模拟下单能力",
            "这只打开客户端的 Paper 能力标志，不会立即下单，也不会"
            "自动武装策略。\n\n实际提交前仍必须满足：本机端口 4002、"
            "唯一 DU 模拟账户、fresh 实时行情、合格候选、单笔上限、"
            "会话内再次确认与武装。自动量化页武装后，策略信号可能"
            "自动向 IBKR Paper 提交 DAY 限价单。\n\nLive 账户、"
            "市场单、碎股、做空、借款和"
            "期权仍被禁止。是否保留开启状态？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self.settings_paper_order_capability.blockSignals(True)
            self.settings_paper_order_capability.setChecked(False)
            self.settings_paper_order_capability.blockSignals(False)

    def _extended_hours_paper_toggled(self, checked: bool) -> None:
        if not checked:
            return
        answer = QMessageBox.warning(
            self,
            "开启 IBKR Paper 5×24 扩展时段",
            "这会让 Paper 自动量化按当前美东时段路由整股限价单："
            "盘前/盘后为 SMART + OutsideRth，隔夜为 OVERNIGHT。\n\n"
            "它不会立即下单，不会连接 Live，也不会绕过实时行情、"
            "策略、风控、DU 账户和逐会话确认。周末、休市和美东 "
            "03:50–04:00 维护窗口仍会拒绝订单。是否保留开启状态？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self.settings_extended_hours_paper.blockSignals(True)
            self.settings_extended_hours_paper.setChecked(False)
            self.settings_extended_hours_paper.blockSignals(False)

    def _save_user_preferences(self) -> None:
        try:
            preferences = UserPreferences(
                theme=str(
                    self.settings_theme_combo.currentData() or "dark"
                ),
                market_provider=str(
                    self.settings_provider_combo.currentData()
                    or "finnhub_trades"
                ),
                ibkr_host=self.settings_ibkr_host.text(),
                ibkr_port=self.settings_ibkr_port.value(),
                ibkr_client_id=self.settings_ibkr_client_id.value(),
                connection_timeout_seconds=float(
                    self.settings_ibkr_timeout.value()
                ),
                paper_order_capability_enabled=(
                    self.settings_paper_order_capability.isChecked()
                ),
                extended_hours_paper_enabled=(
                    self.settings_extended_hours_paper.isChecked()
                ),
            )
            saved = self.preferences_store.save(preferences)
        except UserSettingsError as error:
            QMessageBox.warning(self, "设置未保存", str(error))
            return
        self.preferences = saved
        self._apply_preferences_to_config(saved)
        self._apply_theme(saved.theme)
        index = self.stream_mode.findData(saved.market_provider)
        if index >= 0:
            self.stream_mode.setCurrentIndex(index)
        self._log(
            "设置已保存；主题已生效，连接参数在下一次连接时使用"
        )
        if saved.paper_order_capability_enabled:
            self.safety_badge.setText(
                "Paper下单能力 · 未武装"
            )
        else:
            self.safety_badge.setText(
                "只读 · 自动下单关闭"
            )
        self._refresh_auto_quant_preflight()
        self._refresh_extended_hours_status()

    def _apply_preferences_to_config(
        self, preferences: UserPreferences
    ) -> None:
        self.config = replace(
            self.config,
            ibkr=IBKRConnectionConfig(
                host=preferences.ibkr_host,
                port=preferences.ibkr_port,
                client_id=preferences.ibkr_client_id,
                api_read_only=True,
                paper_order_submission_enabled=False,
                connection_timeout_seconds=(
                    preferences.connection_timeout_seconds
                ),
            ),
        )

    def _save_api_credentials(self) -> None:
        provider = str(
            self.settings_api_provider_combo.currentData()
            or "finnhub_trades"
        )
        if provider == "finnhub_trades":
            supplied = {
                "finnhub_api_key": (
                    self.settings_finnhub_key.text().strip()
                )
            }
        elif provider == "alpaca_iex":
            supplied = {
                "alpaca_api_key": (
                    self.settings_alpaca_key.text().strip()
                ),
                "alpaca_api_secret": (
                    self.settings_alpaca_secret.text().strip()
                ),
            }
        else:
            QMessageBox.information(
                self,
                "无需 API Key",
                "IBKR Gateway 使用本机 Host、端口和 Client ID，"
                "不在这里保存 API Key。",
            )
            return
        supplied = {
            name: value for name, value in supplied.items() if value
        }
        if not supplied:
            QMessageBox.information(
                self,
                "没有变化",
                "所选数据源的输入框为空，没有修改已保存凭据。",
            )
            return
        if provider == "alpaca_iex" and len(supplied) != 2:
            QMessageBox.warning(
                self,
                "凭据不完整",
                "Alpaca 需要同时填写 API Key 和 API Secret。",
            )
            return
        try:
            for name, value in supplied.items():
                self.credential_store.save_secret(name, value)
        except (CredentialStoreError, OSError, ValueError) as error:
            QMessageBox.warning(self, "凭据保存失败", str(error))
            return
        for field in (
            self.settings_finnhub_key,
            self.settings_alpaca_key,
            self.settings_alpaca_secret,
        ):
            field.clear()
        self._refresh_credential_status()
        self._log("API 凭据已使用 Windows 当前用户 DPAPI 加密保存")

    def _clear_selected_api_credentials(self) -> None:
        provider = str(
            self.settings_api_provider_combo.currentData()
            or "finnhub_trades"
        )
        if (
            self.stream_worker is not None
            and self.stream_worker.isRunning()
            and self.stream_worker.provider == provider
        ):
            QMessageBox.warning(
                self,
                "行情运行中",
                "当前数据源正在使用这组凭据；请先切换或停止行情，"
                "再清除凭据。",
            )
            return
        if provider == "finnhub_trades":
            names = ("finnhub_api_key",)
            label = "Finnhub"
        elif provider == "alpaca_iex":
            names = ("alpaca_api_key", "alpaca_api_secret")
            label = "Alpaca"
        else:
            QMessageBox.information(
                self,
                "无需清除",
                "IBKR Gateway 没有保存在此处的 API Key。",
            )
            return
        try:
            for name in names:
                self.credential_store.delete_secret(name)
        except (CredentialStoreError, OSError, ValueError) as error:
            QMessageBox.warning(self, "凭据清除失败", str(error))
            return
        for field in (
            self.settings_finnhub_key,
            self.settings_alpaca_key,
            self.settings_alpaca_secret,
        ):
            field.clear()
        self._refresh_credential_status()
        self._log(
            f"已清除当前 Windows 用户保存的 {label} 行情凭据"
        )

    def _clear_saved_finnhub_key(self) -> None:
        try:
            self.credential_store.delete_secret("finnhub_api_key")
        except (CredentialStoreError, OSError, ValueError) as error:
            QMessageBox.warning(self, "Finnhub Key 清除失败", str(error))
            return
        self._refresh_credential_status()
        self._log("已清除当前 Windows 用户保存的 Finnhub Key")

    def _refresh_credential_status(self) -> None:
        if not hasattr(self, "settings_credential_status"):
            return
        provider = str(
            self.settings_api_provider_combo.currentData()
            or "finnhub_trades"
        )
        if provider == "finnhub_trades":
            path = (
                self.credential_store.root / "finnhub_api_key.dpapi"
            )
            status = (
                "Finnhub：已加密保存"
                if path.exists()
                else "Finnhub：未保存"
            )
        elif provider == "alpaca_iex":
            key_path = (
                self.credential_store.root / "alpaca_api_key.dpapi"
            )
            secret_path = (
                self.credential_store.root / "alpaca_api_secret.dpapi"
            )
            status = (
                "Alpaca Key："
                f"{'已加密保存' if key_path.exists() else '未保存'}"
                " · Alpaca Secret："
                f"{'已加密保存' if secret_path.exists() else '未保存'}"
            )
        else:
            status = (
                "IBKR Gateway：使用本机 Host / 端口 / Client ID，"
                "无需 API Key"
            )
        self.settings_credential_status.setText(status)

    def _api_provider_changed(self, *_args: object) -> None:
        provider = str(
            self.settings_api_provider_combo.currentData()
            or "finnhub_trades"
        )
        is_finnhub = provider == "finnhub_trades"
        is_alpaca = provider == "alpaca_iex"
        self.settings_finnhub_key.setVisible(is_finnhub)
        self.settings_alpaca_key.setVisible(is_alpaca)
        self.settings_alpaca_secret.setVisible(is_alpaca)
        stream_running = (
            self.stream_worker is not None
            and self.stream_worker.isRunning()
        )
        active_provider = (
            self.stream_worker.provider
            if stream_running and self.stream_worker is not None
            else None
        )
        has_api_credentials = is_finnhub or is_alpaca
        self.settings_save_credentials_button.setEnabled(
            has_api_credentials
        )
        self.settings_clear_credentials_button.setEnabled(
            has_api_credentials and provider != active_provider
        )
        self._refresh_credential_status()

    def _load_stream_credential(
        self, name: str, environment_name: str
    ) -> str:
        environment_value = os.environ.get(environment_name, "").strip()
        if environment_value:
            return environment_value
        try:
            return self.credential_store.load_secret(name) or ""
        except CredentialStoreError as error:
            raise ValueError(str(error)) from error

    def _set_connection_settings_enabled(self, enabled: bool) -> None:
        for control_name in (
            "settings_ibkr_host",
            "settings_ibkr_port",
            "settings_ibkr_client_id",
            "settings_ibkr_timeout",
            "settings_paper_order_capability",
            "settings_extended_hours_paper",
        ):
            control = getattr(self, control_name, None)
            if control is not None:
                control.setEnabled(enabled)
        self.settings_provider_combo.setEnabled(True)
        self.settings_switch_provider_button.setEnabled(True)
        self.settings_api_provider_combo.setEnabled(True)
        self.settings_finnhub_key.setEnabled(True)
        self.settings_alpaca_key.setEnabled(True)
        self.settings_alpaca_secret.setEnabled(True)
        self._api_provider_changed()

    def _quotes_scroll_started(self) -> None:
        self._quotes_scroll_active = True

    def _quotes_scroll_finished(self) -> None:
        self._quotes_scroll_active = False
        pending = self._pending_stream_snapshot
        self._pending_stream_snapshot = None
        if pending is not None:
            self._populate_stream_snapshot(pending)

    def _apply_theme(self, theme_name: str) -> None:
        self.current_theme_name = (
            "light" if theme_name == "light" else "dark"
        )
        self.theme: ThemePalette = theme_palette(
            self.current_theme_name
        )
        self.setProperty("uiTheme", self.current_theme_name)
        self.setStyleSheet(build_stylesheet(self.theme))
        if hasattr(self, "quotes_model"):
            self.quotes_model.set_theme(self.current_theme_name)
        for chart_name in ("dashboard_chart", "strategy_chart"):
            chart = getattr(self, chart_name, None)
            if chart is not None:
                chart.update()

    def _apply_style(self) -> None:
        self._apply_theme(self.current_theme_name)

    def _apply_legacy_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #0b1219;
                color: #d8e2ec;
                font-family: "Microsoft YaHei UI";
                font-size: 13px;
            }
            QMainWindow { background: #0b1219; }
            #appTitle { font-size: 25px; font-weight: 700; color: #f4f8fb; }
            #subtitle { color: #7f93a7; font-size: 12px; }
            #emptyState {
                background: #101b25;
                color: #8ea1b4;
                border: 1px dashed #2b4355;
                border-radius: 7px;
                padding: 10px;
            }
            #statusBadge, #safetyBadge {
                border-radius: 12px;
                padding: 6px 10px;
                font-weight: 600;
                font-size: 11px;
            }
            #statusBadge { background: #172634; color: #8fb6d3; }
            #statusBadge[state="ok"] { background: #10332d; color: #42deb8; }
            #statusBadge[state="warn"] { background: #3b2a15; color: #f0b35b; }
            #statusBadge[state="error"] { background: #411f29; color: #ff7a8a; }
            #safetyBadge { background: #2b1f37; color: #c8a0f4; }
            QTabWidget::pane {
                border: 1px solid #1f2c38;
                border-radius: 8px;
                background: #0e171f;
            }
            QTabBar::tab {
                background: transparent;
                color: #8194a7;
                padding: 10px 12px;
                margin-right: 3px;
            }
            QTabBar::tab:selected {
                color: #33d6ad;
                border-bottom: 2px solid #33d6ad;
            }
            #metricCard, #panel {
                background: #101b25;
                border: 1px solid #20303e;
                border-radius: 9px;
            }
            #metricTitle { color: #8295a8; font-size: 12px; }
            #metricValue { color: #f5f8fb; font-size: 26px; font-weight: 700; }
            #metricNote { color: #63778b; font-size: 11px; }
            #sectionTitle { font-size: 17px; font-weight: 650; color: #f0f5f8; }
            QPushButton {
                background: #173c38;
                color: #49dfbb;
                border: 1px solid #28635b;
                border-radius: 6px;
                padding: 7px 13px;
                font-weight: 600;
            }
            QPushButton:hover { background: #205047; }
            QPushButton:pressed { background: #102e2a; }
            QLineEdit, QComboBox, QSpinBox {
                background: #111c26;
                border: 1px solid #273746;
                border-radius: 6px;
                padding: 7px;
                min-height: 22px;
            }
            QTableWidget {
                background: #0e171f;
                alternate-background-color: #111d27;
                border: 1px solid #20303e;
                gridline-color: #1c2a36;
                selection-background-color: #17443d;
                selection-color: #f3faf8;
            }
            QHeaderView::section {
                background: #15222d;
                color: #8fa3b5;
                padding: 7px;
                border: none;
                border-right: 1px solid #233340;
                font-weight: 600;
            }
            QTextEdit {
                background: #101923;
                border: 1px solid #20303e;
                border-radius: 7px;
                padding: 8px;
                color: #b8c7d4;
                line-height: 1.5;
            }
            QProgressBar {
                border: 1px solid #263845;
                border-radius: 5px;
                background: #101923;
                text-align: center;
            }
            QProgressBar::chunk { background: #2fbf9e; border-radius: 4px; }
            #footer { color: #71869a; padding: 2px 4px; }
            QSplitter::handle { background: #182733; }
            """
        )


def main() -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    configure_chinese_font(app)
    window = MainWindow()
    window.showMaximized()
    if os.environ.get("US_QUANT_SELF_TEST") == "1":
        app.processEvents()
        window.close()
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
