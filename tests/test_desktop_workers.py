"""Coverage for the Qt worker adapters that moved out of ``desktop.py``.

These two classes are the seam between a background job and the GUI
thread.  The move is only worth anything if the seam still behaves the
same, so every assertion here is about *behaviour* -- what the signals
carry, in what order, and which object owns the stop request.

Two rules get structural tests rather than behavioural ones, because a
behaviour test cannot see them:

* the module must stay free of UI and paper imports (a worker that
  imported ``QWidget`` would still pass every signal assertion), and
* ``StreamWorker`` must contain no provider branching -- the provider
  factory belongs to ``MarketDataService``, and a worker that quietly
  built its own adapter would also read as working.

Design note, and the reason for two unusual choices below: the imports
of the modules under test are *lazy* (inside the tests), and the source
guards read the files by path instead of via an imported module's
``__file__``.  That is not style for its own sake -- it is what makes the
boundary guards work in the one situation they exist for.  If
``desktop_workers`` grows an import of ``desktop`` (the exact violation
being guarded), an eager top-level import makes this whole file fail to
*collect*, and a collection error names no test: the guard would be
invisible precisely when it fires.  Reading paths directly keeps the
violation attributed to a test name.

Nothing here opens a socket or starts a real thread: ``run()`` is called
directly and the service is replaced with a recorder.
"""

from __future__ import annotations

import ast
import inspect
import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dataclasses

import pytest
from PySide6.QtWidgets import QApplication


_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SOURCE = _PROJECT_ROOT / "src/us_quant/desktop_workers.py"
_DESKTOP_SOURCE = _PROJECT_ROOT / "src/us_quant/desktop.py"

_APP = QApplication.instance() or QApplication([])


def _workers():
    """Import the module under test lazily (see the module docstring)."""

    import us_quant.desktop_workers as module

    return module


def _capture(signal) -> list:
    """Connect a list so emissions are recorded synchronously.

    ``cancelled`` is a zero-argument signal, so the slot cannot be
    ``list.append`` directly -- it would raise ``TypeError`` from inside
    the emitter and mask the very behaviour under test.
    """

    received: list = []

    def record(*args):
        received.append(args[0] if len(args) == 1 else (args or None))

    signal.connect(record)
    return received


# -- TaskThread --------------------------------------------------------


def test_task_thread_reports_progress_then_success() -> None:
    def task(progress) -> int:
        progress("step-1")
        return 123

    worker = _workers().TaskThread(task)
    progress = _capture(worker.progress)
    succeeded = _capture(worker.succeeded)
    failed = _capture(worker.failed)
    cancelled = _capture(worker.cancelled)

    worker.run()

    assert progress == ["step-1"]
    assert succeeded == [123]
    assert failed == []
    assert cancelled == []


def test_task_thread_treats_cancellation_as_cancellation() -> None:
    """A cancelled refresh is not a failure.

    The desktop relies on this distinction: ``cancelled`` clears the busy
    state silently, while ``failed`` raises an error banner.
    """

    from us_quant.universe import UniverseRefreshCancelled

    def task(progress):
        raise UniverseRefreshCancelled("superseded by a newer refresh")

    worker = _workers().TaskThread(task)
    succeeded = _capture(worker.succeeded)
    failed = _capture(worker.failed)
    cancelled = _capture(worker.cancelled)

    worker.run()

    assert len(cancelled) == 1
    assert succeeded == []
    assert failed == []


def test_task_thread_flattens_a_multiline_failure_message() -> None:
    def task(progress):
        raise RuntimeError("hello\n   world")

    worker = _workers().TaskThread(task)
    failed = _capture(worker.failed)
    succeeded = _capture(worker.succeeded)

    worker.run()

    assert failed == ["hello world"]
    assert succeeded == []


def test_task_thread_keeps_the_resource_group() -> None:
    def task(progress):
        return None

    worker = _workers().TaskThread(task, resource_group="broker")

    assert worker.resource_group == "broker"
    assert worker.task is task


def test_task_thread_defaults_to_the_research_group() -> None:
    worker = _workers().TaskThread(lambda progress: None)

    assert worker.resource_group == "research"


def test_task_thread_exposes_exactly_the_original_signals() -> None:
    """The constructor contract is what the desktop calls into."""

    assert set(
        inspect.signature(_workers().TaskThread.__init__).parameters
    ) == {"self", "task", "resource_group"}


# -- StreamWorker ------------------------------------------------------


@dataclasses.dataclass
class _FakeStream:
    ran: int = 0
    stopped: int = 0

    def run(self) -> None:
        self.ran += 1

    def stop(self) -> None:
        self.stopped += 1


@dataclasses.dataclass
class _FakeService:
    """Records what the worker asked for, and nothing else."""

    exchange: str = "SMART"
    stream: _FakeStream = dataclasses.field(default_factory=_FakeStream)
    built: list = dataclasses.field(default_factory=list)
    exchanges_asked: list = dataclasses.field(default_factory=list)
    run_calls: int = 0
    stop_calls: int = 0
    failure: Exception | None = None

    def prepare(self, request, *, listener=None):
        self.built.append({"request": request, "listener": listener})
        return self.stream

    @property
    def prepared_market_exchange(self) -> str:
        """The venue the (fake) adapter was built with.

        Mirrors the real application: the worker reads this instead of
        re-resolving, so the fake has to expose it rather than the resolver.
        """

        return self.exchange

    def run(self) -> None:
        self.run_calls += 1
        if self.failure is not None:
            raise self.failure

    def stop(self) -> None:
        self.stop_calls += 1


def _request(source: str | None = None):
    from us_quant.trading.application.market_data import (
        SOURCE_IBKR,
        MarketDataStartRequest,
    )

    return MarketDataStartRequest(
        source_id=source or SOURCE_IBKR, symbols=("SPY",)
    )


def test_stream_worker_asks_the_service_for_its_stream() -> None:
    service = _FakeService(exchange="ARCA")
    request = _request()

    worker = _workers().StreamWorker(service, request)

    assert len(service.built) == 1
    assert service.built[0]["request"] is request
    # The venue comes from the application's prepared state, not from a second
    # resolution: ``market_exchange_for`` is never called here.
    assert service.exchanges_asked == []

    assert worker.source_id == request.source_id
    assert worker.market_data is service
    assert worker.market_exchange == "ARCA"


def test_stream_worker_carries_the_requested_provider_verbatim() -> None:
    """The provider label must come from the request, not a literal.

    The first provider in the list is the IBKR one, and a hardcoded
    ``"ibkr"`` would satisfy an equality check against it -- so this uses
    a different provider, where the two cannot be confused.
    """

    from us_quant.trading.application.market_data import (
        SOURCE_FINNHUB_TRADES,
    )

    service = _FakeService()

    worker = _workers().StreamWorker(
        service, _request(SOURCE_FINNHUB_TRADES)
    )

    assert worker.source_id == SOURCE_FINNHUB_TRADES
    assert worker.source_id == "finnhub_trades"


def test_stream_worker_hands_the_adapter_its_own_signal_as_listener() -> None:
    """The listener must be ``snapshot_ready.emit``.

    Anything else -- a lambda that called the window, say -- would run on
    the stream thread and touch widgets from the wrong thread.
    """

    service = _FakeService()

    worker = _workers().StreamWorker(service, _request())

    listener = service.built[0]["listener"]
    assert listener is not None

    delivered = _capture(worker.snapshot_ready)
    payload = object()
    listener(payload)
    _APP.processEvents()

    assert delivered == [payload]


def test_stream_worker_run_delegates_to_the_service() -> None:
    service = _FakeService()

    worker = _workers().StreamWorker(service, _request())
    failed = _capture(worker.failed)
    worker.run()

    assert service.run_calls == 1
    assert failed == []


def test_stream_worker_run_reports_a_failure_with_its_type() -> None:
    service = _FakeService(failure=RuntimeError("boom"))

    worker = _workers().StreamWorker(service, _request())
    failed = _capture(worker.failed)
    worker.run()

    assert failed == ["RuntimeError: boom"]


def test_stream_worker_request_stop_goes_through_the_service() -> None:
    """Stopping must not bypass the service's lifecycle bookkeeping."""

    service = _FakeService()

    worker = _workers().StreamWorker(service, _request())
    worker.request_stop()

    assert service.stop_calls == 1
    assert service.stream.stopped == 0


def test_stream_worker_does_not_terminate_its_thread(monkeypatch) -> None:
    """``terminate`` is forbidden; the stop request is cooperative.

    A bare ``isRunning()`` check cannot see this: the thread was never
    started, so it is not running whether or not ``terminate`` was
    called.  What has to be observed is the call itself.
    """

    from PySide6.QtCore import QThread

    calls: list[bool] = []
    monkeypatch.setattr(
        QThread, "terminate", lambda self: calls.append(True)
    )

    service = _FakeService()

    worker = _workers().StreamWorker(service, _request())
    worker.request_stop()

    assert calls == []
    assert service.stop_calls == 1


def test_stream_worker_construction_signature_is_unchanged() -> None:
    assert set(
        inspect.signature(_workers().StreamWorker.__init__).parameters
    ) == {"self", "market_data", "request"}


# -- identity: the old import path must keep working --------------------


def test_old_and_new_import_paths_are_the_same_object() -> None:
    """``from us_quant.desktop import TaskThread`` must not be a copy.

    A wrapper or subclass would satisfy the import but break every
    ``isinstance`` check in the desktop.
    """

    import us_quant.desktop as desktop
    import us_quant.desktop_workers as workers

    assert desktop.TaskThread is workers.TaskThread
    assert desktop.StreamWorker is workers.StreamWorker


# -- structural guards -------------------------------------------------


def _module_tree() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"))


def _imported_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_worker_module_imports_no_ui_and_no_paper_stack() -> None:
    """The module must stay importable without the window or the broker.

    The forbidden list is deliberately about *layers* -- widgets, the
    desktop module, the paper stack -- rather than one exact import line,
    so a new UI import cannot slip in unnoticed.
    """

    imported = _imported_names(_module_tree())

    forbidden_prefixes = (
        "PySide6.QtWidgets",
        "PySide6.QtGui",
        "us_quant.desktop",
        "us_quant.paper",
        "us_quant.workflow",
        "us_quant.risk",
        "us_quant.auto_quant",
        "us_quant.ibkr_paper_orders",
        "us_quant.strategy",
    )

    offenders = [
        name
        for name in imported
        for prefix in forbidden_prefixes
        if name == prefix or name.startswith(prefix + ".")
    ]

    assert offenders == []


def test_worker_module_allows_only_the_expected_dependencies() -> None:
    """A pin on the *allowlist*: the dependency surface must not grow."""

    imported = set(_imported_names(_module_tree()))

    assert imported == {
        "__future__",
        "typing",
        "PySide6.QtCore",
        "us_quant.trading.application.market_data",
        "us_quant.universe",
    }


def test_worker_module_names_no_ui_class() -> None:
    tree = _module_tree()
    source = _SOURCE.read_text(encoding="utf-8")

    for banned in ("MainWindow", "QMainWindow", "QWidget", "QMessageBox"):
        assert banned not in source, banned

    defined = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    ]

    assert sorted(defined) == ["StreamWorker", "TaskThread"]


def test_stream_worker_contains_no_provider_branching() -> None:
    """The provider factory belongs to ``MarketDataApplication``.

    Checked as AST rather than as text: a comparison against
    ``request.source_id``/``request.provider`` or a ``match`` on it is the
    shape a re-grown factory takes.  A branch whose body happens to be inert
    is still a factory in waiting, so the guard flags the comparison itself
    rather than any resulting behaviour.
    """

    tree = _module_tree()
    worker_class = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "StreamWorker"
    )

    branches = []
    for node in ast.walk(worker_class):
        if isinstance(node, ast.Match):
            branches.append("match")
        if isinstance(node, ast.Compare):
            for operand in [node.left, *node.comparators]:
                if (
                    isinstance(operand, ast.Attribute)
                    and operand.attr in {"provider", "source_id"}
                ):
                    branches.append(ast.unparse(node)[:80])
        if isinstance(node, (ast.If, ast.IfExp)):
            for operand in ast.walk(node.test):
                if (
                    isinstance(operand, ast.Attribute)
                    and operand.attr in {"provider", "source_id"}
                ):
                    branches.append(ast.unparse(node.test)[:80])
                    break

    assert branches == []


def test_stream_worker_hardcodes_no_source_id() -> None:
    """A literal source id in the worker is a factory that lost its shape."""

    from us_quant.trading.application.market_data import SUPPORTED_SOURCES

    source = _SOURCE.read_text(encoding="utf-8")
    for source_id in SUPPORTED_SOURCES:
        assert f'"{source_id}"' not in source, source_id


def test_worker_module_names_no_provider_adapter() -> None:
    """No concrete adapter may be named here, even in a comment."""

    source = _SOURCE.read_text(encoding="utf-8")

    for adapter in (
        "IBKRReadOnlyStream",
        "AlpacaIEXStream",
        "FinnhubStream",
        "AlpacaStream",
    ):
        assert adapter not in source, adapter


def test_desktop_reexports_the_workers_instead_of_defining_them() -> None:
    """``desktop.py`` must import the classes, not redefine them."""

    tree = ast.parse(_DESKTOP_SOURCE.read_text(encoding="utf-8"))

    defined = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and node.name in {"TaskThread", "StreamWorker"}
    ]

    assert defined == []

    imported: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "us_quant.desktop_workers"
        ):
            imported.extend(alias.name for alias in node.names)

    assert sorted(imported) == ["StreamWorker", "TaskThread"]


def test_worker_module_defines_each_class_exactly_once() -> None:
    tree = _module_tree()

    names = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    ]

    assert names.count("TaskThread") == 1
    assert names.count("StreamWorker") == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
