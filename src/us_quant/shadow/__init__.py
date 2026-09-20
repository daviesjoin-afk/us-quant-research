"""The internal shadow simulation subsystem.

Shadow is the *research-only* simulator: it runs the strategy against a
simulated book so a candidate set can be evaluated without touching a broker.
It is not a trading path and holds no broker port, which is why it lives beside
the trading core rather than inside it.

This package currently owns exactly one thing -- the shadow configuration, whose
overlay fields are why it cannot simply reuse the production session config.
The engine and its store are still in ``us_quant.shadow_paper``, which is
explicitly **TRANSITIONAL**: it is scheduled to be split into
``shadow/models.py``, ``shadow/store.py`` and ``shadow/engine.py``, and only
then deleted.  That decomposition is deliberately *not* part of this round.

What this round does fix is the dependency direction: the trading core imports
``trading.runtime.config``, never ``shadow_paper``, and the shadow side imports
the trading core for the base config it extends.  One way only.
"""

from __future__ import annotations

from us_quant.shadow.config import (
    ShadowSimulationConfig,
    build_targeted_shadow_config,
)

__all__ = [
    "ShadowSimulationConfig",
    "build_targeted_shadow_config",
]
