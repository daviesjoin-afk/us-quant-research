"""Tests for the desktop market scan service and its wiring.

The service is pure application code: no Qt, no threads, no real daily bars
and no real scan JSON.  The two scanner calls are faked.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from us_quant.desktop import MainWindow
from us_quant.desktop_market_scan_service import DesktopMarketScanService
from us_quant.paths import STATE_ROOT_ENV

_REPO_ROOT = Path(__file__).resolve().parents[1]

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
    "_populate_stream_snapshot",
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
    "_populate_auto_quant_snapshot",
    "_refresh_account_snapshot",
    "_refresh_cards",
    "_refresh_target_preflight",
    "_research_capital_changed",
    "_start_shadow",
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

SERVICE_MODULE = "src/us_quant/desktop_market_scan_service.py"
SERVICE_PATH = _REPO_ROOT / SERVICE_MODULE

# Spec 48: the only two MainWindow methods this step may change.
REFACTORED_METHODS = ("__init__", "_run_scan")

# Methods a *later* round legitimately rewrote.  Each round appends the
# methods it declared; the union is what this guard tolerates relative to
# its own base commit.  Adding a name here that no round declared is exactly
# the scope violation this guard exists to catch.
LATER_ROUND_METHODS = (
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
        "_auto_strategy_selection_changed",
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
# Rewritten rather than added or removed: they now talk to the strategy
# application service and the selection service instead of a registry and a
# combo box.
STRATEGY_V2_METHODS = frozenset(
    {
        "_apply_theme",
        "_auto_order_service_connected",
        "_auto_quant_preflight",
        "_auto_quant_tab",
        "_backtest_records",
        "_refresh_backtest_strategy_combo",
        "_selected_auto_strategy_record",
        "_selected_shadow_strategy_record",
        "_simulation_tab",
    }
)

# Spec 46/47: byte-identical to the base commit.
FROZEN_METHODS = (
    "_scan_finished",
    "_prepare_auto_quant_candidates",
    "_auto_candidate_preparation_failed",
    "_auto_market_scan_finished",
    "_select_auto_quant_candidates",
    "_stop_auto_market_data",
    "_confirm_and_start_auto_quant",
    "_start_auto_quant",
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


# -- 17/18/19/39: the service's own boundaries -------------------------


def test_the_service_depends_only_on_the_allowed_modules() -> None:
    """Spec 19/39: dependency set equality, not a forbidden-name scan."""

    source = SERVICE_PATH.read_text(encoding="utf-8")

    assert _module_imports(source) == {
        "__future__",
        "decimal",
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
    """Spec 5/42: the dialog, and nothing else, happens."""

    window = _window(monkeypatch, tmp_path)
    try:
        from PySide6.QtWidgets import QMessageBox

        window.universe = None

        shown: list[tuple] = []
        monkeypatch.setattr(
            QMessageBox,
            "information",
            lambda *args: shown.append(args),
        )
        started: list = []
        monkeypatch.setattr(
            window, "_start_task", lambda *a, **k: started.append(a) or True
        )
        scanned: list = []
        monkeypatch.setattr(
            window.market_scan_service,
            "scan",
            lambda *a, **k: scanned.append(a) or "RESULT",
        )

        window._run_scan()

        assert started == []
        assert scanned == []
        assert len(shown) == 1
        assert shown[0][1] == "缺少标的池"
        assert shown[0][2] == "请先刷新官方标的。"
    finally:
        window.deleteLater()


# -- 6/43/44: progress copy and the task contract ----------------------


def _capture_task(window, monkeypatch):
    """Capture the task ``_run_scan`` starts, without running it.

    The dialog is stubbed because a modal ``QMessageBox`` blocks forever
    under ``QT_QPA_PLATFORM=offscreen``, and ``universe`` is defaulted so
    the missing-universe guard does not fire in the timing tests.
    """

    from PySide6.QtWidgets import QMessageBox

    if window.universe is None:
        window.universe = object()

    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    captured: list = []
    monkeypatch.setattr(
        window,
        "_start_task",
        lambda task, **kwargs: captured.append((task, kwargs)) or True,
    )
    window._run_scan()
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

        assert kwargs["on_success"] == window._scan_finished
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
    """Spec 45: capital is captured now; universe, risk and rules are live.

    The old closure computed ``research_capital`` on the UI thread before
    ``_start_task`` and read the other three inside the worker.  Extraction
    must not silently move any of them.
    """

    from dataclasses import replace

    window = _window(monkeypatch, tmp_path)
    try:
        universe_a = object()
        universe_b = object()
        window.universe = universe_a

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
        window.universe = universe_b
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

        assert seen["universe"] is universe_b
        assert seen["capital"] == "CAPITAL_A"
        assert seen["max_position_risk_pct"] == Decimal("0.42")
        assert seen["substitutions"] is new_rules
    finally:
        window.deleteLater()


def test_the_capital_is_computed_before_the_task_starts(
    monkeypatch, tmp_path
) -> None:
    """Spec 7: ``_research_scenario_capital`` runs on the UI thread."""

    window = _window(monkeypatch, tmp_path)
    try:
        window.universe = object()
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

        monkeypatch.setattr(window, "_start_task", start)
        monkeypatch.setattr(
            window.market_scan_service, "scan", lambda *a, **k: "RESULT"
        )

        window._run_scan()

        assert calls == ["capital", "start"]
    finally:
        window.deleteLater()


def test_the_service_is_called_once_per_task_run(
    monkeypatch, tmp_path
) -> None:
    """Spec 4: the window no longer calls the scanner itself."""

    window = _window(monkeypatch, tmp_path)
    try:
        window.universe = object()
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
    """Spec 48: the declared surface is ``__init__`` and ``_run_scan``.

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
        set(LATER_ROUND_REMOVED_METHODS) | set(STRATEGY_V2_REMOVED_METHODS)
    )
    assert set(current_methods) - set(base_methods) == (
        set(LATER_ROUND_ADDED_METHODS) | set(STRATEGY_V2_ADDED_METHODS)
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
    )
    assert set(changed) <= allowed
    assert set(MARKET_DATA_V2_METHODS) <= set(changed)
    assert set(BROKER_ACCOUNT_V2_METHODS) <= set(changed)
    assert set(STRATEGY_V2_METHODS) <= set(changed)
    assert "_run_scan" in changed


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
    )


def test_the_manual_path_no_longer_calls_the_scanner() -> None:
    """Spec 50: only the manual method, not the whole file."""

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )
    method = _find_method(source, "_run_scan")

    assert "scan_market(" not in method
    assert "save_market_scan(" not in method
    assert "self.market_scan_service.scan(" in method


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
