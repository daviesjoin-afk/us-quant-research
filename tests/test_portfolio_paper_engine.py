from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.trading.application.portfolio_runtime import PortfolioRuntime
from us_quant.trading.domain.market import MarketSnapshot
from us_quant.trading.domain.portfolio import PortfolioCapitalPolicy
from us_quant.trading.domain.portfolio_operations import PortfolioOperatingPlan
from us_quant.trading.runtime.config import TradingSessionConfig
from us_quant.trading.runtime.portfolio import SessionBook
from us_quant.trading.runtime.portfolio_paper import PortfolioPaperEngine
from us_quant.trading.runtime.session import SessionState


class _CycleDriver:
    def __init__(self, runtime, callback):
        self.runtime = runtime
        self.callback = callback

    def __call__(self, market, observed_at, allow_entries, flatten):
        return self.callback(market, observed_at, allow_entries, flatten)


def _plan():
    now = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
    return PortfolioOperatingPlan(
        plan_id="plan-1", revision=1, selected_version_ids=("strategy-a",),
        policy=PortfolioCapitalPolicy(), created_at=now, updated_at=now,
    )


def _market(now: datetime) -> MarketSnapshot:
    return MarketSnapshot(
        generation=1,
        connected=True,
        ready=True,
        reconnect_attempt=0,
        quotes=(),
        error_code=None,
        message="",
        observed_at=now,
        source_id="test",
        source_label="test",
        coverage="test",
    )


def _engine(callback, *, paused=False):
    runtime = object.__new__(PortfolioRuntime)
    config = TradingSessionConfig(initial_cash=Decimal("1000"), capital_source="test")
    book = SessionBook(initial_cash=config.initial_cash, commission=config.commission_per_order)
    book.reset()
    session = SessionState()
    session.begin(candidate_count=3, warmup_minutes=config.warmup_minutes)
    if paused:
        session.pause_entries()
    cycle = _CycleDriver(runtime, callback)
    engine = PortfolioPaperEngine(
        config=config,
        candidate_count=3,
        book=book,
        session=session,
        portfolio_runtime=runtime,
        launch_plan=_plan(),
        portfolio_cycle=cycle,
    )
    return engine, runtime


def test_market_ingress_only_calls_portfolio_cycle_and_updates_shared_book():
    calls = []
    engine, _ = _engine(
        lambda market, observed_at, allow, flatten: calls.append(
            (market, observed_at, allow, flatten)
        )
    )
    observed_at = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
    market = _market(observed_at)

    result = engine.on_stream(market, observed_at=observed_at)

    assert result.active
    assert result.strategy_version_id == "portfolio-runtime"
    assert calls == [(market, observed_at, True, False)]
    assert engine.book.peak_equity == Decimal("1000")


def test_operator_pause_closes_new_exposure_permission():
    calls = []
    engine, _ = _engine(
        lambda market, observed_at, allow, flatten: calls.append((allow, flatten))
    )
    engine.pause_entries()
    now = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)

    engine.on_stream(_market(now), observed_at=now)

    assert calls == [(False, False)]


def test_end_of_day_force_flat_closes_entries_and_requests_attributed_reductions():
    calls = []
    engine, _ = _engine(
        lambda market, observed_at, allow, flatten: calls.append((allow, flatten))
    )
    # 16:00 New York is after the session's configured 15:45 force-flat boundary.
    now = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)

    result = engine.on_stream(_market(now), observed_at=now)

    assert calls == [(False, True)]
    assert result.stop_requested
    assert result.entries_paused
    assert not result.active


def test_portfolio_failure_halts_the_paper_session():
    def fail(*_args):
        raise OSError("ledger unavailable")

    engine, _ = _engine(fail)
    now = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)

    result = engine.on_stream(_market(now), observed_at=now)

    assert not result.active
    assert result.entries_paused
    assert result.stop_requested
    assert "ledger unavailable" in result.status


def test_engine_rejects_a_session_that_has_not_started():
    runtime = object.__new__(PortfolioRuntime)
    config = TradingSessionConfig(initial_cash=Decimal("1000"), capital_source="test")
    book = SessionBook(initial_cash=config.initial_cash, commission=config.commission_per_order)
    with pytest.raises(ValueError, match="started Paper session"):
        PortfolioPaperEngine(
            config=config,
            candidate_count=1,
            book=book,
            session=SessionState(),
            portfolio_runtime=runtime,
            launch_plan=_plan(),
            portfolio_cycle=lambda *_args: None,
        )
