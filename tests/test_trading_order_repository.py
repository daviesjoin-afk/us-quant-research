"""The order store in the trading layer: SQLite behind ``OrderRepositoryPort``.

Covers the structural boundary (the store must not know about brokers, GUIs or
threads) and, most importantly, that a database written by the *previous* build
still reads back correctly: the migration was a relocation behind a port, so no
user may be asked to delete and rebuild their order history.

The write path speaks the domain now -- ``OrderIntent`` / ``OrderEvent`` /
``ExecutionFill`` -- while the table layout, the query order and the stored text
formats stay byte-identical to the retired ``paper_order_journal``.  The store
keeps the broker's own status text in its ``status`` column; the domain status is
derived on read, once, through ``order_status_from_text``.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import importlib
import inspect
import sqlite3
from tempfile import TemporaryDirectory

import pytest

import us_quant.trading.adapters.ibkr.execution as adapter_module
import us_quant.trading.adapters.sqlite.order_repository as repository_module
from us_quant.paper_order_models import TERMINAL_ORDER_STATUSES
from us_quant.trading.adapters import clock
from us_quant.trading.adapters.clock import now_iso
from us_quant.trading.adapters.order_status_mapping import (
    order_status_from_text,
)
from us_quant.trading.adapters.sqlite.order_repository import (
    SQLiteOrderRepository,
)
from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
    OrderStatus,
    Side,
)

# The schema as written by the module before the split, copied verbatim from
# git history. It must stay byte-identical to what the store creates today,
# otherwise old databases stop opening.
LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_order_intent(
    intent_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    strategy_version_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    limit_price TEXT NOT NULL,
    reason TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    broker_order_id INTEGER NOT NULL UNIQUE,
    account_alias TEXT NOT NULL,
    idempotency_key TEXT
);

CREATE TABLE IF NOT EXISTS paper_order_update(
    update_id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id TEXT NOT NULL,
    broker_order_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    filled TEXT NOT NULL,
    remaining TEXT NOT NULL,
    average_fill_price TEXT,
    last_fill_price TEXT,
    message TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    FOREIGN KEY(intent_id) REFERENCES paper_order_intent(intent_id)
);

CREATE TABLE IF NOT EXISTS paper_execution(
    execution_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL UNIQUE,
    intent_id TEXT NOT NULL,
    broker_order_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY(intent_id) REFERENCES paper_order_intent(intent_id)
);
"""


def _repository_module_source() -> str:
    return Path(repository_module.__file__ or "").read_text(encoding="utf-8")


def _adapter_source() -> str:
    return Path(adapter_module.__file__ or "").read_text(encoding="utf-8")


def test_the_retired_journal_module_is_really_gone() -> None:
    """The store moved into the trading layer; the old module must not linger.

    A half-migration that leaves ``paper_order_journal`` importable is how two
    stores start to exist and rows get split across them.
    """

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("us_quant.paper_order_journal")

    assert (
        SQLiteOrderRepository.__module__
        == "us_quant.trading.adapters.sqlite.order_repository"
    )


def test_repository_module_does_not_reach_for_the_broker_or_the_gui() -> None:
    """The dependency arrow points at the domain, never back at the adapter."""

    source = _repository_module_source()

    for forbidden in (
        "ibapi",
        "IBKRExecutionAdapter",
        "QThread",
        "MainWindow",
        "ExecutionLease",
        "PySide6",
        "desktop",
        "paper_trading_service",
        "trading.adapters.ibkr",
    ):
        assert forbidden not in source, forbidden


def test_the_clock_has_exactly_one_definition() -> None:
    """One definition of the timestamp rules, not two.

    A duplicate under any name is the failure mode: two formatters drift and
    the stored timestamps stop being comparable.  The check is by behaviour
    (every module-level function that renders the stored clock text) rather than
    by the single name ``_now_iso``, so a renamed copy is still caught.

    The execution adapter may still fall back to ``datetime.now`` for a broker
    execution time it cannot parse, but that returns a ``datetime``, never the
    stored text -- hence the ``.isoformat()`` marker.
    """

    factory = clock  # the single definition both adapters import

    def clock_formatters(module) -> set[str]:
        names = set()
        for name, value in vars(module).items():
            if not inspect.isfunction(value):
                continue
            if getattr(value, "__module__", None) != module.__name__:
                continue
            source = inspect.getsource(value)
            if "datetime.now" in source and ".isoformat()" in source:
                names.add(name)
        return names

    for source in (_repository_module_source(), _adapter_source()):
        assert "def now_iso" not in source
        assert "def _now_iso" not in source
        assert "def to_stored_text" not in source
        assert "def from_stored_text" not in source

    assert clock_formatters(repository_module) == set()
    assert clock_formatters(adapter_module) == set()
    assert callable(factory.now_iso)
    assert now_iso is factory.now_iso


def test_terminal_statuses_are_shared_from_the_models() -> None:
    assert repository_module.TERMINAL_ORDER_STATUSES is TERMINAL_ORDER_STATUSES


def test_reconciliation_reason_codes_stay_distinguishable() -> None:
    """The status vocabulary drives whether an order counts as settled.

    Pins the two rules that decide ``reconciled``: only terminal statuses can
    settle, and the filled quantity must equal the sum of executions.
    """

    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        repository = SQLiteOrderRepository(path)
        repository.record_intent(
            _intent(), broker_order_id=10, account_alias="DU***17"
        )

        # A working (non-terminal) order is never reconciled...
        repository.record_event(
            _update(status="Submitted", filled=Decimal("0"))
        )
        row = repository.reconciliation_rows()[0]
        assert row.terminal is False
        assert row.reconciled is False

        # ...and a terminal order whose fills disagree with its executions is
        # not reconciled either, even though the status alone would say so.
        repository.record_fill(_execution())
        repository.record_event(_update(status="Filled", filled=Decimal("1")))
        assert repository.reconciliation_rows()[0].reconciled is True

        # Drop the execution row: status still terminal, quantities no longer
        # match, so reconciliation must fail.
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute("DELETE FROM paper_execution")

        row = repository.reconciliation_rows()[0]
        assert row.terminal is True
        assert row.reconciled is False
        assert repository.reconciliation_summary().terminal_unreconciled == 1


def test_reconciliation_rows_are_scoped_to_the_requested_session() -> None:
    """Scoping silently disappearing would mix two sessions' orders together."""

    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        repository = SQLiteOrderRepository(path)

        first = _intent()
        repository.record_intent(first, broker_order_id=10, account_alias="DU***17")
        repository.record_event(_update())

        second = _intent(order_id="i-2", session_id="s-2", idempotency_key="k-2")
        repository.record_intent(second, broker_order_id=11, account_alias="DU***17")
        repository.record_event(
            _update(order_id="i-2", broker_order_id=11)
        )

        assert len(repository.reconciliation_rows()) == 2
        scoped = repository.reconciliation_rows(session_id="s-1")
        assert [row.intent_id for row in scoped] == ["i-1"]
        assert repository.reconciliation_summary(session_id="s-2").total == 1

        # Both orders settled, so neither session has anything pending; an
        # unscoped read would still see both, and a broken scope would hide the
        # non-terminal one.
        assert repository.pending_orders_for_session("s-1") == ()
        assert repository.pending_orders_for_session("s-2") == ()

        repository.record_event(
            _update(status="Submitted", filled=Decimal("0"))
        )
        pending = repository.pending_orders_for_session("s-1")
        assert [intent.order_id for intent in pending] == ["i-1"]
        assert repository.pending_orders_for_session("s-2") == ()
        assert len(repository.audit_rows()) == 2


def test_idempotency_keys_survive_a_write_and_are_not_silently_nulled() -> None:
    """The key is the duplicate-order guard; losing it risks a double submit."""

    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        repository = SQLiteOrderRepository(path)
        repository.record_intent(
            _intent(idempotency_key="key-alpha"),
            broker_order_id=10,
            account_alias="DU***17",
        )

        stored = repository.intent_for_idempotency_key("key-alpha")
        assert stored is not None
        assert stored.order_id == "i-1"

        # The column is written through, not left NULL in the table.
        with closing(sqlite3.connect(path)) as connection:
            raw = connection.execute(
                "SELECT idempotency_key FROM paper_order_intent"
            ).fetchone()[0]
        assert raw == "key-alpha"

        assert repository.intent_for_idempotency_key("key-absent") is None


def test_new_store_still_writes_and_reads_a_full_round_trip() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        repository = SQLiteOrderRepository(path)

        repository.record_intent(
            _intent(),
            broker_order_id=10,
            account_alias="DU***17",
        )
        repository.record_event(_update())
        assert repository.record_fill(_execution()) is True

        reopened = SQLiteOrderRepository(path)

        assert reopened.max_broker_order_id() == 10
        assert reopened.executed_quantity("i-1") == Decimal("1")
        assert reopened.intent_for_broker_order(10) is not None
        assert reopened.intent_for_idempotency_key("k-1") is not None

        rows = reopened.reconciliation_rows()
        assert len(rows) == 1
        assert rows[0].reconciled is True
        assert rows[0].terminal is True

        summary = reopened.reconciliation_summary()
        assert summary.total == 1
        assert summary.reconciled == 1
        assert summary.unreconciled == 0
        assert summary.terminal_unreconciled == 0


def test_record_event_stores_the_raw_broker_status_text() -> None:
    """The column keeps what the channel said, not our spelling of it.

    Reconciliation and the audit view compare against the stored text, so
    normalising it on write would make the stored row disagree with a later
    broker report about the same order.
    """

    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        repository = SQLiteOrderRepository(path)
        repository.record_intent(
            _intent(), broker_order_id=10, account_alias="DU***17"
        )

        # A channel spelling whose domain value spells differently.
        repository.record_event(_update(status="ApiCancelled"))

        with closing(sqlite3.connect(path)) as connection:
            raw = connection.execute(
                "SELECT status FROM paper_order_update"
            ).fetchone()[0]

        assert raw == "ApiCancelled"
        assert repository.audit_rows()[0]["latest_status"] == "ApiCancelled"


def test_status_maps_the_stored_broker_text_back_to_the_domain() -> None:
    """The domain status is derived on read, never stored in the row."""

    expected = {
        "Filled": OrderStatus.FILLED,
        "Cancelled": OrderStatus.CANCELED,
        "ApiCancelled": OrderStatus.CANCELED,
        "Inactive": OrderStatus.INACTIVE,
        "Error": OrderStatus.BROKER_REJECTED,
        "Submitted": OrderStatus.ACKNOWLEDGED,
        "Weird": OrderStatus.UNKNOWN,
    }

    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        repository = SQLiteOrderRepository(path)
        repository.record_intent(
            _intent(), broker_order_id=10, account_alias="DU***17"
        )

        assert repository.status("i-1") is None

        for text, domain in expected.items():
            repository.record_event(_update(status=text))
            assert repository.status("i-1") is domain, text

        assert repository.status("absent") is None


def test_record_fill_is_idempotent_on_the_execution_id() -> None:
    """A replayed execution report must not be counted twice."""

    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        repository = SQLiteOrderRepository(path)
        repository.record_intent(
            _intent(), broker_order_id=10, account_alias="DU***17"
        )

        assert repository.record_fill(_execution()) is True
        assert repository.record_fill(_execution()) is False

        with closing(sqlite3.connect(path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM paper_execution"
            ).fetchone()[0]
        assert count == 1
        assert repository.executed_quantity("i-1") == Decimal("1")


def test_fills_round_trip_quantities_prices_and_times() -> None:
    """Fills come back typed: a float or a raw string here would be a bug."""

    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        repository = SQLiteOrderRepository(path)
        repository.record_intent(
            _intent(), broker_order_id=10, account_alias="DU***17"
        )
        repository.record_fill(
            _execution(quantity=Decimal("1.5"), price=Decimal("200.25"))
        )

        fills = repository.fills("i-1")
        assert len(fills) == 1
        fill = fills[0]
        assert fill.execution_id == "e-1"
        assert fill.order_id == "i-1"
        assert fill.broker_order_id == 10
        assert fill.symbol == "AAPL"
        assert fill.side is Side.BUY
        assert isinstance(fill.quantity, Decimal)
        assert fill.quantity == Decimal("1.5")
        assert isinstance(fill.price, Decimal)
        assert fill.price == Decimal("200.25")
        assert isinstance(fill.occurred_at, datetime)
        assert fill.occurred_at == datetime.fromisoformat(
            "2026-07-26T12:00:04+00:00"
        )

        assert repository.fills("absent") == ()


def test_intent_round_trips_through_the_store() -> None:
    """A stored order reads back as a domain intent, identity included."""

    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        repository = SQLiteOrderRepository(path)
        repository.record_intent(
            _intent(idempotency_key="key-alpha"),
            broker_order_id=10,
            account_alias="DU***17",
        )

        stored = repository.intent("i-1")
        assert stored is not None
        assert stored.order_id == "i-1"
        # Derived, not stored: the client id is the deterministic spelling.
        assert stored.client_order_id == "uq-i-1"
        assert stored.session_id == "s-1"
        assert stored.strategy_version_id == "v-1"
        assert stored.signal_symbol == "AAPL"
        assert stored.execution_symbol == "AAPL"
        assert stored.side is Side.BUY
        assert stored.quantity == 1
        assert stored.limit_price == Decimal("200")
        assert stored.reason == "test"
        assert stored.idempotency_key == "key-alpha"
        assert stored.created_at == datetime.fromisoformat(
            "2026-07-26T12:00:00+00:00"
        )

        assert repository.broker_order_id("i-1") == 10
        assert repository.intent("absent") is None
        assert repository.broker_order_id("absent") is None


def test_a_database_written_before_the_split_still_works() -> None:
    """The migration contract: old files keep opening, reading and growing.

    The database is built with the pre-split schema and pre-split rows, then
    handed to the new store, which must read it, reconcile it and accept
    further writes without any migration step.
    """

    with TemporaryDirectory() as directory:
        path = Path(directory) / "legacy.sqlite3"
        connection = sqlite3.connect(path)
        with connection:
            connection.executescript(LEGACY_SCHEMA)
            connection.execute(
                """
                INSERT INTO paper_order_intent(
                    intent_id, session_id, strategy_version_id, symbol,
                    side, quantity, limit_price, reason, generated_at,
                    broker_order_id, account_alias, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-1",
                    "session-old",
                    "version-old",
                    "MSFT",
                    "BUY",
                    3,
                    "410.25",
                    "written by the previous build",
                    "2026-07-26T12:00:00+00:00",
                    77,
                    "DU***17",
                    "legacy-key",
                ),
            )
            connection.execute(
                """
                INSERT INTO paper_order_update(
                    intent_id, broker_order_id, status, filled, remaining,
                    average_fill_price, last_fill_price, message, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-1",
                    77,
                    "Filled",
                    "3",
                    "0",
                    "410.25",
                    "410.25",
                    "done",
                    "2026-07-26T12:00:05+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO paper_execution(
                    execution_id, intent_id, broker_order_id, symbol, side,
                    quantity, price, occurred_at, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "exec-legacy",
                    "legacy-1",
                    77,
                    "MSFT",
                    "BUY",
                    "3",
                    "410.25",
                    "2026-07-26T12:00:04+00:00",
                    "2026-07-26T12:00:06+00:00",
                ),
            )
        connection.close()

        repository = SQLiteOrderRepository(path)

        intent = repository.intent_for_broker_order(77)
        assert intent is not None
        assert intent.order_id == "legacy-1"
        assert intent.client_order_id == "uq-legacy-1"
        assert intent.signal_symbol == "MSFT"
        assert intent.execution_symbol == "MSFT"
        assert intent.side is Side.BUY
        assert intent.quantity == 3
        assert intent.limit_price == Decimal("410.25")
        assert intent.idempotency_key == "legacy-key"

        assert repository.intent_for_idempotency_key("legacy-key") is not None
        assert repository.max_broker_order_id() == 77
        assert repository.executed_quantity("legacy-1") == Decimal("3")

        rows = repository.reconciliation_rows()
        assert len(rows) == 1
        assert rows[0].latest_status == "Filled"
        assert rows[0].terminal is True
        assert rows[0].reconciled is True
        assert rows[0].intended_quantity == Decimal("3")

        summary = repository.reconciliation_summary()
        assert summary.total == 1
        assert summary.reconciled == 1

        audit = repository.audit_rows()
        assert len(audit) == 1
        assert audit[0]["intent_id"] == "legacy-1"

        # ...and the old file keeps accepting new orders.
        repository.record_intent(
            _intent(),
            broker_order_id=78,
            account_alias="DU***17",
        )
        repository.record_event(_update())

        assert repository.max_broker_order_id() == 78
        assert len(repository.reconciliation_rows()) == 2
        assert repository.reconciliation_summary().total == 2


def test_legacy_schema_matches_what_the_journal_creates_today() -> None:
    """Guards the test above: if the DDL drifted, it would prove nothing."""

    with TemporaryDirectory() as directory:
        fresh = Path(directory) / "fresh.sqlite3"
        SQLiteOrderRepository(fresh)

        connection = sqlite3.connect(fresh)
        created = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table'"
            )
        }
        connection.close()

    legacy_connection = sqlite3.connect(":memory:")
    legacy_connection.executescript(LEGACY_SCHEMA)
    legacy = {
        row[0]: row[1]
        for row in legacy_connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table'"
        )
    }
    legacy_connection.close()

    assert set(created) == set(legacy)
    for name, sql in legacy.items():
        assert _normalise(created[name]) == _normalise(sql), name


def test_sessions_rolls_up_each_session_with_its_own_totals() -> None:
    """The per-session rollup groups, counts, sums and orders correctly.

    Two sessions with deliberately different shapes: ``s-2`` starts later and
    carries two intents (one still working), ``s-1`` has one settled intent.
    Reading the values back from the API rather than recomputing them here is
    the point -- the query is the thing under test.
    """

    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        repository = SQLiteOrderRepository(path)

        # s-1: one intent, one execution, settled.
        repository.record_intent(
            _intent(created_at="2026-07-26T12:00:00+00:00"),
            broker_order_id=10,
            account_alias="DU***17",
        )
        repository.record_fill(_execution())
        repository.record_event(
            _update(
                filled=Decimal("1"),
                occurred_at="2026-07-26T12:00:05+00:00",
            )
        )

        # s-2: two intents, later start, one settled and one still working.
        repository.record_intent(
            _intent(
                order_id="i-2",
                session_id="s-2",
                idempotency_key="k-2",
                created_at="2026-07-26T13:00:00+00:00",
            ),
            broker_order_id=11,
            account_alias="DU***17",
        )
        repository.record_fill(
            _execution(
                order_id="i-2",
                broker_order_id=11,
                execution_id="e-2",
                occurred_at="2026-07-26T13:00:04+00:00",
            )
        )
        repository.record_event(
            _update(
                order_id="i-2",
                broker_order_id=11,
                status="Filled",
                filled=Decimal("2"),
                occurred_at="2026-07-26T13:00:07+00:00",
            )
        )
        repository.record_intent(
            _intent(
                order_id="i-3",
                session_id="s-2",
                idempotency_key="k-3",
                created_at="2026-07-26T13:01:00+00:00",
            ),
            broker_order_id=12,
            account_alias="DU***17",
        )
        repository.record_fill(
            _execution(
                order_id="i-3",
                broker_order_id=12,
                execution_id="e-3",
                quantity=Decimal("0"),
                occurred_at="2026-07-26T13:01:02+00:00",
            )
        )
        repository.record_event(
            _update(
                order_id="i-3",
                broker_order_id=12,
                status="Submitted",
                filled=Decimal("0"),
                occurred_at="2026-07-26T13:01:03+00:00",
            )
        )

        rows = repository.sessions()
        assert [row["session_id"] for row in rows] == ["s-2", "s-1"]

        later, earlier = rows
        assert later["started_at"] == "2026-07-26T13:00:00+00:00"
        assert later["last_activity_at"] == "2026-07-26T13:01:03+00:00"
        assert later["intent_count"] == 2
        assert later["filled_quantity"] == Decimal("2")
        # Equality alone would not catch a float leaking through: Decimal("2")
        # == 2.0 is True, so the type has to be asserted on its own.
        assert isinstance(later["filled_quantity"], Decimal)
        # MAX(status) over the newest update per intent: "Submitted" > "Filled".
        assert later["latest_status"] == "Submitted"

        assert earlier["started_at"] == "2026-07-26T12:00:00+00:00"
        assert earlier["last_activity_at"] == "2026-07-26T12:00:05+00:00"
        assert earlier["intent_count"] == 1
        assert earlier["filled_quantity"] == Decimal("1")
        assert isinstance(earlier["filled_quantity"], Decimal)
        assert earlier["latest_status"] == "Filled"

        # limit is applied after the ORDER BY, so the newest session survives.
        limited = repository.sessions(limit=1)
        assert [row["session_id"] for row in limited] == ["s-2"]


def test_sessions_uses_the_intent_time_when_no_update_arrived() -> None:
    """An intent with no broker update still reports, dated from its own time.

    The LEFT JOIN plus COALESCE is what keeps a just-submitted order visible;
    dropping either would silently hide the newest session.
    """

    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        repository = SQLiteOrderRepository(path)
        repository.record_intent(
            _intent(created_at="2026-07-26T09:30:00+00:00"),
            broker_order_id=10,
            account_alias="DU***17",
        )

        rows = repository.sessions()
        assert len(rows) == 1
        row = rows[0]
        assert row["session_id"] == "s-1"
        assert row["started_at"] == "2026-07-26T09:30:00+00:00"
        assert row["last_activity_at"] == "2026-07-26T09:30:00+00:00"
        assert row["intent_count"] == 1
        assert row["filled_quantity"] == Decimal("0")
        assert row["latest_status"] == ""


def test_sessions_rollup_returns_nothing_for_an_empty_store() -> None:
    with TemporaryDirectory() as directory:
        repository = SQLiteOrderRepository(Path(directory) / "orders.sqlite3")
        assert repository.sessions() == ()


def test_adapter_no_longer_reaches_for_sqlite_directly() -> None:
    """Persistence belongs to the store; the adapter only delegates.

    ``sessions`` was the last method running its own SQL against a
    ``self.path`` the adapter never had. This guard exists so a future
    persistence query cannot quietly move back into the adapter.
    """

    source = _adapter_source()

    assert "connect_sqlite" not in source
    assert "closing(" not in source
    assert "self.path" not in source
    assert "FROM paper_order_intent" not in source
    assert "sqlite3" not in source


def test_repository_owns_the_sessions_rollup() -> None:
    """The query must live in the store and be reachable as a method."""

    assert callable(getattr(SQLiteOrderRepository, "sessions", None))

    source = _repository_module_source()
    assert "connect_sqlite(self.path)" in source
    assert "FROM paper_order_intent" in source
    assert "GROUP BY i.session_id" in source


def test_adapter_sessions_is_a_pure_delegate() -> None:
    """Structure check for the compatibility shim itself.

    The delegate has to stay a one-liner: a re-implemented query here would
    reintroduce exactly the split-brain this step removed.
    """

    import ast

    source = _adapter_source()
    tree = ast.parse(source)
    # Scope to the adapter class: the port protocol declares a ``sessions``
    # stub too, and a global walk would find that one first.
    adapter = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "IBKRExecutionAdapter"
    )
    found = next(
        (
            node
            for node in adapter.body
            if isinstance(node, ast.FunctionDef) and node.name == "sessions"
        ),
        None,
    )

    assert found is not None, "IBKRExecutionAdapter.sessions disappeared"
    body = [stmt for stmt in found.body if not isinstance(stmt, ast.Expr)]
    assert len(body) == 1, f"delegate has {len(body)} statements"
    returns = body[0]
    assert isinstance(returns, ast.Return)
    call = returns.value
    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Attribute)
    assert call.func.attr == "sessions"
    assert isinstance(call.func.value, ast.Attribute)
    assert call.func.value.attr == "repository"


# The layout of the very first order databases: the intent table was created
# without ``idempotency_key``, and ``CREATE TABLE IF NOT EXISTS`` never grows a
# table that already exists.  Such a file rejects every write that names the
# column -- which is every order.
PRE_IDEMPOTENCY_SCHEMA = LEGACY_SCHEMA.replace(
    """    account_alias TEXT NOT NULL,
    idempotency_key TEXT
);""",
    """    account_alias TEXT NOT NULL
);""",
)


def test_pre_idempotency_schema_really_lacks_the_column() -> None:
    """Guards the two tests below: if the fixture grew the column, they prove
    nothing."""

    assert PRE_IDEMPOTENCY_SCHEMA != LEGACY_SCHEMA
    assert "idempotency_key" not in PRE_IDEMPOTENCY_SCHEMA


def test_a_database_without_the_idempotency_column_is_grown_in_place() -> None:
    """An existing install whose order database predates the idempotency key
    could not accept a single order: the insert names the column and SQLite
    refuses it.  The store adds the missing column in place -- no row
    rewritten, no stored value touched -- so the same file keeps working.
    """

    with TemporaryDirectory() as directory:
        path = Path(directory) / "legacy-narrow.sqlite3"
        connection = sqlite3.connect(path)
        with connection:
            connection.executescript(PRE_IDEMPOTENCY_SCHEMA)
            connection.execute(
                """
                INSERT INTO paper_order_intent(
                    intent_id, session_id, strategy_version_id, symbol,
                    side, quantity, limit_price, reason, generated_at,
                    broker_order_id, account_alias
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "narrow-1",
                    "session-old",
                    "version-old",
                    "MSFT",
                    "BUY",
                    1,
                    "410.25",
                    "written before the key existed",
                    "2026-07-26T12:00:00+00:00",
                    11,
                    "DU***17",
                ),
            )
        connection.close()

        store = SQLiteOrderRepository(path)

        # The stored row survived untouched...
        row = store.reconciliation_rows()[0]
        assert row.intent_id == "narrow-1"
        assert row.intended_quantity == Decimal("1")
        legacy = store.intent_for_broker_order(11)
        assert legacy is not None
        assert legacy.idempotency_key == ""
        # ...and the file accepts new orders again.
        intent = _intent(session_id="session-new")
        store.record_intent(
            intent, broker_order_id=12, account_alias="DU***17"
        )
        store.record_event(_update(order_id=intent.order_id))
        assert store.intent(intent.order_id) is not None
        assert len(store.audit_rows()) == 2
        with closing(sqlite3.connect(path)) as check:
            columns = {
                info[1]
                for info in check.execute(
                    "PRAGMA table_info(paper_order_intent)"
                )
            }
        assert "idempotency_key" in columns


def test_an_up_to_date_database_is_not_rewritten() -> None:
    """The repair must not touch a file that already has the column."""

    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        SQLiteOrderRepository(path)
        first = path.read_bytes()
        SQLiteOrderRepository(path)
        assert path.read_bytes() == first


def _normalise(sql: str) -> str:
    return " ".join(sql.split()).replace("( ", "(").replace(" )", ")")


def _intent(
    *,
    order_id: str = "i-1",
    session_id: str = "s-1",
    idempotency_key: str = "k-1",
    created_at: str = "2026-07-26T12:00:00+00:00",
) -> OrderIntent:
    return OrderIntent(
        order_id=order_id,
        client_order_id=f"uq-{order_id}",
        session_id=session_id,
        strategy_version_id="v-1",
        signal_symbol="AAPL",
        execution_symbol="AAPL",
        side=Side.BUY,
        quantity=1,
        limit_price=Decimal("200"),
        reason="test",
        idempotency_key=idempotency_key,
        created_at=datetime.fromisoformat(created_at),
    )


def _update(
    *,
    order_id: str = "i-1",
    broker_order_id: int = 10,
    status: str = "Filled",
    filled: Decimal | None = None,
    occurred_at: str = "2026-07-26T12:00:05+00:00",
) -> OrderEvent:
    return OrderEvent(
        order_id=order_id,
        status=order_status_from_text(status),
        broker_order_id=broker_order_id,
        # The channel's own spelling travels with the event; the store keeps it.
        broker_status=status,
        filled=filled if filled is not None else Decimal("1"),
        remaining=Decimal("0"),
        average_fill_price=Decimal("200"),
        last_fill_price=Decimal("200"),
        message="done",
        occurred_at=datetime.fromisoformat(occurred_at),
    )


def _execution(
    *,
    order_id: str = "i-1",
    broker_order_id: int = 10,
    execution_id: str = "e-1",
    quantity: Decimal | None = None,
    price: Decimal = Decimal("200"),
    occurred_at: str = "2026-07-26T12:00:04+00:00",
) -> ExecutionFill:
    return ExecutionFill(
        execution_id=execution_id,
        order_id=order_id,
        broker_order_id=broker_order_id,
        symbol="AAPL",
        side=Side.BUY,
        quantity=quantity if quantity is not None else Decimal("1"),
        price=price,
        occurred_at=datetime.fromisoformat(occurred_at),
    )