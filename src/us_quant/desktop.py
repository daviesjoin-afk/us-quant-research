from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Callable, Sequence

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
    QGridLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplashScreen,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)


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
from us_quant.desktop_universe_service import DesktopUniverseService
from us_quant.desktop_market_scan_service import DesktopMarketScanService
from us_quant.desktop_backtest_service import DesktopBacktestService
from us_quant.desktop_cross_section_service import (
    DesktopCrossSectionService,
)
from us_quant.desktop_targeted_evidence_service import (
    DesktopTargetedEvidenceService,
)
from us_quant.ibkr import IBKRConnectionConfig, probe_ibkr_socket
from us_quant.trading.composition.accounts import (
    build_broker_account_application,
)
from us_quant.trading.composition.market_data import (
    build_market_data_application,
)
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
)
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketSnapshot,
)
from us_quant.trading.ports.broker_account import (
    BrokerAccountError,
)
from us_quant.trading.ports.market_data import (
    MarketDataActiveError,
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
from us_quant.desktop_v2.pages.dashboard.models import (
    DashboardChartView,
)
from us_quant.desktop_v2.pages.dashboard.page import DashboardPage
from us_quant.desktop_v2.pages.dashboard.presenter import (
    build_dashboard_view,
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
from us_quant.runtime_events import RuntimeEvent, RuntimeEventStore
from us_quant.export_service import export_terminal_bundle
from us_quant.shadow.store import ShadowPaperStore
from us_quant.trading.composition.session_config import (
    build_auto_rotation_config,
    resolve_paper_session_capital,
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
from us_quant.trading.runtime.models import AutoQuantCandidate
from us_quant.trading.runtime.preflight import (
    AutoQuantPreflight,
    calculate_quote_readiness_breakdown,
    evaluate_auto_quant_preflight,
)
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
from us_quant.desktop_v2.orchestration.account import (
    AccountOrchestrator,
    AccountPresentationInputs,
)
from us_quant.desktop_v2.orchestration.paper import PaperOrchestrator
from us_quant.desktop_v2.orchestration.paper.models import (
    DUPLICATE_CONFIRM_MESSAGE,
    DUPLICATE_TITLE,
    PaperAccountReading,
    PaperLaunchRequest,
    PaperOrderChannel,
    PaperSessionBuildResult,
    PaperShutdownDisposition,
)
from us_quant.desktop_v2.orchestration.paper.queries import launch_attempt_in_flight
from us_quant.desktop_v2.orchestration.market import (
    MarketOrchestrator,
    MarketReadinessInputs,
)
from us_quant.desktop_v2.orchestration.research.history import (
    HistoryOrchestrator,
)
from us_quant.desktop_v2.orchestration.research.universe import (
    UniverseOrchestrator,
)
from us_quant.desktop_v2.orchestration.research.scanner import (
    ScannerOrchestrator,
    ScannerRunInputs,
)
from us_quant.desktop_v2.orchestration.research.backtest import (
    BacktestOrchestrator,
    REFUSAL_INFORMATION,
    REFUSAL_WARNING,
)
from us_quant.desktop_v2.pages.execution import ExecutionPage
from us_quant.desktop_v2.pages.execution.presenter import (
    build_candidates_view,
    control_state,
)
from us_quant.desktop_v2.pages.execution.projector import (
    AUDIT_ROW_LIMIT,
    LATENCY_ROW_LIMIT,
    RECONCILIATION_ROW_LIMIT,
    build_session_view,
)
from us_quant.desktop_v2.pages.market import MarketPage
from us_quant.desktop_v2.pages.research.targeted import TargetedValidationPage
from us_quant.desktop_v2.pages.research.targeted.models import (
    TargetedStrategyOption,
)
from us_quant.desktop_v2.pages.research.universe import UniversePage
from us_quant.desktop_v2.pages.research.history import HistoryPage
from us_quant.desktop_v2.pages.research.scanner import ScannerPage
from us_quant.desktop_v2.pages.research.backtest import BacktestPage
from us_quant.desktop_v2.pages.research.cross_section import (
    CrossSectionResearchPage,
)
from us_quant.desktop_v2.orchestration.research.cross_section import (
    CrossSectionOrchestrator,
)
from us_quant.desktop_v2.orchestration.research.scenario_capital import (
    ResearchScenarioCapitalState,
)
from us_quant.desktop_v2.orchestration.research.targeted.evidence import (
    TargetedEvidenceOrchestrator,
)
from us_quant.desktop_v2.orchestration.research.targeted.session import (
    REFUSAL_INFORMATION,
    TargetedSessionOrchestrator,
)
from us_quant.desktop_v2.orchestration.shadow import ShadowOrchestrator
from us_quant.desktop_v2.orchestration.shadow.models import ShadowCapitalFact
from us_quant.desktop_v2.orchestration.system.runtime_events import (
    RuntimeEventsEnvironment,
    RuntimeEventsOrchestrator,
)
from us_quant.desktop_v2.orchestration.system.settings import (
    SettingsOrchestrator,
)
from us_quant.desktop_v2.orchestration.system.settings.queries import (
    settings_draft_from_preferences,
    settings_storage_view,
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
from us_quant.desktop_v2.pages.system.settings import SettingsPage
from us_quant.desktop_v2.pages.system.settings.models import (
    api_provider_for_market_provider,
)
from us_quant.desktop_tasks import DesktopTaskController
from us_quant.desktop_workers import TaskThread
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
from us_quant.desktop_targeted_session_service import (
    DesktopTargetedSessionService,
)
from us_quant.intraday_universe import (
    select_intraday_watchlist,
    select_paper_rotation_rows,
)
from us_quant.cross_sectional import (
    run_cross_sectional_research,
    save_cross_sectional_research,
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


APP_TITLE = "美股量化研究台"

#: The dialog title for each refused-close disposition.  Presentation, so it lives here
#: rather than in the capability: the capability says *why* the close is refused (its
#: ``PaperShutdownResult.message`` is plain Qt-free text) and the window decides how to
#: put the question to the operator.
_PAPER_SHUTDOWN_TITLES = {
    PaperShutdownDisposition.WAITING_FOR_FINALIZATION: "Paper 会话尚未完成",
    PaperShutdownDisposition.MANUAL_RECOVERY_REQUIRED: "Paper 会话尚未完成",
    PaperShutdownDisposition.OWNERSHIP_BLOCKED: "Paper 订单通道未释放",
}


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
        # The cross-sectional research procedure and its report artifact are
        # owned by ``cross_section_service``.  Note what is *not* stored: there
        # is no ``self.cross_section_path``, because the service owns the
        # artifact boundary and the capability owns the report truth.  The
        # config arrives as a provider rather than a value: the run reads the
        # config that is current when the task executes, not the one that was
        # live when the window was built.
        self.cross_section_service = DesktopCrossSectionService(
            config_provider=lambda: self.config,
            report_path=(
                self.paths.research_results_root
                / "cross_sectional_executable_research.json"
            ),
            data_root=self.data_root,
            fallback_data_root=self.bundled_data_root,
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
        # The runtime-event store is deliberately *not* built here any more.  It
        # is constructed inside ``runtime_events_orchestrator``, with the System
        # pages, so the window holds no handle to it at all: there is no
        # ``self.runtime_events`` alias for a later handler to reach for, and
        # "who writes a runtime event?" is the capability rather than a grep.
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
        # ``_paper_finalization_inflight`` and ``_last_paper_finalization_started``
        # used to live here, as the temporary v2O-E2 seam: active ingress had to know
        # whether the zero-state proof was running, and that proof was still the
        # window's.  v2O-E3 moved the proof into ``paper_orchestrator``, so the flags are
        # the orchestrator's own task bookkeeping and there is deliberately no
        # forwarding property left behind.
        #
        # v2O-F1: ``_last_runtime_events_refresh``,
        # ``_runtime_events_refresh_pending`` and ``_last_runtime_export`` used to
        # be assigned here.  They are ``runtime_events_orchestrator``'s own
        # sequencing state now, and there is deliberately no forwarding property:
        # a property would keep every unmigrated caller working, so "who owns the
        # Runtime Events sequence?" would stop being one grep.
        #
        # v2O-F2: the same for ``_settings_api_provider`` and
        # ``_connection_settings_enabled``.  They are ``settings_orchestrator``'s
        # presentation state now -- the settings half of this window is
        # composition, and there is no alias or property left to read them
        # through.  ``self.preferences`` and ``self.config`` deliberately stay:
        # they are the *application's* current configuration, shared with Market,
        # Paper, Risk and Research, and moving them into Settings would force
        # every one of those to depend on Settings for the current config.
        #
        # Two local in-flight facts the execution route's control state reads.
        # They live here because only the window knows a local step is running;
        # the page is told the resulting booleans, never these flags.
        self._launch_busy = False
        # The channel probe is a launch step too, but it is owned by its own
        # worker rather than by the shared launch flag: the broker resource
        # group serializes it, so an unrelated task finishing must not be able
        # to release the route on the probe's behalf.
        self._channel_check_inflight = False
        # The Targeted workspace's *session* half -- the target draft, its status,
        # the local minute evidence and the preflight -- belongs to
        # ``targeted_session_orchestrator``, built below.  Note what is *not*
        # stored: no ``_target_status``, no ``_minute_status``, no
        # ``target_preflight_result``, and no forwarding property either -- a
        # property would keep every unmigrated caller working, so "who owns the
        # target status?" would stop being one grep.  The *evidence* half moved one
        # round earlier.
        self._dashboard_chart_view = DashboardChartView(None, ())
        # Shadow runtime state used to live here: the engine, its snapshot as
        # mutable truth, the store and the workflow.  It belongs to
        # ``shadow_orchestrator``, built below.  Note what is *not* stored: no
        # ``shadow_engine``, no ``shadow_snapshot``, no ``shadow_workflow``, and
        # no forwarding property either -- a property would keep every unmigrated
        # caller working, so "who owns the Shadow runtime?" would stop being one
        # grep.  The store is still owned here because the terminal export reads
        # it through the orchestrator, and the lease is still the shared one the
        # workflow controller composes.
        # The active Paper session's run -- the market ingress, the watchdog poll,
        # pause/resume, an orderly stop -- belongs to ``paper_orchestrator``, built
        # below.  Note what is *not* stored here any more: no ``trading_runtime``
        # handle, no ``paper_execution_health``, and no compatibility property for
        # either.  The runtime is held by the canonical chain
        # (``paper_workflow`` -> coordinator -> engine) and by nobody else; a handle
        # kept here was a second owner of a live session, which is why the Shadow
        # and candidate gates now ask the orchestrator instead.
        #
        # v2O-E4 took the last presentation cache out of the window too.  The execution
        # route needs to keep drawing the session that just ended -- ``finalize_if_safe``
        # clears the workflow's canonical result as part of releasing PAPER -- so the
        # fact that survives is
        # ``paper_orchestrator.presentation``, an immutable projection the *capability*
        # owns and publishes from the one result path.  There is deliberately no
        # ``_paper_render_snapshot`` here, no alias and no forwarding property: a second
        # cache on the window is how the window becomes a second truth owner about a
        # Paper session.  The route reads it for drawing and for nothing else -- no
        # launch gate, no market or Shadow interlock, no broker read and no lifecycle
        # decision may consult it.
        # Paper and internal Shadow simulation share one explicit execution
        # lease.  The desktop renders controller results but never owns the
        # normal Paper event ordering itself.  The Shadow *handle* stays here
        # next to Paper's for the same reason it always did: one
        # ``ExecutionLeaseManager`` is composed by ``WorkflowController`` and
        # given to both, so "Shadow and Paper cannot both hold execution" is
        # structural.  Passing the handle into ``shadow_orchestrator`` keeps the
        # lease shared; the orchestrator never learns that Paper exists.
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
        # The Paper *launch* sequence -- the preflight, the frozen attempt, the
        # asynchronous candidate connect, the stale-callback decision and the
        # arm/publish/promote order -- belongs to ``paper_orchestrator``, built
        # below.  Note what is *not* stored here any more: no
        # ``_active_auto_launch_plan`` and no ``_next_auto_launch_attempt``, and no
        # compatibility property either.  The canonical owner of the active attempt
        # is ``PaperWorkflowController``: "an attempt is in flight" is its
        # ``CONNECTING`` phase, and the candidate sequence number is the
        # orchestrator's own bookkeeping.
        self.paper_orchestrator = PaperOrchestrator(
            workflow_getter=lambda: self.paper_workflow,
            paper_trading_getter=lambda: self.paper_trading,
            build_session=self._build_paper_session,
            submit_task=self._start_task,
            health_evaluator=self._paper_execution_health_adapter,
            preflight_provider=self._auto_quant_preflight,
            strategy_provider=self._selected_auto_strategy_record,
            candidates_provider=lambda: self.auto_quant_candidates,
            capital_limit_provider=lambda: self.execution_page.capital_limit(),
            order_channel_provider=self._auto_quant_order_channel,
            shadow_is_active=lambda: self.shadow_orchestrator.is_active,
            # The market fact an orderly stop is judged against, as a provider: the
            # Paper capability must not import Market, and the snapshot has to be the
            # one that exists when the operator clicks rather than one frozen here.
            market_snapshot_provider=lambda: self.market_orchestrator.snapshot,
            # The journal rows the release sequencing proves "no unreconciled order"
            # against, as one narrow callable: the orchestrator must not import an
            # adapter, and ``session_id -> rows`` is the whole of what the proof reads.
            reconciliation_rows_provider=lambda session_id: (
                self.order_repository.reconciliation_rows(session_id=session_id)
            ),
            clear_arm_confirmation=lambda: self.execution_page.set_arm_confirmed(
                False
            ),
            render_launch_state=self._apply_paper_workflow_button_state,
            render_launch_context=lambda summary: (
                self.execution_page.render_context(summary=summary)
            ),
        )
        self.paper_orchestrator.refused.connect(self._report_paper_launch_refusal)
        self.paper_orchestrator.log_requested.connect(self._log)
        # Every Paper result -- the launch that reached RUNNING and every later stream
        # tick, poll, pause, resume, stop, reconciliation and finalization -- arrives here
        # and nowhere else.  One handler means one render path, and the window publishes
        # no result of its own any more.
        self.paper_orchestrator.result_changed.connect(
            self._on_paper_result_changed
        )
        self.paper_orchestrator.runtime_event_requested.connect(
            self._route_runtime_event
        )
        # The three v2O-E3 publications.  Each one replaces a decision the window used to
        # make for itself: which controls to repaint when no result exists, whether the
        # session needs a human, and whether a finished session's ownership is gone.
        self.paper_orchestrator.presentation_refresh_requested.connect(
            self._apply_paper_workflow_button_state
        )
        self.paper_orchestrator.manual_recovery_required.connect(
            self._on_paper_manual_recovery_required
        )
        self.paper_orchestrator.session_finalized.connect(
            self._on_paper_session_finalized
        )
        # Research Scenario Capital: the initial-equity figure historical
        # research, replay, scan affordability and cross-sectional portfolio
        # research run at.  It is *research-only* -- never broker equity, never
        # buying power, never a sizing authority -- and it has seven consumers
        # across four workspaces, so it is not a Cross Section fact.  The state
        # object is its single canonical owner; the Cross Section page is its
        # only editor and every other workflow reads it when it needs the value.
        self.research_scenario_capital = ResearchScenarioCapitalState(
            int(self.config.initial_equity)
        )
        self.task_controller = DesktopTaskController[TaskThread]()
        self.workers = self.task_controller.workers
        # The universe, history and scanner routes keep their runtime in
        # ``desktop_v2/orchestration/research``.  There is deliberately no
        # ``self.universe``, no ``self.scan``, no refresh ``Event``/worker
        # handle and no history progress percentage here, and no compatibility
        # property either: a forwarding property would keep every unmigrated
        # caller silently working, so "who reads universe or scan truth" would
        # stop being one grep.
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
        # H-1 fix: independent Paper order watchdog heartbeat.  The order
        # lifecycle (stale-BUY cancel, SELL intervention, health evaluation)
        # must keep running even when the market stream is down or stopped;
        # it only skips when stream ticks have driven the same watchdog
        # recently.  The heartbeat, the phase gate and the suppression window
        # are all active-Paper orchestration, so the timer enters the
        # orchestrator directly rather than through a window handler.
        self.paper_order_timer = QTimer(self)
        self.paper_order_timer.setInterval(1_000)
        self.paper_order_timer.timeout.connect(
            self.paper_orchestrator.poll
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

        The market route is composed first: its orchestrator is the window's
        market runtime owner, and the settings view asks it which source holds
        the connection while the rest of the routes are still being built.
        """

        # The market page owns its widgets and reports intent.  The market
        # *runtime* -- the worker, the snapshot, the poll timer, the pending
        # switch and the page's rendering -- belongs to ``market_orchestrator``,
        # which is constructed here with its dependencies injected.  What stays
        # on the window is the cross-workflow part: the Paper and Shadow
        # interlocks that decide whether a stop or a switch is allowed, and the
        # fan-out of market facts to the other workflows.
        self.market_page = MarketPage(
            palette=self.theme,
            theme_name=self.current_theme_name,
            selected_provider=self.preferences.market_provider,
        )
        self.market_orchestrator = MarketOrchestrator(
            market_data=self.market_data,
            credential_service=self.credential_service,
            page=self.market_page,
        )
        self._connect_market_page()

        self.targeted_validation_page = TargetedValidationPage(palette=self.theme)
        # The Targeted workspace's *session* half -- the target draft, the target
        # status, the minute-evidence status, the preflight and the one session
        # paint -- belongs to ``targeted_session_orchestrator``, built just below.
        #
        # It is built *first* because the evidence capability's target provider
        # reads this session's snapshot: one shared fact, passed as a provider
        # rather than an object handle, so neither capability imports the other.
        # That is also what makes "the operator typed AAPL but did not apply it"
        # work -- the draft is the input, and Replay and Robustness read it as the
        # run's symbol.
        #
        # Every dependency is a callable onto a *fact the window can currently
        # answer* rather than another capability.  The two Market commands are
        # injected as narrow callables -- subscribe and start -- because the
        # session may ask for a stream and must not own one.
        self.targeted_session_service = DesktopTargetedSessionService(
            minute_quote_store=self.minute_quote_store,
        )
        self.targeted_session_orchestrator = TargetedSessionOrchestrator(
            page=self.targeted_validation_page,
            service=self.targeted_session_service,
            universe_provider=lambda: self.universe_orchestrator.snapshot,
            market_snapshot_provider=lambda: self.market_orchestrator.snapshot,
            market_is_live=lambda: self.market_orchestrator.is_live,
            account_provider=self._targeted_account_snapshot,
            selected_strategy_provider=self._selected_shadow_strategy_record,
            displayed_strategy_provider=self._targeted_displayed_strategy,
            strategy_options_provider=self._targeted_strategy_options,
            strategy_selector=self._targeted_strategy_selected,
            exposure_multipliers_provider=self._configured_exposure_multipliers,
            shadow_snapshot_provider=lambda: self.shadow_orchestrator.snapshot,
            market_set_subscription=self.market_orchestrator.set_subscription_symbols,
            market_start=self.market_orchestrator.start,
        )
        # The targeted workspace's *research evidence* runtime -- the seven result
        # families, the two run selections, the replay and robustness requests and
        # the evidence render -- belongs to ``targeted_evidence_orchestrator``.
        # Note what is *not* stored: there is no ``self.targeted_replay_results``
        # and no ``self._selected_review_run_id``, because this orchestrator is the
        # canonical owner and the evidence tables are painted only from it.  The
        # procedure itself -- the artifact paths and the minute evidence reads --
        # is in the service next to it.
        #
        # The dependencies are deliberate: the universe arrives as a ``Callable``
        # so the capability never imports ``UniverseOrchestrator``; the strategy
        # as a ``Callable`` onto the selection service, so a change to strategy
        # application does not reach it; the target symbol as a ``Callable`` onto
        # the session snapshot, so the capability never reaches through
        # ``session_panel`` into an editor and never imports its sibling; and the
        # research capital is the one shared state object, which this capability
        # reads but does not own.
        #
        # Each request-time provider is read **once per request**, which is what
        # makes "the strategy and capital the operator saw" true of a run that
        # starts later on a worker.
        self.targeted_evidence_service = DesktopTargetedEvidenceService(
            minute_store=self.minute_quote_store,
            results_root=self.paths.research_results_root,
        )
        self.targeted_evidence_orchestrator = TargetedEvidenceOrchestrator(
            service=self.targeted_evidence_service,
            page=self.targeted_validation_page,
            submit_task=self._start_task,
            universe_provider=lambda: self.universe_orchestrator.snapshot,
            strategy_provider=lambda: self._selected_shadow_strategy_record(),
            target_symbol_provider=(
                lambda: self.targeted_session_orchestrator.snapshot.target_draft
            ),
            capital_state=self.research_scenario_capital,
        )
        # The Targeted workspace's *internal simulation* runtime -- the engine,
        # its snapshot, the start gates, the stream ingress and the stop path --
        # belongs to ``shadow_orchestrator``, constructed here with its
        # dependencies injected.  It is built after the session capability
        # because its target provider reads the session's canonical draft, and
        # after the account route because its capital provider reads the Paper
        # portfolio.  What stays on the window is composition and the routing of
        # the facts it publishes.  Note what is *not* stored: no ``shadow_engine``
        # and no ``shadow_snapshot``, because this orchestrator is the canonical
        # desktop owner.
        #
        # Every dependency is a callable onto a fact the window can currently
        # answer rather than another capability, so this module never imports
        # Account, Market, Universe or the targeted session.  The lease is the
        # *shared* one the workflow controller composed, so "Shadow and Paper
        # cannot both hold execution" stays structural rather than checked here.
        self.shadow_orchestrator = ShadowOrchestrator(
            store=self.shadow_store,
            lease=self.shadow_workflow,
            strategy_provider=lambda: self._selected_shadow_strategy_record(),
            target_provider=(
                lambda: self.targeted_session_orchestrator.snapshot.target_draft
            ),
            capital_provider=lambda: self._shadow_capital_fact(),
            account_alias_provider=self._shadow_account_alias,
            market_stream_provider=lambda: self.market_orchestrator.snapshot,
            market_is_live=lambda: self.market_orchestrator.is_live,
            universe_provider=lambda: self.universe_orchestrator.snapshot,
            runtime_is_active=lambda: self._paper_runtime_is_active(),
            exposure_multipliers_provider=(
                lambda: self._configured_exposure_multipliers()
            ),
            render_session=(
                lambda: self.targeted_session_orchestrator.render_current()
            ),
        )
        self.shadow_orchestrator.refused.connect(self._report_shadow_refusal)
        self.shadow_orchestrator.log_requested.connect(self._log)
        self.shadow_orchestrator.runtime_event_requested.connect(
            self._route_runtime_event
        )
        # Wired last, because the Shadow intents reach the orchestrator built
        # just above rather than a window handler.
        self._connect_targeted_validation_page()
        self.universe_page = UniversePage(palette=self.theme)
        # The universe route's desktop runtime -- the official snapshot, the
        # refresh request and the cancel event -- belongs to
        # ``universe_orchestrator``, constructed here with its dependencies
        # injected.  What stays on the window is composition and the
        # cross-workflow fan-out of the published snapshot.  Note what is *not*
        # stored: there is no ``self.universe``, because this orchestrator is
        # the canonical desktop owner and every consumer reads
        # ``universe_orchestrator.snapshot``.
        self.universe_orchestrator = UniverseOrchestrator(
            service=self.universe_service,
            page=self.universe_page,
            submit_task=self._start_task,
        )
        self._connect_universe_page()
        self.history_page = HistoryPage(palette=self.theme)
        # Same shape for history, with two differences that matter: the queue
        # truth stays in ``history_service`` and this orchestrator caches none
        # of it, and both providers are callables rather than objects -- so
        # History never imports the universe implementation, and an IBKR run
        # always reads the *current* connection config rather than the one that
        # was live when the window was built.
        self.history_orchestrator = HistoryOrchestrator(
            service=self.history_service,
            page=self.history_page,
            submit_task=self._start_task,
            universe_provider=lambda: self.universe_orchestrator.snapshot,
            ibkr_config_provider=lambda: self.config.ibkr,
        )
        self._connect_history_page()
        self.scanner_page = ScannerPage(palette=self.theme)
        # The scanner route's desktop runtime -- the canonical scan, the manual
        # scan request, the startup restore, the cross-workflow adoption and the
        # chart read -- belongs to ``scanner_orchestrator``.  Note what is *not*
        # stored: there is no ``self.scan``, because this orchestrator is the
        # canonical desktop owner and every consumer reads
        # ``scanner_orchestrator.scan``.  Both providers are callables, so the
        # capability never imports the universe implementation and never reads
        # ``config``: the window decides what a scan runs with.
        self.scanner_orchestrator = ScannerOrchestrator(
            service=self.market_scan_service,
            page=self.scanner_page,
            submit_task=self._start_task,
            universe_provider=lambda: self.universe_orchestrator.snapshot,
            run_inputs_provider=self._scanner_run_inputs,
        )
        self._connect_scanner_page()
        self.backtest_page = BacktestPage(
            per_share_commission=(
                self.config.execution.per_share_commission
            ),
            minimum_commission=self.config.execution.minimum_commission,
            slippage_bps=self.config.execution.slippage_bps,
            palette=self.theme,
        )
        # The backtest route's desktop runtime -- this session's runs, the
        # comparison selection and the busy flag -- belongs to
        # ``backtest_orchestrator``, constructed here with its dependencies
        # injected.  Note what is *not* stored: there is no
        # ``self.backtest_runs``, because this orchestrator is the canonical
        # desktop owner and the page is painted only from it.  The strategy
        # catalogue arrives as a provider, so the capability never imports
        # ``StrategySelectionService`` and a change to strategy application does
        # not reach it.
        self.backtest_orchestrator = BacktestOrchestrator(
            service=self.backtest_service,
            page=self.backtest_page,
            submit_task=self._start_task,
            task_available=lambda group: self.task_controller.can_start(
                group
            ),
            strategy_versions_provider=lambda: self.strategy_selection.options(
                StrategySelectionPurpose.BACKTEST
            ),
        )
        self._connect_backtest_page()
        self.backtest_orchestrator.refresh_strategy_options()
        self.backtest_orchestrator.render_current()
        self.cross_section_page = CrossSectionResearchPage(
            research_capital=self.research_scenario_capital.value,
            palette=self.theme,
        )
        # The Cross Section route's desktop runtime -- the last valid report,
        # the scenario-capital edit path, the run request and the page render --
        # belongs to ``cross_section_orchestrator``.  Note what is *not* stored:
        # there is no ``self.cross_section_report``, because the capability is
        # the canonical desktop owner, and no ``self.cross_section_path``,
        # because the service owns the artifact boundary.  The universe arrives
        # as a provider so the capability never imports ``UniverseOrchestrator``;
        # the capital state is passed in rather than owned, because it is shared
        # with four other workspaces.
        #
        # The page is deliberately *not* painted here.  ``restore_saved()``
        # during startup is the first paint, exactly once, so the build stage
        # cannot produce a render that a later load would immediately repeat --
        # the double-paint that the Universe round had to remove.
        self.cross_section_orchestrator = CrossSectionOrchestrator(
            service=self.cross_section_service,
            page=self.cross_section_page,
            submit_task=self._start_task,
            universe_provider=lambda: self.universe_orchestrator.snapshot,
            capital_state=self.research_scenario_capital,
        )
        self._connect_cross_section_page()
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
        #
        # Runtime Events is the first System capability with its own owner
        # (v2O-F1).  The store is constructed inside the orchestrator call and
        # the window keeps no reference to it, no refresh stamp, no pending flag
        # and no last-export fact: what stays here is composition -- the
        # environment facts the info panel prints, the task-count provider, the
        # cross-capability export provider, the page's three intents and the
        # dialogs the capability publishes.
        self.runtime_events_page = RuntimeEventsPage(palette=self.theme)
        self.runtime_events_orchestrator = RuntimeEventsOrchestrator(
            store=RuntimeEventStore(
                self.paths.runtime_root / "runtime_events.sqlite3"
            ),
            page=self.runtime_events_page,
            environment=RuntimeEventsEnvironment(
                version="0.19.0",
                resource_root=self.paths.resource_root,
                state_root=self.paths.state_root,
                runtime_root=self.paths.runtime_root,
                exports_root=self.paths.exports_root,
            ),
            # A provider, not a value: the count is read on every repaint, so a
            # task that starts or finishes after this line is never missed.  The
            # lifecycle it counts is still the window's.
            active_task_count=lambda: len(self.workers),
            export_bundle=self._export_runtime_bundle,
            parent=self,
        )
        self._connect_runtime_events_page()
        # The first paint goes through the same entry point every later repaint
        # uses, so "never read" and "read" cannot diverge into two paths.
        self.runtime_events_orchestrator.refresh()
        # Settings is the second System capability with its own owner (v2O-F2).
        # The window keeps no selected-API-provider fact and no
        # connection-control fact; it supplies the page's initial draft and
        # storage view through two pure projections, hands the capability its
        # services and narrow providers, and adopts the finished commit.
        self.settings_page = SettingsPage(
            draft=settings_draft_from_preferences(self.preferences),
            storage=settings_storage_view(
                state_root=self.paths.state_root,
                runtime_root=self.paths.runtime_root,
                exports_root=self.paths.exports_root,
            ),
        )
        self.settings_orchestrator = SettingsOrchestrator(
            page=self.settings_page,
            settings_service=self.settings_service,
            credential_service=self.credential_service,
            # The mapping is the page's own published pure function, so the
            # initial credential provider and a later market-provider selection
            # cannot drift into two rules.
            initial_api_provider=api_provider_for_market_provider(
                self.preferences.market_provider
            ),
            # Providers, not values: the transaction reads the config that is
            # current when the operator saves, not the one that was live when
            # the window was built.
            current_config=lambda: self.config,
            broker_config=lambda: self.broker_account,
            runtime_guards=lambda: (self.market_data,),
            # Read on every repaint: whether a credential may be cleared depends
            # on the stream that is running *now*.
            active_market_source_id=(
                lambda: self.market_orchestrator.active_source_id
            ),
            parent=self,
        )
        self._connect_settings_page()
        # The first paint goes through the same entry point every later repaint
        # uses, so "never read" and "read" cannot diverge into two paths.
        self.settings_orchestrator.render_current()
        self.system_page = SystemPage(
            {
                SystemWorkspace.RUNTIME_EVENTS:
                    self.runtime_events_page,
                SystemWorkspace.SETTINGS: self.settings_page,
            }
        )

        self.account_page = AccountPage()
        # The account route's desktop runtime -- the refresh request, the
        # ledger append, the page render and the header facts -- belongs to
        # ``account_orchestrator``, constructed here with its dependencies
        # injected.  What stays on the window is composition and the
        # cross-workflow fan-out of the published portfolio.  Note what is
        # *not* stored: there is no ``self.account_portfolio``, because
        # ``broker_account`` is the canonical truth and the orchestrator only
        # delegates to it.
        self.account_orchestrator = AccountOrchestrator(
            application=self.broker_account,
            ledger=self.account_ledger,
            page=self.account_page,
            submit_task=self._start_task,
        )
        self.account_page.refresh_requested.connect(
            self.account_orchestrator.request_refresh
        )
        self.account_orchestrator.portfolio_changed.connect(
            self._on_account_portfolio_changed
        )
        self.account_orchestrator.shell_health_changed.connect(
            self._render_account_shell_health
        )
        self.account_orchestrator.runtime_event_requested.connect(
            self._route_runtime_event
        )
        self.account_orchestrator.log_requested.connect(self._log)
        # The research-capital widget lives on the Cross Section page and the
        # research scenario scalar has its own state owner; this call pushes the
        # *composed* presentation inputs -- the exposure multipliers and the
        # scenario figure -- so the account route renders them through its own
        # render.  There is deliberately no separate capital paint path here:
        # ``AccountOrchestrator`` is the Account page's only render owner.
        self._publish_account_presentation_inputs()
        # The risk page renders the limits the risk layer enforces and the
        # standing safety boundaries.  It is read-only by construction: it has
        # no service to call and no control that could widen a ceiling.
        self.risk_page = RiskPage(palette=self.theme)
        self.risk_page.render(self.config.risk_limits)
        # The page's first paint goes through the same render entry point every
        # later refresh uses, so "never read" and "read" cannot diverge into two
        # rendering paths.
        self.account_orchestrator.render_current()

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

        self.dashboard_page = DashboardPage(palette=self.theme)
        self._connect_dashboard_page()
        self._publish_dashboard_view()

        pages: dict[str, QWidget] = {
            "dashboard": self.dashboard_page,
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
        """Wire the Runtime Events page to its owner, both directions.

        The page's three intents are commands on ``runtime_events_orchestrator``,
        and the owner's three messages are dialogs the window shows.  Neither
        side reaches across: the page never touches the store and the
        orchestrator never touches a widget.
        """

        page = self.runtime_events_page
        orchestrator = self.runtime_events_orchestrator
        page.refresh_requested.connect(orchestrator.refresh)
        page.resolve_requested.connect(orchestrator.resolve)
        page.export_requested.connect(orchestrator.export)
        orchestrator.information_requested.connect(
            self._show_runtime_information
        )
        orchestrator.warning_requested.connect(self._show_runtime_warning)
        orchestrator.export_succeeded.connect(
            self._show_runtime_export_succeeded
        )

    def _connect_settings_page(self) -> None:
        """Wire the Settings page to its owner, and the owner to the workbench.

        Two directions, and neither side reaches across.  The page's nine
        intents are commands on ``settings_orchestrator``; the owner's published
        facts are composition here -- the global theme fan-out, the market
        route's provider selection and switch (both behind the existing
        interlocks), the adoption of a finished commit, the dialogs, and the
        status line.  The window no longer assembles a ``SettingsPageView`` and
        no longer calls ``settings_page.render``.
        """

        page = self.settings_page
        orchestrator = self.settings_orchestrator
        page.theme_preview_requested.connect(orchestrator.preview_theme)
        page.market_provider_selected.connect(
            orchestrator.select_market_provider
        )
        page.switch_provider_requested.connect(
            orchestrator.request_provider_switch
        )
        page.api_provider_selected.connect(orchestrator.select_api_provider)
        page.save_credentials_requested.connect(
            orchestrator.save_credentials
        )
        page.clear_credentials_requested.connect(
            orchestrator.clear_credentials
        )
        page.paper_order_capability_toggled.connect(
            orchestrator.request_paper_order_capability_toggle
        )
        page.extended_hours_paper_toggled.connect(
            orchestrator.request_extended_hours_toggle
        )
        page.save_preferences_requested.connect(
            orchestrator.save_preferences
        )

        # The market route publishes whether its connection controls may be
        # edited; the Settings capability owns that presentation fact and the
        # repaint that follows.  Wired here rather than with the market page
        # because both objects exist by now.
        self.market_orchestrator.connection_settings_enabled_changed.connect(
            orchestrator.set_connection_settings_enabled
        )
        # The global theme fan-out is the window's: applying a palette reaches
        # every page in the workbench, which Settings must not learn about.
        orchestrator.theme_preview_requested.connect(self._apply_theme)
        # Pointing the market route's combo is the market layer's, not Settings'.
        orchestrator.market_provider_selection_requested.connect(
            self.market_orchestrator.set_selected_provider
        )
        orchestrator.market_switch_requested.connect(
            self._on_market_switch_requested
        )
        orchestrator.settings_committed.connect(self._on_settings_committed)
        orchestrator.information_requested.connect(
            self._show_settings_information
        )
        orchestrator.warning_requested.connect(self._show_settings_warning)
        orchestrator.log_requested.connect(self._log)
        orchestrator.paper_order_capability_confirmation_requested.connect(
            self._confirm_paper_order_capability
        )
        orchestrator.extended_hours_confirmation_requested.connect(
            self._confirm_extended_hours_paper
        )

    def _connect_execution_page(self) -> None:
        """Wire the execution page's intents to the handlers that act on them.

        Every session intent now has one owner: ``pause``, ``resume``, ``stop``,
        ``reconcile`` and the confirmed ``resume-after-reconciliation`` all go straight to
        ``paper_orchestrator``, which is the only thing that decides whether the request
        may happen and what the workflow makes of it.  The rest still pass through the
        window, because their orchestration is not Paper's: the launch confirmation and
        the resume confirmation are presentation, and the preparation, channel probe and
        market stop are the window's own composition.
        """

        page = self.execution_page
        page.strategy_selected.connect(self._auto_strategy_selected)
        page.preflight_inputs_changed.connect(self._refresh_auto_quant_preflight)
        page.prepare_requested.connect(self._prepare_auto_quant_candidates)
        page.channel_check_requested.connect(self._check_auto_order_channel)
        page.start_requested.connect(self._confirm_and_start_auto_quant)
        page.stop_stream_requested.connect(self._stop_auto_market_data)
        page.pause_requested.connect(self.paper_orchestrator.pause)
        page.resume_requested.connect(self.paper_orchestrator.resume)
        page.stop_requested.connect(self.paper_orchestrator.stop)
        page.reconcile_requested.connect(self.paper_orchestrator.reconcile)
        page.resume_reconciliation_requested.connect(
            self._confirm_paper_reconciliation_resume
        )

    def _connect_market_page(self) -> None:
        """Wire the market page's intents and the orchestrator's publications.

        The page signals reach the *window*, not the orchestrator directly,
        because every market request still has to pass a cross-workflow gate
        first: a stop is refused while a Paper session holds positions, and a
        switch is refused for the same reason.  The gates are thin -- they read
        the Paper and Shadow facts, decide, and then call the orchestrator.

        The orchestrator's own signals are connected here too, because the
        window is still the fan-out point: market facts go on to the dashboard,
        the minute recorder, the auto-quant shortlist, the Paper workflow and
        the Shadow book, and none of those belong to the market route.
        """

        page = self.market_page
        orchestrator = self.market_orchestrator
        page.provider_selected.connect(self._on_market_provider_selected)
        page.start_requested.connect(self._request_market_start)
        page.stop_requested.connect(self._request_market_stop)
        page.load_scan_watchlist_requested.connect(self._apply_intraday_watchlist)

        orchestrator.snapshot_changed.connect(self._on_market_snapshot_changed)
        orchestrator.snapshot_invalidated.connect(
            self._on_market_snapshot_invalidated
        )
        orchestrator.shell_health_changed.connect(
            self._render_market_shell_health
        )
        orchestrator.controls_changed.connect(self._publish_execution_controls)
        # ``connection_settings_enabled_changed`` is connected with the Settings
        # wiring rather than here: its only consumer is the Settings capability,
        # which is built later in this same pass.
        orchestrator.log_requested.connect(self._log)
        orchestrator.runtime_event_requested.connect(
            self._route_runtime_event
        )
        orchestrator.task_failure_requested.connect(self._task_failed)
        orchestrator.refused.connect(self._report_market_refusal)
        orchestrator.automatic_switch_requested.connect(
            self._request_automatic_market_switch
        )

    # -- the cross-workflow market bridges ------------------------------
    #
    # These three are composition-level interlocks, not compatibility wrappers.
    # Whether a market stop or switch is allowed depends on the Paper session
    # and the Shadow book, which the market orchestrator may not name; so the
    # window reads those facts, decides, and only then calls in.  None of them
    # touches the worker, the snapshot, the page or the poll timer -- that
    # ownership moved, and these must not take it back.

    def _request_market_start(self) -> None:
        """Start the feed; the orchestrator owns everything past this point."""

        self.market_orchestrator.start()

    def _request_market_stop(self, *_args: object) -> None:
        """Stop the feed, unless a Paper session or the Shadow book forbids it."""

        self._stop_market_data()

    def _request_market_switch(
        self,
        provider: str,
        *,
        allow_auto_session_switch: bool = False,
    ) -> None:
        """Switch the feed, unless a Paper session forbids it.

        The Paper interlock is the reason this bridge exists at all: the
        orchestrator may not import the Paper workflow, so the refusal decision
        stays here and the market layer is only asked once it is allowed.  The
        fact it is decided on is the Paper capability's own answer
        (``has_runtime_obligations``), read from the canonical result rather than
        from a snapshot cache this window keeps, so the interlock and the panel
        the operator is looking at can never disagree.
        """

        if (
            self.paper_orchestrator.has_runtime_obligations
            and not allow_auto_session_switch
        ):
            QMessageBox.warning(
                self,
                "自动量化会话仍在运行",
                "Paper 持仓或在途订单存在时禁止切换行情源。"
                "请先停止自动量化并完成券商对账。",
            )
            return
        self.market_orchestrator.request_switch(provider)

    def _request_automatic_market_switch(self, provider: str) -> None:
        """An IBKR 5×24 session rotation, which is allowed to bypass the interlock."""

        self._request_market_switch(provider, allow_auto_session_switch=True)

    def _stop_market_data(self) -> bool:
        """The market stop, with the Paper and Shadow interlocks applied.

        Returns False when a refusal left the feed running, so the supervisor's
        join verdict and the switch path both see the truth.
        """

        if self.paper_orchestrator.has_runtime_obligations:
            QMessageBox.warning(
                self,
                "自动量化会话仍在运行",
                "必须先在“自动量化”点击停止，并等待 Paper 持仓和"
                "在途订单完成对账后才能停止行情。",
            )
            return False
        if self.shadow_orchestrator.is_active:
            self.shadow_orchestrator.stop()
        return self.market_orchestrator.stop()
    def _report_market_refusal(self, title: str, message: str) -> None:
        """Surface a request the market layer refused before touching the feed."""

        QMessageBox.warning(self, title, message)

    def _render_market_shell_health(self, health: object) -> None:
        """Paint the shell header from the market layer's published facts.

        The badges belong to the whole workbench, so the orchestrator publishes
        text and state and the window paints them.  A handshake of ``None``
        means "leave the badge alone": the legacy rule only ever promoted it.
        """

        if health.handshake_text is not None:
            self.handshake_badge.setText(health.handshake_text)
            self.handshake_badge.setProperty(
                "state", health.handshake_state
            )
        self.market_badge.setText(health.market_text)
        self.market_badge.setProperty("state", health.market_state)
        self._repolish_health_badges()
        if health.status_log is not None:
            self._log(health.status_log)

    def _on_market_snapshot_changed(self, snapshot: object) -> None:
        """Fan one market fact out to the workflows that still consume it.

        This is the temporary cross-workflow bridge the extraction leaves
        behind.  Each consumer below is a *different* capability that a later
        round will own: minute evidence, auto-quant candidates, execution
        preflight, Paper and Shadow.  What this method must never do again is
        render the market page, touch the worker or maintain the readiness
        cache -- those moved into the orchestrator, and a guard pins that -- and
        since v2O-E2 neither does it decide anything about the Paper session: it
        hands each capability the fact and lets the capability answer for itself.
        """

        self.workflow_controller.market_account.update(
            account_ready=self.account_orchestrator.portfolio is not None,
            market_ready=snapshot.realtime_ready,
            message=snapshot.message,
        )
        self._record_minute_snapshot(snapshot)
        self._publish_dashboard_view()
        self._populate_auto_quant_candidates()
        self.targeted_session_orchestrator.refresh_preflight()
        # Paper and Shadow each decide for themselves whether this fact belongs to a
        # live session, so the fan-out hands it over and stops there.  The window no
        # longer checks the Paper phase, stamps an ingress or reaches the workflow here:
        # "when does the active session consume the market?" is one owner's question,
        # and a second answer to it is how a halted session gets re-entered.
        self.paper_orchestrator.on_market_snapshot(snapshot)
        self.shadow_orchestrator.on_market_snapshot(snapshot)

    def _on_market_snapshot_invalidated(self) -> None:
        """The feed's snapshot was invalidated; the dashboard card must follow."""

        self._publish_dashboard_view()

    def _publish_market_readiness_inputs(self) -> None:
        """Hand the market layer the cross-domain symbols it must classify.

        The readiness breakdown counts how many candidates and reference
        symbols are fresh, but the candidate shortlist is the execution route's
        and the reference symbols come from the selected strategy.  Neither is
        market data this window's market layer may look up, so they are pushed
        in as finished data whenever either one changes.
        """

        self.market_orchestrator.set_readiness_inputs(
            MarketReadinessInputs(
                candidate_symbols=tuple(
                    row.symbol for row in self.auto_quant_candidates
                ),
                reference_symbols=self._auto_quant_market_reference_symbols(),
            )
        )

    def _connect_dashboard_page(self) -> None:
        """Wire the Dashboard's only user intent to its business handler."""

        self.dashboard_page.gateway_probe_requested.connect(
            self._probe_gateway
        )

    def _publish_dashboard_view(self) -> None:
        """Project window facts onto the native Dashboard page once."""

        if not hasattr(self, "dashboard_page"):
            return
        self.dashboard_page.render(
            build_dashboard_view(
                portfolio=self.account_orchestrator.portfolio,
                snapshot=self.market_orchestrator.snapshot,
                artifacts=self.artifact_catalog.artifacts,
                chart=self._dashboard_chart_view,
                market_stop_reason=self.market_orchestrator.stop_reason,
            )
        )

    def _connect_targeted_validation_page(self) -> None:
        """Wire the targeted page's intents to their owners.  No business here.

        Three groups, three owners, and the split is the point of this round:

        * **session intents** go to ``targeted_session_orchestrator`` -- the target
          draft, the strategy choice, 应用标的 and 订阅该标的行情.  The window used to
          handle all four itself;
        * **evidence intents** go to ``targeted_evidence_orchestrator``, which has
          owned them since v2O-C5A;
        * **Shadow intents** go to ``shadow_orchestrator`` (v2O-D).  Starting and
          stopping the internal simulation is the Shadow runtime, which the session
          capability must not own -- so it reaches its own orchestrator directly,
          and the window has no handler for either any more.

        Nothing is interpreted -- every line is a connect.
        """

        page = self.targeted_validation_page
        session = self.targeted_session_orchestrator
        evidence = self.targeted_evidence_orchestrator
        # Session intents: the capability's.
        page.target_draft_changed.connect(session.adopt_target_draft)
        page.strategy_selected.connect(session.request_strategy_selection)
        page.target_apply_requested.connect(session.request_target_apply)
        page.target_subscribe_requested.connect(session.request_target_subscribe)
        # Shadow intents: the Shadow runtime's own capability.
        page.shadow_start_requested.connect(self.shadow_orchestrator.start)
        page.shadow_stop_requested.connect(self.shadow_orchestrator.stop)
        # Evidence intents: the evidence capability's.  A replay or robustness
        # request validates, freezes its inputs, submits its own task and paints
        # its own evidence -- the window has no handler for either any more.
        page.replay_requested.connect(evidence.request_replay)
        page.robustness_requested.connect(evidence.request_robustness)
        page.robustness_run_selected.connect(evidence.select_robustness_run)
        page.review_run_selected.connect(evidence.select_review_run)
        # The capabilities publish finished facts; the window routes them.  A
        # refusal is a dialog, a log line is the footer, a runtime event is the
        # store, a minute refresh is the session capability's own command, and
        # focus is the research route -- none of which is a capability's decision.
        session.refused.connect(self._report_targeted_session_refusal)
        session.log_requested.connect(self._log)
        evidence.refused.connect(self._report_targeted_evidence_refusal)
        evidence.log_requested.connect(self._log)
        evidence.runtime_event_requested.connect(
            self._route_runtime_event
        )
        evidence.minute_status_refresh_requested.connect(
            session.refresh_minute_status
        )
        evidence.focus_requested.connect(self._focus_targeted_evidence)

    # -- Shadow composition inputs and routes ------------------------------
    #
    # Composition, not behaviour: each of these answers "what is the current value
    # of a fact this window can see?" for the Shadow capability, or routes a fact
    # it published.  None of them decides anything, and none of them touches the
    # engine, the store or the lease.

    def _shadow_capital_fact(self) -> ShadowCapitalFact | None:
        """The Paper amount a simulation may be sized from, or ``None``.

        Only the *amount* is read here, because this is the gate's input: the
        freshness rule itself belongs to the account capability, whose query this
        delegates to, so the simulator can never be sized from an account the
        preflight would reject.  The account it came from is named separately and
        later -- see :meth:`_shadow_account_alias`.
        """

        capital = self.account_orchestrator.fresh_paper_net_liquidation()
        if capital is None:
            return None
        return ShadowCapitalFact(net_liquidation=capital)

    def _shadow_account_alias(self) -> str:
        """The Paper account the run's capital came from, for its provenance line.

        Read only while an engine is being built, i.e. after every gate passed, so
        a refusing start never touches the portfolio.  That ordering is the
        retired handler's and it is preserved here rather than tidied into one
        eager read.
        """

        return self.account_orchestrator.portfolio.account.account_alias

    def _paper_runtime_is_active(self) -> bool:
        """Whether the IBKR Paper runtime already owns the capital truth.

        Read here because the competing session's lifecycle is the execution
        route's, not Shadow's: the Shadow orchestrator must not learn that an
        auto-rotation session exists, only whether it may start.  The answer comes
        from the Paper capability, which reads its own canonical result -- the window
        holds no runtime handle to ask.
        """

        return self.paper_orchestrator.runtime_active

    def _report_shadow_refusal(self, title: str, message: str) -> None:
        """Surface a start the Shadow layer refused before building anything."""

        QMessageBox.warning(self, title, message)

    # -- Targeted session presentation inputs -----------------------------
    #
    # Composition, not behaviour: each of these answers "what is the current
    # value of a fact this window can see?" for the session capability, which
    # reads them through callables.  None of them decides anything.

    def _targeted_account_snapshot(
        self,
    ) -> BrokerAccountSnapshot | None:
        """The canonical broker account truth, or ``None``.

        Read from the account route's portfolio -- the Paper capital gate reads
        *broker* truth, never the research scenario figure.  The two are different
        facts with different trust levels and must not be conflated.
        """

        portfolio = self.account_orchestrator.portfolio
        return portfolio.account if portfolio is not None else None

    def _targeted_strategy_options(
        self,
    ) -> tuple[TargetedStrategyOption, ...]:
        """The targeted-shadow selection's eligible versions, as combo options.

        The label projection lives here rather than in the capability so the
        capability imports no page module for it; the order and the eligibility
        are the selection service's.
        """

        purpose = StrategySelectionPurpose.TARGETED_SHADOW
        return tuple(
            TargetedStrategyOption(
                version.version_id,
                strategy_option_label(version),
            )
            for version in self.strategy_selection.options(purpose)
        )

    def _targeted_displayed_strategy(self) -> StrategyVersion | None:
        """The version the targeted combo should display, adopting a replacement.

        ``restore_or_default``, not ``selected``: the combo is a view of the
        service's choice, and a version stopped since the last paint must move the
        combo on rather than leave it showing a version the runtime will not run.
        """

        return self.strategy_selection.restore_or_default(
            StrategySelectionPurpose.TARGETED_SHADOW
        )

    def _targeted_strategy_selected(self, version_id: str) -> None:
        """Adopt the operator's targeted strategy choice as the runtime selection.

        The seat of truth stays ``StrategySelectionService``: the capability hands
        over the id the page emitted and never caches a version.
        """

        self._record_runtime_strategy_selection(
            StrategySelectionPurpose.TARGETED_SHADOW,
            version_id,
        )

    def _connect_universe_page(self) -> None:
        """Wire the universe page to its capability; no business logic here.

        The window is allowed to connect signals -- that is what composition
        means -- so this stays a pure wiring method rather than being inlined
        away.  What it may *not* contain is the refresh procedure, the cancel
        state or the render, all of which now live in the orchestrator.
        """

        page = self.universe_page
        page.refresh_requested.connect(
            self.universe_orchestrator.request_refresh
        )
        page.cancel_refresh_requested.connect(
            self.universe_orchestrator.request_cancel
        )
        # The capability publishes facts; the window routes them.  Whether the
        # market-scope summary, the scanner or the shadow gate react to a new
        # universe is not a universe decision.
        orchestrator = self.universe_orchestrator
        orchestrator.log_requested.connect(self._log)
        orchestrator.snapshot_changed.connect(self._on_universe_changed)

    def _connect_history_page(self) -> None:
        """Wire the history page to its capability; no business logic here."""

        page = self.history_page
        page.schedule_requested.connect(
            self.history_orchestrator.request_schedule
        )
        page.run_ibkr_requested.connect(
            self.history_orchestrator.request_run_ibkr
        )
        page.run_public_requested.connect(
            self.history_orchestrator.request_run_public
        )
        page.retry_failed_requested.connect(
            self.history_orchestrator.retry_failed
        )
        orchestrator = self.history_orchestrator
        orchestrator.log_requested.connect(self._log)
        orchestrator.history_changed.connect(
            self._refresh_market_scope_summary
        )
        # A refusal is the window's to show: the history capability holds no
        # widget, which is what keeps it importable without Qt.
        orchestrator.refused.connect(self._report_history_refusal)

    def _on_universe_changed(self, snapshot: UniverseSnapshot) -> None:
        """Route a new universe to whatever else consumes it.

        Deliberately thin, and deliberately not inside the capability.  A
        successful refresh moves the canonical ``universe.json`` into the
        writable reference root, and the market-scope summary is built from the
        universe plus the local history counts -- both are cross-workflow facts
        rather than universe decisions.
        """

        self.universe_path = self.reference_root / "universe.json"
        self._refresh_market_scope_summary()

    def _report_history_refusal(self, title: str, message: str) -> None:
        """Surface a request the history layer refused before touching a job.

        ``information`` rather than ``warning``: this is the severity the
        pre-extraction inline dialog used, and the extraction is not an
        occasion to change how the operator is told.
        """

        QMessageBox.information(self, title, message)

    def _connect_scanner_page(self) -> None:
        """Wiring only: the page reports intent, the capability owns the work."""

        page = self.scanner_page
        page.scan_requested.connect(
            self.scanner_orchestrator.request_scan
        )
        page.symbol_selected.connect(
            self.scanner_orchestrator.request_chart
        )
        orchestrator = self.scanner_orchestrator
        orchestrator.log_requested.connect(self._log)
        # A refusal is the window's to show: the scanner capability holds no
        # widget, which is what keeps it free of dialogs.
        orchestrator.refused.connect(self._report_scanner_refusal)
        # Both a manual scan and a cross-workflow adoption publish here, so the
        # scope line follows either without the window tracking them separately.
        # Startup restoration deliberately publishes nothing; ``_load_local_state``
        # refreshes the scope once at the end for every startup path.
        orchestrator.scan_changed.connect(
            self._refresh_market_scope_summary
        )

    def _scanner_run_inputs(self) -> ScannerRunInputs:
        """Freeze the facts one scan runs with, on the UI thread.

        This is a composition bridge, not scanner logic.  The research capital
        is a window-owned scalar, the risk percentage and the substitution
        rules are configuration, and the scanner capability is deliberately
        ignorant of all three: it receives a finished, immutable value.

        Called at *request* time, so a capital change that lands while a scan
        is queued cannot change the scan that is about to run.
        """

        return ScannerRunInputs.of(
            capital=self.research_scenario_capital.decimal_value,
            max_position_risk_pct=(
                self.config.risk_limits.max_position_exposure_pct
            ),
            substitutions=self.config.substitutions,
        )

    def _report_scanner_refusal(self, title: str, message: str) -> None:
        """Surface a request the scanner layer refused before scanning.

        ``information`` rather than ``warning``: this is the severity the
        pre-extraction inline dialog used, and the extraction is not an
        occasion to change how the operator is told.
        """

        QMessageBox.information(self, title, message)

    def _connect_cross_section_page(self) -> None:
        """Wiring only: the page reports intent, the capability owns the work."""

        page = self.cross_section_page
        page.run_requested.connect(
            self.cross_section_orchestrator.request_run
        )
        page.capital_changed.connect(
            self.cross_section_orchestrator.request_capital_change
        )
        orchestrator = self.cross_section_orchestrator
        orchestrator.capital_changed.connect(
            self._on_research_scenario_capital_changed
        )
        orchestrator.report_changed.connect(
            self._on_cross_section_report_changed
        )
        orchestrator.refused.connect(self._report_cross_section_refusal)
        orchestrator.log_requested.connect(self._log)

    def _on_research_scenario_capital_changed(self, value: int) -> None:
        """Publish a scenario-capital change to the account presentation.

        This is the whole cross-workflow bridge, and it is deliberately thin.
        The scalar's owner is ``ResearchScenarioCapitalState`` and this handler
        stores nothing: it re-pushes the composed presentation inputs and asks
        the account route to repaint, which is the only path by which the
        Account card learns the number changed.

        The Scanner, AutoQuant, Targeted and watchlist consumers are
        deliberately *not* notified.  None of them needs to run when the
        scenario figure changes -- each pulls the current value when it next
        builds a request -- so there is no event bus here and no eager rework.
        """

        self._publish_account_presentation_inputs()
        self.account_orchestrator.render_current()

    def _on_cross_section_report_changed(self) -> None:
        """Reload whatever reads the research artifact directory.

        A successful run rewrites the report, so the Dashboard's catalogue is
        stale.  That fan-out is not the Cross Section capability's business --
        it must not import the Dashboard or the artifact catalogue -- so the
        window routes it.  Startup restoration deliberately does not emit
        ``report_changed``: the catalogue is read during the same startup pass,
        and announcing a re-read would fan out a second time for nothing.
        """

        self.artifact_catalog = load_artifact_catalog(
            self.paths.research_results_root
        )
        self._publish_dashboard_view()

    def _report_cross_section_refusal(
        self, title: str, message: str
    ) -> None:
        """Surface a request the cross-section layer refused before running.

        ``information`` rather than ``warning``: this is the severity the
        pre-extraction inline dialog used, and the extraction is not an
        occasion to change how the operator is told.  The handler shows the
        dialog and nothing else.
        """

        QMessageBox.information(self, title, message)

    def _report_targeted_session_refusal(
        self, level: str, title: str, message: str
    ) -> None:
        """Surface a target command the session capability refused.

        The capability chose both the wording *and* the severity, and this handler
        shows the dialog and nothing else -- which is what keeps the capability
        free of widgets.  The severity is honoured rather than reduced to one
        call: an invalid symbol is a warning because the operator mistyped, while a
        running Shadow session or a live feed is information because nothing is
        wrong -- the request is simply unavailable right now.  Collapsing the two
        would tell the operator something false.
        """

        if level == REFUSAL_INFORMATION:
            QMessageBox.information(self, title, message)
        else:
            QMessageBox.warning(self, title, message)

    def _report_targeted_evidence_refusal(
        self, title: str, message: str
    ) -> None:
        """Surface a request the evidence capability refused before running.

        The capability chose the wording; this handler shows the dialog and
        nothing else, which is what keeps the capability free of widgets.
        """

        QMessageBox.warning(self, title, message)

    def _focus_targeted_evidence(self) -> None:
        """Bring the research route and the targeted workspace into view.

        A completed robustness suite wants its own result visible.  The capability
        has already moved the *evidence* workspace to the review section; whether
        the whole desktop navigates is a shell decision, which is why the
        capability holds no shell and this window holds no evidence state.
        """

        self.shell.navigate_to("research")
        self.research_page.set_active_workspace(ResearchWorkspace.TARGETED)

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



    def _configure_table(self, table: QTableWidget) -> None:
        """Apply the workbench's shared read-only table behaviour."""

        configure_table(table)

    def _configure_table_view(self, table: QTableView) -> None:
        """Apply shared behavior to model-backed high-frequency tables."""

        configure_table(table)

    def _load_local_state(self) -> None:
        # Restoration, not a refresh: the orchestrator adopts the snapshot and
        # publishes nothing, so a process that merely re-read a local file does
        # not announce an official refresh.  It *does* paint, because adopting a
        # snapshot changes what the page must show -- which is why the window
        # does not paint again below.  The capability owns the page render, and a
        # second call here would rebuild the whole 11k-row table for nothing.
        restored = False
        if self.universe_path.exists():
            try:
                self.universe_orchestrator.restore_snapshot(
                    load_universe_snapshot(self.universe_path)
                )
                restored = True
            except Exception as error:
                self._log(f"标的快照读取失败：{error}")
        if not restored:
            # Nothing was adopted, so the page still needs its first paint.
            self.universe_orchestrator.render_current()
        self.history_orchestrator.render_current()
        self._probe_gateway()
        # The scanner capability owns the artifact schema now: it restores the
        # saved scan and paints exactly once, and publishes nothing because
        # re-reading a local file is not a new scan.  The window therefore
        # neither parses the JSON nor repaints the page here.
        self.scanner_orchestrator.restore_saved()
        self._refresh_market_scope_summary()
        # The cross-section capability owns the report artifact schema now: it
        # restores the saved report and paints exactly once, and publishes
        # nothing because re-reading a local file is not new research.  The
        # window therefore neither parses the JSON nor checks for the file.
        self.cross_section_orchestrator.restore_saved()
        for symbol in ("SPY", "QQQ", "DIA"):
            try:
                points = load_close_series(
                    symbol,
                    data_root=self.data_root,
                    fallback_data_root=self.bundled_data_root,
                )
            except (FileNotFoundError, ValueError):
                continue
            self._dashboard_chart_view = DashboardChartView(
                symbol, tuple(points)
            )
            break
        self._publish_dashboard_view()
        # The targeted evidence capability owns its seven artifact schemas now: it
        # restores all seven families in one call and paints the evidence exactly
        # once, and publishes nothing because re-reading local files is not new
        # research.  The window therefore neither names the seven directories nor
        # repaints the evidence tables here.  The *session* half belongs to its own
        # capability now, so its first paint is one call to that capability -- a
        # session render, not an evidence render, and the two no longer share a
        # call.  Startup paints it exactly once, leaves the preflight ``None`` and
        # runs no empty-symbol evaluation: an unset target has nothing to check.
        self.targeted_evidence_orchestrator.restore_saved()
        self.targeted_session_orchestrator.render_current()

    def _connect_backtest_page(self) -> None:
        """Wiring only: the page reports intent, the capability owns the work."""

        page = self.backtest_page
        page.run_selected_requested.connect(
            self.backtest_orchestrator.request_selected
        )
        page.compare_all_requested.connect(
            self.backtest_orchestrator.request_compare_all
        )
        page.run_selected.connect(self.backtest_orchestrator.select_run)
        orchestrator = self.backtest_orchestrator
        orchestrator.log_requested.connect(self._log)
        # A refusal is the window's to show: the backtest capability holds no
        # widget, which is what keeps it free of dialogs.  The severity travels
        # with the message, so this handler never has to interpret a title.
        orchestrator.refused.connect(self._report_backtest_refusal)

    def _report_backtest_refusal(
        self, level: str, title: str, message: str
    ) -> None:
        """Surface a request the backtest layer refused before running.

        The severity is not a policy decision made here: the capability already
        chose ``information`` for "busy" and ``warning`` for the two validation
        refusals, and this round is not an occasion to change how the operator
        is told.  This handler shows the dialog and nothing else.
        """

        if level == REFUSAL_WARNING:
            QMessageBox.warning(self, title, message)
            return
        if level == REFUSAL_INFORMATION:
            QMessageBox.information(self, title, message)
            return
        raise ValueError(f"unknown backtest refusal level: {level!r}")

    def _apply_intraday_watchlist(self) -> None:
        scan = self.scanner_orchestrator.scan
        if scan is None:
            return
        if self.market_orchestrator.is_live:
            return
        paper_capital = self.account_orchestrator.fresh_paper_net_liquidation()
        # Fresh Paper net liquidation when there is one; the research scenario
        # figure otherwise.  The fallback is a *research* scenario and is not
        # promoted into Paper truth by this path -- the policy is unchanged, only
        # the source of the fallback moved to the state object that owns it.
        selection_capital = (
            paper_capital or self.research_scenario_capital.decimal_value
        )
        symbols = select_intraday_watchlist(
            scan,
            capital=selection_capital,
        )
        if symbols:
            self.market_orchestrator.set_subscription_symbols(
                symbols,
                note="实时订阅子集；不限制研究或交易范围",
            )
            self._log(
                f"已从 {len(scan.results):,} 个最近扫描结果中选出 "
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
        snapshot = self.market_orchestrator.snapshot
        readiness = calculate_quote_readiness_breakdown(
            snapshot if snapshot is not None else (),
            candidate_symbols=(
                row.symbol for row in self.auto_quant_candidates
            ),
            reference_symbols=self._auto_quant_market_reference_symbols(),
            recently_ready_symbols=(
                self.market_orchestrator.recently_ready_symbols()
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
            paper_capital=self.account_orchestrator.fresh_paper_net_liquidation(),
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
        # The selected strategy is what supplies the reference symbols, so this
        # is the moment they change; the market layer needs them for its
        # readiness card and may not look them up itself.
        self._publish_market_readiness_inputs()
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
            or self.paper_orchestrator.runtime_active
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
        universe = self.universe_orchestrator.snapshot
        if universe is None:
            QMessageBox.information(
                self,
                "缺少官方标的池",
                "请先在总览刷新官方标的池，再执行全市场扫描。",
            )
            return
        if self.paper_orchestrator.runtime_active:
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
        research_capital = self.research_scenario_capital.decimal_value

        def task(progress: Callable[[str], None]) -> MarketScan:
            progress(
                "自动量化第 1 步：扫描全部非中概研究池及已有合格日 K…"
            )
            result = scan_market(
                # Execution-time read: the same timing rule the scanner's
                # manual request uses.
                self.universe_orchestrator.snapshot,
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
        # The AutoQuant preparation path runs its own scan on purpose -- it is
        # wired into Paper PREPARING and failure cleanup -- and then hands the
        # finished fact to the capability that owns scan truth.  The direction
        # is AutoQuant -> Scanner: the scanner never learns Paper exists, and no
        # manual "扫描完成" line is written because nobody clicked 扫描.
        self.scanner_orchestrator.adopt_external_scan(result)
        universe = self.universe_orchestrator.snapshot
        scheduled = HistoryJobStore(self.queue_path).schedule(
            prioritized_research_symbols(universe, limit=None)
            if universe is not None
            else ()
        )
        self.history_orchestrator.render_current()
        if scheduled:
            self._log(
                f"全市场历史缺口已自动加入数据任务队列：新增 "
                f"{scheduled:,} 个；后续分批补齐后会自动扩大可评分覆盖。"
            )
        self._select_auto_quant_candidates()

    def _select_auto_quant_candidates(self) -> None:
        universe = self.universe_orchestrator.snapshot
        scan = self.scanner_orchestrator.scan
        if scan is None or universe is None:
            if self.paper_trading.phase() is PaperWorkflowPhase.PREPARING:
                self.paper_workflow.cancel_preparing()
            self._set_launch_busy(False)
            return
        limit = self.execution_page.candidate_limit()
        paper_capital = self.account_orchestrator.fresh_paper_net_liquidation()
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
            scan,
            universe,
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
            universe.summary()["research_eligible"]
        )
        self.execution_page.render_context(
            scope=(
                f"全市场入口：非中概研究池 {research_count:,} · "
                f"本轮有合格日 K 并完成评分 {len(scan.results):,} · "
                f"缺数据/不足200根 {len(scan.skipped):,} · "
                f"Paper 实时轮动候选 {len(symbols)}。"
            ),
            summary=(
                f"已从全市场扫描中整理 {len(symbols)} 个实时轮动候选。"
                "行情订阅只承担分钟信号，不代表扫描范围只有这些代码；"
                "全部订单仍未武装。"
            ),
        )
        stream_symbols = tuple(dict.fromkeys(symbols + market_references))
        self.market_orchestrator.set_subscription_symbols(stream_symbols)
        self._set_launch_busy(False)
        self._populate_auto_quant_candidates()
        if self.market_orchestrator.is_live:
            self.execution_page.render_context(
                summary=f"已整理 {len(symbols)} 个候选，正在安全停止旧行情并切换。"
            )
            self._request_market_switch(
                self.market_orchestrator.selected_provider() or "finnhub_trades"
            )
            return
        self.market_orchestrator.start()

    def _stop_auto_market_data(self) -> None:
        if self.paper_orchestrator.has_runtime_obligations:
            QMessageBox.information(
                self,
                "请先停止模拟下单",
                "当前 Paper 模拟下单会话仍可能有持仓或在途订单。"
                "请先点击“停止会话并请求平仓”，完成券商对账后"
                "才能停止行情。",
            )
            return
        if self._stop_market_data():
            self.execution_page.render_context(
                summary="当前行情已停止。可重新点击第 1 步准备新的候选。"
            )

    def _confirm_and_start_auto_quant(self) -> None:
        """Ask the operator, then hand the launch to the orchestrator.

        This is the one launch step that stays on the window, and deliberately so:
        it is *presentation* -- a modal confirmation and the arm flag it sets -- and
        ``PaperOrchestrator`` may not import ``QMessageBox``.  Every decision about
        whether the launch may proceed is the orchestrator's; this method only
        collects consent and forwards the request.
        """

        if self.paper_trading.phase() is PaperWorkflowPhase.CONNECTING:
            QMessageBox.information(
                self,
                DUPLICATE_TITLE,
                DUPLICATE_CONFIRM_MESSAGE,
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
        self.paper_orchestrator.start()

    def _report_paper_launch_refusal(self, title: str, message: str) -> None:
        """Surface one launch refusal the orchestrator published."""

        QMessageBox.warning(self, title, message)

    def _auto_quant_order_channel(self) -> PaperOrderChannel:
        """The order channel an attempt connects, composed from current settings.

        Read once per attempt by the orchestrator and frozen into its request, so a
        settings change mid-connect cannot redirect a launch the operator already
        confirmed.  The config is built here -- not in the capability -- because
        naming it reaches the IBKR connection module, which is this root's business.

        The order channel must always use a client id distinct from the read-only
        connection (P1-6), including when the configured id sits near the 999999 cap;
        the modulo keeps it in ``[0, 999999]``.
        """

        return PaperOrderChannel(
            config=IBKRConnectionConfig(
                host=self.preferences.ibkr_host,
                port=4002,
                client_id=(
                    (self.preferences.ibkr_client_id + 100) % 1_000_000
                ),
                api_read_only=False,
                paper_order_submission_enabled=True,
                connection_timeout_seconds=(
                    self.preferences.connection_timeout_seconds
                ),
            ),
            repository=self.order_repository,
            extended_hours_enabled=(
                self.preferences.extended_hours_paper_enabled
            ),
        )

    def _build_paper_session(
        self,
        request: PaperLaunchRequest,
        service: object,
        reading: PaperAccountReading,
    ) -> PaperSessionBuildResult:
        """The narrow session-build seam: compose the runtime over the borrowed channel.

        The composition root keeps every construction that names a concrete type --
        the auto-rotation config, the risk authority, the execution application, the
        trading runtime -- so ``PaperOrchestrator`` imports no adapter, no risk
        implementation and no execution implementation.  It receives an already
        validated reading, the frozen request and a *borrowed* candidate, and returns
        the ports publication binds plus the sizing ``arm`` needs.

        Two properties are load-bearing here and must not be relaxed:

        * **the capital chain stays ``Decimal``.**  ``resolve_paper_session_capital``
          bounds the session by *cash*, never by buying power, so no margin
          borrowing can enter through a float conversion;
        * **the execution application is bound to the same channel being armed.**  It
          is built over the borrowed candidate, so the engine cannot be handed an
          application talking to a different broker session than the coordinator
          reads.

        **Arming is deliberately not done here.**  This seam starts the runtime and
        reports the sizing it computed; ``PaperOrchestrator`` performs ``arm`` itself
        so that ``arm -> ensure -> publish -> promote`` is one explicit sequence in
        one place.  When the arm call lived inside this callable, the order was
        asserted only as ``build < ensure < publish``, and the real constraint --
        that arming precedes the promotability check and both precede publication --
        could not be locked down by a guard.
        """

        fact = request.strategy
        paper_capital = resolve_paper_session_capital(
            net_liquidation=reading.net_liquidation,
            cash=reading.cash,
            requested_limit=request.plan.requested_capital_limit,
        )
        config = build_auto_rotation_config(
            fact.parameters,
            initial_cash=Decimal(paper_capital),
            capital_source=(
                f"IBKR Paper {reading.account_alias} "
                f"现金约束；会话上限 {paper_capital}"
            ),
            daily_loss_limit=Decimal(paper_capital) * Decimal("0.01"),
        )
        # One risk authority, built from the configuration this window is actually
        # running with, and injected.  It used to be split: the account limits went
        # into ``ShadowConfig.layered_risk_limits`` while the engine read a separate
        # constructor argument that was never passed here, so the configured
        # ``risk_limits`` reached a field nobody read.
        risk = self._build_auto_quant_risk()
        execution = build_execution_application(
            repository=self.order_repository,
            broker=service,
        )
        runtime = build_trading_runtime(
            config=config,
            candidates=request.candidates,
            identity=fact.identity,
            risk=risk,
            execution=execution,
            market_reference_symbols=tuple(
                dict.fromkeys(
                    str(symbol).strip().upper()
                    for symbol in fact.parameters.get(
                        "market_reference_symbols", []
                    )
                    if str(symbol).strip()
                )
            ),
        )
        snapshot = runtime.start()
        assert snapshot.session_id is not None
        return PaperSessionBuildResult(
            engine=runtime,
            orders=service,
            session_id=snapshot.session_id,
            candidate_count=snapshot.candidate_count,
            max_order_notional=(
                Decimal(paper_capital) * config.max_position_fraction
            ),
        )

    def _populate_auto_quant_candidates(self) -> None:
        """Re-render the candidate and context surfaces from current facts.

        The page owns the candidate table's static-key optimisation; the window
        only supplies the candidates and the stream facts, and re-runs the two
        context lines that are not session state.
        """

        self._render_auto_quant_snapshot()
        self._refresh_auto_quant_preflight()
        self._refresh_extended_hours_status()

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

    def _on_paper_result_changed(self, result: PaperSessionResult) -> None:
        """Render one Paper result; the events were requested upstream.

        The window's one result handler, and deliberately thin: presentation only.  It
        repaints the route from the capability's retained snapshot and republishes the
        route's control state.

        Nothing here decides anything about the session, and since v2O-E4 nothing here
        *stores* anything about it either: the snapshot this route draws is
        ``paper_orchestrator.presentation``, which the capability refreshes from the same
        result just before it published it.  A second cache taken here is how the window
        becomes a second truth owner about a Paper session -- and how a page blanks out
        when the canonical result is cleared.

        Since v2O-E3 the *consequences* of a result -- whether a zero-state proof is due,
        and whether a finished session's ownership can be released -- are
        ``PaperOrchestrator._after_result``'s, so this handler no longer contains a second
        copy of either decision.  It must never reach the workflow: no ``on_stream``, no
        ``poll``, no ``set_entries_paused``, no ``request_stop``, and no risk, execution or
        broker mutation.  A guard pins that.
        """

        self._render_auto_quant_snapshot()
        self._apply_paper_workflow_button_state()

    def _confirm_paper_reconciliation_resume(self) -> None:
        """Ask the operator, then hand the confirmation to the capability.

        Pure presentation, and deliberately the same shape as the launch confirmation: a
        modal question and the answer, nothing else.  It reads no reconciliation evidence,
        stores no evidence id, reconnects nothing and moves no phase -- and when the
        answer is No it calls nothing at all, so the proof stays exactly where it was.

        Whether a proof is still *current* is decided by ``PaperOrchestrator`` when it
        re-reads the evidence after this returns, which is the only moment at which the
        answer is still true: the operator may take as long as they like to decide, and
        the broker may move in the meantime.
        """

        reply = QMessageBox.question(
            self,
            "Confirm Paper resume",
            "Manual reconciliation is complete. Resume the existing Paper session "
            "without resubmitting pending orders?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.paper_orchestrator.confirm_reconciliation_resume()

    def _on_paper_manual_recovery_required(self) -> None:
        """Paper can now only be continued by the operator: hand the client back.

        The capability saying "this session needs a human" is exactly the condition a
        refused close has to be undone for -- otherwise the gate that admits the
        reconciliation task is still down and the session can never be finalized.  This
        handler holds no Paper knowledge of its own: no phase, no evidence, no transition.
        It only undoes generic teardown, and it is idempotent because the capability
        re-announces the condition rather than diffing phases.
        """

        self._cancel_close_drain()

    def _on_paper_session_finalized(self) -> None:
        """Render a Paper session that has safely ended.

        Presentation only.  The disconnect, the workflow's own release gate and
        ``clear_active`` all already happened inside the capability, in that order, so
        nothing here may repeat any of them -- an ownership that is gone could only be
        "released" again by corrupting the record of what happened.
        """

        self.execution_page.render_execution_health(
            "执行对账：会话已安全结束，券商持仓和订单均已核对。"
        )
        self.execution_page.set_arm_confirmed(False)
        self._apply_paper_workflow_button_state()

    def _apply_paper_workflow_button_state(self) -> None:
        """Render every execution control from controller truth.

        One writer, one source: the publisher turns the phase into booleans and the page
        is handed those rather than a phase, so it cannot act on a lifecycle vocabulary it
        does not own.

        v2O-E3 took the refused-close hook out of here.  ``manual_recovery_required`` is
        the capability's own publication for that condition, so this method no longer
        reasons about Paper phases at all -- it only repaints.
        """

        self._publish_execution_controls()

    def _render_auto_quant_snapshot(self) -> None:
        """Hand the execution route the view it draws, and assemble none of it.

        What is left of the window's part in this render is *fetching*: the ambient route
        facts (quotes, the approved shortlist, the read-only account snapshot, the broker's
        own account reading) and the journal rows the order tables read.  Everything that
        used to make this method a read-model assembler moved in v2O-E4:

        * the session fact is ``paper_orchestrator.presentation`` -- the capability's
          retained, immutable projection -- not a cache the window took from a result;
        * which broker holdings belong to the session, how the pending orders are keyed,
          and which journal rows are the session's are pure conversions, and they live in
          ``pages/execution/projector.py`` beside the presenter that draws them.

        So this method no longer knows what a Paper session looks like; it knows which
        facts the route needs and where to ask for them.  A guard pins that it reads no
        phase and assembles no session view of its own.

        The candidate table is drawn first and on its own, because it has content before
        any session does: the operator approves a shortlist and only then arms it, so a
        route that waited for a session would show an empty table at exactly the moment
        the shortlist is the thing being approved.
        """

        if not hasattr(self, "execution_page"):
            return
        stream = self.market_orchestrator.snapshot
        quotes = {
            quote.symbol: quote
            for quote in (stream.quotes if stream is not None else ())
        }
        candidates = self.auto_quant_candidates
        self.execution_page.render_candidates(
            build_candidates_view(
                candidates=candidates,
                quotes=quotes,
                recently_ready=self.market_orchestrator.was_recently_ready,
            )
        )
        session = self.paper_orchestrator.presentation
        if session is None:
            return
        session_id = session.session_id
        self.execution_page.render(
            build_session_view(
                session=session,
                account=(
                    self.account_orchestrator.portfolio.account
                    if self.account_orchestrator.portfolio is not None
                    else None
                ),
                broker_state=self.paper_trading.broker_state(),
                quotes=quotes,
                candidates=candidates,
                reconciliations=(
                    self.order_repository.reconciliation_rows(
                        session_id=session_id,
                        limit=RECONCILIATION_ROW_LIMIT,
                    )
                    if session_id
                    else ()
                ),
                audit_rows=self.order_repository.audit_rows(limit=AUDIT_ROW_LIMIT),
                latency=self.paper_trading.reconciliation_rows_with_latency(
                    session_id=session_id,
                    limit=LATENCY_ROW_LIMIT,
                ),
                recently_ready=self.market_orchestrator.was_recently_ready,
            )
        )

    def _publish_execution_controls(self) -> None:
        """Publish the route's control state from the workflow truth.

        One writer, one source, and since v2O-E4 the *interpretation* of the source is
        the capability's: ``paper_orchestrator.session_control_facts`` answers which
        session controls the canonical phase makes available, so the window no longer
        compares phase values to decide what an operator may click.  The window still
        reads the canonical truth (through the capability, which reads the workflow and
        the order-service owner live) and still hands the page booleans rather than a
        phase, so the page cannot act on a lifecycle vocabulary it does not own.

        ``stop_stream_enabled`` is deliberately tied to the launch lock as well as to the
        stream: stopping the feed under a live Paper session would starve the strategy of
        the quotes its exit gates read, which is why the legacy builder disabled it when a
        session was armed.
        """

        if not hasattr(self, "execution_page"):
            return
        facts = self.paper_orchestrator.session_control_facts
        self.execution_page.set_control_state(
            control_state(
                launch_locked=self._launch_locked(),
                session_running=facts.running,
                session_paused=facts.paused,
                reconcile_available=facts.reconcile_available,
                resume_ready=facts.resume_ready,
                stream_running=self.market_orchestrator.is_live,
            )
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

        "A connection attempt is pending" is read off the workflow's phase now --
        canonically, and through the capability's own rule rather than by comparing the
        phase here: ``queries.launch_attempt_in_flight`` is the one definition of "an
        attempt owns the connect step", and it is equivalent to the retired
        ``_active_auto_launch_plan is not None`` while staying correct after publication.
        """

        return bool(
            self._launch_busy
            or self._channel_check_inflight
            or launch_attempt_in_flight(self.paper_trading.phase())
            or self.paper_trading.has_order_service()
            or self.paper_orchestrator.runtime_active
        )

    def _set_launch_busy(self, busy: bool) -> None:
        """Mark a local launch step in flight and republish the controls."""

        self._launch_busy = busy
        self._publish_execution_controls()


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

    def _publish_account_presentation_inputs(self) -> None:
        """Push the composed presentation facts to the account route.

        Two facts travel, for the same reason: the account route may not import
        where either comes from.

        * the exposure multiplier is a *risk* fact -- it comes from the
          substitution rules in the app config -- so the window hands over a
          frozen snapshot and the account route only ever learns
          "symbol -> presentation multiplier";
        * the research scenario capital is edited by the Cross Section page, and
          the account route must not learn that capability exists.  It is shown
          on this route purely as a scenario figure, never as broker equity.

        This is the *only* path that gives the Account page a scenario number,
        and it does so through the page's own ``render``: there is no separate
        capital paint path and no ``AccountPage.set_research_capital``.
        """

        self.account_orchestrator.set_presentation_inputs(
            AccountPresentationInputs.of(
                self._configured_exposure_multipliers(),
                research_capital=self.research_scenario_capital.value,
            )
        )

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

    # -- the cross-workflow account bridges -----------------------------
    #
    # These are composition-level fan-out, not compatibility wrappers.  The
    # account orchestrator owns the refresh, the ledger, the page and the
    # header facts; what it may *not* own is what the rest of the workbench
    # does when an account fact changes -- the dashboard card, the auto-quant
    # preflight and runtime view, and the targeted preflight are three other
    # capabilities.  So the orchestrator publishes ``portfolio_changed`` and
    # the window routes it.
    #
    # None of these bridges renders the account page, appends to the ledger or
    # asks for a refresh: that ownership moved, and a guard pins that.

    def _on_account_portfolio_changed(
        self, portfolio: BrokerAccountPortfolio
    ) -> None:
        """Fan one finished account fact out to its existing consumers.

        This preserves exactly the downstream side effects the retired
        ``_account_snapshot_finished`` performed, in the same order.  It adds
        no new behaviour: a readiness this round's account refresh did not
        already refresh is still not refreshed here.
        """

        self._publish_dashboard_view()
        # The route is repainted only when there is a session to paint.  The question is
        # "is there a retained presentation fact?", which is a *presentation* read and
        # deliberately nothing more: no account fact may be turned into a statement about
        # a Paper session by consulting a cache.
        if self.paper_orchestrator.presentation is not None:
            self._render_auto_quant_snapshot()
        self.targeted_session_orchestrator.refresh_preflight()
        self._refresh_auto_quant_preflight()

    def _render_account_shell_health(self, view: object) -> None:
        """Paint the shell header from the account layer's published facts.

        The badges belong to the whole workbench, so the orchestrator publishes
        text and state and the window paints them.  A field of ``None`` means
        "leave that badge alone": an account read only ever *promotes* the
        handshake badge, and a later failure must not demote it.

        This method reads nothing but the view: no portfolio, no page, no
        broker application.  The market badge is deliberately untouched --
        whether quotes are real-time is Market Data v2's answer to give.
        """

        if view.handshake_text is not None:
            self.handshake_badge.setText(view.handshake_text)
            self.handshake_badge.setProperty(
                "state", view.handshake_state
            )
        if view.handshake_tooltip is not None:
            self.handshake_badge.setToolTip(view.handshake_tooltip)
        if view.account_text is not None:
            self.account_badge.setText(view.account_text)
            self.account_badge.setProperty("state", view.account_state)
        self._repolish_health_badges()

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

        if hasattr(self, "backtest_orchestrator"):
            # The backtest option order is the capability's rule, not the
            # window's: this asks it to re-read the catalogue rather than
            # recomputing the projection here.
            self.backtest_orchestrator.refresh_strategy_options()
        if hasattr(self, "targeted_session_orchestrator"):
            # The Targeted combo's options and its selected version are the
            # capability's to publish: it is the only production caller of the
            # page's ``set_strategy_options``, so the page cannot be given a
            # selection the service would refuse.
            self.targeted_session_orchestrator.refresh_strategy_options()
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
        self.runtime_events_orchestrator.record(
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

    def _repolish_health_badges(self) -> None:
        for badge in (
            self.gateway_badge,
            self.handshake_badge,
            self.account_badge,
            self.market_badge,
        ):
            badge.style().unpolish(badge)
            badge.style().polish(badge)


    # -- the Settings composition bridges ---------------------------------
    #
    # Three thin slots, and each one exists because the fact it reads or the
    # action it takes belongs to another capability.  None of them decides
    # anything about Settings: the capability has already sequenced the save,
    # the transaction and the provider syncs by the time they run.

    def _on_market_provider_selected(self, *_args: object) -> None:
        """The market route's provider moved; mirror it into Settings, silently.

        A sync, not an intent: the Settings control is pointed at the route's
        provider without emitting, so the two combos cannot drive each other.
        The selected *API* provider deliberately does not follow here — the
        Settings combo is where the operator chooses which credential to edit —
        which is the asymmetry the retired handler had.
        """

        provider = str(
            self.market_orchestrator.selected_provider() or "finnhub_trades"
        )
        self.settings_orchestrator.adopt_market_provider(provider)

    def _on_market_switch_requested(self, provider: str) -> None:
        """Switch the feed after a *successful* settings save.

        The market route owns the switch, the Paper/Shadow interlock owns
        whether it may happen, and Settings owns neither -- it only says that
        the operator asked for this provider and that the preference behind it
        is now on disk.  A refusal never reaches here, which is the sequencing
        defect this round fixed.
        """

        if not self.market_orchestrator.subscription_symbols():
            self.market_orchestrator.set_selected_provider(provider)
            self._log(
                "默认行情源已切换；当前没有订阅代码。"
                "请在“监控台 → 行情监控”输入任意股票或 ETF 后启动行情。"
            )
            return
        self._request_market_switch(provider)

    def _on_settings_committed(self, commit: object) -> None:
        """Adopt one finished settings transaction, then fan it out.

        Composition and nothing else: the commit is already validated,
        persisted and applied by ``DesktopSettingsService``, so this method only
        adopts the two finished facts and repaints what depends on them.  It
        must not re-validate, re-commit, write a preference, touch a credential
        or build a ``SettingsPageView`` — every one of those is a second owner.
        """

        saved = commit.preferences
        self.config = commit.config
        self.preferences = saved
        self._apply_theme(saved.theme)
        # Silent: restoring a saved preference is not an operator intent on the
        # market route, so this must not re-publish a provider selection.  It
        # goes through the orchestrator, not the page, so the route's render
        # ownership stays intact.
        self.market_orchestrator.set_selected_provider(
            saved.market_provider
        )
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

    def _show_settings_information(self, title: str, message: str) -> None:
        """Show one informational message the Settings owner published."""

        QMessageBox.information(self, title, message)

    def _show_settings_warning(self, title: str, message: str) -> None:
        """Show one warning the Settings owner published."""

        QMessageBox.warning(self, title, message)

    def _confirm_paper_order_capability(
        self, title: str, message: str
    ) -> None:
        """Ask the operator to confirm enabling the Paper order capability.

        The wording is the capability's -- it is the safety explanation of what
        the flag does and does not authorise -- and the dialog is the window's,
        because Settings may not import a widget.  The answer goes straight back
        to the capability, which is the only thing that may revert the control.
        """

        answer = QMessageBox.warning(
            self,
            title,
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        self.settings_orchestrator.confirm_paper_order_capability(
            answer == QMessageBox.Yes
        )

    def _confirm_extended_hours_paper(
        self, title: str, message: str
    ) -> None:
        """Ask the operator to confirm enabling the extended-hours Paper mode."""

        answer = QMessageBox.warning(
            self,
            title,
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        self.settings_orchestrator.confirm_extended_hours(
            answer == QMessageBox.Yes
        )

    def _maybe_rotate_extended_ibkr_session(self) -> None:
        """Refresh the session context, then let the market layer decide.

        The rotation itself is the market orchestrator's: it owns the active
        source and the venue the adapter was built for.  It emits a request
        rather than switching, so the Paper interlock below still applies.
        """

        self._refresh_extended_hours_status()
        self.market_orchestrator.maybe_request_extended_session_rotation()

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
            self.runtime_events_orchestrator.record(
                severity="warning",
                component="minute_data",
                code="MINUTE_PERSIST_FAILED",
                message=str(error),
            )
            return
        # Fresh minute evidence for the current target means the session panel's
        # minute line is stale.  The target is read from the session capability's
        # snapshot -- the one canonical draft -- rather than from the editor, and
        # the refresh is the capability's own command: minute evidence is not an
        # evidence-routing decision, and this deliberately does not recompute the
        # preflight.  Only the minute line moved.
        target = self.targeted_session_orchestrator.snapshot.target_draft
        if target in symbols_to_record:
            self.targeted_session_orchestrator.refresh_minute_status(target)







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
            stop=self.market_orchestrator.stop_polling,
            is_running=lambda: self.market_orchestrator.polling_active,
            order=30,
        )
        # Order 100+: release the market data stream.  The market orchestrator
        # owns the worker; ``_stop_market_data`` is the window's thin bridge that
        # applies the Paper and Shadow interlocks before asking it to stop, and
        # returns False rather than raising, so the return value is mapped to a
        # join verdict for the supervisor.
        #
        # ``worker_running``, not ``is_live``: a stop that timed out already
        # makes ``is_live`` false while the network thread is still alive, so
        # asking the business fact here would report a clean release over a
        # thread that never exited.  ``is_live`` stays the answer for "is the
        # feed usable", which is what the business gates ask.
        supervisor.register(
            "market_data_stream",
            stop=self._stop_market_data,
            join=lambda: not self.market_orchestrator.worker_running,
            is_running=lambda: self.market_orchestrator.worker_running,
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

    def _running_workers(self) -> list[TaskThread]:
        return [worker for worker in self.workers if worker.isRunning()]

    def _request_worker_stops(self) -> None:
        """Ask the one cancellable task to stop.

        ``TaskThread`` has no generic cancel hook -- each task owns its own
        ``Event`` -- so the only universal signal is the universe refresh, which
        is the one long-running network task the desktop can interrupt.  The
        window does not read that event and does not reach into the capability
        for it: it calls the capability's shutdown lifecycle, which is a
        different intent from the operator pressing cancel and therefore does
        not write an operator status line.  The thread is never terminated: a
        half-written reference file is worse than a slow close.
        """

        self.universe_orchestrator.cancel_for_shutdown()

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
        # Paper's own shutdown decision belongs to the capability.  Which of the three
        # situations this close is in -- an automatic route still running, a session only
        # the operator can leave, or an ownership that cannot be shown to be released --
        # is answered by ``prepare_shutdown``, which also performs the release when it is
        # provably safe.  What stays here is the *presentation* of that verdict, the
        # generic admission gate and the generic runtime teardown below.
        paper_shutdown = self.paper_orchestrator.prepare_shutdown()
        if paper_shutdown.disposition is not PaperShutdownDisposition.READY:
            if (
                paper_shutdown.disposition
                is PaperShutdownDisposition.MANUAL_RECOVERY_REQUIRED
            ):
                # No automatic route is left, and every step out of it is a task: hand
                # the client back, or the gate would refuse the very recovery that can
                # finalize the session.  The capability announces the same condition
                # through ``manual_recovery_required``; this branch is the case where
                # the close itself is what discovered it.
                self._cancel_close_drain()
            # The other two keep the gate exactly as it is.  ``WAITING_FOR_FINALIZATION``
            # is an automatic route still running, so admitting work now would race it;
            # ``OWNERSHIP_BLOCKED`` means an ownership could not be accounted for, and
            # nothing here forces it, releases the lease for it or swallows the refusal
            # to make the process exit.
            event.ignore()
            QMessageBox.information(
                self,
                _PAPER_SHUTDOWN_TITLES[paper_shutdown.disposition],
                paper_shutdown.message,
            )
            return
        # The internal simulation is stopped through its own capability, which
        # is quieter than the operator's stop: no repaint, no event, no log, so a
        # close cannot write a "stopped" event over a session nobody stopped.
        self.shadow_orchestrator.shutdown()
        # Generic runtime teardown: stop new work, release the heartbeats and
        # the market data stream, join the workers, and keep going even if
        # one of them fails.  Trading-safety ordering above is unchanged --
        # the supervisor only runs after the Paper session is finalized.
        snapshot = self.runtime_supervisor.shutdown()
        self._report_runtime_shutdown(snapshot)
        # ``worker_running``, not ``is_live``: the question here is whether the
        # network thread has actually exited, and a stop that timed out has
        # already made the *feed* unavailable without ending the thread.  Asking
        # the business fact would let the application exit over a live worker.
        if self.market_orchestrator.worker_running:
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
            self.runtime_events_orchestrator.record(
                severity="warning",
                component="runtime",
                code="RUNTIME_SHUTDOWN_PARTIAL",
                message="；".join(messages),
            )
        else:
            self._log("运行期资源已全部释放。")


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
        universe = self.universe_orchestrator.snapshot
        universe_summary = (
            universe.summary() if universe is not None else {}
        )
        research_count = int(
            universe_summary.get("research_eligible", 0)
        )
        total_count = int(universe_summary.get("total", 0))
        history_count = self._local_history_symbol_count()
        scan = self.scanner_orchestrator.scan
        scanned_count = len(scan.results) if scan is not None else 0
        missing_count = len(scan.skipped) if scan is not None else 0
        scope = (
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
        # The scope line needs the universe and the scan, so the window builds
        # it; the orchestrator only draws it on the page it owns.
        self.market_orchestrator.set_scope(scope)
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
        on_finished: Callable[[], None] | None = None,
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
        # The count card is repainted through the capability that draws it.  The
        # task's lifecycle -- admission, registration, teardown -- is unchanged
        # and still entirely the window's.
        if hasattr(self, "runtime_events_orchestrator"):
            self.runtime_events_orchestrator.notify_task_count_changed()
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
            lambda: self._finish_task(worker, on_finished)
        )
        self._log(start_message)
        worker.start()
        return True

    def _finish_task(
        self,
        worker: TaskThread,
        on_finished: Callable[[], None] | None,
    ) -> None:
        """Release the worker, then let the capability update its own state.

        The order is the contract.  Generic lifecycle cleanup runs first, so the
        resource group is free and the execution controls are republished before
        anything repaints; the capability's ``on_finished`` hook runs second, so
        it observes a released worker rather than one still registered.

        The worker object is deliberately not passed to the hook.  A capability
        that received it could compare identity against the worker list, which is
        exactly the coupling ``on_finished`` exists to remove.
        """

        self._worker_finished(worker)
        if on_finished is not None:
            on_finished()

    def _worker_finished(self, worker: TaskThread) -> None:
        self.task_controller.finish(worker)
        if hasattr(self, "runtime_events_orchestrator"):
            self.runtime_events_orchestrator.notify_task_count_changed()
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
        self.runtime_events_orchestrator.record(
            severity="error",
            component="task",
            code="TASK_FAILED",
            message=message,
        )
        QMessageBox.warning(self, "任务失败", message)

    def _route_runtime_event(self, event: object) -> None:
        """Forward one capability's runtime event to its single owner.

        This is the *only* runtime-event adapter the window has.  Market,
        Account, Shadow, Paper and Targeted Evidence all publish the same four
        fields, so five identical per-capability handlers used to exist -- and a
        sixth caller would have been free to write the store directly.  This
        reads the fields and forwards them, and nothing else: it does not filter
        a severity, rewrite a code, redact a message, cache the event, decide
        whether to accept it or repaint.  The capability decided what happened,
        the store decides how it is persisted, and
        ``runtime_events_orchestrator`` decides when the page repaints.
        """

        self.runtime_events_orchestrator.record(
            severity=event.severity,
            component=event.component,
            code=event.code,
            message=event.message,
        )

    def _export_runtime_bundle(
        self, events: Sequence[RuntimeEvent]
    ) -> Path:
        """Gather the cross-capability facts one terminal export carries.

        This is the composition root's job, and deliberately not the
        capability's: the bundle is Account + Market + Strategy + Shadow +
        Targeted Evidence + the two Paper audit tables, so an orchestrator that
        gathered it would have to import five capabilities and would become the
        god object this round exists to avoid.  Only the events half is handed
        in -- the capability read them from its own store -- so the export and
        the screen can only ever describe the same rows.

        It is a provider and nothing more: it writes no runtime event, repaints
        no page, keeps no last-export fact, shows no dialog and schedules
        nothing.  It returns the artifact path; a refusal raises, and the
        capability sequences the failure.

        The evidence snapshot is read once into a local.  The export is a reader
        and nothing more: it neither selects a run, nor edits the evidence, nor
        triggers research.  Reading the capability's snapshot directly is also
        what keeps this method from becoming a second truth -- there are no
        ``self.targeted_*_results`` lists left for it to prefer over the owner.
        """

        evidence = self.targeted_evidence_orchestrator.snapshot
        return export_terminal_bundle(
            self.paths.exports_root,
            portfolio=self.account_orchestrator.portfolio,
            stream=self.market_orchestrator.snapshot,
            strategies=self.strategies.list_versions(),
            events=events,
            shadow_fills=self.shadow_orchestrator.recent_fills(500),
            targeted_replays=evidence.replay_results,
            targeted_robustness=evidence.robustness_results,
            targeted_walk_forward=evidence.walk_forward_results,
            targeted_overfit=evidence.overfit_results,
            targeted_data_quality=evidence.data_quality_results,
            targeted_execution_stress=(
                evidence.execution_stress_results
            ),
            targeted_review=evidence.review_results,
            paper_order_audit=(
                self.order_repository.audit_rows()
            ),
            paper_execution_audit=(
                self.order_repository.execution_rows()
            ),
        )

    def _show_runtime_information(self, title: str, message: str) -> None:
        """Show one informational message the Runtime Events owner published.

        The capability decides *what* the operator must be told -- an event was
        not selected, an export finished -- and the window, which is the only
        thing here with a widget, decides *how*.
        """

        QMessageBox.information(self, title, message)

    def _show_runtime_warning(self, title: str, message: str) -> None:
        """Show one warning the Runtime Events owner published."""

        QMessageBox.warning(self, title, message)

    def _show_runtime_export_succeeded(self, target: object) -> None:
        """Show the completion dialog for a finished terminal export."""

        self._show_runtime_information(
            "导出完成",
            f"已导出脱敏 CSV / JSON：\n{target}",
        )

    def _log(self, message: str) -> None:
        self.status_label.setText(message)

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
        if hasattr(self, "dashboard_page"):
            self.dashboard_page.set_palette(self.theme)

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
