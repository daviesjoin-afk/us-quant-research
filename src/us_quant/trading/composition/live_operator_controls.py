"""Composition root for durable desktop Live operator controls."""

from __future__ import annotations

from pathlib import Path

from us_quant.trading.adapters.sqlite.live_safety_repository import (
    SQLiteLiveSafetyRepository,
)
from us_quant.trading.application.live_operator_controls import (
    LiveOperatorControlsApplication,
)


def build_live_operator_controls_application(
    path: str | Path,
) -> LiveOperatorControlsApplication:
    """Build the operator application over the durable SQLite safety record."""

    return LiveOperatorControlsApplication(SQLiteLiveSafetyRepository(path))


__all__ = ["build_live_operator_controls_application"]
