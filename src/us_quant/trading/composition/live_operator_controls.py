"""Composition root for durable desktop Live operator controls."""

from __future__ import annotations

from pathlib import Path

from us_quant.trading.adapters.sqlite.live_safety_repository import (
    SQLiteLiveSafetyRepository,
)
from us_quant.trading.application.live_operator_controls import (
    LiveOperatorControlsApplication,
)
from us_quant.trading.domain.live_safety import LiveSafetyRecord
from us_quant.trading.ports.live_safety_repository import (
    LiveSafetyRepositoryError,
)


class _UnavailableLiveSafetyRepository:
    """Keep optional Live status fail-closed without blocking Paper startup."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def load(self) -> LiveSafetyRecord:
        raise LiveSafetyRepositoryError(
            f"Live safety store is unavailable: {self._error}"
        ) from self._error

    def save(self, *, expected_revision: int, replacement: LiveSafetyRecord) -> None:
        del expected_revision, replacement
        raise LiveSafetyRepositoryError(
            f"Live safety store is unavailable: {self._error}"
        ) from self._error


def build_live_operator_controls_application(
    path: str | Path,
) -> LiveOperatorControlsApplication:
    """Build the operator application over the durable SQLite safety record."""

    try:
        repository = SQLiteLiveSafetyRepository(path)
    except (LiveSafetyRepositoryError, OSError) as error:
        repository = _UnavailableLiveSafetyRepository(error)
    return LiveOperatorControlsApplication(repository)


__all__ = ["build_live_operator_controls_application"]
