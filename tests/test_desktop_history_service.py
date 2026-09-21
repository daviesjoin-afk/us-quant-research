"""Tests for the desktop history queue service and its wiring.

The service is pure application code: no Qt, no threads, no network.  The
runners and the job store are faked, so these tests never touch SQLite in the
real runtime root, never open an IBKR socket and never reach Yahoo.

One deliberate asymmetry is worth stating up front, because it looks like a
gap and is not: ``_auto_market_scan_finished`` keeps calling
``HistoryJobStore`` and ``prioritized_research_symbols`` directly.  That call
sits on the AutoQuant candidate-preparation path, which this step freezes, so
the scope guard below checks only the five history methods rather than
asserting the whole module is free of the store.  A module-wide ban would
force a change to AutoQuant.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
from dataclasses import replace

import pytest

from us_quant.desktop import MainWindow
from us_quant.desktop_history_service import (
    DesktopHistoryService,
    HistoryQueueSnapshot,
    HistoryScheduleResult,
)
from us_quant.paths import STATE_ROOT_ENV

_APP = None


def _qapp():
    """A ``QApplication`` for the wiring tests, created at most once."""

    global _APP
    from PySide6.QtWidgets import QApplication

    _APP = QApplication.instance() or QApplication([])
    return _APP


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_DESKTOP_PATH = _REPO_ROOT / "src" / "us_quant" / "desktop.py"
_SERVICE_PATH = (
    _REPO_ROOT / "src" / "us_quant" / "desktop_history_service.py"
)

BASE_COMMIT = "2b3e3f5337274f46f66e7757d7c71be2b471e846"

# Spec 33: the only MainWindow methods this step may change.
REFACTORED_METHODS = (
    "__init__",
    "_schedule_history",
    "_run_history",
    "_run_public_history",
    "_retry_failed",
)

# Methods a *later* round legitimately rewrote.  Each round appends the
# methods it declared; the union is what this guard tolerates relative to
# its own base commit.  Adding a name here that no round declared is exactly
# the scope violation this guard exists to catch -- and the round that adds
# one must ship its own guard against its own base commit.
LATER_ROUND_METHODS = (
    "_settings_tab",  # step 11: the Settings page moved into a panel
    "_refresh_universe",  # step 13: the refresh moved into a service
    "_run_scan",  # step 14: the manual scan moved into a service
    "_run_backtest_workspace",  # step 15: the batch loop moved into a service
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
    "_populate_dashboard_account_cards",
    "_refresh_account_surfaces",
"_build_v2_pages",)

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

# Spec 34/58: these must stay byte-identical to the base commit.
# Market Data v2: the methods this round rewrote because the market
# data boundary moved from a v1 service to the application service.
# Declared so the guard below can assert the delta exactly.
MARKET_DATA_V2_METHODS = (
    # Market Data v2: the window talks to the application service and the
    # domain snapshot instead of the v1 service and transport types.
    "__init__",
    "_start_stream",
    "_stream_snapshot_pushed",
    "_stream_snapshot_received",
    "_poll_stream_snapshot",
    "_invalidate_stream_snapshot",
    "_record_minute_snapshot",
    "_update_quote_readiness",
    "_maybe_rotate_extended_ibkr_session",
    "_request_stream_switch",
    "_save_user_preferences",
    "_clear_selected_api_credentials",
    "_api_provider_changed",
)

# Broker Account v2: the methods this round rewrote because the
# account truth moved from ``IBKRReadOnlySnapshot``/``PortfolioView``
# to the domain ``BrokerAccountPortfolio``, and because the account
# page became a native Desktop UI v2 page.
BROKER_ACCOUNT_V2_METHODS = (
    "_account_snapshot_finished",
    "_export_terminal_state",
    "_paper_simulation_capital",
    "_refresh_account_snapshot",
    "_refresh_cards",
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
    "_market_controls",
    "_publish_market_controls",
    "_publish_market_health",
    "_publish_market_view",
)

DESKTOP_MARKET_V2_METHODS = (
    "_activate_pending_stream_switch",
    "_apply_intraday_watchlist",
    "_apply_target_symbol",
    "_settings_provider_selected",
    "_stream_failed",
    "_stream_provider_selected",
    "_stream_symbols_from_input",
    "_switch_to_settings_provider",
    "_sync_targeted_symbol_to_stream",
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
    "_publish_universe_view",
    "_connect_history_page",
    "_publish_history_view",
    "_history_task_failed",
)
RESEARCH_DATA_V2_METHODS = (
    "_dashboard_tab",
    "_load_local_state",
    "_refresh_universe",
    "_cancel_universe_refresh",
    "_reset_universe_refresh_controls",
    "_universe_refreshed",
    "_auto_market_scan_finished",
    "_schedule_history",
    "_run_history",
    "_run_public_history",
    "_retry_failed",
    "_history_finished",
    "_task_failed",
)

SCANNER_V2_REMOVED_METHODS = (
    "_scanner_tab",
    "_populate_scan_table",
    "_scan_selection_changed",
)
SCANNER_V2_ADDED_METHODS = (
    "_connect_scanner_page",
    "_publish_scanner_view",
    "_scanner_symbol_selected",
)
SCANNER_V2_METHODS = (
    "_scan_finished",
    "_load_scan_file",
    "_auto_market_scan_finished",
    "_dashboard_tab",
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
    "_stream_is_live",
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
    "_start_stream",
    "_stop_auto_market_data",
    "_stop_stream",
    "_stream_finished",
    "_task_failed",
    "_worker_finished",
)

FROZEN_METHODS = (
    "_start_task",
    "closeEvent",
)

# Spec 56: the five history methods must not build a store or call a runner.
HISTORY_METHODS = (
    "_schedule_history",
    "_run_history",
    "_run_public_history",
    "_retry_failed",
    "_publish_history_view",
)

FORBIDDEN_CALLS = (
    "HistoryJobStore",
    "run_history_queue",
    "run_public_history_queue",
)


# -- source helpers ---------------------------------------------------


def _source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _desktop_source() -> str:
    return _source(_DESKTOP_PATH)


def _service_source() -> str:
    return _source(_SERVICE_PATH)


def _main_window_class(tree: ast.Module) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise AssertionError("MainWindow not found")


def _method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _source_of(cls: ast.ClassDef, node: ast.FunctionDef, text: str) -> str:
    lines = text.splitlines()
    return "\n".join(lines[node.lineno - 1 : node.end_lineno])


def _called_names(node: ast.AST) -> set[str]:
    """Every bare or attribute call name inside ``node``."""

    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _base_source(path: str) -> str | None:
    """``path`` as it was at the base commit, or ``None`` if unreachable.

    CI checks out with ``fetch-depth: 1``, so the base commit is not in the
    object database by default.  Fetch it on demand; if it still cannot be
    read the caller fails hard rather than skipping, because a silently
    skipped scope guard is worse than a red build.
    """

    def run(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            args,
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    result = run(["git", "show", f"{BASE_COMMIT}:{path}"])
    if result.returncode == 0:
        return result.stdout
    run(["git", "fetch", "--depth", "1", "-q", "origin", BASE_COMMIT])
    result = run(["git", "show", f"{BASE_COMMIT}:{path}"])
    return result.stdout if result.returncode == 0 else None


def _window(monkeypatch, tmp_path) -> MainWindow:
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path / "state"))
    _qapp()
    window = MainWindow()
    _qapp().processEvents()
    return window


def _job(index: int, status: str):
    """A minimal stand-in for a ``HistoryJob`` row."""

    return type(
        "Job",
        (),
        {
            "symbol": f"S{index:05d}",
            "duration": "1 Y",
            "priority": index,
            "status": status,
            "attempts": 0,
            "row_count": None,
            "last_error": "",
        },
    )()


# -- fakes ------------------------------------------------------------


class _FakeStore:
    """A ``HistoryJobStore`` stand-in that records every interaction."""

    instances: list["_FakeStore"] = []

    def __init__(self, path) -> None:
        self.path = path
        self.calls: list[tuple] = []
        self.scheduled: list[tuple[str, ...]] = []
        self.schedule_result = 0
        self.reset_result = 0
        self.jobs: tuple = ()
        self.counts_result: dict[str, int] = {}
        _FakeStore.instances.append(self)

    def schedule(self, symbols) -> int:
        self.calls.append(("schedule", tuple(symbols)))
        self.scheduled.append(tuple(symbols))
        return self.schedule_result

    def list_jobs(self, **_kwargs) -> tuple:
        self.calls.append(("list_jobs",))
        return self.jobs

    def counts(self) -> dict[str, int]:
        self.calls.append(("counts",))
        return dict(self.counts_result)

    def reset_failed(self) -> int:
        self.calls.append(("reset_failed",))
        return self.reset_result


@pytest.fixture
def fake_store(monkeypatch):
    _FakeStore.instances = []
    monkeypatch.setattr(
        "us_quant.desktop_history_service.HistoryJobStore", _FakeStore
    )
    return _FakeStore


def _service(tmp_path) -> DesktopHistoryService:
    return DesktopHistoryService(
        queue_path=tmp_path / "history.sqlite3",
        data_root=tmp_path / "data",
    )


# -- 39: the constructor must not touch the filesystem -----------------


def test_the_constructor_performs_no_io(monkeypatch, tmp_path) -> None:
    """Spec 2/22/39: building the service must not create the SQLite file.

    ``HistoryJobStore.__init__`` makes the parent directory and opens SQLite.
    Holding a store on the service would therefore create
    ``runtime/history_jobs.sqlite3`` merely by starting the window, moving
    startup I/O.  The fake raises on construction so any attempt is loud.
    """

    def exploding_store(*_args, **_kwargs):
        raise AssertionError("the service must not build a store eagerly")

    monkeypatch.setattr(
        "us_quant.desktop_history_service.HistoryJobStore", exploding_store
    )

    nested = tmp_path / "nested" / "history.sqlite3"
    service = DesktopHistoryService(
        queue_path=nested,
        data_root=tmp_path / "data",
    )

    assert service.queue_path == nested
    assert not nested.exists()
    assert not nested.parent.exists()


def test_the_constructor_stores_paths_not_strings(tmp_path) -> None:
    """The spec signature accepts ``str | Path``; both normalise to ``Path``."""

    service = DesktopHistoryService(
        queue_path=str(tmp_path / "q.sqlite3"),
        data_root=str(tmp_path / "data"),
    )

    assert isinstance(service.queue_path, pathlib.Path)
    assert isinstance(service.data_root, pathlib.Path)
    assert service.queue_path.name == "q.sqlite3"


def test_the_store_is_built_per_call(tmp_path, fake_store) -> None:
    """A store is created lazily, once per call, and never cached."""

    service = _service(tmp_path)

    service.reset_failed()
    service.reset_failed()

    assert len(fake_store.instances) == 2
    for store in fake_store.instances:
        assert store.path == service.queue_path


def test_the_store_receives_the_service_queue_path(tmp_path, fake_store) -> None:
    """The store must be opened on ``self.queue_path``, not a default."""

    service = _service(tmp_path)

    def build(path):
        store = _FakeStore(path)
        store.counts_result = {
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
        }
        return store

    import us_quant.desktop_history_service as module

    original = module.HistoryJobStore
    module.HistoryJobStore = build
    try:
        service.snapshot()
    finally:
        module.HistoryJobStore = original

    assert fake_store.instances[0].path == service.queue_path


# -- 40/41: schedule_universe ----------------------------------------


def test_schedule_universe_keeps_the_full_research_pool(
    monkeypatch, tmp_path, fake_store
) -> None:
    """Spec 4: ``limit=None`` -- this button means the whole pool.

    The order the priority helper returns must reach the store unchanged, so
    the assertion records the tuple the fake was handed rather than a count.
    """

    recorded: list[dict] = []

    def fake_priority(universe, *, limit=250):
        recorded.append({"universe": universe, "limit": limit})
        return ("MSFT", "AAPL", "NVDA")

    monkeypatch.setattr(
        "us_quant.desktop_history_service.prioritized_research_symbols",
        fake_priority,
    )

    universe = object()
    service = _service(tmp_path)
    service.schedule_universe(universe)

    assert recorded == [{"universe": universe, "limit": None}]
    assert fake_store.instances[0].scheduled == [
        ("MSFT", "AAPL", "NVDA")
    ]


def test_schedule_universe_reports_inserted_and_total(
    monkeypatch, tmp_path, fake_store
) -> None:
    """Spec 41: ``inserted`` is the store's return, ``total`` the queue size."""

    monkeypatch.setattr(
        "us_quant.desktop_history_service.prioritized_research_symbols",
        lambda *_args, **_kwargs: ("MSFT",),
    )

    def with_result(service):
        store = _FakeStore(service.queue_path)
        store.schedule_result = 2
        store.jobs = (object(),) * 5
        return store

    service = _service(tmp_path)
    monkeypatch.setattr(
        "us_quant.desktop_history_service.HistoryJobStore",
        lambda path: with_result(service),
    )

    result = service.schedule_universe(object())

    assert isinstance(result, HistoryScheduleResult)
    assert (result.inserted, result.total) == (2, 5)


def test_schedule_result_is_a_frozen_slots_dataclass() -> None:
    """Spec 48: the result type is immutable and slot-based."""

    import dataclasses

    assert [field.name for field in dataclasses.fields(HistoryScheduleResult)] == [
        "inserted",
        "total",
    ]
    assert HistoryScheduleResult.__slots__ == ("inserted", "total")

    instance = HistoryScheduleResult(inserted=1, total=2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        instance.inserted = 3


# -- 42/43: run_ibkr --------------------------------------------------


def test_run_ibkr_passes_every_argument_through(
    monkeypatch, tmp_path, fake_store
) -> None:
    """Spec 7/42: config, store, data root, batch size and progress identity."""

    seen: dict = {}

    def fake_runner(config, store, **kwargs):
        seen["config"] = config
        seen["store"] = store
        seen.update(kwargs)
        return {"pending": 1}

    monkeypatch.setattr(
        "us_quant.desktop_history_service.run_history_queue", fake_runner
    )

    config = object()
    progress = lambda *args: None  # noqa: E731
    service = _service(tmp_path)

    result = service.run_ibkr(
        config, maximum_jobs=25, progress=progress
    )

    assert result == {"pending": 1}
    assert seen["config"] is config
    assert seen["store"] is fake_store.instances[0]
    assert seen["data_root"] == service.data_root
    assert seen["maximum_jobs"] == 25
    assert seen["progress"] is progress


def test_run_ibkr_does_not_add_runner_defaults(
    monkeypatch, tmp_path, fake_store
) -> None:
    """Spec 8: the window passed only these keys; the service must not widen."""

    seen: dict = {}

    def fake_runner(config, store, **kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(
        "us_quant.desktop_history_service.run_history_queue", fake_runner
    )

    _service(tmp_path).run_ibkr(object(), maximum_jobs=3)

    assert set(seen) == {"data_root", "maximum_jobs", "progress"}
    assert seen["progress"] is None


def test_run_ibkr_never_resets_failed_jobs(
    monkeypatch, tmp_path, fake_store
) -> None:
    """Spec 43: the IBKR path must leave failures alone.

    ``run_public`` re-queues failures because the free source exists to
    retry what IBKR could not deliver.  Doing that here would silently
    resurrect every failure on every IBKR run.
    """

    def forbidden(*_args, **_kwargs):
        raise AssertionError("run_ibkr must not reset failed jobs")

    monkeypatch.setattr(_FakeStore, "reset_failed", forbidden)
    monkeypatch.setattr(
        "us_quant.desktop_history_service.run_history_queue",
        lambda *args, **kwargs: {"completed": 1},
    )

    assert _service(tmp_path).run_ibkr(
        object(), maximum_jobs=1
    ) == {"completed": 1}


# -- 44/45: run_public -------------------------------------------------


def test_run_public_resets_failed_before_running(
    monkeypatch, tmp_path, fake_store
) -> None:
    """Spec 11/12/44: the order is construct, reset, then run."""

    order: list[str] = []

    monkeypatch.setattr(
        _FakeStore,
        "reset_failed",
        lambda self: (order.append("reset_failed"), 0)[1],
    )
    monkeypatch.setattr(
        "us_quant.desktop_history_service.run_public_history_queue",
        lambda store, **kwargs: (
            order.append("runner"),
            {"completed": 0},
        )[1],
    )

    _service(tmp_path).run_public(maximum_jobs=5)

    assert order == ["reset_failed", "runner"]


def test_run_public_passes_arguments_through(
    monkeypatch, tmp_path, fake_store
) -> None:
    """Spec 45: data root, batch size and progress all survive the move."""

    seen: dict = {}

    def fake_runner(store, **kwargs):
        seen["store"] = store
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(
        "us_quant.desktop_history_service.run_public_history_queue",
        fake_runner,
    )

    progress = lambda *args: None  # noqa: E731
    service = _service(tmp_path)
    service.run_public(maximum_jobs=7, progress=progress)

    assert seen["store"] is fake_store.instances[0]
    assert seen["data_root"] == service.data_root
    assert seen["maximum_jobs"] == 7
    assert seen["progress"] is progress
    assert set(seen) == {"store", "data_root", "maximum_jobs", "progress"}


def test_run_public_returns_the_runner_result(
    monkeypatch, tmp_path, fake_store
) -> None:
    """Spec 10: ``_history_finished`` still receives the raw count dict."""

    payload = {"pending": 2, "running": 0, "completed": 9, "failed": 1}
    monkeypatch.setattr(
        "us_quant.desktop_history_service.run_public_history_queue",
        lambda store, **kwargs: payload,
    )

    assert _service(tmp_path).run_public(maximum_jobs=1) is payload


# -- 46: reset_failed --------------------------------------------------


def test_reset_failed_returns_the_store_count(tmp_path, fake_store) -> None:
    """Spec 15/46: the service forwards the count and adds nothing."""

    service = _service(tmp_path)
    _FakeStore.instances = []
    holder: list = []

    def build(path):
        store = _FakeStore(path)
        store.reset_result = 4
        holder.append(store)
        return store

    import us_quant.desktop_history_service as module

    original = module.HistoryJobStore
    module.HistoryJobStore = build
    try:
        assert service.reset_failed() == 4
    finally:
        module.HistoryJobStore = original

    assert [call[0] for call in holder[0].calls] == ["reset_failed"]
    assert holder[0].path == service.queue_path


# -- 47/48: snapshot ---------------------------------------------------


def test_snapshot_maps_counts_onto_explicit_fields(
    monkeypatch, tmp_path, fake_store
) -> None:
    """Spec 18/47: the UI must not know the raw ``counts()`` key contract."""

    jobs = (object(), object(), object())

    def build(path):
        store = _FakeStore(path)
        store.jobs = jobs
        store.counts_result = {
            "pending": 4,
            "running": 1,
            "completed": 8,
            "failed": 2,
        }
        return store

    import us_quant.desktop_history_service as module

    original = module.HistoryJobStore
    module.HistoryJobStore = build
    try:
        snapshot = _service(tmp_path).snapshot()
    finally:
        module.HistoryJobStore = original

    assert isinstance(snapshot, HistoryQueueSnapshot)
    assert snapshot.jobs == jobs
    assert (snapshot.pending, snapshot.running) == (4, 1)
    assert (snapshot.completed, snapshot.failed) == (8, 2)


def test_snapshot_preserves_the_windows_original_read_order(
    tmp_path,
) -> None:
    """Rows are observed before counts, exactly as the old window did.

    The queue can change while a history runner is active.  Reversing these
    two reads would be a subtle behaviour change in an extraction-only PR,
    even though both orders look equivalent in a quiescent unit test.
    """

    calls: list[str] = []

    class OrderedStore(_FakeStore):
        def list_jobs(self, **_kwargs) -> tuple:
            calls.append("list_jobs")
            return ()

        def counts(self) -> dict[str, int]:
            calls.append("counts")
            return {
                "pending": 0,
                "running": 0,
                "completed": 0,
                "failed": 0,
            }

    import us_quant.desktop_history_service as module

    original = module.HistoryJobStore
    module.HistoryJobStore = OrderedStore
    try:
        _service(tmp_path).snapshot()
    finally:
        module.HistoryJobStore = original

    assert calls == ["list_jobs", "counts"]


def test_snapshot_does_not_apply_the_table_cap(
    monkeypatch, tmp_path, fake_store
) -> None:
    """Spec 17: the 2500-row cap is a presentation rule, not a data rule."""

    jobs = tuple(object() for _ in range(2600))

    def build(path):
        store = _FakeStore(path)
        store.jobs = jobs
        store.counts_result = {
            "pending": 2600,
            "running": 0,
            "completed": 0,
            "failed": 0,
        }
        return store

    import us_quant.desktop_history_service as module

    original = module.HistoryJobStore
    module.HistoryJobStore = build
    try:
        snapshot = _service(tmp_path).snapshot()
    finally:
        module.HistoryJobStore = original

    assert len(snapshot.jobs) == 2600


def test_snapshot_type_is_frozen_and_slotted() -> None:
    """Spec 48: ``HistoryQueueSnapshot`` is a frozen, slotted dataclass."""

    import dataclasses

    assert dataclasses.fields(HistoryQueueSnapshot)
    assert HistoryQueueSnapshot.__slots__ == (
        "jobs",
        "pending",
        "running",
        "completed",
        "failed",
    )

    instance = HistoryQueueSnapshot((), 0, 0, 0, 0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        instance.pending = 1


# -- 23/24/25/26/60: the service stays pure ----------------------------


def _service_imports() -> set[str]:
    tree = ast.parse(_service_source())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def test_the_service_imports_no_qt() -> None:
    """Spec 23: an application service must not depend on the GUI toolkit."""

    modules = _service_imports()
    assert not [name for name in modules if "PySide6" in name]
    assert "us_quant.desktop" not in modules
    assert "us_quant.desktop_widgets" not in modules


def test_the_service_imports_no_paper_or_workflow_modules() -> None:
    """Spec 24: the Paper stack is a different domain and stays untouched."""

    forbidden = (
        "paper_trading_service",
        "paper_session",
        "paper_workflow",
        "ibkr_paper_orders",
        "ibkr_paper_gateway",
        "workflow_state",
        "risk",
        "auto_quant",
    )
    modules = _service_imports()
    for name in forbidden:
        assert not [
            module for module in modules if name in module
        ], name


def test_the_service_depends_only_on_the_allowed_modules() -> None:
    """Spec 25/26: the dependency arrow points one way only."""

    allowed = {
        "__future__",
        "dataclasses",
        "pathlib",
        "typing",
        "us_quant.history_queue",
        "us_quant.ibkr",
        "us_quant.public_history",
        "us_quant.universe",
    }
    assert _service_imports() <= allowed


def test_the_service_starts_no_threads() -> None:
    """Spec 60: threading belongs to the window's task controller."""

    tree = ast.parse(_service_source())
    banned = {"Thread", "QThread", "ThreadPoolExecutor", "asyncio"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not banned & {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert not banned & {
                alias.name for alias in node.names
            }
        elif isinstance(node, ast.Attribute):
            assert node.attr not in banned


def test_the_service_offers_no_cancel_mechanism() -> None:
    """Spec 61: the plain history download has no cancel contract to keep."""

    tree = ast.parse(_service_source())
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    assert not [name for name in names if "cancel" in name.lower()]
    assert "should_stop" not in _service_source()


# -- 27/28/29: the collaborators stay frozen ---------------------------


def test_the_collaborators_are_untouched() -> None:
    """Spec 27/28/29: queue, public history and universe are frozen."""

    for path in (
        "src/us_quant/history_queue.py",
        "src/us_quant/public_history.py",
        "src/us_quant/universe.py",
    ):
        base = _base_source(path)
        if base is None:
            pytest.fail(
                f"base commit {BASE_COMMIT} is unreachable; the "
                "byte-equivalence guard cannot run"
            )
        current = (_REPO_ROOT / path).read_text(encoding="utf-8")
        assert current == base, path


# Market Data v2: sibling modules this round legitimately rewrote.
# Declared so the guard can assert the delta exactly.
MARKET_DATA_V2_CHANGED_MODULES = (
    "src/us_quant/desktop_workers.py",
    "src/us_quant/desktop_widgets.py",
)

# Broker Account v2: modules whose bytes moved with this round.
BROKER_ACCOUNT_V2_CHANGED_MODULES = (
    "src/us_quant/desktop_settings.py",
    "src/us_quant/ibkr_paper_orders.py",
)

# Execution v2: the Paper execution stack moved out of the root package into
# the trading layer, so `desktop.py` builds its order store and execution
# service through the composition root.  Only the three modules this guard
# actually watches are listed here.
EXECUTION_V2_CHANGED_MODULES = (
    # Execution v2: retyped to the domain order types and the new ports.
    "src/us_quant/paper_trading_service.py",
    "src/us_quant/paper_session.py",
    "src/us_quant/ibkr_paper_gateway.py",
)

# Runtime v2B: the Paper workflow and the lease/phase guards moved into
# ``trading/runtime/``; the root copies are deleted.  Declared so the guard can
# still assert the delta exactly.
RUNTIME_V2B_CHANGED_MODULES = (
    "src/us_quant/paper_workflow.py",
    "src/us_quant/workflow_state.py",
)


def test_the_frozen_sibling_modules_are_untouched() -> None:
    """Spec 35/36/37: Paper, settings, workers and widgets are frozen."""

    changed = []
    for path in (
        "src/us_quant/paper_trading_service.py",
        "src/us_quant/paper_session.py",
        "src/us_quant/paper_workflow.py",
        "src/us_quant/ibkr_paper_orders.py",
        "src/us_quant/ibkr_paper_gateway.py",
        "src/us_quant/workflow_state.py",
        "src/us_quant/desktop_settings.py",
        "src/us_quant/desktop_credentials.py",
        "src/us_quant/desktop_settings_panel.py",
        "src/us_quant/credential_store.py",
        "src/us_quant/desktop_workers.py",
        "src/us_quant/desktop_widgets.py",
    ):
        base = _base_source(path)
        if base is None:
            pytest.fail(
                f"base commit {BASE_COMMIT} is unreachable; the "
                "byte-equivalence guard cannot run"
            )
        current_path = _REPO_ROOT / path
        if not current_path.exists():
            # A deleted frozen module only counts as declared if this
            # round said so; an undeclared deletion must still fail.
            changed.append(path)
            continue
        current = current_path.read_text(encoding="utf-8")
        if current != base:
            changed.append(path)

    # Exact, not a subset: the delta is the declared Market Data v2 surface
    # and nothing else.
    assert set(changed) == (
        set(MARKET_DATA_V2_CHANGED_MODULES)
        | set(BROKER_ACCOUNT_V2_CHANGED_MODULES)
        | set(EXECUTION_V2_CHANGED_MODULES)
        | set(RUNTIME_V2B_CHANGED_MODULES)
    )


# -- 58: the AutoQuant trio is frozen ---------------------------------


@pytest.mark.parametrize("name", FROZEN_METHODS)
def test_the_frozen_method_is_byte_identical(name: str) -> None:
    """Spec 34/58: these methods may not change in this step."""

    base = _base_source("src/us_quant/desktop.py")
    if base is None:
        pytest.fail(
            f"base commit {BASE_COMMIT} is unreachable; the "
            "byte-equivalence guard cannot run"
        )

    current = _desktop_source()
    base_cls = _main_window_class(ast.parse(base))
    current_cls = _main_window_class(ast.parse(current))

    base_body = _source_of(base_cls, _method(base_cls, name), base).replace(
        "\r\n", "\n"
    )
    current_body = _source_of(
        current_cls, _method(current_cls, name), current
    ).replace("\r\n", "\n")

    assert current_body == base_body


def test_only_the_declared_methods_changed() -> None:
    """Spec 33: the changed set must stay inside the declared surface.

    ``_settings_tab`` is included because step 11 rewrote it; the six history
    methods are this step's surface.  A method changing without being listed
    here is a scope violation.
    """

    base = _base_source("src/us_quant/desktop.py")
    if base is None:
        pytest.fail(
            f"base commit {BASE_COMMIT} is unreachable; the "
            "byte-equivalence guard cannot run"
        )

    current = _desktop_source()
    base_cls = _main_window_class(ast.parse(base))
    current_cls = _main_window_class(ast.parse(current))

    base_methods = {
        node.name: node
        for node in base_cls.body
        if isinstance(node, ast.FunctionDef)
    }
    current_methods = {
        node.name: node
        for node in current_cls.body
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
    )

    changed = []
    for name, node in base_methods.items():
        if name not in current_methods:
            continue
        before = _source_of(base_cls, node, base).replace("\r\n", "\n")
        after = _source_of(
            current_cls, current_methods[name], current
        ).replace("\r\n", "\n")
        if before != after:
            changed.append(name)

    declared = (
        set(REFACTORED_METHODS)
        | set(LATER_ROUND_METHODS)
        | set(LATER_ROUND_UI_METHODS)
    )
    declared = set(declared) | set(MARKET_DATA_V2_METHODS)
    declared = (
        set(declared)
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
    )
    assert set(changed) <= declared
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
    for name in REFACTORED_METHODS:
        if name != "__init__":
            assert name in changed, name


# -- 56/57: the scope guard is per method, not per module --------------


@pytest.mark.parametrize("name", HISTORY_METHODS)
def test_the_history_method_no_longer_builds_a_store(name: str) -> None:
    """Spec 31/56: these five methods go through the service instead."""

    cls = _main_window_class(ast.parse(_desktop_source()))
    names = _called_names(_method(cls, name))

    for forbidden in FORBIDDEN_CALLS:
        assert forbidden not in names, f"{name} still calls {forbidden}"


def test_the_module_wide_ban_is_deliberately_not_asserted() -> None:
    """Spec 30/31/32/57: AutoQuant keeps its direct store call on purpose.

    A test asserting ``desktop.py`` never mentions ``HistoryJobStore`` would
    force a rewrite of ``_auto_market_scan_finished``, which sits beside the
    Paper-adjacent candidate-preparation path and is frozen for this step.
    The guard is therefore scoped to the five history methods above.
    """

    source = _desktop_source()
    assert "HistoryJobStore" in source

    cls = _main_window_class(ast.parse(source))
    auto = _called_names(_method(cls, "_auto_market_scan_finished"))
    assert "HistoryJobStore" in auto
    assert "prioritized_research_symbols" in auto

    for path in (
        "src/us_quant/universe.py",
        "src/us_quant/history_queue.py",
    ):
        assert (_REPO_ROOT / path).exists()


def test_desktop_keeps_only_the_autoquant_store_import_not_history_runners(
) -> None:
    """The AutoQuant store stays, but the two queue runners belong to service."""

    source = _desktop_source()
    assert "HistoryJobStore" in source
    assert "run_history_queue" not in source
    assert "run_public_history_queue" not in source


# -- 49: the window owns the service -----------------------------------


def test_the_window_owns_a_configured_service(monkeypatch, tmp_path) -> None:
    """Spec 21/49: the service is built from the window's own paths."""

    window = _window(monkeypatch, tmp_path)
    try:
        assert isinstance(window.history_service, DesktopHistoryService)
        assert window.history_service.queue_path == window.queue_path
        assert window.history_service.data_root == window.data_root
    finally:
        window.deleteLater()


def test_building_the_window_does_not_create_the_queue_file(
    monkeypatch, tmp_path
) -> None:
    """Spec 22: startup I/O must not move into the service constructor.

    ``queue_path`` lives under the runtime root; if the service held a store
    the file would appear as a side effect of opening the window.
    """

    window = _window(monkeypatch, tmp_path)
    try:
        assert not pathlib.Path(window.queue_path).exists()
    finally:
        window.deleteLater()


# -- 50/51: _schedule_history wiring ----------------------------------


def test_schedule_history_hands_the_universe_to_the_service(
    monkeypatch, tmp_path
) -> None:
    """Spec 50: the result drives the log line and both refreshes."""

    window = _window(monkeypatch, tmp_path)
    calls: list = []
    try:
        monkeypatch.setattr(
            window.history_service,
            "schedule_universe",
            lambda universe: (
                calls.append(universe),
                HistoryScheduleResult(inserted=7, total=1234),
            )[1],
        )
        monkeypatch.setattr(
            window,
            "_publish_history_view",
            lambda: calls.append("publish_history"),
        )
        monkeypatch.setattr(
            window,
            "_refresh_market_scope_summary",
            lambda: calls.append("refresh_scope"),
        )
        logs: list[str] = []
        monkeypatch.setattr(window, "_log", logs.append)

        universe = object()
        window.universe = universe
        window._schedule_history()

        assert calls[0] is universe
        assert calls[1:] == ["publish_history", "refresh_scope"]
        assert len(logs) == 1
        assert "新增 7 个" in logs[0]
        assert "队列合计 1,234 个" in logs[0]
    finally:
        window.deleteLater()


def test_schedule_history_still_refuses_without_a_universe(
    monkeypatch, tmp_path
) -> None:
    """Spec 5/51: the ``None`` guard and its dialog stay in the window."""

    window = _window(monkeypatch, tmp_path)
    shown: list = []
    called: list = []
    try:
        monkeypatch.setattr(
            window.history_service,
            "schedule_universe",
            lambda universe: called.append(universe),
        )
        monkeypatch.setattr(
            "us_quant.desktop.QMessageBox.information",
            staticmethod(lambda *args, **kwargs: shown.append(args)),
        )

        window.universe = None
        window._schedule_history()

        assert called == []
        assert len(shown) == 1
        assert shown[0][1] == "缺少标的池"
    finally:
        window.deleteLater()


# -- 52: _run_history wiring ------------------------------------------


def test_run_history_wraps_the_service_as_a_task(
    monkeypatch, tmp_path
) -> None:
    """Spec 9/52: domain progress becomes the UI string, config stays live."""

    window = _window(monkeypatch, tmp_path)
    seen: dict = {}
    captured: list = []
    try:

        def fake_run_ibkr(config, *, maximum_jobs, progress=None):
            seen["config"] = config
            seen["maximum_jobs"] = maximum_jobs
            seen["progress"] = progress
            if progress is not None:
                progress(1, 25, "AAPL", "完成 1200 根")
            return {"completed": 1}

        monkeypatch.setattr(
            window.history_service, "run_ibkr", fake_run_ibkr
        )
        monkeypatch.setattr(
            window,
            "_start_task",
            lambda task, **kwargs: captured.append((task, kwargs)) or True,
        )
        window._run_history(37)

        assert len(captured) == 1
        task, kwargs = captured[0]
        assert kwargs["on_success"] == window._history_finished
        assert kwargs["resource_group"] == "history"

        ui: list[str] = []
        task(ui.append)

        assert seen["config"] is window.config.ibkr
        assert seen["maximum_jobs"] == 37
        assert ui == ["1/25 AAPL：完成 1200 根"]

        seen["progress"](1, 25, "AAPL", "完成 1200 根")
        assert ui[-1] == "1/25 AAPL：完成 1200 根"
    finally:
        window.deleteLater()


def test_run_history_reads_the_live_config_not_the_stream_copy(
    monkeypatch, tmp_path
) -> None:
    """Spec 7: ``self.config.ibkr`` is re-read per call.

    ``BrokerAccountApplication.config`` happens to be the *same object*
    right after construction, so ``is window.config.ibkr`` cannot tell the
    two apart.  A settings commit replaces ``self.config``, which is exactly
    when a stale copy would diverge -- and since Broker Account v2 the
    account application is the owner that must observe the replacement.
    """

    window = _window(monkeypatch, tmp_path)
    seen: dict = {}
    captured: list = []
    try:
        fresh_ibkr = replace(window.config.ibkr)
        window.config = replace(window.config, ibkr=fresh_ibkr)
        # The account application still holds the pre-replacement config:
        # the window's own attribute is not where the runtime reads from.
        assert window.broker_account.config is not fresh_ibkr

        def fake_run_ibkr(config, *, maximum_jobs, progress=None):
            seen["config"] = config
            return {"completed": 1}

        monkeypatch.setattr(
            window.history_service, "run_ibkr", fake_run_ibkr
        )
        monkeypatch.setattr(
            window,
            "_start_task",
            lambda task, **kwargs: captured.append((task, kwargs)) or True,
        )
        window._run_history(37)
        captured[0][0](lambda _message: None)

        assert seen["config"] is fresh_ibkr
    finally:
        window.deleteLater()


# -- 53: _run_public_history wiring -----------------------------------


def test_run_public_history_wraps_the_service_as_a_task(
    monkeypatch, tmp_path
) -> None:
    """Spec 53: the window must not reset failures itself any more."""

    window = _window(monkeypatch, tmp_path)
    seen: dict = {}
    captured: list = []
    try:

        def fake_run_public(*, maximum_jobs, progress=None):
            seen["maximum_jobs"] = maximum_jobs
            seen["progress"] = progress
            return {"completed": 2}

        def forbidden_reset_failed() -> int:
            raise AssertionError(
                "the window must not reset failures itself; run_public does"
            )

        monkeypatch.setattr(
            window.history_service, "run_public", fake_run_public
        )
        monkeypatch.setattr(
            window.history_service,
            "reset_failed",
            forbidden_reset_failed,
        )
        monkeypatch.setattr(
            window,
            "_start_task",
            lambda task, **kwargs: captured.append((task, kwargs)) or True,
        )
        window._run_public_history(9)

        task, kwargs = captured[0]
        assert kwargs["resource_group"] == "history"
        # Spec 14: the whole two-line copy, not just its first phrase.
        assert kwargs["start_message"] == (
            "备用免费日 K 下载中；只用于历史研究，"
            "不会替代 IBKR 实时行情…"
        )

        ui: list[str] = []
        task(ui.append)

        assert seen["maximum_jobs"] == 9
        seen["progress"](2, 9, "MSFT", "完成 300 根")
        assert ui == ["2/9 MSFT：完成 300 根"]
    finally:
        window.deleteLater()


# -- 54: _retry_failed wiring -----------------------------------------


def test_retry_failed_reports_the_service_count(
    monkeypatch, tmp_path
) -> None:
    """Spec 54: the count comes from the service and reaches the log."""

    window = _window(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(
            window.history_service, "reset_failed", lambda: 6
        )
        refreshed: list = []
        monkeypatch.setattr(
            window, "_publish_history_view", lambda: refreshed.append(1)
        )
        logs: list[str] = []
        monkeypatch.setattr(window, "_log", logs.append)

        window._retry_failed()

        assert refreshed == [1]
        assert logs == ["已将 6 个失败任务放回待处理队列。"]
    finally:
        window.deleteLater()


# -- 55: _refresh_queue_table wiring ----------------------------------


def test_refresh_queue_table_caps_the_table_not_the_snapshot(
    monkeypatch, tmp_path
) -> None:
    """Spec 16/17/20/55: the 2500-row cap belongs to the table."""

    window = _window(monkeypatch, tmp_path)
    try:
        jobs = tuple(
            type(
                "Job",
                (),
                {
                    "symbol": f"S{index:05d}",
                    "duration": "1 Y",
                    "priority": 0,
                    "status": "pending",
                    "attempts": 0,
                    "row_count": None,
                    "last_error": "",
                },
            )()
            for index in range(2600)
        )
        monkeypatch.setattr(
            window.history_service,
            "snapshot",
            lambda: HistoryQueueSnapshot(
                jobs=jobs,
                pending=2600,
                running=1,
                # Deliberately distinct and non-zero: with both at zero the
                # "completed" and "failed" slots are interchangeable and a
                # swap would go unnoticed.
                completed=7,
                failed=3,
            ),
        )

        window._publish_history_view()

        assert window.history_page.table.rowCount() == 2500
        summary = window.history_page.summary_label.text()
        assert "历史队列 2,600" in summary
        assert "待处理 2,600" in summary
        assert "完成 7" in summary
        assert "失败 3" in summary
        assert "表格仅显示前 2,500 条" in summary
    finally:
        window.deleteLater()


def test_refresh_queue_table_hides_the_hint_at_exactly_the_cap(
    monkeypatch, tmp_path
) -> None:
    """Spec 20: the hint is for *more than* the cap, not for any queue.

    Widening ``len(jobs) > 2500`` to something always true would still pass a
    test that only ever looks at an oversized queue.
    """

    window = _window(monkeypatch, tmp_path)
    try:
        jobs = tuple(_job(index, "pending") for index in range(2500))
        monkeypatch.setattr(
            window.history_service,
            "snapshot",
            lambda: HistoryQueueSnapshot(jobs, 2500, 0, 0, 0),
        )

        window._publish_history_view()

        assert window.history_page.table.rowCount() == 2500
        summary = window.history_page.summary_label.text()
        assert "历史队列 2,500" in summary
        assert "表格仅显示前 2,500 条" not in summary
    finally:
        window.deleteLater()


def test_refresh_queue_table_snapshots_once(monkeypatch, tmp_path) -> None:
    """Spec 16/55: one snapshot per refresh, reused for rows and summary."""

    window = _window(monkeypatch, tmp_path)
    calls: list[int] = []
    try:
        jobs = tuple(_job(index, "pending") for index in range(3))
        snapshot = HistoryQueueSnapshot(jobs, 3, 0, 0, 0)

        def counting_snapshot() -> HistoryQueueSnapshot:
            calls.append(1)
            return snapshot

        monkeypatch.setattr(
            window.history_service, "snapshot", counting_snapshot
        )

        window._publish_history_view()

        assert len(calls) == 1
        assert window.history_page.table.rowCount() == 3
        assert "历史队列 3" in window.history_page.summary_label.text()
    finally:
        window.deleteLater()


def test_refresh_queue_table_translates_statuses(
    monkeypatch, tmp_path
) -> None:
    """Spec 19: the Chinese status mapping stays in the window."""

    window = _window(monkeypatch, tmp_path)
    try:
        statuses = ("pending", "running", "completed", "failed", "unknown")
        jobs = tuple(
            type(
                "Job",
                (),
                {
                    "symbol": f"S{index}",
                    "duration": "1 Y",
                    "priority": index,
                    "status": status,
                    "attempts": index,
                    "row_count": 10 * index,
                    "last_error": "",
                },
            )()
            for index, status in enumerate(statuses)
        )
        monkeypatch.setattr(
            window.history_service,
            "snapshot",
            lambda: HistoryQueueSnapshot(jobs, 1, 1, 1, 1),
        )

        window._publish_history_view()

        rendered = {
            window.history_page.table.item(row, 0).text(): window.history_page.table.item(
                row, 3
            ).text()
            for row in range(window.history_page.table.rowCount())
        }
        assert rendered == {
            "S0": "待处理",
            "S1": "运行中",
            "S2": "完成",
            "S3": "失败",
            "S4": "unknown",
        }
    finally:
        window.deleteLater()
