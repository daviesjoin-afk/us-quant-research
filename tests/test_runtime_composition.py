"""The runtime composition root, and the contract it has to satisfy.

Two things are pinned here.  First, that ``build_trading_runtime`` assembles
the session over the authorities it was handed rather than creating its own --
a second risk application would be a second truth about what is affordable.
Second, that the object it returns still is the ``PaperEngine`` the session
coordinator drives: the coordinator imports no runtime type, so the shape has
to hold at the boundary even though nothing declares it.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.paper_session import PaperEngine
from us_quant.shadow_paper import ShadowConfig
from us_quant.trading.application.execution import ExecutionApplication
from us_quant.trading.application.risk import RiskApplication
from us_quant.trading.composition.runtime import (
    build_strategy_runtime,
    build_trading_runtime,
)
from us_quant.trading.domain.risk import (
    LayeredRiskLimits,
    RiskLimits,
)
from us_quant.trading.domain.strategy import StrategyIdentity
from us_quant.trading.runtime.models import AutoQuantCandidate
from us_quant.trading.runtime.strategy import StrategyRuntime
from us_quant.trading.runtime.trading import TradingRuntime


_STRATEGY = StrategyIdentity(
    strategy_id="intraday-auto-rotation",
    version_id="version",
    parameter_hash="hash",
)

_CANDIDATES = (
    AutoQuantCandidate(
        symbol="AAA",
        name="AAA",
        sector="T",
        leader_tier=1,
        scan_score=Decimal("80"),
        signal="UP",
    ),
    AutoQuantCandidate(
        symbol="BBB",
        name="BBB",
        sector="T",
        leader_tier=1,
        scan_score=Decimal("70"),
        signal="UP",
    ),
)


def _config() -> ShadowConfig:
    return ShadowConfig(
        initial_cash=Decimal("10000"), capital_source="test"
    )


def _risk() -> RiskApplication:
    return RiskApplication(
        LayeredRiskLimits(
            account=RiskLimits(
                max_gross_exposure_pct=Decimal("1"),
                max_position_exposure_pct=Decimal("1"),
                daily_loss_halt_pct=Decimal("1"),
                drawdown_halt_pct=Decimal("1"),
            )
        )
    )


class _StubExecution:
    """An execution-shaped object that is deliberately not the real service.

    The composition root must refuse it: a session that dispatches through
    something that only looks like the execution service would pass every unit
    test and submit through no channel at all.
    """

    def submit_approved(self, **kwargs: object) -> None:
        raise AssertionError("must never be called")

    def reissue(self, intent: object, *, reason: str) -> None:
        raise AssertionError("must never be called")


def _execution() -> ExecutionApplication:
    return ExecutionApplication(
        repository=_NullRepository(), broker=_NullBroker()
    )


class _NullRepository:
    def record_intent(self, intent, *, broker_order_id, account_alias):
        raise AssertionError("composition must not touch the store")

    def intent(self, order_id: str):
        return None

    def record_event(self, event) -> None:
        raise AssertionError("composition must not touch the store")

    def record_fill(self, fill) -> bool:
        raise AssertionError("composition must not touch the store")

    def status(self, order_id: str):
        return None

    def broker_order_id(self, order_id: str):
        return None

    def fills(self, order_id: str):
        return ()

    def intent_for_idempotency_key(self, key: str):
        return None

    def executed_quantity(self, order_id: str) -> Decimal:
        return Decimal("0")

    def max_broker_order_id(self) -> int:
        return 0


class _NullBroker:
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def reserve(self, intent) -> None:
        raise AssertionError("composition must not reserve")

    def submit(self, reservation) -> None:
        raise AssertionError("composition must not submit")

    def cancel(self, order_id: str) -> bool:
        raise AssertionError("composition must not cancel")

    def events(self):
        return ()

    def fills(self):
        return ()


# -- assembly --------------------------------------------------------------


def test_the_builder_binds_the_authorities_it_was_handed() -> None:
    """No hidden construction: the session runs on the injected services."""

    risk, execution = _risk(), _execution()
    runtime = build_trading_runtime(
        config=_config(),
        candidates=_CANDIDATES,
        identity=_STRATEGY,
        risk=risk,
        execution=execution,
    )

    assert isinstance(runtime, TradingRuntime)
    assert isinstance(runtime.strategy, StrategyRuntime)
    assert runtime.risk is risk
    assert runtime.execution is execution
    assert runtime.dispatch.risk is risk
    assert runtime.dispatch.execution is execution
    assert runtime.identity is _STRATEGY


def test_the_strategy_and_the_session_scan_the_same_candidates() -> None:
    """Two lists would mean a symbol the strategy scans and risk forbids."""

    runtime = build_trading_runtime(
        config=_config(),
        candidates=_CANDIDATES,
        identity=_STRATEGY,
        risk=_risk(),
        execution=_execution(),
    )

    assert runtime.candidates == runtime.strategy.candidates
    assert runtime._candidate_symbols() == frozenset({"AAA", "BBB"})


def test_the_builder_refuses_a_missing_or_foreign_authority() -> None:
    with pytest.raises(TypeError):
        build_trading_runtime(
            config=_config(),
            candidates=_CANDIDATES,
            identity=_STRATEGY,
            risk=None,
            execution=_execution(),
        )
    with pytest.raises(TypeError):
        build_trading_runtime(
            config=_config(),
            candidates=_CANDIDATES,
            identity=_STRATEGY,
            risk=_risk(),
            execution=_StubExecution(),
        )
    with pytest.raises(TypeError):
        build_trading_runtime(
            config=_config(),
            candidates=_CANDIDATES,
            identity="version",
            risk=_risk(),
            execution=_execution(),
        )


def test_the_strategy_builder_validates_the_candidate_set() -> None:
    with pytest.raises(ValueError):
        build_strategy_runtime(
            config=_config(), candidates=(), identity=_STRATEGY
        )
    with pytest.raises(ValueError):
        build_strategy_runtime(
            config=_config(),
            candidates=(_CANDIDATES[0], _CANDIDATES[0]),
            identity=_STRATEGY,
        )
    with pytest.raises(ValueError):
        build_strategy_runtime(
            config=_config(),
            candidates=_CANDIDATES,
            identity=_STRATEGY,
            market_reference_symbols=("AAA",),
        )


# -- the boundary the coordinator drives -----------------------------------


def test_the_runtime_satisfies_the_paper_engine_protocol() -> None:
    """The coordinator names no runtime type, so the shape is the contract."""

    runtime = build_trading_runtime(
        config=_config(),
        candidates=_CANDIDATES,
        identity=_STRATEGY,
        risk=_risk(),
        execution=_execution(),
    )

    for name in PaperEngine.__protocol_attrs__:
        assert hasattr(runtime, name), name
    for name in (
        "snapshot",
        "on_stream",
        "on_execution",
        "on_order_event",
        "pause_entries",
        "resume_entries",
        "request_stop",
        "halt_for_reconciliation",
        "resume_from_reconciliation",
    ):
        assert callable(getattr(runtime, name)), name


def test_the_runtime_starts_and_reports_the_bound_identity() -> None:
    runtime = build_trading_runtime(
        config=_config(),
        candidates=_CANDIDATES,
        identity=_STRATEGY,
        risk=_risk(),
        execution=_execution(),
    )

    snapshot = runtime.start()

    assert snapshot.session_id
    assert snapshot.active
    assert snapshot.strategy_version_id == _STRATEGY.version_id
    assert snapshot.parameter_hash == _STRATEGY.parameter_hash
    assert snapshot.candidate_count == 2
    assert snapshot.observed_at
    assert runtime.snapshot(observed_at=datetime(
        2026, 7, 24, 14, 0, tzinfo=timezone.utc
    )).observed_at == "2026-07-24T14:00:00+00:00"