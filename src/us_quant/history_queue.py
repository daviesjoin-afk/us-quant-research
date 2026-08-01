from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from time import sleep
from typing import Callable, Iterator

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.ibkr_history import collect_daily_history
from us_quant.market_data import (
    save_historical_series,
    validate_daily_series,
)


@dataclass(frozen=True, slots=True)
class HistoryJob:
    symbol: str
    duration: str
    priority: int
    status: str
    attempts: int
    row_count: int
    last_error: str
    updated_at: str


class HistoryJobStore:
    def __init__(
        self,
        path: str | Path = "runtime/history_jobs.sqlite3",
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def schedule(
        self,
        symbols: tuple[str, ...],
        *,
        duration: str = "5 Y",
    ) -> int:
        now = _now()
        inserted = 0
        with self._connect() as connection:
            for priority, symbol in enumerate(symbols, start=1):
                normalized = symbol.strip().upper()
                if not normalized:
                    continue
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO history_jobs(
                        symbol, duration, priority, status, attempts,
                        row_count, last_error, updated_at
                    ) VALUES (?, ?, ?, 'pending', 0, 0, '', ?)
                    """,
                    (normalized, duration, priority, now),
                )
                inserted += cursor.rowcount
            connection.commit()
        return inserted

    def reset_failed(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE history_jobs
                SET status = 'pending', last_error = '', updated_at = ?
                WHERE status = 'failed'
                """,
                (_now(),),
            )
            connection.commit()
            return cursor.rowcount

    def reset_stale_running(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE history_jobs
                SET status = 'pending',
                    last_error = '上次运行中断，已自动恢复',
                    updated_at = ?
                WHERE status = 'running'
                """,
                (_now(),),
            )
            connection.commit()
            return cursor.rowcount

    def claim(self, limit: int) -> tuple[HistoryJob, ...]:
        if limit <= 0:
            return ()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT symbol, duration, priority, status, attempts,
                       row_count, last_error, updated_at
                FROM history_jobs
                WHERE status = 'pending'
                ORDER BY priority, symbol
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            if rows:
                symbols = [row[0] for row in rows]
                placeholders = ",".join("?" for _ in symbols)
                connection.execute(
                    f"""
                    UPDATE history_jobs
                    SET status = 'running',
                        attempts = attempts + 1,
                        updated_at = ?
                    WHERE symbol IN ({placeholders})
                    """,
                    (_now(), *symbols),
                )
            connection.commit()
        return tuple(
            HistoryJob(
                symbol=row[0],
                duration=row[1],
                priority=row[2],
                status="running",
                attempts=row[4] + 1,
                row_count=row[5],
                last_error=row[6],
                updated_at=_now(),
            )
            for row in rows
        )

    def complete(self, symbol: str, row_count: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE history_jobs
                SET status = 'completed', row_count = ?,
                    last_error = '', updated_at = ?
                WHERE symbol = ?
                """,
                (row_count, _now(), symbol),
            )
            connection.commit()

    def fail(self, symbol: str, error: str) -> None:
        safe_error = " ".join(str(error).split())[:500]
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE history_jobs
                SET status = 'failed', last_error = ?, updated_at = ?
                WHERE symbol = ?
                """,
                (safe_error, _now(), symbol),
            )
            connection.commit()

    def list_jobs(
        self,
        *,
        status: str | None = None,
    ) -> tuple[HistoryJob, ...]:
        query = """
            SELECT symbol, duration, priority, status, attempts,
                   row_count, last_error, updated_at
            FROM history_jobs
        """
        parameters: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (status,)
        query += " ORDER BY priority, symbol"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(HistoryJob(*row) for row in rows)

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*)
                FROM history_jobs
                GROUP BY status
                """
            ).fetchall()
        result = {
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
        }
        result.update({status: int(count) for status, count in rows})
        return result

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS history_jobs(
                    symbol TEXT PRIMARY KEY,
                    duration TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(
                        status IN (
                            'pending', 'running', 'completed', 'failed'
                        )
                    ),
                    attempts INTEGER NOT NULL,
                    row_count INTEGER NOT NULL,
                    last_error TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()


def run_history_queue(
    config: IBKRConnectionConfig,
    store: HistoryJobStore,
    *,
    data_root: str | Path = "data",
    maximum_jobs: int = 25,
    batch_size: int = 4,
    pause_seconds: float = 1.0,
    timeout_seconds: float = 60,
    progress: Callable[[int, int, str, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, int]:
    if maximum_jobs <= 0:
        return store.counts()
    if batch_size <= 0 or batch_size > 8:
        raise ValueError("batch_size must be between 1 and 8")
    store.reset_stale_running()
    processed = 0
    while processed < maximum_jobs:
        if should_stop is not None and should_stop():
            break
        jobs = store.claim(min(batch_size, maximum_jobs - processed))
        if not jobs:
            break
        symbols = tuple(job.symbol for job in jobs)
        duration = jobs[0].duration
        try:
            series_collection = collect_daily_history(
                config,
                symbols=symbols,
                duration=duration,
                timeout_seconds=timeout_seconds,
            )
            by_symbol = {
                series.request.symbol: series
                for series in series_collection
            }
            for job in jobs:
                series = by_symbol.get(job.symbol)
                if series is None:
                    store.fail(job.symbol, "IBKR 未返回该标的")
                    status = "失败"
                else:
                    quality = validate_daily_series(series)
                    if quality.passed:
                        save_historical_series(
                            series,
                            data_root=data_root,
                        )
                        store.complete(job.symbol, quality.row_count)
                        status = f"完成 {quality.row_count} 根"
                    else:
                        codes = ",".join(
                            issue.code for issue in quality.issues
                        )
                        store.fail(
                            job.symbol,
                            f"数据质量不通过: {codes}",
                        )
                        status = "质量不通过"
                processed += 1
                if progress is not None:
                    progress(
                        processed,
                        maximum_jobs,
                        job.symbol,
                        status,
                    )
        except Exception as error:
            for job in jobs:
                store.fail(job.symbol, str(error))
                processed += 1
                if progress is not None:
                    progress(
                        processed,
                        maximum_jobs,
                        job.symbol,
                        "批次失败",
                    )
        if pause_seconds and processed < maximum_jobs:
            sleep(pause_seconds)
    return store.counts()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
