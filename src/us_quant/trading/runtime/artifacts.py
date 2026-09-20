"""The artifacts the window renders and the research runs record.

``AutoQuantSnapshot`` and its rows are the vocabulary of the presentation layer
and of the exported bundles, moved here unchanged from ``auto_quant.py`` so a
migration did not rewrite the UI.  The names stay because renaming them would
change the artifact format without changing a single decision.

They are kept apart from ``models.py`` for one structural reason: a snapshot
carries ``OrderIntent`` -- the orders a session is holding -- and the strategy
runtime must not be able to reach order identity even by importing a module
that mentions it.  A strategy imports ``models``; this file is the session's.

No module here imports an adapter, a risk service, an execution service or a
store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Protocol, Sequence

from us_quant.trading.domain.orders import OrderIntent
from us_quant.trading.domain.strategy import StrategyIdentity
from us_quant.trading.runtime.config import TradingSessionConfig


@dataclass(frozen=True, slots=True)
class AutoQuantPosition:
    symbol: str
    quantity: int
    average_price: Decimal
    opened_at: str
    high_water: Decimal
    provider: str


@dataclass(frozen=True, slots=True)
class AutoQuantFill:
    execution_id: str
    intent_id: str
    occurred_at: str
    symbol: str
    side: str
    quantity: int
    price: Decimal
    estimated_commission: Decimal
    realized_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class AutoQuantSnapshot:
    """What the session looks like right now, to a window or an artifact."""

    session_id: str | None
    active: bool
    strategy_version_id: str
    parameter_hash: str
    candidate_count: int
    initial_equity: Decimal
    estimated_cash: Decimal
    estimated_equity: Decimal
    estimated_realized_pnl: Decimal
    estimated_unrealized_pnl: Decimal
    positions: tuple[AutoQuantPosition, ...]
    fills: tuple[AutoQuantFill, ...]
    intents: tuple[OrderIntent, ...]
    pending_orders: tuple[OrderIntent, ...]
    trades_today: int
    trading_day: str | None
    status: str
    observed_at: str
    entries_paused: bool = False
    stop_requested: bool = False
    broker_truth_required: bool = True


class BookView(Protocol):
    """The book facts a snapshot needs, and nothing else.

    Declared as a protocol so the snapshot can be built here without this
    module importing the store that produces the values -- the ledger knows
    about these types, not the other way round.
    """

    cash: Decimal
    realized_pnl: Decimal
    positions: Mapping[str, AutoQuantPosition]
    fills: Sequence[AutoQuantFill]

    def equity(self) -> Decimal: ...
    def unrealized_pnl(self) -> Decimal: ...
    def all_intents(self) -> tuple[OrderIntent, ...]: ...
    def pending_intents(self) -> tuple[OrderIntent, ...]: ...


def build_snapshot(
    *,
    session,
    book: BookView,
    config: TradingSessionConfig,
    identity: StrategyIdentity,
    candidate_count: int,
    observed_at: datetime,
) -> AutoQuantSnapshot:
    """Assemble the one artifact the window and the research runs read.

    Kept beside the type it builds rather than inside the runtime: the snapshot
    is a projection of session and book facts, and projecting them here is what
    lets the runtime stay focused on ordering them.
    """

    return AutoQuantSnapshot(
        session_id=session.session_id,
        active=session.active,
        # Projected from the bound identity rather than stored a second time:
        # the artifact and UI field names are unchanged, but there is one
        # source for them.
        strategy_version_id=identity.version_id,
        parameter_hash=identity.parameter_hash,
        candidate_count=candidate_count,
        initial_equity=config.initial_cash,
        estimated_cash=book.cash,
        estimated_equity=book.equity(),
        estimated_realized_pnl=book.realized_pnl,
        estimated_unrealized_pnl=book.unrealized_pnl(),
        positions=tuple(book.positions.values()),
        fills=tuple(book.fills),
        intents=book.all_intents(),
        pending_orders=book.pending_intents(),
        trades_today=session.trades_today,
        trading_day=(
            session.trading_day.isoformat()
            if session.trading_day is not None
            else None
        ),
        status=session.status,
        observed_at=observed_at.isoformat(),
        entries_paused=session.entries_paused,
        stop_requested=session.stop_requested,
    )