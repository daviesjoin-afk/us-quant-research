"""Risk composition root.

Risk is the one chain with no storage and no provider, so this builder has
nothing to choose between: it normalises the limits into their layered form
and hands back one application service.  It exists anyway, and it is the only
place the window calls, because "which limits is the runtime actually
enforcing?" must have exactly one answer at the point of assembly.

That question is not rhetorical.  The Desktop used to put the account limits
into ``ShadowConfig.layered_risk_limits`` and construct ``AutoQuantEngine``
without ever passing ``layered_risk_limits`` to it, so the configured
``risk_limits`` reached a field nobody read while the engine's own copy stayed
``None``.  Tests passed because they injected the argument directly.  Callers
now build a ``RiskApplication`` here and inject it, and
``test_desktop_risk_wiring`` asserts the window's configured limits are the
ones the running engine holds.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping

from us_quant.trading.application.risk import RiskApplication
from us_quant.trading.domain.risk import LayeredRiskLimits, RiskLimits


def build_risk_application(
    limits: LayeredRiskLimits | RiskLimits,
    *,
    exposure_multipliers: Mapping[str, Decimal] | None = None,
) -> RiskApplication:
    """Assemble the single pre-trade risk authority."""

    return RiskApplication(
        limits, exposure_multipliers=exposure_multipliers
    )


__all__ = ["build_risk_application"]
