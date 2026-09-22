"""Tests for the desktop market scan service and its wiring.

The service is pure application code: no Qt, no threads, no real daily bars
and no real scan JSON.  The two scanner calls are faked.
"""

from __future__ import annotations

import ast
import pathlib
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

SERVICE_MODULE = "src/us_quant/desktop_market_scan_service.py"
SERVICE_PATH = _REPO_ROOT / SERVICE_MODULE


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

    v2O-C4 moved the capital's owner from a window scalar to
    ``ResearchScenarioCapitalState``, so the freeze is proved by *editing the
    canonical state after the capture* rather than by a second read: the worker
    runs with ``CAPITAL_A`` while the state already says ``CAPITAL_B``.
    """

    from dataclasses import replace

    window = _window(monkeypatch, tmp_path)
    try:
        universe_a = _universe("AAA")
        universe_b = _universe("BBB")
        window.universe_orchestrator.restore_snapshot(universe_a)

        # The canonical capital the operator is looking at when they click.
        window.research_scenario_capital.set(2500)

        task, _kwargs = _capture_task(window, monkeypatch)

        # Everything the worker will read is replaced *after* the capture.
        window.research_scenario_capital.set(9999)
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
        assert seen["capital"] == Decimal("2500")
        assert seen["max_position_risk_pct"] == Decimal("0.10")
        assert seen["substitutions"] == {}
    finally:
        window.deleteLater()


def test_the_run_inputs_are_frozen_at_request_time(
    monkeypatch, tmp_path
) -> None:
    """The whole input set is a UI-thread snapshot, not just the capital.

    Asserted separately from the universe half above because the two halves have
    *opposite* timing: the capital, the risk percentage and the substitution
    rules are frozen when the operator clicks, while the universe is re-read when
    the task executes.  A guard that only checked the capital would not notice the
    others sliding back into the worker.
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
    """Spec 7: the run inputs are read on the UI thread.

    The orchestrator takes ``run_inputs_provider`` as a bound method at
    construction, so the spy has to sit on the *state* the provider reads: the
    canonical research scenario capital.  Recording the ``decimal_value`` read
    is what proves the capital was resolved before ``submit_task`` ran rather
    than inside the worker.  v2O-C4 moved that read from the retired window
    scalar to ``ResearchScenarioCapitalState``; the timing rule is unchanged.
    """

    window = _window(monkeypatch, tmp_path)
    try:
        window.universe_orchestrator.restore_snapshot(_universe())
        calls: list[str] = []

        class RecordingCapital:
            value = 1500

            @property
            def decimal_value(self):
                calls.append("capital")
                return Decimal(1500)

        monkeypatch.setattr(
            window, "research_scenario_capital", RecordingCapital()
        )

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
        window.research_scenario_capital.set(1500)
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
