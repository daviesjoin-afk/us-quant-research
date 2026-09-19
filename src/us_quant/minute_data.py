from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketSnapshot,
)
from us_quant.sqlite_support import connect_sqlite


@dataclass(frozen=True, slots=True)
class MinuteQuoteRecord:
    symbol: str
    minute: str
    provider: str
    coverage: str
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    mode: MarketDataMode
    realtime_ready: bool
    stale: bool
    stale_reason: str | None
    generation: int
    evidence_origin: str = "captured_stream"
    source_age_seconds: float | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None


@dataclass(frozen=True, slots=True)
class MinuteDataSummary:
    symbol: str
    total_rows: int
    usable_rows: int
    first_minute: str | None
    last_minute: str | None
    providers: tuple[str, ...]
    evidence_origins: tuple[str, ...]


class MinuteQuoteStore:
    """Local minute-level Level-I evidence with explicit quality metadata."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def record_snapshot(
        self,
        snapshot: MarketSnapshot,
        *,
        symbols: Iterable[str] | None = None,
        evidence_origin: str = "captured_stream",
    ) -> int:
        if evidence_origin not in {
            "captured_stream",
            "synthetic_preview",
            "imported_research",
        }:
            raise ValueError("unsupported minute evidence origin")
        allowed = (
            {symbol.strip().upper() for symbol in symbols}
            if symbols is not None
            else None
        )
        rows: list[MinuteQuoteRecord] = []
        for quote in snapshot.quotes:
            if allowed is not None and quote.symbol not in allowed:
                continue
            # Both fields are timezone-aware ``datetime`` in the domain type,
            # so this is a normalisation step, not a parse.  A quote with no
            # usable timestamp falls back to the snapshot's own observation
            # time, which is what the pre-migration code did with the ISO
            # string fallback.
            observed = _as_utc(quote.updated_at or snapshot.observed_at)
            rows.append(
                MinuteQuoteRecord(
                    symbol=quote.symbol,
                    minute=_minute_iso(observed),
                    provider=quote.source_label or snapshot.source_label,
                    coverage=quote.coverage or snapshot.coverage,
                    bid=quote.bid,
                    ask=quote.ask,
                    last=quote.last,
                    mode=quote.mode,
                    realtime_ready=quote.realtime_ready,
                    stale=quote.stale,
                    stale_reason=quote.stale_reason,
                    generation=snapshot.generation,
                    evidence_origin=evidence_origin,
                    source_age_seconds=quote.age_seconds,
                    bid_size=quote.bid_size,
                    ask_size=quote.ask_size,
                )
            )
        if not rows:
            return 0
        with closing(self._connect()) as connection:
            with connection:
                connection.executemany(
                    """
                    INSERT INTO minute_quote (
                        symbol, minute, provider, coverage, bid, ask, last,
                        mode, realtime_ready, stale,
                        stale_reason, generation, recorded_at
                        , evidence_origin, source_age_seconds,
                        bid_size, ask_size
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?)
                    ON CONFLICT(symbol, minute, provider) DO UPDATE SET
                        coverage = excluded.coverage,
                        bid = excluded.bid,
                        ask = excluded.ask,
                        last = excluded.last,
                        mode = excluded.mode,
                        realtime_ready = excluded.realtime_ready,
                        stale = excluded.stale,
                        stale_reason = excluded.stale_reason,
                        generation = excluded.generation,
                        evidence_origin = excluded.evidence_origin,
                        source_age_seconds = excluded.source_age_seconds,
                        bid_size = excluded.bid_size,
                        ask_size = excluded.ask_size,
                        recorded_at = excluded.recorded_at
                    """,
                    [
                        (
                            row.symbol,
                            row.minute,
                            row.provider,
                            row.coverage,
                            _decimal_text(row.bid),
                            _decimal_text(row.ask),
                            _decimal_text(row.last),
                            row.mode.value,
                            int(row.realtime_ready),
                            int(row.stale),
                            row.stale_reason,
                            row.generation,
                            datetime.now(timezone.utc).isoformat(),
                            row.evidence_origin,
                            row.source_age_seconds,
                            _decimal_text(row.bid_size),
                            _decimal_text(row.ask_size),
                        )
                        for row in rows
                    ],
                )
        return len(rows)

    def load(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        usable_only: bool = True,
    ) -> tuple[MinuteQuoteRecord, ...]:
        normalized = symbol.strip().upper()
        clauses = ["symbol = ?"]
        values: list[object] = [normalized]
        if provider:
            clauses.append("provider = ?")
            values.append(provider)
        if usable_only:
            clauses.extend(
                (
                    "realtime_ready = 1",
                    "stale = 0",
                    "bid IS NOT NULL",
                    "ask IS NOT NULL",
                )
            )
        query = (
            """
            SELECT symbol, minute, provider, coverage, bid, ask, last,
                   mode, realtime_ready, stale,
                   stale_reason, generation
                   , evidence_origin, source_age_seconds,
                     bid_size, ask_size
            FROM minute_quote
            WHERE """
            + " AND ".join(clauses)
            + " ORDER BY minute, provider"
        )
        with closing(self._connect()) as connection:
            rows = connection.execute(query, values).fetchall()
        return tuple(_row_to_record(row) for row in rows)

    def summary(self, symbol: str) -> MinuteDataSummary:
        normalized = symbol.strip().upper()
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*),
                       SUM(
                           CASE WHEN realtime_ready = 1 AND stale = 0
                                     AND bid IS NOT NULL AND ask IS NOT NULL
                                THEN 1 ELSE 0 END
                       ),
                       MIN(minute), MAX(minute)
                FROM minute_quote
                WHERE symbol = ?
                """,
                (normalized,),
            ).fetchone()
            providers = tuple(
                item[0]
                for item in connection.execute(
                    """
                    SELECT DISTINCT provider
                    FROM minute_quote
                    WHERE symbol = ?
                    ORDER BY provider
                    """,
                    (normalized,),
                ).fetchall()
            )
            evidence_origins = tuple(
                item[0]
                for item in connection.execute(
                    """
                    SELECT DISTINCT evidence_origin
                    FROM minute_quote
                    WHERE symbol = ?
                    ORDER BY evidence_origin
                    """,
                    (normalized,),
                ).fetchall()
            )
        return MinuteDataSummary(
            symbol=normalized,
            total_rows=int(row[0] or 0),
            usable_rows=int(row[1] or 0),
            first_minute=row[2],
            last_minute=row[3],
            providers=providers,
            evidence_origins=evidence_origins,
        )

    @staticmethod
    def fingerprint(records: Iterable[MinuteQuoteRecord]) -> str:
        payload = [
            {
                **asdict(record),
                "bid": _decimal_text(record.bid),
                "ask": _decimal_text(record.ask),
                "last": _decimal_text(record.last),
                "bid_size": _decimal_text(record.bid_size),
                "ask_size": _decimal_text(record.ask_size),
            }
            for record in records
        ]
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS minute_quote (
                        quote_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        minute TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        coverage TEXT NOT NULL,
                        bid TEXT,
                        ask TEXT,
                        last TEXT,
                        mode TEXT,
                        realtime_ready INTEGER NOT NULL,
                        stale INTEGER NOT NULL,
                        stale_reason TEXT,
                        generation INTEGER NOT NULL,
                        recorded_at TEXT NOT NULL,
                        evidence_origin TEXT NOT NULL
                            DEFAULT 'captured_stream',
                        source_age_seconds REAL,
                        bid_size TEXT,
                        ask_size TEXT,
                        UNIQUE(symbol, minute, provider)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                        idx_minute_quote_symbol_minute
                    ON minute_quote(symbol, minute)
                    """
                )
                # Snapshot of the schema the database *arrived* with, taken
                # before any ALTER below: it is what tells us whether this is
                # a Market Data v1 database that still carries the retired
                # ``market_data_type`` column.
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(minute_quote)"
                    ).fetchall()
                }
                had_legacy_market_data_type = (
                    "market_data_type" in columns
                )
                if "evidence_origin" not in columns:
                    connection.execute(
                        """
                        ALTER TABLE minute_quote
                        ADD COLUMN evidence_origin TEXT NOT NULL
                        DEFAULT 'captured_stream'
                        """
                    )
                for column, declaration in (
                    ("source_age_seconds", "REAL"),
                    ("bid_size", "TEXT"),
                    ("ask_size", "TEXT"),
                    ("mode", "TEXT"),
                ):
                    if column not in columns:
                        connection.execute(
                            f"ALTER TABLE minute_quote "
                            f"ADD COLUMN {column} {declaration}"
                        )
                # Same transaction as the ``mode`` ALTER above: either the
                # column and its backfill both land, or neither does.
                if had_legacy_market_data_type:
                    _migrate_legacy_market_data_type(
                        connection,
                        columns=columns,
                    )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path)


def _migrate_legacy_market_data_type(
    connection: sqlite3.Connection,
    *,
    columns: set[str],
) -> None:
    """Backfill ``mode`` from the retired ``market_data_type`` column.

    Market Data v1 persisted the IBKR market-data type as an integer
    (``1`` realtime, ``2`` frozen, ``3`` delayed, ``4`` delayed/frozen);
    v2 persists a ``mode`` string.  An upgraded database therefore carries
    the legacy column with no ``mode`` value, and reading that as
    ``UNKNOWN`` would silently downgrade historically valid realtime
    evidence: ``targeted_replay`` rebuilds a domain ``MarketQuote`` from the
    stored row, and ``MarketQuote.realtime_ready`` requires
    ``mode is REALTIME``.

    The mapping is taken from ``MarketDataMode`` itself rather than from
    hard-coded strings.  Anything else -- ``NULL``, ``99``, a negative
    value -- maps to ``UNKNOWN`` and stays unusable, because guessing
    "realtime" from an unrecognised legacy type would let stale evidence
    drive an intraday signal.

    Only rows with no usable ``mode`` are touched, so re-running this on an
    already-migrated database (or on a row a v2 build has since rewritten)
    is a no-op and never overwrites a newer value.  The legacy column is
    left in place as retired evidence; it is no longer read or written.
    """

    if "market_data_type" not in columns:
        return
    connection.execute(
        """
        UPDATE minute_quote
        SET mode = CASE market_data_type
                WHEN 1 THEN ?
                WHEN 2 THEN ?
                WHEN 3 THEN ?
                WHEN 4 THEN ?
                ELSE ?
            END
        WHERE mode IS NULL OR mode = ''
        """,
        (
            MarketDataMode.REALTIME.value,
            MarketDataMode.FROZEN.value,
            MarketDataMode.DELAYED.value,
            MarketDataMode.DELAYED_FROZEN.value,
            MarketDataMode.UNKNOWN.value,
        ),
    )


def _row_to_record(row: tuple[object, ...]) -> MinuteQuoteRecord:
    return MinuteQuoteRecord(
        symbol=str(row[0]),
        minute=str(row[1]),
        provider=str(row[2]),
        coverage=str(row[3]),
        bid=_to_decimal(row[4]),
        ask=_to_decimal(row[5]),
        last=_to_decimal(row[6]),
        mode=_to_mode(row[7]),
        realtime_ready=bool(row[8]),
        stale=bool(row[9]),
        stale_reason=str(row[10]) if row[10] is not None else None,
        generation=int(row[11]),
        evidence_origin=str(row[12]),
        source_age_seconds=(
            float(row[13]) if row[13] is not None else None
        ),
        bid_size=_to_decimal(row[14]),
        ask_size=_to_decimal(row[15]),
    )


def _as_utc(value: datetime) -> datetime:
    """Normalise a domain timestamp to UTC.

    Accepts a ``datetime`` (the domain contract) and tolerates an ISO string
    for rows replayed from an older store, so a legacy record does not crash
    the recorder.
    """

    if isinstance(value, str):
        return _parse_timestamp(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _minute_iso(value: datetime) -> str:
    return value.replace(second=0, microsecond=0).isoformat()


def _decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _to_decimal(value: object) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _to_mode(value: object) -> MarketDataMode:
    """Read the persisted mode, defaulting to ``UNKNOWN``.

    Rows written before the Market Data v2 migration have no ``mode`` column
    value; an unreadable or absent mode is ``UNKNOWN`` rather than a guess,
    because a wrong "realtime" would make stale evidence look usable.
    """

    if value is None:
        return MarketDataMode.UNKNOWN
    try:
        return MarketDataMode(str(value))
    except ValueError:
        return MarketDataMode.UNKNOWN
