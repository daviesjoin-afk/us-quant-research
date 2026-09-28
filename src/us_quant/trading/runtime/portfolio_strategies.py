"""Signal-only workers for the governed Paper portfolio runtime."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from us_quant.trading.domain.portfolio import PortfolioSnapshot
from us_quant.trading.domain.strategy import StrategyVersion
from us_quant.trading.runtime.models import AutoQuantCandidate, StrategyPositionView, StrategySessionPolicy
from us_quant.trading.runtime.session_config import build_auto_rotation_config
from us_quant.trading.runtime.strategy import StrategyRuntime


class PortfolioStrategyWorkers:
    """Run one proposal-only StrategyRuntime per explicitly selected version."""

    def __init__(
        self,
        *,
        strategies: tuple[StrategyVersion, ...],
        candidates: tuple[AutoQuantCandidate, ...],
        policy,
    ) -> None:
        if not strategies or not candidates:
            raise ValueError("portfolio strategy workers need selected versions and candidates")
        if any(item.strategy_id != "intraday-auto-rotation" for item in strategies):
            raise ValueError("no production Paper signal worker is registered for a selected strategy family")
        self._workers: dict[str, StrategyRuntime] = {}
        self._candidate_symbols = frozenset(item.symbol.strip().upper() for item in candidates)
        self._entries_enabled = True
        self._flatten = False
        self._opened_at: dict[tuple[str, str], datetime] = {}
        self._high_water: dict[tuple[str, str], Decimal] = {}
        self._ready_quotes = {}
        for version in sorted(strategies, key=lambda item: item.version_id):
            allocation = policy.allocation_for(version.version_id)
            if allocation is None or not allocation.enabled:
                raise ValueError("selected strategy has no enabled frozen allocation")
            parameters = dict(version.parameters)
            if "market_reference_symbols" in parameters:
                parameters["market_reference_symbols"] = list(
                    parameters["market_reference_symbols"]
                )
            config = build_auto_rotation_config(
                parameters,
                initial_cash=allocation.max_capital,
                capital_source=f"portfolio allocation {version.version_id}",
                daily_loss_limit=allocation.max_capital * Decimal("0.01"),
            )
            worker = StrategyRuntime(
                candidates=candidates,
                config=config,
                strategy=version.identity,
                market_reference_symbols=tuple(
                    str(item).strip().upper()
                    for item in parameters.get("market_reference_symbols", ())
                ),
            )
            worker.start()
            self._workers[version.version_id] = worker

    def observe(self, market_snapshot, *, observed_at: datetime, entries_enabled: bool, flatten: bool) -> None:
        self._entries_enabled = entries_enabled
        self._flatten = flatten
        for version_id, worker in self._workers.items():
            ready, references = worker.ready_quotes(market_snapshot)
            self._ready_quotes[version_id] = (ready, references)
            worker.observe(now=observed_at, ready=ready, reference_ready=references)
            for symbol, quote in ready.items():
                assert quote.bid is not None and quote.ask is not None
                mark = (quote.bid + quote.ask) / Decimal("2")
                for key in self._high_water:
                    if key[1] == symbol:
                        self._high_water[key] = max(self._high_water[key], mark)

    def proposals_for(
        self,
        strategy: StrategyVersion,
        *,
        observed_at: datetime,
        proposal_cutoff: datetime,
        portfolio_snapshot: PortfolioSnapshot,
    ):
        worker = self._workers.get(strategy.version_id)
        if worker is None or worker.identity != strategy.identity:
            raise ValueError("portfolio strategy worker does not match the frozen version identity")
        # StrategyRuntime retains its minute history; the current quotes are
        # supplied by observe() through this worker's current market snapshot.
        ready, references = self._ready_quotes.get(strategy.version_id, ({}, {}))
        positions = self._positions_for(strategy.version_id, portfolio_snapshot, observed_at)
        bids = {symbol: quote.bid for symbol, quote in ready.items() if quote.bid is not None}
        marks = {
            symbol: (quote.bid + quote.ask) / Decimal("2")
            for symbol, quote in ready.items()
            if quote.bid is not None and quote.ask is not None
        }
        pending_sells = frozenset(
            item.symbol
            for item in portfolio_snapshot.open_orders
            if item.side.value == "sell"
        )
        exits = worker.exit_evaluation(
            now=observed_at,
            positions=positions,
            bids=bids,
            marks=marks,
            reason="portfolio Paper stop requested" if self._flatten else None,
            skip=pending_sells,
        )
        proposals = list(exits.proposals)
        if self._entries_enabled and not self._flatten:
            evaluation = worker.entry_evaluation(
                now=observed_at,
                ready=ready,
                reference_ready=references,
                positions=positions,
                trades_today=sum(
                    item.trades_today
                    for item in portfolio_snapshot.strategy_exposure
                    if item.strategy_version_id == strategy.version_id
                ),
                realized_pnl=sum(
                    (item.realized_pnl for item in portfolio_snapshot.strategy_exposure
                     if item.strategy_version_id == strategy.version_id),
                    Decimal("0"),
                ),
                policy=StrategySessionPolicy(
                    entry_start=worker.config.entry_start,
                    last_entry=worker.config.last_entry,
                    maximum_trades_per_day=worker.config.maximum_trades_per_day,
                    daily_loss_limit=worker.config.daily_loss_limit,
                ),
            )
            proposals.extend(evaluation.proposals)
        return tuple(proposals)

    def _positions_for(self, strategy_version_id, snapshot, now):
        result = {}
        quotes = self._ready_quotes.get(strategy_version_id, ({}, {}))[0]
        for item in snapshot.strategy_exposure:
            if item.strategy_version_id != strategy_version_id or item.quantity <= 0:
                continue
            key = (strategy_version_id, item.symbol)
            quote = quotes.get(item.symbol)
            mark = (
                (quote.bid + quote.ask) / Decimal("2")
                if quote is not None and quote.bid is not None and quote.ask is not None
                else item.notional / item.quantity
            )
            self._opened_at.setdefault(key, now)
            self._high_water[key] = max(self._high_water.get(key, mark), mark)
            result[item.symbol] = StrategyPositionView(
                symbol=item.symbol,
                quantity=item.quantity,
                average_price=item.average_cost or item.notional / item.quantity,
                opened_at=self._opened_at[key],
                high_water=self._high_water[key],
            )
        return result


__all__ = ["PortfolioStrategyWorkers"]
