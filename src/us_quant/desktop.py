from __future__ import annotations

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
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplashScreen,
    QSizePolicy,
    QSplitter,
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


from us_quant.config import load_config
from us_quant.credential_store import (
    CredentialStoreError,
    WindowsCredentialStore,
)
from us_quant.desktop_credentials import DesktopCredentialService
from us_quant.artifact_state import (
    ArtifactCatalog,
    load_artifact_catalog,
)
from us_quant.paths import ApplicationPaths
from us_quant.account_ledger import AccountLedger
from us_quant.history_queue import HistoryJobStore
from us_quant.desktop_history_service import DesktopHistoryService
from us_quant.desktop_universe_service import (
    STAGE_DOWNLOAD_OFFICIAL,
    STAGE_ENRICH_SEC,
    STAGE_ENRICH_SEC_START,
    STAGE_PREPARE_REFERENCE,
    UniverseRefreshProgress,
    DesktopUniverseService,
)
from us_quant.desktop_market_scan_service import DesktopMarketScanService
from us_quant.desktop_backtest_service import DesktopBacktestService
from us_quant.ibkr import IBKRConnectionConfig, probe_ibkr_socket
from us_quant.trading.application.market_data import (
    PUSH_LISTENER_SOURCES,
    SOURCE_ALPACA_IEX,
    SOURCE_IBKR_EXTENDED,
    MarketDataCredentials,
    MarketDataStartRequest,
)
from us_quant.trading.composition.accounts import (
    build_broker_account_application,
)
from us_quant.trading.composition.market_data import (
    build_market_data_application,
)
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
    BrokerPositionSnapshot,
)
from us_quant.trading.domain.common import Environment
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketSnapshot,
)
from us_quant.trading.ports.broker_account import (
    BrokerAccountError,
)
from us_quant.trading.ports.market_data import (
    MarketDataActiveError,
    MarketDataCredentialsError,
)
from us_quant.desktop_settings import (
    DesktopSettingsService,
    ibkr_config_from_preferences,
)
from us_quant.extended_hours import (
    USEquitySession,
    paper_order_routing,
    us_equity_session,
)
from us_quant.desktop_v2.pages.account import AccountPage
from us_quant.desktop_v2.pages.risk import RiskPage
from us_quant.desktop_v2.pages.strategy import (
    StrategyPage,
    strategy_option_label,
)
from us_quant.scanner import (
    MarketScan,
    load_close_series,
    save_market_scan,
    scan_market,
)
from us_quant.trading.application.strategies import (
    StrategyApplicationError,
    StrategyNotFoundError,
)
from us_quant.trading.application.strategy_selection import (
    StrategySelectionError,
    StrategySelectionPurpose,
    StrategySelectionService,
)
from us_quant.trading.composition.strategies import (
    build_strategy_application,
)
from us_quant.trading.domain.strategy import (
    StrategyIdentity,
    StrategyStatus,
    StrategyVersion,
)
from us_quant.runtime_events import RuntimeEventStore
from us_quant.export_service import export_terminal_bundle
from us_quant.shadow.config import build_targeted_shadow_config
from us_quant.shadow.engine import ShadowPaperEngine
from us_quant.shadow.models import ShadowSnapshot
from us_quant.shadow.store import ShadowPaperStore
from us_quant.trading.composition.session_config import (
    build_auto_rotation_config,
    resolve_paper_session_capital,
)
from us_quant.auto_launch import (
    AutoLaunchPlan,
    auto_launch_plan_matches,
    build_auto_launch_plan,
)
from us_quant.trading.application.risk import RiskApplication
from us_quant.trading.composition.execution import (
    build_execution_application,
    build_execution_candidate,
    build_order_repository,
)
from us_quant.trading.composition.risk import build_risk_application
from us_quant.trading.composition.runtime import build_trading_runtime
from us_quant.trading.domain.risk import LayeredRiskLimits
from us_quant.trading.runtime.artifacts import AutoQuantSnapshot
from us_quant.trading.runtime.models import AutoQuantCandidate
from us_quant.trading.runtime.preflight import (
    AutoQuantPreflight,
    calculate_quote_readiness_breakdown,
    evaluate_auto_quant_preflight,
)
from us_quant.trading.runtime.trading import TradingRuntime
from us_quant.runtime_supervisor import RuntimeSnapshot, RuntimeSupervisor
from us_quant.paper_order_models import PaperOrderReconciliation
from us_quant.trading.ports.broker_execution import ExecutionRefused
from us_quant.trading.runtime.health import (
    PaperExecutionHealth,
    PaperExecutionIssue,
    evaluate_paper_execution_health,
)
from us_quant.trading.runtime.paper_models import PaperSessionResult
from us_quant.trading.application.paper import PaperTradingService
from us_quant.trading.runtime.workflow_state import (
    PaperWorkflowPhase,
    WorkflowStateError,
)
from us_quant.desktop_v2.pages.execution import ExecutionPage
from us_quant.desktop_v2.pages.execution.presenter import (
    build_candidates_view,
    build_runtime_view,
    control_state,
)
from us_quant.desktop_v2.pages.market import MarketPage
from us_quant.desktop_v2.pages.market.controls import VALID_MARKET_SOURCES
from us_quant.desktop_v2.pages.market.models import (
    MarketConnectingFacts,
    MarketReadinessFacts,
)
from us_quant.desktop_v2.pages.market.presenter import (
    build_connecting_view,
    build_market_view,
    control_view,
)
from us_quant.desktop_v2.pages.market.rows import quote_rows
from us_quant.desktop_v2.pages.research.targeted import TargetedValidationPage
from us_quant.desktop_v2.pages.research.targeted.evidence_presenter import (
    evidence_view,
)
from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedControlView,
    TargetedStrategyOption,
    TargetedValidationView,
)
from us_quant.desktop_v2.pages.research.targeted.session_presenter import (
    session_view,
)
from us_quant.desktop_v2.pages.research.universe import UniversePage
from us_quant.desktop_v2.pages.research.universe.presenter import (
    build_universe_view,
)
from us_quant.desktop_v2.pages.research.history import HistoryPage
from us_quant.desktop_v2.pages.research.history.presenter import (
    build_history_view,
)
from us_quant.desktop_v2.pages.research.scanner import ScannerPage
from us_quant.desktop_v2.pages.research.scanner.models import ScannerChartView
from us_quant.desktop_v2.pages.research.scanner.presenter import (
    build_scanner_view,
)
from us_quant.desktop_v2.pages.research.backtest import BacktestPage
from us_quant.desktop_v2.pages.research.backtest.models import (
    BacktestFormDraft,
    BacktestStrategyOption,
)
from us_quant.desktop_v2.pages.research.backtest.presenter import (
    build_backtest_view,
)
from us_quant.desktop_v2.pages.research.cross_section import (
    CrossSectionResearchPage,
)
from us_quant.desktop_v2.pages.research.cross_section.models import (
    CrossSectionResearchDraft,
)
from us_quant.desktop_v2.pages.research.cross_section.presenter import (
    build_cross_section_view,
)
from us_quant.desktop_v2.pages.research import (
    ResearchPage,
    ResearchWorkspace,
)
from us_quant.desktop_v2.pages.system import (
    SystemPage,
    SystemWorkspace,
)
from us_quant.desktop_v2.pages.system.runtime_events import (
    RuntimeEventsPage,
)
from us_quant.desktop_v2.pages.system.runtime_events.presenter import (
    build_runtime_events_view,
    runtime_info_text,
)
from us_quant.desktop_v2.pages.system.settings import SettingsPage
from us_quant.desktop_v2.pages.system.settings.models import (
    CredentialDraft,
    SettingsDraft,
    SettingsPageView,
    SettingsStorageView,
)
from us_quant.desktop_tasks import DesktopTaskController
from us_quant.desktop_workers import (
    StreamWorker,
    TaskThread,
)
from us_quant.desktop_widgets import (
    EquityComparisonChart,
    MetricCard,
    PriceChart,
    _price,
    _sortable_number,
    configure_combo_width,
    configure_table,
)
from us_quant.desktop_v2.workflows import WorkflowController
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
from us_quant.cross_sectional import (
    run_cross_sectional_research,
    save_cross_sectional_research,
)
from us_quant.executable_research import (
    run_executable_cross_sectional_research,
    save_executable_research,
)
from us_quant.universe import (
    UniverseSnapshot,
    load_universe_snapshot,
    prioritized_research_symbols,
)
from us_quant.ui_theme import (
    ThemePalette,
    build_stylesheet,
    theme_palette,
)
from us_quant.desktop_v2.navigation import ROUTES
from us_quant.desktop_v2.shell import DesktopShellV2
from us_quant.user_settings import (
    UserPreferences,
    UserPreferencesStore,
    UserSettingsError,
)
from us_quant.backtest_workspace import (
    STRATEGY_SPECS,
    BacktestRequest,
    BacktestRun,
)


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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.paths = ApplicationPaths.discover()
        self.paths.ensure_state_directories()
        self.paths.seed_research_results()
        self.credential_store = WindowsCredentialStore(
            self.paths.state_root / "credentials"
        )
        self.credential_service = DesktopCredentialService(
            self.credential_store
        )
        self.root = self.paths.resource_root
        self.config_path = self.paths.config_path
        self.data_root = self.paths.user_data_root
        self.bundled_data_root = self.paths.bundled_data_root
        self.reference_root = self.data_root / "reference"
        self.universe_service = DesktopUniverseService(paths=self.paths)
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
        self.history_service = DesktopHistoryService(
            queue_path=self.queue_path,
            data_root=self.data_root,
        )
        self.scan_path = (
            self.paths.research_results_root / "market_scan.json"
        )
        self.market_scan_service = DesktopMarketScanService(
            data_root=self.data_root,
            fallback_data_root=self.bundled_data_root,
            scan_path=self.scan_path,
        )
        self.backtest_service = DesktopBacktestService(
            data_root=self.data_root,
            fallback_data_root=self.bundled_data_root,
            output_root=self.paths.research_results_root / "backtests",
        )
        self.cross_section_path = (
            self.paths.research_results_root
            / "cross_sectional_executable_research.json"
        )
        baseline_config = load_config(self.config_path)
        self.preferences_store = UserPreferencesStore(
            self.paths.state_root / "settings" / "preferences.json"
        )
        self.settings_service = DesktopSettingsService(
            self.preferences_store
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
        # Start-up builds its IBKR config through the same mapping a later
        # save uses, so the two cannot drift into separate rules.
        self.config = replace(
            baseline_config,
            ibkr=ibkr_config_from_preferences(self.preferences),
        )
        self.backtest_runs: list[BacktestRun] = []
        self._selected_backtest_run_id: str | None = None
        self._backtest_busy = False
        self.artifact_catalog: ArtifactCatalog = load_artifact_catalog(
            self.paths.research_results_root
        )
        self.account_ledger = AccountLedger(
            self.paths.runtime_root / "account_equity.sqlite3"
        )
        # Strategy governance is composed here and nowhere else: the window
        # asks the application service, and the application service has never
        # heard of SQLite.  ``bootstrap`` is idempotent, so an existing store
        # is brought up to date in place rather than rebuilt.
        self.strategies = build_strategy_application(
            self.paths.runtime_root / "strategies.sqlite3"
        )
        # Runtime selection is service state, not combo state.  The combos on
        # the research and execution pages are views onto this, so asking
        # "which version does auto rotation run?" has one answer that does not
        # depend on which widget happens to be visible.
        self.strategy_selection = StrategySelectionService(self.strategies)
        self.runtime_events = RuntimeEventStore(
            self.paths.runtime_root / "runtime_events.sqlite3"
        )
        self.shadow_store = ShadowPaperStore(
            self.paths.runtime_root / "shadow_paper.sqlite3"
        )
        self.order_repository = build_order_repository(
            self.paths.runtime_root / "ibkr_paper_orders.sqlite3"
        )
        self.minute_quote_store = MinuteQuoteStore(
            self.paths.runtime_root / "minute_quotes.sqlite3"
        )
        self._minute_recorded_keys: dict[
            tuple[str, str, str], bool
        ] = {}
        self._last_stream_event_key: (
            tuple[str, int] | None
        ) = None
        self._quote_last_ready_monotonic: dict[str, float] = {}
        self._last_stream_status_key: tuple[object, ...] | None = None
        self._last_stream_status_log_at = 0.0
        self._last_stream_ingress_monotonic = 0.0
        self._last_stream_push_monotonic = 0.0
        self._paper_finalization_inflight = False
        self._last_paper_finalization_started: float | None = None
        self._last_runtime_events_refresh = 0.0
        self._runtime_events_refresh_pending = False
        self._last_runtime_export: tuple[str, str] | None = None
        # Settings presentation facts the window owns: the selected API
        # provider and whether the Gateway controls are currently open.
        self._settings_api_provider = (
            "ibkr"
            if self.preferences.market_provider == "ibkr_extended"
            else self.preferences.market_provider
        )
        self._connection_settings_enabled = True
        # Two local in-flight facts the execution route's control state reads.
        # They live here because only the window knows a local step is running;
        # the page is told the resulting booleans, never these flags.
        self._launch_busy = False
        self._stream_stop_pending = False
        # The channel probe is a launch step too, but it is owned by its own
        # worker rather than by the shared launch flag: the broker resource
        # group serializes it, so an unrelated task finishing must not be able
        # to release the route on the probe's behalf.
        self._channel_check_inflight = False
        # The market route's scope line and watchlist note are computed here --
        # the scope needs the universe and the scan, the note records which
        # workflow last set the subscription -- and handed to the page as text.
        self._market_scope = ""
        self._market_watchlist_note: str | None = None
        # Targeted workspace facts the page renders but does not own.
        self._target_status = "未指定"
        self._minute_status = (
            "分钟证据：输入代码后显示本地已录数据；只回放 fresh bid/ask。"
        )
        self._selected_robustness_run_id: str | None = None
        self._selected_review_run_id: str | None = None
        self._targeted_active_workspace: int | None = None
        self._targeted_active_evidence_tab: int | None = None
        self.account_portfolio: BrokerAccountPortfolio | None = None
        self.stream_worker: StreamWorker | None = None
        self._pending_stream_switch: (
            tuple[str, tuple[str, ...]] | None
        ) = None
        self.stream_snapshot: MarketSnapshot | None = None
        self.shadow_engine: ShadowPaperEngine | None = None
        self.shadow_snapshot: ShadowSnapshot | None = None
        self.target_preflight_result: TargetPreflightResult | None = None
        self.trading_runtime: TradingRuntime | None = None
        self.auto_quant_snapshot: AutoQuantSnapshot | None = None
        self.paper_execution_health: PaperExecutionHealth | None = None
        # Paper and internal Shadow simulation share one explicit execution
        # lease.  The desktop renders controller results but never owns the
        # normal Paper event ordering itself.
        self.workflow_controller = WorkflowController()
        self.paper_workflow = self.workflow_controller.paper
        self.shadow_workflow = self.workflow_controller.shadow
        # One boundary for the Paper reads, lifecycle and order-service
        # ownership this window performs.  The workflow getter resolves the
        # live attribute on every call: the window still owns and replaces
        # that controller, and the safety tests swap ``paper_workflow`` to
        # drive the halted and refused-close paths.  The order service is
        # owned *by* this service, never by the window.
        self.paper_trading = PaperTradingService(
            workflow_getter=lambda: self.paper_workflow,
            order_service_factory=build_execution_candidate,
        )
        self.auto_quant_candidates: tuple[
            AutoQuantCandidate, ...
        ] = ()
        self._next_auto_launch_attempt = 0
        self._active_auto_launch_plan: AutoLaunchPlan | None = None
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
        self.cross_section_report: dict | None = None
        self._research_capital_value = int(self.config.initial_equity)
        self.task_controller = DesktopTaskController[TaskThread]()
        self.workers = self.task_controller.workers
        self.universe_refresh_cancel_event: Event | None = None
        self.universe_refresh_worker: TaskThread | None = None
        self._history_progress_percent = 0
        # Admission gate for new background work.  The runtime supervisor
        # raises it as the first step of teardown so a close cannot race a
        # task that is still being admitted.
        self._closing = False
        self.runtime_supervisor = RuntimeSupervisor()
        # Broker/Account v2: the account application is built first because it
        # is the runtime owner of the IBKR connection settings.  Market data
        # reads the current endpoint from it through a getter, so a settings
        # change is picked up by the next stream instead of being captured
        # here at start-up.  Neither application names a concrete adapter --
        # composition did that.
        self.broker_account = build_broker_account_application(
            self.config.ibkr
        )
        self.market_data = build_market_data_application(
            config_getter=lambda: self.broker_account.config
        )

        self.setWindowTitle(APP_TITLE)
        self.resize(1440, 900)
        self.setMinimumSize(1120, 720)
        self._build_ui()
        self._finalize_layout_behavior()
        self._apply_style()
        self._register_runtime_components()

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
        # Desktop UI v2.  MainWindow is the temporary composition root: it
        # builds the page widgets below and hands them to the shell, which
        # owns nothing but route registration and page switching.  The shell
        # never connects a broker, starts a stream or submits an order.
        self.shell = DesktopShellV2(self._build_v2_pages())
        root_layout.addWidget(self.shell, 1)

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
        # H-1 fix: independent Paper order watchdog heartbeat.  The order
        # lifecycle (stale-BUY cancel, SELL intervention, health evaluation)
        # must keep running even when the market stream is down or stopped;
        # it only skips when stream ticks have driven the same watchdog
        # recently.
        self.paper_order_timer = QTimer(self)
        self.paper_order_timer.setInterval(1_000)
        self.paper_order_timer.timeout.connect(
            self._poll_auto_quant_orders
        )
        self.paper_order_timer.start()
        self.extended_session_timer = QTimer(self)
        self.extended_session_timer.setInterval(15_000)
        self.extended_session_timer.timeout.connect(
            self._maybe_rotate_extended_ibkr_session
        )
        self.extended_session_timer.start()
        self._refresh_extended_hours_status()

    def _build_v2_pages(self) -> dict[str, QWidget]:
        """Compose the eight Desktop UI v2 routes.

        Most routes are still backed by a page builder that predates v2; as a
        v2 page is rewritten, its old builder is deleted and this table points
        at the replacement.  ``account`` is the first route to complete that
        move: it is served by :class:`AccountPage`, a native v2 page, and
        ``MainWindow._account_tab`` no longer exists.  The dict is keyed by
        route and must cover ``ROUTES`` exactly -- the shell fails closed on a
        missing or unknown route.

        ``research`` and ``system`` are native aggregates that own their fixed
        secondary workspace tabs.  Research is deliberately not part of the
        trading runtime navigation.
        """
        self.targeted_validation_page = TargetedValidationPage(palette=self.theme)
        self._connect_targeted_validation_page()
        self.universe_page = UniversePage(palette=self.theme)
        self._connect_universe_page()
        self.history_page = HistoryPage(palette=self.theme)
        self._connect_history_page()
        self.scanner_page = ScannerPage(palette=self.theme)
        self._connect_scanner_page()
        self.backtest_page = BacktestPage(
            per_share_commission=(
                self.config.execution.per_share_commission
            ),
            minimum_commission=self.config.execution.minimum_commission,
            slippage_bps=self.config.execution.slippage_bps,
            palette=self.theme,
        )
        self._connect_backtest_page()
        self._publish_backtest_strategy_options()
        self._publish_backtest_view()
        self.cross_section_page = CrossSectionResearchPage(
            research_capital=self._research_capital_value,
            palette=self.theme,
        )
        self._connect_cross_section_page()
        self._publish_cross_section_view()
        self.research_page = ResearchPage(
            {
                ResearchWorkspace.TARGETED: self.targeted_validation_page,
                ResearchWorkspace.UNIVERSE: self.universe_page,
                ResearchWorkspace.HISTORY: self.history_page,
                ResearchWorkspace.SCANNER: self.scanner_page,
                ResearchWorkspace.BACKTEST: self.backtest_page,
                ResearchWorkspace.CROSS_SECTION: self.cross_section_page,
            }
        )

        # The System aggregate is containment only: the two workspaces are
        # built and wired here, then handed to the page as finished widgets.
        self.runtime_events_page = RuntimeEventsPage(palette=self.theme)
        self._connect_runtime_events_page()
        self._refresh_runtime_events()
        self.settings_page = SettingsPage(
            draft=self._settings_draft(),
            storage=self._settings_storage_view(),
        )
        self._connect_settings_page()
        self._publish_settings_view()
        self.system_page = SystemPage(
            {
                SystemWorkspace.RUNTIME_EVENTS:
                    self.runtime_events_page,
                SystemWorkspace.SETTINGS: self.settings_page,
            }
        )

        self.account_page = AccountPage()
        self.account_page.refresh_requested.connect(
            self._refresh_account_snapshot
        )
        # The risk page renders the limits the risk layer enforces and the
        # standing safety boundaries.  It is read-only by construction: it has
        # no service to call and no control that could widen a ceiling.
        self.risk_page = RiskPage(palette=self.theme)
        self.risk_page.render(self.config.risk_limits)
        # The research-capital widget lives on the Cross Section page; the
        # window owns the scalar truth and paints the Account card from it.
        # Initialising through the same handler keeps the first paint and every
        # later change on one path.
        self._research_capital_changed(self._research_capital_value)

        # The strategy page renders and reports intent; every decision it
        # reports is executed here by the application service.  The page holds
        # no catalogue and cannot mutate one.
        self.strategy_page = StrategyPage(palette=self.theme)
        self.strategy_page.version_selected.connect(
            self._strategy_version_selected
        )
        self.strategy_page.clone_requested.connect(
            self._strategy_clone_requested
        )
        self.strategy_page.transition_requested.connect(
            self._strategy_transition_requested
        )

        # The execution page renders and reports intent; every decision it
        # reports is executed here.  It holds no service and cannot arm, connect
        # or start anything, so the launch confirmation below stays the window's.
        self.execution_page = ExecutionPage(palette=self.theme)
        self._connect_execution_page()

        # The market page owns its widgets and reports intent; the stream
        # lifecycle, the credentials and the safety gates stay here.  It is told
        # which controls are open rather than deciding that itself.
        self.market_page = MarketPage(
            palette=self.theme,
            theme_name=self.current_theme_name,
            selected_provider=self.preferences.market_provider,
        )
        self._connect_market_page()

        pages: dict[str, QWidget] = {
            "dashboard": self._dashboard_tab(),
            "market": self.market_page,
            "account": self.account_page,
            "strategy": self.strategy_page,
            "risk": self.risk_page,
            "execution": self.execution_page,
            "research": self.research_page,
            "system": self.system_page,
        }
        assert set(pages) == set(ROUTES)
        # Paint strategy last, once every page exists.  Both runtime-selection
        # combos live on pages built just above, and the selection service is
        # what fills them, so this is the first moment a complete paint is
        # possible.  The retired page handler ran in the middle of this
        # construction, which is why the auto-rotation combo used to start up
        # empty: it had not been created yet.
        self._refresh_strategy_page()
        return pages

    def _connect_runtime_events_page(self) -> None:
        """Wire the runtime-events page's intents to the window handlers."""

        page = self.runtime_events_page
        page.refresh_requested.connect(self._refresh_runtime_events)
        page.resolve_requested.connect(self._resolve_runtime_event)
        page.export_requested.connect(self._export_terminal_state)

    def _connect_settings_page(self) -> None:
        """Wire the settings page's nine intent signals to the window."""

        page = self.settings_page
        page.theme_preview_requested.connect(self._preview_theme_changed)
        page.market_provider_selected.connect(
            self._settings_provider_selected
        )
        page.switch_provider_requested.connect(
            self._switch_to_settings_provider
        )
        page.api_provider_selected.connect(self._api_provider_changed)
        page.save_credentials_requested.connect(
            self._save_api_credentials
        )
        page.clear_credentials_requested.connect(
            self._clear_selected_api_credentials
        )
        page.paper_order_capability_toggled.connect(
            self._paper_order_capability_toggled
        )
        page.extended_hours_paper_toggled.connect(
            self._extended_hours_paper_toggled
        )
        page.save_preferences_requested.connect(
            self._save_user_preferences
        )

    def _settings_draft(self) -> SettingsDraft:
        """The window's current preferences as the page's initial draft."""

        preferences = self.preferences
        return SettingsDraft(
            theme=preferences.theme,
            market_provider=preferences.market_provider,
            ibkr_host=preferences.ibkr_host,
            ibkr_port=preferences.ibkr_port,
            ibkr_client_id=preferences.ibkr_client_id,
            connection_timeout_seconds=(
                preferences.connection_timeout_seconds
            ),
            paper_order_capability_enabled=(
                preferences.paper_order_capability_enabled
            ),
            extended_hours_paper_enabled=(
                preferences.extended_hours_paper_enabled
            ),
        )

    def _settings_storage_view(self) -> SettingsStorageView:
        """The four paths the storage section prints, already formatted."""

        state_root = self.paths.state_root
        return SettingsStorageView(
            settings_path=str(state_root / "settings"),
            credentials_path=str(state_root / "credentials"),
            runtime_path=str(self.paths.runtime_root),
            exports_path=str(self.paths.exports_root),
        )

    def _active_stream_provider(self) -> str | None:
        """The provider whose stream is live, or ``None`` when idle."""

        worker = self.stream_worker
        if worker is not None and worker.isRunning():
            return worker.source_id
        return None

    def _publish_settings_view(self) -> None:
        """Render one consistent settings view from the window's facts."""

        if not hasattr(self, "settings_page"):
            return
        provider = self._settings_api_provider
        has_credentials = provider in {"finnhub_trades", "alpaca_iex"}
        self.settings_page.render(
            SettingsPageView(
                credential_status_text=self._credential_status_text(
                    provider
                ),
                credential_save_enabled=has_credentials,
                credential_clear_enabled=(
                    has_credentials
                    and provider != self._active_stream_provider()
                ),
                connection_settings_enabled=(
                    self._connection_settings_enabled
                ),
            )
        )

    def _credential_status_text(self, provider: str) -> str:
        """The frozen credential status line for the selected API provider."""

        status = self.credential_service.status(provider)
        if provider == "finnhub_trades":
            return (
                "Finnhub：已加密保存"
                if status.api_key_saved
                else "Finnhub：未保存"
            )
        if provider == "alpaca_iex":
            return (
                "Alpaca Key："
                f"{'已加密保存' if status.api_key_saved else '未保存'}"
                " · Alpaca Secret："
                f"{'已加密保存' if status.api_secret_saved else '未保存'}"
            )
        return (
            "IBKR Gateway：使用本机 Host / 端口 / Client ID，"
            "无需 API Key"
        )

    def _runtime_info_text(self) -> str:
        """The read-only environment panel the runtime-events page renders."""

        return runtime_info_text(
            version="0.19.0",
            resource_root=self.paths.resource_root,
            state_root=self.paths.state_root,
            runtime_root=self.paths.runtime_root,
            exports_root=self.paths.exports_root,
        )

    def _connect_execution_page(self) -> None:
        """Wire the execution page's intents to the handlers that act on them.

        Every entry is a page signal and an existing window handler: the page
        reports what the operator asked for, and the orchestration that decides
        whether it may happen lives here, where the workflow and the services
        are.
        """

        page = self.execution_page
        page.strategy_selected.connect(self._auto_strategy_selected)
        page.preflight_inputs_changed.connect(self._refresh_auto_quant_preflight)
        page.prepare_requested.connect(self._prepare_auto_quant_candidates)
        page.channel_check_requested.connect(self._check_auto_order_channel)
        page.start_requested.connect(self._confirm_and_start_auto_quant)
        page.stop_stream_requested.connect(self._stop_auto_market_data)
        page.pause_requested.connect(self._pause_auto_quant_entries)
        page.resume_requested.connect(self._resume_auto_quant_entries)
        page.stop_requested.connect(self._stop_auto_quant)
        page.reconcile_requested.connect(self._reconnect_auto_order_service)
        page.resume_reconciliation_requested.connect(
            self._resume_auto_quant_from_reconciliation
        )

    def _connect_market_page(self) -> None:
        """Wire the market page's intents to the handlers that act on them.

        The provider signal is the one entry that fires for an *operator* change
        only: the programmatic setter used to restore a saved preference and to
        sync the settings panel is deliberately silent, so the two combos cannot
        drive each other.
        """

        page = self.market_page
        page.provider_selected.connect(self._stream_provider_selected)
        page.start_requested.connect(self._start_stream)
        page.stop_requested.connect(self._stop_stream)
        page.load_scan_watchlist_requested.connect(self._apply_intraday_watchlist)

    def _connect_targeted_validation_page(self) -> None:
        """Wire the targeted page's intents to the existing window handlers."""

        page = self.targeted_validation_page
        page.strategy_selected.connect(self._shadow_strategy_selection_changed)
        page.target_apply_requested.connect(self._target_symbol_requested)
        page.target_subscribe_requested.connect(self._target_subscribe_requested)
        page.shadow_start_requested.connect(self._start_shadow)
        page.shadow_stop_requested.connect(self._stop_shadow)
        page.replay_requested.connect(self._run_targeted_replay)
        page.robustness_requested.connect(self._run_targeted_robustness)
        page.robustness_run_selected.connect(self._robustness_run_selected)
        page.review_run_selected.connect(self._review_run_selected)

    def _connect_universe_page(self) -> None:
        page = self.universe_page
        page.refresh_requested.connect(self._refresh_universe)
        page.cancel_refresh_requested.connect(self._cancel_universe_refresh)

    def _connect_history_page(self) -> None:
        page = self.history_page
        page.schedule_requested.connect(self._schedule_history)
        page.run_ibkr_requested.connect(self._run_history)
        page.run_public_requested.connect(self._run_public_history)
        page.retry_failed_requested.connect(self._retry_failed)

    def _connect_scanner_page(self) -> None:
        page = self.scanner_page
        page.scan_requested.connect(self._run_scan)
        page.symbol_selected.connect(self._scanner_symbol_selected)

    def _connect_cross_section_page(self) -> None:
        page = self.cross_section_page
        page.run_requested.connect(self._run_cross_section_research)
        page.capital_changed.connect(self._research_capital_changed)

    def _publish_cross_section_view(self) -> None:
        self.cross_section_page.render(
            build_cross_section_view(self.cross_section_report)
        )

    def _target_symbol_requested(self, symbol: str) -> None:
        self.targeted_validation_page.set_target_symbol(symbol)
        self._apply_target_symbol()

    def _target_subscribe_requested(self, symbol: str) -> None:
        self.targeted_validation_page.set_target_symbol(symbol)
        self._sync_targeted_symbol_to_stream()

    def _robustness_run_selected(self, run_id: str) -> None:
        self._selected_robustness_run_id = run_id
        self._publish_targeted_view()

    def _review_run_selected(self, run_id: str) -> None:
        self._selected_review_run_id = run_id
        self._publish_targeted_view()

    def _targeted_controls(self) -> TargetedControlView:
        shadow_active = bool(
            self.shadow_snapshot is not None and self.shadow_snapshot.active
        )
        return TargetedControlView(
            strategy_enabled=not shadow_active,
            target_enabled=not shadow_active,
            subscribe_enabled=not shadow_active,
            shadow_start_enabled=not shadow_active,
            shadow_stop_enabled=shadow_active,
            replay_enabled=True,
            robustness_enabled=True,
        )

    def _publish_universe_view(self) -> None:
        """Project the universe business state onto the native page."""

        if not hasattr(self, "universe_page"):
            return
        cancel_event = self.universe_refresh_cancel_event
        self.universe_page.render(
            build_universe_view(
                self.universe,
                refreshing=self.universe_refresh_worker is not None,
                cancel_requested=bool(cancel_event and cancel_event.is_set()),
            )
        )

    def _publish_history_view(self) -> None:
        """Project the history queue state onto the native page."""

        if not hasattr(self, "history_page"):
            return
        self.history_page.render(
            build_history_view(
                self.history_service.snapshot(),
                progress_percent=self._history_progress_percent,
            )
        )

    def _publish_scanner_view(self) -> None:
        """Project the scan truth onto the native scanner page."""

        if not hasattr(self, "scanner_page"):
            return
        if self.universe is not None:
            research_count = int(
                self.universe.summary()["research_eligible"]
            )
        elif self.scan is not None:
            research_count = len(self.scan.results) + len(self.scan.skipped)
        else:
            research_count = 0
        self.scanner_page.render(
            build_scanner_view(
                self.scan,
                research_count=research_count,
            )
        )

    def _scanner_symbol_selected(self, symbol: str) -> None:
        try:
            points = load_close_series(
                symbol,
                data_root=self.data_root,
                fallback_data_root=self.bundled_data_root,
            )
        except Exception as error:
            self._log(f"{symbol} 图表读取失败：{error}")
            return
        self.scanner_page.render_chart(
            ScannerChartView(symbol=symbol, points=points)
        )

    def _publish_targeted_view(self) -> None:
        """Project targeted business state and render it on the native page."""

        if not hasattr(self, "targeted_validation_page"):
            return
        session = session_view(
            snapshot=self.shadow_snapshot,
            target_status=self._target_status,
            minute_status=self._minute_status,
            preflight=self.target_preflight_result,
            controls=self._targeted_controls(),
        )
        evidence = evidence_view(
            replay_results=tuple(self.targeted_replay_results),
            robustness_results=tuple(self.targeted_robustness_results),
            walk_forward_results=tuple(self.targeted_walk_forward_results),
            overfit_results=tuple(self.targeted_overfit_results),
            data_quality_results=tuple(self.targeted_data_quality_results),
            execution_stress_results=tuple(
                self.targeted_execution_stress_results
            ),
            review_results=tuple(self.targeted_review_results),
            selected_robustness_run_id=self._selected_robustness_run_id,
            selected_review_run_id=self._selected_review_run_id,
        )
        active_workspace = self._targeted_active_workspace
        active_evidence_tab = self._targeted_active_evidence_tab
        self._targeted_active_workspace = None
        self._targeted_active_evidence_tab = None
        self.targeted_validation_page.render(
            TargetedValidationView(
                session=session,
                evidence=evidence,
                active_workspace=active_workspace,
                active_evidence_tab=active_evidence_tab,
            )
        )

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
        """Size a selection combo the way every combo in the workbench is."""

        configure_combo_width(
            combo,
            minimum_width=minimum_width,
            minimum_contents=minimum_contents,
        )

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
        gateway_button = QPushButton("仅检查 Gateway 端口")
        gateway_button.clicked.connect(self._probe_gateway)
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



    def _configure_table(self, table: QTableWidget) -> None:
        """Apply the workbench's shared read-only table behaviour."""

        configure_table(table)

    def _configure_table_view(self, table: QTableView) -> None:
        """Apply shared behavior to model-backed high-frequency tables."""

        configure_table(table)

    def _load_local_state(self) -> None:
        if self.universe_path.exists():
            try:
                self.universe = load_universe_snapshot(
                    self.universe_path
                )
            except Exception as error:
                self._log(f"标的快照读取失败：{error}")
        self._publish_universe_view()
        self._populate_artifact_table()
        self._publish_history_view()
        self._refresh_cards()
        self._probe_gateway()
        if self.scan_path.exists():
            self._load_scan_file()
        self._refresh_market_scope_summary()
        if self.cross_section_path.exists():
            self._load_cross_section_report()
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
        self.targeted_robustness_results = list(
            load_targeted_robustness(
                self.paths.research_results_root
                / "targeted_robustness"
            )
        )
        self.targeted_walk_forward_results = list(
            load_targeted_walk_forwards(
                self.paths.research_results_root
                / "targeted_walk_forward"
            )
        )
        self.targeted_overfit_results = list(
            load_targeted_overfits(
                self.paths.research_results_root
                / "targeted_overfit"
            )
        )
        self.targeted_data_quality_results = list(
            load_targeted_data_quality(
                self.paths.research_results_root
                / "targeted_data_quality"
            )
        )
        self.targeted_execution_stress_results = list(
            load_targeted_execution_stress(
                self.paths.research_results_root
                / "targeted_execution_stress"
            )
        )
        self.targeted_review_results = list(
            load_targeted_reviews(
                self.paths.research_results_root
                / "targeted_review"
            )
        )
        self._publish_targeted_view()

    def _refresh_universe(self) -> None:
        cancel_event = Event()

        def task(progress: Callable[[str], None]) -> UniverseSnapshot:
            def report(event: UniverseRefreshProgress) -> None:
                if event.stage == STAGE_PREPARE_REFERENCE:
                    progress("正在准备可写的用户参考数据目录…")
                elif event.stage == STAGE_DOWNLOAD_OFFICIAL:
                    progress("正在下载 Nasdaq Trader 与 SEC 官方标的清单…")
                elif event.stage == STAGE_ENRICH_SEC_START:
                    progress("正在增量核验 500 家 SEC 注册地与行业…")
                elif event.stage == STAGE_ENRICH_SEC:
                    progress(
                        f"SEC 核验 {event.done}/{event.total}：{event.detail}"
                    )
                else:
                    raise ValueError(
                        f"unknown universe refresh stage: {event.stage}"
                    )

            return self.universe_service.refresh(
                should_stop=cancel_event.is_set,
                progress=report,
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
        self._publish_universe_view()

    def _cancel_universe_refresh(self) -> None:
        if self.universe_refresh_cancel_event is None:
            return
        self.universe_refresh_cancel_event.set()
        self._publish_universe_view()
        self._log("已请求取消官方标的刷新；当前网络请求最多再等待 8 秒。")

    def _reset_universe_refresh_controls(self) -> None:
        self.universe_refresh_cancel_event = None
        self.universe_refresh_worker = None
        self._publish_universe_view()

    def _universe_refreshed(self, result: object) -> None:
        self.universe = result  # type: ignore[assignment]
        self.universe_path = (
            self.reference_root / "universe.json"
        )
        self._publish_universe_view()
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
        result = self.history_service.schedule_universe(self.universe)
        self._publish_history_view()
        self._refresh_market_scope_summary()
        self._log(
            f"全部非中概研究池已加入历史队列：新增 {result.inserted} 个，"
            f"队列合计 {result.total:,} 个；"
            "下载仍按页面所选批量执行。"
        )

    def _run_history(self, maximum_jobs: int) -> None:
        def task(progress: Callable[[str], None]) -> dict[str, int]:
            return self.history_service.run_ibkr(
                self.config.ibkr,
                maximum_jobs=maximum_jobs,
                progress=lambda done, total, symbol, status: (
                    progress(f"{done}/{total} {symbol}：{status}")
                ),
            )

        self._history_progress_percent = 1
        self._publish_history_view()
        self._start_task(
            task,
            on_success=self._history_finished,
            on_failure=self._history_task_failed,
            start_message="IBKR 历史日 K 下载中…",
            resource_group="history",
        )

    def _history_finished(self, result: object) -> None:
        counts: dict[str, int] = result  # type: ignore[assignment]
        total = sum(counts.values())
        completed = counts.get("completed", 0)
        self._history_progress_percent = (
            int(completed / total * 100) if total else 0
        )
        self._publish_history_view()
        self._refresh_cards()
        self._refresh_market_scope_summary()
        self._log(
            f"本批结束：累计完成 {completed}，"
            f"失败 {counts.get('failed', 0)}。"
        )

    def _history_task_failed(self, _message: str) -> None:
        self._history_progress_percent = 0
        self._publish_history_view()

    def _run_public_history(self, maximum_jobs: int) -> None:
        def task(progress: Callable[[str], None]) -> dict[str, int]:
            return self.history_service.run_public(
                maximum_jobs=maximum_jobs,
                progress=lambda done, total, symbol, status: (
                    progress(f"{done}/{total} {symbol}：{status}")
                ),
            )

        self._history_progress_percent = 1
        self._publish_history_view()
        self._start_task(
            task,
            on_success=self._history_finished,
            on_failure=self._history_task_failed,
            start_message=(
                "备用免费日 K 下载中；只用于历史研究，"
                "不会替代 IBKR 实时行情…"
            ),
            resource_group="history",
        )

    def _retry_failed(self) -> None:
        count = self.history_service.reset_failed()
        self._publish_history_view()
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
            return self.market_scan_service.scan(
                self.universe,
                capital=research_capital,
                max_position_risk_pct=(
                    self.config.risk_limits.max_position_exposure_pct
                ),
                substitutions=self.config.substitutions,
            )

        self._start_task(
            task,
            on_success=self._scan_finished,
            start_message="市场扫描中…",
            resource_group="scan",
        )

    def _scan_finished(self, result: object) -> None:
        self.scan = result  # type: ignore[assignment]
        self._publish_scanner_view()
        self._refresh_cards()
        self._refresh_market_scope_summary()
        summary = self.scan.summary()
        self._log(
            f"扫描完成：{summary['scanned']} 个，"
            f"趋势候选 {summary['positive_signal']} 个。"
        )

    def _connect_backtest_page(self) -> None:
        page = self.backtest_page
        page.run_selected_requested.connect(self._run_selected_backtest)
        page.compare_all_requested.connect(self._run_all_backtests)
        page.run_selected.connect(self._backtest_run_selected)

    def _publish_backtest_strategy_options(self) -> None:
        if not hasattr(self, "backtest_page"):
            return
        versions = self.strategy_selection.options(
            StrategySelectionPurpose.BACKTEST
        )
        order = {
            spec.strategy_id: index
            for index, spec in enumerate(STRATEGY_SPECS)
        }
        ordered = sorted(
            versions,
            key=lambda item: (
                order.get(item.strategy_id, 999),
                item.semver,
            ),
        )
        self.backtest_page.set_strategy_options(
            tuple(
                BacktestStrategyOption(
                    version.version_id,
                    f"{version.name} · {version.semver}",
                )
                for version in ordered
            )
        )

    def _publish_backtest_view(self) -> None:
        if not hasattr(self, "backtest_page"):
            return
        self.backtest_page.render(
            build_backtest_view(
                tuple(self.backtest_runs),
                self._selected_backtest_run_id,
                busy=self._backtest_busy,
            )
        )

    def _backtest_run_selected(self, run_id: str) -> None:
        self._selected_backtest_run_id = run_id
        self._publish_backtest_view()

    def _backtest_records(
        self,
        compare_all: bool,
        selected_version_id: str,
    ) -> list[StrategyVersion]:
        versions = self.strategy_selection.options(
            StrategySelectionPurpose.BACKTEST
        )
        if compare_all:
            latest: dict[str, StrategyVersion] = {}
            for version in versions:
                latest.setdefault(version.strategy_id, version)
            return [
                latest[spec.strategy_id]
                for spec in STRATEGY_SPECS
                if spec.strategy_id in latest
            ]
        return [
            version
            for version in versions
            if version.version_id == selected_version_id
        ]

    def _run_selected_backtest(self, draft: BacktestFormDraft) -> None:
        self._run_backtest_workspace(False, draft)

    def _run_all_backtests(self, draft: BacktestFormDraft) -> None:
        self._run_backtest_workspace(True, draft)

    def _run_backtest_workspace(
        self,
        compare_all: bool,
        draft: BacktestFormDraft,
    ) -> None:
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
        records = self._backtest_records(
            compare_all,
            draft.strategy_version_id,
        )
        if not records:
            QMessageBox.warning(
                self,
                "没有可运行版本",
                "策略目录中没有与回测工厂匹配的研究版本。",
            )
            return
        if draft.start_date > draft.end_date:
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
                symbol=draft.symbol,
                start_date=draft.start_date,
                end_date=draft.end_date,
                initial_equity=Decimal(draft.initial_equity),
                target_weight=(
                    Decimal(draft.target_weight_percent)
                    / Decimal("100")
                ),
                per_share_commission=Decimal(
                    draft.per_share_commission
                ),
                minimum_commission=Decimal(
                    draft.minimum_commission
                ),
                slippage_bps=Decimal(draft.slippage_bps),
            )
            for record in records
        ]

        def task(
            progress: Callable[[str], None],
        ) -> tuple[BacktestRun, ...]:
            return self.backtest_service.run(
                requests,
                on_progress=lambda index, total, request: progress(
                    f"回测 {index}/{total}："
                    f"{request.strategy_id} {request.symbol}"
                ),
            )

        self._backtest_busy = True
        self._publish_backtest_view()
        started = self._start_task(
            task,
            on_success=self._backtest_workspace_finished,
            on_failure=self._backtest_task_failed,
            start_message=(
                f"正在运行 {len(requests)} 个版本绑定回测…"
            ),
            resource_group="backtest",
        )
        if not started:
            self._backtest_busy = False
            self._publish_backtest_view()

    def _backtest_task_failed(self, _message: str) -> None:
        self._backtest_busy = False
        self._publish_backtest_view()

    def _backtest_workspace_finished(self, result: object) -> None:
        self._backtest_busy = False
        runs = list(result)  # type: ignore[arg-type]
        self.backtest_runs = runs
        self._selected_backtest_run_id = (
            runs[0].run_id if runs else None
        )
        self._publish_backtest_view()
        self._log(
            f"回测完成：{len(runs)} 个不可变 run 已保存到用户研究目录"
        )

    def _run_cross_section_research(
        self,
        draft: CrossSectionResearchDraft,
    ) -> None:
        if self.universe is None:
            QMessageBox.information(
                self,
                "缺少标的池",
                "请先刷新官方标的。",
            )
            return

        self._research_capital_value = draft.research_capital
        research_capital = Decimal(draft.research_capital)

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
                Path(self.cross_section_path),
            )
            return result

        self._start_task(
            task,
            on_success=self._cross_section_finished,
            start_message="组合走样本外研究开始…",
            resource_group="strategy",
        )

    def _cross_section_finished(self, result: object) -> None:
        self.cross_section_report = result  # type: ignore[assignment]
        self._publish_cross_section_view()
        self.artifact_catalog = load_artifact_catalog(
            self.paths.research_results_root
        )
        self._populate_artifact_table()
        metrics = self.cross_section_report["out_of_sample"]["strategy"]
        self._log(
            f"组合研究完成：OOS {metrics['total_return']:+.1%}，"
            f"最大回撤 {metrics['max_drawdown']:.1%}。"
        )

    def _load_cross_section_report(self) -> None:
        try:
            self.cross_section_report = json.loads(
                self.cross_section_path.read_text(encoding="utf-8")
            )
            # Projection is part of the load guard: valid JSON can still be a
            # partially written or incompatible report, and startup must not
            # fail because of one bad research artifact.
            self._publish_cross_section_view()
        except Exception as error:
            self.cross_section_report = None
            self._log(
                f"风险一致研究产物读取失败："
                f"{type(error).__name__}: {error}"
            )
            self._publish_cross_section_view()

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
            self._publish_scanner_view()
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
            self.market_page.set_subscription_symbols(symbols)
            self._market_watchlist_note = "实时订阅子集；不限制研究或交易范围"
            self._publish_market_controls()
            self._log(
                f"已从 {len(self.scan.results):,} 个最近扫描结果中选出 "
                f"{len(symbols)} 个实时订阅代码；"
                "30 是行情连接上限，不是广域股票池大小"
            )

    def _auto_quant_preflight(self) -> AutoQuantPreflight:
        strategy = self._selected_auto_strategy_record()
        # Statuses are compared as enum members, not as raw strings: the
        # vocabulary is owned by the domain now, and a comparison against
        # "research" would keep passing if the domain renamed it.
        strategy_eligible = (
            strategy is not None
            and strategy.strategy_id == "intraday-auto-rotation"
            and strategy.status
            in {StrategyStatus.RESEARCH, StrategyStatus.PAPER_SHADOW}
            and (
                strategy.status is StrategyStatus.RESEARCH
                or strategy.gate_passed
            )
        )
        strategy_detail = (
            f"{strategy.semver} · {strategy.status}"
            if strategy is not None
            else "请选择自动轮动策略版本"
        )
        readiness = calculate_quote_readiness_breakdown(
            self.stream_snapshot
            if self.stream_snapshot is not None
            else (),
            candidate_symbols=(
                row.symbol for row in self.auto_quant_candidates
            ),
            reference_symbols=self._auto_quant_market_reference_symbols(),
            recently_ready_symbols=(
                symbol
                for symbol in self._quote_last_ready_monotonic
                if self._quote_was_recently_ready(symbol)
            ),
        )
        minimum_realtime_quotes = (
            3
            if us_equity_session() is USEquitySession.REGULAR
            else 1
        )
        return evaluate_auto_quant_preflight(
            capability_enabled=(
                self.preferences.paper_order_capability_enabled
            ),
            paper_confirmed=self.execution_page.arm_confirmed(),
            strategy_eligible=strategy_eligible,
            strategy_detail=strategy_detail,
            candidate_count=readiness.candidate_count,
            realtime_ready_count=readiness.candidate_current_count,
            paper_capital=self._paper_simulation_capital(),
            recent_ready_count=readiness.candidate_recent_count,
            minimum_realtime_quotes=minimum_realtime_quotes,
        )

    def _auto_quant_market_reference_symbols(self) -> tuple[str, ...]:
        strategy = self._selected_auto_strategy_record()
        return tuple(
            dict.fromkeys(
                str(symbol).strip().upper()
                for symbol in (
                    strategy.parameters.get("market_reference_symbols", [])
                    if strategy is not None
                    else []
                )
                if str(symbol).strip()
            )
        )

    def _refresh_auto_quant_preflight(
        self, *_args: object
    ) -> None:
        if not hasattr(self, "execution_page"):
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
        self.execution_page.render_preflight(
            ready_count, len(displayed_checks), details
        )

    def _check_auto_order_channel(self) -> None:
        if self._channel_check_inflight:
            # The probe owns the route while it runs, so a second request can
            # only come from a path that ignored the disabled control.  The
            # broker resource group would refuse the second worker anyway, and
            # that refusal must not clear the first probe's flag.
            return
        if (
            self.paper_trading.has_order_service()
            or self.trading_runtime is not None
        ):
            QMessageBox.information(
                self,
                "Paper 会话正在使用",
                "当前自动量化会话已占用订单通道，无需重复检查。",
            )
            return
        self._channel_check_inflight = True
        self._apply_paper_workflow_button_state()
        order_config = IBKRConnectionConfig(
            host=self.preferences.ibkr_host,
            port=4002,
            # P1-6: the order channel must always use a client id distinct
            # from the read-only connection, including when the configured id
            # sits near the 999999 cap (modulo keeps it in [0, 999999]).
            client_id=(
                (self.preferences.ibkr_client_id + 100) % 1_000_000
            ),
            api_read_only=False,
            paper_order_submission_enabled=True,
            connection_timeout_seconds=(
                self.preferences.connection_timeout_seconds
            ),
        )

        def task(progress: Callable[[str], None]):
            progress("连接 IBKR Paper 订单通道并读取账户/订单；不下单…")
            return self.paper_trading.probe_order_channel(
                config=order_config,
                repository=self.order_repository,
                extended_hours_enabled=(
                    self.preferences.extended_hours_paper_enabled
                ),
            )

        started = self._start_task(
            task,
            on_success=self._auto_order_channel_checked,
            on_failure=self._auto_order_channel_failed,
            start_message="正在检查 IBKR Paper 订单通道（不下单）…",
            resource_group="broker",
        )
        if not started:
            # This attempt never owned the route: the broker group was busy or
            # the window is closing.  Only this attempt's own flag is released.
            self._channel_check_inflight = False
            self._apply_paper_workflow_button_state()

    def _auto_order_channel_failed(self, _message: str) -> None:
        """Release the probe's own lock; ``_start_task`` then reports failure."""

        self._channel_check_inflight = False
        self._apply_paper_workflow_button_state()

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
        self._channel_check_inflight = False
        self._apply_paper_workflow_button_state()
        detail = (
            f"{account_alias} · 净值 {_money(net_liquidation)} · "
            f"现金 {_money(cash)} · 持仓 {positions} · "
            f"开放 API 订单 {open_orders} · 本地待对账 {unresolved}"
        )
        self.execution_page.render_execution_health(
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
            self.trading_runtime is not None
            and self.trading_runtime.session.active
        ):
            QMessageBox.information(
                self,
                "自动量化运行中",
                "请先停止并完成 Paper 持仓对账，再更换候选集。",
            )
            return
        try:
            self.paper_workflow.begin_preparing()
        except WorkflowStateError as error:
            QMessageBox.information(self, "Paper 会话不可准备", str(error))
            return
        self._set_launch_busy(True)
        self.execution_page.render_context(
            summary=(
                "正在扫描全部非中概研究池；只有历史数据质量达标的标的"
                "才会进入实时轮动候选。"
            )
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
            on_failure=self._auto_candidate_preparation_failed,
            start_message="全市场扫描与 Paper 候选准备中…",
            resource_group="scan",
        )
        if not started:
            self.paper_workflow.cancel_preparing()
            self._set_launch_busy(False)

    def _auto_candidate_preparation_failed(self, _message: str) -> None:
        """Release PREPARING after an asynchronous scan failure."""

        if self.paper_trading.phase() is PaperWorkflowPhase.PREPARING:
            self.paper_workflow.cancel_preparing()
        self._set_launch_busy(False)

    def _auto_market_scan_finished(self, result: object) -> None:
        if not isinstance(result, MarketScan):
            raise TypeError("unexpected full-market scan result")
        self.scan = result
        scheduled = HistoryJobStore(self.queue_path).schedule(
            prioritized_research_symbols(self.universe, limit=None)
            if self.universe is not None
            else ()
        )
        self._publish_history_view()
        self._publish_scanner_view()
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
            if self.paper_trading.phase() is PaperWorkflowPhase.PREPARING:
                self.paper_workflow.cancel_preparing()
            self._set_launch_busy(False)
            return
        limit = self.execution_page.candidate_limit()
        paper_capital = self._paper_simulation_capital()
        if paper_capital is None:
            if self.paper_trading.phase() is PaperWorkflowPhase.PREPARING:
                self.paper_workflow.cancel_preparing()
            self._set_launch_busy(False)
            QMessageBox.information(
                self,
                "需要新鲜的 Paper 资金",
                "请先在“账户与持仓”刷新 IBKR Paper 账户。"
                "自动候选会按模拟账户资金筛选，不再套用 1500 美元"
                "历史研究情景。",
            )
            return
        requested_limit = self.execution_page.capital_limit()
        if requested_limit > 0:
            paper_capital = min(paper_capital, requested_limit)
        multipliers = self._configured_exposure_multipliers()
        selected_strategy = self._selected_auto_strategy_record()
        market_references = tuple(
            dict.fromkeys(
                str(symbol).strip().upper()
                for symbol in (
                    selected_strategy.parameters.get(
                        "market_reference_symbols", []
                    )
                    if selected_strategy is not None
                    else []
                )
                if str(symbol).strip()
            )
        )
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
            liquidity_first=(
                us_equity_session() is not USEquitySession.REGULAR
            ),
            excluded_symbols=market_references,
        )
        candidates: list[AutoQuantCandidate] = []
        for row in eligible:
            symbol = row.execution_symbol.strip().upper()
            if symbol in market_references:
                continue
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
            if self.paper_trading.phase() is PaperWorkflowPhase.PREPARING:
                self.paper_workflow.cancel_preparing()
            QMessageBox.warning(
                self,
                "合格候选不足",
                (
                    f"最新扫描只有 {len(candidates)} 个满足非中概、"
                    "龙头/优质二线、Paper 整股容量和数据门的候选；"
                    "至少需要 3 个才启动自动轮动。"
                ),
            )
            self._set_launch_busy(False)
            return
        self.auto_quant_candidates = tuple(candidates)
        if self.paper_trading.phase() is PaperWorkflowPhase.PREPARING:
            self.paper_workflow.mark_ready()
        symbols = tuple(row.symbol for row in candidates)
        research_count = int(
            self.universe.summary()["research_eligible"]
        )
        self.execution_page.render_context(
            scope=(
                f"全市场入口：非中概研究池 {research_count:,} · "
                f"本轮有合格日 K 并完成评分 {len(self.scan.results):,} · "
                f"缺数据/不足200根 {len(self.scan.skipped):,} · "
                f"Paper 实时轮动候选 {len(symbols)}。"
            ),
            summary=(
                f"已从全市场扫描中整理 {len(symbols)} 个实时轮动候选。"
                "行情订阅只承担分钟信号，不代表扫描范围只有这些代码；"
                "全部订单仍未武装。"
            ),
        )
        stream_symbols = tuple(dict.fromkeys(symbols + market_references))
        self.market_page.set_subscription_symbols(stream_symbols)
        self._set_launch_busy(False)
        self._populate_auto_quant_candidates()
        if (
            self.stream_worker is not None
            and self.stream_worker.isRunning()
        ):
            self.execution_page.render_context(
                summary=f"已整理 {len(symbols)} 个候选，正在安全停止旧行情并切换。"
            )
            self._request_stream_switch(
                str(self.market_page.selected_provider() or "finnhub_trades")
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
            self.execution_page.render_context(
                summary="当前行情已停止。可重新点击第 1 步准备新的候选。"
            )

    def _confirm_and_start_auto_quant(self) -> None:
        if self._active_auto_launch_plan is not None:
            QMessageBox.information(
                self,
                "Paper 会话正在连接",
                "当前启动检查仍在进行中，请等待本次连接完成或失败后再试。",
            )
            return
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
            self.execution_page.set_arm_confirmed(False)
            return
        self.execution_page.set_arm_confirmed(True)
        self._start_auto_quant()

    def _reset_auto_launch_controls(
        self, plan: AutoLaunchPlan
    ) -> bool:
        """Reset only the controls owned by a completed launch attempt."""

        active = self._active_auto_launch_plan
        if active is None or active.attempt_id != plan.attempt_id:
            return False
        self._active_auto_launch_plan = None
        self.execution_page.set_arm_confirmed(False)
        self._apply_paper_workflow_button_state()
        return True

    def _current_auto_launch_matches(
        self, plan: AutoLaunchPlan
    ) -> bool:
        strategy = self._selected_auto_strategy_record()
        return strategy is not None and auto_launch_plan_matches(
            plan,
            strategy_version_id=strategy.version_id,
            parameter_hash=strategy.parameter_hash,
            candidate_symbols=(
                row.symbol for row in self.auto_quant_candidates
            ),
            requested_capital_limit=self.execution_page.capital_limit(),
        )

    def _reject_unpublished_auto_candidate(
        self,
        candidate_id: object,
        plan: AutoLaunchPlan,
        message: str,
        *,
        show_message: bool,
    ) -> None:
        """Dispose a late/pre-arm candidate without touching a newer attempt.

        Only this candidate is discarded -- the active order service and every
        other candidate are untouched.  The workflow rejection runs in a
        ``finally`` so a failing broker disconnect can never leave the session
        stuck in ``CONNECTING``; the disconnect error itself still propagates.
        """

        try:
            if self.paper_trading.has_candidate(candidate_id):
                self.paper_trading.discard_candidate(candidate_id)
        finally:
            self.paper_workflow.reject_connecting(plan)
            reset = self._reset_auto_launch_controls(plan)
            self._log(message)
            if reset and show_message:
                QMessageBox.warning(self, "Paper 会话未启动", message)

    def _reject_auto_launch_without_service(
        self,
        plan: AutoLaunchPlan,
        message: str,
    ) -> None:
        """Finish a failed connection without affecting another launch."""

        self.paper_workflow.reject_connecting(plan)
        reset = self._reset_auto_launch_controls(plan)
        self._log(message)
        if reset:
            QMessageBox.warning(self, "Paper 会话未启动", message)

    def _populate_auto_quant_candidates(self) -> None:
        """Re-render the candidate and context surfaces from current facts.

        The page owns the candidate table's static-key optimisation; the window
        only supplies the candidates and the stream facts, and re-runs the two
        context lines that are not session state.
        """

        self._render_auto_quant_snapshot()
        self._refresh_auto_quant_preflight()
        self._refresh_extended_hours_status()

    def _start_auto_quant(self) -> None:
        if self._active_auto_launch_plan is not None:
            QMessageBox.information(
                self,
                "Paper 会话正在连接",
                "当前启动检查仍在进行中，请不要重复启动。",
            )
            return
        if (
            self.shadow_engine is not None
            and self.shadow_engine.active
        ):
            self.execution_page.set_arm_confirmed(False)
            QMessageBox.warning(
                self,
                "内部仿真仍在运行",
                "同一资金真值不能同时运行内部仿真和 IBKR Paper 自动量化。",
            )
            return
        preflight = self._auto_quant_preflight()
        if not preflight.ready:
            self.execution_page.set_arm_confirmed(False)
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
        requested_capital_limit = self.execution_page.capital_limit()
        self._next_auto_launch_attempt += 1
        plan = build_auto_launch_plan(
            attempt_id=self._next_auto_launch_attempt,
            strategy_version_id=strategy.version_id,
            parameter_hash=strategy.parameter_hash,
            candidate_symbols=(
                row.symbol for row in self.auto_quant_candidates
            ),
            requested_capital_limit=requested_capital_limit,
        )
        self._active_auto_launch_plan = plan
        try:
            self.paper_workflow.begin_connecting(plan)
        except WorkflowStateError as error:
            self._active_auto_launch_plan = None
            QMessageBox.information(self, "Paper 会话不可启动", str(error))
            return
        self._apply_paper_workflow_button_state()
        self.execution_page.render_context(
            summary="正在连接独立 IBKR Paper 订单会话并核验唯一 DU 账户…"
        )
        order_config = IBKRConnectionConfig(
            host=self.preferences.ibkr_host,
            port=4002,
            # P1-6: the order channel must always use a client id distinct
            # from the read-only connection, including when the configured id
            # sits near the 999999 cap (modulo keeps it in [0, 999999]).
            client_id=(
                (self.preferences.ibkr_client_id + 100) % 1_000_000
            ),
            api_read_only=False,
            paper_order_submission_enabled=True,
            connection_timeout_seconds=(
                self.preferences.connection_timeout_seconds
            ),
        )

        candidate_id = str(plan.attempt_id)

        def task(progress: Callable[[str], None]):
            progress("连接 IBKR Paper 订单通道…")
            try:
                connection = self.paper_trading.connect_candidate(
                    candidate_id,
                    config=order_config,
                    repository=self.order_repository,
                    extended_hours_enabled=(
                        self.preferences.extended_hours_paper_enabled
                    ),
                )
            except Exception as error:
                return candidate_id, plan, str(error)
            progress(
                f"已核验 {connection.account_alias}；准备逐会话武装…"
            )
            return candidate_id, plan, None

        started = self._start_task(
            task,
            on_success=self._auto_order_service_connected,
            start_message="IBKR Paper 自动量化连接中…",
            resource_group="broker",
        )
        if not started:
            self.paper_workflow.reject_connecting(plan)
            self._reset_auto_launch_controls(plan)

    def _auto_order_service_connected(self, result: object) -> None:
        try:
            candidate_id, plan, connection_error = result  # type: ignore[misc]
        except (TypeError, ValueError) as error:
            raise TypeError(
                "unexpected auto order connection result"
            ) from error
        if not isinstance(plan, AutoLaunchPlan):
            raise TypeError("unexpected Paper launch plan")
        if connection_error is not None:
            self._reject_auto_launch_without_service(
                plan,
                "IBKR Paper 连接失败，未启动会话："
                f"{connection_error}",
            )
            return
        if self._active_auto_launch_plan != plan:
            self._reject_unpublished_auto_candidate(
                candidate_id,
                plan,
                "已忽略过期的 Paper 连接结果；不会武装订单会话。",
                show_message=False,
            )
            return
        preflight = self._auto_quant_preflight()
        if not preflight.ready:
            self._reject_unpublished_auto_candidate(
                candidate_id,
                plan,
                "连接期间启动条件发生变化，已断开未武装的 Paper 会话。",
                show_message=True,
            )
            return
        if not self._current_auto_launch_matches(plan):
            self._reject_unpublished_auto_candidate(
                candidate_id,
                plan,
                "连接期间策略、候选或资金上限已变化；已断开未武装的 Paper 会话。",
                show_message=True,
            )
            return
        try:
            # Borrowed for this call stack only: the window never stores it,
            # never assigns it to a member, and never keeps it past promotion.
            service = self.paper_trading.candidate_service(candidate_id)
            broker_state = service.broker_state()
            if (
                broker_state.net_liquidation is None
                or broker_state.net_liquidation <= 0
            ):
                raise ExecutionRefused(
                    "IBKR Paper 订单会话未返回有效净值"
                )
            if broker_state.positions:
                symbols = ", ".join(
                    row.symbol for row in broker_state.positions
                )
                raise ExecutionRefused(
                    "首期自动量化要求 Paper 账户启动时空仓；"
                    f"当前持仓：{symbols}"
                )
            if broker_state.cash is None:
                raise ExecutionRefused(
                    "IBKR Paper 订单会话未返回现金；"
                    "禁止使用保证金借款代替现金"
                )
            paper_capital = resolve_paper_session_capital(
                net_liquidation=broker_state.net_liquidation,
                cash=broker_state.cash,
                requested_limit=plan.requested_capital_limit,
            )
            strategy = self.strategies.get_version(
                plan.strategy_version_id
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
            )
            # One risk authority, built from the configuration this window is
            # actually running with, and injected.  It used to be split: the
            # account limits went into ``ShadowConfig.layered_risk_limits``
            # while the engine read a separate constructor argument that was
            # never passed here, so the configured ``risk_limits`` reached a
            # field nobody read.
            risk = self._build_auto_quant_risk()
            # The execution application is bound to the *same* channel this
            # session is arming and will publish, and to the one order store
            # the window opened at construction.  Binding it from the borrowed
            # candidate means the engine cannot be handed an application that
            # talks to a different broker session than the coordinator reads.
            execution = build_execution_application(
                repository=self.order_repository,
                broker=service,
            )
            # The runtime is assembled through the composition root, from the
            # risk and execution services this window built, so the session
            # dispatches through the same authorities it renders.
            runtime = build_trading_runtime(
                config=config,
                candidates=self.auto_quant_candidates,
                identity=strategy.identity,
                risk=risk,
                execution=execution,
                market_reference_symbols=tuple(
                    dict.fromkeys(
                        str(symbol).strip().upper()
                        for symbol in strategy.parameters.get(
                            "market_reference_symbols", []
                        )
                        if str(symbol).strip()
                    )
                ),
            )
            snapshot = runtime.start()
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
            # Pure check, no mutation: promotion after ``publish_armed`` must not
            # be able to fail for a reason that was already knowable here, so
            # the published session can never end up without an owner.
            self.paper_trading.ensure_candidate_can_promote(candidate_id)
            workflow_result = self.paper_workflow.publish_armed(
                plan,
                engine=runtime,
                orders=service,
                health_evaluator=self._paper_execution_health_adapter,
                candidate_symbols=frozenset(
                    row.symbol for row in self.auto_quant_candidates
                ),
            )
            self.paper_trading.promote_candidate(candidate_id)
        except Exception as error:
            # Discards the candidate only -- once promotion succeeded there is
            # no candidate left, so the live owner is never torn down here.
            self._reject_unpublished_auto_candidate(
                candidate_id,
                plan,
                "Paper 会话校验或武装失败，未提交自动订单："
                f"{error}",
                show_message=True,
            )
            return
        self.trading_runtime = runtime
        self.auto_quant_snapshot = workflow_result.engine_snapshot
        self._active_auto_launch_plan = None
        self._apply_paper_workflow_result(workflow_result)
        self._record_runtime_event(
            severity="warning",
            component="auto_quant",
            code="PAPER_SESSION_ARMED",
            message=(
                f"IBKR Paper 自动量化会话 {snapshot.session_id[:8]} "
                f"已武装；候选 {snapshot.candidate_count}；Live 永久阻断"
            ),
        )

    def _paper_execution_health_adapter(self, **kwargs: object) -> PaperExecutionHealth:
        """Normalize journal dictionaries at the desktop/broker boundary."""
        reconciliations = tuple(kwargs.pop("reconciliations", ()))
        models = tuple(
            PaperOrderReconciliation(
                intent_id=str(row["intent_id"]), session_id=str(row.get("session_id", "")),
                broker_order_id=int(row["broker_order_id"]), symbol=str(row["symbol"]),
                side=str(row["side"]), intended_quantity=Decimal(str(row["intended_quantity"])),
                latest_status=(str(row["latest_status"]) if row.get("latest_status") is not None else None),
                reported_filled=Decimal(str(row.get("executed_quantity", 0))),
                reported_remaining=Decimal(str(max(0, row.get("intended_quantity", 0) - row.get("executed_quantity", 0)))),
                executed_quantity=Decimal(str(row.get("executed_quantity", 0))),
                reconciled=bool(row["reconciled"]), terminal=bool(row["terminal"]),
                reason=str(row["reason"]), observed_at=str(row["observed_at"]),
            )
            for row in reconciliations
        )
        return evaluate_paper_execution_health(reconciliations=models, **kwargs)  # type: ignore[arg-type]

    def _apply_paper_workflow_result(self, result: PaperSessionResult) -> None:
        """Render one controller result; desktop never replays its ingress."""
        self.auto_quant_snapshot = result.engine_snapshot  # type: ignore[assignment]
        self.paper_execution_health = result.health  # type: ignore[assignment]
        self._render_auto_quant_snapshot()
        for event in result.events:
            self._record_runtime_event(severity=event.severity, component="paper_execution", code=event.code, message=event.message)
        # Reconciliation controls *and* the refused-close recovery hook.
        self._apply_paper_workflow_button_state()
        phase = self.paper_trading.phase()
        if (
            phase is PaperWorkflowPhase.STOPPING
            and not result.state.finalized
            and not getattr(self, "_paper_finalization_inflight", False)
        ):
            self._schedule_paper_finalization_refresh(result)
        self._finish_auto_quant_session_if_safe()

    def _schedule_paper_finalization_refresh(
        self, result: PaperSessionResult
    ) -> None:
        """Start the zero-state proof with backoff while exits are pending.

        H-3 fix: while the engine is still flattening positions, each stream
        tick must not launch a fresh full-broker refresh; and a busy broker
        resource group must defer the proof instead of halting the session.
        """
        if getattr(self, "_paper_finalization_inflight", False):
            return
        engine_active = bool(
            getattr(result.engine_snapshot, "active", True)
        )
        last = self._last_paper_finalization_started
        if (
            engine_active
            and last is not None
            and monotonic() - last < 5.0
        ):
            return
        self._start_paper_finalization_refresh()

    def _start_paper_finalization_refresh(self) -> None:
        """Prove broker zero-state, disconnect, then release nothing yet."""

        if not self.paper_trading.has_order_service():
            self.paper_workflow.fail_finalization_refresh()
            self._apply_paper_workflow_button_state()
            return
        self._paper_finalization_inflight = True
        self._last_paper_finalization_started = monotonic()

        def task(progress: Callable[[str], None]):
            progress("Verifying complete Paper zero-state before disconnect...")
            result, evidence_id = self.paper_workflow.capture_finalization_evidence()
            if evidence_id is None:
                return result
            self.paper_trading.disconnect()
            return self.paper_workflow.confirm_finalization_after_disconnect(
                evidence_id
            )

        started = self._start_task(
            task,
            on_success=self._paper_finalization_completed,
            on_failure=self._paper_finalization_failed,
            start_message="Paper safe finalization check in progress...",
            resource_group="broker",
            suppress_busy_message=True,
            # This task is part of the close path itself, not new work: it is
            # what proves broker zero-state so the session can be finalized.
            shutdown_essential=True,
        )
        if not started:
            # A busy broker resource group defers the proof instead of
            # halting the session; the next tick retries (H-3 fix).
            self._paper_finalization_inflight = False
            self._apply_paper_workflow_button_state()

    def _paper_finalization_completed(self, result: object) -> None:
        """Render finalization while suppressing an immediate duplicate task."""

        try:
            self._apply_paper_workflow_result(result)  # type: ignore[arg-type]
        finally:
            self._paper_finalization_inflight = False

    def _paper_finalization_failed(self, message: str) -> None:
        """Keep the PAPER lease and require manual reconciliation on ambiguity."""

        self._paper_finalization_inflight = False
        self.paper_workflow.fail_finalization_refresh()
        self._log(message)
        # ``fail_finalization_refresh`` moves STOPPING -> HALTED, the automatic
        # failure route; the button-state helper carries the recovery hook.
        self._apply_paper_workflow_button_state()

    def _pause_auto_quant_entries(self) -> None:
        try:
            self._apply_paper_workflow_result(self.paper_workflow.set_entries_paused(True))
        except WorkflowStateError as error:
            self._log(str(error))
            return
        self._log(
            "自动量化已暂停新开仓；现有持仓的止损、止盈和时段退出继续运行。"
        )

    def _resume_auto_quant_entries(self) -> None:
        try:
            self._apply_paper_workflow_result(self.paper_workflow.set_entries_paused(False))
        except WorkflowStateError as error:
            self._log(str(error))
            return
        self._log("自动量化已恢复新开仓。")

    def _stop_auto_quant(self) -> None:
        try:
            self._apply_paper_workflow_result(self.paper_workflow.request_stop(self.stream_snapshot))
        except WorkflowStateError as error:
            self._log(str(error))

    def _resume_auto_quant_from_reconciliation(self) -> None:
        """Resume only after an explicit reconciliation action and confirmation.

        Pending broker rows remain review evidence.  This path deliberately
        never creates replacement intents or submits orders.
        """
        if self.paper_trading.phase() is not PaperWorkflowPhase.RECONCILING_READY:
            self._log("A fresh reconciliation proof is required before Paper can resume.")
            return
        evidence = self.paper_workflow.reconciliation_evidence
        if evidence is None:
            self._log("Reconciliation proof is missing; run manual reconciliation again.")
            return
        reply = QMessageBox.question(
            self, "Confirm Paper resume",
            "Manual reconciliation is complete. Resume the existing Paper session without resubmitting pending orders?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        evidence_id = evidence.evidence_id

        def task(progress: Callable[[str], None]):
            progress("Revalidating the complete IBKR Paper snapshot...")
            return self.paper_workflow.confirm_manual_resume(evidence_id)

        started = self._start_task(
            task,
            on_success=lambda result: self._apply_paper_workflow_result(result),
            on_failure=self._auto_order_resume_failed,
            start_message="Paper reconciliation confirmation in progress...",
            resource_group="broker",
        )
        if not started:
            # The controller was not invoked; keep the proof available.
            self._apply_paper_workflow_button_state()

    def _poll_auto_quant_orders(self) -> None:
        if getattr(self, "_paper_finalization_inflight", False):
            return
        if (
            monotonic() - self._last_stream_ingress_monotonic < 1.2
        ):
            # Stream ticks already drove the identical watchdog sequence
            # (drain, stale-BUY cancel, SELL intervention, health).
            return
        if self.paper_trading.phase() not in {
            PaperWorkflowPhase.RUNNING,
            PaperWorkflowPhase.PAUSED,
            PaperWorkflowPhase.STOPPING,
        }:
            return
        try:
            self._apply_paper_workflow_result(self.paper_workflow.poll())
        except WorkflowStateError as error:
            self._log(str(error))

    def _reconnect_auto_order_service(self) -> None:
        if not self.paper_trading.has_order_service():
            return
        try:
            attempt_id = self.paper_workflow.begin_manual_reconciliation()
        except WorkflowStateError as error:
            self._log(str(error))
            return
        self._publish_execution_controls()
        self.execution_page.render_execution_health(
            "执行对账：正在重新连接 IBKR Paper 并读取开放订单、"
            "当日成交和当前持仓；不会自动恢复交易。"
        )

        def task(progress: Callable[[str], None]):
            progress("重新连接 IBKR Paper 并恢复订单快照…")
            if not self.paper_trading.is_connected():
                self.paper_trading.connect_active()
            return self.paper_workflow.complete_manual_reconciliation(attempt_id)

        started = self._start_task(
            task,
            on_success=self._auto_order_service_reconnected,
            on_failure=lambda message: self._auto_order_reconciliation_failed(
                attempt_id, message
            ),
            start_message="IBKR Paper 重新对账中…",
            resource_group="broker",
        )
        if not started:
            self.paper_workflow.fail_manual_reconciliation(attempt_id)
            self._apply_paper_workflow_button_state()

    def _auto_order_reconciliation_failed(
        self, attempt_id: str, _message: str
    ) -> None:
        """Keep a failed evidence refresh halted and explicitly retryable."""

        self.paper_workflow.fail_manual_reconciliation(attempt_id)
        self._apply_paper_workflow_button_state()

    def _auto_order_resume_failed(self, message: str) -> None:
        """A consumed or changed proof always returns to sticky HALTED."""

        self._log(message)
        self._apply_paper_workflow_button_state()

    def _auto_order_service_reconnected(self, result: object) -> None:
        # Reconnect is evidence collection only.  It must never resume or
        # re-submit; the separate explicit confirmation handles that.
        self._apply_paper_workflow_result(result)  # type: ignore[arg-type]

    def _apply_paper_workflow_button_state(self) -> None:
        """Render every execution control from controller truth.

        This is the one place every route into ``HALTED``/``RECONCILING*``
        passes through -- the automatic ``STOPPING`` -> ``HALTED``
        (finalization failure) as well as each explicit operator step -- so
        the refused-close recovery hook lives here rather than being repeated
        at each call site.
        """

        self._publish_execution_controls()
        self._release_close_drain_if_recovery_required()

    def _finish_auto_quant_session_if_safe(self) -> None:
        result = self.paper_workflow.result
        if result is None or not result.state.finalized:
            return
        snapshot = result.engine_snapshot
        if self.paper_trading.has_order_service():
            broker_state = self.paper_trading.broker_state()
            reconciliations = (
                self.order_repository.reconciliation_rows(
                    session_id=snapshot.session_id
                )
            )
            if broker_state.positions or any(
                not row.reconciled for row in reconciliations
            ):
                return
            self.paper_trading.disconnect()
        if not self.paper_workflow.finalize_if_safe():
            return
        # Ownership is released only now, after the workflow itself reported the
        # session finalized -- a successful disconnect alone proves nothing.
        self.paper_trading.clear_active()
        self.trading_runtime = None
        self.paper_execution_health = None
        self.execution_page.render_execution_health(
            "执行对账：会话已安全结束，券商持仓和订单均已核对。"
        )
        self.execution_page.set_arm_confirmed(False)
        self._apply_paper_workflow_button_state()

    def _render_auto_quant_snapshot(self) -> None:
        """Gather the session facts and hand them to the page as one view.

        This is the whole of the window's part in the execution route's
        rendering: fetch, project, draw.  The projection lives in the page
        package's presenter, which is Qt-free, and the drawing lives in the page,
        so neither of them can reach a service and neither of them is reached
        into by name from here.

        The candidate table is drawn first and on its own, because it has content
        before any session does: the operator approves a shortlist and only then
        arms it, so a route that waited for a snapshot would show an empty table
        at exactly the moment the shortlist is the thing being approved.
        """

        if not hasattr(self, "execution_page"):
            return
        quotes = {
            quote.symbol: quote
            for quote in (
                self.stream_snapshot.quotes
                if self.stream_snapshot is not None
                else ()
            )
        }
        candidates = self.auto_quant_candidates
        self.execution_page.render_candidates(
            build_candidates_view(
                candidates=candidates,
                quotes=quotes,
                recently_ready=self._quote_was_recently_ready,
            )
        )
        snapshot = self.auto_quant_snapshot
        if snapshot is None:
            return
        candidate_symbols = {row.symbol for row in candidates}
        broker_state = self.paper_trading.broker_state()
        broker_positions = (
            tuple(
                row
                for row in broker_state.positions
                if row.symbol in candidate_symbols
            )
            if broker_state is not None
            else ()
        )
        session_id = snapshot.session_id
        reconciliations = (
            self.order_repository.reconciliation_rows(
                session_id=session_id,
                limit=50,
            )
            if session_id
            else ()
        )
        audit_by_intent = {
            str(row["intent_id"]): row
            for row in self.order_repository.audit_rows(limit=1000)
            if row.get("session_id") == session_id
        }
        self.execution_page.render(
            build_runtime_view(
                snapshot=snapshot,
                account=(
                    self.account_portfolio.account
                    if self.account_portfolio is not None
                    else None
                ),
                broker_state=broker_state,
                broker_positions=broker_positions,
                quotes=quotes,
                pending_by_symbol={
                    intent.execution_symbol: intent
                    for intent in snapshot.pending_orders
                },
                latency=self.paper_trading.reconciliation_rows_with_latency(
                    session_id=session_id,
                    limit=100,
                ),
                reconciliations=reconciliations,
                audit_by_intent=audit_by_intent,
                candidates=candidates,
                recently_ready=self._quote_was_recently_ready,
            )
        )

    def _publish_execution_controls(self) -> None:
        """Publish the route's control state from the workflow truth.

        One writer, one source.  The phase decides the session controls, the
        launch facts decide the launch controls, and the page is handed booleans
        rather than a phase so it cannot act on a lifecycle vocabulary it does
        not own.

        ``stop_stream_enabled`` is deliberately tied to the launch lock as well
        as to the stream: stopping the feed under a live Paper session would
        starve the strategy of the quotes its exit gates read, which is why the
        legacy builder disabled it when a session was armed.
        """

        if not hasattr(self, "execution_page"):
            return
        phase = self.paper_trading.phase()
        launch_locked = self._launch_locked()
        self.execution_page.set_control_state(
            control_state(
                launch_locked=launch_locked,
                session_running=phase is PaperWorkflowPhase.RUNNING,
                session_paused=phase is PaperWorkflowPhase.PAUSED,
                reconcile_available=phase is PaperWorkflowPhase.HALTED,
                resume_ready=(
                    phase is PaperWorkflowPhase.RECONCILING_READY
                    and self.paper_trading.reconciliation_status().awaiting_confirmation
                ),
                stream_running=self._stream_is_live(),
            )
        )

    def _stream_is_live(self) -> bool:
        """Whether a usable feed is owned: running, and not stuck stopping."""

        worker = self.stream_worker
        return (
            not self._stream_stop_pending
            and worker is not None
            and worker.isRunning()
        )

    def _launch_locked(self) -> bool:
        """Whether a launch attempt currently owns the route's inputs.

        True while a local preflight or channel probe is in flight, while a
        connection attempt is pending, and while a session owns an order service
        or a runtime.  Each of those is a reason the operator must not be offered
        a second launch from the same route.

        The probe is checked as its own fact rather than through
        ``_launch_busy``: it is released by the probe's own worker, so an
        unrelated task failing cannot reopen the route while the broker is still
        being probed.
        """

        return bool(
            self._launch_busy
            or self._channel_check_inflight
            or self._active_auto_launch_plan is not None
            or self.paper_trading.has_order_service()
            or self.trading_runtime is not None
        )

    def _set_launch_busy(self, busy: bool) -> None:
        """Mark a local launch step in flight and republish the controls."""

        self._launch_busy = busy
        self._publish_execution_controls()


    def _current_target_symbol(self) -> str:
        return self.targeted_validation_page.target_symbol()

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
                self.targeted_validation_page.set_target_symbol(
                    self.shadow_snapshot.target_symbol
                )
                return
        self.targeted_validation_page.set_target_symbol(symbol)
        eligible = None
        if self.universe is not None:
            eligible = any(
                row.symbol == symbol and row.eligible_for_research
                for row in self.universe.records
            )
        if eligible is False:
            self._target_status = (
                f"{symbol} · 已设置，但尚未通过当前非中概研究资格门"
            )
        else:
            self._target_status = f"{symbol} · 订阅、回放、评估和影子做 T 共用"
        if (
            self.stream_worker is None
            or not self.stream_worker.isRunning()
        ):
            self.market_page.set_subscription_symbols((symbol,))
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
        self.targeted_validation_page.set_target_symbol(symbol)
        self.market_page.set_subscription_symbols((symbol,))
        self._market_watchlist_note = f"针对性日内 T：{symbol}"
        self._publish_market_controls()
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
            self._minute_status = (
                "分钟证据：输入代码后显示本地已录数据；只回放 fresh bid/ask。"
            )
            self._publish_targeted_view()
            return
        summary = self.minute_quote_store.summary(target)
        providers = " / ".join(summary.providers) or "无"
        origins = " / ".join(summary.evidence_origins) or "缺失"
        data_range = (
            f"{summary.first_minute} → {summary.last_minute}"
            if summary.first_minute and summary.last_minute
            else "尚无"
        )
        self._minute_status = (
            f"分钟证据 · {target}：可用 {summary.usable_rows} / "
            f"总计 {summary.total_rows} 行 · 来源 {providers} · "
            f"证据类型 {origins} · 区间 {data_range}"
        )
        self._publish_targeted_view()

    def _refresh_target_preflight(self, *_args: object) -> None:
        if not hasattr(self, "targeted_validation_page"):
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
        account: BrokerAccountSnapshot | None = (
            self.account_portfolio.account
            if self.account_portfolio is not None
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
        self._publish_targeted_view()

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
        self._publish_targeted_view()
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
        self._selected_robustness_run_id = robustness.run_id
        self._selected_review_run_id = review.run_id
        # Bring the finished robustness evidence into view: the targeted
        # workspace lives on the research route, and its own detail tabs are
        # what actually hold the result.
        self._targeted_active_workspace = 3
        self._targeted_active_evidence_tab = 6
        self._publish_targeted_view()
        self.shell.navigate_to("research")
        self.research_page.set_active_workspace(
            ResearchWorkspace.TARGETED
        )
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











    def _configured_exposure_multipliers(
        self,
    ) -> dict[str, Decimal]:
        return {
            rule.execution_symbol: rule.exposure_multiplier
            for rule in self.config.substitutions.values()
        }

    def _build_auto_quant_risk(self) -> RiskApplication:
        """The single pre-trade risk authority for the auto-rotation session.

        Built from this window's current configuration and handed to the
        runtime through the composition root as one object.  It used to be
        two: the account limits were placed in
        ``ShadowConfig.layered_risk_limits`` while the engine read
        ``layered_risk_limits`` from a separate constructor argument the
        window never passed, so the configured limits sat in a field nobody
        read and the running engine enforced none of them.  Unit tests passed
        because they injected the argument directly.

        Keeping the construction in one named method is what makes "the
        configured limits are the enforced limits" assertable without standing
        up a Paper session -- see ``test_desktop_risk_wiring``.
        """

        return build_risk_application(
            LayeredRiskLimits(account=self.config.risk_limits),
            exposure_multipliers=(
                self._configured_exposure_multipliers()
            ),
        )

    def _research_scenario_capital(self) -> Decimal:
        return Decimal(self._research_capital_value)

    def _research_capital_changed(self, value: int) -> None:
        self._research_capital_value = int(value)
        if not hasattr(self, "account_page"):
            return
        self.account_page.research_capital_card.set_value(
            f"${value:,.0f}",
            "历史研究情景；不是 Paper/Live 账户余额",
        )

    def _paper_simulation_capital(self) -> Decimal | None:
        """Fresh Paper net liquidation, or ``None`` if it is not usable.

        The 300-second freshness rule and the positive-NLV rule are the same
        ones the preflight applies, so internal simulation can never be sized
        from an account the preflight would reject.  ``observed_at`` is
        already a timezone-aware ``datetime`` from the domain, so there is no
        string parsing here.
        """

        portfolio = self.account_portfolio
        if portfolio is None:
            return None
        account = portfolio.account
        if account.environment is not Environment.PAPER:
            return None
        observed = account.observed_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age_seconds = (
            datetime.now(timezone.utc)
            - observed.astimezone(timezone.utc)
        ).total_seconds()
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
        """Read broker account truth through the account application.

        The account path requests no market data.  The v1 collector took a
        symbol list and ran a SPY/QQQ readiness check on the same socket;
        both are gone, because whether quotes are real-time is Market Data
        v2's answer to give, not the account chain's.
        """

        def task(
            progress: Callable[[str], None],
        ) -> BrokerAccountPortfolio:
            progress(
                "正在进行 IBKR 只读握手并读取账户、持仓和 P&L…"
            )
            return self.broker_account.refresh(timeout_seconds=20)

        self._start_task(
            task,
            on_success=self._account_snapshot_finished,
            start_message="IBKR 只读账户刷新开始…",
            resource_group="broker",
        )



    # -- strategy governance wiring -------------------------------------
    #
    # This is the whole of the window's strategy role: read a signal from the
    # page, call the application service, repaint.  The retired
    # ``_strategy_manager_tab`` / ``_populate_strategy_registry`` /
    # ``_clone_strategy_version`` / ``_transition_selected_strategy`` did the
    # same work while also owning the widgets and the registry, which is what
    # made the catalogue a UI concern.

    def _strategy_version_selected(self, version_id: str) -> None:
        """React to the page showing a different version.

        This is a *governance view* selection, not a runtime selection.
        Opening a version in the strategy page must never repoint the
        auto-rotation or targeted-shadow runtime at it; only the combos on
        those pages do that, and they go through ``StrategySelectionService``.
        """

        if not version_id:
            return
        version = self._strategy_version_or_none(version_id)
        if version is None:
            return
        self._set_strategy_account_notice(version)

    def _refresh_strategy_page(self) -> None:
        """Repaint the strategy page and resync the runtime selection views."""

        if not hasattr(self, "strategy_page"):
            return
        try:
            versions = self.strategies.list_versions()
        except StrategyApplicationError as error:
            self._log(f"策略目录读取失败：{error}")
            return
        self.strategy_page.render(versions)
        self._populate_strategy_selection_combos()

    def _populate_strategy_selection_combos(self) -> None:
        """Point every runtime-selection combo at the selection service."""

        if hasattr(self, "backtest_page"):
            self._publish_backtest_strategy_options()
        if hasattr(self, "targeted_validation_page"):
            purpose = StrategySelectionPurpose.TARGETED_SHADOW
            selected = self.strategy_selection.restore_or_default(purpose)
            self.targeted_validation_page.set_strategy_options(
                tuple(
                    TargetedStrategyOption(
                        version.version_id,
                        strategy_option_label(version),
                    )
                    for version in self.strategy_selection.options(purpose)
                ),
                selected.version_id if selected else None,
            )
        if hasattr(self, "execution_page"):
            purpose = StrategySelectionPurpose.AUTO_ROTATION
            # The combo is a view: it is refilled from the service's options and
            # aimed at the service's selection, so it can never keep displaying
            # a version the runtime will not use.
            selected = self.strategy_selection.restore_or_default(purpose)
            self.execution_page.set_strategy_options(
                [
                    (strategy_option_label(version), version.version_id)
                    for version in self.strategy_selection.options(purpose)
                ],
                selected.version_id if selected else None,
            )
            self._refresh_auto_quant_preflight()

    def _sync_strategy_combo(
        self,
        purpose: StrategySelectionPurpose,
        combo: QComboBox,
    ) -> None:
        """Make ``combo`` a view of the service's selection for ``purpose``.

        The combo carries no truth of its own: it is cleared, refilled from
        the policy's options, then pointed at whatever the service currently
        considers selected.  If the previously chosen version has since been
        stopped or invalidated, ``restore_or_default`` has already moved to
        the newest eligible replacement, so the widget cannot keep displaying
        a version the runtime will not use.
        """

        selected = self.strategy_selection.restore_or_default(purpose)
        options = self.strategy_selection.options(purpose)
        combo.blockSignals(True)
        combo.clear()
        for version in options:
            combo.addItem(strategy_option_label(version), version.version_id)
        index = combo.findData(selected.version_id) if selected else -1
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def _record_runtime_strategy_selection(
        self,
        purpose: StrategySelectionPurpose,
        version_id: object,
    ) -> None:
        """Adopt a combo's choice as the runtime selection.

        A refused selection is logged rather than silently kept: it means the
        widget is showing something the policy no longer permits, and the
        operator needs to know the runtime did not move.
        """

        if not version_id:
            return
        try:
            self.strategy_selection.select(purpose, str(version_id))
        except StrategySelectionError as error:
            self._log(
                f"{purpose} 运行选择未生效：{error}；运行时保持原版本"
            )

    def _strategy_clone_requested(
        self,
        version_id: str,
        semver: str,
        parameters_json: str,
    ) -> None:
        """Execute a clone the page asked for."""

        try:
            parameters = json.loads(parameters_json)
            if not isinstance(parameters, dict):
                raise ValueError("参数必须是 JSON 对象")
            created = self.strategies.clone_version(
                version_id,
                semver=semver,
                parameters=parameters,
            )
        except json.JSONDecodeError as error:
            QMessageBox.warning(
                self, "创建失败", f"参数不是合法 JSON：{error}"
            )
            return
        except (ValueError, StrategyApplicationError) as error:
            QMessageBox.warning(self, "创建失败", str(error))
            return
        self._refresh_strategy_page()
        self._log(
            f"已创建 {created.strategy_id} {created.semver}；"
            "状态回到研究，需重新验证"
        )

    def _strategy_transition_requested(
        self,
        version_id: str,
        target_status: str,
    ) -> None:
        """Execute a lifecycle change the page asked for."""

        try:
            changed = self.strategies.transition(
                version_id,
                target_status,
                reason="desktop governance action",
            )
        except StrategyApplicationError as error:
            QMessageBox.warning(
                self,
                "晋级门阻断",
                f"{error}\n\n自动下单仍保持关闭。",
            )
            self._log(f"策略状态变更被阻断：{error}")
            return
        self._refresh_strategy_page()
        self._log(f"{changed.strategy_id} 已变更为 {changed.status}")
        self._record_runtime_event(
            severity="info",
            component="strategy",
            code="STATUS_CHANGE",
            message=(
                f"{changed.strategy_id} {changed.semver} -> "
                f"{changed.status}"
            ),
        )

    def _strategy_version_or_none(
        self, version_id: str
    ) -> StrategyVersion | None:
        try:
            return self.strategies.get_version(version_id)
        except StrategyNotFoundError:
            return None

    def _set_strategy_account_notice(
        self, version: StrategyVersion
    ) -> None:
        """Bind the account page's notice strip to the shown version.

        Kept semantically identical to the retired page handler: this is
        strategy-evidence copy displayed on the account page, and the account
        page itself still knows nothing about strategy.
        """

        if not hasattr(self, "account_page"):
            return
        if (
            version.strategy_id == "intraday-targeted-t"
            and version.status is StrategyStatus.RESEARCH
        ):
            self.account_page.set_notice(
                f"探索性影子模式：已绑定 {version.strategy_id} "
                f"{version.semver}。可收集实时模拟证据；"
                "不代表晋级，不会发送券商订单。"
            )
        elif (
            version.gate_passed
            and version.status is StrategyStatus.PAPER_SHADOW
        ):
            self.account_page.set_notice(
                f"策略证据门：通过；已绑定 {version.strategy_id} "
                f"{version.semver}。仍需新鲜 Paper 账户与实时行情。"
            )
        else:
            self.account_page.set_notice(
                "策略证据门：硬阻断。"
                f"{version.strategy_id} {version.semver}："
                f"{version.gate_reason}"
            )

    def _auto_strategy_selected(self, version_id: object) -> None:
        """Adopt the execution page's choice as the runtime selection.

        The page reports which version the operator chose; the seat of truth is
        the selection service, so selecting here is what records it -- never the
        combo on screen.
        """

        self._record_runtime_strategy_selection(
            StrategySelectionPurpose.AUTO_ROTATION,
            version_id,
        )

    def _shadow_strategy_selection_changed(self, *_args: object) -> None:
        """Adopt the targeted page's choice as the runtime selection."""

        self._record_runtime_strategy_selection(
            StrategySelectionPurpose.TARGETED_SHADOW,
            self.targeted_validation_page.selected_strategy_version_id(),
        )
        self._refresh_target_preflight()

    def _selected_shadow_strategy_record(
        self,
    ) -> StrategyVersion | None:
        """The targeted-shadow runtime version, from the selection service.

        Retained under its old name because nine call sites read it; what
        changed is the source.  It is no longer ``QComboBox.currentData()``
        with a registry lookup, so the answer does not depend on which tab is
        on screen.
        """

        return self.strategy_selection.selected(
            StrategySelectionPurpose.TARGETED_SHADOW
        )

    def _selected_auto_strategy_record(
        self,
    ) -> StrategyVersion | None:
        """The auto-rotation runtime version, from the selection service."""

        return self.strategy_selection.selected(
            StrategySelectionPurpose.AUTO_ROTATION
        )

    def _account_snapshot_finished(self, result: object) -> None:
        """Store account truth and repaint the account surfaces.

        This handler touches *only* account state.  It deliberately does not
        set ``market_badge``: the v1 version derived a market-readiness
        verdict from the account snapshot's quotes, which meant an account
        refresh could relabel the market as "实时 Type 1".  Market state now
        comes exclusively from ``MarketSnapshot`` via the stream path, so an
        account read can neither claim nor deny that quotes are real-time.
        """

        if not isinstance(result, BrokerAccountPortfolio):
            raise TypeError("unexpected broker account portfolio")
        self.account_portfolio = result
        account = result.account
        self.account_ledger.append(account)
        self._record_runtime_event(
            severity="info",
            component="account",
            code="SNAPSHOT_OK",
            message=(
                f"{account.account_alias} 只读快照完成；"
                f"{len(result.positions)} 个持仓"
            ),
        )
        self._refresh_account_surfaces()
        self._refresh_auto_quant_preflight()
        # "已握手" describes the most recent account refresh, not a socket
        # that is still open: the read is a one-shot connect/read/disconnect.
        self.handshake_badge.setText("协议 · 已握手")
        self.handshake_badge.setToolTip("最近一次账户刷新握手成功")
        self.handshake_badge.setProperty("state", "ok")
        self.account_badge.setText(
            f"Paper · {account.account_alias}"
        )
        self.account_badge.setProperty("state", "ok")
        self._repolish_health_badges()
        self._log(
            f"已读取 {account.account_alias}："
            f"{len(result.positions)} 个持仓"
        )

    def _refresh_account_surfaces(self) -> None:
        """Repaint every account surface from the stored portfolio.

        The page owns the widgets; the window owns the data.  Ledger points
        and the presentation-only exposure multipliers are passed in rather
        than looked up by the page, which keeps the page free of the ledger
        store and of any application service.
        """

        if not hasattr(self, "account_page"):
            return
        portfolio = self.account_portfolio
        points: tuple = ()
        if portfolio is not None:
            points = self.account_ledger.list_points(
                environment=portfolio.account.environment.value,
                account_alias=portfolio.account.account_alias,
                limit=100,
            )
        self.account_page.render(
            portfolio,
            ledger_points=points,
            exposure_multipliers=self._configured_exposure_multipliers(),
        )
        self._populate_dashboard_account_cards(portfolio)
        if self.auto_quant_snapshot is not None:
            self._populate_auto_quant_snapshot(
                self.auto_quant_snapshot
            )
        self._refresh_target_preflight()

    def _populate_dashboard_account_cards(
        self,
        portfolio: BrokerAccountPortfolio | None,
    ) -> None:
        """Mirror account truth onto the dashboard's account cards.

        These three cards predate the account page and are the dashboard's
        summary of the same broker truth -- not a second source of it.
        """

        if portfolio is None:
            self.universe_card.set_value(
                "未读取", "到账户与持仓页执行只读刷新"
            )
            self.verified_card.set_value(
                "不可用", "不会以研究收益代替"
            )
            self.history_card.set_value(
                "未读取", "券商空仓与未读取严格区分"
            )
            return
        account = portfolio.account
        self.universe_card.set_value(
            _money(account.net_liquidation),
            f"IBKR {account.environment.value.title()} · "
            f"{account.account_alias}",
        )
        self.verified_card.set_value(
            _money(account.daily_pnl, signed=True),
            "券商 reqPnL；不含回测",
        )
        self.history_card.set_value(
            str(len(portfolio.positions)),
            "当前券商持仓",
        )

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
        """The operator's subscription input, parsed at the page's boundary."""

        return self.market_page.subscription_symbols()

    def _settings_provider_selected(self, provider: str) -> None:
        # Programmatic: the settings combo is not the operator choosing on this
        # route, and the setter is silent so the two combos cannot drive each
        # other.
        self.market_page.set_selected_provider(provider)
        api_provider = (
            "ibkr" if provider == "ibkr_extended" else provider
        )
        self.settings_page.set_api_provider(
            api_provider, emit_change=False
        )
        self._api_provider_changed(api_provider)

    def _stream_provider_selected(self, *_args: object) -> None:
        provider = str(
            self.market_page.selected_provider() or "finnhub_trades"
        )
        self.settings_page.set_market_provider(
            provider, emit_change=False
        )

    def _switch_to_settings_provider(
        self,
        draft: SettingsDraft,
    ) -> None:
        provider = draft.market_provider
        self._save_user_preferences(draft)
        if not self._stream_symbols_from_input():
            self.market_page.set_selected_provider(provider)
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
        if provider not in VALID_MARKET_SOURCES:
            QMessageBox.warning(
                self, "无法切换行情", f"不支持的数据源：{provider}"
            )
            return
        self.market_page.set_selected_provider(provider)
        self._pending_stream_switch = (provider, symbols)
        worker = self.stream_worker
        if worker is not None and worker.isRunning():
            self._log(
                f"正在停止 {worker.source_id}，随后切换到 {provider}…"
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
        self.market_page.set_selected_provider(provider)
        self.market_page.set_subscription_symbols(symbols)
        self._start_stream()

    def _maybe_rotate_extended_ibkr_session(self) -> None:
        self._refresh_extended_hours_status()
        worker = self.stream_worker
        if (
            worker is None
            or not worker.isRunning()
            or worker.source_id != SOURCE_IBKR_EXTENDED
            or self._pending_stream_switch is not None
        ):
            return
        desired_exchange = self.market_data.desired_market_exchange(
            worker.source_id
        )
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
        if not hasattr(self, "execution_page"):
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
        self.execution_page.render_context(
            session=(
                f"当前美东时段：{routing.label} · 5×24 Paper：{status} · "
                f"{routing.reason}"
            )
        )

    def _start_stream(self) -> None:
        if (
            self.stream_worker is not None
            and self.stream_worker.isRunning()
        ):
            self._request_stream_switch(
                str(
                    self.market_page.selected_provider()
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
        provider = str(self.market_page.selected_provider() or "ibkr")
        try:
            credentials = (
                self.credential_service.resolve_stream_credentials()
            )
            # The UI knows *what the operator selected* (source id,
            # watchlist, credential store values).  Everything else --
            # constructor, timeouts, venue, labels, coverage -- is the
            # application's business.
            request = MarketDataStartRequest(
                source_id=provider,
                symbols=symbols,
                credentials=MarketDataCredentials(
                    alpaca_api_key=credentials.alpaca_api_key,
                    alpaca_api_secret=credentials.alpaca_api_secret,
                    finnhub_api_key=credentials.finnhub_api_key,
                ),
            )
            worker = StreamWorker(self.market_data, request)
            market_exchange = worker.market_exchange
        except (
            MarketDataCredentialsError,
            MarketDataActiveError,
            ValueError,
        ) as error:
            # ``MarketDataActiveError`` is a ``RuntimeError``, so it has to
            # be named here: the application refuses to prepare while it
            # still holds a live feed, and an uncaught exception escaping a Qt
            # slot would abort the process instead of telling the operator.
            self._task_failed(str(error))
            return
        self.stream_worker = worker
        self._quote_last_ready_monotonic.clear()
        self._last_stream_status_key = None
        self._last_stream_status_log_at = 0.0
        worker.snapshot_ready.connect(self._stream_snapshot_pushed)
        worker.failed.connect(self._stream_failed)
        worker.finished.connect(self._stream_finished)
        self._set_connection_settings_enabled(False)
        # The cards are drawn from the page's own render, so the "connecting"
        # state is published as a view rather than by writing widgets here.
        self.market_page.render(
            build_connecting_view(
                facts=MarketConnectingFacts(
                    source_id=provider,
                    symbol_count=len(symbols),
                ),
                scope=self._market_scope,
                controls=self._market_controls(),
                watchlist_note="Level I 持续订阅",
            )
        )
        worker.start()
        self.stream_timer.start()
        # Published *after* the thread starts: the route's stop-stream control
        # reads "is a worker running", so publishing before ``start()`` would
        # leave it disabled for every direct start from the market page.
        self._publish_market_controls()
        self._publish_execution_controls()
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
            self._stream_stop_pending = True
            self._publish_market_controls()
            self.market_page.render_health(
                "停止中：网络线程尚未确认退出；禁止重复启动"
            )
            self._publish_execution_controls()
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
        self._stream_stop_pending = False
        self._publish_market_controls()
        self.market_page.render_health("已停止：最后行情保留为 stale")
        self._publish_execution_controls()
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
                connected=False,
                ready=False,
                quotes=invalid_quotes,
                message=reason,
                observed_at=datetime.now(timezone.utc),
            )
            self.stream_snapshot = snapshot
            self._publish_market_view(snapshot)
        self.market_badge.setText("行情 · 已停止")
        self.market_badge.setProperty("state", "warn")
        self.signal_card.set_value("不可用", reason)
        self._repolish_health_badges()

    def _stream_snapshot_pushed(self, result: object) -> None:
        """Event-driven ingress: a service push (worker listener) delivered a
        fresh snapshot; record its liveness so the timer poll stays idle and
        latency is bounded by the feed, not the poll interval."""
        if not isinstance(result, MarketSnapshot):
            return
        self._last_stream_push_monotonic = monotonic()
        self._stream_snapshot_received(result)

    def _stream_snapshot_received(self, result: object) -> None:
        if not isinstance(result, MarketSnapshot):
            return
        self.stream_snapshot = result
        self.workflow_controller.market_account.update(
            account_ready=self.account_portfolio is not None,
            market_ready=result.realtime_ready,
            message=result.message,
        )
        self._record_minute_snapshot(result)
        if result.error_code is not None:
            # Key by provider+error code only: reconnect generations must not
            # flood the event log with one error entry per retry attempt.
            event_key = (
                result.source_id,
                result.error_code,
            )
            if event_key != self._last_stream_event_key:
                self._last_stream_event_key = event_key
                self._record_runtime_event(
                    severity="error",
                    component="market_data",
                    code=str(result.error_code),
                    message=result.message,
                )
        else:
            # Recovered: allow the next outage of the same kind to be
            # recorded again instead of being swallowed by the old key.
            self._last_stream_event_key = None
        self._publish_market_view(result)
        self._populate_auto_quant_candidates()
        self._refresh_target_preflight()
        if (
            not getattr(self, "_paper_finalization_inflight", False)
            and self.paper_trading.phase() in {
            PaperWorkflowPhase.RUNNING,
            PaperWorkflowPhase.PAUSED,
            PaperWorkflowPhase.STOPPING,
            }
        ):
            self._last_stream_ingress_monotonic = monotonic()
            try:
                self._apply_paper_workflow_result(
                    self.paper_workflow.on_stream(result)
                )
            except WorkflowStateError as error:
                self._log(str(error))
        if self.shadow_engine is not None and self.shadow_engine.active:
            self.shadow_snapshot = self.shadow_engine.on_stream(result)
            self._publish_targeted_view()

    def _record_minute_snapshot(
        self, snapshot: MarketSnapshot
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
            key = (quote.source_id, quote.symbol, minute)
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
        if (
            monotonic() - self._last_stream_push_monotonic < 1.0
        ):
            # Event-driven pushes are flowing (Finnhub/Alpaca); the timer is
            # only a fallback for services without a push listener (IBKR).
            return
        self._stream_snapshot_received(self.market_data.snapshot())

    def _publish_market_view(self, snapshot: MarketSnapshot) -> None:
        """Gather the market facts and hand them to the page as one view.

        This is the window's whole part in the market route's rendering: fetch,
        project, draw, and then update the pieces that are *not* the page's --
        the shell's global badges, the signal card and the throttled status log.

        The readiness counts and the scope line are computed here rather than on
        the page because they depend on subsystems this route does not own: auto
        quant candidates, market reference symbols, the universe and the scan.
        The page is handed the finished numbers and the finished line.
        """

        if not hasattr(self, "market_page"):
            return
        self._update_quote_readiness(snapshot)
        readiness = calculate_quote_readiness_breakdown(
            snapshot,
            candidate_symbols=(
                row.symbol for row in self.auto_quant_candidates
            ),
            reference_symbols=self._auto_quant_market_reference_symbols(),
            recently_ready_symbols=self._quote_last_ready_monotonic,
        )
        self.market_page.render(
            build_market_view(
                snapshot=snapshot,
                readiness=MarketReadinessFacts(
                    candidate_count=readiness.candidate_count,
                    candidate_current_count=(
                        readiness.candidate_current_count
                    ),
                    candidate_recent_count=(
                        readiness.candidate_recent_count
                    ),
                    reference_count=readiness.reference_count,
                    reference_current_count=(
                        readiness.reference_current_count
                    ),
                    reference_recent_count=(
                        readiness.reference_recent_count
                    ),
                    subscription_count=readiness.subscription_count,
                    subscription_current_count=(
                        readiness.subscription_current_count
                    ),
                    subscription_recent_count=(
                        readiness.subscription_recent_count
                    ),
                ),
                scope=self._market_scope,
                rows=quote_rows(snapshot),
                controls=self._market_controls(),
                watchlist_note=self._market_watchlist_note,
            )
        )
        self._publish_market_health(snapshot, readiness)

    def _market_controls(self):
        """The control state the page may offer, from the window's own facts."""

        live = self._stream_is_live()
        return control_view(
            worker_running=live,
            stop_pending=self._stream_stop_pending,
            symbols_enabled=not live,
            provider_enabled=True,
        )

    def _publish_market_controls(self) -> None:
        """Publish just the control state and the watchlist card.

        Used by the start/stop and subscription paths, which change what the
        route may offer without having a snapshot to render.  The watchlist card
        is included because its note records *why* the subscription is what it
        is, and that is a view fact.
        """

        if not hasattr(self, "market_page"):
            return
        self.market_page.render_controls(
            self._market_controls(),
            watchlist=self._market_watchlist_note,
        )

    def _publish_market_health(self, snapshot: MarketSnapshot, readiness) -> None:
        """Update the badges, the signal card and the throttled status log.

        These belong to the shell and the window rather than to the page: the
        market and handshake badges speak for the whole workbench, and the status
        log is a runtime concern the page must not own.
        """

        if snapshot.ready:
            self.handshake_badge.setText(
                (
                    f"{snapshot.source_label} · 已认证"
                    if snapshot.source_id in PUSH_LISTENER_SOURCES
                    else "协议 · 已握手"
                )
            )
            self.handshake_badge.setProperty("state", "ok")
        if snapshot.realtime_ready:
            self.market_badge.setText(
                (
                    (
                        "行情 · IEX 实时"
                        if snapshot.source_id == SOURCE_ALPACA_IEX
                        else "行情 · Finnhub 成交"
                    )
                    if snapshot.source_id in PUSH_LISTENER_SOURCES
                    else "行情 · 实时"
                )
            )
            self.market_badge.setProperty("state", "ok")
        elif snapshot.error_code is not None:
            self.market_badge.setText(
                f"行情 · 错误 {snapshot.error_code}"
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
                        if snapshot.source_id == SOURCE_ALPACA_IEX
                        else "Finnhub 实时成交+明确模拟执行带"
                    )
                    if snapshot.source_id in PUSH_LISTENER_SOURCES
                    else "fresh 实时 + bid/ask"
                )
                if snapshot.realtime_ready
                else snapshot.message[:42]
            ),
        )
        self._repolish_health_badges()
        status_key = (
            snapshot.source_id,
            snapshot.generation,
            snapshot.ready,
            snapshot.error_code,
        )
        now_monotonic = monotonic()
        if (
            status_key != self._last_stream_status_key
            or now_monotonic - self._last_stream_status_log_at >= 30
        ):
            self._last_stream_status_key = status_key
            self._last_stream_status_log_at = now_monotonic
            if snapshot.error_code is not None:
                self._log(
                    f"{snapshot.source_label} 行情错误 "
                    f"{snapshot.error_code}：{snapshot.message}"
                )
            elif snapshot.ready:
                self._log(
                    f"{snapshot.source_label} 已连接 · 可下单候选 "
                    f"{readiness.candidate_current_count}/"
                    f"{readiness.candidate_count} · 市场参考 "
                    f"{readiness.reference_current_count}/"
                    f"{readiness.reference_count} · 订阅合计 "
                    f"{readiness.subscription_current_count}/"
                    f"{readiness.subscription_count}"
                )
            else:
                self._log(
                    f"{snapshot.source_label} 正在连接（第 "
                    f"{snapshot.reconnect_attempt} 次）…"
                )

    def _update_quote_readiness(
        self, snapshot: MarketSnapshot
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
            self.trading_runtime is not None
            and self.trading_runtime.session.active
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
            strategy.status
            not in {StrategyStatus.RESEARCH, StrategyStatus.PAPER_SHADOW}
            or (
                strategy.status is StrategyStatus.PAPER_SHADOW
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
                    f"{self.account_portfolio.account.account_alias} "
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
        try:
            self.shadow_workflow.start()
            self.shadow_engine = engine
            self.shadow_snapshot = engine.start()
        except Exception as error:
            if self.shadow_workflow.active:
                self.shadow_workflow.stop()
            self.shadow_engine = None
            QMessageBox.warning(self, "内部影子仿真未启动", str(error))
            return
        self._publish_targeted_view()
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
        if self.shadow_workflow.active:
            self.shadow_workflow.stop()
        self._publish_targeted_view()
        self._record_runtime_event(
            severity="info",
            component="shadow_paper",
            code="SHADOW_STOP",
            message="内部影子盘停止；最后持仓按最后有效 mark 影子平仓",
        )


    def _stream_failed(self, message: str) -> None:
        self.market_page.render_failure(message)
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
        self._stream_stop_pending = False
        self._publish_market_controls()
        self._publish_execution_controls()
        self._set_connection_settings_enabled(True)
        self._activate_pending_stream_switch()

    def _register_runtime_components(self) -> None:
        """Hand generic runtime lifecycle to the supervisor.

        Only resources whose teardown is unconditional and carries no
        trading semantics are registered here.  Paper order submission,
        reconciliation, the execution lease and the workflow state machine
        deliberately stay owned by ``MainWindow`` (see
        ``docs/DESKTOP_DECOMPOSITION.md``).
        """

        supervisor = self.runtime_supervisor
        # Order 5: the admission gate.  ``begin_shutdown`` calls this the
        # moment the operator asks to close, before any task is joined, so it
        # must be a drain component -- without the flag the gate would only
        # ever be raised by the *release* pass, which runs after the running
        # tasks it is supposed to exclude.
        supervisor.register(
            "closing_gate",
            stop=self._close_admission_gate,
            order=5,
            drain=True,
        )
        # Order 10-30: stop the heartbeats first so no new work is scheduled
        # while the rest of the stack is being released.  These timers are
        # started in ``_build_ui``; the probe makes them releasable anyway.
        # None of them is a drain component: stopping the timer *is* the
        # release, so ``begin_shutdown`` must not touch it.
        supervisor.register(
            "paper_order_heartbeat",
            stop=self.paper_order_timer.stop,
            is_running=self.paper_order_timer.isActive,
            order=10,
        )
        supervisor.register(
            "extended_session_heartbeat",
            stop=self.extended_session_timer.stop,
            is_running=self.extended_session_timer.isActive,
            order=20,
        )
        supervisor.register(
            "stream_snapshot_timer",
            stop=self.stream_timer.stop,
            is_running=self.stream_timer.isActive,
            order=30,
        )
        # Order 100+: release the market data stream.  ``_stop_stream`` owns
        # the trading-safety guards (it refuses while a Paper session holds
        # positions) and returns False rather than raising, so the return
        # value is mapped to a join verdict for the supervisor.
        supervisor.register(
            "market_data_stream",
            stop=self._stop_stream,
            join=lambda: not (
                self.stream_worker is not None
                and self.stream_worker.isRunning()
            ),
            is_running=lambda: (
                self.stream_worker is not None
                and self.stream_worker.isRunning()
            ),
            order=100,
        )
        # Order 200+: background research/data workers.  ``wait`` is the Qt
        # join; it returns False when the thread did not exit in time.
        supervisor.register(
            "background_workers",
            stop=self._request_worker_stops,
            join=self._join_background_workers,
            is_running=lambda: bool(self._running_workers()),
            order=200,
            # A worker's ``stop`` is a cancel request, not a release: ask the
            # cancellable ones to wind down as soon as closing starts.
            drain=True,
        )

    def _close_admission_gate(self) -> None:
        """First teardown step: refuse any new background work."""

        self._closing = True

    def _paper_needs_manual_recovery(self) -> bool:
        """Whether leaving this Paper phase is *only* possible via the operator.

        ``RUNNING``/``PAUSED`` are excluded on purpose: closing those still
        has an automatic route (``request_stop`` -> ``STOPPING`` -> the
        zero-state proof), so the gate stays down while that runs.  The three
        phases below have no automatic exit -- each is left by an explicit
        human reconciliation step -- and every one of those steps is a task.
        """

        return self.paper_trading.phase() in {
            PaperWorkflowPhase.HALTED,
            PaperWorkflowPhase.RECONCILING,
            PaperWorkflowPhase.RECONCILING_READY,
        }

    def _cancel_close_drain(self) -> None:
        """Undo phase one: this close was refused, so the client stays usable.

        A refused close hands control back to the operator -- reconcile, then
        confirm -- and that recovery runs through ``_start_task`` like any
        other work.  Leaving ``_closing`` up would refuse the very task that
        can finalize the session, so the client would be stuck: halted,
        unable to reconcile, unable to finalize, unable to exit.

        This only lifts the admission gate; the supervisor starts nothing,
        restarts nothing, creates no thread and performs no I/O.  If the
        drain can no longer be undone (something was already released) the
        gate stays *down* and the failure is logged: a half-released runtime
        must never be presented as open.
        """

        if not self._closing:
            return
        try:
            self.runtime_supervisor.cancel_shutdown()
        except RuntimeError as error:
            self._log(f"关闭流程无法撤销，保持关闭状态：{error}")
            return
        self._closing = False

    def _release_close_drain_if_recovery_required(self) -> None:
        """Undo a refused close once Paper can only be left by the operator.

        Called from every route that can leave the session in
        ``HALTED``/``RECONCILING``/``RECONCILING_READY`` -- including the
        automatic one (``RUNNING`` -> ``STOPPING`` -> finalization failure ->
        ``HALTED``), which is *not* covered by checking the phase at close
        time.  Without this the operator would be told to reconcile while the
        gate that admits the reconciliation task is still down.
        """

        if not self._closing:
            return
        if not self._paper_needs_manual_recovery():
            return
        self._cancel_close_drain()

    def _running_workers(self) -> list[TaskThread]:
        return [worker for worker in self.workers if worker.isRunning()]

    def _request_worker_stops(self) -> None:
        """Ask every cancellable worker to stop.

        ``TaskThread`` has no generic cancel hook -- each task owns its own
        ``Event`` -- so the only universal signal is the universe refresh
        cancel event, which is the one long-running network task the desktop
        can interrupt.  The thread is never terminated: a half-written
        reference file is worse than a slow close.
        """

        event = self.universe_refresh_cancel_event
        if event is not None:
            event.set()

    def _join_background_workers(self) -> bool:
        """Wait for running workers; report whether all of them exited."""

        all_exited = True
        for worker in self._running_workers():
            if not worker.wait(3_000):
                all_exited = False
                self._log(
                    "后台任务线程未在 3 秒内退出；已记录并继续释放其他资源。"
                )
        return all_exited

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # Phase one, before any other check: close the admission gate and ask
        # the cancellable tasks to stop.  This ordering is the point -- while
        # a task is still running the window must already refuse new work, and
        # a second close must not re-admit anything.  ``begin_shutdown`` never
        # joins and never releases, so no in-flight write is disturbed.
        self.runtime_supervisor.begin_shutdown()
        running_tasks = self._running_workers()
        if running_tasks:
            event.ignore()
            QMessageBox.information(
                self,
                "后台任务仍在运行",
                (
                    f"仍有 {len(running_tasks)} 个数据/研究任务运行中。"
                    "已请求可取消的任务停止；为防止半写入产物，"
                    "请等任务自行结束后再关闭程序。"
                ),
            )
            return
        if not self.paper_trading.is_finalized():
            if self.paper_trading.phase() in {
                PaperWorkflowPhase.RUNNING,
                PaperWorkflowPhase.PAUSED,
            }:
                # Automatic safe stop: request_stop -> STOPPING -> the
                # zero-state proof.  The gate stays down while that runs.
                self._stop_auto_quant()
            # But if no automatic route is left -- HALTED, RECONCILING or
            # RECONCILING_READY can only be left by the operator, and every
            # one of those steps is a task -- this close has been refused in
            # practice.  Hand the client back, or the recovery task itself
            # would be refused by the gate and the session could never be
            # finalized.
            self._release_close_drain_if_recovery_required()
            event.ignore()
            QMessageBox.information(
                self,
                "Paper 会话尚未完成",
                "必须先完成安全停止和券商对账。停机状态需要人工对账与明确确认；"
                "客户端不会在未 finalized 时断开订单会话或退出。",
            )
            return
        if self.paper_trading.has_order_service():
            self.paper_trading.disconnect()
            self.paper_trading.clear_active()
        if self.shadow_engine is not None and self.shadow_engine.active:
            self.shadow_engine.stop()
            if self.shadow_workflow.active:
                self.shadow_workflow.stop()
        # Generic runtime teardown: stop new work, release the heartbeats and
        # the market data stream, join the workers, and keep going even if
        # one of them fails.  Trading-safety ordering above is unchanged --
        # the supervisor only runs after the Paper session is finalized.
        snapshot = self.runtime_supervisor.shutdown()
        self._report_runtime_shutdown(snapshot)
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

    def _report_runtime_shutdown(self, snapshot: RuntimeSnapshot) -> None:
        """Surface supervisor teardown failures instead of swallowing them.

        Reports the *snapshot that was returned by the shutdown call* rather
        than re-reading the supervisor, so a component whose error arrived
        from an earlier phase (``drain``) is still visible even when the
        release pass itself was clean.
        """

        messages = list(self.runtime_supervisor.errors())
        for component in snapshot.components:
            if component.exit_ok is False and component.last_error is not None:
                if not any(
                    component.last_error in message for message in messages
                ):
                    messages.append(f"{component.name}: {component.last_error}")
        if messages:
            for message in messages:
                self._log(f"运行期资源释放异常：{message}")
            self._record_runtime_event(
                severity="warning",
                component="runtime",
                code="RUNTIME_SHUTDOWN_PARTIAL",
                message="；".join(messages),
            )
        else:
            self._log("运行期资源已全部释放。")


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
        if not hasattr(self, "market_page"):
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
        self._market_scope = (
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
        self.market_page.render_scope(self._market_scope)
        if hasattr(self, "execution_page"):
            self.execution_page.render_context(
                scope=(
                    f"全市场入口：官方美股/ETF {total_count:,} · "
                    f"非中概研究池 {research_count:,} · "
                    f"本地已有日 K {history_count:,} · "
                    f"最近完成评分 {scanned_count:,} · "
                    f"当前实时候选 {len(self.auto_quant_candidates)}。"
                )
            )

    def _refresh_cards(self) -> None:
        if self.account_portfolio is None:
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
        on_failure: Callable[[str], None] | None = None,
        start_message: str,
        resource_group: str = "research",
        suppress_busy_message: bool = False,
        shutdown_essential: bool = False,
    ) -> bool:
        if self._closing and not shutdown_essential:
            # Closing raises the admission gate before releasing anything,
            # so no new work is admitted while teardown is in flight.  The
            # teardown's own mandatory proof is the one exception: the Paper
            # zero-state finalization is what lets the session reach
            # ``finalized``, and refusing it would leave the window
            # permanently unclosable.
            self._log("程序正在关闭；拒绝启动新的后台任务。")
            return False
        if not self.task_controller.can_start(resource_group):
            message = (
                f"已有{resource_group}任务在运行。为避免同类文件和数据库"
                "并发写入，请等待当前任务完成。"
            )
            self._log(message)
            if not suppress_busy_message:
                QMessageBox.information(self, "任务忙", message)
            return False
        worker = TaskThread(task, resource_group=resource_group)
        self.task_controller.register(worker)
        if hasattr(self, "runtime_events_page"):
            self._schedule_runtime_events_refresh()
        worker.progress.connect(self._log)
        if on_failure is None:
            worker.failed.connect(self._task_failed)
        else:
            def handle_failure(message: str) -> None:
                on_failure(message)
                self._task_failed(message)

            worker.failed.connect(handle_failure)
        worker.cancelled.connect(self._task_cancelled)
        worker.succeeded.connect(on_success)
        worker.finished.connect(
            lambda: self._worker_finished(worker)
        )
        self._log(start_message)
        worker.start()
        return True

    def _worker_finished(self, worker: TaskThread) -> None:
        self.task_controller.finish(worker)
        if hasattr(self, "runtime_events_page"):
            self._schedule_runtime_events_refresh()
        if worker is self.universe_refresh_worker:
            self._reset_universe_refresh_controls()
        self._publish_execution_controls()

    def _task_cancelled(self) -> None:
        self._log("任务已取消；已保留上一次完整可用的研究结果。")

    def _task_failed(self, message: str) -> None:
        # A failed task releases any launch step it was holding; the publish
        # then restores exactly the controls that step had locked.
        self._launch_busy = False
        self.execution_page.set_arm_confirmed(False)
        self._publish_execution_controls()
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
        if hasattr(self, "runtime_events_page"):
            self._schedule_runtime_events_refresh()

    def _schedule_runtime_events_refresh(self) -> None:
        """M-7 optimization: coalesce full-table rebuilds to 1 per second."""
        now = monotonic()
        if now - self._last_runtime_events_refresh >= 1.0:
            self._last_runtime_events_refresh = now
            self._refresh_runtime_events()
        elif not self._runtime_events_refresh_pending:
            self._runtime_events_refresh_pending = True
            QTimer.singleShot(1_000, self._flush_runtime_events_refresh)

    def _flush_runtime_events_refresh(self) -> None:
        self._runtime_events_refresh_pending = False
        self._refresh_runtime_events()

    def _refresh_runtime_events(self) -> None:
        events = self.runtime_events.list_recent(500)
        view = build_runtime_events_view(
            events=events,
            active_task_count=len(self.workers),
            last_export=self._last_runtime_export,
            info_text=self._runtime_info_text(),
        )
        self.runtime_events_page.render(view)

    def _resolve_runtime_event(
        self,
        event_id: int | None,
    ) -> None:
        """Resolve the event the page reported, by its full integer id.

        The page reports ``None`` when nothing is selected; the decision to
        prompt belongs here, where the message box and the store already are.
        """

        if event_id is None:
            QMessageBox.information(
                self, "未选择事件", "请先选择一条运行事件。"
            )
            return
        self.runtime_events.resolve(event_id)
        self._refresh_runtime_events()

    def _export_terminal_state(self) -> None:
        try:
            target = export_terminal_bundle(
                self.paths.exports_root,
                portfolio=self.account_portfolio,
                stream=self.stream_snapshot,
                strategies=self.strategies.list_versions(),
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
                    self.order_repository.audit_rows()
                ),
                paper_execution_audit=(
                    self.order_repository.execution_rows()
                ),
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "导出失败", str(error))
            return
        self._last_runtime_export = (
            target.name,
            str(target),
        )
        self._refresh_runtime_events()
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

    def _preview_theme_changed(self, theme_name: str) -> None:
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
            self.settings_page.set_paper_order_capability(
                False, emit_change=False
            )

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
            self.settings_page.set_extended_hours_paper(
                False, emit_change=False
            )

    def _save_user_preferences(self, draft: SettingsDraft) -> None:
        """UI adapter around :meth:`DesktopSettingsService.commit`.

        Turns the page's immutable draft into a ``UserPreferences`` and reports
        failures; the transaction itself -- validate, preflight, persist, apply
        -- belongs to the service.  The widget work below runs only after the
        commit succeeded, and in the order it always has.
        """

        try:
            preferences = UserPreferences(
                theme=draft.theme,
                market_provider=draft.market_provider,
                ibkr_host=draft.ibkr_host,
                ibkr_port=draft.ibkr_port,
                ibkr_client_id=draft.ibkr_client_id,
                connection_timeout_seconds=(
                    draft.connection_timeout_seconds
                ),
                paper_order_capability_enabled=(
                    draft.paper_order_capability_enabled
                ),
                extended_hours_paper_enabled=(
                    draft.extended_hours_paper_enabled
                ),
            )
            commit = self.settings_service.commit(
                preferences,
                current_config=self.config,
                broker_config=getattr(
                    self, "broker_account", None
                ),
                # Both runtimes that must be quiescent before the IBKR
                # endpoint changes.  The account application owns the config
                # and is asked directly; the market data application only
                # reports whether a stream is live.
                runtime_guards=(
                    (getattr(self, "market_data", None),)
                    if getattr(self, "market_data", None) is not None
                    else ()
                ),
            )
        except UserSettingsError as error:
            QMessageBox.warning(self, "设置未保存", str(error))
            return
        except MarketDataActiveError as error:
            QMessageBox.warning(self, "设置未保存", str(error))
            return
        except BrokerAccountError as error:
            QMessageBox.warning(self, "设置未保存", str(error))
            return
        saved = commit.preferences
        self.config = commit.config
        self.preferences = saved
        self._apply_theme(saved.theme)
        # Silent: restoring a saved preference is not an operator intent on the
        # market route, so this must not re-publish a provider selection.
        self.market_page.set_selected_provider(saved.market_provider)
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

    def _save_api_credentials(self, draft: CredentialDraft) -> None:
        provider = draft.provider
        if provider == "finnhub_trades":
            supplied = {"finnhub_api_key": draft.api_key.strip()}
        elif provider == "alpaca_iex":
            supplied = {
                "alpaca_api_key": draft.api_key.strip(),
                "alpaca_api_secret": draft.api_secret.strip(),
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
        if provider == "finnhub_trades":
            api_key = supplied.get("finnhub_api_key", "")
            api_secret = ""
        else:
            api_key = supplied.get("alpaca_api_key", "")
            api_secret = supplied.get("alpaca_api_secret", "")
        try:
            self.credential_service.save_provider(
                provider, api_key=api_key, api_secret=api_secret
            )
        except (CredentialStoreError, OSError, ValueError) as error:
            QMessageBox.warning(self, "凭据保存失败", str(error))
            return
        self.settings_page.clear_credential_inputs()
        self._publish_settings_view()
        self._log("API 凭据已使用 Windows 当前用户 DPAPI 加密保存")

    def _clear_selected_api_credentials(self, provider: str) -> None:
        if self._active_stream_provider() == provider:
            QMessageBox.warning(
                self,
                "行情运行中",
                "当前数据源正在使用这组凭据；请先切换或停止行情，"
                "再清除凭据。",
            )
            return
        if provider == "finnhub_trades":
            label = "Finnhub"
        elif provider == "alpaca_iex":
            label = "Alpaca"
        else:
            QMessageBox.information(
                self,
                "无需清除",
                "IBKR Gateway 没有保存在此处的 API Key。",
            )
            return
        try:
            self.credential_service.clear_provider(provider)
        except (CredentialStoreError, OSError, ValueError) as error:
            QMessageBox.warning(self, "凭据清除失败", str(error))
            return
        self.settings_page.clear_credential_inputs()
        self._publish_settings_view()
        self._log(
            f"已清除当前 Windows 用户保存的 {label} 行情凭据"
        )

    def _clear_saved_finnhub_key(self) -> None:
        try:
            self.credential_service.clear_provider("finnhub_trades")
        except (CredentialStoreError, OSError, ValueError) as error:
            QMessageBox.warning(self, "Finnhub Key 清除失败", str(error))
            return
        self._publish_settings_view()
        self._log("已清除当前 Windows 用户保存的 Finnhub Key")

    def _api_provider_changed(self, provider: str) -> None:
        self._settings_api_provider = provider
        self._publish_settings_view()

    def _set_connection_settings_enabled(self, enabled: bool) -> None:
        self._connection_settings_enabled = enabled
        self._publish_settings_view()

    def _apply_theme(self, theme_name: str) -> None:
        self.current_theme_name = (
            "light" if theme_name == "light" else "dark"
        )
        self.theme: ThemePalette = theme_palette(
            self.current_theme_name
        )
        self.setProperty("uiTheme", self.current_theme_name)
        self.setStyleSheet(build_stylesheet(self.theme))
        if hasattr(self, "market_page"):
            self.market_page.set_palette(self.theme)
        if hasattr(self, "runtime_events_page"):
            self.runtime_events_page.set_palette(self.theme)
        # The strategy page colours gate-blocked rows from the palette, so it
        # has to be told when the palette changes.  It re-renders its own rows.
        if hasattr(self, "strategy_page"):
            self.strategy_page.set_palette(self.theme)
        # The risk page colours the margin-borrowing card from the palette,
        # so it has to be told when the palette changes.
        if hasattr(self, "risk_page"):
            self.risk_page.set_palette(self.theme)
        if hasattr(self, "targeted_validation_page"):
            self.targeted_validation_page.set_palette(self.theme)
        if hasattr(self, "universe_page"):
            self.universe_page.set_palette(self.theme)
        if hasattr(self, "history_page"):
            self.history_page.set_palette(self.theme)
        if hasattr(self, "scanner_page"):
            self.scanner_page.set_palette(self.theme)
        if hasattr(self, "backtest_page"):
            self.backtest_page.set_palette(self.theme)
        if hasattr(self, "cross_section_page"):
            self.cross_section_page.set_palette(self.theme)
        # The execution page colours toned status cells from the palette, and
        # its tables keep the rows they were handed, so it has to be told *and*
        # redrawn: there is no repaint path that would re-read the palette on
        # its own.  The redraw is the same entry point a quote tick uses, so a
        # theme switch and a tick cannot diverge.
        if hasattr(self, "execution_page"):
            self.execution_page.set_palette(self.theme)
            self._render_auto_quant_snapshot()
        for chart_name in ("dashboard_chart",):
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
    self_test = os.environ.get("US_QUANT_SELF_TEST") == "1"
    splash: QSplashScreen | None = None
    if not self_test:
        splash_image = QPixmap(460, 150)
        splash_image.fill(QColor("#0b1219"))
        splash = QSplashScreen(splash_image)
        splash.showMessage(
            "正在启动美股量化研究台…",
            Qt.AlignCenter,
            QColor("#d9e6f2"),
        )
        splash.show()
        app.processEvents()
    window = MainWindow()
    if self_test:
        app.processEvents()
        window.close()
        return 0
    window.showMaximized()
    assert splash is not None
    splash.finish(window)
    # Let Windows show the usable shell first.  Local research artifacts are
    # then restored on the first event-loop turn, rather than delaying the
    # initial window presentation behind disk/database reads.
    QTimer.singleShot(0, window._load_local_state)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
