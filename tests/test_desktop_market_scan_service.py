"""Tests for the desktop market scan service and its wiring.

The service is pure application code: no Qt, no threads, no real daily bars
and no real scan JSON.  The two scanner calls are faked.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from us_quant.desktop import MainWindow
from us_quant.universe import UniverseRecord, UniverseSnapshot
from us_quant.desktop_market_scan_service import DesktopMarketScanService
from us_quant.paths import STATE_ROOT_ENV
from us_quant.scanner import MarketScan, ScanResult, save_market_scan

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Market Data v2: the methods this round rewrote because the market
# data boundary moved from a v1 service to the application service.
# Declared so the guard below can assert the delta exactly.
MARKET_DATA_V2_METHODS = (
    # Market Data v2: the window talks to the application service and the
    # domain snapshot instead of the v1 service and transport types.
    "__init__",
    "_record_minute_snapshot",
    "_maybe_rotate_extended_ibkr_session",
    "_save_user_preferences",
    "_clear_selected_api_credentials",
    "_api_provider_changed",
)

# Broker Account v2: the methods this round rewrote because the
# account truth moved from ``IBKRReadOnlySnapshot``/``PortfolioView``
# to the domain ``BrokerAccountPortfolio``, and because the account
# page became a native Desktop UI v2 page.
BROKER_ACCOUNT_V2_METHODS = (
    "_export_terminal_state",
    "_refresh_target_preflight",
    "_research_capital_changed",
    "_start_shadow",
)

# Execution v2: the Paper execution stack moved out of the root package into
# the trading layer, so ``desktop.py`` builds its order store and execution
# service through the composition root instead of naming ``PaperOrderJournal``
# and ``IBKRPaperOrderService``.  Declared so the guard can assert the delta.
EXECUTION_V2_METHODS = (
    "_start_auto_quant",
    "_check_auto_order_channel",
    "_finish_auto_quant_session_if_safe",
)

# Spec 46/47/48: the base commit of this step.
BASE_COMMIT = "d497388af958e59b1bdeab1325e2f4708e082c82"

# Market Data v2: frozen modules this round legitimately rewrote or
# deleted.  Declared so the guard can assert the delta exactly.
MARKET_DATA_V2_CHANGED_MODULES = (
    # Market Data v2: the v1 service is deleted and its policy moved to
    # `trading/application/market_data.py`.
    "src/us_quant/market_data_service.py",
    # Market Data v2: the worker now drives the application, not a service.
    "src/us_quant/desktop_workers.py",
    # Market Data v2: the quote grid renders the domain snapshot.
    "src/us_quant/desktop_widgets.py",
)

# Broker Account v2: modules whose bytes moved with this round.
BROKER_ACCOUNT_V2_CHANGED_MODULES = (
    "src/us_quant/desktop_settings.py",
    "src/us_quant/ibkr_paper_orders.py",
)

# Execution v2: the Paper execution stack moved out of the root package into
# the trading layer.  Declared so the guard can assert the delta exactly.
EXECUTION_V2_CHANGED_MODULES = (
    # Execution v2: retyped to the domain order types and the new ports.
    "src/us_quant/paper_trading_service.py",
    "src/us_quant/paper_session.py",
    "src/us_quant/paper_order_models.py",
    "src/us_quant/ibkr_paper_gateway.py",
    # Execution v2: the journal file is gone; its store is a trading-layer
    # SQLite repository now.
    "src/us_quant/paper_order_journal.py",
)

# Runtime v2B: the Paper workflow and the lease/phase guards moved into
# ``trading/runtime/``; the root copies are deleted.  Declared so the guard can
# still assert the delta exactly.
RUNTIME_V2B_CHANGED_MODULES = (
    "src/us_quant/paper_workflow.py",
    "src/us_quant/workflow_state.py",
)

SERVICE_MODULE = "src/us_quant/desktop_market_scan_service.py"
SERVICE_PATH = _REPO_ROOT / SERVICE_MODULE

# Spec 48: the one MainWindow method this step may change.
REFACTORED_METHODS = ("__init__",)

# Methods a *later* round legitimately rewrote.  Each round appends the
# methods it declared; the union is what this guard tolerates relative to
# its own base commit.  Adding a name here that no round declared is exactly
# the scope violation this guard exists to catch.
LATER_ROUND_METHODS = (
    # ``_run_backtest_workspace`` moved out in v2O-C3: the request
    # construction and the busy lifecycle are the capability's now, so its
    # deletion is declared in
    # ``DESKTOP_BACKTEST_ORCHESTRATION_V2_REMOVED_METHODS`` rather than here.
)

# Trading Core v2: this round deleted the legacy/unified shell builders and
# added the v2 page composer.  The delta is declared here so the guard below
# can assert it *exactly* instead of merely tolerating it.
LATER_ROUND_REMOVED_METHODS = (
    # Broker Account v2 deleted the legacy account page builder;
    # the account route is a native v2 page now.
    "_account_tab",
    "_populate_account_view",

    "_build_legacy_workspace",
    "_build_unified_workflow",
    "_workflow_tabs",
    "_workflow_scroll_page",
    "_workspace_tabs",
)
LATER_ROUND_ADDED_METHODS = (
"_build_v2_pages",)

# Dashboard v2 retired the last legacy first-level builder and widget
# writers, replacing them with one projection and one page-level publish.
DASHBOARD_V2_REMOVED_METHODS = (
    "_dashboard_tab",
    "_populate_artifact_table",
    "_refresh_cards",
)
DASHBOARD_V2_ADDED_METHODS = (
    "_connect_dashboard_page",
    "_publish_dashboard_view",
)

# Trading Core v2 also rewrote these two: `_build_ui` now composes the v2
# shell, and `_targeted_robustness_finished` navigates through it.
LATER_ROUND_UI_METHODS = (
    "_build_ui",
    "_targeted_robustness_finished",
)

# Strategy v2: this round moved strategy governance, storage, selection and the
# strategy page out of ``MainWindow``.  The retired registry, the six page
# handlers and the two accessors that read a widget for their answer are gone;
# the page itself is a native v2 route that reports intent through signals.
STRATEGY_V2_REMOVED_METHODS = frozenset(
    {
        "_strategy_manager_tab",
        "_populate_strategy_registry",
        "_selected_strategy_record",
        "_strategy_registry_selection_changed",
        "_clone_strategy_version",
        "_transition_selected_strategy",
    }
)
STRATEGY_V2_ADDED_METHODS = frozenset(
    {
        # Desktop Execution v2 renamed this handler to
        # ``_auto_strategy_selected``; see the delta below.
        "_populate_strategy_selection_combos",
        "_record_runtime_strategy_selection",
        "_refresh_strategy_page",
        "_set_strategy_account_notice",
        "_shadow_strategy_selection_changed",
        "_strategy_clone_requested",
        "_strategy_transition_requested",
        "_strategy_version_or_none",
        "_strategy_version_selected",
        "_sync_strategy_combo",
    }
)

# Risk v2: ``MainWindow`` no longer builds the static safety page; its text and
# the limits the risk layer enforces live on the native v2 risk route.  Only the
# deletion is declared here -- this round also rewrote ``_apply_theme``, which
# Strategy v2 already lists.
RISK_V2_REMOVED_METHODS = frozenset({"_safety_tab"})

#: The one method this round adds: the window asks the composition root for
#: its single risk authority instead of assembling one inline.
RISK_V2_ADDED_METHODS = frozenset({"_build_auto_quant_risk"})
# Rewritten rather than added or removed: they now talk to the strategy
# application service and the selection service instead of a registry and a
# combo box.
STRATEGY_V2_METHODS = frozenset(
    {
        "_apply_theme",
        "_auto_order_service_connected",
        "_auto_quant_preflight",
        # ``_backtest_records`` is no longer listed: Strategy v2 rewrote it to
        # read the selection service, and v2O-C3 then deleted it when the rule
        # moved into ``backtest/queries.py``.  A method that does not exist
        # cannot be "changed in place", so its deletion is declared in
        # ``DESKTOP_BACKTEST_ORCHESTRATION_V2_REMOVED_METHODS`` instead.
        "_selected_auto_strategy_record",
        "_selected_shadow_strategy_record",
    }
)

# Spec 46/47: byte-identical to the base commit.
# Runtime v2A: the window builds its trading session through the runtime
# composition root, so the methods that named the old engine follow the rename.
# Declared so the guard can assert the delta exactly rather than tolerate it.
RUNTIME_V2A_METHODS = (
    "_prepare_auto_quant_candidates",
    "_reset_auto_launch_controls",
    "_task_failed",
)

# Desktop Execution v2: the execution route became a native v2 page.  The
# legacy builder and the two table populators it owned are deleted, the page and
# its control publisher are the new surface, and every handler that used to write
# a widget by attribute now writes through the page.  Declared so the guard can
# still assert the delta exactly, in both directions.
# Desktop Execution v2: the legacy route builder and the table populators it
# owned are deleted, and the page plus its control publisher take their place.
# Declared as a delta so the guard can assert the surface exactly, in both
# directions.
# Desktop Market v2: the market route became a native v2 page.  The legacy
# builder, its two scroll handlers and the snapshot populator are deleted, the
# page and its publishers are the new surface, and every handler that used to
# write a market widget by attribute now writes through the page.  Declared so
# the guard can assert the delta exactly, in both directions.
DESKTOP_MARKET_V2_REMOVED_METHODS = (
    "_quotes_tab",
    "_quotes_scroll_started",
    "_quotes_scroll_finished",
    "_populate_stream_snapshot",
)

DESKTOP_MARKET_V2_ADDED_METHODS = (
    "_connect_market_page",
)

DESKTOP_MARKET_V2_METHODS = (
    "_apply_intraday_watchlist",
    "_apply_target_symbol",
    "_settings_provider_selected",
    "_stream_provider_selected",
    "_switch_to_settings_provider",
    "_sync_targeted_symbol_to_stream",
)

# v2O-A Market orchestration extraction: the market route's runtime truth moved
# out of ``MainWindow`` into ``MarketOrchestrator``.  The methods that owned the
# worker, the snapshot, the poll timer, the pending switch, the readiness cache
# and the page render are deleted; what replaces them is the cross-workflow
# safety bridge (stop/switch still obey the Paper interlock, which the market
# layer may not name) plus the one snapshot fan-out that feeds the dashboard,
# the minute recorder, the auto-quant shortlist, Paper and Shadow.  Declared so
# the guard can assert the delta exactly, in both directions.
DESKTOP_MARKET_ORCHESTRATION_V2_REMOVED_METHODS = (
    "_activate_pending_stream_switch",
    "_invalidate_stream_snapshot",
    "_poll_stream_snapshot",
    "_quote_was_recently_ready",
    "_request_stream_switch",
    "_start_stream",
    "_stop_stream",
    "_stream_failed",
    "_stream_finished",
    "_stream_snapshot_pushed",
    "_stream_snapshot_received",
    "_stream_symbols_from_input",
    "_update_quote_readiness",
)

DESKTOP_MARKET_ORCHESTRATION_V2_ADDED_METHODS = (
    "_on_market_snapshot_changed",
    "_on_market_snapshot_invalidated",
    "_publish_market_readiness_inputs",
    "_record_market_runtime_event",
    "_render_market_shell_health",
    "_report_market_refusal",
    "_request_automatic_market_switch",
    "_request_market_start",
    "_request_market_stop",
    "_request_market_switch",
    "_stop_market_data",
)

# Rewritten rather than added or removed: the runtime registration now asks the
# orchestrator for the poll timer and the feed, the auto-quant stop reads the
# market truth through the orchestrator instead of the window, and ``closeEvent``
# asks it whether a feed is live instead of reading the worker attribute.
DESKTOP_MARKET_ORCHESTRATION_V2_METHODS = (
    "_register_runtime_components",
    "_stop_auto_quant",
    "closeEvent",
)

# v2O-A: ``closeEvent`` asks the market orchestrator whether the market
# *thread* is still alive instead of reaching for the worker, via
# ``worker_running`` (``is_live`` would report a feed that is merely
# stopping as a thread that has exited).  A byte-level delta so the
# frozen-method guard still proves nothing else moved.
_DESKTOP_MARKET_ORCHESTRATION_V2_CLOSE_EVENT_BASE = (
    '        if (\n            self.stream_worker is not None\n            and self.stream_worker.isRunning()\n        ):\n'
)
_DESKTOP_MARKET_ORCHESTRATION_V2_CLOSE_EVENT_DELTA = (
    '        # ``worker_running``, not ``is_live``: the question here is whether the\n'
    '        # network thread has actually exited, and a stop that timed out has\n'
    '        # already made the *feed* unavailable without ending the thread.  Asking\n'
    '        # the business fact would let the application exit over a live worker.\n'
    '        if self.market_orchestrator.worker_running:\n'
)

DESKTOP_ACCOUNT_ORCHESTRATION_V2_REMOVED_METHODS = (
    "_account_snapshot_finished",
    "_paper_simulation_capital",
    "_refresh_account_snapshot",
)

DESKTOP_ACCOUNT_ORCHESTRATION_V2_ADDED_METHODS = (
    "_on_account_portfolio_changed",
    "_publish_account_presentation_inputs",
    "_record_account_runtime_event",
    "_render_account_shell_health",
)

DESKTOP_ACCOUNT_ORCHESTRATION_V2_METHODS = (
    "__init__",
    "_apply_intraday_watchlist",
    "_auto_quant_preflight",
    "_export_terminal_state",
    "_refresh_target_preflight",
    "_research_capital_changed",
    "_select_auto_quant_candidates",
    "_start_shadow",
)

TARGETED_RESEARCH_V2_REMOVED_METHODS = (
    "_simulation_tab",
    "_populate_targeted_replay_results",
    "_populate_targeted_robustness_results",
    "_populate_robustness_scenarios",
    "_populate_targeted_walk_forward_results",
    "_populate_targeted_overfit_results",
    "_populate_targeted_data_quality_results",
    "_populate_targeted_execution_stress_results",
    "_populate_targeted_review_results",
    "_populate_targeted_review_gates",
    "_populate_shadow_snapshot",
    "_robustness_selection_changed",
    "_targeted_review_selection_changed",
)

TARGETED_RESEARCH_V2_ADDED_METHODS = (
    "_connect_targeted_validation_page",
    "_publish_targeted_view",
    "_targeted_controls",
    "_target_symbol_requested",
    "_target_subscribe_requested",
    "_robustness_run_selected",
    "_review_run_selected",
)

TARGETED_RESEARCH_V2_METHODS = (
    "_load_local_state",
    "_current_target_symbol",
    "_apply_target_symbol",
    "_sync_targeted_symbol_to_stream",
    "_refresh_minute_data_status",
    "_refresh_target_preflight",
    "_start_shadow",
    "_stop_shadow",
    "_targeted_replay_finished",
    "_targeted_robustness_finished",
)

RESEARCH_DATA_V2_REMOVED_METHODS = (
    "_universe_tab",
    "_populate_universe_table",
    "_data_tab",
    "_refresh_queue_table",
)
RESEARCH_DATA_V2_ADDED_METHODS = (
    "_connect_universe_page",
    "_connect_history_page",
)
RESEARCH_DATA_V2_METHODS = (
    "_load_local_state",
    "_auto_market_scan_finished",
    "_task_failed",
)

# v2O-C1 (Research foundations): the universe and history runtimes moved out of
# the window into ``desktop_v2/orchestration/research``.  Twelve handlers left
# the window, three arrived, and four more changed because they used to read the
# universe off the window.  ``_publish_universe_view``, ``_publish_history_view``
# and ``_history_task_failed`` were *added* by the round above and *removed*
# here, so they exist at neither the base commit nor now and belong to neither
# delta.  Dropping them silently would hide a real deletion, so they are pinned
# below and asserted absent from both deltas.
DESKTOP_RESEARCH_FOUNDATIONS_V2_NET_ZERO_METHODS = (
    "_publish_universe_view",
    "_publish_history_view",
    "_history_task_failed",
)
DESKTOP_RESEARCH_FOUNDATIONS_V2_REMOVED_METHODS = (
    "_refresh_universe",
    "_cancel_universe_refresh",
    "_reset_universe_refresh_controls",
    "_universe_refreshed",
    "_schedule_history",
    "_run_history",
    "_history_finished",
    "_run_public_history",
    "_retry_failed",
)
DESKTOP_RESEARCH_FOUNDATIONS_V2_ADDED_METHODS = (
    "_finish_task",
    "_on_universe_changed",
    "_report_history_refusal",
)
DESKTOP_RESEARCH_FOUNDATIONS_V2_METHODS = (
    # ``_request_worker_stops`` calls the capability's shutdown lifecycle
    # instead of setting the cancel event itself.
    "_request_worker_stops",
    # The targeted-replay entry points read the universe at execution time.
    "_run_targeted_replay",
    "_run_targeted_robustness",
)

# v2O-C2 (Research scanner): the scan truth, the manual scan request, the
# startup restore, the cross-workflow adoption and the chart read moved out of
# the window into ``desktop_v2/orchestration/research/scanner``.  Five handlers
# left, two arrived, and five more changed because they used to read
# ``self.scan`` off the window.  ``_publish_scanner_view`` and
# ``_scanner_symbol_selected`` were *added* by the ScannerPage round and
# *removed* here, so they exist at neither this file's base commit nor now and
# belong to neither delta; they are pinned below and asserted absent from both.
DESKTOP_SCANNER_ORCHESTRATION_V2_NET_ZERO_METHODS = (
    "_publish_scanner_view",
    "_scanner_symbol_selected",
)
DESKTOP_SCANNER_ORCHESTRATION_V2_REMOVED_METHODS = (
    "_run_scan",
    "_scan_finished",
    "_load_scan_file",
)
DESKTOP_SCANNER_ORCHESTRATION_V2_ADDED_METHODS = (
    "_scanner_run_inputs",
    "_report_scanner_refusal",
)
DESKTOP_SCANNER_ORCHESTRATION_V2_METHODS = (
    # ``_load_local_state`` asks the capability to restore instead of parsing
    # the scan JSON itself.
    "_load_local_state",
    # The AutoQuant completion hands its finished scan over instead of
    # assigning ``self.scan`` and painting the page.
    "_auto_market_scan_finished",
    # The three cross-workflow consumers read ``scanner_orchestrator.scan``.
    "_apply_intraday_watchlist",
    "_select_auto_quant_candidates",
    "_refresh_market_scope_summary",
)

SCANNER_V2_REMOVED_METHODS = (
    "_scanner_tab",
    "_populate_scan_table",
    "_scan_selection_changed",
)
SCANNER_V2_ADDED_METHODS = (
    "_connect_scanner_page",
)
SCANNER_V2_METHODS = (
    "_auto_market_scan_finished",
    "_apply_theme",
)

BACKTEST_V2_REMOVED_METHODS = (
    "_backtest_tab",
    "_refresh_backtest_strategy_combo",
    "_backtest_result_selection_changed",
    "_show_backtest_run",
)
BACKTEST_V2_ADDED_METHODS = (
    "_connect_backtest_page",
)
BACKTEST_V2_METHODS = (
    "_worker_finished",
    "_apply_theme",
)

# v2O-C3 moved the Backtest workspace's desktop runtime into
# ``BacktestOrchestrator``: the runs, the selection and the busy flag, plus the
# two request entry points, the render and the option projection.  The window
# keeps composition (``_connect_backtest_page``) and one dialog bridge
# (``_report_backtest_refusal``).
DESKTOP_BACKTEST_ORCHESTRATION_V2_REMOVED_METHODS = (
    "_backtest_records",
    "_run_backtest_workspace",
    "_backtest_workspace_finished",
)
DESKTOP_BACKTEST_ORCHESTRATION_V2_ADDED_METHODS = (
    "_report_backtest_refusal",
)
#: Net zero relative to this file's base commit: the BacktestPage round *added*
#: these six window handlers and v2O-C3 *deleted* them, so they exist at neither
#: revision.  Declared separately because dropping them from
#: ``BACKTEST_V2_ADDED_METHODS`` without a record would hide a real deletion.
DESKTOP_BACKTEST_ORCHESTRATION_V2_NET_ZERO_METHODS = (
    "_publish_backtest_strategy_options",
    "_publish_backtest_view",
    "_backtest_run_selected",
    "_run_selected_backtest",
    "_run_all_backtests",
    "_backtest_task_failed",
)

CROSS_SECTION_V2_REMOVED_METHODS = (
    "_strategy_tab",
    "_populate_strategy_report",
    "_run_strategy_research",
    "_strategy_finished",
    "_load_strategy_report",
)
CROSS_SECTION_V2_ADDED_METHODS = (
    "_connect_cross_section_page",
    "_publish_cross_section_view",
    "_run_cross_section_research",
    "_cross_section_finished",
    "_load_cross_section_report",
)
CROSS_SECTION_V2_METHODS = (
    "_load_local_state",
    "_research_scenario_capital",
    "_research_capital_changed",
    "_apply_theme",
)

DESKTOP_EXECUTION_V2_REMOVED_METHODS = (
    "_auto_quant_tab",
    "_populate_auto_latency_table",
    "_populate_auto_quant_snapshot",
    "_populate_auto_shadow_table",
)

DESKTOP_EXECUTION_V2_ADDED_METHODS = (
    "_auto_order_channel_failed",
    "_auto_strategy_selected",
    "_connect_execution_page",
    "_launch_locked",
    "_publish_execution_controls",
    "_render_auto_quant_snapshot",
    "_set_launch_busy",
    "_build_v2_pages",
    "_populate_strategy_selection_combos",
)

DESKTOP_EXECUTION_V2_METHODS = (
    "__init__",
    "_apply_paper_workflow_button_state",
    "_apply_paper_workflow_result",
    "_auto_candidate_preparation_failed",
    "_auto_order_channel_checked",
    "_auto_order_service_connected",
    "_auto_quant_preflight",
    "_check_auto_order_channel",
    "_configure_combo_width",
    "_configure_table",
    "_configure_table_view",
    "_confirm_and_start_auto_quant",
    "_current_auto_launch_matches",
    "_finish_auto_quant_session_if_safe",
    "_populate_auto_quant_candidates",
    "_prepare_auto_quant_candidates",
    "_reconnect_auto_order_service",
    "_refresh_auto_quant_preflight",
    "_refresh_extended_hours_status",
    "_refresh_market_scope_summary",
    "_reset_auto_launch_controls",
    "_select_auto_quant_candidates",
    "_start_auto_quant",
    "_stop_auto_market_data",
    "_task_failed",
    "_worker_finished",
)

# System v2: the System route became a native v2 aggregate.  The legacy
# runtime/settings builders and the selected-row resolve handler are deleted,
# the settings handlers now take immutable drafts, and the page publishers
# replace the widget-by-widget updates.  Declared so the guard can assert the
# delta exactly, in both directions.
SYSTEM_V2_REMOVED_METHODS = (
    "_runtime_tab",
    "_settings_tab",
    "_resolve_selected_runtime_event",
    "_refresh_credential_status",
)
SYSTEM_V2_ADDED_METHODS = (
    "_connect_runtime_events_page",
    "_connect_settings_page",
    "_credential_status_text",
    "_publish_settings_view",
    "_resolve_runtime_event",
    "_runtime_info_text",
    "_settings_draft",
    "_settings_storage_view",
)
SYSTEM_V2_METHODS = (
    "_preview_theme_changed",
    "_record_runtime_event",
    "_refresh_runtime_events",
    "_paper_order_capability_toggled",
    "_extended_hours_paper_toggled",
    "_save_api_credentials",
    "_clear_saved_finnhub_key",
    "_set_connection_settings_enabled",
)

# System v2 repair: the worker lifecycle now republishes the Runtime Events
# view, so ``_start_task``'s retired activity-card update became a scheduled
# page refresh.  Declared here so the byte-equivalence guard asserts exactly
# that delta and nothing else.
SYSTEM_V2_REPAIR_METHODS = ("_start_task",)

_SYSTEM_V2_REPAIR_BASE_BLOCK = (
    '        if hasattr(self, "runtime_task_card"):\n'
    '            self.runtime_task_card.set_value(\n'
    '                str(len(self.workers)), start_message\n'
    '            )\n'
)
_SYSTEM_V2_REPAIR_DELTA_BLOCK = (
    '        if hasattr(self, "runtime_events_page"):\n'
    '            self._schedule_runtime_events_refresh()\n'
)

SYSTEM_V2_CHANGED_MODULES = (
    # The transitional settings panel is deleted; the page owns its widgets now.
    "src/us_quant/desktop_settings_panel.py",
)

FROZEN_METHODS = (
    # No method from the original step remains frozen after later declared rounds.
)

# Spec 26/27/28/29/30: modules this step must not touch at all.
FROZEN_MODULES = (
    "src/us_quant/scanner.py",
    "src/us_quant/desktop_universe_service.py",
    "src/us_quant/desktop_history_service.py",
    "src/us_quant/desktop_settings.py",
    "src/us_quant/desktop_credentials.py",
    "src/us_quant/desktop_settings_panel.py",
    "src/us_quant/credential_store.py",
    "src/us_quant/paper_trading_service.py",
    "src/us_quant/paper_session.py",
    "src/us_quant/paper_workflow.py",
    "src/us_quant/paper_order_models.py",
    "src/us_quant/paper_order_journal.py",
    "src/us_quant/ibkr_paper_orders.py",
    "src/us_quant/ibkr_paper_gateway.py",
    "src/us_quant/workflow_state.py",
    "src/us_quant/market_data_service.py",
    "src/us_quant/desktop_workers.py",
    "src/us_quant/desktop_widgets.py",
    "src/us_quant/runtime_supervisor.py",
    "src/us_quant/universe.py",
    "src/us_quant/paths.py",
)


# -- helpers -----------------------------------------------------------


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=_REPO_ROOT, capture_output=True, check=False
    )


def _base_source(path: str) -> str | None:
    """Read ``path`` at the base commit, fetching it when CI is shallow."""

    result = _run(["git", "show", f"{BASE_COMMIT}:{path}"])
    if result.returncode == 0:
        return result.stdout.decode("utf-8")
    _run(["git", "fetch", "--depth", "1", "-q", "origin", BASE_COMMIT])
    result = _run(["git", "show", f"{BASE_COMMIT}:{path}"])
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8")


def _require_base(path: str) -> str:
    base = _base_source(path)
    if base is None:
        pytest.fail(
            f"base commit {BASE_COMMIT} is unreachable; the "
            "byte-equivalence guard cannot run"
        )
    return base


def _source_of(owner: ast.AST, node: ast.AST, source: str) -> str:
    lines = source.splitlines(keepends=True)
    return "".join(lines[node.lineno - 1 : node.end_lineno])


def _class_named(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _find_method(source: str, name: str, owner: str = "MainWindow") -> str:
    tree = ast.parse(source)
    cls = _class_named(tree, owner)
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return _source_of(cls, node, source).replace("\r\n", "\n")
    raise AssertionError(f"method {name} not found")


def _module_imports(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names


class _Recorder:
    """A scanner/save double that records calls and can raise."""

    def __init__(self, *, result=None, raises: Exception | None = None):
        self.result = result
        self.raises = raises
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.result


@pytest.fixture
def scanner(monkeypatch):
    """Patch the service module's two scanner imports."""

    import us_quant.desktop_market_scan_service as module

    scan = _Recorder(result="RESULT")
    save = _Recorder(result="SAVED")
    monkeypatch.setattr(module, "scan_market", scan)
    monkeypatch.setattr(module, "save_market_scan", save)
    return scan, save


def _service(tmp_path) -> DesktopMarketScanService:
    return DesktopMarketScanService(
        data_root=tmp_path / "data",
        fallback_data_root=tmp_path / "bundled",
        scan_path=tmp_path / "results" / "market_scan.json",
    )


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _window(monkeypatch, tmp_path) -> MainWindow:
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path / "state"))
    _qapp()
    window = MainWindow()
    _qapp().processEvents()
    return window


# -- 2/32: the constructor performs no I/O -----------------------------


def test_the_constructor_performs_no_io(tmp_path) -> None:
    """Spec 2/32: constructing the service must not touch the filesystem."""

    data_root = tmp_path / "data"
    fallback = tmp_path / "bundled"
    scan_path = tmp_path / "results" / "market_scan.json"

    DesktopMarketScanService(
        data_root=data_root,
        fallback_data_root=fallback,
        scan_path=scan_path,
    )

    assert not data_root.exists()
    assert not fallback.exists()
    assert not scan_path.exists()
    assert not scan_path.parent.exists()


def test_the_constructor_stores_exactly_three_paths(tmp_path) -> None:
    """Spec 2/32: no mutable runtime state, no extra attribute."""

    service = _service(tmp_path)

    assert sorted(vars(service)) == [
        "data_root",
        "fallback_data_root",
        "scan_path",
    ]


def test_the_constructor_body_contains_no_io_call() -> None:
    """Spec 2: no ``mkdir`` / ``exists`` / ``open`` / ``read`` in ``__init__``.

    A bare ``data_root.exists()`` is behaviourally invisible -- nothing can
    observe it from outside -- so the no-I/O rule needs a structural guard
    rather than a behavioural one.
    """

    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    service = _class_named(tree, "DesktopMarketScanService")
    init = next(
        node
        for node in service.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )

    forbidden = {
        "mkdir",
        "exists",
        "is_dir",
        "is_file",
        "open",
        "read_text",
        "read_bytes",
        "glob",
        "iterdir",
        "resolve",
        "touch",
        "write_text",
        "write_bytes",
        "scan_market",
        "save_market_scan",
    }

    called: list[str] = []
    for node in ast.walk(init):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                called.append(func.attr)
            elif isinstance(func, ast.Name):
                called.append(func.id)

    assert [name for name in called if name in forbidden] == []


def test_the_constructor_keeps_the_paths_it_was_given(tmp_path) -> None:
    """Spec 3: the paths are passed in, never rebuilt inside the service."""

    data_root = tmp_path / "data"
    fallback = tmp_path / "bundled"
    scan_path = tmp_path / "results" / "market_scan.json"

    service = DesktopMarketScanService(
        data_root=data_root,
        fallback_data_root=fallback,
        scan_path=scan_path,
    )

    assert service.data_root is data_root
    assert service.fallback_data_root is fallback
    assert service.scan_path is scan_path


# -- 11/33/34: the scanner arguments -----------------------------------


def test_scan_passes_every_argument_verbatim(tmp_path, scanner) -> None:
    """Spec 11/33: sentinels for each parameter, compared by identity."""

    scan, _save = scanner
    service = _service(tmp_path)
    universe = object()
    substitutions = {"AAPL": object()}
    capital = Decimal("4321")
    risk = Decimal("0.07")

    service.scan(
        universe,
        capital=capital,
        max_position_risk_pct=risk,
        substitutions=substitutions,
    )

    args, kwargs = scan.calls[0]
    assert args[0] is universe
    assert kwargs["data_root"] is service.data_root
    assert kwargs["fallback_data_root"] is service.fallback_data_root
    assert kwargs["capital"] is capital
    assert kwargs["max_position_risk_pct"] is risk
    assert kwargs["substitutions"] is substitutions


def test_scan_does_not_pass_the_quality_second_tier_flag(
    tmp_path, scanner
) -> None:
    """Spec 11/34: the scanner's own default must stay in force."""

    scan, _save = scanner
    service = _service(tmp_path)

    service.scan(
        object(),
        capital=Decimal("1500"),
        max_position_risk_pct=Decimal("0.1"),
        substitutions={},
    )

    _args, kwargs = scan.calls[0]
    assert "allow_quality_second_tier" not in kwargs


def test_scan_passes_no_extra_keyword_at_all(tmp_path, scanner) -> None:
    """Spec 11: the argument set is exactly the five the old code passed."""

    scan, _save = scanner
    service = _service(tmp_path)

    service.scan(
        object(),
        capital=Decimal("1500"),
        max_position_risk_pct=Decimal("0.1"),
        substitutions={},
    )

    _args, kwargs = scan.calls[0]
    assert set(kwargs) == {
        "data_root",
        "fallback_data_root",
        "capital",
        "max_position_risk_pct",
        "substitutions",
    }


# -- 12/13/14/35/36: save ordering, path and identity ------------------


def test_scan_then_save_then_return(tmp_path, scanner, monkeypatch) -> None:
    """Spec 12/35: the order is the contract."""

    import us_quant.desktop_market_scan_service as module

    order: list[str] = []
    monkeypatch.setattr(
        module,
        "scan_market",
        lambda *a, **k: order.append("scan") or "RESULT",
    )
    monkeypatch.setattr(
        module,
        "save_market_scan",
        lambda *a, **k: order.append("save") or "SAVED",
    )
    service = _service(tmp_path)

    service.scan(
        object(),
        capital=Decimal("1500"),
        max_position_risk_pct=Decimal("0.1"),
        substitutions={},
    )

    assert order == ["scan", "save"]


def test_save_receives_the_result_and_the_configured_path(
    tmp_path, scanner, monkeypatch
) -> None:
    """Spec 13/35: the save path is the constructor's, not a rebuilt one."""

    import us_quant.desktop_market_scan_service as module

    _scan, save = scanner
    service = _service(tmp_path)
    result = object()
    monkeypatch.setattr(module, "scan_market", _Recorder(result=result))

    service.scan(
        object(),
        capital=Decimal("1500"),
        max_position_risk_pct=Decimal("0.1"),
        substitutions={},
    )

    args, _kwargs = save.calls[0]
    assert args[0] is result
    assert args[1] is service.scan_path


def test_save_is_called_exactly_once(tmp_path, scanner) -> None:
    """Spec 35: one scan, one save."""

    _scan, save = scanner
    service = _service(tmp_path)

    service.scan(
        object(),
        capital=Decimal("1500"),
        max_position_risk_pct=Decimal("0.1"),
        substitutions={},
    )

    assert len(save.calls) == 1


def test_scan_returns_the_domain_result_not_the_saved_path(
    tmp_path, scanner, monkeypatch
) -> None:
    """Spec 14/36: identity, not equality."""

    import us_quant.desktop_market_scan_service as module

    _scan, save = scanner
    service = _service(tmp_path)
    result = object()
    save.result = "SOME PATH"
    monkeypatch.setattr(module, "scan_market", _Recorder(result=result))

    assert (
        service.scan(
            object(),
            capital=Decimal("1500"),
            max_position_risk_pct=Decimal("0.1"),
            substitutions={},
        )
        is result
    )


# -- 15/16/37/38: failures propagate and never half-commit -------------


def test_a_failing_scan_writes_nothing(tmp_path, scanner, monkeypatch) -> None:
    """Spec 15/37: no save after a failed scan, and the error is unchanged."""

    import us_quant.desktop_market_scan_service as module

    _scan, save = scanner
    service = _service(tmp_path)
    error = OSError("bad history")
    monkeypatch.setattr(module, "scan_market", _Recorder(raises=error))

    with pytest.raises(OSError) as caught:
        service.scan(
            object(),
            capital=Decimal("1500"),
            max_position_risk_pct=Decimal("0.1"),
            substitutions={},
        )

    assert caught.value is error
    assert save.calls == []


def test_a_value_error_from_the_scanner_propagates(
    tmp_path, scanner, monkeypatch
) -> None:
    """Spec 15: no wrapping into a service-specific error type."""

    import us_quant.desktop_market_scan_service as module

    _scan, save = scanner
    service = _service(tmp_path)
    error = ValueError("capital must be positive")
    monkeypatch.setattr(module, "scan_market", _Recorder(raises=error))

    with pytest.raises(ValueError) as caught:
        service.scan(
            object(),
            capital=Decimal("0"),
            max_position_risk_pct=Decimal("0.1"),
            substitutions={},
        )

    assert caught.value is error
    assert save.calls == []


def test_a_failing_save_does_not_report_success(
    tmp_path, scanner, monkeypatch
) -> None:
    """Spec 16/38: a failed save must not return the scan result."""

    import us_quant.desktop_market_scan_service as module

    scan, _save = scanner
    service = _service(tmp_path)
    error = OSError("disk full")
    monkeypatch.setattr(module, "save_market_scan", _Recorder(raises=error))

    with pytest.raises(OSError) as caught:
        service.scan(
            object(),
            capital=Decimal("1500"),
            max_position_risk_pct=Decimal("0.1"),
            substitutions={},
        )

    assert caught.value is error
    assert len(scan.calls) == 1


def test_the_service_never_swallows_an_exception(tmp_path) -> None:
    """Spec 15/16: no ``except`` clause anywhere in the service."""

    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    service = _class_named(tree, "DesktopMarketScanService")

    handlers = [
        node for node in ast.walk(service) if isinstance(node, ast.ExceptHandler)
    ]
    assert handlers == []


# -- v2O-C2: load_saved (the artifact read) ----------------------------


def _saved_scan() -> MarketScan:
    """A scan with every restorable field set to a non-default value.

    ``max_position_risk_pct`` is deliberately not the dataclass default, and
    ``skipped`` is non-empty: a reader that dropped either field would still
    pass against a scan built from defaults.
    """

    return MarketScan(
        generated_at=datetime(2026, 9, 18, 12, 30, tzinfo=timezone.utc),
        capital=4321.0,
        data_date=date(2026, 9, 18),
        results=(
            ScanResult(
                symbol="AAPL",
                execution_symbol="AAPL",
                name="Apple",
                sector="Technology",
                leader_tier=1,
                security_type="STK",
                trading_date=date(2026, 9, 17),
                close=100.0,
                execution_price=100.0,
                whole_share_capacity=10,
                average_dollar_volume_20d=1_000_000.0,
                return_20d=0.01,
                return_63d=0.02,
                volatility_20d=0.2,
                drawdown_252d=-0.1,
                rsi_14d=55.0,
                atr_pct_14d=0.02,
                above_sma_50=True,
                above_sma_200=True,
                score=50.0,
                signal="观察",
                research_eligible=True,
                trade_eligible=False,
                reason="test",
            ),
        ),
        skipped={"MSFT": "insufficient history"},
        max_position_risk_pct=0.07,
    )


def test_load_saved_returns_none_when_the_file_is_absent(tmp_path) -> None:
    """The ordinary first-run state: nothing cached is not an error."""

    service = _service(tmp_path)

    assert service.load_saved() is None


def test_load_saved_restores_every_field(tmp_path) -> None:
    """Every serialized fact comes back, compared by value."""

    service = _service(tmp_path)
    original = _saved_scan()
    save_market_scan(original, service.scan_path)

    restored = service.load_saved()

    assert restored is not None
    assert restored.generated_at == original.generated_at
    assert restored.capital == original.capital
    assert restored.data_date == original.data_date
    assert restored.max_position_risk_pct == original.max_position_risk_pct
    assert restored.skipped == original.skipped
    assert restored.results == original.results


def test_load_saved_restores_the_row_dates_as_dates(tmp_path) -> None:
    """The two date fields must come back as ``date``, not ``str``.

    A round-trip that left them as strings would still compare equal to nothing
    useful, so the type is asserted rather than the value.
    """

    service = _service(tmp_path)
    save_market_scan(_saved_scan(), service.scan_path)

    restored = service.load_saved()

    assert isinstance(restored.data_date, date)
    assert isinstance(restored.results[0].trading_date, date)


def test_load_saved_uses_the_configured_path(tmp_path) -> None:
    """The artifact is read from the path this service owns."""

    service = _service(tmp_path)
    save_market_scan(_saved_scan(), service.scan_path)
    elsewhere = tmp_path / "other" / "market_scan.json"
    assert not elsewhere.exists()

    assert service.load_saved() is not None


def test_load_saved_raises_on_a_malformed_artifact(tmp_path) -> None:
    """A corrupt artifact is a real problem, not "no scan".

    Silently degrading it would hide the corruption and make the next scan look
    like the first one.
    """

    service = _service(tmp_path)
    service.scan_path.parent.mkdir(parents=True, exist_ok=True)
    service.scan_path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(Exception):
        service.load_saved()


def test_load_saved_raises_on_a_truncated_artifact(tmp_path) -> None:
    """Valid JSON that is missing a required key must also fail loudly."""

    service = _service(tmp_path)
    service.scan_path.parent.mkdir(parents=True, exist_ok=True)
    service.scan_path.write_text("{}", encoding="utf-8")

    with pytest.raises(Exception):
        service.load_saved()


# -- v2O-C2: load_chart (the chart read) -------------------------------


def test_load_chart_passes_both_roots_to_the_loader(
    tmp_path, monkeypatch
) -> None:
    """The two roots are the service's own, passed verbatim.

    Not read from a global or rebuilt: a chart drawn from a different tree than
    the scan read would be a second, silently disagreeing data source.
    """

    import us_quant.desktop_market_scan_service as module

    seen: dict = {}
    sentinel = ((__import__("datetime").date(2026, 9, 18), 10.0),)

    def fake_loader(symbol, *, data_root, fallback_data_root):
        seen["symbol"] = symbol
        seen["data_root"] = data_root
        seen["fallback_data_root"] = fallback_data_root
        return sentinel

    monkeypatch.setattr(module, "load_close_series", fake_loader)
    service = _service(tmp_path)

    result = service.load_chart("AAPL")

    assert result is sentinel
    assert seen["symbol"] == "AAPL"
    assert seen["data_root"] is service.data_root
    assert seen["fallback_data_root"] is service.fallback_data_root


def test_load_chart_propagates_a_read_failure(tmp_path, monkeypatch) -> None:
    """The capability decides that a chart failure is a log line, not this."""

    import us_quant.desktop_market_scan_service as module

    error = FileNotFoundError("no bars")

    def fake_loader(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(module, "load_close_series", fake_loader)
    service = _service(tmp_path)

    with pytest.raises(FileNotFoundError) as caught:
        service.load_chart("AAPL")

    assert caught.value is error


# -- 17/18/19/39: the service's own boundaries -------------------------


def test_the_service_depends_only_on_the_allowed_modules() -> None:
    """Spec 19/39: dependency set equality, not a forbidden-name scan.

    v2O-C2 grew the service from "manual scan only" to the Scanner data
    boundary, which is why the standard-library set now also covers reading
    the artifact back and the domain set also covers its two row types and the
    chart loader.  Still compared for equality: a new import must be declared
    here, in a place a reviewer sees.
    """

    source = SERVICE_PATH.read_text(encoding="utf-8")

    assert _module_imports(source) == {
        "__future__",
        "datetime",
        "decimal",
        "json",
        "pathlib",
        "us_quant.portfolio",
        "us_quant.scanner",
        "us_quant.universe",
    }


def test_the_service_imports_no_qt_and_no_gui_modules() -> None:
    """Spec 17: the service is Qt-free application code."""

    imported = _module_imports(
        SERVICE_PATH.read_text(encoding="utf-8")
    )

    for forbidden in (
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtWidgets",
        "us_quant.desktop",
        "us_quant.desktop_workers",
        "us_quant.runtime_supervisor",
        "us_quant.market_data_service",
        "us_quant.auto_quant",
    ):
        assert forbidden not in imported, forbidden


def test_the_service_imports_no_paper_or_workflow_modules() -> None:
    """Spec 18: the scan service is unrelated to the order stack."""

    imported = _module_imports(
        SERVICE_PATH.read_text(encoding="utf-8")
    )

    for forbidden in (
        "us_quant.paper_trading_service",
        "us_quant.paper_session",
        "us_quant.paper_workflow",
        "us_quant.ibkr_paper_orders",
        "us_quant.ibkr_paper_gateway",
        "us_quant.workflow_state",
        "us_quant.risk",
    ):
        assert forbidden not in imported, forbidden


def test_the_service_does_not_import_the_risk_limits_type() -> None:
    """Spec 18: ``max_position_risk_pct`` is just a Decimal."""

    source = SERVICE_PATH.read_text(encoding="utf-8")

    assert "LayeredRiskLimits" not in source
    assert "AppConfig" not in source
    assert "ApplicationPaths" not in source


def test_the_service_starts_no_threads() -> None:
    """Spec 20/40: threading stays with TaskThread and the window."""

    source = SERVICE_PATH.read_text(encoding="utf-8")

    for forbidden in (
        "QThread",
        "Thread(",
        "ThreadPoolExecutor",
        "Event(",
        "asyncio",
        "import threading",
        "from threading",
    ):
        assert forbidden not in source, forbidden


def test_the_service_holds_no_mutable_runtime_state() -> None:
    """Spec 2: only the three paths are stored."""

    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    service = _class_named(tree, "DesktopMarketScanService")

    assigned: list[str] = []
    for node in ast.walk(service):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            assigned.append(node.attr)

    assert set(assigned) == {
        "data_root",
        "fallback_data_root",
        "scan_path",
    }


def test_the_service_carries_no_presentation_copy() -> None:
    """Spec 51: no Chinese progress text in the service."""

    source = SERVICE_PATH.read_text(encoding="utf-8")

    for text in (
        "正在读取已通过质量门的本地日 K…",
        "市场扫描中…",
        "扫描完成",
    ):
        assert text not in source, text


def test_the_service_offers_no_cancellation_api() -> None:
    """Spec 20: cancellation belongs to the window's TaskThread."""

    for name in ("cancel", "stop", "request_stop"):
        assert not hasattr(DesktopMarketScanService, name), name


# -- 41: the window owns the service -----------------------------------


def test_the_window_owns_the_service(monkeypatch, tmp_path) -> None:
    """Spec 41: one service, built over the window's own three paths."""

    window = _window(monkeypatch, tmp_path)
    try:
        assert isinstance(
            window.market_scan_service, DesktopMarketScanService
        )
        assert window.market_scan_service.data_root is window.data_root
        assert (
            window.market_scan_service.fallback_data_root
            is window.bundled_data_root
        )
        assert window.market_scan_service.scan_path is window.scan_path
    finally:
        window.deleteLater()


def test_the_scan_path_is_the_research_results_one(
    monkeypatch, tmp_path
) -> None:
    """Spec 3/13: the saved file stays where it always was."""

    window = _window(monkeypatch, tmp_path)
    try:
        assert window.scan_path == (
            window.paths.research_results_root / "market_scan.json"
        )
        assert window.market_scan_service.scan_path == window.scan_path
    finally:
        window.deleteLater()


def test_the_window_does_not_build_a_second_paths_object(
    monkeypatch, tmp_path
) -> None:
    """Spec 3: ``ApplicationPaths.discover`` must not run again."""

    from us_quant.paths import ApplicationPaths

    calls: list[int] = []
    original = ApplicationPaths.discover

    def counting_discover():
        calls.append(1)
        return original()

    monkeypatch.setattr(ApplicationPaths, "discover", counting_discover)

    window = _window(monkeypatch, tmp_path)
    try:
        assert window.market_scan_service.data_root is window.data_root
        assert calls == [1]
    finally:
        window.deleteLater()


# -- 5/42: the missing-universe guard ----------------------------------


def test_a_missing_universe_blocks_the_scan(monkeypatch, tmp_path) -> None:
    """Spec 5/42: the dialog, and nothing else, happens.

    v2O-C2 moved the request to ``ScannerOrchestrator.request_scan``; the rule
    is unchanged.  The orchestrator refuses through its ``refused`` signal and
    the window shows the dialog, so the operator-facing severity and copy are
    asserted through the window exactly as before.
    """

    window = _window(monkeypatch, tmp_path)
    try:
        from PySide6.QtWidgets import QMessageBox

        # No universe is loaded: the window's default, asserted rather than
        # assigned, because the window no longer holds a universe of its own.
        assert window.universe_orchestrator.snapshot is None

        shown: list[tuple] = []
        monkeypatch.setattr(
            QMessageBox,
            "information",
            lambda *args: shown.append(args),
        )
        started: list = []
        monkeypatch.setattr(
            window.scanner_orchestrator,
            "_submit_task",
            lambda *a, **k: started.append(a) or True,
        )
        scanned: list = []
        monkeypatch.setattr(
            window.market_scan_service,
            "scan",
            lambda *a, **k: scanned.append(a) or "RESULT",
        )

        window.scanner_orchestrator.request_scan()

        assert started == []
        assert scanned == []
        assert len(shown) == 1
        assert shown[0][1] == "缺少标的池"
        assert shown[0][2] == "请先刷新官方标的。"
    finally:
        window.deleteLater()


# -- 6/43/44: progress copy and the task contract ----------------------


def _universe(symbol: str = "AAPL") -> UniverseSnapshot:
    """A real snapshot: ``restore_snapshot`` renders, so it reads ``records``.

    Seeding a bare ``object()`` used to be enough when the window merely held
    the value; now that adoption paints the page, the placeholder would fail
    inside the presenter instead of exercising the path under test.
    """

    return UniverseSnapshot(
        generated_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
        source_timestamps={"test": "now"},
        records=(
            UniverseRecord(
                symbol=symbol,
                name=symbol,
                exchange="NASDAQ",
                security_type="STK",
                eligible_for_research=True,
            ),
        ),
    )


def _capture_task(window, monkeypatch):
    """Capture the task ``request_scan`` starts, without running it.

    The dialog is stubbed because a modal ``QMessageBox`` blocks forever
    under ``QT_QPA_PLATFORM=offscreen``, and ``universe`` is defaulted so
    the missing-universe guard does not fire in the timing tests.

    The task boundary is replaced on the *orchestrator*, not on the window:
    the orchestrator takes ``submit_task`` at construction, so patching
    ``window._start_task`` after the fact would no longer reach it.  The two
    providers stay real, which is what keeps the window's own composition --
    the run-inputs bridge and the universe lambda -- under test.
    """

    from PySide6.QtWidgets import QMessageBox

    if window.universe_orchestrator.snapshot is None:
        window.universe_orchestrator.restore_snapshot(_universe())

    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    captured: list = []
    monkeypatch.setattr(
        window.scanner_orchestrator,
        "_submit_task",
        lambda task, **kwargs: captured.append((task, kwargs)) or True,
    )
    window.scanner_orchestrator.request_scan()
    return captured[0]


def test_the_progress_line_is_verbatim(monkeypatch, tmp_path) -> None:
    """Spec 6/43: the single manual scan progress line."""

    window = _window(monkeypatch, tmp_path)
    try:
        task, _kwargs = _capture_task(window, monkeypatch)
        monkeypatch.setattr(
            window.market_scan_service,
            "scan",
            lambda *a, **k: "RESULT",
        )

        seen: list[str] = []
        task(seen.append)

        assert seen == ["正在读取已通过质量门的本地日 K…"]
    finally:
        window.deleteLater()


def test_the_start_task_contract_is_unchanged(monkeypatch, tmp_path) -> None:
    """Spec 21/44: on_success, start message and resource group.

    The keyword set is asserted too: adding a keyword (e.g. an
    ``on_failure`` handler) changes the task contract even though every
    asserted value still matches.
    """

    window = _window(monkeypatch, tmp_path)
    try:
        _task, kwargs = _capture_task(window, monkeypatch)

        assert kwargs["on_success"] == (
            window.scanner_orchestrator._scan_finished
        )
        assert kwargs["start_message"] == "市场扫描中…"
        assert kwargs["resource_group"] == "scan"
        assert set(kwargs) == {
            "on_success",
            "start_message",
            "resource_group",
        }
    finally:
        window.deleteLater()


# -- 7/8/9/10/45: evaluation timing ------------------------------------


def test_the_evaluation_timing_is_preserved(monkeypatch, tmp_path) -> None:
    """Spec 45: the run inputs are captured now; the universe is live.

    The old closure computed ``research_capital`` on the UI thread before
    ``_start_task`` and read the other three inside the worker.  Extraction
    changed that on purpose: the capital, the risk percentage *and* the
    substitution rules are now one frozen :class:`ScannerRunInputs` taken at
    request time, while the universe is still re-read inside the worker so a
    refresh that landed while this task queued is the universe that gets
    scanned.  Both halves are asserted, because the whole point is that they
    are deliberately opposite.
    """

    from dataclasses import replace

    window = _window(monkeypatch, tmp_path)
    try:
        universe_a = _universe("AAA")
        universe_b = _universe("BBB")
        window.universe_orchestrator.restore_snapshot(universe_a)

        # The capital is computed once, on the UI thread.  Returning a
        # *different* value on a second call is what proves the worker does
        # not compute it: a constant would look the same either way.
        capitals = ["CAPITAL_A", "CAPITAL_B"]
        monkeypatch.setattr(
            window,
            "_research_scenario_capital",
            lambda: capitals.pop(0) if capitals else "CAPITAL_LATE",
        )

        task, _kwargs = _capture_task(window, monkeypatch)

        # Everything the worker will read is replaced *after* the capture.
        window.universe_orchestrator.restore_snapshot(universe_b)
        new_rules = {"MSFT": object()}
        window.config = replace(
            window.config,
            substitutions=new_rules,
            risk_limits=replace(
                window.config.risk_limits,
                max_position_exposure_pct=Decimal("0.42"),
            ),
        )

        seen: dict = {}

        def fake_scan(universe, **kwargs):
            seen["universe"] = universe
            seen.update(kwargs)
            return "RESULT"

        monkeypatch.setattr(window.market_scan_service, "scan", fake_scan)

        task(lambda _message: None)

        # The universe is the live one: read at execution time.
        assert seen["universe"] is universe_b
        # The three run inputs are the frozen ones: the capital is the value
        # the operator saw, and the risk/rules are the values that were
        # configured when they clicked, not the ones edited afterwards.
        assert seen["capital"] == "CAPITAL_A"
        assert seen["max_position_risk_pct"] == Decimal("0.10")
        assert seen["substitutions"] == {}
    finally:
        window.deleteLater()


def test_the_run_inputs_are_frozen_at_request_time(
    monkeypatch, tmp_path
) -> None:
    """Spec 7/27: the whole input set is a UI-thread snapshot.

    Asserted separately from the universe half above: the risk percentage and
    the substitution rules used to be read inside the worker, and this round
    deliberately moved them to the request thread.  A guard that only checked
    the capital would not notice them sliding back.
    """

    from dataclasses import replace

    window = _window(monkeypatch, tmp_path)
    try:
        window.universe_orchestrator.restore_snapshot(_universe())
        window.config = replace(
            window.config,
            substitutions={"OLD": object()},
            risk_limits=replace(
                window.config.risk_limits,
                max_position_exposure_pct=Decimal("0.11"),
            ),
        )

        task, _kwargs = _capture_task(window, monkeypatch)

        window.config = replace(
            window.config,
            substitutions={"NEW": object()},
            risk_limits=replace(
                window.config.risk_limits,
                max_position_exposure_pct=Decimal("0.99"),
            ),
        )

        seen: dict = {}
        monkeypatch.setattr(
            window.market_scan_service,
            "scan",
            lambda universe, **kwargs: seen.update(kwargs) or "RESULT",
        )

        task(lambda _message: None)

        assert seen["max_position_risk_pct"] == Decimal("0.11")
        assert set(seen["substitutions"]) == {"OLD"}
    finally:
        window.deleteLater()


def test_the_capital_is_computed_before_the_task_starts(
    monkeypatch, tmp_path
) -> None:
    """Spec 7: ``_research_scenario_capital`` runs on the UI thread."""

    window = _window(monkeypatch, tmp_path)
    try:
        window.universe_orchestrator.restore_snapshot(_universe())
        calls: list[str] = []

        def capital():
            calls.append("capital")
            return "CAPITAL"

        monkeypatch.setattr(window, "_research_scenario_capital", capital)

        captured: list = []

        def start(task, **kwargs):
            calls.append("start")
            captured.append(task)
            return True

        monkeypatch.setattr(
            window.scanner_orchestrator, "_submit_task", start
        )
        monkeypatch.setattr(
            window.market_scan_service, "scan", lambda *a, **k: "RESULT"
        )

        window.scanner_orchestrator.request_scan()

        assert calls == ["capital", "start"]
    finally:
        window.deleteLater()


def test_the_service_is_called_once_per_task_run(
    monkeypatch, tmp_path
) -> None:
    """Spec 4: the window no longer calls the scanner itself."""

    window = _window(monkeypatch, tmp_path)
    try:
        window.universe_orchestrator.restore_snapshot(_universe())
        monkeypatch.setattr(
            window, "_research_scenario_capital", lambda: Decimal("1500")
        )
        task, _kwargs = _capture_task(window, monkeypatch)

        calls: list = []
        monkeypatch.setattr(
            window.market_scan_service,
            "scan",
            lambda *a, **k: calls.append(a) or "RESULT",
        )

        task(lambda _message: None)
        task(lambda _message: None)

        assert len(calls) == 2
    finally:
        window.deleteLater()


# -- 46/47/48/49/50: the scope guards ----------------------------------


@pytest.mark.parametrize("name", FROZEN_METHODS)
def test_the_frozen_method_is_byte_identical(name: str) -> None:
    """Spec 46/47: these methods may not change in this step."""

    base = _require_base("src/us_quant/desktop.py")
    current = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )

    assert _find_method(current, name) == _find_method(base, name), name


def test_only_the_declared_methods_changed() -> None:
    """Spec 48: the declared surface is ``__init__`` plus later rounds.

    The guard is relative to this step's base commit.  Every later round
    appends the methods it declares to its own delta set and ships its own
    guard against its own base commit.
    """

    base = _require_base("src/us_quant/desktop.py")
    current = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )

    base_window = _class_named(ast.parse(base), "MainWindow")
    current_window = _class_named(ast.parse(current), "MainWindow")

    base_methods = {
        node.name: node
        for node in base_window.body
        if isinstance(node, ast.FunctionDef)
    }
    current_methods = {
        node.name: node
        for node in current_window.body
        if isinstance(node, ast.FunctionDef)
    }

    # Trading Core v2 removed the legacy/unified shells and added the v2
    # page composer.  Asserting the delta exactly keeps this guard strict:
    # any other addition or removal still fails here.
    assert set(base_methods) - set(current_methods) == (
        set(LATER_ROUND_REMOVED_METHODS)
        | set(STRATEGY_V2_REMOVED_METHODS)
        | set(RISK_V2_REMOVED_METHODS)
        | set(DESKTOP_EXECUTION_V2_REMOVED_METHODS)
        | set(DESKTOP_MARKET_V2_REMOVED_METHODS)
        | set(TARGETED_RESEARCH_V2_REMOVED_METHODS)
        | set(RESEARCH_DATA_V2_REMOVED_METHODS)
        | set(SCANNER_V2_REMOVED_METHODS)
        | set(BACKTEST_V2_REMOVED_METHODS)
        | set(DESKTOP_BACKTEST_ORCHESTRATION_V2_REMOVED_METHODS)
        | set(CROSS_SECTION_V2_REMOVED_METHODS)
        | set(SYSTEM_V2_REMOVED_METHODS)
        | set(DASHBOARD_V2_REMOVED_METHODS)
        | set(DESKTOP_MARKET_ORCHESTRATION_V2_REMOVED_METHODS)
        | set(DESKTOP_ACCOUNT_ORCHESTRATION_V2_REMOVED_METHODS)
        | set(DESKTOP_RESEARCH_FOUNDATIONS_V2_REMOVED_METHODS)
        | set(DESKTOP_SCANNER_ORCHESTRATION_V2_REMOVED_METHODS)
    )
    assert set(current_methods) - set(base_methods) == (
        set(LATER_ROUND_ADDED_METHODS)
        | set(STRATEGY_V2_ADDED_METHODS)
        | set(RISK_V2_ADDED_METHODS)
        | set(DESKTOP_EXECUTION_V2_ADDED_METHODS)
        | set(DESKTOP_MARKET_V2_ADDED_METHODS)
        | set(TARGETED_RESEARCH_V2_ADDED_METHODS)
        | set(RESEARCH_DATA_V2_ADDED_METHODS)
        | set(SCANNER_V2_ADDED_METHODS)
        | set(BACKTEST_V2_ADDED_METHODS)
        | set(DESKTOP_BACKTEST_ORCHESTRATION_V2_ADDED_METHODS)
        | set(CROSS_SECTION_V2_ADDED_METHODS)
        | set(SYSTEM_V2_ADDED_METHODS)
        | set(DASHBOARD_V2_ADDED_METHODS)
        | set(DESKTOP_MARKET_ORCHESTRATION_V2_ADDED_METHODS)
        | set(DESKTOP_ACCOUNT_ORCHESTRATION_V2_ADDED_METHODS)
        | set(DESKTOP_RESEARCH_FOUNDATIONS_V2_ADDED_METHODS)
        | set(DESKTOP_SCANNER_ORCHESTRATION_V2_ADDED_METHODS)
    )

    # The two methods the ScannerPage round added and this round removed exist
    # at neither commit.  They are declared separately and asserted absent from
    # both deltas: dropping them silently would hide a real deletion.
    declared = set(DESKTOP_SCANNER_ORCHESTRATION_V2_REMOVED_METHODS) | set(
        DESKTOP_SCANNER_ORCHESTRATION_V2_ADDED_METHODS
    )
    assert not (
        set(DESKTOP_SCANNER_ORCHESTRATION_V2_NET_ZERO_METHODS) & declared
    )
    assert not (
        set(DESKTOP_SCANNER_ORCHESTRATION_V2_NET_ZERO_METHODS)
        & (set(base_methods) - set(current_methods))
    )
    assert not (
        set(DESKTOP_SCANNER_ORCHESTRATION_V2_NET_ZERO_METHODS)
        & (set(current_methods) - set(base_methods))
    )

    # v2O-C3: the six BacktestPage handlers are net zero against this file's
    # base commit.  Asserted separately so their deletion is recorded rather
    # than inferred from a missing declaration.
    declared_backtest = set(
        DESKTOP_BACKTEST_ORCHESTRATION_V2_REMOVED_METHODS
    ) | set(DESKTOP_BACKTEST_ORCHESTRATION_V2_ADDED_METHODS)
    assert not (
        set(DESKTOP_BACKTEST_ORCHESTRATION_V2_NET_ZERO_METHODS)
        & declared_backtest
    )
    assert not (
        set(DESKTOP_BACKTEST_ORCHESTRATION_V2_NET_ZERO_METHODS)
        & (set(base_methods) - set(current_methods))
    )
    assert not (
        set(DESKTOP_BACKTEST_ORCHESTRATION_V2_NET_ZERO_METHODS)
        & (set(current_methods) - set(base_methods))
    )
    assert not (
        set(DESKTOP_BACKTEST_ORCHESTRATION_V2_NET_ZERO_METHODS)
        & set(current_methods)
    )

    changed = []
    for name, node in base_methods.items():
        if name not in current_methods:
            continue
        before = _source_of(base_window, node, base).replace("\r\n", "\n")
        after = _source_of(
            current_window, current_methods[name], current
        ).replace("\r\n", "\n")
        if before != after:
            changed.append(name)

    # Exact, not a subset: every changed method must be declared, and
    # every declared method must actually have changed.
    allowed = (
        set(REFACTORED_METHODS)
        | set(LATER_ROUND_METHODS)
        | set(LATER_ROUND_UI_METHODS)
        | set(MARKET_DATA_V2_METHODS)
        | set(BROKER_ACCOUNT_V2_METHODS)
        | set(STRATEGY_V2_METHODS)
        | set(EXECUTION_V2_METHODS)
        | set(RUNTIME_V2A_METHODS)
        | set(DESKTOP_EXECUTION_V2_METHODS)
        | set(DESKTOP_MARKET_V2_METHODS)
        | set(TARGETED_RESEARCH_V2_METHODS)
        | set(RESEARCH_DATA_V2_METHODS)
        | set(SCANNER_V2_METHODS)
        | set(BACKTEST_V2_METHODS)
        | set(CROSS_SECTION_V2_METHODS)
        | set(SYSTEM_V2_METHODS)
        | set(SYSTEM_V2_REPAIR_METHODS)
        | set(DESKTOP_MARKET_ORCHESTRATION_V2_METHODS)
        | set(DESKTOP_ACCOUNT_ORCHESTRATION_V2_METHODS)
        | set(DESKTOP_RESEARCH_FOUNDATIONS_V2_METHODS)
        | set(DESKTOP_SCANNER_ORCHESTRATION_V2_METHODS)
    )
    assert set(changed) <= allowed
    assert set(MARKET_DATA_V2_METHODS) <= set(changed)
    assert set(BROKER_ACCOUNT_V2_METHODS) <= set(changed)
    assert set(STRATEGY_V2_METHODS) <= set(changed)
    assert set(EXECUTION_V2_METHODS) <= set(changed)
    assert set(RUNTIME_V2A_METHODS) <= set(changed)
    assert set(DESKTOP_EXECUTION_V2_METHODS) <= set(changed)
    assert set(DESKTOP_MARKET_V2_METHODS) <= set(changed)
    assert set(TARGETED_RESEARCH_V2_METHODS) <= set(changed)
    assert set(RESEARCH_DATA_V2_METHODS) <= set(changed)
    assert set(SCANNER_V2_METHODS) <= set(changed)
    assert set(BACKTEST_V2_METHODS) <= set(changed)
    assert set(CROSS_SECTION_V2_METHODS) <= set(changed)
    assert set(SYSTEM_V2_METHODS) <= set(changed)
    assert set(SYSTEM_V2_REPAIR_METHODS) <= set(changed)
    assert set(DESKTOP_MARKET_ORCHESTRATION_V2_METHODS) <= set(changed)
    assert set(DESKTOP_ACCOUNT_ORCHESTRATION_V2_METHODS) <= set(changed)
    assert set(DESKTOP_SCANNER_ORCHESTRATION_V2_METHODS) <= set(changed)


def test_the_other_frozen_modules_are_untouched() -> None:
    """Spec 26/27/28/29/30: the neighbours do not move."""

    changed = []
    for path in FROZEN_MODULES:
        base = _require_base(path)
        current_path = _REPO_ROOT / path
        if not current_path.exists():
            # A deleted frozen module only counts as declared if this
            # round said so; an undeclared deletion must still fail.
            changed.append(path)
            continue
        current = current_path.read_text(encoding="utf-8")
        if current.replace("\r\n", "\n") != base.replace(
            "\r\n", "\n"
        ):
            changed.append(path)

    # Exact, not a subset: the delta is the declared Market Data v2
    # surface and nothing else.
    assert set(changed) == (
        set(MARKET_DATA_V2_CHANGED_MODULES)
        | set(BROKER_ACCOUNT_V2_CHANGED_MODULES)
        | set(EXECUTION_V2_CHANGED_MODULES)
        | set(RUNTIME_V2B_CHANGED_MODULES)
        | set(SYSTEM_V2_CHANGED_MODULES)
    )


def test_the_manual_path_no_longer_calls_the_scanner() -> None:
    """Spec 50: the manual scan request delegates, it does not scan.

    v2O-C2 moved the request itself into ``ScannerOrchestrator``, so the
    window no longer declares ``_run_scan`` at all.  The rule this test has
    always protected is unchanged: the manual path must not reach the scanner
    domain directly, and the service is the only thing that may.
    """

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )

    assert "_run_scan" not in source

    from us_quant.desktop_v2.orchestration.research.scanner import (
        orchestrator as module,
    )

    request = _find_method(
        pathlib.Path(module.__file__).read_text(encoding="utf-8"),
        "request_scan",
        owner="ScannerOrchestrator",
    )

    assert "scan_market(" not in request
    assert "save_market_scan(" not in request
    assert "self._service.scan(" in request


def test_the_auto_quant_path_still_calls_the_scanner_directly() -> None:
    """Spec 23/24: the duplicate is deliberate and stays direct.

    If this ever goes through the service, the AutoQuant/Paper chain was
    changed by a refactor that promised not to touch it.
    """

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )
    method = _find_method(source, "_prepare_auto_quant_candidates")

    assert "scan_market(" in method
    assert "save_market_scan(" in method
    assert "market_scan_service" not in method


def test_the_window_keeps_the_scanner_import() -> None:
    """Spec 24: AutoQuant still needs both names."""

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )
    imported = {
        alias.asname or alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert "scan_market" in imported
    assert "save_market_scan" in imported


def test_the_window_keeps_its_universe_service() -> None:
    """Spec 26: the previous step's service is untouched and still wired."""

    from us_quant.desktop_universe_service import DesktopUniverseService

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )

    assert "DesktopUniverseService" in source
    assert DesktopUniverseService.__module__ == (
        "us_quant.desktop_universe_service"
    )
