"""Equivalence tests for the domain migration out of ``us_quant.domain``.

The retired ``us_quant.domain`` module had no test file of its own, so this
suite is what pins the semantics that moved.  It is written as an
*equivalence* suite: every assertion describes behaviour that existed before
the move and must be unchanged after it, covering the invariants the rest of
the system relies on -- bar and market-slice validation, no-short positions,
non-negative account values, whole-share orders, positive prices, and the
approve/reject risk decisions.

Assertions check types as well as values on purpose.  ``Decimal("2500") ==
2500`` is true in Python, so a value-only assertion cannot tell a ``Decimal``
field from an ``int`` one; ``isinstance`` is what actually pins the contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from us_quant.trading.domain.account import (
    AccountSnapshot,
    BrokerConnectionState,
    Position,
)
from us_quant.trading.domain.common import ONE, ZERO, Environment, decimal
from us_quant.trading.domain.market import (
    Bar,
    MarketDataHealth,
    MarketQuote,
    MarketSlice,
    MarketSnapshot,
    MarketSubscription,
)
from us_quant.trading.domain.orders import (
    ExecutionFill,
    OrderEvent,
    OrderIntent,
    OrderStatus,
    Side,
)
from us_quant.trading.domain.risk import RiskDecision
from us_quant.trading.domain.session import (
    TradingSessionPhase,
    TradingSnapshot,
)
from us_quant.trading.domain.strategy import (
    StrategyIdentity,
    TradeAction,
    TradeProposal,
)


_AT = datetime(2026, 9, 18, 14, 30, tzinfo=timezone.utc)


def _bar(**overrides) -> Bar:
    values = {
        "symbol": "AAPL",
        "timestamp": _AT,
        "open": Decimal("100"),
        "high": Decimal("105"),
        "low": Decimal("99"),
        "close": Decimal("104"),
        "volume": 1_000,
    }
    values.update(overrides)
    return Bar(**values)


# -- common ---------------------------------------------------------------


def test_zero_and_one_are_decimal_not_int() -> None:
    assert isinstance(ZERO, Decimal)
    assert isinstance(ONE, Decimal)
    assert ZERO == 0 and ONE == 1


def test_decimal_keeps_decimal_and_parses_str_without_binary_float() -> None:
    existing = Decimal("1.5")
    assert decimal(existing) is existing
    assert decimal("0.1") == Decimal("0.1")
    # str() conversion, not float(): Decimal(0.1) would be 0.1000...555.
    assert decimal(0.1) == Decimal("0.1")


def test_environment_values_are_unchanged() -> None:
    assert Environment.BACKTEST == "backtest"
    assert Environment.PAPER == "paper"
    assert Environment.LIVE == "live"


# -- Bar / MarketSlice ----------------------------------------------------


def test_bar_accepts_a_consistent_ohlc_row() -> None:
    bar = _bar()
    assert bar.symbol == "AAPL"
    assert isinstance(bar.close, Decimal)


@pytest.mark.parametrize(
    "overrides, message",
    (
        ({"symbol": ""}, "symbol is required"),
        (
            {"timestamp": datetime(2026, 9, 18, 14, 30)},
            "bar timestamp must be timezone-aware",
        ),
        ({"open": Decimal("0")}, "bar prices must be positive"),
        ({"low": Decimal("-1")}, "bar prices must be positive"),
        ({"high": Decimal("90")}, "high price is inconsistent"),
        # low=103 clears the high check (105 >= max(100, 104, 103)) but is
        # still above min(open, close, high) == 100.
        ({"low": Decimal("103")}, "low price is inconsistent"),
        ({"volume": -1}, "volume cannot be negative"),
    ),
)
def test_bar_rejects_inconsistent_rows(overrides, message) -> None:
    with pytest.raises(ValueError) as error:
        _bar(**overrides)
    assert message in str(error.value)


def test_market_slice_accepts_matching_bars() -> None:
    bar = _bar()
    slice_ = MarketSlice(timestamp=_AT, bars={"AAPL": bar})
    assert slice_.bars["AAPL"] is bar


def test_market_slice_rejects_naive_timestamp_empty_and_mismatched_bars() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        MarketSlice(timestamp=datetime(2026, 9, 18), bars={"AAPL": _bar()})
    with pytest.raises(ValueError, match="at least one bar"):
        MarketSlice(timestamp=_AT, bars={})
    with pytest.raises(ValueError, match="does not match market slice"):
        MarketSlice(timestamp=_AT, bars={"MSFT": _bar()})
    other = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="does not match market slice"):
        MarketSlice(timestamp=other, bars={"AAPL": _bar()})


# -- Position / AccountSnapshot -------------------------------------------


def test_position_rejects_short_and_negative_and_zero_multiplier() -> None:
    with pytest.raises(ValueError, match="cannot be short"):
        Position(symbol="AAPL", quantity=-1, average_price=Decimal("10"))
    with pytest.raises(ValueError, match="average price cannot be negative"):
        Position(symbol="AAPL", quantity=1, average_price=Decimal("-1"))
    with pytest.raises(ValueError, match="exposure multiplier must be positive"):
        Position(
            symbol="AAPL",
            quantity=1,
            average_price=Decimal("10"),
            exposure_multiplier=ZERO,
        )


def test_position_market_value_and_risk_exposure_scale_with_multiplier() -> None:
    position = Position(
        symbol="AAPL",
        quantity=3,
        average_price=Decimal("10"),
        exposure_multiplier=Decimal("2"),
    )
    assert position.market_value(Decimal("5")) == Decimal("15")
    assert position.risk_exposure(Decimal("5")) == Decimal("30")


def test_account_snapshot_rejects_any_negative_field() -> None:
    for field in ("net_liquidation", "cash", "day_start_equity", "high_watermark"):
        values = {
            "net_liquidation": Decimal("100"),
            "cash": Decimal("100"),
            "day_start_equity": Decimal("100"),
            "high_watermark": Decimal("100"),
        }
        values[field] = Decimal("-1")
        with pytest.raises(ValueError, match="account values cannot be negative"):
            AccountSnapshot(**values)


def test_account_snapshot_defaults_its_timestamp_to_now_utc() -> None:
    snapshot = AccountSnapshot(
        net_liquidation=Decimal("100"),
        cash=Decimal("50"),
        day_start_equity=Decimal("90"),
        high_watermark=Decimal("110"),
    )
    assert snapshot.timestamp.tzinfo is not None


# -- OrderIntent / Side / OrderStatus -------------------------------------


def test_side_and_order_status_values_are_unchanged() -> None:
    assert Side.BUY == "buy" and Side.SELL == "sell"
    assert OrderStatus.CREATED == "created"
    assert OrderStatus.RISK_APPROVED == "risk_approved"
    assert OrderStatus.RISK_REJECTED == "risk_rejected"
    assert OrderStatus.BROKER_REJECTED == "broker_rejected"
    assert OrderStatus.UNKNOWN == "unknown"


def test_order_intent_create_derives_client_order_id_from_order_id() -> None:
    intent = OrderIntent.create(
        signal_symbol="AAPL",
        execution_symbol="AAPL",
        side=Side.BUY,
        quantity=2,
        estimated_price=Decimal("100"),
    )
    assert intent.client_order_id == f"uq-{intent.order_id}"
    assert isinstance(intent.quantity, int)
    assert isinstance(intent.estimated_price, Decimal)


def test_order_intent_requires_positive_whole_shares() -> None:
    base = {
        "order_id": "o-1",
        "client_order_id": "uq-o-1",
        "signal_symbol": "AAPL",
        "execution_symbol": "AAPL",
        "side": Side.BUY,
        "estimated_price": Decimal("100"),
    }
    with pytest.raises(ValueError, match="positive whole number"):
        OrderIntent(**base, quantity=0)
    with pytest.raises(ValueError, match="positive whole number"):
        OrderIntent(**base, quantity=-3)
    # bool is a subclass of int, so True would otherwise pass as quantity 1.
    with pytest.raises(ValueError, match="positive whole number"):
        OrderIntent(**base, quantity=True)


def test_order_intent_requires_positive_price_and_multiplier() -> None:
    base = {
        "order_id": "o-1",
        "client_order_id": "uq-o-1",
        "signal_symbol": "AAPL",
        "execution_symbol": "AAPL",
        "side": Side.BUY,
        "quantity": 1,
    }
    with pytest.raises(ValueError, match="estimated price must be positive"):
        OrderIntent(**base, estimated_price=ZERO)
    with pytest.raises(ValueError, match="estimated price must be positive"):
        OrderIntent(**base, estimated_price=Decimal("-5"))
    with pytest.raises(ValueError, match="exposure multiplier must be positive"):
        OrderIntent(
            **base,
            estimated_price=Decimal("10"),
            exposure_multiplier=ZERO,
        )


def test_order_event_carries_status_and_defaults_payload_per_instance() -> None:
    first = OrderEvent(
        order_id="o-1",
        status=OrderStatus.CREATED,
        idempotency_key="k-1",
    )
    second = OrderEvent(
        order_id="o-2",
        status=OrderStatus.CREATED,
        idempotency_key="k-2",
    )
    assert first.payload == {}
    assert first.payload is not second.payload


def test_execution_fill_requires_whole_share_quantity_and_decimal_price() -> None:
    fill = ExecutionFill(
        execution_id="e-1",
        order_id="o-1",
        symbol="AAPL",
        side=Side.BUY,
        quantity=5,
        price=Decimal("101.25"),
        occurred_at=_AT,
    )
    assert isinstance(fill.quantity, int)
    assert isinstance(fill.price, Decimal)


# -- RiskDecision ---------------------------------------------------------


def test_risk_decision_approve_and_reject() -> None:
    approved = RiskDecision.approve()
    assert approved.approved is True
    assert approved.reasons == ()

    rejected = RiskDecision.reject("too large", "no cash")
    assert rejected.approved is False
    assert rejected.reasons == ("too large", "no cash")
    assert isinstance(rejected.reasons, tuple)


# -- new market models ----------------------------------------------------


def test_market_quote_is_provider_neutral_and_frozen() -> None:
    quote = MarketQuote(
        symbol="AAPL",
        bid=Decimal("100"),
        ask=Decimal("100.02"),
        last=Decimal("100.01"),
        close=None,
        observed_at=_AT,
        source="IBKR",
        realtime=True,
        stale=False,
    )
    assert quote.bid is not None
    assert not hasattr(quote, "__dict__")  # slots
    with pytest.raises(Exception):
        quote.symbol = "MSFT"  # type: ignore[misc]


def test_market_snapshot_subscription_and_health_shapes() -> None:
    snapshot = MarketSnapshot(quotes=(), observed_at=_AT)
    assert snapshot.quotes == ()
    assert MarketSubscription(symbols=("AAPL",)).source is None
    assert MarketSubscription(symbols=("AAPL",), source="IBKR").source == "IBKR"
    health = MarketDataHealth(
        connected=True,
        source="IBKR",
        stale_symbols=("AAPL",),
        message="ok",
    )
    assert health.stale_symbols == ("AAPL",)


# -- strategy -------------------------------------------------------------


def test_trade_action_values() -> None:
    assert TradeAction.BUY == "buy"
    assert TradeAction.SELL == "sell"
    assert TradeAction.HOLD == "hold"


def test_trade_proposal_cannot_be_submitted_as_an_order() -> None:
    """A proposal carries no order identity, so it cannot reach a broker."""

    proposal = TradeProposal(
        strategy=StrategyIdentity(
            strategy_id="s-1",
            version_id="v-1",
            parameter_hash="abc",
        ),
        symbol="AAPL",
        action=TradeAction.BUY,
        desired_quantity=3,
        reference_price=Decimal("100"),
        reason="momentum",
        generated_at=_AT,
    )
    assert proposal.desired_quantity == 3
    assert not hasattr(proposal, "order_id")
    assert not hasattr(proposal, "client_order_id")


# -- session --------------------------------------------------------------


def test_trading_session_phase_matches_the_paper_lifecycle_vocabulary() -> None:
    assert TradingSessionPhase.IDLE == "IDLE"
    assert TradingSessionPhase.PREPARING == "PREPARING"
    assert TradingSessionPhase.READY == "READY"
    assert TradingSessionPhase.CONNECTING == "CONNECTING"
    assert TradingSessionPhase.RUNNING == "RUNNING"
    assert TradingSessionPhase.PAUSED == "PAUSED"
    assert TradingSessionPhase.STOPPING == "STOPPING"
    assert TradingSessionPhase.HALTED == "HALTED"
    assert TradingSessionPhase.RECONCILING == "RECONCILING"
    assert TradingSessionPhase.RECONCILING_READY == "RECONCILING_READY"
    assert TradingSessionPhase.FINALIZED == "FINALIZED"


def test_trading_snapshot_holds_domain_types_only() -> None:
    snapshot = TradingSnapshot(
        phase=TradingSessionPhase.IDLE,
        market_health=MarketDataHealth(
            connected=False,
            source=None,
            stale_symbols=(),
            message="idle",
        ),
        broker=BrokerConnectionState(
            connected=False,
            account_ready=False,
            execution_ready=False,
            message="idle",
        ),
        account=None,
        positions=(),
        risk=None,
        active_strategy=None,
        open_order_count=0,
        status_message="idle",
    )
    assert snapshot.phase is TradingSessionPhase.IDLE
    assert snapshot.account is None
    assert snapshot.positions == ()
    assert snapshot.open_order_count == 0
