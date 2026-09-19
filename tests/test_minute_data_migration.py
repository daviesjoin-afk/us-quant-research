"""Legacy minute-quote schema migration.

Market Data v1 persisted the IBKR market-data type as an integer column
``minute_quote.market_data_type``; v2 persists ``minute_quote.mode`` as text.
An upgraded database keeps the legacy column (retired, no longer read or
written) and has ``mode`` added and backfilled once.

These tests do not mock SQLite.  They build a real v1 database on disk with
the real v1 ``CREATE TABLE`` statement, then open it with the real
``MinuteQuoteStore`` so the real ``_initialize()`` runs, and inspect the
resulting rows directly with SQL before re-reading them through the store.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from decimal import Decimal
import inspect
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from us_quant.minute_data import MinuteQuoteStore
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)


MINUTE = "2026-07-24T14:00:00+00:00"
PROVIDER = "IBKR"
COVERAGE = "legacy Level-I"
SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "us_quant"
    / "minute_data.py"
)

# The legacy source of truth: ``market_data_type`` -> v2 ``mode``.
# ``1`` was historically valid realtime evidence, so losing it on upgrade
# would downgrade usable rows to UNKNOWN.
LEGACY_MODE_CASES: tuple[tuple[str, int | None, str], ...] = (
    ("AAPL", 1, "realtime"),
    ("MSFT", 2, "frozen"),
    ("NVDA", 3, "delayed"),
    ("SPY", 4, "delayed_frozen"),
    ("QQQ", None, "unknown"),
    ("IWM", 99, "unknown"),
)


def _legacy_table_columns(*, with_mode_column: bool) -> str:
    """The v1 ``minute_quote`` body, optionally already carrying ``mode``.

    ``mode`` is deliberately absent in the default shape: that is exactly the
    database an existing user upgrades from.
    """

    mode_column = "mode TEXT,\n        " if with_mode_column else ""
    return f"""
        quote_id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        minute TEXT NOT NULL,
        provider TEXT NOT NULL,
        coverage TEXT NOT NULL,
        bid TEXT,
        ask TEXT,
        last TEXT,
        market_data_type INTEGER,
        {mode_column}realtime_ready INTEGER NOT NULL,
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
    """


def _legacy_row(
    symbol: str,
    market_data_type: int | None,
    *,
    mode: str | None = None,
    realtime_ready: int = 1,
    stale: int = 0,
    bid: str = "100",
    ask: str = "100.01",
) -> dict[str, object]:
    """One fully populated v1 row.

    ``realtime_ready``/``stale`` are deliberately set to the *most*
    favourable values so that, after migration, only ``mode`` can decide
    whether a row is usable -- which is the whole point of the fix.
    """

    return {
        "symbol": symbol,
        "minute": MINUTE,
        "provider": PROVIDER,
        "coverage": COVERAGE,
        "bid": bid,
        "ask": ask,
        "last": bid,
        "market_data_type": market_data_type,
        "realtime_ready": realtime_ready,
        "stale": stale,
        "stale_reason": None,
        "generation": 1,
        "recorded_at": MINUTE,
        "evidence_origin": "captured_stream",
        "source_age_seconds": 0.0,
        "bid_size": "400",
        "ask_size": "600",
        "mode": mode,
    }


def _create_legacy_database(
    path: Path,
    rows: list[dict[str, object]],
    *,
    with_mode_column: bool = False,
) -> None:
    """Write a real v1 database to disk.  No mocking, no framework."""

    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute(
                "CREATE TABLE minute_quote ("
                + _legacy_table_columns(with_mode_column=with_mode_column)
                + ")"
            )
            for row in rows:
                fields = [
                    name
                    for name in row
                    if name != "mode" or with_mode_column
                ]
                placeholders = ", ".join(["?"] * len(fields))
                connection.execute(
                    "INSERT INTO minute_quote ("
                    + ", ".join(fields)
                    + f") VALUES ({placeholders})",
                    [row[name] for name in fields],
                )
    finally:
        connection.close()


def _schema_columns(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(minute_quote)"
            ).fetchall()
        }
    finally:
        connection.close()


def _stored_modes(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(path)
    try:
        return {
            str(row[0]): row[1]
            for row in connection.execute(
                "SELECT symbol, mode FROM minute_quote ORDER BY symbol"
            ).fetchall()
        }
    finally:
        connection.close()


def _unique_columns(path: Path) -> tuple[str, ...]:
    connection = sqlite3.connect(path)
    try:
        indexes = connection.execute(
            "PRAGMA index_list(minute_quote)"
        ).fetchall()
        for index in indexes:
            if not index[2]:
                continue
            return tuple(
                str(row[2])
                for row in connection.execute(
                    f"PRAGMA index_info({index[1]})"
                ).fetchall()
            )
        return ()
    finally:
        connection.close()


def _replay_quote(record: object) -> MarketQuote:
    """Rebuild a domain quote exactly the way ``targeted_replay`` does."""

    observed = datetime.fromisoformat(record.minute)  # type: ignore[attr-defined]
    return MarketQuote(
        symbol=record.symbol,  # type: ignore[attr-defined]
        bid=record.bid,  # type: ignore[attr-defined]
        ask=record.ask,  # type: ignore[attr-defined]
        last=record.last,  # type: ignore[attr-defined]
        close=None,
        bid_size=record.bid_size,  # type: ignore[attr-defined]
        ask_size=record.ask_size,  # type: ignore[attr-defined]
        mode=record.mode,  # type: ignore[attr-defined]
        updated_at=observed,
        age_seconds=0,
        stale=False,
        stale_reason=None,
        generation=record.generation,  # type: ignore[attr-defined]
        source_id=record.provider,  # type: ignore[attr-defined]
        source_label=record.provider,  # type: ignore[attr-defined]
        coverage=record.coverage,  # type: ignore[attr-defined]
    )


def _snapshot(mode: MarketDataMode, minute: datetime) -> MarketSnapshot:
    quote = MarketQuote(
        symbol="AAPL",
        bid=Decimal("100"),
        ask=Decimal("100.01"),
        last=Decimal("100"),
        close=None,
        bid_size=Decimal("400"),
        ask_size=Decimal("600"),
        mode=mode,
        updated_at=minute,
        age_seconds=0,
        stale=False,
        stale_reason=None,
        generation=1,
        source_id=PROVIDER,
        source_label=PROVIDER,
        coverage=COVERAGE,
    )
    return MarketSnapshot(
        generation=1,
        connected=True,
        ready=True,
        reconnect_attempt=0,
        quotes=(quote,),
        error_code=None,
        message="migration test",
        observed_at=minute,
        source_id=PROVIDER,
        source_label=PROVIDER,
        coverage=COVERAGE,
    )


def _legacy_database(path: Path) -> None:
    """The canonical v1 fixture: six symbols covering every legacy case."""

    _create_legacy_database(
        path,
        [
            _legacy_row(symbol, market_data_type)
            for symbol, market_data_type, _ in LEGACY_MODE_CASES
        ],
    )


class LegacyMinuteMigrationTests(unittest.TestCase):
    def test_upgrade_backfills_mode_and_retains_legacy_column(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "minute.sqlite3"
            _legacy_database(path)
            self.assertNotIn("mode", _schema_columns(path))

            MinuteQuoteStore(path)

            columns = _schema_columns(path)
            self.assertIn("market_data_type", columns)
            self.assertIn("mode", columns)
            # The uniqueness contract must survive the upgrade untouched.
            self.assertEqual(
                _unique_columns(path),
                ("symbol", "minute", "provider"),
            )

    def test_upgrade_maps_every_legacy_market_data_type(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "minute.sqlite3"
            _legacy_database(path)

            MinuteQuoteStore(path)

            self.assertEqual(
                _stored_modes(path),
                {symbol: mode for symbol, _, mode in LEGACY_MODE_CASES},
            )

    def test_store_load_reads_the_migrated_modes(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "minute.sqlite3"
            _legacy_database(path)
            store = MinuteQuoteStore(path)

            for symbol, _, mode in LEGACY_MODE_CASES:
                record = store.load(symbol, usable_only=False)[0]
                self.assertIs(record.mode, MarketDataMode(mode))

    def test_legacy_type_one_replays_as_realtime_ready(self) -> None:
        """The blocker: a v1 realtime row must stay usable after upgrade."""

        with TemporaryDirectory() as directory:
            path = Path(directory) / "minute.sqlite3"
            _legacy_database(path)

            store = MinuteQuoteStore(path)
            record = store.load("AAPL", usable_only=False)[0]

            self.assertIs(record.mode, MarketDataMode.REALTIME)
            self.assertTrue(record.realtime_ready)
            self.assertEqual(record.bid, Decimal("100"))
            self.assertEqual(record.ask, Decimal("100.01"))

            quote = _replay_quote(record)
            self.assertIs(quote.mode, MarketDataMode.REALTIME)
            self.assertTrue(quote.realtime_ready)
            # The default (usable-only) read must see it too.
            self.assertEqual(len(store.load("AAPL")), 1)

    def test_legacy_delayed_is_not_upgraded_to_realtime(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "minute.sqlite3"
            _legacy_database(path)

            store = MinuteQuoteStore(path)
            record = store.load("NVDA", usable_only=False)[0]

            self.assertIs(record.mode, MarketDataMode.DELAYED)
            # Even with a favourable legacy ``realtime_ready``/``stale`` and a
            # legal bid/ask, the domain must fail closed on a delayed feed.
            # The stored ``realtime_ready`` column is v1 evidence metadata,
            # not domain truth: only ``mode`` may decide usability.
            self.assertTrue(record.realtime_ready)
            quote = _replay_quote(record)
            self.assertIs(quote.mode, MarketDataMode.DELAYED)
            self.assertFalse(quote.realtime_ready)

    def test_legacy_frozen_and_delayed_frozen_modes_are_exact(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "minute.sqlite3"
            _legacy_database(path)

            store = MinuteQuoteStore(path)

            frozen = store.load("MSFT", usable_only=False)[0]
            self.assertIs(frozen.mode, MarketDataMode.FROZEN)
            self.assertFalse(_replay_quote(frozen).realtime_ready)

            delayed_frozen = store.load("SPY", usable_only=False)[0]
            self.assertIs(
                delayed_frozen.mode,
                MarketDataMode.DELAYED_FROZEN,
            )
            self.assertFalse(_replay_quote(delayed_frozen).realtime_ready)

    def test_legacy_unknown_types_fail_closed(self) -> None:
        """``NULL`` and an out-of-range type must never guess realtime."""

        with TemporaryDirectory() as directory:
            path = Path(directory) / "minute.sqlite3"
            _legacy_database(path)

            store = MinuteQuoteStore(path)

            for symbol in ("QQQ", "IWM"):
                record = store.load(symbol, usable_only=False)[0]
                self.assertIs(record.mode, MarketDataMode.UNKNOWN)
                self.assertFalse(_replay_quote(record).realtime_ready)

    def test_existing_mode_is_never_overwritten(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "minute.sqlite3"
            _create_legacy_database(
                path,
                [
                    _legacy_row("AAPL", 1, mode="delayed"),
                    _legacy_row("MSFT", 1, mode="realtime"),
                ],
                with_mode_column=True,
            )

            store = MinuteQuoteStore(path)
            self.assertEqual(
                _stored_modes(path),
                {"AAPL": "delayed", "MSFT": "realtime"},
            )
            # Re-opening must not re-run the backfill over a v2 value.
            MinuteQuoteStore(path)
            self.assertEqual(
                _stored_modes(path),
                {"AAPL": "delayed", "MSFT": "realtime"},
            )
            self.assertIs(
                store.load("AAPL", usable_only=False)[0].mode,
                MarketDataMode.DELAYED,
            )

    def test_migration_is_idempotent_across_reopens(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "minute.sqlite3"
            _legacy_database(path)

            MinuteQuoteStore(path)
            first = _stored_modes(path)
            MinuteQuoteStore(path)
            MinuteQuoteStore(path)

            self.assertEqual(_stored_modes(path), first)
            self.assertEqual(
                first,
                {symbol: mode for symbol, _, mode in LEGACY_MODE_CASES},
            )

    def test_empty_mode_string_is_backfilled(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "minute.sqlite3"
            _create_legacy_database(
                path,
                [
                    _legacy_row("MSFT", 2, mode=""),
                    _legacy_row("NVDA", 3, mode=""),
                ],
                with_mode_column=True,
            )

            store = MinuteQuoteStore(path)

            self.assertEqual(
                _stored_modes(path),
                {"MSFT": "frozen", "NVDA": "delayed"},
            )
            self.assertIs(
                store.load("MSFT", usable_only=False)[0].mode,
                MarketDataMode.FROZEN,
            )

    def test_new_database_creates_mode_without_legacy_column(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fresh.sqlite3"

            MinuteQuoteStore(path)

            columns = _schema_columns(path)
            self.assertIn("mode", columns)
            self.assertNotIn("market_data_type", columns)
            self.assertEqual(
                _unique_columns(path),
                ("symbol", "minute", "provider"),
            )

    def test_record_snapshot_roundtrips_every_mode(self) -> None:
        minute = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as directory:
            for mode in MarketDataMode:
                path = Path(directory) / f"{mode.value}.sqlite3"
                store = MinuteQuoteStore(path)
                self.assertEqual(
                    store.record_snapshot(_snapshot(mode, minute)),
                    1,
                )
                record = store.load("AAPL", usable_only=False)[0]
                self.assertIs(record.mode, mode)
                self.assertEqual(
                    _stored_modes(path),
                    {"AAPL": mode.value},
                )

    def test_record_snapshot_never_writes_the_legacy_column(self) -> None:
        source = inspect.getsource(MinuteQuoteStore.record_snapshot)
        self.assertIn("mode", source)
        self.assertNotIn("market_data_type", source)

    def test_fingerprint_serialises_migrated_enum_modes(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "minute.sqlite3"
            _legacy_database(path)

            store = MinuteQuoteStore(path)
            records = tuple(
                store.load(symbol, usable_only=False)[0]
                for symbol, _, _ in LEGACY_MODE_CASES
            )
            # ``fingerprint`` runs ``asdict`` over records whose ``mode`` is a
            # ``MarketDataMode``; ``json.dumps`` must not choke on the enum.
            digest = MinuteQuoteStore.fingerprint(records)

            self.assertEqual(len(digest), 64)
            self.assertEqual(
                digest,
                MinuteQuoteStore.fingerprint(records),
            )
            self.assertNotEqual(
                digest,
                MinuteQuoteStore.fingerprint(
                    tuple(
                        record
                        for record in records
                        if record.symbol != "AAPL"
                    )
                ),
            )


class MigrationStructuralGuardTests(unittest.TestCase):
    """Guard the migration against a future "clean up the old column" pass.

    The guard is about *behaviour that must stay* -- the legacy column is
    still read as a backfill source and still maps onto the four real modes
    -- not about the string ``market_data_type`` existing forever.  A later
    schema-version framework may retire it; this PR may not drop it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_legacy_source_column_and_full_mode_mapping_are_present(
        self,
    ) -> None:
        self.assertIn("market_data_type", self.source)
        for mode in (
            MarketDataMode.REALTIME,
            MarketDataMode.FROZEN,
            MarketDataMode.DELAYED,
            MarketDataMode.DELAYED_FROZEN,
        ):
            self.assertIn(f"MarketDataMode.{mode.name}.value", self.source)

    def test_helper_reads_the_pre_upgrade_column_snapshot(self) -> None:
        function = _find_function(
            self.tree,
            class_name="MinuteQuoteStore",
            function_name="_initialize",
        )
        self.assertIsNotNone(function)
        assert function is not None
        body = ast.unparse(function)
        self.assertIn("had_legacy_market_data_type", body)
        self.assertIn("PRAGMA table_info(minute_quote)", body)

    def test_backfill_runs_inside_the_initialize_transaction(self) -> None:
        function = _find_function(
            self.tree,
            class_name="MinuteQuoteStore",
            function_name="_initialize",
        )
        self.assertIsNotNone(function)
        assert function is not None
        self.assertTrue(
            _calls_inside_connection_transaction(
                function,
                "_migrate_legacy_market_data_type",
            ),
            "legacy backfill must run inside the same transaction as the "
            "mode column addition",
        )
        # A mid-transaction commit would let the ALTER land without the
        # backfill, leaving half-migrated rows behind.
        self.assertNotIn("commit()", ast.unparse(function))

    def test_backfill_helper_only_depends_on_sqlite_and_the_mode_enum(
        self,
    ) -> None:
        function = _find_function(
            self.tree,
            function_name="_migrate_legacy_market_data_type",
        )
        self.assertIsNotNone(function)
        assert function is not None
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(
                node.func, ast.Attribute
            ):
                self.assertIn(
                    node.func.attr,
                    {"execute"},
                    "the helper must stay dependency-free",
                )


def _find_function(
    tree: ast.Module,
    *,
    function_name: str,
    class_name: str | None = None,
) -> ast.FunctionDef | None:
    if class_name is None:
        for node in tree.body:
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == function_name
            ):
                return node
        return None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if (
                    isinstance(child, ast.FunctionDef)
                    and child.name == function_name
                ):
                    return child
    return None


def _calls_inside_connection_transaction(
    function: ast.FunctionDef,
    function_name: str,
) -> bool:
    """True when ``function_name`` is called under ``with connection:``."""

    for node in ast.walk(function):
        if not isinstance(node, ast.With):
            continue
        if not any(
            isinstance(item.context_expr, ast.Name)
            and item.context_expr.id == "connection"
            for item in node.items
        ):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == function_name
            ):
                return True
    return False


if __name__ == "__main__":
    unittest.main()
