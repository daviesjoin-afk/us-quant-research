"""Unit tests for the pure Shadow start rules.

No Qt, no engine, no store, no broker, no network: every case here is a plain
function over plain data.  That is the point of the extraction -- the ten gates
that used to be reachable only by standing up a 5k-line window are now testable
one rule at a time.

The wording assertions are deliberate.  The operator-facing sentences were moved
verbatim, and a test that pins them is what stops a future "tidy-up" from
quietly changing what the operator is told about the same condition.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from us_quant.desktop_v2.orchestration.shadow import queries
from us_quant.desktop_v2.orchestration.shadow.models import (
    CAPITAL_MESSAGE,
    CAPITAL_TITLE,
    MARKET_TITLE,
    QUOTE_TITLE,
    RUNTIME_BUSY_TITLE,
    STATUS_TITLE,
    STRATEGY_MISMATCH_TITLE,
    STRATEGY_TITLE,
    SYMBOL_TITLE,
    UNIVERSE_TITLE,
    ShadowCapitalFact,
    ShadowStartRefusal,
    ShadowStartRequest,
)
from us_quant.trading.domain.market import (
    MarketDataMode,
    MarketQuote,
    MarketSnapshot,
)
from us_quant.trading.domain.strategy import (
    StrategyDefinition,
    StrategyIdentity,
    StrategyMode,
    StrategyStatus,
    StrategyVersion,
)
from us_quant.universe import UniverseRecord, UniverseSnapshot

NOW = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)


def _quote(symbol: str = "AAPL", *, ready: bool = True) -> MarketQuote:
    """One quote whose ``realtime_ready`` is exactly ``ready``.

    ``realtime_ready`` is *derived* -- not stale, realtime mode, and a usable
    bid/ask.  So "not ready" is produced by flipping the real fields a provider
    would, not by stubbing the property: the rule under test reads it.
    """

    return MarketQuote(
        symbol=symbol,
        bid=Decimal("100.00") if ready else None,
        ask=Decimal("100.02") if ready else None,
        last=Decimal("100.01"),
        close=None,
        bid_size=None,
        ask_size=None,
        mode=MarketDataMode.REALTIME if ready else MarketDataMode.DELAYED,
        updated_at=NOW,
        age_seconds=0.0,
        stale=not ready,
        stale_reason=None if ready else "延迟",
        generation=1,
        source_id="test",
        source_label="Test",
        coverage="NBBO",
    )


def _stream(*quotes: MarketQuote, ready: bool = True) -> MarketSnapshot:
    """A snapshot whose ``realtime_ready`` is ``ready``.

    The snapshot's own flag is ``any(quote.realtime_ready ...)``, so a not-ready
    stream is produced by supplying not-ready quotes -- the property stays real.
    """

    return MarketSnapshot(
        generation=1,
        connected=True,
        ready=True,
        reconnect_attempt=0,
        quotes=quotes or (_quote(ready=ready),),
        error_code=None,
        message="",
        observed_at=NOW,
        source_id="test",
        source_label="Test",
        coverage="NBBO",
    )


def _universe(*symbols: str, eligible: bool = True) -> UniverseSnapshot:
    return UniverseSnapshot(
        generated_at=NOW,
        source_timestamps={"test": "now"},
        records=tuple(
            UniverseRecord(
                symbol=symbol,
                name=symbol,
                exchange="NASDAQ",
                security_type="STK",
                eligible_for_research=eligible,
            )
            for symbol in (symbols or ("AAPL",))
        ),
    )


def _strategy(
    *,
    strategy_id: str = queries.TARGETED_STRATEGY_ID,
    status: StrategyStatus = StrategyStatus.RESEARCH,
    gate_passed: bool = True,
) -> StrategyVersion:
    return StrategyVersion(
        definition=StrategyDefinition(
            strategy_id=strategy_id,
            name=strategy_id,
            description="test",
        ),
        identity=StrategyIdentity(
            strategy_id=strategy_id,
            version_id="targeted-v1",
            parameter_hash="hash-v1",
        ),
        semver="1.0.0",
        status=status,
        mode=StrategyMode.RESEARCH,
        parameters={"momentum_lookback_minutes": 5},
        universe_hash="u",
        code_hash="c",
        risk_budget_pct=Decimal("0.01"),
        gate_passed=gate_passed,
        gate_reason="" if gate_passed else "证据门未通过",
        created_at=NOW,
        updated_at=NOW,
    )


def _plan(**overrides):
    """``plan_start`` with every gate satisfied, then the named overrides."""

    arguments: dict[str, object] = {
        "runtime_is_active": False,
        "strategy": _strategy(),
        "capital": ShadowCapitalFact(net_liquidation=Decimal("10000")),
        "market_is_live": True,
        "market_stream": _stream(),
        "universe": _universe("AAPL"),
        "target_symbol": "AAPL",
        "symbol_risk_multipliers": {"AAPL": Decimal("1.5")},
    }
    arguments.update(overrides)
    return queries.plan_start(**arguments)  # type: ignore[arg-type]


# -- the happy path ------------------------------------------------------


def test_a_satisfied_plan_returns_a_frozen_request() -> None:
    result = _plan()

    assert isinstance(result, ShadowStartRequest)
    assert result.target_symbol == "AAPL"
    assert result.strategy_version_id == "targeted-v1"
    assert result.parameter_hash == "hash-v1"
    assert result.semver == "1.0.0"
    assert result.initial_cash == Decimal("10000")
    assert result.parameters == {"momentum_lookback_minutes": 5}


def test_the_daily_loss_limit_is_one_percent_of_the_paper_capital() -> None:
    """The ceiling is derived from broker truth, never invented."""

    result = _plan(capital=ShadowCapitalFact(net_liquidation=Decimal("25000")))

    assert isinstance(result, ShadowStartRequest)
    assert result.daily_loss_limit == Decimal("250.00")
    assert queries.daily_loss_limit_for(Decimal("10000")) == Decimal("100")


def test_the_risk_multipliers_are_frozen_into_pairs() -> None:
    """A mapping would let the window mutate the run behind the orchestrator."""

    given = {"AAPL": Decimal("1.5")}
    result = _plan(symbol_risk_multipliers=given)

    assert isinstance(result, ShadowStartRequest)
    given["AAPL"] = Decimal("99")
    assert result.risk_multipliers() == {"AAPL": Decimal("1.5")}


def test_plan_start_is_deterministic() -> None:
    first = _plan()
    second = _plan()

    assert isinstance(first, ShadowStartRequest)
    assert isinstance(second, ShadowStartRequest)
    assert first == second


# -- the gate order ------------------------------------------------------


def test_every_gate_refuses_with_its_own_wording_in_order() -> None:
    """The first failing gate wins, and each says what the retired one said.

    The order is asserted, not just the outcomes: an operator who has not read
    their Paper account must be told *that*, not sent after a symbol.
    """

    cases = (
        ({"runtime_is_active": True}, RUNTIME_BUSY_TITLE),
        ({"strategy": None}, STRATEGY_TITLE),
        ({"strategy": _strategy(strategy_id="other")}, STRATEGY_MISMATCH_TITLE),
        (
            {"strategy": _strategy(status=StrategyStatus.STOPPED)},
            STATUS_TITLE,
        ),
        ({"capital": None}, CAPITAL_TITLE),
        ({"market_stream": None}, MARKET_TITLE),
        ({"market_is_live": False}, MARKET_TITLE),
        ({"market_stream": _stream(ready=False)}, MARKET_TITLE),
        ({"universe": None}, UNIVERSE_TITLE),
        ({"target_symbol": "not a symbol"}, SYMBOL_TITLE),
        ({"universe": _universe("AAPL", eligible=False)}, UNIVERSE_TITLE),
        # A ready stream that carries no fresh quote for the target.
        ({"market_stream": _stream(_quote("MSFT"))}, QUOTE_TITLE),
    )
    for overrides, expected_title in cases:
        result = _plan(**overrides)
        assert isinstance(result, ShadowStartRefusal), overrides
        assert result.title == expected_title, overrides
        assert result.message, overrides


def test_the_runtime_gate_is_checked_before_anything_else() -> None:
    """A busy runtime refuses even when every other gate would fail too."""

    result = _plan(runtime_is_active=True, strategy=None, capital=None)

    assert isinstance(result, ShadowStartRefusal)
    assert result.title == RUNTIME_BUSY_TITLE


def test_the_capital_gate_reports_the_paper_account_requirement() -> None:
    result = _plan(capital=None)

    assert isinstance(result, ShadowStartRefusal)
    assert result.message == CAPITAL_MESSAGE
    assert "NetLiquidation" in result.message


# -- individual rules ----------------------------------------------------


def test_only_the_targeted_strategy_may_run() -> None:
    assert queries.is_targeted_strategy(queries.TARGETED_STRATEGY_ID)
    assert not queries.is_targeted_strategy("intraday-targeted-t-v2")
    assert not queries.is_targeted_strategy("")


def test_paper_shadow_is_conditional_on_the_evidence_gate() -> None:
    """A version that has not passed its gate is refused like a stopped one."""

    assert queries.is_runnable_status(
        StrategyStatus.RESEARCH, gate_passed=False
    )
    assert queries.is_runnable_status(
        StrategyStatus.PAPER_SHADOW, gate_passed=True
    )
    assert not queries.is_runnable_status(
        StrategyStatus.PAPER_SHADOW, gate_passed=False
    )
    for status in (
        StrategyStatus.PAUSED,
        StrategyStatus.STOPPED,
        StrategyStatus.LEGACY_INVALIDATED,
    ):
        assert not queries.is_runnable_status(status, gate_passed=True), status


def test_the_symbol_pattern_is_anchored_at_both_ends() -> None:
    """A trailing space must be *rejected*, not silently trimmed into validity."""

    for symbol in ("AAPL", "BRK.B", "A", "RDS-A", "ABCDEFGHIJ"):
        assert queries.is_valid_target_symbol(symbol), symbol
    for symbol in ("", "aapl", "AAPL ", " AAPL", "AA PL", "ABCDEFGHIJK", "1AAPL"):
        assert not queries.is_valid_target_symbol(symbol), symbol


def test_the_market_gate_requires_liveness_and_freshness_together() -> None:
    stream = _stream()
    assert queries.market_gate_passed(is_live=True, stream=stream)
    assert not queries.market_gate_passed(is_live=False, stream=stream)
    assert not queries.market_gate_passed(is_live=True, stream=None)
    assert not queries.market_gate_passed(
        is_live=True, stream=_stream(ready=False)
    )


def test_the_research_gate_is_the_non_china_pool_membership() -> None:
    universe = _universe("AAPL", "MSFT")
    assert queries.is_research_eligible(universe, "AAPL")
    assert queries.is_research_eligible(universe, "MSFT")
    assert not queries.is_research_eligible(universe, "BABA")
    assert not queries.is_research_eligible(_universe("AAPL", eligible=False), "AAPL")


def test_a_draft_symbol_must_be_in_the_pool() -> None:
    """The draft is already normalized by its owner; Shadow only validates it."""

    result = _plan(universe=_universe("MSFT"), target_symbol="AAPL")

    assert isinstance(result, ShadowStartRefusal)
    assert result.title == UNIVERSE_TITLE
    assert "AAPL" in result.message


# -- quote selection -----------------------------------------------------


def test_only_a_fresh_quote_for_the_target_counts() -> None:
    fresh = _quote("AAPL")
    stale = _quote("MSFT", ready=False)

    assert queries.find_ready_quote(_stream(fresh, stale), "AAPL") is fresh
    assert queries.find_ready_quote(_stream(fresh), "MSFT") is None
    assert queries.find_ready_quote(_stream(ready=False), "AAPL") is None


def test_a_stale_quote_for_the_target_is_refused_while_the_stream_is_ready() -> None:
    """Stale is not a degraded input: the simulator fills against the touch.

    The stream here is live and ready -- it carries a fresh MSFT quote -- but the
    target's own quote is stale, which is the case the market gate cannot catch.
    A wholly stale stream never reaches this gate: it is refused earlier as a
    market problem, which is the ordering the cases above pin.
    """

    result = _plan(
        market_stream=_stream(_quote("MSFT"), _quote("AAPL", ready=False)),
    )

    assert isinstance(result, ShadowStartRefusal)
    assert result.title == QUOTE_TITLE
    assert "AAPL" in result.message


def test_a_ready_quote_for_another_symbol_does_not_satisfy_the_target() -> None:
    """No fallback: not another symbol, not the last quote, not an account mark."""

    result = _plan(market_stream=_stream(_quote("MSFT")))

    assert isinstance(result, ShadowStartRefusal)
    assert result.title == QUOTE_TITLE
    assert "AAPL" in result.message


# -- provenance and formatting -------------------------------------------


def test_the_capital_source_names_the_broker_account() -> None:
    """The provenance records that the figure came from a real Paper reading."""

    assert (
        queries.capital_source_text("DU1234567")
        == "IBKR Paper DU1234567 NetLiquidation"
    )


def test_money_formatting_matches_the_retired_helper() -> None:
    """Byte-identical to ``desktop._money``, including the no-sign case."""

    assert queries.format_money(Decimal("10000")) == "$10,000.00"
    assert queries.format_money(Decimal("1234.5")) == "$1,234.50"
    assert queries.format_money(None) == "不可用"
    assert queries.format_money(0) == "$0.00"
    # A negative amount is not a refusal case here, and must not gain a '+'.
    assert queries.format_money(Decimal("-5")) == "$-5.00"
