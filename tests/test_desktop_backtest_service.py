"""Tests for the desktop backtest service and its wiring.

The service is pure application code: no Qt, no threads, no real daily bars
and no real run JSON.  Both domain calls are faked.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import textwrap
from decimal import Decimal
from pathlib import Path

import pytest

from us_quant.desktop import MainWindow
from us_quant.desktop_backtest_service import DesktopBacktestService
from us_quant.paths import STATE_ROOT_ENV

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Spec 55: this step's own guard uses this base commit.
BASE_COMMIT = "b2fd5ee38a9aab7b0476724a644c3efbb1523699"

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

SERVICE_MODULE = "src/us_quant/desktop_backtest_service.py"
SERVICE_PATH = _REPO_ROOT / SERVICE_MODULE

# Spec 54: the only two MainWindow methods this step may change.
REFACTORED_METHODS = ("__init__", "_run_backtest_workspace")

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
        "_refresh_backtest_strategy_combo",
        "_selected_auto_strategy_record",
        "_selected_shadow_strategy_record",
    }
)

# Spec 53: byte-identical to the base commit.
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
    "_backtest_workspace_finished",
    "_backtest_result_selection_changed",
    "_show_backtest_run",
    # ``_backtest_records`` is no longer frozen: Strategy v2 moved it onto
    # ``StrategySelectionService``, which is why it appears in
    # ``STRATEGY_V2_METHODS`` above.
    "_run_strategy_research",
    "_strategy_finished",
    "_start_task",
    "closeEvent",
)

# Spec 26/27/28/56/57: modules this step must not touch at all.
FROZEN_MODULES = (
    "src/us_quant/backtest_workspace.py",
    "src/us_quant/desktop_settings.py",
    "src/us_quant/desktop_credentials.py",
    "src/us_quant/desktop_settings_panel.py",
    "src/us_quant/desktop_history_service.py",
    "src/us_quant/desktop_universe_service.py",
    "src/us_quant/desktop_market_scan_service.py",
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
    "src/us_quant/credential_store.py",
    "src/us_quant/user_settings.py",
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


def _imported_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _code_without_docstrings(source: str) -> str:
    """Return the source with every docstring replaced by a blank line.

    Text guards that scan raw source fire on their own documentation (a
    module explaining that it uses *no* threads mentions threads).  Strip
    docstrings first so the guard reads code, not prose.
    """

    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            for lineno in range(first.lineno, first.end_lineno + 1):
                docstrings.add(lineno)

    return "\n".join(
        "" if index in docstrings else line
        for index, line in enumerate(source.splitlines(), start=1)
    )


class _Recorder:
    """A run/save double that records calls and can raise on a given index."""

    def __init__(self, results=None, raises: dict[int, Exception] | None = None):
        self.results = list(results or [])
        self.raises = raises or {}
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs):
        index = len(self.calls)
        self.calls.append((args, kwargs))
        if index in self.raises:
            raise self.raises[index]
        if index < len(self.results):
            return self.results[index]
        return f"RUN{index}"


@pytest.fixture
def domain(monkeypatch):
    """Patch the service module's two domain imports."""

    import us_quant.desktop_backtest_service as module

    run = _Recorder()
    save = _Recorder()
    monkeypatch.setattr(module, "run_backtest", run)
    monkeypatch.setattr(module, "save_backtest_run", save)
    return run, save


def _service(tmp_path) -> DesktopBacktestService:
    return DesktopBacktestService(
        data_root=tmp_path / "data",
        fallback_data_root=tmp_path / "bundled",
        output_root=tmp_path / "results" / "backtests",
    )


def _requests(count: int) -> list[object]:
    return [object() for _ in range(count)]


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _window(monkeypatch, tmp_path) -> MainWindow:
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path / "state"))
    _qapp()
    window = MainWindow()
    _qapp().processEvents()
    return window


# -- 2/30/31: the constructor ------------------------------------------


def test_the_constructor_performs_no_io(tmp_path) -> None:
    """Spec 2/30: constructing the service must not touch the filesystem."""

    data_root = tmp_path / "data"
    fallback = tmp_path / "bundled"
    output = tmp_path / "results" / "backtests"

    DesktopBacktestService(
        data_root=data_root,
        fallback_data_root=fallback,
        output_root=output,
    )

    assert not data_root.exists()
    assert not fallback.exists()
    assert not output.exists()
    assert not output.parent.exists()


def test_the_constructor_body_contains_no_io_call() -> None:
    """Spec 2/30: no mkdir / exists / open / read in ``__init__``.

    A bare ``root.exists()`` is behaviourally invisible, so the no-I/O rule
    needs a structural guard rather than a behavioural one.
    """

    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    service = _class_named(tree, "DesktopBacktestService")
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
        "write_text",
        "write_bytes",
        "glob",
        "iterdir",
        "resolve",
        "touch",
        "run_backtest",
        "save_backtest_run",
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


def test_the_constructor_stores_exactly_three_roots(tmp_path) -> None:
    """Spec 2/31: no extra attribute, no mutable runtime state."""

    service = _service(tmp_path)

    assert sorted(vars(service)) == [
        "data_root",
        "fallback_data_root",
        "output_root",
    ]


def test_the_constructor_keeps_the_paths_it_was_given(tmp_path) -> None:
    """Spec 31: identity, not a re-wrapped or resolved copy."""

    data_root = tmp_path / "data"
    fallback = tmp_path / "bundled"
    output = tmp_path / "results" / "backtests"

    service = DesktopBacktestService(
        data_root=data_root,
        fallback_data_root=fallback,
        output_root=output,
    )

    assert service.data_root is data_root
    assert service.fallback_data_root is fallback
    assert service.output_root is output


# -- 6/8/9/32/33/34: ordering and progress -----------------------------


def test_a_single_request_runs_progress_then_run_then_save(
    tmp_path, domain
) -> None:
    """Spec 6/32: the exact three-step order."""

    import us_quant.desktop_backtest_service as module

    order: list[str] = []
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        module, "run_backtest", lambda *a, **k: order.append("run") or "RUN"
    )
    monkeypatch.setattr(
        module,
        "save_backtest_run",
        lambda *a, **k: order.append("save") or "PATH",
    )
    try:
        service = _service(tmp_path)
        service.run(
            _requests(1),
            on_progress=lambda *a: order.append("progress"),
        )
    finally:
        monkeypatch.undo()

    assert order == ["progress", "run", "save"]


def test_three_requests_run_in_order(tmp_path, domain) -> None:
    """Spec 9/33: strictly serial, one request at a time."""

    import us_quant.desktop_backtest_service as module

    monkeypatch = pytest.MonkeyPatch()
    order: list[str] = []
    requests = _requests(3)
    labels = {id(request): label for request, label in zip(requests, "ABC")}

    monkeypatch.setattr(
        module,
        "run_backtest",
        lambda request, **k: order.append(f"run {labels[id(request)]}") or "RUN",
    )
    monkeypatch.setattr(
        module,
        "save_backtest_run",
        lambda run, **k: order.append("save") or "PATH",
    )
    try:
        service = _service(tmp_path)
        service.run(
            requests,
            on_progress=lambda index, total, request: order.append(
                f"progress {labels[id(request)]}"
            ),
        )
    finally:
        monkeypatch.undo()

    assert order == [
        "progress A",
        "run A",
        "save",
        "progress B",
        "run B",
        "save",
        "progress C",
        "run C",
        "save",
    ]


def test_progress_receives_the_index_total_and_the_request_itself(
    tmp_path, domain
) -> None:
    """Spec 34: index, total, and the very same request object."""

    service = _service(tmp_path)
    requests = _requests(3)
    seen: list[tuple] = []

    service.run(requests, on_progress=lambda *args: seen.append(args))

    assert len(seen) == 3
    assert seen[1][0] == 2
    assert seen[1][1] == 3
    assert seen[1][2] is requests[1]
    assert seen[2][0] == 3
    assert seen[2][1] == 3


def test_progress_is_emitted_before_that_request_runs(
    tmp_path, domain
) -> None:
    """Spec 8: progress first, never after the run."""

    import us_quant.desktop_backtest_service as module

    run, _save = domain
    monkeypatch = pytest.MonkeyPatch()
    snapshots: list = []
    run_count = [0]

    def fake_run(request, **kwargs):
        run_count[0] += 1
        snapshots.append(("run", run_count[0]))
        return "RUN"

    monkeypatch.setattr(module, "run_backtest", fake_run)
    try:
        service = _service(tmp_path)
        service.run(
            _requests(2),
            on_progress=lambda index, total, request: snapshots.append(
                ("progress", index)
            ),
        )
    finally:
        monkeypatch.undo()

    # Each progress event is recorded before its own run call, and no run
    # happens before the first progress event.
    assert snapshots == [
        ("progress", 1),
        ("run", 1),
        ("progress", 2),
        ("run", 2),
    ]


def test_run_works_without_an_observer(tmp_path, domain) -> None:
    """Spec 35: no callback must not change the business path."""

    run, save = domain
    service = _service(tmp_path)

    result = service.run(_requests(2))

    assert len(result) == 2
    assert len(run.calls) == 2
    assert len(save.calls) == 2


# -- 11/12/36/37: the domain call arguments ----------------------------


def test_run_backtest_receives_identity_arguments(tmp_path, domain) -> None:
    """Spec 11/36: request and both roots by identity, exact keyword set.

    Three requests, so "the loop passes *this* request" is actually
    distinguishable from "the loop always passes ``requests[0]``".
    """

    run, _save = domain
    service = _service(tmp_path)
    requests = _requests(3)

    service.run(requests)

    assert len(run.calls) == 3
    for index, (args, kwargs) in enumerate(run.calls):
        assert args[0] is requests[index]
        assert kwargs["data_root"] is service.data_root
        assert kwargs["fallback_data_root"] is service.fallback_data_root
        assert set(kwargs) == {"data_root", "fallback_data_root"}


def test_save_receives_the_run_and_the_output_root(tmp_path, domain) -> None:
    """Spec 12/37: the run by identity, the configured output root."""

    run, save = domain
    service = _service(tmp_path)
    result = object()
    run.results = [result]

    service.run(_requests(1))

    args, kwargs = save.calls[0]
    assert args[0] is result
    assert kwargs["output_root"] is service.output_root
    assert set(kwargs) == {"output_root"}


def test_save_is_called_once_per_successful_run(tmp_path, domain) -> None:
    """Spec 37: one save per run, no batching."""

    run, save = domain
    service = _service(tmp_path)

    service.run(_requests(4))

    assert len(run.calls) == 4
    assert len(save.calls) == 4


# -- 13/14/38: the return value ----------------------------------------


def test_the_result_is_the_run_objects_by_identity(tmp_path, domain) -> None:
    """Spec 13/14/38: the saved Path never replaces the domain result."""

    run, save = domain
    service = _service(tmp_path)
    run_a, run_b = object(), object()
    run.results = [run_a, run_b]
    save.results = ["PATH A", "PATH B"]

    result = service.run(_requests(2))

    assert result == (run_a, run_b)
    assert result[0] is run_a
    assert result[1] is run_b
    assert result != ("PATH A", "PATH B")


def test_the_result_is_a_tuple(tmp_path, domain) -> None:
    """Spec 13: ``tuple[BacktestRun, ...]``, not a list."""

    service = _service(tmp_path)

    result = service.run(_requests(2))

    assert isinstance(result, tuple)


def test_each_run_is_appended_before_the_next_request(
    tmp_path, domain
) -> None:
    """Spec 6: append happens per request, not once at the end.

    The append is observed through the save/run interleaving *and* through
    a source-level guard: a mutation that moves ``runs.append`` before the
    save is otherwise invisible, because the tuple is identical either way.
    """

    import us_quant.desktop_backtest_service as module

    monkeypatch = pytest.MonkeyPatch()
    order: list[str] = []
    monkeypatch.setattr(
        module, "run_backtest", lambda *a, **k: order.append("run") or "RUN"
    )
    monkeypatch.setattr(
        module,
        "save_backtest_run",
        lambda *a, **k: order.append("save") or "PATH",
    )
    try:
        service = _service(tmp_path)
        service.run(
            _requests(2),
            on_progress=lambda *a: order.append("progress"),
        )
    finally:
        monkeypatch.undo()

    assert order == [
        "progress",
        "run",
        "save",
        "progress",
        "run",
        "save",
    ]


def test_the_append_comes_after_the_save_in_the_source() -> None:
    """Spec 6/13: the append is the last step of a request's body.

    The returned tuple cannot distinguish ``append`` from ``save`` order,
    so this pins the statement order directly.
    """

    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    service = _class_named(tree, "DesktopBacktestService")
    run = next(
        node
        for node in service.body
        if isinstance(node, ast.FunctionDef) and node.name == "run"
    )
    loop = next(
        node for node in ast.walk(run) if isinstance(node, ast.For)
    )

    statements = [
        type(node).__name__ for node in loop.body
    ]
    assert statements == [
        "If",
        "Assign",
        "Expr",
        "Expr",
    ]
    save = loop.body[2]
    append = loop.body[3]
    assert isinstance(save.value.func, ast.Name)
    assert save.value.func.id == "save_backtest_run"
    assert isinstance(append.value.func, ast.Attribute)
    assert append.value.func.attr == "append"


# -- 15/16/17/39/40: failure and partial-commit semantics --------------


def test_a_failing_run_stops_the_batch(tmp_path, domain) -> None:
    """Spec 15/39: A completes, B's run raises, B is not saved, C never runs."""

    run, save = domain
    service = _service(tmp_path)
    error = ValueError("bad data")
    run.raises = {1: error}

    with pytest.raises(ValueError) as caught:
        service.run(_requests(3))

    assert caught.value is error
    assert len(run.calls) == 2
    assert len(save.calls) == 1


def test_a_failing_save_stops_the_batch(tmp_path, domain) -> None:
    """Spec 16/40: A saved, B's save raises, C never runs."""

    run, save = domain
    service = _service(tmp_path)
    error = OSError("disk full")
    save.raises = {1: error}

    with pytest.raises(OSError) as caught:
        service.run(_requests(3))

    assert caught.value is error
    assert len(run.calls) == 2
    assert len(save.calls) == 2


def test_a_failing_save_leaves_the_earlier_runs_saved(
    tmp_path, domain
) -> None:
    """Spec 17: partial commit is the contract; nothing is rolled back.

    A finished, saved run must stay on disk -- the batch is not a
    transaction, and the service must not try to undo A.  The fake save
    writes a real file so the assertion is about the filesystem, not about
    how many times a function was called.
    """

    import us_quant.desktop_backtest_service as module

    monkeypatch = pytest.MonkeyPatch()
    written: list[Path] = []
    error = OSError("disk full")
    calls: list[int] = []

    def fake_save(run, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise error
        path = kwargs["output_root"] / f"run-{len(calls)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        written.append(path)
        return path

    monkeypatch.setattr(module, "save_backtest_run", fake_save)
    monkeypatch.setattr(module, "run_backtest", lambda *a, **k: object())
    try:
        service = _service(tmp_path)
        with pytest.raises(OSError) as caught:
            service.run(_requests(3))
    finally:
        monkeypatch.undo()

    assert caught.value is error
    assert len(written) == 1
    # A's file is still there: no rollback, no cleanup, no transaction.
    assert written[0].exists()


def test_the_service_defines_no_error_types() -> None:
    """Spec 18: no ``DesktopBacktestError`` / ``BacktestBatchError``."""

    source = SERVICE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    classes = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }

    assert classes == {"DesktopBacktestService"}
    assert "PartialBacktestFailure" not in source


def test_the_service_never_swallows_an_exception() -> None:
    """Spec 41: no ``ExceptHandler`` inside the service class."""

    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    service = _class_named(tree, "DesktopBacktestService")

    handlers = [
        node
        for node in ast.walk(service)
        if isinstance(node, ast.ExceptHandler)
    ]
    assert handlers == []


def test_a_failed_request_is_not_in_the_result(tmp_path, domain) -> None:
    """Spec 16: the failing request never reaches the returned tuple."""

    run, save = domain
    service = _service(tmp_path)
    run_a = object()
    run.results = [run_a]
    error = OSError("disk full")
    save.raises = {0: error}

    with pytest.raises(OSError):
        service.run(_requests(1))

    # Nothing was returned at all; the run that failed to save is not
    # observable as a completed result.
    assert len(save.calls) == 1


# -- 19/42: empty requests ---------------------------------------------


def test_an_empty_batch_returns_an_empty_tuple(tmp_path, domain) -> None:
    """Spec 19/42: no invented validation, natural behaviour only."""

    run, save = domain
    service = _service(tmp_path)
    seen: list = []

    result = service.run((), on_progress=lambda *a: seen.append(a))

    assert result == ()
    assert isinstance(result, tuple)
    assert run.calls == []
    assert save.calls == []
    assert seen == []


# -- 10/43: no threading -----------------------------------------------


def test_the_service_starts_no_threads() -> None:
    """Spec 10/43: serial execution inside the caller's thread.

    Docstrings are stripped before scanning: the prose here legitimately
    mentions the concurrency primitives the code must not use, and a guard
    that cannot tell code from commentary fires on its own documentation.
    """

    source = _code_without_docstrings(SERVICE_PATH.read_text(encoding="utf-8"))

    for forbidden in (
        "QThread",
        "Thread(",
        "ThreadPoolExecutor",
        "asyncio",
        "multiprocessing",
        "concurrent.futures",
        "import threading",
        "from threading",
    ):
        assert forbidden not in source, forbidden


# -- 24/25/44: the service's own boundaries ----------------------------


def test_the_service_depends_only_on_the_allowed_modules() -> None:
    """Spec 24: dependency set equality, not a forbidden-name scan."""

    source = SERVICE_PATH.read_text(encoding="utf-8")

    assert _module_imports(source) == {
        "__future__",
        "collections.abc",
        "pathlib",
        "us_quant.backtest_workspace",
    }


def test_the_service_imports_no_gui_or_sibling_services() -> None:
    """Spec 23/24: no Qt, no window, no other desktop service."""

    imported = _module_imports(SERVICE_PATH.read_text(encoding="utf-8"))

    for forbidden in (
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtWidgets",
        "us_quant.desktop",
        "us_quant.desktop_workers",
        "us_quant.desktop_widgets",
        "us_quant.runtime_supervisor",
        "us_quant.market_data_service",
        "us_quant.user_settings",
        "us_quant.credential_store",
    ):
        assert forbidden not in imported, forbidden


def test_the_service_imports_no_paper_or_auto_quant_modules() -> None:
    """Spec 25: the backtest service is pure research application code."""

    imported = _module_imports(SERVICE_PATH.read_text(encoding="utf-8"))

    for forbidden in (
        "us_quant.paper_trading_service",
        "us_quant.paper_session",
        "us_quant.paper_workflow",
        "us_quant.ibkr_paper_orders",
        "us_quant.ibkr_paper_gateway",
        "us_quant.workflow_state",
        "us_quant.auto_quant",
        "us_quant.risk",
    ):
        assert forbidden not in imported, forbidden


def test_the_service_carries_no_presentation_copy() -> None:
    """Spec 7/44: no Chinese progress or dialog text in the service."""

    source = SERVICE_PATH.read_text(encoding="utf-8")

    for text in ("回测", "正在运行", "任务忙", "日期无效", "没有可运行版本"):
        assert text not in source, text


def test_the_service_knows_no_widget_names() -> None:
    """Spec 23: no widget attribute names leak into the service."""

    source = SERVICE_PATH.read_text(encoding="utf-8")

    for name in (
        "backtest_comparison_table",
        "backtest_run_button",
        "backtest_compare_button",
        "QTableWidget",
        "QTableWidgetItem",
        "QMessageBox",
        "MetricCard",
        "PriceChart",
    ):
        assert name not in source, name


def test_the_service_holds_no_mutable_runtime_state() -> None:
    """Spec 2: only the three roots are stored."""

    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    service = _class_named(tree, "DesktopBacktestService")

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
        "output_root",
    }


def test_the_service_offers_no_cancellation_api() -> None:
    """Spec 10: cancellation belongs to the window's TaskThread."""

    for name in ("cancel", "stop", "request_stop"):
        assert not hasattr(DesktopBacktestService, name), name


# -- 45: the window owns the service -----------------------------------


def test_the_window_owns_the_service(monkeypatch, tmp_path) -> None:
    """Spec 45: one service, built over the window's own roots."""

    window = _window(monkeypatch, tmp_path)
    try:
        assert isinstance(
            window.backtest_service, DesktopBacktestService
        )
        assert window.backtest_service.data_root is window.data_root
        assert (
            window.backtest_service.fallback_data_root
            is window.bundled_data_root
        )
        assert window.backtest_service.output_root == (
            window.paths.research_results_root / "backtests"
        )
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
        assert window.backtest_service.data_root is window.data_root
        assert calls == [1]
    finally:
        window.deleteLater()


# -- 46/47/48: the window's imports and method body --------------------


def test_the_run_loop_no_longer_calls_the_domain_directly() -> None:
    """Spec 46: only the workspace method, not the whole file."""

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )
    method = _find_method(source, "_run_backtest_workspace")

    assert "run_backtest(" not in method
    assert "save_backtest_run(" not in method
    assert "self.backtest_service.run(" in method


def test_the_window_no_longer_imports_the_run_helpers() -> None:
    """Spec 47: after this step nothing else referenced them."""

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )
    imported = _imported_names(source)

    assert "run_backtest" not in imported
    assert "save_backtest_run" not in imported


def test_the_window_keeps_the_request_and_run_types() -> None:
    """Spec 48: the window still builds requests and types the result."""

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )
    imported = _imported_names(source)

    assert "BacktestRequest" in imported
    assert "BacktestRun" in imported
    assert "STRATEGY_SPECS" in imported


def test_the_domain_module_still_owns_the_run_helpers() -> None:
    """Spec 26: the real calls live only in the service module now."""

    service = SERVICE_PATH.read_text(encoding="utf-8")

    assert "run_backtest(" in service
    assert "save_backtest_run(" in service
    assert "us_quant.backtest_workspace" in _module_imports(service)


# -- 49/50/51: the window's validation, requests and progress ----------


def _capture_task(window, monkeypatch):
    """Run ``_run_backtest_workspace`` and hand back the started task.

    Dialogs are stubbed because a modal ``QMessageBox`` blocks forever
    under ``QT_QPA_PLATFORM=offscreen``.
    """

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)

    captured: list = []
    monkeypatch.setattr(
        window,
        "_start_task",
        lambda task, **kwargs: captured.append((task, kwargs)) or True,
    )
    monkeypatch.setattr(
        window.backtest_run_button, "setEnabled", lambda _v: None
    )
    monkeypatch.setattr(
        window.backtest_compare_button, "setEnabled", lambda _v: None
    )
    window._run_backtest_workspace(False)
    return captured[0] if captured else None


def test_a_busy_backtest_worker_blocks_the_run(monkeypatch, tmp_path) -> None:
    """Spec 49: an already-running backtest worker means "busy"."""

    from PySide6.QtWidgets import QMessageBox

    window = _window(monkeypatch, tmp_path)
    try:
        class _Worker:
            resource_group = "backtest"

            def isRunning(self) -> bool:
                return True

        window.workers = [_Worker()]

        shown: list[tuple] = []
        monkeypatch.setattr(
            QMessageBox, "information", lambda *a: shown.append(a)
        )
        started: list = []
        monkeypatch.setattr(
            window, "_start_task", lambda *a, **k: started.append(a) or True
        )
        ran: list = []
        monkeypatch.setattr(
            window.backtest_service,
            "run",
            lambda *a, **k: ran.append(a) or (),
        )

        window._run_backtest_workspace(False)

        assert started == []
        assert ran == []
        assert shown[0][1] == "任务忙"
        assert shown[0][2] == "请等待当前数据或研究任务完成后再运行回测。"
    finally:
        window.deleteLater()


def test_no_records_blocks_the_run(monkeypatch, tmp_path) -> None:
    """Spec 49: no matching research versions means "nothing to run"."""

    from PySide6.QtWidgets import QMessageBox

    window = _window(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(window, "_backtest_records", lambda _all: [])

        shown: list[tuple] = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *a: shown.append(a)
        )
        started: list = []
        monkeypatch.setattr(
            window, "_start_task", lambda *a, **k: started.append(a) or True
        )
        ran: list = []
        monkeypatch.setattr(
            window.backtest_service,
            "run",
            lambda *a, **k: ran.append(a) or (),
        )

        window._run_backtest_workspace(False)

        assert started == []
        assert ran == []
        assert shown[0][1] == "没有可运行版本"
        assert shown[0][2] == "策略目录中没有与回测工厂匹配的研究版本。"
    finally:
        window.deleteLater()


def test_an_invalid_date_range_blocks_the_run(monkeypatch, tmp_path) -> None:
    """Spec 49: start after end means "invalid dates"."""

    from PySide6.QtWidgets import QMessageBox

    window = _window(monkeypatch, tmp_path)
    try:
        record = _Record()
        monkeypatch.setattr(
            window, "_backtest_records", lambda _all: [record]
        )
        # Start strictly after end, whatever "today" happens to be.
        window.backtest_start.setDate(
            window.backtest_end.date().addDays(10)
        )

        shown: list[tuple] = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *a: shown.append(a)
        )
        started: list = []
        monkeypatch.setattr(
            window, "_start_task", lambda *a, **k: started.append(a) or True
        )
        ran: list = []
        monkeypatch.setattr(
            window.backtest_service,
            "run",
            lambda *a, **k: ran.append(a) or (),
        )

        window._run_backtest_workspace(False)

        assert started == []
        assert ran == []
        assert shown[0][1] == "日期无效"
        assert shown[0][2] == "起始日期不能晚于结束日期。"
    finally:
        window.deleteLater()


class _Record:
    """A strategy record stand-in with the fields the form reads."""

    strategy_id = "breakout"
    version_id = "v1"
    parameter_hash = "p-hash"
    code_hash = "c-hash"
    parameters = {"lookback": 20}


def test_the_requests_come_from_the_form_controls(
    monkeypatch, tmp_path
) -> None:
    """Spec 50: the window still converts controls into domain requests."""

    window = _window(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(
            window, "_backtest_records", lambda _all: [_Record(), _Record()]
        )
        window.backtest_symbol.setText("aapl")
        window.backtest_capital.setValue(2500)
        window.backtest_weight.setValue(30)
        window.backtest_per_share_cost.setValue(0.005)
        window.backtest_minimum_cost.setValue(1.25)
        window.backtest_slippage.setValue(4)

        captured = _capture_task(window, monkeypatch)
        assert captured is not None
        task, kwargs = captured

        seen: list = []
        monkeypatch.setattr(
            window.backtest_service,
            "run",
            lambda requests, **k: seen.append(requests) or (),
        )

        task(lambda _message: None)

        requests = seen[0]
        assert len(requests) == 2
        request = requests[0]
        assert request.strategy_id == "breakout"
        assert request.strategy_version_id == "v1"
        assert request.parameter_hash == "p-hash"
        assert request.code_hash == "c-hash"
        assert request.symbol == "AAPL"
        assert isinstance(request.initial_equity, Decimal)
        assert request.initial_equity == Decimal("2500")
        assert isinstance(request.target_weight, Decimal)
        assert request.target_weight == Decimal("30") / Decimal("100")
        assert isinstance(request.per_share_commission, Decimal)
        assert request.per_share_commission == Decimal("0.005")
        assert isinstance(request.minimum_commission, Decimal)
        assert request.minimum_commission == Decimal("1.25")
        assert isinstance(request.slippage_bps, Decimal)
        assert request.slippage_bps == Decimal("4")
        assert kwargs["resource_group"] == "backtest"
    finally:
        window.deleteLater()


def test_the_progress_copy_is_verbatim(monkeypatch, tmp_path) -> None:
    """Spec 51: ``回测 i/n：<strategy_id> <symbol>``, exactly."""

    window = _window(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(
            window, "_backtest_records", lambda _all: [_Record()]
        )
        captured = _capture_task(window, monkeypatch)
        assert captured is not None
        task, _kwargs = captured

        seen: list[str] = []

        def fake_run(requests, *, on_progress=None):
            on_progress(2, 3, _FakeRequest())
            return ()

        monkeypatch.setattr(window.backtest_service, "run", fake_run)

        task(seen.append)

        assert seen == ["回测 2/3：breakout AAPL"]
    finally:
        window.deleteLater()


class _FakeRequest:
    strategy_id = "breakout"
    symbol = "AAPL"


def test_the_start_task_contract_is_unchanged(monkeypatch, tmp_path) -> None:
    """Spec 21: on_success, start message, resource group, exact keyword set."""

    window = _window(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(
            window, "_backtest_records", lambda _all: [_Record(), _Record()]
        )
        captured = _capture_task(window, monkeypatch)
        assert captured is not None
        _task, kwargs = captured

        assert kwargs["on_success"] == window._backtest_workspace_finished
        assert kwargs["start_message"] == "正在运行 2 个版本绑定回测…"
        assert kwargs["resource_group"] == "backtest"
        assert set(kwargs) == {
            "on_success",
            "start_message",
            "resource_group",
        }
    finally:
        window.deleteLater()


def test_the_run_buttons_are_disabled_before_the_task_starts(
    monkeypatch, tmp_path
) -> None:
    """Spec 20: both buttons are disabled, in order, before _start_task."""

    window = _window(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(
            window, "_backtest_records", lambda _all: [_Record()]
        )

        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
        monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)

        order: list[str] = []
        monkeypatch.setattr(
            window.backtest_run_button,
            "setEnabled",
            lambda _v: order.append("run.setEnabled"),
        )
        monkeypatch.setattr(
            window.backtest_compare_button,
            "setEnabled",
            lambda _v: order.append("compare.setEnabled"),
        )
        monkeypatch.setattr(
            window,
            "_start_task",
            lambda task, **kwargs: order.append("start_task") or True,
        )

        window._run_backtest_workspace(False)

        assert order == [
            "run.setEnabled",
            "compare.setEnabled",
            "start_task",
        ]
    finally:
        window.deleteLater()


def test_the_window_does_not_catch_service_failures(
    monkeypatch, tmp_path
) -> None:
    """Spec 52: no try/except in the workspace method."""

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )
    method = _find_method(source, "_run_backtest_workspace")

    tree = ast.parse(textwrap.dedent(method))
    assert [
        node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
    ] == []


# -- 53/54/55/56/57: the scope guards ----------------------------------


@pytest.mark.parametrize("name", FROZEN_METHODS)
def test_the_frozen_method_is_byte_identical(name: str) -> None:
    """Spec 53: these methods may not change in this step."""

    base = _require_base("src/us_quant/desktop.py")
    current = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )

    assert _find_method(current, name) == _find_method(base, name), name


def test_only_the_declared_methods_changed() -> None:
    """Spec 54: the declared surface is ``__init__`` and the workspace run.

    The guard is relative to this step's base commit.  Every later round
    appends the methods it declares to ``LATER_ROUND_METHODS`` and ships its
    own guard against its own base commit.
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
    assert "_run_backtest_workspace" in changed


def test_the_other_frozen_modules_are_untouched() -> None:
    """Spec 26/27/28/56/57: the neighbours do not move."""

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
    )


def test_strategy_research_is_frozen() -> None:
    """Spec 53: the Strategy Research path is explicitly untouched."""

    base = _require_base("src/us_quant/desktop.py")
    current = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )

    for name in ("_run_strategy_research", "_strategy_finished"):
        assert _find_method(current, name) == _find_method(base, name), name
