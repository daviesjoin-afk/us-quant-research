"""History presenter and page tests."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from us_quant.desktop_history_service import HistoryQueueSnapshot
from us_quant.desktop_v2.pages.research.history.models import (
    HistoryControlView,
    HistoryPageView,
)
from us_quant.desktop_v2.pages.research.history.page import (
    HISTORY_HEADERS,
    HistoryPage,
)
from us_quant.desktop_v2.pages.research.history.presenter import (
    HISTORY_DISPLAY_CAP,
    build_history_view,
    history_rows,
    history_summary,
)
from us_quant.history_queue import HistoryJob


_APP = QApplication.instance() or QApplication([])


def _job(index: int, status: str = "pending") -> HistoryJob:
    return HistoryJob(
        symbol=f"S{index}",
        duration="1 Y",
        priority=index,
        status=status,
        attempts=index,
        row_count=10 * index,
        last_error="",
        updated_at="2026-09-20T00:00:00+00:00",
    )


@pytest.fixture()
def page() -> HistoryPage:
    widget = HistoryPage()
    yield widget
    widget.deleteLater()


def test_history_rows_translate_statuses_and_row_counts() -> None:
    snapshot = HistoryQueueSnapshot(
        jobs=(
            _job(0, "pending"),
            _job(1, "running"),
            _job(2, "completed"),
            _job(3, "failed"),
        ),
        pending=1,
        running=1,
        completed=1,
        failed=1,
    )
    rows = history_rows(snapshot)
    assert [row.status for row in rows] == ["待处理", "运行中", "完成", "失败"]
    assert rows[0].row_count == "—"
    assert rows[1].row_count == "10"


def test_history_summary_and_cap_keep_all_tasks() -> None:
    jobs = tuple(_job(index) for index in range(HISTORY_DISPLAY_CAP + 1))
    snapshot = HistoryQueueSnapshot(
        jobs=jobs,
        pending=HISTORY_DISPLAY_CAP + 1,
        running=0,
        completed=0,
        failed=0,
    )
    rows = history_rows(snapshot)
    summary = history_summary(snapshot)
    assert len(rows) == HISTORY_DISPLAY_CAP
    assert "历史队列 2,501" in summary
    assert "表格仅显示前 2,500 条" in summary


def test_history_view_has_progress_control() -> None:
    snapshot = HistoryQueueSnapshot((), 0, 0, 0, 0)
    view = build_history_view(snapshot, progress_percent=42)
    assert view.controls.progress_percent == 42
    assert view.rows == ()


def test_history_page_has_four_buttons_and_batch_defaults(page: HistoryPage) -> None:
    assert page.schedule_button.text() == "将全部非中概研究池加入队列"
    assert page.run_ibkr_button.text() == "下载下一批日 K"
    assert page.run_public_button.text() == "备用免费日 K（仅研究）"
    assert page.retry_button.text() == "重试失败任务"
    assert page.batch_size.minimum() == 1
    assert page.batch_size.maximum() == 100
    assert page.batch_size.value() == 25


def test_history_page_signals_emit_current_batch(page: HistoryPage) -> None:
    seen: list[tuple[str, int | None]] = []
    page.schedule_requested.connect(lambda: seen.append(("schedule", None)))
    page.run_ibkr_requested.connect(lambda value: seen.append(("ibkr", value)))
    page.run_public_requested.connect(lambda value: seen.append(("public", value)))
    page.retry_failed_requested.connect(lambda: seen.append(("retry", None)))
    page.batch_size.setValue(37)
    page.schedule_button.click()
    page.run_ibkr_button.click()
    page.run_public_button.click()
    page.retry_button.click()
    assert seen == [("schedule", None), ("ibkr", 37), ("public", 37), ("retry", None)]


def test_history_page_render_updates_summary_rows_and_progress(page: HistoryPage) -> None:
    page.render(
        HistoryPageView(
            summary="历史队列 1 · 待处理 1 · 完成 0 · 失败 0。",
            rows=history_rows(HistoryQueueSnapshot((_job(0),), 1, 0, 0, 0)),
            controls=HistoryControlView(progress_percent=75),
        )
    )
    assert page.summary_label.text().startswith("历史队列 1")
    assert page.table.rowCount() == 1
    assert page.progress.value() == 75


def test_history_page_has_frozen_headers(page: HistoryPage) -> None:
    assert page.table.columnCount() == len(HISTORY_HEADERS)
    assert tuple(page.table.horizontalHeaderItem(i).text() for i in range(7)) == HISTORY_HEADERS


def test_history_priority_sorts_numerically(page: HistoryPage) -> None:
    page.render(
        HistoryPageView(
            summary="two rows",
            rows=history_rows(
                HistoryQueueSnapshot((_job(2), _job(10)), 2, 0, 0, 0)
            ),
            controls=HistoryControlView(progress_percent=0),
        )
    )
    page.table.sortItems(2, Qt.AscendingOrder)
    assert page.table.item(0, 2).text() == "2"
