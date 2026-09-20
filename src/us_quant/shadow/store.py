"""SQLite persistence for shadow sessions and their simulated fills.

``ShadowPaperStore`` is the only thing in the shadow package that touches a
database.  The schema is unchanged from the module this moved out of -- the two
tables, the columns, the additive ``ALTER TABLE`` upgrades and the foreign key
from ``shadow_fill`` to ``shadow_session`` are all exactly as they were, so a
database written before the move still opens after it.

The dependency runs one way and only one way: this module imports the models it
persists and nothing else from the package.  The engine imports the store, so the
reverse import (``engine`` from here) would be a cycle -- and the guard test says
so.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3

from us_quant.shadow.models import ShadowFill, ShadowSessionProvenance
from us_quant.sqlite_support import connect_sqlite


class ShadowPaperStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_session(
        self,
        *,
        session_id: str,
        initial_cash: Decimal,
        capital_source: str,
        allowed_symbols: tuple[str, ...],
        strategy_version_id: str,
        parameter_hash: str,
        target_symbol: str,
    ) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO shadow_session(
                        session_id, started_at, stopped_at, mode,
                        initial_cash, capital_source, allowed_symbols,
                        strategy_version_id, parameter_hash, target_symbol
                    ) VALUES (
                        ?, ?, NULL, 'internal_shadow', ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        session_id,
                        _now_iso(),
                        str(initial_cash),
                        capital_source,
                        ",".join(allowed_symbols),
                        strategy_version_id,
                        parameter_hash,
                        target_symbol,
                    ),
                )

    def stop_session(self, session_id: str) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE shadow_session
                    SET stopped_at = ?
                    WHERE session_id = ? AND stopped_at IS NULL
                    """,
                    (_now_iso(), session_id),
                )

    def add_fill(self, fill: ShadowFill) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO shadow_fill(
                        session_id, occurred_at, symbol, side, quantity,
                        price, commission, reason, provider, coverage,
                        realized_pnl
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fill.session_id,
                        fill.occurred_at,
                        fill.symbol,
                        fill.side,
                        fill.quantity,
                        str(fill.price),
                        str(fill.commission),
                        fill.reason,
                        fill.provider,
                        fill.coverage,
                        (
                            str(fill.realized_pnl)
                            if fill.realized_pnl is not None
                            else None
                        ),
                    ),
                )

    def recent_fills(self, limit: int = 200) -> tuple[ShadowFill, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT session_id, occurred_at, symbol, side, quantity,
                       price, commission, reason, provider, coverage,
                       realized_pnl
                FROM shadow_fill
                ORDER BY fill_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(
            ShadowFill(
                session_id=row[0],
                occurred_at=row[1],
                symbol=row[2],
                side=row[3],
                quantity=int(row[4]),
                price=Decimal(row[5]),
                commission=Decimal(row[6]),
                reason=row[7],
                provider=row[8],
                coverage=row[9],
                realized_pnl=(
                    Decimal(row[10]) if row[10] is not None else None
                ),
            )
            for row in rows
        )

    def session_provenance(
        self, session_id: str
    ) -> ShadowSessionProvenance:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT session_id, strategy_version_id, parameter_hash,
                       target_symbol, allowed_symbols
                FROM shadow_session
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return ShadowSessionProvenance(
            session_id=row[0],
            strategy_version_id=row[1],
            parameter_hash=row[2],
            target_symbol=row[3],
            allowed_symbols=tuple(
                symbol for symbol in row[4].split(",") if symbol
            ),
        )

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS shadow_session(
                        session_id TEXT PRIMARY KEY,
                        started_at TEXT NOT NULL,
                        stopped_at TEXT,
                        mode TEXT NOT NULL,
                        initial_cash TEXT NOT NULL,
                        capital_source TEXT NOT NULL,
                        allowed_symbols TEXT NOT NULL,
                        strategy_version_id TEXT NOT NULL
                            DEFAULT 'legacy_unversioned',
                        parameter_hash TEXT NOT NULL
                            DEFAULT 'legacy_unverified',
                        target_symbol TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(shadow_session)"
                    ).fetchall()
                }
                if "capital_source" not in columns:
                    connection.execute(
                        """
                        ALTER TABLE shadow_session
                        ADD COLUMN capital_source TEXT NOT NULL
                        DEFAULT 'legacy_unspecified'
                        """
                    )
                for name, default in (
                    ("strategy_version_id", "legacy_unversioned"),
                    ("parameter_hash", "legacy_unverified"),
                    ("target_symbol", ""),
                ):
                    if name not in columns:
                        connection.execute(
                            f"""
                            ALTER TABLE shadow_session
                            ADD COLUMN {name} TEXT NOT NULL
                            DEFAULT '{default}'
                            """
                        )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS shadow_fill(
                        fill_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        quantity INTEGER NOT NULL,
                        price TEXT NOT NULL,
                        commission TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        coverage TEXT NOT NULL,
                        realized_pnl TEXT,
                        FOREIGN KEY(session_id)
                            REFERENCES shadow_session(session_id)
                    )
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["ShadowPaperStore"]
