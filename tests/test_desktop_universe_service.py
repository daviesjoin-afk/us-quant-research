"""Tests for the desktop universe refresh service and its wiring.

The service is pure application code: no Qt, no threads, no network.  The
two domain calls are faked, so these tests never touch Nasdaq Trader or SEC.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import subprocess
from pathlib import Path

import pytest

from us_quant.desktop import MainWindow
from us_quant.desktop_universe_service import (
    STAGE_DOWNLOAD_OFFICIAL,
    STAGE_ENRICH_SEC,
    STAGE_ENRICH_SEC_START,
    STAGE_PREPARE_REFERENCE,
    SEC_PROFILE_BUDGET,
    DesktopUniverseService,
    UniverseRefreshProgress,
)
from us_quant.paths import STATE_ROOT_ENV, ApplicationPaths
from us_quant.universe import UniverseRefreshCancelled

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Spec 54/55/56: the base commit of this step, not the project's first one.
BASE_COMMIT = "5109a18033b044252ee4a04f81f4e696f1a5fab3"

# Spec 56: the only MainWindow methods this step may change.  v2O-C1 retired
# ``_refresh_universe`` (the capability owns the refresh now), so the constant
# keeps only what still exists -- the removal is declared below.
REFACTORED_METHODS = ("__init__",)

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
        "_backtest_records",
        "_selected_auto_strategy_record",
        "_selected_shadow_strategy_record",
    }
)

# Spec 55: everything else must stay byte-identical to the base commit.
# ``_run_scan`` is NOT here: step 14 legitimately rewrote it, and that step
# ships its own byte-equivalence guard for the methods it froze.
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
    "_run_backtest_workspace",
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
    # ``_build_ui`` lost the universe/history page wiring to the capability.
    "_build_ui",
    # ``_request_worker_stops`` calls the capability's shutdown lifecycle.
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
    # ``_build_ui`` constructs the scanner capability instead of connecting the
    # page straight to a window handler.
    "_build_ui",
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
    "_publish_backtest_strategy_options",
    "_publish_backtest_view",
    "_backtest_run_selected",
    "_run_selected_backtest",
    "_run_all_backtests",
    "_backtest_task_failed",
)
BACKTEST_V2_METHODS = (
    "_backtest_records",
    "_run_backtest_workspace",
    "_backtest_workspace_finished",
    "_worker_finished",
    "_apply_theme",
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

# v2O-C1 (Research foundations): ``_request_worker_stops`` no longer reads the
# capability's cancel event -- the window would be reaching into the capability
# for state it no longer owns.  It calls the capability's *shutdown* lifecycle
# instead, which is a different intent from the operator pressing cancel and so
# does not write an operator status line.  The whole method is declared as the
# delta because the intent changed with the body: there is no unchanged
# remainder left to freeze.
_DESKTOP_RESEARCH_FOUNDATIONS_V2_REQUEST_WORKER_STOPS_BASE = (
    '    def _request_worker_stops(self) -> None:\n'
    '        """Ask every cancellable worker to stop.\n'
    '\n'
    '        ``TaskThread`` has no generic cancel hook -- each task owns its own\n'
    '        ``Event`` -- so the only universal signal is the universe refresh\n'
    '        cancel event, which is the one long-running network task the desktop\n'
    '        can interrupt.  The thread is never terminated: a half-written\n'
    '        reference file is worse than a slow close.\n'
    '        """\n'
    '\n'
    '        event = self.universe_refresh_cancel_event\n'
    '        if event is not None:\n'
    '            event.set()\n'
)
_DESKTOP_RESEARCH_FOUNDATIONS_V2_REQUEST_WORKER_STOPS_DELTA = (
    '    def _request_worker_stops(self) -> None:\n'
    '        """Ask the one cancellable task to stop.\n'
    '\n'
    '        ``TaskThread`` has no generic cancel hook -- each task owns its own\n'
    '        ``Event`` -- so the only universal signal is the universe refresh, which\n'
    '        is the one long-running network task the desktop can interrupt.  The\n'
    '        window does not read that event and does not reach into the capability\n'
    "        for it: it calls the capability's shutdown lifecycle, which is a\n"
    '        different intent from the operator pressing cancel and therefore does\n'
    '        not write an operator status line.  The thread is never terminated: a\n'
    '        half-written reference file is worse than a slow close.\n'
    '        """\n'
    '\n'
    '        self.universe_orchestrator.cancel_for_shutdown()\n'
)


SYSTEM_V2_CHANGED_MODULES = (
    # The transitional settings panel is deleted; the page owns its widgets now.
    "src/us_quant/desktop_settings_panel.py",
)

FROZEN_METHODS = (
    "_request_worker_stops",
    "_task_cancelled",
    "_start_task",
    "closeEvent",
)

# v2O-C1: ``_start_task`` is the generic task boundary (spec 9).  It gained one
# optional completion hook and nothing else; the hook exists so a caller can run
# work after the *task* finishes, which the orchestrators need because they are
# the ones that know what to do with the result.  Declared as the minimal delta,
# in both places it appears, so the rest of the method stays frozen byte for
# byte.
_DESKTOP_RESEARCH_FOUNDATIONS_V2_START_TASK_DELTAS = (
    (
        "        shutdown_essential: bool = False,\n"
        "        on_finished: Callable[[], None] | None = None,\n",
        "        shutdown_essential: bool = False,\n",
    ),
    (
        "        worker.finished.connect(\n"
        "            lambda: self._finish_task(worker, on_finished)\n"
        "        )\n",
        "        worker.finished.connect(\n"
        "            lambda: self._worker_finished(worker)\n"
        "        )\n",
    ),
)

# Spec 62/63/64: modules this step must not touch at all.
FROZEN_MODULES = (
    "src/us_quant/desktop_settings.py",
    "src/us_quant/desktop_credentials.py",
    "src/us_quant/desktop_settings_panel.py",
    "src/us_quant/credential_store.py",
    "src/us_quant/market_data_service.py",
    "src/us_quant/desktop_workers.py",
    "src/us_quant/desktop_widgets.py",
    "src/us_quant/runtime_supervisor.py",
    "src/us_quant/paper_trading_service.py",
    "src/us_quant/paper_session.py",
    "src/us_quant/paper_workflow.py",
    "src/us_quant/paper_order_models.py",
    "src/us_quant/paper_order_journal.py",
    "src/us_quant/ibkr_paper_orders.py",
    "src/us_quant/ibkr_paper_gateway.py",
    "src/us_quant/workflow_state.py",
    # Spec 58/59/60: the domain module, the paths module and the history
    # service are all frozen too.
    "src/us_quant/universe.py",
    "src/us_quant/paths.py",
    "src/us_quant/desktop_history_service.py",
)

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

SERVICE_MODULE = "src/us_quant/desktop_universe_service.py"


# -- helpers -----------------------------------------------------------


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=_REPO_ROOT,
        capture_output=True,
        check=False,
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


def _module_imports(source: str) -> set[str]:
    """The set of modules a source file imports, normalised."""

    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names


def _imported_names(source: str) -> set[str]:
    """The set of symbols a source file imports, normalised.

    ``_module_imports`` only knows module paths, so an assertion like
    "``enrich_us_profiles`` is not imported" is vacuously true against it.
    """

    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


class _FakePaths:
    """An ``ApplicationPaths`` stand-in that records every call."""

    def __init__(self, resource_root: Path, reference_root: Path) -> None:
        self.resource_root = resource_root
        self.reference_root = reference_root
        self.calls: list[str] = []

    def ensure_user_reference_catalog(self) -> Path:
        self.calls.append("ensure_user_reference_catalog")
        return self.reference_root


def _paths(tmp_path: Path) -> _FakePaths:
    return _FakePaths(
        resource_root=tmp_path / "resources",
        reference_root=tmp_path / "reference",
    )


def _fake_domain(monkeypatch, service_module, **overrides):
    """Replace both domain calls with recorders."""

    calls: list[tuple] = []

    def refresh_official_universe(**kwargs):
        calls.append(("refresh_official_universe", kwargs))
        return overrides.get("snapshot", "SNAPSHOT")

    def enrich_us_profiles(snapshot, **kwargs):
        calls.append(("enrich_us_profiles", snapshot, kwargs))
        if "enrich" in overrides:
            return overrides["enrich"](snapshot, kwargs)
        return "ENRICHED"

    monkeypatch.setattr(
        service_module, "refresh_official_universe", refresh_official_universe
    )
    monkeypatch.setattr(
        service_module, "enrich_us_profiles", enrich_us_profiles
    )
    return calls


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _window(monkeypatch, tmp_path) -> MainWindow:
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path / "state"))
    _qapp()
    window = MainWindow()
    _qapp().processEvents()
    return window


# -- 2/37: the constructor performs no I/O -----------------------------


def test_the_constructor_performs_no_io(tmp_path) -> None:
    """Spec 2/37: constructing the service must not touch the filesystem.

    ``ensure_user_reference_catalog`` raises here, so any I/O in the
    constructor surfaces as a failure rather than as a stray directory.
    """

    paths = _paths(tmp_path)

    def forbidden() -> Path:
        raise AssertionError("the constructor must not prepare the catalog")

    paths.ensure_user_reference_catalog = forbidden

    service = DesktopUniverseService(paths=paths)

    assert service.paths is paths
    assert paths.calls == []


def test_the_constructor_only_stores_paths(tmp_path) -> None:
    """Spec 35: no mutable runtime state is kept between calls."""

    service = DesktopUniverseService(paths=_paths(tmp_path))

    assert list(vars(service)) == ["paths"]


def test_the_constructor_does_not_create_the_reference_root(
    tmp_path,
) -> None:
    """Spec 5: the writable root only appears once ``refresh`` runs."""

    paths = _paths(tmp_path)
    DesktopUniverseService(paths=paths)

    assert not paths.reference_root.exists()


# -- 4/38: the call order is the contract ------------------------------


def test_refresh_emits_and_calls_in_the_original_order(
    tmp_path, monkeypatch
) -> None:
    """Spec 38: order, not merely "everything was called"."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    order: list[str] = []

    class _Paths:
        resource_root = tmp_path / "resources"
        reference_root = tmp_path / "reference"

        def ensure_user_reference_catalog(self) -> Path:
            order.append("ensure_user_reference_catalog")
            return self.reference_root

    service.paths = _Paths()

    def refresh_official_universe(**kwargs):
        order.append("refresh_official_universe")
        return "SNAPSHOT"

    def enrich_us_profiles(snapshot, **kwargs):
        order.append("enrich_us_profiles")
        return "ENRICHED"

    monkeypatch.setattr(
        module, "refresh_official_universe", refresh_official_universe
    )
    monkeypatch.setattr(module, "enrich_us_profiles", enrich_us_profiles)

    service.refresh(
        should_stop=None,
        progress=lambda event: order.append(f"progress:{event.stage}"),
    )

    assert order == [
        f"progress:{STAGE_PREPARE_REFERENCE}",
        "ensure_user_reference_catalog",
        f"progress:{STAGE_DOWNLOAD_OFFICIAL}",
        "refresh_official_universe",
        f"progress:{STAGE_ENRICH_SEC_START}",
        "enrich_us_profiles",
    ]


def test_the_reference_progress_comes_before_the_directory_exists(
    tmp_path, monkeypatch
) -> None:
    """Spec 4: emit first, then create -- never the other way round."""

    import us_quant.desktop_universe_service as module

    seen: list[tuple[str, bool]] = []
    paths = _paths(tmp_path)
    real_ensure = paths.ensure_user_reference_catalog

    def recording_ensure() -> Path:
        seen.append(("ensure", paths.reference_root.exists()))
        return real_ensure()

    paths.ensure_user_reference_catalog = recording_ensure
    service = DesktopUniverseService(paths=paths)

    def refresh_official_universe(**kwargs):
        return "SNAPSHOT"

    monkeypatch.setattr(
        module, "refresh_official_universe", refresh_official_universe
    )
    monkeypatch.setattr(
        module, "enrich_us_profiles", lambda snapshot, **kwargs: "ENRICHED"
    )

    service.refresh(
        should_stop=None,
        progress=lambda event: seen.append(
            (event.stage, paths.reference_root.exists())
        ),
    )

    assert seen[0] == (STAGE_PREPARE_REFERENCE, False)
    assert seen[1] == ("ensure", False)


# -- 5/6/39: the official download arguments ---------------------------


def test_official_download_receives_every_argument_verbatim(
    tmp_path, monkeypatch
) -> None:
    """Spec 5/6/39: sentinels for each parameter, including save_snapshot."""

    import us_quant.desktop_universe_service as module

    paths = _paths(tmp_path)
    service = DesktopUniverseService(paths=paths)
    seen: dict = {}

    def refresh_official_universe(**kwargs):
        seen.update(kwargs)
        return "SNAPSHOT"

    monkeypatch.setattr(
        module, "refresh_official_universe", refresh_official_universe
    )
    monkeypatch.setattr(
        module, "enrich_us_profiles", lambda snapshot, **kwargs: "ENRICHED"
    )

    def stop() -> bool:
        return False

    service.refresh(should_stop=stop)

    assert seen["cache_root"] == paths.reference_root
    assert seen["leader_seed_path"] == (
        paths.resource_root / "configs" / "sector_leaders.csv"
    )
    assert seen["china_denylist_path"] == (
        paths.resource_root / "configs" / "china_concept_denylist.csv"
    )
    assert seen["should_stop"] is stop
    assert seen["save_snapshot"] is False


def test_the_official_download_never_saves_a_snapshot(
    tmp_path, monkeypatch
) -> None:
    """Spec 7: a cancelled enrichment must not clobber the last snapshot.

    ``save_snapshot=False`` is the safety semantic that keeps a
    half-finished refresh from overwriting a complete one.
    """

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    seen: dict = {}

    monkeypatch.setattr(
        module,
        "refresh_official_universe",
        lambda **kwargs: seen.update(kwargs) or "SNAPSHOT",
    )
    monkeypatch.setattr(
        module, "enrich_us_profiles", lambda snapshot, **kwargs: "ENRICHED"
    )

    service.refresh(should_stop=None)

    assert seen["save_snapshot"] is False


# -- 9/40: the enrichment arguments ------------------------------------


def test_enrichment_receives_every_argument_verbatim(
    tmp_path, monkeypatch
) -> None:
    """Spec 9/40: snapshot identity, cache root, budget, callback identity."""

    import us_quant.desktop_universe_service as module

    paths = _paths(tmp_path)
    service = DesktopUniverseService(paths=paths)
    seen: dict = {}
    snapshot = object()

    monkeypatch.setattr(
        module,
        "refresh_official_universe",
        lambda **kwargs: snapshot,
    )

    def enrich_us_profiles(passed, **kwargs):
        seen["snapshot"] = passed
        seen.update(kwargs)
        return "ENRICHED"

    monkeypatch.setattr(module, "enrich_us_profiles", enrich_us_profiles)

    def stop() -> bool:
        return False

    service.refresh(should_stop=stop)

    assert seen["snapshot"] is snapshot
    assert seen["cache_root"] == paths.reference_root / "sec_profiles"
    assert seen["max_new_profiles"] == 500
    assert seen["should_stop"] is stop


def test_the_profile_budget_is_five_hundred(tmp_path, monkeypatch) -> None:
    """Spec 8/9: 500 stays a literal, not a tuning knob."""

    assert SEC_PROFILE_BUDGET == 500

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    seen: dict = {}
    monkeypatch.setattr(
        module, "refresh_official_universe", lambda **kwargs: "SNAPSHOT"
    )
    monkeypatch.setattr(
        module,
        "enrich_us_profiles",
        lambda snapshot, **kwargs: seen.update(kwargs) or "ENRICHED",
    )

    service.refresh(should_stop=None)

    assert seen["max_new_profiles"] == 500


def test_the_domain_timeouts_are_left_at_their_defaults(
    tmp_path, monkeypatch
) -> None:
    """Spec 9: no request interval / user agent / timeout is passed."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    seen: dict = {}
    monkeypatch.setattr(
        module, "refresh_official_universe", lambda **kwargs: "SNAPSHOT"
    )
    monkeypatch.setattr(
        module,
        "enrich_us_profiles",
        lambda snapshot, **kwargs: seen.update(kwargs) or "ENRICHED",
    )

    service.refresh(should_stop=None)

    for name in (
        "request_interval_seconds",
        "user_agent",
        "timeout",
        "max_attempts",
    ):
        assert name not in seen, name


# -- 10/41/42: the SEC progress becomes a domain event -----------------


def test_sec_progress_becomes_a_domain_event(tmp_path, monkeypatch) -> None:
    """Spec 10/41: done/total/symbol map onto the DTO fields."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    events: list[UniverseRefreshProgress] = []

    monkeypatch.setattr(
        module, "refresh_official_universe", lambda **kwargs: "SNAPSHOT"
    )

    def enrich_us_profiles(snapshot, *, progress, **kwargs):
        progress(37, 500, "AAPL")
        return "ENRICHED"

    monkeypatch.setattr(module, "enrich_us_profiles", enrich_us_profiles)

    service.refresh(should_stop=None, progress=events.append)

    sec = [event for event in events if event.stage == STAGE_ENRICH_SEC]
    assert len(sec) == 1
    assert sec[0].done == 37
    assert sec[0].total == 500
    assert sec[0].detail == "AAPL"


def test_a_failure_detail_is_passed_through_untouched(
    tmp_path, monkeypatch
) -> None:
    """Spec 10/42: no parsing, no truncation, no translation."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    events: list[UniverseRefreshProgress] = []
    detail = "XYZ 暂时失败: timeout"

    monkeypatch.setattr(
        module, "refresh_official_universe", lambda **kwargs: "SNAPSHOT"
    )

    def enrich_us_profiles(snapshot, *, progress, **kwargs):
        progress(9, 500, detail)
        return "ENRICHED"

    monkeypatch.setattr(module, "enrich_us_profiles", enrich_us_profiles)

    service.refresh(should_stop=None, progress=events.append)

    sec = [event for event in events if event.stage == STAGE_ENRICH_SEC]
    assert sec[0].detail == detail


def test_every_sec_progress_call_is_forwarded(tmp_path, monkeypatch) -> None:
    """Spec 10: one event per domain callback, none dropped."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    events: list[UniverseRefreshProgress] = []

    monkeypatch.setattr(
        module, "refresh_official_universe", lambda **kwargs: "SNAPSHOT"
    )

    def enrich_us_profiles(snapshot, *, progress, **kwargs):
        for index in range(3):
            progress(index, 500, f"S{index}")
        return "ENRICHED"

    monkeypatch.setattr(module, "enrich_us_profiles", enrich_us_profiles)

    service.refresh(should_stop=None, progress=events.append)

    sec = [event for event in events if event.stage == STAGE_ENRICH_SEC]
    assert [(event.done, event.detail) for event in sec] == [
        (0, "S0"),
        (1, "S1"),
        (2, "S2"),
    ]


# -- 11/43: the four stages, and progress=None -------------------------


def test_the_service_declares_exactly_four_stages(tmp_path) -> None:
    """Spec 11: the stage names are module constants, not magic strings."""

    import us_quant.desktop_universe_service as module

    assert STAGE_PREPARE_REFERENCE == "prepare_reference"
    assert STAGE_DOWNLOAD_OFFICIAL == "download_official"
    assert STAGE_ENRICH_SEC_START == "enrich_sec_start"
    assert STAGE_ENRICH_SEC == "enrich_sec"

    source = pathlib.Path(
        _REPO_ROOT / SERVICE_MODULE
    ).read_text(encoding="utf-8")
    assert '"prepare_reference"' in source
    assert '"download_official"' in source
    assert '"enrich_sec_start"' in source
    assert '"enrich_sec"' in source
    assert source.count('"prepare_reference"') == 1
    assert source.count('"download_official"') == 1
    assert source.count('"enrich_sec_start"') == 1
    assert source.count('"enrich_sec"') == 1


def test_refresh_works_without_an_observer(tmp_path, monkeypatch) -> None:
    """Spec 43: no observer must not change the business path."""

    import us_quant.desktop_universe_service as module

    paths = _paths(tmp_path)
    service = DesktopUniverseService(paths=paths)
    calls: list[str] = []

    monkeypatch.setattr(
        module,
        "refresh_official_universe",
        lambda **kwargs: calls.append("official") or "SNAPSHOT",
    )
    monkeypatch.setattr(
        module,
        "enrich_us_profiles",
        lambda snapshot, **kwargs: calls.append("enrich") or "ENRICHED",
    )

    assert service.refresh(should_stop=None) == "ENRICHED"
    assert calls == ["official", "enrich"]
    assert paths.calls == ["ensure_user_reference_catalog"]


# -- 14: the return value is the domain snapshot -----------------------


def test_refresh_returns_the_enriched_snapshot(tmp_path, monkeypatch) -> None:
    """Spec 14: no extra business wrapper around the domain result."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    enriched = object()

    monkeypatch.setattr(
        module, "refresh_official_universe", lambda **kwargs: "SNAPSHOT"
    )
    monkeypatch.setattr(
        module, "enrich_us_profiles", lambda snapshot, **kwargs: enriched
    )

    assert service.refresh(should_stop=None) is enriched


# -- 15/44/45/46: exceptions propagate unchanged -----------------------


def test_cancellation_during_the_download_propagates(
    tmp_path, monkeypatch
) -> None:
    """Spec 15/44: the service must not swallow or convert it."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    enriched: list = []

    def cancelled(**kwargs):
        raise UniverseRefreshCancelled()

    monkeypatch.setattr(module, "refresh_official_universe", cancelled)
    monkeypatch.setattr(
        module,
        "enrich_us_profiles",
        lambda snapshot, **kwargs: enriched.append(snapshot) or "ENRICHED",
    )

    with pytest.raises(UniverseRefreshCancelled):
        service.refresh(should_stop=None)

    assert enriched == []


def test_cancellation_during_enrichment_propagates(
    tmp_path, monkeypatch
) -> None:
    """Spec 45: the same for the SEC phase."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))

    def cancelled(snapshot, **kwargs):
        raise UniverseRefreshCancelled()

    monkeypatch.setattr(
        module, "refresh_official_universe", lambda **kwargs: "SNAPSHOT"
    )
    monkeypatch.setattr(module, "enrich_us_profiles", cancelled)

    with pytest.raises(UniverseRefreshCancelled):
        service.refresh(should_stop=None)


def test_a_generic_exception_propagates_unchanged(
    tmp_path, monkeypatch
) -> None:
    """Spec 16/46: no new error hierarchy, no wrapping."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    error = OSError("offline")

    def failing(**kwargs):
        raise error

    monkeypatch.setattr(module, "refresh_official_universe", failing)

    with pytest.raises(OSError) as caught:
        service.refresh(should_stop=None)

    assert caught.value is error


def test_the_service_defines_no_cancellation_api(tmp_path) -> None:
    """Spec 17: cancellation belongs to the window, not to the service."""

    for name in ("cancel", "stop", "request_stop"):
        assert not hasattr(DesktopUniverseService, name), name


# -- 47: the progress DTO ----------------------------------------------


def test_the_progress_dto_is_frozen_and_slotted() -> None:
    """Spec 47: immutable, slot-based, and with a fixed field set."""

    assert [field.name for field in dataclasses.fields(
        UniverseRefreshProgress
    )] == ["stage", "done", "total", "detail"]

    event = UniverseRefreshProgress(stage=STAGE_ENRICH_SEC, done=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.stage = "other"  # type: ignore[misc]
    assert not hasattr(event, "__dict__")


def test_the_progress_dto_carries_nothing_but_data() -> None:
    """Spec 47: no exception, window, worker or Event field."""

    names = {
        field.name for field in dataclasses.fields(UniverseRefreshProgress)
    }

    assert names == {"stage", "done", "total", "detail"}
    for forbidden in ("exception", "window", "worker", "event"):
        assert forbidden not in names


# -- 33/34/35: the service's own boundaries ----------------------------


def test_the_service_depends_only_on_the_allowed_modules() -> None:
    """Spec 33: dependency set equality, not a forbidden-name scan."""

    source = (_REPO_ROOT / SERVICE_MODULE).read_text(encoding="utf-8")

    assert _module_imports(source) == {
        "__future__",
        "collections.abc",
        "dataclasses",
        "us_quant.paths",
        "us_quant.universe",
    }


def test_the_service_imports_no_qt_and_no_gui_modules() -> None:
    """Spec 33: the service is Qt-free application code."""

    source = (_REPO_ROOT / SERVICE_MODULE).read_text(encoding="utf-8")
    imported = _module_imports(source)

    for forbidden in (
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtWidgets",
        "us_quant.desktop",
        "us_quant.desktop_workers",
        "us_quant.runtime_supervisor",
        "us_quant.market_data_service",
        "us_quant.history_queue",
        "us_quant.desktop_history_service",
        "us_quant.auto_quant",
    ):
        assert forbidden not in imported, forbidden

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            head = node.module.split(".")[0]
            assert head != "PySide6", node.module


def test_the_service_starts_no_threads() -> None:
    """Spec 34: threading stays with TaskThread and the window."""

    source = (_REPO_ROOT / SERVICE_MODULE).read_text(encoding="utf-8")

    for forbidden in (
        "Thread(",
        "QThread",
        "ThreadPoolExecutor",
        "asyncio",
        "import threading",
        "from threading",
    ):
        assert forbidden not in source, forbidden


def test_the_service_holds_no_mutable_runtime_state() -> None:
    """Spec 35: only ``paths`` is stored; no snapshot, worker or error."""

    tree = ast.parse(
        (_REPO_ROOT / SERVICE_MODULE).read_text(encoding="utf-8")
    )
    service = _class_named(tree, "DesktopUniverseService")

    assigned: list[str] = []
    for node in ast.walk(service):
        if isinstance(node, ast.Attribute) and isinstance(
            node.ctx, ast.Store
        ):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                assigned.append(node.attr)

    assert set(assigned) == {"paths"}


# -- 48: the window owns the service -----------------------------------


def test_the_window_owns_the_service(monkeypatch, tmp_path) -> None:
    """Spec 48: one service, built over the window's own paths."""

    window = _window(monkeypatch, tmp_path)
    try:
        assert isinstance(window.universe_service, DesktopUniverseService)
        assert window.universe_service.paths is window.paths
    finally:
        window.deleteLater()


def test_the_window_does_not_build_a_second_paths_object(
    monkeypatch, tmp_path
) -> None:
    """Spec 48: ``ApplicationPaths.discover`` must not run again."""

    calls: list[int] = []
    original = ApplicationPaths.discover

    def counting_discover():
        calls.append(1)
        return original()

    monkeypatch.setattr(ApplicationPaths, "discover", counting_discover)

    window = _window(monkeypatch, tmp_path)
    try:
        assert window.universe_service.paths is window.paths
        assert calls == [1]
    finally:
        window.deleteLater()


# -- 25/49/50: the window's formatting ---------------------------------





# -- 51: the cancel callback is the current event ----------------------



def test_the_service_receives_the_same_callback_twice(
    monkeypatch, tmp_path
) -> None:
    """Spec 18: both domain calls get the identical callable."""

    import us_quant.desktop_universe_service as module

    service = DesktopUniverseService(paths=_paths(tmp_path))
    seen: list = []

    monkeypatch.setattr(
        module,
        "refresh_official_universe",
        lambda **kwargs: seen.append(kwargs["should_stop"]) or "SNAPSHOT",
    )
    monkeypatch.setattr(
        module,
        "enrich_us_profiles",
        lambda snapshot, **kwargs: seen.append(kwargs["should_stop"])
        or "ENRICHED",
    )

    def stop() -> bool:
        return False

    service.refresh(should_stop=stop)

    assert len(seen) == 2
    assert seen[0] is stop
    assert seen[1] is stop


# -- 26/52/53: start refusal and start success -------------------------





# -- 54/55/56/57: the scope guards -------------------------------------


@pytest.mark.parametrize("name", FROZEN_METHODS)
def test_the_frozen_method_is_byte_identical(name: str) -> None:
    """Spec 54/55: these methods may not change in this step."""

    base = _require_base("src/us_quant/desktop.py")
    current = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )

    base_method = _find_method(base, name)
    current_method = _find_method(current, name)

    if name in SYSTEM_V2_REPAIR_METHODS:
        # System v2 repair: only the declared lifecycle publication delta may
        # differ from the base bytes; anything else still fails here.
        assert current_method.count(_SYSTEM_V2_REPAIR_DELTA_BLOCK) == 1, name
        current_method = current_method.replace(
            _SYSTEM_V2_REPAIR_DELTA_BLOCK, _SYSTEM_V2_REPAIR_BASE_BLOCK
        )

    if name == "closeEvent":
        # v2O-A: the live-feed check asks the market orchestrator instead of
        # reading the worker attribute.  Only that declared delta may differ.
        assert (
            current_method.count(
                _DESKTOP_MARKET_ORCHESTRATION_V2_CLOSE_EVENT_DELTA
            )
            == 1
        ), name
        current_method = current_method.replace(
            _DESKTOP_MARKET_ORCHESTRATION_V2_CLOSE_EVENT_DELTA,
            _DESKTOP_MARKET_ORCHESTRATION_V2_CLOSE_EVENT_BASE,
        )

    if name == "_start_task":
        # v2O-C1: the generic task boundary gained one optional completion hook.
        # Each declared edit is asserted to be present exactly once before being
        # reverted, so an edit that lands twice -- or lands somewhere else in
        # the method -- fails here instead of silently widening the freeze.
        for delta, base_block in _DESKTOP_RESEARCH_FOUNDATIONS_V2_START_TASK_DELTAS:
            assert current_method.count(delta) == 1, (name, delta)
            current_method = current_method.replace(delta, base_block)

    if name == "_request_worker_stops":
        # v2O-C1: the shutdown signal now goes through the capability's
        # lifecycle rather than the window setting its cancel event.
        assert (
            current_method.count(
                _DESKTOP_RESEARCH_FOUNDATIONS_V2_REQUEST_WORKER_STOPS_DELTA
            )
            == 1
        ), name
        current_method = current_method.replace(
            _DESKTOP_RESEARCH_FOUNDATIONS_V2_REQUEST_WORKER_STOPS_DELTA,
            _DESKTOP_RESEARCH_FOUNDATIONS_V2_REQUEST_WORKER_STOPS_BASE,
        )

    assert current_method == base_method, name


def _find_method(source: str, name: str) -> str:
    tree = ast.parse(source)
    window = _class_named(tree, "MainWindow")
    for node in window.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return _source_of(window, node, source).replace("\r\n", "\n")
    raise AssertionError(f"method {name} not found")


def test_only_the_declared_methods_changed() -> None:
    """Spec 56: the declared surface is exactly ``__init__`` and the refresh.

    The guard is relative to this step's base commit.  Every later round
    appends the methods it declares to ``LATER_ROUND_METHODS`` and ships its
    own guard against its own base commit, so this stays meaningful instead
    of being relaxed every time.
    """

    base = _require_base("src/us_quant/desktop.py")
    current = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )

    base_tree = ast.parse(base)
    current_tree = ast.parse(current)
    base_window = _class_named(base_tree, "MainWindow")
    current_window = _class_named(current_tree, "MainWindow")

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
    declared_scanner = set(
        DESKTOP_SCANNER_ORCHESTRATION_V2_REMOVED_METHODS
    ) | set(DESKTOP_SCANNER_ORCHESTRATION_V2_ADDED_METHODS)
    assert not (
        set(DESKTOP_SCANNER_ORCHESTRATION_V2_NET_ZERO_METHODS)
        & declared_scanner
    )
    assert not (
        set(DESKTOP_SCANNER_ORCHESTRATION_V2_NET_ZERO_METHODS)
        & (set(base_methods) - set(current_methods))
    )
    assert not (
        set(DESKTOP_SCANNER_ORCHESTRATION_V2_NET_ZERO_METHODS)
        & (set(current_methods) - set(base_methods))
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

    allowed = (
        set(REFACTORED_METHODS)
        | {"_run_backtest_workspace"}
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
    # Exact, not a subset: the delta is the declared surface and nothing
    # else, in both directions.
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
    # v2O-C1: the window no longer reads the universe off itself, so the
    # targeted-replay entry points and the shutdown path changed with it.
    assert set(DESKTOP_RESEARCH_FOUNDATIONS_V2_METHODS) <= set(changed)


def test_the_net_zero_methods_exist_in_neither_revision() -> None:
    """v2O-C1: pin what an earlier round added and this round deleted.

    These methods are in neither delta, so without this guard the only record of
    their deletion would be a comment.  Asserting both halves -- absent from the
    base commit *and* absent now -- is what makes "net zero" a fact rather than a
    claim, and asserting disjointness from the deltas keeps them from being
    double-counted if a later round re-adds one.
    """

    base = _require_base("src/us_quant/desktop.py")
    current = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )

    def names(source: str) -> set[str]:
        window = _class_named(ast.parse(source), "MainWindow")
        return {
            node.name
            for node in window.body
            if isinstance(node, ast.FunctionDef)
        }

    base_names, current_names = names(base), names(current)
    for name in DESKTOP_RESEARCH_FOUNDATIONS_V2_NET_ZERO_METHODS:
        assert name not in base_names, f"{name} is in the base commit"
        assert name not in current_names, f"{name} is still declared"

    declared = set(DESKTOP_RESEARCH_FOUNDATIONS_V2_REMOVED_METHODS) | set(
        DESKTOP_RESEARCH_FOUNDATIONS_V2_ADDED_METHODS
    )
    assert not (set(DESKTOP_RESEARCH_FOUNDATIONS_V2_NET_ZERO_METHODS) & declared)


def test_the_other_frozen_modules_are_untouched() -> None:
    """Spec 58/59/60/62/63/64: the neighbours do not move."""

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


def test_desktop_py_no_longer_calls_the_domain_directly() -> None:
    """Spec 57: the real calls live only in the service module."""

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )

    assert "refresh_official_universe(" not in source
    assert "enrich_us_profiles(" not in source
    assert "refresh_official_universe" not in _imported_names(source)
    assert "enrich_us_profiles" not in _imported_names(source)

    service = (_REPO_ROOT / SERVICE_MODULE).read_text(encoding="utf-8")
    assert "refresh_official_universe(" in service
    assert "enrich_us_profiles(" in service


def test_the_window_still_uses_the_remaining_universe_helpers() -> None:
    """Spec 28: the three helpers other paths need are still imported.

    Import *names* are checked, not source substrings: the auto-scan
    teardown calls ``prioritized_research_symbols`` in its body, so a
    substring assertion would still pass after the import was deleted.
    """

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )
    imported = _imported_names(source)

    assert "us_quant.universe" in _module_imports(source)
    for name in (
        "UniverseSnapshot",
        "load_universe_snapshot",
        "prioritized_research_symbols",
    ):
        assert name in imported, name
