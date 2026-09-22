"""Behaviour tests for the history orchestration capability (v2O-C1).

Spec 39.  These drive ``HistoryOrchestrator`` directly with a recording page, a
recording service and recording providers -- no ``MainWindow``, no widgets.

The property that matters most here is a *negative* one: the queue truth lives
in ``DesktopHistoryService`` / ``HistoryJobStore``, so the orchestrator must
hold no mirror of it.  That is pinned structurally in the architecture guards,
and behaviourally here by proving every render asks the service again.

The other two things pinned here are the ones the extraction could silently get
wrong: the IBKR config must be read per run (a captured startup copy would keep
dialling the old endpoint after a Settings change), and a missing universe must
be *refused* rather than dialled.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from us_quant.desktop_history_service import (
    HistoryQueueSnapshot,
    HistoryScheduleResult,
)
from us_quant.desktop_v2.orchestration.research.history import (
    HISTORY_RESOURCE_GROUP,
    IBKR_START_MESSAGE,
    MISSING_UNIVERSE_MESSAGE,
    MISSING_UNIVERSE_TITLE,
    PUBLIC_START_MESSAGE,
    HistoryOrchestrator,
)

_APP = QApplication.instance() or QApplication([])


# -- doubles ------------------------------------------------------------


class _Page:
    def __init__(self) -> None:
        self.views: list = []

    def render(self, view) -> None:  # noqa: ANN001
        self.views.append(view)

    @property
    def last(self):  # noqa: ANN201
        assert self.views, "the page was never rendered"
        return self.views[-1]

    @property
    def progress(self) -> int:
        return self.last.controls.progress_percent


class _Service:
    """The queue service: snapshot + the four operations, all recorded."""

    def __init__(self) -> None:
        self.snapshots: list[HistoryQueueSnapshot] = [
            _queue(completed=1)
        ]
        self.snapshot_calls = 0
        self.scheduled: list = []
        self.ibkr_calls: list[dict] = []
        self.public_calls: list[dict] = []
        self.reset_calls = 0
        self.schedule_result = HistoryScheduleResult(inserted=7, total=1234)

    def snapshot(self) -> HistoryQueueSnapshot:
        self.snapshot_calls += 1
        return self.snapshots[0]

    def schedule_universe(self, universe):  # noqa: ANN001
        self.scheduled.append(universe)
        return self.schedule_result

    def run_ibkr(self, config, *, maximum_jobs, progress=None):  # noqa: ANN001
        self.ibkr_calls.append(
            {"config": config, "maximum_jobs": maximum_jobs, "progress": progress}
        )
        return {"completed": 1}

    def run_public(self, *, maximum_jobs, progress=None):  # noqa: ANN001
        self.public_calls.append(
            {"maximum_jobs": maximum_jobs, "progress": progress}
        )
        return {"completed": 1}

    def reset_failed(self) -> int:
        self.reset_calls += 1
        return 6


class _Submit:
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


def _queue(completed: int = 0, failed: int = 0) -> HistoryQueueSnapshot:
    return HistoryQueueSnapshot(
        jobs=(), pending=0, running=0, completed=completed, failed=failed
    )


def _build(universe: object | None = object(), admitted: bool = True):
    page = _Page()
    service = _Service()
    submit = _Submit(admitted)
    universes = [universe]
    configs: list[object] = []

    orchestrator = HistoryOrchestrator(
        service=service,
        page=page,
        submit_task=submit,
        universe_provider=lambda: universes[0],
        ibkr_config_provider=lambda: configs[-1],
    )
    return orchestrator, page, service, submit, universes, configs


def _logs(orchestrator: HistoryOrchestrator) -> list[str]:
    seen: list[str] = []
    orchestrator.log_requested.connect(seen.append)
    return seen


def _changes(orchestrator: HistoryOrchestrator) -> list:
    seen: list = []
    orchestrator.history_changed.connect(lambda: seen.append(1))
    return seen


def _refusals(orchestrator: HistoryOrchestrator) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    orchestrator.refused.connect(lambda title, message: seen.append((title, message)))
    return seen


# -- scheduling ---------------------------------------------------------


def test_scheduling_without_a_universe_is_refused() -> None:
    """Spec 21: refuse, and do not dial.  The window owns the dialog."""

    orchestrator, page, service, _submit, universes, _configs = _build()
    universes[0] = None
    refusals = _refusals(orchestrator)

    orchestrator.request_schedule()

    assert service.scheduled == []
    assert page.views == []
    assert refusals == [(MISSING_UNIVERSE_TITLE, MISSING_UNIVERSE_MESSAGE)]


def test_scheduling_hands_the_providers_universe_to_the_service() -> None:
    """Identity: the object scheduled is the one the provider returned."""

    universe = object()
    orchestrator, _page, service, _submit, universes, _configs = _build(
        universe=universe
    )
    orchestrator.request_schedule()

    assert len(service.scheduled) == 1
    assert service.scheduled[0] is universe


def test_scheduling_renders_announces_and_logs() -> None:
    orchestrator, page, service, _submit, _universes, _configs = _build()
    changes = _changes(orchestrator)
    logs = _logs(orchestrator)

    orchestrator.request_schedule()

    assert len(page.views) == 1
    assert changes == [1]
    assert len(logs) == 1
    assert "新增 7 个" in logs[0]
    assert "队列合计 1,234 个" in logs[0]


def test_the_universe_is_read_from_the_provider_each_time() -> None:
    """The provider is the boundary, so a later universe is the one used."""

    first = object()
    second = object()
    orchestrator, _page, service, _submit, universes, _configs = _build(
        universe=first
    )
    orchestrator.request_schedule()

    universes[0] = second
    orchestrator.request_schedule()

    assert [id(u) for u in service.scheduled] == [id(first), id(second)]


# -- IBKR run -----------------------------------------------------------


def test_an_ibkr_run_shows_the_starting_progress() -> None:
    """Spec 23: progress 1 is painted before the task is submitted."""

    orchestrator, page, _service, submit, _universes, configs = _build()
    configs.append(object())

    orchestrator.request_run_ibkr(37)

    assert page.progress == 1
    assert len(submit.calls) == 1
    assert submit.last["resource_group"] == HISTORY_RESOURCE_GROUP
    assert submit.last["start_message"] == IBKR_START_MESSAGE


def test_an_ibkr_run_reads_the_latest_config() -> None:
    """Spec 23: the config is read when the task *runs*, not when it is queued.

    Appending a new config before each request would pass even if the provider
    were called once at request time -- the captured copy would be the right one
    both times.  The change that matters happens *between* the request and the
    run, because that is when Settings commits a new connection and the task is
    still sitting in the queue.  So this submits the task first and only then
    swaps the provider's answer.
    """

    orchestrator, _page, service, submit, _universes, configs = _build()
    first = object()
    configs.append(first)

    # Queued with ``first`` available.
    orchestrator.request_run_ibkr(37)

    # Settings commits while the task is still queued.
    second = object()
    configs.append(second)

    submit.last["task"](lambda _message: None)

    assert service.ibkr_calls[0]["config"] is second, (
        "the run must read the config live, not the one that was current when "
        "it was queued"
    )

    # And the second run reads the provider again rather than reusing ``second``.
    third = object()
    configs.append(third)
    orchestrator.request_run_ibkr(9)
    submit.calls[1]["task"](lambda _message: None)

    assert service.ibkr_calls[1]["config"] is third


def test_the_ibkr_task_translates_domain_progress() -> None:
    orchestrator, _page, service, submit, _universes, configs = _build()
    configs.append(object())
    orchestrator.request_run_ibkr(37)

    ui: list[str] = []
    submit.last["task"](ui.append)
    service.ibkr_calls[0]["progress"](1, 25, "AAPL", "完成 1200 根")

    assert ui == ["1/25 AAPL：完成 1200 根"]


def test_the_batch_size_reaches_the_service() -> None:
    orchestrator, _page, service, submit, _universes, configs = _build()
    configs.append(object())
    orchestrator.request_run_ibkr(37)
    submit.last["task"](lambda _message: None)

    assert service.ibkr_calls[0]["maximum_jobs"] == 37


# -- public fallback ----------------------------------------------------


def test_a_public_run_does_not_reset_failures_itself() -> None:
    """Spec 24: the reset is the service's rule, not a second copy of it."""

    orchestrator, _page, service, submit, _universes, _configs = _build()
    orchestrator.request_run_public(9)

    assert service.reset_calls == 0
    assert submit.last["resource_group"] == HISTORY_RESOURCE_GROUP
    assert submit.last["start_message"] == PUBLIC_START_MESSAGE

    submit.last["task"](lambda _message: None)
    assert service.public_calls[0]["maximum_jobs"] == 9
    assert service.reset_calls == 0


# -- retry --------------------------------------------------------------


def test_retry_failed_reports_the_service_count() -> None:
    orchestrator, page, service, _submit, _universes, _configs = _build()
    logs = _logs(orchestrator)

    orchestrator.retry_failed()

    assert service.reset_calls == 1
    assert len(page.views) == 1
    assert logs == ["已将 6 个失败任务放回待处理队列。"]


# -- completion ---------------------------------------------------------


def test_success_computes_the_completed_percentage() -> None:
    orchestrator, page, _service, submit, _universes, configs = _build()
    configs.append(object())
    changes = _changes(orchestrator)
    logs = _logs(orchestrator)
    orchestrator.request_run_ibkr(37)

    submit.last["on_success"]({"completed": 3, "failed": 1})

    assert page.progress == 75
    assert changes == [1]
    assert "累计完成 3" in logs[0]
    assert "失败 1" in logs[0]


def test_success_with_no_jobs_is_zero_not_a_division_error() -> None:
    orchestrator, page, _service, submit, _universes, configs = _build()
    configs.append(object())
    orchestrator.request_run_ibkr(37)

    submit.last["on_success"]({})

    assert page.progress == 0


def test_failure_resets_the_progress() -> None:
    """Spec 25: a failed batch puts the bar back to zero."""

    orchestrator, page, _service, submit, _universes, configs = _build()
    configs.append(object())
    orchestrator.request_run_ibkr(37)
    assert page.progress == 1

    submit.last["on_failure"]("boom")

    assert page.progress == 0


def test_the_failure_handler_is_the_orchestrators_own() -> None:
    """Spec 25: the orchestrator supplies ``on_failure``; the window's generic
    ``_task_failed`` still runs, because the task boundary chains them."""

    orchestrator, _page, _service, submit, _universes, configs = _build()
    configs.append(object())
    orchestrator.request_run_ibkr(37)

    assert callable(submit.last["on_failure"])


# -- the queue is never mirrored ---------------------------------------


def test_every_render_reads_a_fresh_snapshot() -> None:
    """Spec 18/26: no cached ``HistoryQueueSnapshot`` anywhere.

    One call per render, and the second render sees the queue the service now
    reports -- which is what a cached mirror could not do.
    """

    orchestrator, page, service, _submit, _universes, _configs = _build()

    orchestrator.render_current()
    assert service.snapshot_calls == 1

    service.snapshots[0] = _queue(completed=9)
    orchestrator.render_current()

    assert service.snapshot_calls == 2
    assert "完成 9" in page.last.summary


def test_the_progress_bar_is_presentation_not_queue_truth() -> None:
    """The one stored value is the percentage, and the page is drawn from it."""

    orchestrator, page, _service, submit, _universes, configs = _build()
    configs.append(object())
    orchestrator.request_run_ibkr(37)
    submit.last["on_success"]({"completed": 1, "failed": 1})

    orchestrator.render_current()

    assert page.progress == 50
