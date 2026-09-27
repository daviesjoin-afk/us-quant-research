"""Qt timer host for the already-composed Paper autonomy supervisor."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal


_LOGGER = logging.getLogger(__name__)
_GENERIC_FAILURE = (
    "Paper autonomy tick failed before a safe decision could be completed"
)


class PaperAutonomyHost(QObject):
    """Schedule one supervisor tick per timer event; carry no trading decisions."""

    tick_completed = Signal(object)
    tick_failed = Signal(str)

    def __init__(self, supervisor: object, *, tick_interval_seconds: int, parent=None):
        super().__init__(parent)
        if tick_interval_seconds <= 0:
            raise ValueError("tick interval must be positive")
        self._supervisor = supervisor
        self._timer = QTimer(self)
        self._timer.setInterval(tick_interval_seconds * 1000)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        try:
            result = self._supervisor.tick()
        except Exception:  # noqa: BLE001 - generic telemetry; no retry here
            _LOGGER.exception(_GENERIC_FAILURE)
            self.tick_failed.emit(_GENERIC_FAILURE)
            return
        self.tick_completed.emit(result)


__all__ = ["PaperAutonomyHost"]
