"""The internal shadow simulator's configuration.

The shadow simulator runs the *same* strategy as the Paper session, but it does
so against a simulated book and with a per-symbol risk overlay.  Those two extra
facts are the whole difference, and they are expressed here as an extension of
the production session config rather than as a parallel type:

``TradingSessionConfig``
    the production session parameters (capital, gates, exits, session window).
``ShadowSimulationConfig``
    the above, plus the overlay the simulator applies on top -- per-symbol risk
    multipliers and an optional layered-risk envelope.

The direction matters and is one-way by construction.  A shadow config *is* a
session config, so ``ShadowPaperEngine`` can be handed either one; but the
production runtime takes ``TradingSessionConfig``, so nothing in the trading
core can reach an overlay field.  The overlay is not "risk policy the strategy
carries" -- it is the *simulator's* approximation of a risk layer, which is
exactly why it lives with the simulator.

``build_targeted_shadow_config`` is the builder the Desktop and the replay tool
both use; it moves here from the deleted ``targeted_intraday`` root module
unchanged, other than returning this type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping

from us_quant.trading.domain.risk import LayeredRiskLimits
from us_quant.trading.domain.strategy_parameters import (
    validate_strategy_parameters,
)
from us_quant.trading.runtime.config import TradingSessionConfig


@dataclass(frozen=True, slots=True)
class ShadowSimulationConfig(TradingSessionConfig):
    """The session parameters plus the simulator's risk overlay.

    Both overlay fields default to "no overlay", so a shadow config with neither
    set behaves exactly like the production session config it extends.
    """

    symbol_risk_multipliers: Mapping[str, Decimal] = field(
        default_factory=dict
    )
    layered_risk_limits: LayeredRiskLimits | None = field(
        default=None, repr=False
    )


def build_targeted_shadow_config(
    parameters: Mapping[str, Any],
    *,
    initial_cash: Decimal,
    capital_source: str,
    daily_loss_limit: Decimal,
    symbol_risk_multipliers: Mapping[str, Decimal] | None = None,
) -> ShadowSimulationConfig:
    """Create the runtime engine config from an immutable strategy version."""

    values = validate_strategy_parameters(
        "intraday-targeted-t", parameters
    )
    return ShadowSimulationConfig(
        initial_cash=initial_cash,
        capital_source=capital_source,
        max_position_fraction=_decimal(
            values, "max_position_fraction"
        ),
        symbol_risk_multipliers=symbol_risk_multipliers or {},
        min_order_notional=_decimal(values, "min_order_notional"),
        commission_per_order=_decimal(
            values, "commission_per_order"
        ),
        slippage_bps=_decimal(values, "slippage_bps"),
        maximum_spread_fraction=_decimal(
            values, "maximum_spread_fraction"
        ),
        momentum_lookback_minutes=int(
            values["momentum_lookback_minutes"]
        ),
        warmup_minutes=int(values["warmup_minutes"]),
        minimum_momentum=_decimal(values, "minimum_momentum"),
        maximum_momentum=_decimal(values, "maximum_momentum"),
        profit_target=_decimal(values, "profit_target"),
        stop_loss=_decimal(values, "stop_loss"),
        trailing_stop=_decimal(values, "trailing_stop"),
        maximum_hold_minutes=int(values["maximum_hold_minutes"]),
        maximum_trades_per_day=int(
            values["maximum_trades_per_day"]
        ),
        daily_loss_limit=daily_loss_limit,
    )


def _decimal(values: Mapping[str, Any], name: str) -> Decimal:
    return Decimal(str(values[name]))


__all__ = [
    "ShadowSimulationConfig",
    "build_targeted_shadow_config",
]
