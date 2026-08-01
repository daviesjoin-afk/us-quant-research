from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from us_quant.shadow_paper import ShadowConfig
from us_quant.strategy_schema import validate_strategy_parameters


def resolve_paper_session_capital(
    *,
    net_liquidation: Decimal,
    cash: Decimal,
    requested_limit: Decimal,
) -> Decimal:
    if net_liquidation <= 0:
        raise ValueError("IBKR Paper 净值必须为正")
    if cash <= 0:
        raise ValueError(
            "IBKR Paper 现金必须为正；自动量化禁止借款"
        )
    if requested_limit < 0:
        raise ValueError("会话资金上限不能为负")
    cash_capital = min(net_liquidation, cash)
    if requested_limit == 0:
        return cash_capital
    return min(cash_capital, requested_limit)


def build_auto_rotation_config(
    parameters: Mapping[str, Any],
    *,
    initial_cash: Decimal,
    capital_source: str,
    daily_loss_limit: Decimal,
    symbol_risk_multipliers: Mapping[str, Decimal] | None = None,
) -> ShadowConfig:
    values = validate_strategy_parameters(
        "intraday-auto-rotation", parameters
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
        minimum_positive_steps=int(
            values.get("minimum_positive_steps", 0)
        ),
        maximum_one_minute_move=Decimal(
            str(values.get("maximum_one_minute_move", "1"))
        ),
        profit_target=_decimal(values, "profit_target"),
        stop_loss=_decimal(values, "stop_loss"),
        trailing_stop=_decimal(values, "trailing_stop"),
        maximum_hold_minutes=int(values["maximum_hold_minutes"]),
        maximum_trades_per_day=int(
            values["maximum_trades_per_day"]
        ),
        entry_order_timeout_seconds=int(
            values.get("entry_order_timeout_seconds", 90)
        ),
        daily_loss_limit=daily_loss_limit,
    )


def _decimal(values: Mapping[str, Any], name: str) -> Decimal:
    return Decimal(str(values[name]))
