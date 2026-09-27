from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from us_quant.desktop_v2.orchestration.autonomy.completion import (
    PaperAutonomyCompletionObserver,
)
from us_quant.trading.adapters.sqlite.paper_autonomy_action_repository import (
    SQLitePaperAutonomyActionRepository,
)
from us_quant.trading.domain.paper_autonomy_supervisor import (
    PaperAutonomyActionStatus,
    PaperAutonomyActionType,
    PaperAutonomyEventCode,
)
from us_quant.trading.ports.paper_autonomy_action_repository import (
    PaperAutonomyActionRecord,
    PaperAutonomyActionRepositoryError,
)


NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
DAY = date(2026, 9, 28)


def _store(tmp_path: Path) -> SQLitePaperAutonomyActionRepository:
    return SQLitePaperAutonomyActionRepository(tmp_path / "actions.sqlite3")


def _claim(
    store: SQLitePaperAutonomyActionRepository,
    action: PaperAutonomyActionType,
    *,
    status: PaperAutonomyActionStatus = PaperAutonomyActionStatus.CLAIMED,
):
    key = f"{action.value}-r3"
    store.claim(
        PaperAutonomyActionRecord(
            action_key=key,
            intent_revision=3,
            trading_day=DAY,
            action=action,
            status=PaperAutonomyActionStatus.CLAIMED,
            claimed_at=NOW,
            completed_at=None,
            detail="claimed before owner request",
        )
    )
    if status is PaperAutonomyActionStatus.REQUESTED:
        store.mark_requested(
            action_key=key, detail="Paper action request admitted"
        )
    return key


def _observer(store, events=None):
    return PaperAutonomyCompletionObserver(
        actions=store,
        emit=lambda code, detail: events.append((code, detail)) if events is not None else None,
        clock=lambda: NOW,
    )


def test_sync_publication_before_mark_requested_is_buffered_then_flushed(tmp_path):
    store = _store(tmp_path)
    _claim(store, PaperAutonomyActionType.START)
    events = []
    observer = _observer(store, events)

    observer.session_running()

    assert store.unresolved()[0].status is PaperAutonomyActionStatus.CLAIMED
    store.mark_requested(
        action_key="start-r3", detail="Paper start request admitted"
    )
    observer.flush_pending()

    assert not store.unresolved()
    assert store.recent()[0].status is PaperAutonomyActionStatus.SUCCEEDED
    assert events[0][0] is PaperAutonomyEventCode.AUTONOMY_START_SUCCEEDED


def test_requested_action_completes_from_matching_publication(tmp_path):
    store = _store(tmp_path)
    _claim(store, PaperAutonomyActionType.STOP, status=PaperAutonomyActionStatus.REQUESTED)
    observer = _observer(store)

    observer.session_finalized()

    assert store.recent()[0].status is PaperAutonomyActionStatus.SUCCEEDED


def test_start_completes_only_from_running_publication(tmp_path):
    store = _store(tmp_path)
    _claim(store, PaperAutonomyActionType.START, status=PaperAutonomyActionStatus.REQUESTED)
    events = []

    _observer(store, events).session_running()

    assert not store.unresolved()
    assert store.recent()[0].status is PaperAutonomyActionStatus.SUCCEEDED
    assert events[0][0] is PaperAutonomyEventCode.AUTONOMY_START_SUCCEEDED


def test_stop_completes_only_from_finalized_publication(tmp_path):
    store = _store(tmp_path)
    _claim(store, PaperAutonomyActionType.STOP, status=PaperAutonomyActionStatus.REQUESTED)
    observer = _observer(store)

    observer.session_running()

    assert store.unresolved()[0].status is PaperAutonomyActionStatus.REQUESTED
    observer.session_finalized()
    assert not store.unresolved()
    assert store.recent()[0].status is PaperAutonomyActionStatus.SUCCEEDED


def test_wrong_publication_type_does_not_complete_action(tmp_path):
    store = _store(tmp_path)
    _claim(store, PaperAutonomyActionType.START, status=PaperAutonomyActionStatus.REQUESTED)

    _observer(store).preparation_ready()

    assert store.unresolved()[0].status is PaperAutonomyActionStatus.REQUESTED


def test_publication_after_terminal_action_is_unattributable_and_ignored(tmp_path):
    store = _store(tmp_path)
    _claim(store, PaperAutonomyActionType.PREPARE, status=PaperAutonomyActionStatus.REQUESTED)
    observer = _observer(store)

    observer.preparation_ready()
    observer.preparation_ready()
    observer.preparation_failed("unrelated later manual failure")

    assert store.recent()[0].status is PaperAutonomyActionStatus.SUCCEEDED


def test_manual_running_after_failed_autonomous_start_is_ignored(tmp_path):
    store = _store(tmp_path)
    key = _claim(
        store,
        PaperAutonomyActionType.START,
        status=PaperAutonomyActionStatus.REQUESTED,
    )
    store.complete(
        action_key=key,
        status=PaperAutonomyActionStatus.FAILED,
        completed_at=NOW,
        detail="autonomous launch failed",
    )
    observer = _observer(store)

    observer.session_running()

    assert len(store.recent()) == 1
    assert store.recent()[0].status is PaperAutonomyActionStatus.FAILED


def test_manual_launch_failure_after_successful_start_is_ignored(tmp_path):
    store = _store(tmp_path)
    key = _claim(
        store,
        PaperAutonomyActionType.START,
        status=PaperAutonomyActionStatus.REQUESTED,
    )
    store.complete(
        action_key=key,
        status=PaperAutonomyActionStatus.SUCCEEDED,
        completed_at=NOW,
        detail="autonomous session running",
    )

    _observer(store).launch_failed("unrelated manual launch failure")

    assert len(store.recent()) == 1
    assert store.recent()[0].status is PaperAutonomyActionStatus.SUCCEEDED


def test_manual_prepare_ready_after_failed_autonomous_prepare_is_ignored(tmp_path):
    store = _store(tmp_path)
    key = _claim(
        store,
        PaperAutonomyActionType.PREPARE,
        status=PaperAutonomyActionStatus.REQUESTED,
    )
    store.complete(
        action_key=key,
        status=PaperAutonomyActionStatus.FAILED,
        completed_at=NOW,
        detail="autonomous preparation failed",
    )

    _observer(store).preparation_ready()

    assert len(store.recent()) == 1
    assert store.recent()[0].status is PaperAutonomyActionStatus.FAILED


def test_duplicate_pending_completion_is_safe_but_conflict_is_rejected(tmp_path):
    store = _store(tmp_path)
    _claim(store, PaperAutonomyActionType.START)
    observer = _observer(store)

    observer.session_running()
    observer.session_running()
    with pytest.raises(PaperAutonomyActionRepositoryError):
        observer.launch_failed("conflict")


@pytest.mark.parametrize(
    "status",
    [PaperAutonomyActionStatus.CLAIMED, PaperAutonomyActionStatus.REQUESTED],
)
def test_crash_states_remain_unresolved_without_a_publication(tmp_path, status):
    store = _store(tmp_path)
    _claim(store, PaperAutonomyActionType.START, status=status)

    assert store.unresolved()[0].status is status
