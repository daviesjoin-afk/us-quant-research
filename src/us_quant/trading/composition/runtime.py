"""Runtime composition root: the one place the two runtimes are assembled.

The window, the session coordinator and the tests all need "a trading session
wired to the strategy, risk and execution services this window is running
with", and none of them may build that themselves: a second assembly point is
how one window ends up dispatching through a risk application it does not
render.

The builder takes the already-built authorities rather than creating them --
connecting a broker, reading a strategy version or opening a store are the
caller's steps -- and it creates nothing but the runtimes: no IBKR connection,
no SQLite handle, no Qt object and no widget.  ``TradingRuntime`` in particular
must never construct its own risk or execution service, because that is exactly
how a session comes to enforce a policy nobody configured.

The strategy runtime is built here too, from the same candidates the session
scopes itself to, so "the symbols the strategy scans" and "the symbols the risk
layer permits" cannot be assembled from two different lists.
"""

from __future__ import annotations

from us_quant.shadow_paper import ShadowConfig
from us_quant.trading.application.execution import ExecutionApplication
from us_quant.trading.application.risk import RiskApplication
from us_quant.trading.domain.strategy import StrategyIdentity
from us_quant.trading.runtime.models import AutoQuantCandidate
from us_quant.trading.runtime.strategy import StrategyRuntime
from us_quant.trading.runtime.trading import TradingRuntime


def build_strategy_runtime(
    *,
    config: ShadowConfig,
    candidates: tuple[AutoQuantCandidate, ...],
    identity: StrategyIdentity,
    market_reference_symbols: tuple[str, ...] = (),
) -> StrategyRuntime:
    """Build the signal side for one candidate set."""

    return StrategyRuntime(
        candidates=candidates,
        config=config,
        strategy=identity,
        market_reference_symbols=market_reference_symbols,
    )


def build_trading_runtime(
    *,
    config: ShadowConfig,
    candidates: tuple[AutoQuantCandidate, ...],
    identity: StrategyIdentity,
    risk: RiskApplication,
    execution: ExecutionApplication,
    market_reference_symbols: tuple[str, ...] = (),
) -> TradingRuntime:
    """Assemble one trading session over the injected authorities.

    The risk and execution services arrive already built and already bound to
    the channel this session will use; this function only decides how the
    strategy and the session that drives it are put together.
    """

    return TradingRuntime(
        config=config,
        strategy=build_strategy_runtime(
            config=config,
            candidates=candidates,
            identity=identity,
            market_reference_symbols=market_reference_symbols,
        ),
        risk=risk,
        execution=execution,
    )


__all__ = [
    "build_strategy_runtime",
    "build_trading_runtime",
]