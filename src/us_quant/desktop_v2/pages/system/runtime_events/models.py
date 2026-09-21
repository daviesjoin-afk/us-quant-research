"""Immutable view models for the Runtime Events workspace.

Everything here is a display fact: a string the operator reads, or the tone a
row should be coloured with.  Nothing here can fetch an event, resolve one or
read the database -- the page is handed these and draws them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RuntimeEventTone(str, Enum):
    """How a runtime-event row should be coloured, in presentation terms."""

    NEUTRAL = "neutral"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RuntimeEventRowView:
    """One runtime event as the table shows it.

    ``event_id`` is the full, stable integer identity; the page resolves an
    action by this value, never by a row index that sorting can move.
    """

    event_id: int
    event_id_text: str
    occurred_at: str
    severity: str
    severity_text: str
    component: str
    code: str
    message: str
    status_text: str
    tone: RuntimeEventTone = RuntimeEventTone.NEUTRAL


@dataclass(frozen=True, slots=True)
class RuntimeEventsPageView:
    """Everything one render of the Runtime Events workspace draws."""

    error_count: str
    warning_count: str
    active_task_count: str

    last_export_value: str
    last_export_note: str

    rows: tuple[RuntimeEventRowView, ...]

    info_text: str


__all__ = [
    "RuntimeEventRowView",
    "RuntimeEventTone",
    "RuntimeEventsPageView",
]
