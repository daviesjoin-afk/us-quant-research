"""A narrow callback bridge into the existing Desktop capability owners."""

from __future__ import annotations

from collections.abc import Callable

from us_quant.trading.domain.paper_preparation import PaperPreparationRequest
from us_quant.trading.ports.paper_autonomy_supervisor import (
    PaperAutonomyRequestOutcome,
)


class PaperAutonomyDesktopExecutor:
    """Translate callback admission into supervisor outcomes without catching errors."""

    def __init__(
        self,
        *,
        prepare: Callable[[PaperPreparationRequest], bool],
        start: Callable[[], bool],
        pause: Callable[[], bool],
        resume: Callable[[], bool],
        stop: Callable[[], bool],
    ) -> None:
        self._prepare = prepare
        self._start = start
        self._pause = pause
        self._resume = resume
        self._stop = stop

    def request_prepare(
        self, request: PaperPreparationRequest
    ) -> PaperAutonomyRequestOutcome:
        return _outcome(self._prepare(request), "Execution preparation")

    def request_start(self) -> PaperAutonomyRequestOutcome:
        return _outcome(self._start(), "Paper start")

    def request_pause(self) -> PaperAutonomyRequestOutcome:
        return _outcome(self._pause(), "Paper pause")

    def request_resume(self) -> PaperAutonomyRequestOutcome:
        return _outcome(self._resume(), "Paper resume")

    def request_stop(self) -> PaperAutonomyRequestOutcome:
        return _outcome(self._stop(), "Paper stop")


def _outcome(accepted: bool, action: str) -> PaperAutonomyRequestOutcome:
    return PaperAutonomyRequestOutcome(
        accepted=bool(accepted),
        detail=(
            f"{action} request admitted"
            if accepted
            else f"{action} request refused"
        ),
    )


__all__ = ["PaperAutonomyDesktopExecutor"]
