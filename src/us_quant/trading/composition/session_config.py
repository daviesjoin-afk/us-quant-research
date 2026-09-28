"""Builders that turn a strategy version into one session's parameters.

Two responsibilities, both about *describing* a session rather than running one:

* ``resolve_paper_session_capital`` decides how much of the Paper account one
  session may use.  It is deliberately conservative -- cash-bounded, never
  margin -- and it refuses rather than clamps, because a session that silently
  runs with less capital than the operator asked for is worse than one that
  does not start.
* ``build_auto_rotation_config`` is re-exported from the runtime layer for
  existing composition callers.

The capital resolver moved here from the deleted ``auto_intraday`` root module.
The runtime config builder lives in the runtime layer so execution runtime code
does not depend on composition.
"""

from __future__ import annotations

from decimal import Decimal
from us_quant.trading.runtime.session_config import build_auto_rotation_config


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


__all__ = [
    "build_auto_rotation_config",
    "resolve_paper_session_capital",
]
