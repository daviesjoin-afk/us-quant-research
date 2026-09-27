"""Canonical publication observer for supervisor action completion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from us_quant.trading.domain.paper_autonomy_supervisor import (
    PaperAutonomyActionStatus,
    PaperAutonomyActionType,
    PaperAutonomyEventCode,
)
from us_quant.trading.ports.paper_autonomy_action_repository import (
    PaperAutonomyActionRecord,
    PaperAutonomyActionRepositoryError,
    PaperAutonomyActionRepositoryPort,
)


@dataclass(frozen=True, slots=True)
class _PendingCompletion:
    action_key: str
    action: PaperAutonomyActionType
    status: PaperAutonomyActionStatus
    detail: str
    event_code: PaperAutonomyEventCode


class PaperAutonomyCompletionObserver:
    """Complete only the one matching unresolved action from canonical facts."""

    def __init__(
        self,
        *,
        actions: PaperAutonomyActionRepositoryPort,
        emit: Callable[[PaperAutonomyEventCode, str], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._actions = actions
        self._emit = emit
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._pending: dict[str, _PendingCompletion] = {}
        self._completed: dict[str, PaperAutonomyActionStatus] = {}
        self._last_terminal_by_type: dict[
            PaperAutonomyActionType, PaperAutonomyActionStatus
        ] = {}

    def preparation_ready(self) -> None:
        self._observe(
            PaperAutonomyActionType.PREPARE,
            PaperAutonomyActionStatus.SUCCEEDED,
            "canonical Paper preparation reached READY",
            PaperAutonomyEventCode.AUTONOMY_PREPARE_SUCCEEDED,
        )

    def preparation_failed(self, detail: str) -> None:
        self._observe(
            PaperAutonomyActionType.PREPARE,
            PaperAutonomyActionStatus.FAILED,
            detail,
            PaperAutonomyEventCode.ACTION_FAILED,
        )

    def session_running(self) -> None:
        self._observe(
            PaperAutonomyActionType.START,
            PaperAutonomyActionStatus.SUCCEEDED,
            "canonical Paper RUNNING publication observed",
            PaperAutonomyEventCode.AUTONOMY_START_SUCCEEDED,
        )

    def launch_failed(self, detail: str) -> None:
        self._observe(
            PaperAutonomyActionType.START,
            PaperAutonomyActionStatus.FAILED,
            detail,
            PaperAutonomyEventCode.ACTION_FAILED,
        )

    def session_paused(self) -> None:
        self._observe(
            PaperAutonomyActionType.PAUSE_ENTRIES,
            PaperAutonomyActionStatus.SUCCEEDED,
            "canonical Paper paused publication observed",
            PaperAutonomyEventCode.AUTONOMY_PAUSE_SUCCEEDED,
        )

    def session_resumed(self) -> None:
        self._observe(
            PaperAutonomyActionType.RESUME_ENTRIES,
            PaperAutonomyActionStatus.SUCCEEDED,
            "canonical Paper resumed publication observed",
            PaperAutonomyEventCode.AUTONOMY_RESUME_SUCCEEDED,
        )

    def session_finalized(self) -> None:
        self._observe(
            PaperAutonomyActionType.STOP,
            PaperAutonomyActionStatus.SUCCEEDED,
            "Paper session_finalized publication observed",
            PaperAutonomyEventCode.AUTONOMY_STOP_SUCCEEDED,
        )

    def flush_pending(self) -> None:
        """Flush a synchronous publication buffered while its action was CLAIMED."""

        if not self._pending:
            return
        unresolved = {row.action_key: row for row in self._actions.unresolved()}
        for action_key, completion in tuple(self._pending.items()):
            row = unresolved.get(action_key)
            if row is None:
                self._pending.pop(action_key)
                continue
            if row.status is PaperAutonomyActionStatus.CLAIMED:
                continue
            if row.status is not PaperAutonomyActionStatus.REQUESTED:
                raise PaperAutonomyActionRepositoryError(
                    "a buffered completion conflicts with the action ledger"
                )
            self._write(completion)

    def _observe(
        self,
        action: PaperAutonomyActionType,
        status: PaperAutonomyActionStatus,
        detail: str,
        code: PaperAutonomyEventCode,
    ) -> None:
        matches = tuple(
            row for row in self._actions.unresolved() if row.action is action
        )
        if not matches:
            terminal = self._last_terminal_by_type.get(action)
            if terminal is not None and terminal is not status:
                raise PaperAutonomyActionRepositoryError(
                    "conflicting terminal canonical publications were observed"
                )
            return
        if len(matches) != 1:
            raise PaperAutonomyActionRepositoryError(
                "a canonical publication matched multiple unresolved actions"
            )
        row = matches[0]
        completion = _PendingCompletion(
            row.action_key, action, status, detail, code
        )
        previous = self._completed.get(row.action_key)
        if previous is not None:
            if previous is status:
                return
            raise PaperAutonomyActionRepositoryError(
                "conflicting canonical completion publications were observed"
            )
        pending = self._pending.get(row.action_key)
        if pending is not None:
            if pending.status is status:
                return
            raise PaperAutonomyActionRepositoryError(
                "conflicting completion publications are buffered"
            )
        if row.status is PaperAutonomyActionStatus.CLAIMED:
            self._pending[row.action_key] = completion
            return
        if row.status is not PaperAutonomyActionStatus.REQUESTED:
            raise PaperAutonomyActionRepositoryError(
                "a completion publication conflicts with a terminal action"
            )
        self._write(completion)

    def _write(self, completion: _PendingCompletion) -> None:
        self._actions.complete(
            action_key=completion.action_key,
            status=completion.status,
            completed_at=self._clock(),
            detail=completion.detail,
        )
        self._pending.pop(completion.action_key, None)
        self._completed[completion.action_key] = completion.status
        self._last_terminal_by_type[completion.action] = completion.status
        self._emit_event(completion.event_code, completion.detail)

    def _emit_event(self, code: PaperAutonomyEventCode, detail: str) -> None:
        if self._emit is not None:
            self._emit(code, detail)


__all__ = ["PaperAutonomyCompletionObserver"]
