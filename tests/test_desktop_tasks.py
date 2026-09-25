"""The task controller is the worker collection's only owner (G1).

``DesktopTaskController`` owns the mutable worker collection and the
deterministic admission rule; the window owns Qt signal wiring and presentation.
The properties pinned here are the ones that make that split safe: every query
recomputes from the collection at call time, nothing that leaves the controller
can be used to mutate it, and registration refuses the two shapes that would
make "one task per resource group" a lie (a re-registered identity and a second
worker in a busy group).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from us_quant.desktop_tasks import DesktopTaskController


@dataclass
class Worker:
    resource_group: str
    running: bool = True

    def isRunning(self) -> bool:
        return self.running


def test_resource_group_admission_and_finish_are_deterministic() -> None:
    controller = DesktopTaskController[Worker]()
    first = Worker("broker")
    other = Worker("research")
    controller.register(first)

    assert not controller.can_start("broker")
    assert controller.can_start("research")
    controller.register(other)
    assert controller.active_count == 2
    assert controller.finish(first)
    assert controller.can_start("broker")
    assert not controller.finish(first)


def test_duplicate_group_registration_is_rejected() -> None:
    controller = DesktopTaskController[Worker]()
    controller.register(Worker("scan"))

    with pytest.raises(RuntimeError, match="scan"):
        controller.register(Worker("scan"))


def test_duplicate_identity_registration_is_rejected() -> None:
    """The same object must not be registered twice."""

    controller = DesktopTaskController[Worker]()
    worker = Worker("research")
    controller.register(worker)

    with pytest.raises(ValueError, match="already registered"):
        controller.register(worker)
    assert controller.active_count == 1, "the duplicate was admitted"


def test_active_count_counts_only_running_workers() -> None:
    controller = DesktopTaskController[Worker]()
    running = Worker("research", running=True)
    finished = Worker("broker", running=False)
    controller.register(running)
    controller.register(finished)

    assert controller.active_count == 1
    assert controller.running_workers() == (running,)

    finished.running = True
    assert controller.active_count == 2
    finished.running = False
    assert controller.active_count == 1


def test_running_workers_is_an_immutable_snapshot() -> None:
    """The query may not hand out a handle that can mutate the collection."""

    controller = DesktopTaskController[Worker]()
    first = Worker("research")
    second = Worker("universe")
    controller.register(first)
    controller.register(second)

    snapshot = controller.running_workers()
    assert isinstance(snapshot, tuple)
    assert list(snapshot) == [first, second]

    # A caller that tried to change the snapshot would fail, and either way the
    # collection itself is untouched.
    with pytest.raises((AttributeError, TypeError)):
        snapshot.append(first)  # type: ignore[attr-defined]
    with pytest.raises((AttributeError, TypeError)):
        snapshot.clear()  # type: ignore[attr-defined]
    assert controller.has_running_workers() is True
    assert len(controller.running_workers()) == 2


def test_the_shutdown_query_reads_the_collection_at_call_time() -> None:
    """``has_running_workers`` is a live question, not a cached verdict."""

    controller = DesktopTaskController[Worker]()
    assert controller.has_running_workers() is False

    worker = Worker("research")
    controller.register(worker)
    assert controller.has_running_workers() is True

    controller.finish(worker)
    assert controller.has_running_workers() is False
    assert controller.running_workers() == ()


def test_finish_removes_exactly_one_identity() -> None:
    controller = DesktopTaskController[Worker]()
    first = Worker("research")
    second = Worker("universe")
    controller.register(first)
    controller.register(second)

    assert controller.finish(first) is True
    assert first not in controller.running_workers()
    assert second in controller.running_workers()
    assert controller.finish(first) is False, "the identity was removed twice"
    assert controller.has_running_workers() is True


def test_the_internal_collection_is_not_exposed() -> None:
    """The retired compatibility view must not come back."""

    controller = DesktopTaskController[Worker]()
    controller.register(Worker("research"))

    assert not hasattr(controller, "workers") or not isinstance(
        getattr(controller, "workers", None), list
    )
