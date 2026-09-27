"""Persistence boundary for Live operator authorization and kill state."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol, runtime_checkable

from us_quant.trading.domain.live_safety import LiveSafetyRecord


class LiveSafetyRepositoryError(RuntimeError):
    """Base error for failed or unreadable Live safety persistence."""


class LiveSafetyConflict(LiveSafetyRepositoryError):
    """A stale safety-state writer was refused without overwriting newer state."""


class LiveSafetyStoreUnreadable(LiveSafetyRepositoryError):
    """Stored safety data cannot be trusted and must not be interpreted permissively."""


@runtime_checkable
class LiveSafetyRepositoryPort(Protocol):
    """Store authorization and kill latch only, never process-local arm state."""

    def load(self) -> LiveSafetyRecord:
        """Return durable state, or the disabled, unlatched initial record."""

    def save(self, *, expected_revision: int, replacement: LiveSafetyRecord) -> None:
        """Atomically compare-and-swap the complete durable safety record."""

    def execution_lease(self) -> AbstractContextManager[LiveSafetyRecord]:
        """Serialize a broker submit against every durable authorization update."""


__all__ = [
    "LiveSafetyConflict",
    "LiveSafetyRepositoryError",
    "LiveSafetyRepositoryPort",
    "LiveSafetyStoreUnreadable",
]
