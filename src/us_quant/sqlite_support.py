from __future__ import annotations

from pathlib import Path
import sqlite3


def connect_sqlite(path: str | Path) -> sqlite3.Connection:
    """Open a local state database with consistent safety pragmas."""
    connection = sqlite3.connect(Path(path), timeout=30)
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection
