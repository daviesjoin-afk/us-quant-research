"""Strategy domain: identity, intent, and the proposal hand-off.

This is the piece the old domain never had.  A strategy's output is a
``TradeProposal`` -- an *intent to trade* -- and never an ``OrderIntent``.

That separation is the whole point of the type.  ``OrderIntent`` carries an
``order_id`` and a ``client_order_id``, which means it can be submitted.  A
proposal has neither, so a strategy physically cannot reach the broker: the
risk engine must approve it and the execution service must turn it into an
``OrderIntent``.  Keeping the two distinct is what lets a later change delete
AutoQuant's ``order_sink`` callback without redesigning the strategy surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class TradeAction(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass(frozen=True, slots=True)
class StrategyIdentity:
    strategy_id: str
    version_id: str
    parameter_hash: str


@dataclass(frozen=True, slots=True)
class TradeProposal:
    strategy: StrategyIdentity
    symbol: str
    action: TradeAction
    desired_quantity: int
    reference_price: Decimal
    reason: str
    generated_at: datetime
