"""Qt-free projection for the History page."""

from __future__ import annotations

from us_quant.desktop_history_service import HistoryQueueSnapshot
from us_quant.desktop_v2.pages.research.history.models import (
    HistoryControlView,
    HistoryPageView,
    HistoryQueueRow,
)


HISTORY_DISPLAY_CAP = 2_500
STATUS_LABELS = {
    "pending": "待处理",
    "running": "运行中",
    "completed": "完成",
    "failed": "失败",
}


def history_rows(
    snapshot: HistoryQueueSnapshot,
) -> tuple[HistoryQueueRow, ...]:
    rows: list[HistoryQueueRow] = []
    for job in snapshot.jobs[:HISTORY_DISPLAY_CAP]:
        rows.append(
            HistoryQueueRow(
                symbol=job.symbol,
                duration=job.duration,
                priority=str(job.priority),
                status=STATUS_LABELS.get(job.status, job.status),
                attempts=str(job.attempts),
                row_count=str(job.row_count or "—"),
                note=job.last_error,
            )
        )
    return tuple(rows)


def history_summary(snapshot: HistoryQueueSnapshot) -> str:
    text = (
        f"历史队列 {len(snapshot.jobs):,} · 待处理 "
        f"{snapshot.pending:,} · 完成 {snapshot.completed:,} · "
        f"失败 {snapshot.failed:,}。"
    )
    if len(snapshot.jobs) > HISTORY_DISPLAY_CAP:
        text += " 表格仅显示前 2,500 条，任务会全部保留并执行。"
    return text


def build_history_view(
    snapshot: HistoryQueueSnapshot,
    *,
    progress_percent: int,
) -> HistoryPageView:
    return HistoryPageView(
        summary=history_summary(snapshot),
        rows=history_rows(snapshot),
        controls=HistoryControlView(progress_percent=progress_percent),
    )


__all__ = [
    "HISTORY_DISPLAY_CAP",
    "STATUS_LABELS",
    "build_history_view",
    "history_rows",
    "history_summary",
]
