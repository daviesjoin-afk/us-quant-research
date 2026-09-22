"""Behaviour tests for the universe orchestration capability (v2O-C1).

Spec 38.  These drive ``UniverseOrchestrator`` directly, with a recording page,
a recording service and a recording ``submit_task`` -- no ``MainWindow``, no
``TaskThread``, no widgets.  What is being pinned is the capability's own
contract:

* the snapshot it owns and what each entry point publishes;
* the refresh lifecycle, including the *refusal* path, which is the one the old
  worker-identity design got wrong (a refused task left the controls lit);
* that the cancel ``Event`` the operator's button sets is the very object the
  service polls;
* that every terminal path -- success, failure, cancellation -- lands on
  ``on_finished`` and resets the state.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop_universe_service import (
    STAGE_DOWNLOAD_OFFICIAL,
    STAGE_ENRICH_SEC,
    STAGE_ENRICH_SEC_START,
    STAGE_PREPARE_REFERENCE,
    UniverseRefreshProgress,
)
from us_quant.desktop_v2.orchestration.research.universe import (
    CANCEL_MESSAGE,
    REFRESH_RESOURCE_GROUP,
    REFRESH_START_MESSAGE,
    UniverseOrchestrator,
    progress_message,
)
from us_quant.universe import (
    UniverseRecord,
    UniverseRefreshCancelled,
    UniverseSnapshot,
)

_APP = QApplication.instance() or QApplication([])


# -- doubles ------------------------------------------------------------


class _Page:
    """A page that records what it was asked to draw."""

    def __init__(self) -> None:
        self.views: list = []

    def render(self, view) -> None:  # noqa: ANN001 - the real page's signature
        self.views.append(view)

    @property
    def last(self):  # noqa: ANN201
        assert self.views, "the page was never rendered"
        return self.views[-1]


class _Service:
    """The universe refresh procedure, with the calls it received."""

    def __init__(
        self,
        result: object | None = None,
        error: BaseException | None = None,
        stages: tuple = (),
    ):
        self.result = _universe() if result is None else result
        self.error = error
        self.stages = stages
        self.should_stop_calls: list = []

    def refresh(self, *, should_stop, progress=None):  # noqa: ANN001
        self.should_stop_calls.append(should_stop)
        if progress is not None:
            for stage in self.stages:
                progress(stage)
        if self.error is not None:
            raise self.error
        return self.result


class _Submit:
    """A recording task boundary with a configurable admission verdict."""

    def __init__(self, admitted: bool = True) -> None:
        self.admitted = admitted
        self.calls: list[dict] = []

    def __call__(self, task, **kwargs):  # noqa: ANN001
        self.calls.append({"task": task, **kwargs})
        return self.admitted

    @property
    def last(self) -> dict:
        assert self.calls, "submit_task was never called"
        return self.calls[-1]


def _universe() -> UniverseSnapshot:
    return UniverseSnapshot(
        generated_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        source_timestamps={"test": "now"},
        records=(
            UniverseRecord(
                symbol="AAPL",
                name="Apple",
                exchange="NASDAQ",
                security_type="STK",
                eligible_for_research=True,
            ),
        ),
    )


def _build(admitted: bool = True, **service_kwargs):
    page = _Page()
    service = _Service(**service_kwargs)
    submit = _Submit(admitted)
    orchestrator = UniverseOrchestrator(
        service=service, page=page, submit_task=submit
    )
    return orchestrator, page, service, submit


def _logs(orchestrator: UniverseOrchestrator) -> list[str]:
    seen: list[str] = []
    orchestrator.log_requested.connect(seen.append)
    return seen


def _changes(orchestrator: UniverseOrchestrator) -> list:
    seen: list = []
    orchestrator.snapshot_changed.connect(seen.append)
    return seen


def _refreshing(page: _Page) -> bool:
    """Whether the page is currently showing the refreshing controls.

    Read off the rendered view rather than a property: the capability
    deliberately exposes no ``refresh_active`` accessor, so the page state is
    the observable, and asserting on it is asserting what the operator sees.
    """

    return page.last.controls.refresh_enabled is False


# -- the snapshot -------------------------------------------------------


def test_the_initial_snapshot_is_none() -> None:
    orchestrator, _page, _service, _submit = _build()
    assert orchestrator.snapshot is None


def test_restoring_a_snapshot_publishes_nothing() -> None:
    """Spec 6: startup restoration is not a successful refresh.

    It seeds the truth and paints it -- no ``snapshot_changed`` for the
    cross-workflow fan-out and no "official refresh finished" status line, or a
    process that merely re-read a local file would announce a refresh that never
    happened.
    """

    orchestrator, page, _service, _submit = _build()
    changes = _changes(orchestrator)
    logs = _logs(orchestrator)
    snapshot = _universe()

    orchestrator.restore_snapshot(snapshot)

    assert orchestrator.snapshot is snapshot
    assert len(page.views) == 1
    assert page.last.rows[0].symbol == "AAPL"
    assert changes == []
    assert logs == []


def test_restoring_a_snapshot_does_not_enter_refreshing() -> None:
    orchestrator, page, _service, _submit = _build()
    orchestrator.restore_snapshot(_universe())
    assert _refreshing(page) is False
    assert page.last.controls.cancel_enabled is False


# -- refresh admission --------------------------------------------------


def test_a_rejected_refresh_never_enters_refreshing() -> None:
    """Spec 11: a refused task must not leave the button lit.

    This is the behaviour the old design got wrong: the window set its worker
    handle only after a successful start, but nothing cleared a stale one, so a
    refusal could leave the page permanently "refreshing".

    The snapshot is restored first so there is an idle view to compare against:
    a refused refresh must paint nothing at all, so the page still shows the
    controls from before the attempt.
    """

    orchestrator, page, service, submit = _build(admitted=False)
    orchestrator.restore_snapshot(_universe())
    views_before = len(page.views)

    orchestrator.request_refresh()

    assert len(submit.calls) == 1
    assert submit.last["resource_group"] == REFRESH_RESOURCE_GROUP
    assert submit.last["start_message"] == REFRESH_START_MESSAGE
    assert len(page.views) == views_before
    assert _refreshing(page) is False
    assert page.last.controls.cancel_enabled is False
    # The task never ran, so nothing may have polled the service.
    assert service.should_stop_calls == []


def test_a_rejected_refresh_leaves_no_cancel_state() -> None:
    """Spec 11: ``_cancel_event`` must be cleared, proven behaviourally.

    There is no accessor for the event by design, so this asks the capability
    to cancel and requires that nothing happens: if an event had been left
    behind, ``request_cancel`` would have logged its status line.
    """

    orchestrator, _page, _service, _submit = _build(admitted=False)
    logs = _logs(orchestrator)
    orchestrator.request_refresh()

    orchestrator.request_cancel()

    assert logs == []


def test_a_rejected_refresh_does_not_set_the_flag_behind_the_page() -> None:
    """Spec 11: the flag, not just the painted controls.

    A refusal returns before rendering, so a design that set ``_refresh_active``
    ahead of the admission check would still paint an idle page at that moment
    and pass a test that only looked at the page.  The flag would be live
    though, and the *next* repaint -- from a later refresh, or any other render
    -- would show a refresh that never started, with a live cancel button and
    nothing behind it.

    So this repaints on purpose and asserts what that repaint shows.
    """

    orchestrator, page, _service, _submit = _build(admitted=False)
    orchestrator.restore_snapshot(_universe())
    orchestrator.request_refresh()

    # The repaint a refusal must not have poisoned.
    orchestrator.render_current()

    assert _refreshing(page) is False
    assert page.last.controls.refresh_enabled is True
    assert page.last.controls.cancel_enabled is False
    assert page.last.controls.refresh_label == "刷新官方标的"


def test_a_rejected_refresh_cannot_be_cancelled_after_a_repaint() -> None:
    """The consequence an operator would see: a cancel button with nothing behind it.

    ``request_cancel`` logs a status line whenever it has an event.  A refusal
    that left the flag set would leave the cancel path reachable even though no
    task is running, so this asserts the log stays silent across a repaint.
    """

    orchestrator, _page, _service, _submit = _build(admitted=False)
    logs = _logs(orchestrator)
    orchestrator.request_refresh()
    orchestrator.render_current()

    orchestrator.request_cancel()

    assert logs == []


def test_an_admitted_refresh_enters_refreshing() -> None:
    """Spec 11: accepted -> refreshing, and the cancel button becomes live."""

    orchestrator, page, _service, submit = _build()
    orchestrator.request_refresh()

    assert len(submit.calls) == 1
    assert _refreshing(page) is True
    assert page.last.controls.cancel_enabled is True
    assert page.last.controls.refresh_label == "官方标的刷新中…"


def test_the_submitted_task_has_a_completion_hook() -> None:
    """Spec 9/12: completion arrives as a callback, not as a worker handle."""

    orchestrator, _page, _service, submit = _build()
    orchestrator.request_refresh()

    assert callable(submit.last["on_finished"])
    # The worker object is never handed over -- there is no key for it.
    assert "worker" not in submit.last
    assert "on_success" in submit.last


# -- progress translation ----------------------------------------------


@pytest.mark.parametrize(
    ("event", "expected"),
    (
        (
            UniverseRefreshProgress(stage=STAGE_PREPARE_REFERENCE),
            "正在准备可写的用户参考数据目录…",
        ),
        (
            UniverseRefreshProgress(stage=STAGE_DOWNLOAD_OFFICIAL),
            "正在下载 Nasdaq Trader 与 SEC 官方标的清单…",
        ),
        (
            UniverseRefreshProgress(stage=STAGE_ENRICH_SEC_START),
            "正在增量核验 500 家 SEC 注册地与行业…",
        ),
        (
            UniverseRefreshProgress(
                stage=STAGE_ENRICH_SEC, done=2, total=500, detail="AAPL"
            ),
            "SEC 核验 2/500：AAPL",
        ),
    ),
)
def test_every_stage_is_translated_verbatim(event, expected: str) -> None:
    """Spec 38: the four Chinese strings, unchanged from the window's copy."""

    assert progress_message(event) == expected


def test_an_unknown_stage_raises() -> None:
    """A stage the desktop cannot describe must not pass silently.

    Swallowing it would leave the operator watching a frozen progress line
    while the refresh is in fact still running.
    """

    with pytest.raises(ValueError):
        progress_message(UniverseRefreshProgress(stage="SOMETHING_NEW"))


def test_the_service_receives_the_translated_stage() -> None:
    """The wiring, not just the function: the task reports through ``progress``."""

    stages = (
        UniverseRefreshProgress(stage=STAGE_PREPARE_REFERENCE),
        UniverseRefreshProgress(
            stage=STAGE_ENRICH_SEC, done=2, total=500, detail="AAPL"
        ),
    )
    orchestrator, _page, _service, submit = _build(stages=stages)
    orchestrator.request_refresh()

    seen: list[str] = []
    submit.last["task"](seen.append)

    assert seen == [
        "正在准备可写的用户参考数据目录…",
        "SEC 核验 2/500：AAPL",
    ]


def test_the_service_polls_this_refresh_cancel_event() -> None:
    """Spec 38: the operator's button sets the very Event the service polls."""

    orchestrator, _page, service, submit = _build()
    orchestrator.request_refresh()
    submit.last["task"](lambda _message: None)

    assert len(service.should_stop_calls) == 1
    should_stop = service.should_stop_calls[0]
    assert should_stop() is False

    orchestrator.request_cancel()

    assert should_stop() is True


# -- success ------------------------------------------------------------


def test_a_successful_refresh_commits_and_publishes() -> None:
    orchestrator, page, _service, submit = _build()
    changes = _changes(orchestrator)
    logs = _logs(orchestrator)
    snapshot = _universe()

    orchestrator.request_refresh()
    submit.last["on_success"](snapshot)

    assert orchestrator.snapshot is snapshot
    assert page.last.rows[0].symbol == "AAPL"
    assert changes == [snapshot]
    assert len(logs) == 1
    assert "官方标的已刷新" in logs[0]


def test_the_committed_snapshot_is_the_one_the_service_returned() -> None:
    """Identity, not equality: the capability must not copy or rewrap it."""

    snapshot = _universe()
    orchestrator, _page, _service, submit = _build(result=snapshot)
    orchestrator.request_refresh()
    submit.last["on_success"](snapshot)
    assert orchestrator.snapshot is snapshot


def test_an_unexpected_success_payload_is_rejected() -> None:
    orchestrator, _page, _service, submit = _build()
    orchestrator.request_refresh()
    with pytest.raises(TypeError):
        submit.last["on_success"](object())


# -- cancel -------------------------------------------------------------


def test_a_cancel_request_repaints_and_reports() -> None:
    """Spec 13: set the Event, render the cancelling controls, log the wait."""

    orchestrator, page, _service, submit = _build()
    logs = _logs(orchestrator)
    orchestrator.request_refresh()
    submit.last["task"](lambda _message: None)

    orchestrator.request_cancel()

    assert page.last.controls.cancel_label == "正在取消…"
    assert page.last.controls.refresh_enabled is False
    assert page.last.controls.cancel_enabled is False
    assert logs == [CANCEL_MESSAGE]


def test_cancelling_when_nothing_is_running_is_a_no_op() -> None:
    orchestrator, page, _service, _submit = _build()
    logs = _logs(orchestrator)
    orchestrator.restore_snapshot(_universe())
    before = len(page.views)

    orchestrator.request_cancel()

    assert logs == []
    assert len(page.views) == before


# -- completion ---------------------------------------------------------


@pytest.mark.parametrize(
    "terminal",
    ("success", "failure", "cancelled"),
)
def test_every_terminal_path_resets_the_refresh_state(terminal: str) -> None:
    """Spec 12/38: success, failure and cancellation all reset the same way.

    The completion hook is what makes the window's old worker-identity special
    case unnecessary, so this pins that the hook -- and not the success
    handler -- is what clears the state.
    """

    error = UniverseRefreshCancelled() if terminal == "cancelled" else None
    orchestrator, page, _service, submit = _build(error=error)
    orchestrator.request_refresh()
    on_finished = submit.last["on_finished"]

    if terminal == "success":
        submit.last["on_success"](_universe())
    elif terminal == "failure":
        # A failure never reaches on_success; the window reports the message.
        pass

    on_finished()

    assert _refreshing(page) is False
    assert page.last.controls.cancel_enabled is False
    assert page.last.controls.refresh_label == "刷新官方标的"


def test_completion_clears_the_cancel_state_behaviourally() -> None:
    """Spec 12/38: after finishing, cancelling is a no-op again."""

    orchestrator, _page, _service, submit = _build()
    logs = _logs(orchestrator)
    orchestrator.request_refresh()

    submit.last["on_finished"]()
    orchestrator.request_cancel()

    assert logs == []


def test_a_second_refresh_is_possible_after_completion() -> None:
    """The reset must be complete, not partial: a re-refresh starts clean."""

    orchestrator, page, _service, submit = _build()
    orchestrator.request_refresh()
    submit.last["on_finished"]()

    orchestrator.request_refresh()

    assert len(submit.calls) == 2
    assert _refreshing(page) is True


# -- shutdown -----------------------------------------------------------


def test_shutdown_cancel_sets_the_event_without_operator_noise() -> None:
    """Spec 14: the shutdown lifecycle is not the operator pressing cancel.

    It must reach the same Event -- otherwise a close cannot interrupt the one
    long-running network task -- but it must not write a status line about a
    request nobody made, and it must not repaint while the window tears down.
    """

    orchestrator, page, service, submit = _build()
    logs = _logs(orchestrator)
    orchestrator.request_refresh()
    submit.last["task"](lambda _message: None)
    views_before = len(page.views)

    orchestrator.cancel_for_shutdown()

    assert service.should_stop_calls[0]() is True
    assert logs == []
    assert len(page.views) == views_before


def test_shutdown_cancel_with_nothing_running_is_safe() -> None:
    orchestrator, _page, _service, _submit = _build()
    orchestrator.cancel_for_shutdown()
