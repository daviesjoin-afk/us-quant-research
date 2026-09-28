from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from dataclasses import replace

from us_quant.trading.domain.market import MarketSnapshot
from us_quant.trading.domain.portfolio import (
    PortfolioCapitalPolicy,
    PortfolioSnapshot,
    PortfolioStrategyAllocation,
    PortfolioStrategyExposure,
)
from us_quant.trading.domain.market import MarketDataMode, MarketQuote
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
    parameter_hash_for,
)
from us_quant.trading.runtime.models import AutoQuantCandidate
from us_quant.trading.runtime.portfolio_strategies import PortfolioStrategyWorkers


NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
PARAMETERS = {
    "momentum_lookback_minutes": 5,
    "warmup_minutes": 15,
    "maximum_hold_minutes": 35,
    "maximum_trades_per_day": 6,
    "max_position_fraction": "0.05",
    "min_order_notional": "50",
    "commission_per_order": "0.35",
    "slippage_bps": "3",
    "maximum_spread_fraction": "0.0015",
    "minimum_momentum": "0.0025",
    "maximum_momentum": "0.018",
    "minimum_positive_steps": 3,
    "maximum_one_minute_move": "0.012",
    "entry_order_timeout_seconds": 45,
    "market_reference_symbols": ["SPY", "QQQ"],
    "profit_target": "0.010",
    "stop_loss": "0.006",
    "trailing_stop": "0.0045",
    "whole_shares": True,
}


def _version(version_id: str) -> StrategyVersion:
    parameters = dict(PARAMETERS)
    parameters["market_reference_symbols"] = list(PARAMETERS["market_reference_symbols"])
    return StrategyVersion(
        definition=StrategyDefinition("intraday-auto-rotation", version_id, "test"),
        identity=StrategyIdentity(
            "intraday-auto-rotation", version_id, parameter_hash_for(parameters)
        ),
        semver="1.0.0",
        status=StrategyStatus.PAPER_SHADOW,
        mode=StrategyMode.PAPER_SHADOW,
        parameters=parameters,
        universe_hash="u",
        code_hash="c",
        risk_budget_pct=Decimal("0.01"),
        gate_passed=True,
        gate_reason="reviewed",
        created_at=NOW,
        updated_at=NOW,
    )


def _market() -> MarketSnapshot:
    return MarketSnapshot(
        generation=1,
        connected=True,
        ready=True,
        reconnect_attempt=0,
        quotes=(),
        error_code=None,
        message="",
        observed_at=NOW,
        source_id="test",
        source_label="test",
        coverage="test",
    )


def test_selected_versions_run_as_signal_only_workers_over_one_portfolio_snapshot():
    versions = (_version("strategy-a-v1"), _version("strategy-b-v1"))
    candidates = (AutoQuantCandidate("AAPL", "Apple", "Tech", 1, Decimal("1"), "test"),)
    policy = PortfolioCapitalPolicy(
        total_capital_limit=Decimal("1000"),
        max_gross_exposure=Decimal("1000"),
        max_net_exposure=Decimal("1000"),
        max_single_position_notional=Decimal("500"),
        max_symbol_concentration=Decimal("1"),
        max_strategy_concentration=Decimal("1"),
        max_positions=4,
        max_open_orders=4,
        allocations=(
            PortfolioStrategyAllocation("strategy-a-v1", Decimal("0.5"), Decimal("500"), Decimal("500"), True),
            PortfolioStrategyAllocation("strategy-b-v1", Decimal("0.5"), Decimal("500"), Decimal("500"), True),
        ),
    )
    workers = PortfolioStrategyWorkers(
        strategies=versions,
        candidates=candidates,
        policy=policy,
    )
    workers.observe(_market(), observed_at=NOW, entries_enabled=True, flatten=False)
    snapshot = PortfolioSnapshot(
        cash=Decimal("1000"),
        equity=Decimal("1000"),
        gross_exposure=Decimal("0"),
        net_exposure=Decimal("0"),
        observed_at=NOW,
    )

    for version in versions:
        assert not hasattr(workers._workers[version.version_id], "risk")
        assert not hasattr(workers._workers[version.version_id], "execution")
        assert workers.proposals_for(
            version,
            observed_at=NOW,
            proposal_cutoff=NOW,
            portfolio_snapshot=snapshot,
        ) == ()


def test_stop_generates_one_owned_reduction_and_never_an_entry():
    version = _version("strategy-a-v1")
    candidates = (AutoQuantCandidate("AAPL", "Apple", "Tech", 1, Decimal("1"), "test"),)
    policy = PortfolioCapitalPolicy(
        total_capital_limit=Decimal("1000"),
        max_gross_exposure=Decimal("1000"),
        max_net_exposure=Decimal("1000"),
        max_single_position_notional=Decimal("500"),
        max_symbol_concentration=Decimal("1"),
        max_strategy_concentration=Decimal("1"),
        max_positions=4,
        max_open_orders=4,
        allocations=(PortfolioStrategyAllocation(
            version.version_id, Decimal("1"), Decimal("1000"), Decimal("1000"), True
        ),),
    )
    workers = PortfolioStrategyWorkers(
        strategies=(version,), candidates=candidates, policy=policy
    )
    quote = MarketQuote(
        symbol="AAPL", bid=Decimal("9.99"), ask=Decimal("10.01"),
        last=Decimal("10"), close=Decimal("9.5"),
        bid_size=Decimal("100"), ask_size=Decimal("100"),
        mode=MarketDataMode.REALTIME, updated_at=NOW, age_seconds=0,
        stale=False, stale_reason=None, generation=1, source_id="test",
        source_label="test", coverage="test",
    )
    market = replace(_market(), quotes=(quote,))
    workers.observe(market, observed_at=NOW, entries_enabled=False, flatten=True)
    snapshot = PortfolioSnapshot(
        cash=Decimal("900"), equity=Decimal("1000"),
        gross_exposure=Decimal("100"), net_exposure=Decimal("100"),
        strategy_exposure=(PortfolioStrategyExposure(
            version.version_id, "AAPL", Decimal("100"), 10,
            average_cost=Decimal("10"),
        ),),
        observed_at=NOW,
    )

    proposals = workers.proposals_for(
        version, observed_at=NOW, proposal_cutoff=NOW, portfolio_snapshot=snapshot
    )

    assert len(proposals) == 1
    assert proposals[0].action.value == "sell"
    assert proposals[0].desired_quantity == 10
    assert "stop requested" in proposals[0].reason
