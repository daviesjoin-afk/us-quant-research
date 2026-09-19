"""Pure data models for the Paper order channel.

The DTO layer only: value objects shared by the order store and the broker
adapter. Nothing here talks to a broker, a database, a GUI or a network, so it
can be imported from anywhere.

What remains here is what the *session* still needs: the connection fact, the
broker account state, and the reconciliation views the window displays.  The
per-order DTOs -- the intent, the status update and the execution -- are gone:
orders are domain ``OrderIntent`` / ``OrderEvent`` / ``ExecutionFill`` now, and
a second set of order shapes is how two readings of one order start to differ.

``TERMINAL_ORDER_STATUSES`` lives here because both the store and the adapter
need the same status vocabulary and neither may import the other; two copies
would be free to drift apart.  It is the vocabulary of the *stored text*, which
is why the broker spellings ("cancelled") appear here while the domain status
uses its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PaperOrderConnection:
    connected: bool
    account_alias: str
    server_version: int
    connection_time: str
    next_order_id: int
    open_broker_orders: int = 0
    unreconciled_local_orders: int = 0
    connection_generation: int = 0
    snapshot_complete: bool = False
    observed_at: str = ""


@dataclass(frozen=True, slots=True)
class PaperBrokerPosition:
    symbol: str
    quantity: Decimal
    average_cost: Decimal


@dataclass(frozen=True, slots=True)
class PaperBrokerState:
    account_alias: str
    net_liquidation: Decimal | None
    cash: Decimal | None
    available_funds: Decimal | None
    buying_power: Decimal | None
    daily_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None
    positions: tuple[PaperBrokerPosition, ...]
    observed_at: str


@dataclass(frozen=True, slots=True)
class PaperOrderReconciliation:
    intent_id: str
    session_id: str
    broker_order_id: int
    symbol: str
    side: str
    intended_quantity: Decimal
    latest_status: str | None
    reported_filled: Decimal
    reported_remaining: Decimal
    executed_quantity: Decimal
    reconciled: bool
    terminal: bool
    reason: str
    observed_at: str


@dataclass(frozen=True, slots=True)
class ReconciliationSummary:
    session_id: str
    total: int
    reconciled: int
    unreconciled: int
    terminal: int
    terminal_unreconciled: int
    nonterminal: int
    observed_at: str


@dataclass(frozen=True, slots=True)
class PaperBrokerOrder:
    broker_order_id: int
    symbol: str
    side: str
    quantity: Decimal
    status: str


@dataclass(frozen=True, slots=True)
class PaperReconciliationSnapshot:
    account_fingerprint: str
    connection_generation: int
    reconciliation_generation: int
    captured_at: str
    broker_positions: tuple[PaperBrokerPosition, ...]
    open_broker_orders: tuple[PaperBrokerOrder, ...]
    completed_broker_orders: tuple[PaperBrokerOrder, ...]
    reconciliation_summary: ReconciliationSummary
    state_version: int
    digest: str
    snapshot_complete: bool = True


TERMINAL_ORDER_STATUSES = frozenset(
    {"filled", "cancelled", "apicancelled", "inactive", "error"}
)
