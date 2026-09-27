"""Read-only access to the operator's Paper autonomy intent."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from us_quant.trading.domain.paper_autonomy import PaperAutonomyIntent


@runtime_checkable
class PaperAutonomyIntentReaderPort(Protocol):
    """The fresh intent snapshot needed before resolving an unknown action."""

    def load_intent(self) -> PaperAutonomyIntent:
        """Return the current validated intent and its consistent history."""


__all__ = ["PaperAutonomyIntentReaderPort"]
