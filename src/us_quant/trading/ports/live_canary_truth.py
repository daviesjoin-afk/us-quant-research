"""Fresh account facts consumed by the Live canary authorization guard."""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.live_canary import LiveCanaryTruth


class LiveCanaryTruthUnavailable(RuntimeError):
    """The current broker account truth cannot be read safely."""


class LiveCanaryTruthPort(Protocol):
    def snapshot(self) -> LiveCanaryTruth: ...


__all__ = ["LiveCanaryTruthPort", "LiveCanaryTruthUnavailable"]
