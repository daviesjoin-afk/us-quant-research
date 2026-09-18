"""Trading session lifecycle and the unified UI state model.

``TradingSessionPhase`` intentionally mirrors the Paper lifecycle that
already exists.  The values are the same strings so that a future runtime can
publish one phase vocabulary across Paper and any other environment.

``PaperWorkflowPhase`` is *not* removed by this change.  The real Paper
runtime has not moved yet, so deleting its phase enum would mean either
rewriting live trading code in an architecture change or leaving the Paper
path with no vocabulary at all.  The two coexist until the runtime migration
lands, at which point Paper's enum retires into this one.

``TradingSnapshot`` is the intended single state model for Desktop UI v2: one
immutable value describing everything the UI renders.  It holds domain types
and nothing else -- no ``QWidget``, no ``QLabel``, no IBKR app, no
``PaperTradingService`` and no ``AutoQuantEngine``.  Keeping service objects
out of it is what stops the UI from reaching past the runtime into whatever
happens to be wired up.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from us_quant.trading.domain.account import (
    AccountSnapshot,
    BrokerConnectionState,
    Position,
)
from us_quant.trading.domain.market import MarketDataHealth
from us_quant.trading.domain.risk import RiskDecision
from us_quant.trading.domain.strategy import StrategyIdentity


class TradingSessionPhase(StrEnum):
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    READY = "READY"
    CONNECTING = "CONNECTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    HALTED = "HALTED"
    RECONCILING = "RECONCILING"
    RECONCILING_READY = "RECONCILING_READY"
    FINALIZED = "FINALIZED"


@dataclass(frozen=True, slots=True)
class TradingSnapshot:
    phase: TradingSessionPhase

    market_health: MarketDataHealth
    broker: BrokerConnectionState

    account: AccountSnapshot | None
    positions: tuple[Position, ...]

    risk: RiskDecision | None

    active_strategy: StrategyIdentity | None

    open_order_count: int
    status_message: str
