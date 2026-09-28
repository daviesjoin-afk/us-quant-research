"""Durable operator actions for the desktop Live safety panel."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from us_quant.trading.domain.live_safety import LiveSafetyRecord
from us_quant.trading.ports.live_safety_repository import (
    LiveSafetyConflict,
    LiveSafetyRepositoryPort,
)


class LiveOperatorControlError(RuntimeError):
    """A requested Live operator action could not be persisted safely."""


class LiveOperatorControlsApplication:
    """Read durable Live safety and latch kill without owning any widgets."""

    def __init__(
        self,
        repository: LiveSafetyRepositoryPort,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._now = now or (lambda: datetime.now(timezone.utc))

    def snapshot(self) -> LiveSafetyRecord:
        return self._repository.load()

    def engage_kill(self, *, reason: str) -> LiveSafetyRecord:
        at = self._now()
        if at.tzinfo is None or at.utcoffset() is None:
            raise LiveOperatorControlError("Live kill clock must be timezone-aware")
        if not isinstance(reason, str) or not reason.strip():
            raise LiveOperatorControlError("Live kill reason must not be blank")
        for _ in range(8):
            current = self._repository.load()
            if current.kill_latch.is_latched:
                return current
            replacement = LiveSafetyRecord(
                current.revision + 1,
                current.authorization,
                current.kill_latch.engage(at=at, reason=reason),
                current.recovery_latch,
            )
            try:
                self._repository.save(
                    expected_revision=current.revision,
                    replacement=replacement,
                )
                return replacement
            except LiveSafetyConflict:
                continue
        raise LiveOperatorControlError(
            "Live safety state kept changing while engaging kill"
        )


__all__ = ["LiveOperatorControlError", "LiveOperatorControlsApplication"]
