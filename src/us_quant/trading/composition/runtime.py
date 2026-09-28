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

from threading import RLock
from weakref import WeakValueDictionary

from us_quant.trading.application.execution import ExecutionApplication
from us_quant.trading.application.risk import RiskApplication
from us_quant.trading.domain.strategy import StrategyIdentity
from us_quant.trading.runtime.config import TradingSessionConfig
from us_quant.trading.runtime.models import AutoQuantCandidate
from us_quant.trading.runtime.strategy import StrategyRuntime
from us_quant.trading.runtime.trading import TradingRuntime
from us_quant.trading.application.portfolio_runtime import PortfolioRuntime


class PortfolioRuntimeRegistry:
    """Composition-owned single runtime slot per masked trading account."""

    def __init__(self) -> None:
        self._runtimes: WeakValueDictionary[str, PortfolioRuntime] = WeakValueDictionary()
        self._lock = RLock()

    def register(self, *, account_alias: str, runtime: PortfolioRuntime) -> PortfolioRuntime:
        if (
            not isinstance(account_alias, str)
            or not account_alias.strip()
            or "*" not in account_alias
        ):
            raise ValueError("a masked account alias is required")
        if not isinstance(runtime, PortfolioRuntime):
            raise TypeError("runtime must be PortfolioRuntime")
        key = account_alias.strip()
        with self._lock:
            current = self._runtimes.get(key)
            if current is not None and current is not runtime:
                raise ValueError("this account already has an active PortfolioRuntime")
            self._runtimes[key] = runtime
            return runtime

    def runtime_for(self, account_alias: str) -> PortfolioRuntime | None:
        with self._lock:
            return self._runtimes.get(account_alias.strip())


def build_strategy_runtime(
    *,
    config: TradingSessionConfig,
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
    config: TradingSessionConfig,
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


def build_portfolio_runtime(
    *,
    strategies,
    proposals,
    snapshots,
    repository,
    risk_path,
) -> PortfolioRuntime:
    """Compose the sole allocator authority for one Paper account."""

    return PortfolioRuntime(
        strategies=strategies,
        proposals=proposals,
        snapshots=snapshots,
        repository=repository,
        risk_path=risk_path,
    )


__all__ = [
    "PortfolioRuntimeRegistry",
    "build_strategy_runtime",
    "build_trading_runtime",
    "build_portfolio_runtime",
]
