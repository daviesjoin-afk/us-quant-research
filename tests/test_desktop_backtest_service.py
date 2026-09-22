"""Tests for the desktop backtest service and its wiring.

The service is pure application code: no Qt, no threads, no real daily bars
and no real run JSON.  Both domain calls are faked.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from us_quant.desktop import MainWindow
from us_quant.desktop_backtest_service import DesktopBacktestService
from us_quant.paths import STATE_ROOT_ENV

_REPO_ROOT = Path(__file__).resolve().parents[1]

SERVICE_MODULE = "src/us_quant/desktop_backtest_service.py"
SERVICE_PATH = _REPO_ROOT / SERVICE_MODULE


def _class_named(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


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


def test_the_capability_owns_the_run_loop_not_the_domain_directly() -> None:
    """Spec 46: only the capability, not the whole file."""

    path = (
        _REPO_ROOT
        / "src"
        / "us_quant"
        / "desktop_v2"
        / "orchestration"
        / "research"
        / "backtest"
        / "orchestrator.py"
    )
    source = path.read_text(encoding="utf-8")

    assert "run_backtest(" not in source
    assert "save_backtest_run(" not in source
    assert "self._service.run(" in source


def test_the_window_no_longer_imports_the_run_helpers() -> None:
    """The run helpers belong to the service; the window imports none of them.

    A window that imported them could run a batch itself, which would be a second
    path to the same artifacts.
    """

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )
    imported = _imported_names(source)

    assert "run_backtest" not in imported
    assert "save_backtest_run" not in imported


def test_the_window_keeps_the_backtest_service() -> None:
    """Spec 48: v2O-C3 moved the request construction out, but the window still
    composes the service and hands it to the capability."""

    source = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )
    imported = _imported_names(source)

    assert "DesktopBacktestService" in imported
    assert "BacktestOrchestrator" in imported
    assert "BacktestPage" in imported


def test_the_domain_module_still_owns_the_run_helpers() -> None:
    """Spec 26: the real calls live only in the service module now."""

    service = SERVICE_PATH.read_text(encoding="utf-8")

    assert "run_backtest(" in service
    assert "save_backtest_run(" in service
    assert "us_quant.backtest_workspace" in _module_imports(service)


# -- 49/50/51: the capability's validation, requests and progress ------
#
# v2O-C3 moved these rules into ``BacktestOrchestrator``: the window no longer
# declares ``_run_backtest_workspace`` or ``_backtest_records``.  The assertions
# are kept verbatim where the behaviour is unchanged -- the refusals, the
# request fields and the progress copy are the same contract -- but they are now
# driven through the capability, because that is where the code lives.  What the
# window still owns is checked in ``test_desktop_v2_backtest_wiring.py``.


def _draft(window, *, strategy_version_id: str | None = None):
    draft = window.backtest_page.controls.draft()
    if strategy_version_id is None:
        return draft
    from dataclasses import replace

    return replace(draft, strategy_version_id=strategy_version_id)


def _capture_task(
    window,
    monkeypatch,
    *,
    strategy_version_id: str | None = None,
    compare_all: bool = False,
):
    """Ask the capability to run and hand back the task it submitted.

    ``compare_all`` runs one version per strategy family; otherwise the draft
    names exactly one version.  Either way the request construction is the same
    code path, which is what these tests are about.

    Dialogs are stubbed because a modal ``QMessageBox`` blocks forever under
    ``QT_QPA_PLATFORM=offscreen``.
    """

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)

    captured: list = []
    monkeypatch.setattr(
        window.backtest_orchestrator,
        "_submit_task",
        lambda task, **kwargs: captured.append((task, kwargs)) or True,
    )
    draft = _draft(window, strategy_version_id=strategy_version_id)
    if compare_all:
        window.backtest_orchestrator.request_compare_all(draft)
    else:
        window.backtest_orchestrator.request_selected(draft)
    return captured[0] if captured else None


def _version(strategy_id: str, *, version_id: str):
    """A real ``StrategyVersion``, so the provider contract is honest."""

    from datetime import datetime, timezone

    from us_quant.trading.domain.strategy import (
        StrategyDefinition,
        StrategyIdentity,
        StrategyMode,
        StrategyStatus,
        StrategyVersion,
    )

    now = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    return StrategyVersion(
        definition=StrategyDefinition(
            strategy_id=strategy_id,
            name=f"{strategy_id} name",
            description="test",
        ),
        identity=StrategyIdentity(
            strategy_id=strategy_id,
            version_id=version_id,
            parameter_hash=f"ph-{version_id}",
        ),
        semver="1.0.0",
        status=StrategyStatus.RESEARCH,
        mode=StrategyMode.RESEARCH,
        parameters={"lookback": 20},
        universe_hash="uh",
        code_hash=f"ch-{version_id}",
        risk_budget_pct=Decimal("0.01"),
        gate_passed=True,
        gate_reason="",
        created_at=now,
        updated_at=now,
    )


def _provider(window, versions):
    """Point the capability's catalogue provider at a fixed list."""

    def provider():
        return tuple(versions)

    window.backtest_orchestrator._strategy_versions_provider = provider
    return provider


def test_a_busy_backtest_worker_blocks_the_run(monkeypatch, tmp_path) -> None:
    """Spec 49: an already-running backtest worker means "busy".

    Registered through the controller, which is what the capability's admission
    pre-check asks.  Rebinding ``window.workers`` would not reach it: that
    attribute is a view onto the controller's own list.
    """

    from PySide6.QtWidgets import QMessageBox

    window = _window(monkeypatch, tmp_path)
    try:
        class _Worker:
            resource_group = "backtest"

            def isRunning(self) -> bool:
                return True

        window.task_controller.register(_Worker())

        shown: list[tuple] = []
        monkeypatch.setattr(
            QMessageBox, "information", lambda *a: shown.append(a)
        )
        started: list = []
        monkeypatch.setattr(
            window.backtest_orchestrator,
            "_submit_task",
            lambda *a, **k: started.append(a) or True,
        )
        ran: list = []
        monkeypatch.setattr(
            window.backtest_service,
            "run",
            lambda *a, **k: ran.append(a) or (),
        )

        window.backtest_orchestrator.request_selected(_draft(window))

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
        _provider(window, ())

        shown: list[tuple] = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *a: shown.append(a)
        )
        started: list = []
        monkeypatch.setattr(
            window.backtest_orchestrator,
            "_submit_task",
            lambda *a, **k: started.append(a) or True,
        )
        ran: list = []
        monkeypatch.setattr(
            window.backtest_service,
            "run",
            lambda *a, **k: ran.append(a) or (),
        )

        window.backtest_orchestrator.request_selected(_draft(window))

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
        _provider(window, (_version("breakout", version_id="v1"),))

        shown: list[tuple] = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *a: shown.append(a)
        )
        started: list = []
        monkeypatch.setattr(
            window.backtest_orchestrator,
            "_submit_task",
            lambda *a, **k: started.append(a) or True,
        )
        ran: list = []
        monkeypatch.setattr(
            window.backtest_service,
            "run",
            lambda *a, **k: ran.append(a) or (),
        )

        # Start strictly after end, whatever "today" happens to be.
        draft = _draft(window, strategy_version_id="v1")
        from dataclasses import replace
        from datetime import timedelta

        draft = replace(
            draft,
            start_date=draft.end_date + timedelta(days=10),
        )

        window.backtest_orchestrator.request_selected(draft)

        assert started == []
        assert ran == []
        assert shown[0][1] == "日期无效"
        assert shown[0][2] == "起始日期不能晚于结束日期。"
    finally:
        window.deleteLater()


def test_the_requests_come_from_the_form_controls(
    monkeypatch, tmp_path
) -> None:
    """Spec 50: the capability converts controls into domain requests.

    Driven as a compare-all batch so there are two requests to check: the
    property under test is that *every* request carries the form's values, and
    the ordering is ``STRATEGY_SPECS`` order (``buy-hold`` first).
    """

    window = _window(monkeypatch, tmp_path)
    try:
        _provider(
            window,
            (
                _version("buy-hold", version_id="v1"),
                _version("dual-ma-trend", version_id="v1"),
            ),
        )
        window.backtest_page.controls.symbol_input.setText("aapl")
        window.backtest_page.controls.capital_spin.setValue(2500)
        window.backtest_page.controls.weight_spin.setValue(30)
        window.backtest_page.controls.per_share_commission_spin.setValue(0.005)
        window.backtest_page.controls.minimum_commission_spin.setValue(1.25)
        window.backtest_page.controls.slippage_spin.setValue(4)

        captured = _capture_task(window, monkeypatch, compare_all=True)
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
        assert request.strategy_id == "buy-hold"
        assert request.strategy_version_id == "v1"
        assert request.parameter_hash == "ph-v1"
        assert request.code_hash == "ch-v1"
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
        # The form values reach every request in the batch, not just the first.
        assert [row.symbol for row in requests] == ["AAPL", "AAPL"]
        assert [row.initial_equity for row in requests] == [
            Decimal("2500"),
            Decimal("2500"),
        ]
    finally:
        window.deleteLater()


def test_the_progress_copy_is_verbatim(monkeypatch, tmp_path) -> None:
    """Spec 51: ``回测 i/n：<strategy_id> <symbol>``, exactly."""

    window = _window(monkeypatch, tmp_path)
    try:
        _provider(window, (_version("breakout", version_id="v1"),))
        captured = _capture_task(
            window, monkeypatch, strategy_version_id="v1"
        )
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
        _provider(
            window,
            (
                _version("buy-hold", version_id="v1"),
                _version("dual-ma-trend", version_id="v1"),
            ),
        )
        captured = _capture_task(window, monkeypatch, compare_all=True)
        assert captured is not None
        _task, kwargs = captured

        orchestrator = window.backtest_orchestrator
        assert kwargs["on_success"] == orchestrator._runs_finished
        assert kwargs["start_message"] == "正在运行 2 个版本绑定回测…"
        assert kwargs["resource_group"] == "backtest"
        assert kwargs["on_failure"] == orchestrator._runs_failed
        assert set(kwargs) == {
            "on_success",
            "on_failure",
            "start_message",
            "resource_group",
        }
    finally:
        window.deleteLater()


def test_publish_disables_run_buttons_before_the_task_starts(
    monkeypatch, tmp_path
) -> None:
    """Spec 25/28: busy is rendered before _start_task, never by workers."""

    window = _window(monkeypatch, tmp_path)
    try:
        _provider(window, (_version("breakout", version_id="v1"),))

        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
        monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)

        order: list[str] = []
        orchestrator = window.backtest_orchestrator
        original_render = orchestrator.render_current

        def render() -> None:
            original_render()
            order.append(
                "render-disabled"
                if not window.backtest_page.controls.run_selected_button.isEnabled()
                else "render-enabled"
            )

        def submit_task(task, **kwargs):
            del task, kwargs
            order.append("start_task")
            return True

        monkeypatch.setattr(orchestrator, "render_current", render)
        monkeypatch.setattr(orchestrator, "_submit_task", submit_task)

        orchestrator.request_selected(
            _draft(window, strategy_version_id="v1")
        )

        assert order == ["render-disabled", "start_task"]
    finally:
        window.deleteLater()


def test_the_capability_does_not_catch_service_failures() -> None:
    """Spec 52: no try/except around the service call."""

    path = (
        _REPO_ROOT
        / "src"
        / "us_quant"
        / "desktop_v2"
        / "orchestration"
        / "research"
        / "backtest"
        / "orchestrator.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert [
        node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
    ] == []


# -- the window does not regain a retired Cross Section handler --------


def test_cross_section_legacy_handlers_are_retired() -> None:
    """v2E renames the Cross Section handlers and removes the old names."""

    current = (_REPO_ROOT / "src/us_quant/desktop.py").read_text(
        encoding="utf-8"
    )
    current_window = _class_named(ast.parse(current), "MainWindow")
    methods = {
        node.name
        for node in current_window.body
        if isinstance(node, ast.FunctionDef)
    }
    for name in (
        "_strategy_tab",
        "_populate_strategy_report",
        "_run_strategy_research",
        "_strategy_finished",
        "_load_strategy_report",
    ):
        assert name not in methods, name
