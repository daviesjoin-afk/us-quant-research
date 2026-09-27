"""Operator-reviewed closure of ambiguous Paper autonomy actions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from us_quant.trading.domain.paper_autonomy import (
    PaperAutonomyMode,
)
from us_quant.trading.domain.paper_autonomy_supervisor import (
    PaperAutonomyActionStatus,
    PaperAutonomySupervisorViolation,
)
from us_quant.trading.ports.paper_autonomy_action_repository import (
    PaperAutonomyActionRecord,
    PaperAutonomyActionRepositoryPort,
)
from us_quant.trading.ports.paper_autonomy_intent_reader import (
    PaperAutonomyIntentReaderPort,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PaperAutonomyRecoveryApplication:
    """Read the action ledger and close unknowns after an operator review.

    This authority cannot establish an action outcome or change autonomy
    intent. It only records that an operator inspected an ambiguous action
    while autonomy was already disabled.
    """

    def __init__(
        self,
        intent_reader: PaperAutonomyIntentReaderPort,
        actions: PaperAutonomyActionRepositoryPort,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._intent_reader = intent_reader
        self._actions = actions
        self._clock = clock or _utc_now

    def unresolved(self) -> tuple[PaperAutonomyActionRecord, ...]:
        """Return ledger rows whose action outcome is still unknown."""

        return self._actions.unresolved()

    def resolve_unknown(
        self,
        *,
        action_key: str,
        expected_status: PaperAutonomyActionStatus,
        reason: str,
        resolved_at: datetime | None = None,
    ) -> PaperAutonomyActionRecord:
        """Close one unresolved row without claiming success or failure."""

        intent = self._intent_reader.load_intent()
        if intent.mode is not PaperAutonomyMode.DISABLED:
            raise PaperAutonomySupervisorViolation(
                "disable Paper autonomy before resolving an ambiguous action"
            )
        if expected_status not in (
            PaperAutonomyActionStatus.CLAIMED,
            PaperAutonomyActionStatus.REQUESTED,
        ):
            raise PaperAutonomySupervisorViolation(
                "only claimed or requested actions can be resolved"
            )
        moment = resolved_at or self._clock()
        self._actions.resolve_unknown(
            action_key=action_key,
            expected_status=expected_status,
            resolved_at=moment,
            detail=reason,
        )
        record = self._actions.get(action_key)
        if record is None:  # pragma: no cover - adapter contract violation
            raise PaperAutonomySupervisorViolation(
                "the resolved action could not be read back"
            )
        return record


__all__ = ["PaperAutonomyRecoveryApplication"]
