"""Small admission and ownership boundary for desktop background workers."""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar


class DesktopWorker(Protocol):
    resource_group: str

    def isRunning(self) -> bool: ...


WorkerT = TypeVar("WorkerT", bound=DesktopWorker)


class DesktopTaskController(Generic[WorkerT]):
    """Own worker registration and serialize tasks by resource group.

    Qt signal wiring and all presentation remain in ``MainWindow``.  This
    object only owns the mutable worker collection and deterministic admission
    rule, which makes the concurrency boundary independently testable.

    The collection itself never leaves this class.  Callers who need to know
    what is running ask through :meth:`running_workers` /
    :meth:`has_running_workers` / :attr:`active_count`, which return snapshots
    or counts -- never a handle that could be appended to from outside, and
    never an alias that could drift from the collection it came from.
    """

    def __init__(self) -> None:
        self._workers: list[WorkerT] = []

    @property
    def active_count(self) -> int:
        """How many registered workers are still running."""

        return sum(1 for worker in self._workers if worker.isRunning())

    def running_workers(self) -> tuple[WorkerT, ...]:
        """The running workers, as an immutable snapshot.

        A tuple on purpose: a caller that received the internal list could
        append to it, remove from it, or hold a stale view of it.  Every read
        here recomputes from the collection, so the answer is current even
        after a worker finished while the snapshot was being taken.
        """

        return tuple(
            worker for worker in self._workers if worker.isRunning()
        )

    def has_running_workers(self) -> bool:
        """Whether any registered worker is still running."""

        return any(worker.isRunning() for worker in self._workers)

    def can_start(self, resource_group: str) -> bool:
        return not any(
            worker.isRunning() and worker.resource_group == resource_group
            for worker in self._workers
        )

    def register(self, worker: WorkerT) -> None:
        if any(existing is worker for existing in self._workers):
            raise ValueError("desktop worker is already registered")
        if not self.can_start(worker.resource_group):
            raise RuntimeError(
                f"resource group is already active: {worker.resource_group}"
            )
        self._workers.append(worker)

    def finish(self, worker: WorkerT) -> bool:
        for index, existing in enumerate(self._workers):
            if existing is worker:
                del self._workers[index]
                return True
        return False
