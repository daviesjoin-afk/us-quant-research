from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Any

from us_quant.shadow_paper import ShadowConfig
from us_quant.strategy_schema import validate_strategy_parameters


def build_targeted_shadow_config(
    parameters: Mapping[str, Any],
    *,
    initial_cash: Decimal,
    capital_source: str,
    daily_loss_limit: Decimal,
    symbol_risk_multipliers: Mapping[str, Decimal] | None = None,
) -> ShadowConfig:
    """Create the runtime engine config from an immutable strategy version."""

    values = validate_strategy_parameters(
        "intraday-targeted-t", parameters
    )
    return ShadowConfig(
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
