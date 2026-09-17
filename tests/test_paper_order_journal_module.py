"""Step 5: the Paper order journal after the move out of the adapter.

Covers the structural boundary (the journal must not know about brokers, GUIs
or threads) and, most importantly, that a database written by the *previous*
build still reads back correctly: the split was a relocation, so no user may be
asked to delete and rebuild their order history.
"""

from __future__ import annotations

from decimal import Decimal
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

import us_quant.ibkr_paper_orders as old_path
import us_quant.paper_order_journal as journal_module
from us_quant.paper_order_journal import PaperOrderJournal
from us_quant.paper_order_models import (
    PaperExecution,
    PaperOrderUpdate,
    TERMINAL_ORDER_STATUSES,
)

# The schema as written by the module before the split, copied verbatim from
# git history. It must stay byte-identical to what the journal creates today,
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


def _journal_module_source() -> str:
    return Path(journal_module.__file__ or "").read_text(encoding="utf-8")


def test_journal_is_importable_from_both_paths_and_is_the_same_class() -> None:
    assert old_path.PaperOrderJournal is journal_module.PaperOrderJournal


def test_journal_module_does_not_reach_for_the_broker_or_the_gui() -> None:
    """The dependency arrow points at the models, never back at the adapter."""

    source = _journal_module_source()

    for forbidden in (
        "IBKRPaperOrderService",
        "ibapi",
        "QThread",
        "MainWindow",
        "ExecutionLease",
        "PySide6",
        "desktop",
        "paper_trading_service",
        "ibkr_paper_orders",
    ):
        assert forbidden not in source, forbidden


def test_journal_module_has_no_private_duplicate_of_shared_helpers() -> None:
    """One definition of the timestamp and decimal text rules, not two.

    A duplicate under any name is the failure mode: two formatters drift and
    the stored timestamps stop being comparable. The check is by behaviour
    (every module-level function that formats the clock) rather than by the
    single name ``_now_iso``, so a renamed copy is still caught.
    """

    import inspect

    source = _journal_module_source()
    adapter = Path(old_path.__file__ or "").read_text(encoding="utf-8")

    assert "def _now_iso() -> str:" in source
    assert "def _now_iso() -> str:" not in adapter
    assert "def _decimal_text(" not in adapter

    def formatters(module) -> set[str]:
        names = set()
        for name, value in vars(module).items():
            if not inspect.isfunction(value):
                continue
            if getattr(value, "__module__", None) != module.__name__:
                continue
            if "datetime.now" in inspect.getsource(value):
                names.add(name)
        return names

    assert formatters(journal_module) == {"_now_iso"}


def test_reconciliation_reason_codes_stay_distinguishable() -> None:
    """The status vocabulary drives whether an order counts as settled.

    Pins the two rules that decide ``reconciled``: only terminal statuses can
    settle, and the filled quantity must equal the sum of executions.
    """

    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        journal = PaperOrderJournal(path)
        journal.record_intent(_intent(), broker_order_id=10, account_alias="DU***17")

        # A working (non-terminal) order is never reconciled...
        journal.record_update(_update(status="Submitted", filled=Decimal("0")))
        row = journal.reconciliation_rows()[0]
        assert row.terminal is False
        assert row.reconciled is False

        # ...and a terminal order whose fills disagree with its executions is
        # not reconciled either, even though the status alone would say so.
        journal.record_execution(_execution())
        journal.record_update(_update(status="Filled", filled=Decimal("1")))
        assert journal.reconciliation_rows()[0].reconciled is True

        # Drop the execution row: status still terminal, quantities no longer
        # match, so reconciliation must fail.
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute("DELETE FROM paper_execution")

        row = journal.reconciliation_rows()[0]
        assert row.terminal is True
        assert row.reconciled is False
        assert journal.reconciliation_summary().terminal_unreconciled == 1


def test_reconciliation_rows_are_scoped_to_the_requested_session() -> None:
    """Scoping silently disappearing would mix two sessions' orders together."""

    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        journal = PaperOrderJournal(path)

        first = _intent()
        journal.record_intent(first, broker_order_id=10, account_alias="DU***17")
        journal.record_update(_update())

        second = _intent(intent_id="i-2", session_id="s-2", idempotency_key="k-2")
        journal.record_intent(second, broker_order_id=11, account_alias="DU***17")
        journal.record_update(
            _update(intent_id="i-2", broker_order_id=11)
        )

        assert len(journal.reconciliation_rows()) == 2
        scoped = journal.reconciliation_rows(session_id="s-1")
        assert [row.intent_id for row in scoped] == ["i-1"]
        assert journal.reconciliation_summary(session_id="s-2").total == 1

        # Both orders settled, so neither session has anything pending; an
        # unscoped read would still see both, and a broken scope would hide the
        # non-terminal one.
        assert journal.pending_orders_for_session("s-1") == ()
        assert journal.pending_orders_for_session("s-2") == ()

        journal.record_update(_update(status="Submitted", filled=Decimal("0")))
        pending = journal.pending_orders_for_session("s-1")
        assert [intent.intent_id for intent in pending] == ["i-1"]
        assert journal.pending_orders_for_session("s-2") == ()
        assert len(journal.audit_rows()) == 2


def test_idempotency_keys_survive_a_write_and_are_not_silently_nulled() -> None:
    """The key is the duplicate-order guard; losing it risks a double submit."""

    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        journal = PaperOrderJournal(path)
        journal.record_intent(
            _intent(idempotency_key="key-alpha"),
            broker_order_id=10,
            account_alias="DU***17",
        )

        stored = journal.intent_for_idempotency_key("key-alpha")
        assert stored is not None
        assert stored.intent_id == "i-1"

        # The column is written through, not left NULL in the table.
        with closing(sqlite3.connect(path)) as connection:
            raw = connection.execute(
                "SELECT idempotency_key FROM paper_order_intent"
            ).fetchone()[0]
        assert raw == "key-alpha"

        assert journal.intent_for_idempotency_key("key-absent") is None


def test_new_journal_still_writes_and_reads_a_full_round_trip() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "orders.sqlite3"
        journal = PaperOrderJournal(path)

        journal.record_intent(
            _intent(),
            broker_order_id=10,
            account_alias="DU***17",
        )
        journal.record_update(_update())
        assert journal.record_execution(_execution()) is True

        reopened = PaperOrderJournal(path)

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


def test_a_database_written_before_the_split_still_works() -> None:
    """The migration contract: old files keep opening, reading and growing.

    The database is built with the pre-split schema and pre-split rows, then
    handed to the new journal, which must read it, reconcile it and accept
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

        journal = PaperOrderJournal(path)

        intent = journal.intent_for_broker_order(77)
        assert intent is not None
        assert intent.intent_id == "legacy-1"
        assert intent.symbol == "MSFT"
        assert intent.quantity == 3
        assert intent.limit_price == Decimal("410.25")

        assert journal.intent_for_idempotency_key("legacy-key") is not None
        assert journal.max_broker_order_id() == 77
        assert journal.executed_quantity("legacy-1") == Decimal("3")

        rows = journal.reconciliation_rows()
        assert len(rows) == 1
        assert rows[0].latest_status == "Filled"
        assert rows[0].terminal is True
        assert rows[0].reconciled is True
        assert rows[0].intended_quantity == Decimal("3")

        summary = journal.reconciliation_summary()
        assert summary.total == 1
        assert summary.reconciled == 1

        audit = journal.audit_rows()
        assert len(audit) == 1
        assert audit[0]["intent_id"] == "legacy-1"

        # ...and the old file keeps accepting new orders.
        journal.record_intent(
            _intent(),
            broker_order_id=78,
            account_alias="DU***17",
        )
        journal.record_update(_update())

        assert journal.max_broker_order_id() == 78
        assert len(journal.reconciliation_rows()) == 2
        assert journal.reconciliation_summary().total == 2


def test_legacy_schema_matches_what_the_journal_creates_today() -> None:
    """Guards the test above: if the DDL drifted, it would prove nothing."""

    with TemporaryDirectory() as directory:
        fresh = Path(directory) / "fresh.sqlite3"
        PaperOrderJournal(fresh)

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


def _normalise(sql: str) -> str:
    return " ".join(sql.split()).replace("( ", "(").replace(" )", ")")


def test_terminal_statuses_are_shared_with_the_adapter() -> None:
    assert journal_module.TERMINAL_ORDER_STATUSES is TERMINAL_ORDER_STATUSES


def _intent(
    *,
    intent_id: str = "i-1",
    session_id: str = "s-1",
    idempotency_key: str = "k-1",
):
    from us_quant.paper_order_models import PaperOrderIntent

    return PaperOrderIntent(
        intent_id=intent_id,
        session_id=session_id,
        strategy_version_id="v-1",
        symbol="AAPL",
        side="BUY",
        quantity=1,
        limit_price=Decimal("200"),
        reason="test",
        generated_at="2026-07-26T12:00:00+00:00",
        idempotency_key=idempotency_key,
    )


def _update(
    *,
    intent_id: str = "i-1",
    broker_order_id: int = 10,
    status: str = "Filled",
    filled: Decimal | None = None,
) -> PaperOrderUpdate:
    return PaperOrderUpdate(
        intent_id=intent_id,
        broker_order_id=broker_order_id,
        status=status,
        filled=filled if filled is not None else Decimal("1"),
        remaining=Decimal("0"),
        average_fill_price=Decimal("200"),
        last_fill_price=Decimal("200"),
        message="done",
        observed_at="2026-07-26T12:00:05+00:00",
    )


def _execution() -> PaperExecution:
    return PaperExecution(
        intent_id="i-1",
        broker_order_id=10,
        execution_id="e-1",
        symbol="AAPL",
        side="BUY",
        quantity=Decimal("1"),
        price=Decimal("200"),
        occurred_at="2026-07-26T12:00:04+00:00",
    )
