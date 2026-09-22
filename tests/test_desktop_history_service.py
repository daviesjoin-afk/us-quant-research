"""Tests for the desktop history queue service and its wiring.

The service is pure application code: no Qt, no threads, no network.  The
runners and the job store are faked, so these tests never touch SQLite in the
real runtime root, never open an IBKR socket and never reach Yahoo.

This file tests the **service**.  Whether the window still declares a retired
history handler, and which commit removed it, is not a claim about the service
and does not belong here: the current-state half lives in
``tests/test_desktop_research_foundations_architecture.py``, which asserts the
retired names are absent from ``MainWindow`` today.  What the previous rounds
added and removed relative to their own base commits was recorded here as a
method-delta ledger; it has been removed, because a record of which PR touched
which method does not protect anything a current-state assertion does not.
"""

from __future__ import annotations

import ast
import pathlib

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

# v2O-C1 (Research foundations): the history runtime moved into
# ``desktop_v2/orchestration/research/history``.  The five methods this file's
# spec named no longer exist on the window -- their behaviour is asserted
# directly against the orchestrator in ``test_desktop_history_orchestrator.py``,
# which is a stronger guard than reading a window method that forwards.
#
# ``_publish_history_view`` and ``_history_task_failed`` are on the list even
# though no current round removed them: a window handler that renders the queue
# or reports a history task failure is an entry point into history state, and the
# claim being made is about today's tree, not about who deleted what.
#: The window methods this capability retired, as a **current-state** list.
#:
#: No base commit and no per-round delta: the question is whether any of these
#: names exists on ``MainWindow`` today.  The window must not regain a history
#: entry point -- a forwarding method would pass a "does not build a store" check
#: while still being a second place history could be started from, which is why
#: absence is the assertion.
#:
#: ``_publish_history_view`` and ``_history_task_failed`` were added by the
#: HistoryPage round and removed by v2O-C1, so they exist at no revision; they
#: belong on this list for the same reason as the rest.
RETIRED_WINDOW_METHODS = (
    "_refresh_universe",
    "_cancel_universe_refresh",
    "_reset_universe_refresh_controls",
    "_universe_refreshed",
    "_schedule_history",
    "_run_history",
    "_history_finished",
    "_run_public_history",
    "_retry_failed",
    "_publish_history_view",
    "_history_task_failed",
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


# -- the window no longer declares a history entry point --------------


@pytest.mark.parametrize("name", RETIRED_WINDOW_METHODS)
def test_the_history_method_is_gone_from_the_window(name: str) -> None:
    """The window no longer runs history, and must not regain a way to.

    The behavioural half lives in ``test_desktop_history_orchestrator.py``,
    which drives the orchestrator directly.

    The same absence is asserted for the whole capability in
    ``tests/test_desktop_research_foundations_architecture.py``; this list is the
    history-specific spelling of it, kept here because a reader looking for "can
    the window start a history run?" should find the answer next to the service
    it asks about.
    """

    cls = _main_window_class(ast.parse(_desktop_source()))
    names = {
        node.name
        for node in cls.body
        if isinstance(node, ast.FunctionDef)
    }
    assert name not in names, f"{name} is back on the window"


def test_the_module_wide_ban_is_deliberately_not_asserted() -> None:
    """AutoQuant keeps its direct job-store call on purpose.

    A test asserting ``desktop.py`` never mentions ``HistoryJobStore`` would
    force a rewrite of ``_auto_market_scan_finished``, which sits on the
    Paper-adjacent candidate-preparation path and reads the store to decide
    whether the queue has drained.  That call is *correct*: AutoQuant is not
    starting a history run, it is inspecting queue state to sequence its own
    preparation.

    So the retirement guard is scoped to the history entry points -- the methods
    that could start a run -- rather than the store symbol.  This test records
    that boundary, so a future round does not "finish the cleanup" by banning the
    symbol and quietly breaking AutoQuant sequencing.
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


# -- 52: _run_history wiring ------------------------------------------


# -- 53: _run_public_history wiring -----------------------------------


# -- 54: _retry_failed wiring -----------------------------------------


# -- 55: render_current wiring (was _refresh_queue_table) -------------


def test_render_current_caps_the_table_not_the_snapshot(
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

        # v2O-C1: the render entry point moved to the capability; the
        # assertions below still read the real widget the window shows.
        window.history_orchestrator.render_current()

        assert window.history_page.table.rowCount() == 2500
        summary = window.history_page.summary_label.text()
        assert "历史队列 2,600" in summary
        assert "待处理 2,600" in summary
        assert "完成 7" in summary
        assert "失败 3" in summary
        assert "表格仅显示前 2,500 条" in summary
    finally:
        window.deleteLater()


def test_render_current_hides_the_hint_at_exactly_the_cap(
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

        # v2O-C1: the render entry point moved to the capability; the
        # assertions below still read the real widget the window shows.
        window.history_orchestrator.render_current()

        assert window.history_page.table.rowCount() == 2500
        summary = window.history_page.summary_label.text()
        assert "历史队列 2,500" in summary
        assert "表格仅显示前 2,500 条" not in summary
    finally:
        window.deleteLater()


def test_render_current_snapshots_once(monkeypatch, tmp_path) -> None:
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

        # v2O-C1: the render entry point moved to the capability; the
        # assertions below still read the real widget the window shows.
        window.history_orchestrator.render_current()

        assert len(calls) == 1
        assert window.history_page.table.rowCount() == 3
        assert "历史队列 3" in window.history_page.summary_label.text()
    finally:
        window.deleteLater()


def test_render_current_translates_statuses(
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

        # v2O-C1: the render entry point moved to the capability; the
        # assertions below still read the real widget the window shows.
        window.history_orchestrator.render_current()

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
