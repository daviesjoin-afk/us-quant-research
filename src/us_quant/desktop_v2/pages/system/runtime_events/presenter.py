"""Projection of runtime-event facts into the Runtime Events view.

This module is Qt-free.  It takes the store's facts -- the recent events, the
active task count, the last export fact and a pre-built info panel -- and
returns one immutable :class:`RuntimeEventsPageView`.  It performs no I/O, owns
no database handle and imports no widget toolkit.

The two translation tables are frozen here because the display contract is
fixed: a severity or a resolution flag renders the same way on every repaint.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from .models import (
    RuntimeEventRowView,
    RuntimeEventsPageView,
    RuntimeEventTone,
)

if TYPE_CHECKING:
    from us_quant.runtime_events import RuntimeEvent


SEVERITY_LABELS: dict[str, str] = {
    "info": "信息",
    "warning": "警告",
    "error": "错误",
}

DEFAULT_EXPORT_VALUE = "无"
DEFAULT_EXPORT_NOTE = "脱敏 CSV / JSON"

_ROW_TONES = {
    "error": RuntimeEventTone.ERROR,
    "warning": RuntimeEventTone.WARNING,
}


def severity_text(severity: str) -> str:
    """Translate a stored severity into its frozen display label."""

    return SEVERITY_LABELS.get(severity, severity)


def status_text(resolved: bool) -> str:
    """Translate a resolution flag into its frozen display label."""

    return "已确认" if resolved else "待确认"


def tone_for(severity: str) -> RuntimeEventTone:
    """Return the row tone for a severity; anything else is neutral."""

    return _ROW_TONES.get(severity, RuntimeEventTone.NEUTRAL)


def _row_view(event: RuntimeEvent) -> RuntimeEventRowView:
    return RuntimeEventRowView(
        event_id=event.event_id,
        event_id_text=str(event.event_id),
        occurred_at=event.occurred_at,
        severity=event.severity,
        severity_text=severity_text(event.severity),
        component=event.component,
        code=event.code,
        message=event.message,
        status_text=status_text(event.resolved),
        tone=tone_for(event.severity),
    )


def _export_fact(
    last_export: tuple[str, str] | None,
) -> tuple[str, str]:
    if last_export is None:
        return DEFAULT_EXPORT_VALUE, DEFAULT_EXPORT_NOTE
    name, path = last_export
    return name, path


def build_runtime_events_view(
    *,
    events: Sequence[RuntimeEvent],
    active_task_count: int,
    last_export: tuple[str, str] | None,
    info_text: str,
) -> RuntimeEventsPageView:
    """Build the immutable view from the window's runtime facts.

    Card counts read *unresolved* events only, matching the old refresh: a
    confirmed error must leave the two cards blank.  The table, by contrast,
    shows every recent event, so its rows are not filtered.
    """

    unresolved = [event for event in events if not event.resolved]
    counts = Counter(event.severity for event in unresolved)
    export_value, export_note = _export_fact(last_export)
    return RuntimeEventsPageView(
        error_count=str(counts["error"]),
        warning_count=str(counts["warning"]),
        active_task_count=str(active_task_count),
        last_export_value=export_value,
        last_export_note=export_note,
        rows=tuple(_row_view(event) for event in events),
        info_text=info_text,
    )


def runtime_info_text(
    *,
    version: str,
    resource_root: object,
    state_root: object,
    runtime_root: object,
    exports_root: object,
) -> str:
    """Format the read-only environment panel the window hands the page."""

    return (
        f"版本：{version}\n"
        f"只读资源：{resource_root}\n"
        f"用户状态：{state_root}\n"
        f"日志/数据库：{runtime_root}\n"
        f"脱敏导出：{exports_root}\n\n"
        "关闭流程：停止行情流 → 等待网络线程 → 保存本地数据库。"
    )


__all__ = [
    "DEFAULT_EXPORT_NOTE",
    "DEFAULT_EXPORT_VALUE",
    "SEVERITY_LABELS",
    "build_runtime_events_view",
    "runtime_info_text",
    "severity_text",
    "status_text",
    "tone_for",
]
