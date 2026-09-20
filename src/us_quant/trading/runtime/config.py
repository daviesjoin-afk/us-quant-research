"""The strategy/session parameters one trading session runs with.

This is the production session contract, and it is deliberately narrower than
the simulator's.  A rotation session needs to know how much capital it may use,
how wide its stops are and when it must be flat; it does **not** need to know
how much of the account to risk per symbol, because that is a risk-policy
question and the answer arrives as a ``RiskApplication``.

That split is why this type exists at all.  The runtime used to take the
simulator's config -- ``ShadowConfig`` -- which meant the *production* runtime
imported the *internal shadow* module, and it meant a session could carry
``layered_risk_limits`` and ``symbol_risk_multipliers`` that no production code
read.  The dependency was inverted: the real runtime depended on a research
simulator, and the config it was handed described policy it must not own.

So: the fields below are the session's own parameters, they keep the meaning
they had, and policy is not among them.  ``ShadowSimulationConfig`` extends this
type with its overlay fields; nothing extends it in the other direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class TradingSessionConfig:
    """The strategy and session parameters for one trading session.

    Frozen because a running session's parameters are the ones its orders were
    sized against: mutating them mid-session would make every already-issued
    order unexplainable.  The field set is exactly what the runtime reads; there
    is no account risk here, by construction (see the module docstring).
    """

    initial_cash: Decimal
    capital_source: str
    max_position_fraction: Decimal = Decimal("0.10")
    min_order_notional: Decimal = Decimal("50")
    commission_per_order: Decimal = Decimal("0.35")
    slippage_bps: Decimal = Decimal("2")
    maximum_spread_fraction: Decimal = Decimal("0.002")
    momentum_lookback_minutes: int = 5
    warmup_minutes: int = 10
    minimum_momentum: Decimal = Decimal("0.0035")
    maximum_momentum: Decimal = Decimal("0.025")
    minimum_positive_steps: int = 0
    maximum_one_minute_move: Decimal = Decimal("1")
    profit_target: Decimal = Decimal("0.012")
    stop_loss: Decimal = Decimal("0.007")
    trailing_stop: Decimal = Decimal("0.006")
    maximum_hold_minutes: int = 45
    maximum_trades_per_day: int = 4
    entry_order_timeout_seconds: int = 90
    daily_loss_limit: Decimal = Decimal("15")
    entry_start: time = time(10, 0)
    last_entry: time = time(15, 30)
    force_flat: time = time(15, 45)
    max_open_symbols: int = 1


__all__ = ["TradingSessionConfig"]
