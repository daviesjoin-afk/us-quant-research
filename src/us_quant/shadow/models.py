"""The immutable facts the shadow simulator records.

Four shapes, all frozen, all plain data: what a simulated holding looks like, one
simulated fill, the provenance of a session, and the snapshot a caller renders.

They are separated from the engine and the store for the reason the trading
runtime separates its models too: the engine owns mutable state and the store
owns persistence, and neither needs the other's types to define its own.  This
module therefore imports nothing but the standard library -- no SQLite, no
engine, no store, no Qt, no broker, and no trading application or adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ShadowPosition:
    """One simulated holding, with the mark it has already reached."""

    symbol: str
    quantity: int
    entry_price: Decimal
    opened_at: str
    high_water: Decimal
    provider: str
    coverage: str


@dataclass(frozen=True, slots=True)
class ShadowFill:
    """One simulated execution; ``realized_pnl`` is set on the closing side."""

    session_id: str
    occurred_at: str
    symbol: str
    side: str
    quantity: int
    price: Decimal
    commission: Decimal
    reason: str
    provider: str
    coverage: str
    realized_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class ShadowSessionProvenance:
    """Which strategy version produced a session, and over what universe."""

    session_id: str
    strategy_version_id: str
    parameter_hash: str
    target_symbol: str
    allowed_symbols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ShadowSnapshot:
    """One reading of a simulated session, as a caller or an export sees it."""

    session_id: str | None
    strategy_version_id: str
    parameter_hash: str
    target_symbol: str
    active: bool
    initial_cash: Decimal
    capital_source: str
    cash: Decimal
    equity: Decimal
    realized_pnl: Decimal
    daily_realized_pnl: Decimal
    unrealized_pnl: Decimal
    positions: tuple[ShadowPosition, ...]
    fills: tuple[ShadowFill, ...]
    trades_today: int
    trading_day: str | None
    status: str
    observed_at: str


__all__ = [
    "ShadowFill",
    "ShadowPosition",
    "ShadowSessionProvenance",
    "ShadowSnapshot",
]
