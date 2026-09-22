"""Real ``MainWindow`` wiring for the v2O-C1 research foundations extraction.

The orchestrators' own tests prove they decide correctly; these prove the
*window* is still listening.  The extraction moved the universe and history
runtimes out of ``MainWindow``, and the failure mode it could hide is a signal
nobody connected -- a refresh button that does nothing, a cancel button that
never reaches the running task, a downstream reader that still holds its own
copy of the universe -- which is invisible to a test that only drives the
orchestrator.

The last two sections are the ones that matter for this stage:

* a real refresh is driven to completion and the *downstream* fact is read back,
  not just the callback.  A test that asserts "the orchestrator was told to
  refresh" passes even if every consumer still reads a stale mirror;
* ``_start_task`` is exercised through all three completion paths -- success,
  failure and cancellation -- because the capability's completion now runs from
  an ``on_finished`` hook rather than from a worker attribute.

Nothing here reaches the network: the service is faked at its boundary.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timezone
from threading import Event as ThreadingEvent

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop import MainWindow
from us_quant.universe import UniverseRecord, UniverseSnapshot


_APP = QApplication.instance() or QApplication([])
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _universe(symbol: str = "AAPL") -> UniverseSnapshot:
    return UniverseSnapshot(
        generated_at=NOW,
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


@pytest.fixture()
def window(monkeypatch, tmp_path):
    """A real window with its dialogs silenced.

    The close path reports to the operator, and a modal ``QMessageBox`` blocks
    forever under ``QT_QPA_PLATFORM=offscreen``.  Silencing belongs to the
    fixture rather than to individual tests: any test that leaves a task running
    would otherwise hang in teardown, which is a failure mode that looks like a
    deadlock in the code under test.
    """

    from us_quant.paths import STATE_ROOT_ENV

    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.information",
        staticmethod(lambda *args, **kwargs: None),
    )
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.warning",
        staticmethod(lambda *args, **kwargs: None),
    )
    widget = MainWindow()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


@pytest.fixture()
def silent_dialogs(monkeypatch):
    """Retained for tests that name the dependency explicitly."""

    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.information",
        staticmethod(lambda *args, **kwargs: None),
    )
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.warning",
        staticmethod(lambda *args, **kwargs: None),
    )


# -- 40: the buttons reach the capability ------------------------------


def test_the_refresh_button_reaches_the_orchestrator(window, monkeypatch) -> None:
    """A click, not a direct method call: the signal wiring is the subject."""

    calls: list[str] = []
    monkeypatch.setattr(
        window.universe_orchestrator,
        "request_refresh",
        lambda: calls.append("refresh"),
    )

    window.universe_page.refresh_button.click()
    _APP.processEvents()

    assert calls == ["refresh"]


def test_the_cancel_button_reaches_the_orchestrator(window, monkeypatch) -> None:
    """A click, not a direct method call: the signal wiring is the subject.

    The button starts disabled -- there is nothing to cancel before a refresh is
    running -- so a refresh is admitted first through the *real* entry point.
    Stubbing ``request_refresh`` here would be self-defeating: the state the
    button reads is set by the very call the stub replaced.  Clicking a disabled
    button emits nothing, so a test that skipped the admission would pass for the
    wrong reason.
    """

    calls: list[str] = []
    monkeypatch.setattr(
        window.universe_orchestrator,
        "request_cancel",
        lambda: calls.append("cancel"),
    )
    monkeypatch.setattr(
        window.universe_orchestrator._service,
        "refresh",
        lambda *, should_stop, progress=None: _universe(),
    )

    # Admit a refresh so the capability reports itself as active, then paint the
    # real controls: the enabled state is part of what the page shows, so the
    # real render must run for this test to mean anything.
    window.universe_orchestrator.request_refresh()
    window.universe_orchestrator.render_current()

    assert window.universe_page.cancel_button.isEnabled()
    window.universe_page.cancel_button.click()
    _APP.processEvents()

    assert calls == ["cancel"]


@pytest.mark.parametrize(
    ("button_name", "expected"),
    (
        ("schedule_button", "request_schedule"),
        ("retry_button", "retry_failed"),
    ),
)
def test_the_history_buttons_reach_the_orchestrator(
    window, monkeypatch, button_name: str, expected: str
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        window.history_orchestrator, expected, lambda: calls.append(expected)
    )

    getattr(window.history_page, button_name).click()
    _APP.processEvents()

    assert calls == [expected]


@pytest.mark.parametrize(
    ("button_name", "expected"),
    (
        ("run_ibkr_button", "request_run_ibkr"),
        ("run_public_button", "request_run_public"),
    ),
)
def test_the_history_batch_buttons_pass_their_batch_size(
    window, monkeypatch, button_name: str, expected: str
) -> None:
    """The batch size is the page's, and it must arrive as an argument."""

    seen: list[int] = []
    monkeypatch.setattr(
        window.history_orchestrator, expected, lambda count: seen.append(count)
    )

    getattr(window.history_page, button_name).click()
    _APP.processEvents()

    assert seen == [window.history_page.batch_size.value()]


# -- 40: a real refresh reaches the downstream readers -----------------


def test_a_successful_refresh_is_what_the_downstream_readers_see(
    window, monkeypatch
) -> None:
    """Spec 40: read a real downstream fact, not the callback.

    The window used to hold ``self.universe`` and every consumer read that.  A
    test that only proved ``snapshot_changed`` fired would pass even if the
    scanner still read a copy.  So this drives the whole path -- service call,
    success handler, fan-out -- and then asserts the fact the *scanner* would
    consume is the refreshed snapshot.
    """

    refreshed = _universe("MSFT")
    before = window.universe_orchestrator.snapshot

    monkeypatch.setattr(
        window.universe_orchestrator._service,
        "refresh",
        lambda *, should_stop, progress=None: refreshed,
    )
    # The page repaint is not the subject and would need a real view.
    monkeypatch.setattr(
        window.universe_orchestrator._page, "render", lambda view: None
    )
    scope_refreshes: list[str] = []
    monkeypatch.setattr(
        window, "_refresh_market_scope_summary", lambda: scope_refreshes.append("x")
    )

    window.universe_orchestrator.request_refresh()
    _APP.waitForWindowShown(window) if False else None
    for _ in range(200):
        _APP.processEvents()
        if window.universe_orchestrator.snapshot is refreshed:
            break
        ThreadingEvent().wait(0.02)

    assert window.universe_orchestrator.snapshot is refreshed
    assert before is not refreshed
    # The cross-workflow bridge ran, which is the window's half of the deal.
    assert scope_refreshes, "the market scope bridge never ran"
    # And the fact the scanner reads is the refreshed one, not a copy.
    assert window.universe_orchestrator.snapshot.records[0].symbol == "MSFT"


def test_the_scan_hands_the_orchestrators_snapshot_to_the_service(
    window, monkeypatch, silent_dialogs
) -> None:
    """Spec 29: ``_run_scan`` reads the capability, not a window attribute."""

    snapshot = _universe("NVDA")
    window.universe_orchestrator.restore_snapshot(snapshot)

    seen: dict = {}
    captured: list = []
    monkeypatch.setattr(
        window,
        "_start_task",
        lambda task, **kwargs: captured.append(task) or True,
    )
    monkeypatch.setattr(
        window.market_scan_service,
        "scan",
        lambda universe, **kwargs: seen.setdefault("universe", universe),
    )

    window._run_scan()
    captured[0](lambda _message: None)

    assert seen["universe"] is snapshot


# -- 41: shutdown cancels a live refresh -------------------------------


def test_worker_stops_sets_the_live_refresh_cancel_event(
    window, monkeypatch, silent_dialogs
) -> None:
    """Spec 41: the shutdown path reaches the running refresh's own event.

    The event is captured from the call the *domain* received, so this asserts
    the event the refresh actually polls -- not an object the test parked on the
    window.
    """

    entered = ThreadingEvent()
    seen: dict = {}

    def refresh(*, should_stop, progress=None):
        seen["should_stop"] = should_stop
        entered.set()
        return _universe("TSLA")

    monkeypatch.setattr(
        window.universe_orchestrator._service, "refresh", refresh
    )
    monkeypatch.setattr(
        window.universe_orchestrator._page, "render", lambda view: None
    )

    window.universe_orchestrator.request_refresh()
    assert entered.wait(10), "the refresh never reached the service"
    assert seen["should_stop"]() is False

    window._request_worker_stops()

    assert seen["should_stop"]() is True


def test_shutdown_cancellation_writes_no_operator_status_line(
    window, monkeypatch
) -> None:
    """A close is not an operator asking to cancel.

    :meth:`cancel_for_shutdown` exists separately from :meth:`request_cancel`
    for this reason, so the distinction is asserted rather than assumed.  The
    admission's own start line is expected; the *cancel* line is not.
    """

    from us_quant.desktop_v2.orchestration.research.universe.orchestrator import (
        CANCEL_MESSAGE,
        REFRESH_START_MESSAGE,
    )

    monkeypatch.setattr(
        window.universe_orchestrator._service,
        "refresh",
        lambda *, should_stop, progress=None: _universe(),
    )
    monkeypatch.setattr(
        window.universe_orchestrator._page, "render", lambda view: None
    )
    logs: list[str] = []
    monkeypatch.setattr(window, "_log", logs.append)

    window.universe_orchestrator.request_refresh()
    window._request_worker_stops()

    assert REFRESH_START_MESSAGE in logs
    assert CANCEL_MESSAGE not in logs, logs


# -- 37: the generic task lifecycle, all three completion paths --------


def _drive_task(window, monkeypatch, outcome: str):
    """Run one real task through ``_start_task`` and return what was seen.

    The dialogs are silenced because the failure path reports to the operator
    and a modal ``QMessageBox`` blocks forever under
    ``QT_QPA_PLATFORM=offscreen``.
    """

    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.warning",
        staticmethod(lambda *args, **kwargs: None),
    )
    monkeypatch.setattr(
        "us_quant.desktop.QMessageBox.information",
        staticmethod(lambda *args, **kwargs: None),
    )

    seen: dict = {"finished": 0, "success": [], "failure": []}

    def task(progress) -> str:
        if outcome == "failure":
            raise RuntimeError("boom")
        if outcome == "cancelled":
            from us_quant.universe import UniverseRefreshCancelled

            raise UniverseRefreshCancelled("cancelled")
        return "ok"

    def on_finished() -> None:
        seen["finished"] += 1

    admitted = window._start_task(
        task,
        on_success=seen["success"].append,
        on_failure=seen["failure"].append,
        start_message="test task",
        resource_group="research",
        on_finished=on_finished,
    )
    assert admitted is True

    for _ in range(400):
        _APP.processEvents()
        if seen["finished"]:
            break
        ThreadingEvent().wait(0.02)
    return seen


def test_a_successful_task_runs_the_completion_hook(window, monkeypatch) -> None:
    """Spec 37: success -> on_success -> generic cleanup -> on_finished."""

    seen = _drive_task(window, monkeypatch, "success")

    assert seen["success"] == ["ok"]
    assert seen["finished"] == 1


def test_a_failed_task_runs_the_completion_hook(window, monkeypatch) -> None:
    seen = _drive_task(window, monkeypatch, "failure")

    assert len(seen["failure"]) == 1
    assert seen["finished"] == 1


def test_a_cancelled_task_runs_the_completion_hook(window, monkeypatch) -> None:
    seen = _drive_task(window, monkeypatch, "cancelled")

    assert seen["success"] == []
    assert seen["finished"] == 1


def test_the_completion_hook_does_not_need_a_worker_handle(
    window, monkeypatch
) -> None:
    """Spec 37: capability completion must not depend on ``workers[-1]``.

    The hook is passed in by the caller, so it is invoked for a task the window
    knows nothing about beyond the worker it created.  If completion were still
    read off ``workers[-1]``, a second concurrent task would complete the wrong
    capability -- so a hook that fires for a task admitted while another is
    registered is the assertion that distinguishes the two designs.
    """

    blockers: list[ThreadingEvent] = []
    release = ThreadingEvent()

    def blocker(progress) -> str:
        blockers.append(ThreadingEvent())
        release.wait(20)
        return "blocked"

    try:
        window._start_task(
            blocker,
            on_success=lambda _result: None,
            start_message="blocker",
            resource_group="history",
        )
        for _ in range(200):
            _APP.processEvents()
            if blockers:
                break
            ThreadingEvent().wait(0.02)
        assert blockers, "the blocking task never started"

        seen = _drive_task(window, monkeypatch, "success")

        assert seen["finished"] == 1, "the second task's hook did not run"
    finally:
        # Always released: a failed assertion above must not leave a worker
        # thread parked for the rest of the session.
        release.set()


# -- startup restoration paints exactly once ---------------------------


def _count_startup_renders(window, monkeypatch, *, cache: str) -> list[int]:
    """Drive ``_load_local_state`` and return how many times the page painted.

    ``_load_local_state`` is scheduled by ``main()`` via
    ``QTimer.singleShot(0, ...)``, so a window built directly never runs it and
    it has to be driven explicitly.

    ``window.universe_path`` is set explicitly: the constructor falls back to the
    *bundled* snapshot when no writable one exists, and that file is present in
    the repository, so the real "nothing cached" case cannot be produced by
    pointing the state root at an empty directory.

    The subject is the *render count*, not the resulting view.  Adopting a
    snapshot changes what the page must show, so restoration has to paint -- and
    exactly once.  The old shape painted twice: ``restore_snapshot`` painted and
    ``_load_local_state`` painted again, rebuilding the whole table on every
    ordinary startup.
    """

    from us_quant.desktop_v2.pages.research.universe import page as page_module

    renders: list[int] = []
    original = page_module.UniversePage.render

    def counting_render(self, view) -> None:
        renders.append(len(getattr(view, "rows", ()) or ()))
        return original(self, view)

    monkeypatch.setattr(page_module.UniversePage, "render", counting_render)

    target = window.paths.user_data_root / "reference" / "universe.json"
    window.universe_path = target
    if cache != "absent":
        target.parent.mkdir(parents=True, exist_ok=True)
        if cache == "valid":
            from us_quant.universe import save_universe_snapshot

            save_universe_snapshot(_universe("AAPL"), target)
        else:
            target.write_text("{ this is not json", encoding="utf-8")

    window._load_local_state()
    _APP.processEvents()
    return renders


def test_a_cached_universe_paints_the_page_exactly_once(
    window, monkeypatch
) -> None:
    """Spec: restore + render, not restore then render again."""

    renders = _count_startup_renders(window, monkeypatch, cache="valid")

    assert len(renders) == 1, renders
    # And the single paint is the restored one, not an empty placeholder.
    assert renders[0] > 0
    assert window.universe_orchestrator.snapshot is not None


def test_an_absent_universe_paints_the_empty_page_exactly_once(
    window, monkeypatch
) -> None:
    """The fallback path still paints: an unpainted page is a blank table."""

    renders = _count_startup_renders(window, monkeypatch, cache="absent")

    assert len(renders) == 1, renders
    assert renders == [0]


def test_an_invalid_cached_universe_paints_exactly_once(
    window, monkeypatch
) -> None:
    """A failed restore paints once, and does not double-paint on the way out."""

    renders = _count_startup_renders(window, monkeypatch, cache="invalid")

    assert len(renders) == 1, renders
    assert renders == [0]
    assert window.universe_orchestrator.snapshot is None


def test_restore_snapshot_stays_atomic(window, monkeypatch) -> None:
    """Adopting a snapshot paints, so the caller must not paint again.

    This is the other half of the single-paint rule: the fix is not "the window
    stops painting", it is "the capability paints on adoption and the caller
    therefore does not".  Pinned separately so that removing the paint from
    ``restore_snapshot`` (which would silently make the window the painter
    again) fails here rather than only showing up as a blank page.
    """

    from us_quant.desktop_v2.pages.research.universe import page as page_module

    renders: list[int] = []
    original = page_module.UniversePage.render
    monkeypatch.setattr(
        page_module.UniversePage,
        "render",
        lambda self, view: renders.append(len(getattr(view, "rows", ()) or ()))
        or original(self, view),
    )

    window.universe_orchestrator.restore_snapshot(_universe("AAPL"))

    assert len(renders) == 1, renders
    assert renders[0] == 1
    assert window.universe_orchestrator.snapshot is not None


def test_restoration_announces_nothing(window, monkeypatch) -> None:
    """Re-reading a local file is not a successful official refresh."""

    published: list = []
    logs: list[str] = []
    window.universe_orchestrator.snapshot_changed.connect(published.append)
    window.universe_orchestrator.log_requested.connect(logs.append)

    window.universe_orchestrator.restore_snapshot(_universe("AAPL"))

    assert published == []
    assert logs == []


