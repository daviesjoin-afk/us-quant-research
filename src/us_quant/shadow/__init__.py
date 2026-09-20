"""The internal shadow simulation subsystem.

Shadow is the *research-only* simulator: it runs the strategy against a simulated
book so a candidate set can be evaluated without touching a broker.  It is not a
trading path, it holds no broker port, and no module in the trading core may
import it -- the production runtime takes ``TradingSessionConfig`` and never
learns that a simulator exists.

Five modules, one responsibility each:

``config.py``
    ``ShadowSimulationConfig`` -- the session config plus the simulator's risk
    overlay, and the builder the Desktop and the replay tool share.
``models.py``
    the immutable facts: a position, a fill, a session's provenance and a
    snapshot.  Standard library only.
``store.py``
    ``ShadowPaperStore`` -- the only thing here that touches SQLite.
``trade_logic.py``
    ``ShadowTradeLogic`` -- entry and exit behaviour, holding no state.
``engine.py``
    ``ShadowPaperEngine`` -- the lifecycle, the stream sequencing, and the one
    copy of every mutable fact.

This is a thin re-export surface, not a facade: importing a specific submodule is
preferred, and nothing here reaches for the engine or the store on a caller's
behalf.
"""

from __future__ import annotations

from us_quant.shadow.config import (
    ShadowSimulationConfig,
    build_targeted_shadow_config,
)
from us_quant.shadow.models import (
    ShadowFill,
    ShadowPosition,
    ShadowSessionProvenance,
    ShadowSnapshot,
)

__all__ = [
    "ShadowFill",
    "ShadowPosition",
    "ShadowSessionProvenance",
    "ShadowSimulationConfig",
    "ShadowSnapshot",
    "build_targeted_shadow_config",
]
