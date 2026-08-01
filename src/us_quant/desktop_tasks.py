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
    """

    def __init__(self) -> None:
        self._workers: list[WorkerT] = []

    @property
    def workers(self) -> list[WorkerT]:
        """Compatibility view used by existing cancellation/status code."""

        return self._workers

    @property
    def active_count(self) -> int:
        return sum(1 for worker in self._workers if worker.isRunning())

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
