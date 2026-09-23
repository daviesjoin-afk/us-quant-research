"""The targeted session's two application boundaries, without Qt.

Exactly two things, and both are boundaries rather than algorithms:

* the **minute-evidence store**.  The desk's local minute rows are read by a
  summary query; this service is where the store is reached, so the orchestration
  above it never opens SQLite and a test can swap the store without a window;
* the **target preflight evaluator**.  ``evaluate_target_preflight`` is a domain
  function, and this service is the desktop's one call site for it.

Why so narrow: everything else the targeted session does -- the target status, the
minute line, the refusal rules, the ordering, the render -- is *rule and timing*,
which belongs to the capability that owns the truth, not to a service.  A service
that grew a ``format_status`` or a ``refresh_all`` would be a second place where
the session's behaviour lives.

``broker_orders_available`` is passed as the literal ``False`` and is not a
parameter.  Research Targeted asks whether an internal simulation could start; it
has no route to a broker order, and a caller that could pass ``True`` would be
able to claim broker execution authority through a research workflow.  A future
live path needs an independent risk kernel, not a flag on this call.

The preflight is delegated to, not reimplemented: the format gate, the Universe
identity and STK/ETF checks, ``eligible_for_research``, the strategy status rules,
the fresh realtime quote, the Paper account truth with its 300-second freshness
window, whole-share sizing with commission and slippage, the exposure multiplier,
the minute-evidence gate, the broker-route gate and the decision strings are all
the domain function's and are frozen this round.  In particular the decimal
arithmetic stays decimal: ``net_liquidation`` and ``exposure_multiplier`` are
passed through as ``Decimal`` so the evaluator's own whole-share math is unchanged.

Deliberately not here: Qt, the page, ``MainWindow``, any orchestrator, the market
worker, the Shadow engine, the strategy selection and the runtime event store.
"""

from __future__ import annotations

from decimal import Decimal

from us_quant.minute_data import MinuteDataSummary, MinuteQuoteStore
from us_quant.targeted_preflight import (
    TargetPreflightResult,
    evaluate_target_preflight,
)
from us_quant.trading.domain.account import BrokerAccountSnapshot
from us_quant.trading.domain.market import MarketQuote
from us_quant.trading.domain.strategy import StrategyVersion
from us_quant.universe import UniverseRecord


class DesktopTargetedSessionService:
    """Reads the minute-evidence summary and runs the target preflight.

    The constructor performs no I/O and holds no state beyond the store handle:
    the summary must be read when it is asked for, because the operator records
    market data between the click and the evaluation, and a captured summary would
    report evidence that no longer matches the store.
    """

    def __init__(
        self,
        *,
        minute_quote_store: MinuteQuoteStore,
    ) -> None:
        self._store = minute_quote_store

    def minute_summary(self, symbol: str) -> MinuteDataSummary:
        """The local minute-evidence summary for ``symbol``.

        The store's own normalization and empty-summary semantics are used
        unchanged: a symbol with no rows returns a summary of zeros rather than
        raising, because "no evidence yet" is a normal state the panel renders.
        """

        return self._store.summary(symbol)

    def evaluate_preflight(
        self,
        symbol: str,
        *,
        universe_record: UniverseRecord | None,
        quote: MarketQuote | None,
        account: BrokerAccountSnapshot | None,
        strategy: StrategyVersion | None,
        exposure_multiplier: Decimal = Decimal("1"),
    ) -> TargetPreflightResult:
        """Evaluate the target preflight from finished external facts.

        Every argument is a *fact the caller already read*, which is what makes
        the timing explicit: this service decides nothing about which universe,
        which quote or which account is current.  ``broker_orders_available`` is
        fixed at ``False`` here -- see the module docstring.
        """

        return evaluate_target_preflight(
            symbol,
            universe_record=universe_record,
            quote=quote,
            account=account,
            minute_summary=self._store.summary(symbol),
            strategy=strategy,
            exposure_multiplier=exposure_multiplier,
            broker_orders_available=False,
        )


__all__ = ["DesktopTargetedSessionService"]
