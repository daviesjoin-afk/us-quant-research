"""Builders that turn a strategy version into one session's parameters.

Two responsibilities, both about *describing* a session rather than running one:

* ``resolve_paper_session_capital`` decides how much of the Paper account one
  session may use.  It is deliberately conservative -- cash-bounded, never
  margin -- and it refuses rather than clamps, because a session that silently
  runs with less capital than the operator asked for is worse than one that
  does not start.
* ``build_auto_rotation_config`` reads the strategy version's validated
  parameters and produces the session config.

Both moved here from the deleted ``auto_intraday`` root module.  The builder now
returns ``TradingSessionConfig`` rather than the simulator's ``ShadowConfig``:
the parameters it sets were always exactly the session's own, and the two extra
fields the simulator type carried were risk policy that the production path
never read.  Behaviour is otherwise identical.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from us_quant.trading.domain.strategy_parameters import (
    validate_strategy_parameters,
)
from us_quant.trading.runtime.config import TradingSessionConfig


def resolve_paper_session_capital(
    *,
    net_liquidation: Decimal,
    cash: Decimal,
    requested_limit: Decimal,
) -> Decimal:
    """The capital one Paper session may use, bounded by cash, never margin."""

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
) -> TradingSessionConfig:
    """Build the strategy/session parameters for one rotation session.

    It deliberately carries no account risk.  Those limits used to be smuggled
    in here -- as ``symbol_risk_multipliers`` and as a whole
    ``LayeredRiskLimits`` stuffed into the config -- so the runtime had to go and
    find them again, and the Desktop's real ``risk_limits`` reached the engine
    only by luck.  Risk policy is now passed to the engine as a
    ``RiskApplication`` instead, and this builder describes only what the
    strategy wants.
    """

    values = validate_strategy_parameters(
        "intraday-auto-rotation", parameters
    )
    return TradingSessionConfig(
        initial_cash=initial_cash,
        capital_source=capital_source,
        max_position_fraction=_decimal(
            values, "max_position_fraction"
        ),
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


__all__ = [
    "build_auto_rotation_config",
    "resolve_paper_session_capital",
]
