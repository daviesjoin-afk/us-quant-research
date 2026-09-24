"""Behaviour tests for ``RuntimeEventsOrchestrator`` (v2O-F1).

No window, no event loop and no ``sleep``: the store, the page, the clock and
the scheduler are all fakes, so every sequencing claim is decided by the code
under test rather than by wall-clock timing.  The one thing that is real is the
public API -- these tests drive ``record`` / ``refresh`` / ``resolve`` /
``export`` / ``notify_task_count_changed`` exactly as the composition root does.

The properties covered, in the order the round's acceptance list states them:
record writes once, a burst arms one flush, a flush re-reads the store, a
superseded flush paints nothing, an explicit refresh and a resolve are
immediate, the task count is read per paint rather than captured, and an export
outcome is sequenced so that neither a failure nor a success can be reported
twice or in the wrong order.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from us_quant.desktop_v2.orchestration.system.runtime_events.models import (
    RuntimeEventsEnvironment,
)
from us_quant.desktop_v2.orchestration.system.runtime_events.orchestrator import (
    EXPORT_FAILED_TITLE,
    NO_SELECTION_MESSAGE,
    NO_SELECTION_TITLE,
    RECENT_EVENT_LIMIT,
    REFRESH_COALESCE_SECONDS,
    RuntimeEventsOrchestrator,
)
from us_quant.runtime_events import RuntimeEvent


ENVIRONMENT = RuntimeEventsEnvironment(
    version="0.19.0",
    resource_root="R:/resources",
    state_root="S:/state",
    runtime_root="S:/state/runtime",
    exports_root="S:/state/exports",
)


def _event(
    event_id: int,
    *,
    severity: str = "info",
    component: str = "market_data",
    code: str = "STREAM_START",
    message: str | None = None,
    resolved: bool = False,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=event_id,
        occurred_at="2026-01-01T00:00:00+00:00",
        severity=severity,
        component=component,
        code=code,
        message=message if message is not None else f"event {event_id}",
        resolved=resolved,
    )


# -- fakes ---------------------------------------------------------------


class FakeStore:
    """The canonical-truth stand-in: records every call, keeps the rows."""

    def __init__(self, events: tuple[RuntimeEvent, ...] = ()) -> None:
        self.added: list[dict[str, str]] = []
        self.resolved: list[int] = []
        self.reads: list[int] = []
        self.fail_with: Exception | None = None
        self._events = list(events)
        self._next_id = 900

    def add(
        self,
        *,
        severity: str,
        component: str,
        code: str,
        message: str,
    ) -> RuntimeEvent:
        if self.fail_with is not None:
            raise self.fail_with
        self.added.append(
            {
                "severity": severity,
                "component": component,
                "code": code,
                "message": message,
            }
        )
        self._next_id += 1
        event = _event(
            self._next_id,
            severity=severity,
            component=component,
            code=code,
            message=message,
        )
        self._events.insert(0, event)
        return event

    def list_recent(self, limit: int = RECENT_EVENT_LIMIT) -> tuple:
        self.reads.append(limit)
        return tuple(self._events[:limit])

    def resolve(self, event_id: int) -> None:
        self.resolved.append(event_id)
        self._events = [
            replace(event, resolved=True) if event.event_id == event_id else event
            for event in self._events
        ]

    def inject(self, event: RuntimeEvent) -> None:
        """Add a row behind the orchestrator's back.

        Production has exactly one writer, so this exists for one assertion
        only: that a *flush* reads the store when it runs rather than a list
        captured when the burst started.
        """

        self._events.insert(0, event)


class FakePage:
    """The render seam: keeps every view it was handed."""

    def __init__(self) -> None:
        self.views: list[object] = []

    def render(self, view: object) -> None:
        self.views.append(view)


class ManualClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ManualTimer:
    """A scheduler a test fires by hand, so no test ever sleeps.

    :meth:`fire` deliberately still delivers a callback that has since been
    disarmed: a real Qt timeout can already be queued when ``disarm`` runs, and
    the orchestrator -- not the timer -- is what has to make that harmless.
    """

    def __init__(self) -> None:
        self.armed: list[float] = []
        self.disarmed = 0
        self._callback = None

    def arm(self, delay_seconds, callback) -> None:
        self.armed.append(delay_seconds)
        self._callback = callback

    def disarm(self) -> None:
        self.disarmed += 1

    def fire(self) -> None:
        assert self._callback is not None, "nothing was armed"
        self._callback()


class TaskCounter:
    def __init__(self, value: int = 0) -> None:
        self.value = value
        self.reads = 0

    def __call__(self) -> int:
        self.reads += 1
        return self.value


class RecordingExporter:
    """The injected cross-capability bundle writer, with no capabilities in it."""

    def __init__(
        self,
        *,
        target: Path | None = None,
        error: Exception | None = None,
    ) -> None:
        self.target = target
        self.error = error
        self.calls: list[tuple] = []

    def __call__(self, events) -> Path:
        self.calls.append(tuple(events))
        if self.error is not None:
            raise self.error
        assert self.target is not None
        return self.target


@dataclass
class Harness:
    orchestrator: RuntimeEventsOrchestrator
    store: FakeStore
    page: FakePage
    clock: ManualClock
    timer: ManualTimer
    tasks: TaskCounter
    exporter: RecordingExporter
    messages: list = field(default_factory=list)


def _harness(
    *,
    events: tuple[RuntimeEvent, ...] = (),
    tasks: int = 0,
    exporter: RecordingExporter | None = None,
) -> Harness:
    store = FakeStore(events)
    page = FakePage()
    clock = ManualClock()
    timer = ManualTimer()
    counter = TaskCounter(tasks)
    bundle = exporter or RecordingExporter(target=Path("C:/exports/none.zip"))
    orchestrator = RuntimeEventsOrchestrator(
        store=store,
        page=page,
        environment=ENVIRONMENT,
        active_task_count=counter,
        export_bundle=bundle,
        clock=clock,
        timer=timer,
    )
    messages: list = []
    orchestrator.information_requested.connect(
        lambda title, message: messages.append(("information", title, message))
    )
    orchestrator.warning_requested.connect(
        lambda title, message: messages.append(("warning", title, message))
    )
    return Harness(
        orchestrator=orchestrator,
        store=store,
        page=page,
        clock=clock,
        timer=timer,
        tasks=counter,
        exporter=bundle,
        messages=messages,
    )


# -- A. record -----------------------------------------------------------


def test_record_writes_once_through_the_store_and_paints_it() -> None:
    harness = _harness()
    event = harness.orchestrator.record(
        severity="warning",
        component="market_data",
        code="STREAM_DROPPED",
        message="一条行情事件",
    )

    assert harness.store.added == [
        {
            "severity": "warning",
            "component": "market_data",
            "code": "STREAM_DROPPED",
            "message": "一条行情事件",
        }
    ]
    assert event.event_id == harness.page.views[-1].rows[0].event_id
    # The first arrival has no previous paint to wait for, so it paints at once.
    assert len(harness.page.views) == 1


def test_a_refused_write_propagates_and_paints_nothing() -> None:
    """A page that repainted over a refused row would be the worst outcome."""

    harness = _harness()
    harness.store.fail_with = ValueError("unsupported event severity")

    with pytest.raises(ValueError):
        harness.orchestrator.record(
            severity="fatal",
            component="test",
            code="NOPE",
            message="refused",
        )

    assert harness.page.views == []
    assert harness.timer.armed == []
    assert harness.store.added == []


def test_every_paint_reads_the_store_rather_than_a_retained_list() -> None:
    harness = _harness()
    harness.orchestrator.record(
        severity="info", component="test", code="FIRST", message="first"
    )
    harness.orchestrator.refresh()
    assert harness.store.reads == [RECENT_EVENT_LIMIT, RECENT_EVENT_LIMIT]

    latent = _event(4242, code="INJECTED")
    harness.store.inject(latent)
    harness.orchestrator.refresh()

    assert harness.page.views[-1].rows[0].event_id == 4242
    assert len(harness.page.views[-1].rows) == 2


# -- B. coalescing -------------------------------------------------------


def test_a_burst_of_arrivals_arms_exactly_one_flush() -> None:
    harness = _harness()
    harness.orchestrator.record(
        severity="info", component="t", code="A", message="a"
    )
    assert len(harness.page.views) == 1

    for code in ("B", "C", "D"):
        harness.clock.advance(0.1)
        harness.orchestrator.record(
            severity="info", component="t", code=code, message=code
        )

    assert harness.timer.armed == [REFRESH_COALESCE_SECONDS]
    assert len(harness.page.views) == 1

    harness.timer.fire()

    assert len(harness.page.views) == 2
    assert [row.code for row in harness.page.views[-1].rows] == [
        "D",
        "C",
        "B",
        "A",
    ]


def test_the_first_arrival_after_a_quiet_window_paints_at_once() -> None:
    harness = _harness()
    harness.orchestrator.record(
        severity="info", component="t", code="A", message="a"
    )
    harness.clock.advance(REFRESH_COALESCE_SECONDS)
    harness.orchestrator.record(
        severity="info", component="t", code="B", message="b"
    )

    assert harness.timer.armed == []
    assert [view.rows[0].code for view in harness.page.views] == ["A", "B"]


def test_the_flush_reads_the_store_rather_than_the_burst() -> None:
    harness = _harness()
    harness.orchestrator.record(
        severity="info", component="t", code="A", message="a"
    )
    harness.clock.advance(0.2)
    harness.orchestrator.record(
        severity="info", component="t", code="B", message="b"
    )
    assert len(harness.timer.armed) == 1

    latent = _event(777, code="LATENT")
    harness.store.inject(latent)
    harness.timer.fire()

    assert harness.page.views[-1].rows[0].event_id == 777
    assert harness.store.reads[-1] == RECENT_EVENT_LIMIT


def test_a_superseded_flush_paints_nothing() -> None:
    """A late callback must not add a second render to the sequence."""

    harness = _harness()
    harness.orchestrator.record(
        severity="info", component="t", code="A", message="a"
    )
    harness.clock.advance(0.2)
    harness.orchestrator.record(
        severity="info", component="t", code="B", message="b"
    )
    assert len(harness.timer.armed) == 1

    before = harness.timer.disarmed
    harness.orchestrator.refresh()
    assert len(harness.page.views) == 2
    assert harness.timer.disarmed == before + 1

    harness.timer.fire()
    assert len(harness.page.views) == 2


def test_the_coalescing_state_survives_a_superseded_flush() -> None:
    harness = _harness()
    harness.orchestrator.record(
        severity="info", component="t", code="A", message="a"
    )
    harness.clock.advance(0.2)
    harness.orchestrator.record(
        severity="info", component="t", code="B", message="b"
    )
    harness.orchestrator.refresh()
    harness.timer.fire()

    harness.clock.advance(0.1)
    harness.orchestrator.record(
        severity="info", component="t", code="C", message="c"
    )

    # One new arm for the new window -- not two, and not a permanently stuck
    # pending flag either.
    assert len(harness.timer.armed) == 2
    harness.timer.fire()
    assert len(harness.page.views) == 3
    harness.timer.fire()
    assert len(harness.page.views) == 3


# -- C / F. explicit refresh and the live task count ---------------------


def test_explicit_refresh_reads_and_renders_exactly_once() -> None:
    harness = _harness(events=(_event(1), _event(2)))

    harness.orchestrator.refresh()

    assert harness.store.reads == [RECENT_EVENT_LIMIT]
    assert len(harness.page.views) == 1
    assert harness.store.resolved == []
    assert harness.store.added == []


def test_the_task_count_is_read_on_every_paint() -> None:
    harness = _harness()
    assert harness.tasks.value == 0

    harness.orchestrator.refresh()
    harness.tasks.value = 1
    harness.orchestrator.refresh()
    harness.tasks.value = 0
    harness.orchestrator.refresh()

    assert [view.active_task_count for view in harness.page.views] == [
        "0",
        "1",
        "0",
    ]
    assert harness.tasks.reads == 3


def test_a_task_count_burst_coalesces_like_an_event_burst() -> None:
    harness = _harness()
    harness.orchestrator.notify_task_count_changed()
    assert len(harness.page.views) == 1

    for _ in range(5):
        harness.clock.advance(0.05)
        harness.orchestrator.notify_task_count_changed()

    assert harness.timer.armed == [REFRESH_COALESCE_SECONDS]
    assert len(harness.page.views) == 1

    harness.tasks.value = 3
    harness.timer.fire()

    assert harness.page.views[-1].active_task_count == "3"


def test_the_info_panel_is_built_from_the_environment_facts() -> None:
    harness = _harness()
    harness.orchestrator.refresh()

    info = harness.page.views[-1].info_text
    assert "0.19.0" in info
    assert "R:/resources" in info
    assert "S:/state/exports" in info


# -- D / E. resolve ------------------------------------------------------


def test_resolve_none_asks_for_a_selection_and_touches_nothing() -> None:
    harness = _harness(events=(_event(1),))

    harness.orchestrator.resolve(None)

    assert harness.store.resolved == []
    assert harness.store.reads == []
    assert harness.page.views == []
    assert harness.messages == [
        ("information", NO_SELECTION_TITLE, NO_SELECTION_MESSAGE)
    ]


def test_resolve_uses_the_stable_id_and_repaints_immediately() -> None:
    harness = _harness(
        events=(_event(7), _event(3, severity="warning"))
    )

    harness.orchestrator.resolve(3)

    assert harness.store.resolved == [3]
    assert harness.store.reads == [RECENT_EVENT_LIMIT]
    assert len(harness.page.views) == 1
    status = {
        row.event_id: row.status_text for row in harness.page.views[0].rows
    }
    assert status == {7: "待确认", 3: "已确认"}
    assert harness.messages == []


def test_resolve_does_not_wait_out_a_coalescing_window() -> None:
    harness = _harness(events=(_event(7), _event(3)))
    harness.orchestrator.record(
        severity="info", component="t", code="A", message="a"
    )
    harness.clock.advance(0.1)
    assert harness.timer.armed == []

    harness.orchestrator.resolve(3)

    # A resolve inside the window still paints now: the operator's click is not
    # an arrival, so it is not coalesced.
    assert harness.timer.armed == []
    assert harness.page.views[-1].rows[-1].event_id == 3
    assert harness.page.views[-1].rows[-1].status_text == "已确认"


# -- G. export failure ---------------------------------------------------


@pytest.mark.parametrize("error", [OSError("磁盘已满"), ValueError("坏的行")])
def test_a_refused_export_keeps_the_fact_and_records_nothing(error) -> None:
    harness = _harness(exporter=RecordingExporter(error=error))
    successes: list = []
    harness.orchestrator.export_succeeded.connect(successes.append)

    harness.orchestrator.export()

    assert harness.orchestrator.last_export is None
    assert "EXPORT_OK" not in [
        entry["code"] for entry in harness.store.added
    ]
    assert successes == []
    assert harness.messages == [
        ("warning", EXPORT_FAILED_TITLE, str(error))
    ]
    assert harness.page.views == []


def test_a_later_failure_keeps_the_earlier_export_fact() -> None:
    exporter = RecordingExporter(target=Path("C:/exports/first.zip"))
    harness = _harness(exporter=exporter)
    harness.orchestrator.export()
    first = harness.orchestrator.last_export
    assert first == ("first.zip", str(Path("C:/exports/first.zip")))

    exporter.target = None
    exporter.error = OSError("第二次失败")
    harness.orchestrator.export()

    assert harness.orchestrator.last_export == first
    assert [entry["code"] for entry in harness.store.added] == ["EXPORT_OK"]


def test_the_exporter_is_handed_the_stores_current_events() -> None:
    stored = _event(11, code="KEEP")
    exporter = RecordingExporter(target=Path("C:/exports/terminal.zip"))
    harness = _harness(events=(stored,), exporter=exporter)

    harness.orchestrator.export()

    assert exporter.calls == [(stored,)]
    # Two reads of the same limit and no cached list: one for the bundle the
    # exporter receives, one for the paint that follows it.
    assert harness.store.reads == [RECENT_EVENT_LIMIT, RECENT_EVENT_LIMIT]


# -- H. export success ---------------------------------------------------


def test_a_successful_export_sequences_fact_event_paint_then_report() -> None:
    target = Path("C:/exports/terminal-9.zip")
    harness = _harness(exporter=RecordingExporter(target=target))
    successes: list = []
    painted_when_reported: list[int] = []
    harness.orchestrator.export_succeeded.connect(successes.append)
    # The *order* is the claim, so the count of paints is sampled from inside
    # the slot: a report emitted before the repaint would see zero views.
    harness.orchestrator.export_succeeded.connect(
        lambda _target: painted_when_reported.append(len(harness.page.views))
    )

    harness.orchestrator.export()

    assert harness.orchestrator.last_export == (target.name, str(target))
    assert harness.store.added == [
        {
            "severity": "info",
            "component": "export",
            "code": "EXPORT_OK",
            "message": f"终端状态已脱敏导出到 {target}",
        }
    ]
    view = harness.page.views[-1]
    # The export fact is on the card, and the event it just wrote is in the
    # rows: the paint the success reports is the paint that includes the record.
    assert view.last_export_value == "terminal-9.zip"
    assert view.last_export_note == str(target)
    assert view.rows[0].code == "EXPORT_OK"
    assert painted_when_reported == [1]
    assert successes == [target]
    assert harness.messages == []


def test_the_export_paints_exactly_once() -> None:
    """Writing EXPORT_OK must not also arm a flush for the same truth."""

    harness = _harness(
        exporter=RecordingExporter(target=Path("C:/exports/one.zip"))
    )

    harness.orchestrator.export()

    assert len(harness.page.views) == 1
    assert harness.timer.armed == []


def test_a_successful_export_after_a_failure_still_reports_success() -> None:
    exporter = RecordingExporter(error=OSError("第一次失败"))
    harness = _harness(exporter=exporter)
    successes: list = []
    harness.orchestrator.export_succeeded.connect(successes.append)
    harness.orchestrator.export()
    assert successes == []
    assert harness.orchestrator.last_export is None

    exporter.error = None
    exporter.target = Path("C:/exports/second.zip")
    harness.orchestrator.export()

    assert successes == [Path("C:/exports/second.zip")]
    assert harness.orchestrator.last_export == (
        "second.zip",
        str(Path("C:/exports/second.zip")),
    )
