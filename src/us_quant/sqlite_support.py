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


def connect_sqlite_readonly(path: str | Path) -> sqlite3.Connection:
    """Open an existing SQLite database without permitting any writes."""
    database_uri = f"{Path(path).resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(database_uri, timeout=30, uri=True)
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA query_only = ON")
    return connection
