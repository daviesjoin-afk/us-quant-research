"""The history day-K queue, without Qt.

``MainWindow`` used to drive the history queue itself: it resolved the
research symbol order, constructed ``HistoryJobStore``, called the IBKR and
public runners, and read the raw ``counts()`` dict straight into the table.
This module owns that orchestration.

Two boundaries are deliberate:

* The constructor performs **no I/O**.  ``HistoryJobStore`` creates its parent
  directory and opens SQLite the moment it is built, so holding a store would
  make starting the window create ``runtime/history_jobs.sqlite3``.  The store
  is built per call instead.
* Progress is reported in domain terms -- ``(done, total, symbol, status)``.
  The Chinese ``"{done}/{total} {symbol}：{status}"`` line is a UI string and
  stays in the window.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from us_quant.history_queue import HistoryJob, HistoryJobStore, run_history_queue
from us_quant.ibkr import IBKRConnectionConfig
from us_quant.public_history import run_public_history_queue
from us_quant.universe import UniverseSnapshot, prioritized_research_symbols

DomainProgress = Callable[[int, int, str, str], None]


@dataclass(frozen=True, slots=True)
class HistoryScheduleResult:
    """What a schedule pass added, and how large the queue now is."""

    inserted: int
    total: int


@dataclass(frozen=True, slots=True)
class HistoryQueueSnapshot:
    """The whole queue plus its per-status counts.

    ``jobs`` is never truncated: the 2500-row cap is a presentation rule and
    belongs to the table, not here.  Counts are explicit fields so the UI does
    not depend on the raw ``counts()`` key contract.
    """

    jobs: tuple[HistoryJob, ...]
    pending: int
    running: int
    completed: int
    failed: int


class DesktopHistoryService:
    """History day-K queue orchestration for the desktop app."""

    def __init__(
        self,
        *,
        queue_path: str | Path,
        data_root: str | Path,
    ) -> None:
        # Stored as paths only.  Building the store here would create the
        # directory and the SQLite file at window construction time.
        self.queue_path = Path(queue_path)
        self.data_root = Path(data_root)

    def _store(self) -> HistoryJobStore:
        return HistoryJobStore(self.queue_path)

    def schedule_universe(
        self,
        universe: UniverseSnapshot,
    ) -> HistoryScheduleResult:
        """Enqueue every research-eligible symbol, in priority order.

        ``limit=None`` is required: this button means the *whole* non-China
        research pool, not the first page of it.
        """

        symbols = prioritized_research_symbols(universe, limit=None)
        store = self._store()
        inserted = store.schedule(symbols)
        return HistoryScheduleResult(
            inserted=inserted,
            total=len(store.list_jobs()),
        )

    def run_ibkr(
        self,
        config: IBKRConnectionConfig,
        *,
        maximum_jobs: int,
        progress: DomainProgress | None = None,
    ) -> dict[str, int]:
        """Drain up to ``maximum_jobs`` pending jobs over IBKR.

        ``config`` is passed per call rather than held: the Settings page can
        change ``self.config.ibkr`` while the app is running.

        The failed jobs are left alone.  The public path re-queues them; this
        one must not, so a retry stays an explicit choice.
        """

        store = self._store()
        return run_history_queue(
            config,
            store,
            data_root=self.data_root,
            maximum_jobs=maximum_jobs,
            progress=progress,
        )

    def run_public(
        self,
        *,
        maximum_jobs: int,
        progress: DomainProgress | None = None,
    ) -> dict[str, int]:
        """Re-queue failures, then drain up to ``maximum_jobs`` from the free source.

        The ``reset_failed()`` is part of this path's contract, not a
        convenience: the free fallback source is meant to retry what IBKR
        could not deliver, so it starts by putting ``failed`` back to
        ``pending``.  The separate retry button does not replace it.
        """

        store = self._store()
        store.reset_failed()
        return run_public_history_queue(
            store,
            data_root=self.data_root,
            maximum_jobs=maximum_jobs,
            progress=progress,
        )

    def reset_failed(self) -> int:
        return self._store().reset_failed()

    def snapshot(self) -> HistoryQueueSnapshot:
        store = self._store()
        # Preserve the window's original observation order: rows first,
        # counts second.  A runner may update the queue between these two
        # reads, so reversing them would subtly change the old UI semantics.
        jobs = tuple(store.list_jobs())
        counts = store.counts()
        return HistoryQueueSnapshot(
            jobs=jobs,
            pending=counts["pending"],
            running=counts["running"],
            completed=counts["completed"],
            failed=counts["failed"],
        )
