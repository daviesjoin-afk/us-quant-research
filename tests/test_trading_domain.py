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

from datetime import datetime, timezone, tzinfo
from decimal import Decimal

import pytest

from us_quant.trading.domain.account import (
    RiskAccountSnapshot,
    BrokerConnectionState,
    Position,
)
from us_quant.trading.domain.common import ONE, ZERO, Environment, decimal
from us_quant.trading.domain.market import (
    Bar,
    MarketDataHealth,
    MarketDataMode,
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


class _OffsetlessTz(tzinfo):
    """A ``tzinfo`` that reports no UTC offset.

    ``tzinfo is not None`` is true for it, but ``utcoffset()`` returns
    ``None``, so a naive check based on the first property alone would let it
    through and fail much later, inside a subtraction.
    """

    def utcoffset(self, dt):  # type: ignore[no-untyped-def]
        return None

    def tzname(self, dt):  # type: ignore[no-untyped-def]
        return "offsetless"

    def dst(self, dt):  # type: ignore[no-untyped-def]
        return None


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


# -- Position / RiskAccountSnapshot -------------------------------------------


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
            RiskAccountSnapshot(**values)


def test_account_snapshot_defaults_its_timestamp_to_now_utc() -> None:
    snapshot = RiskAccountSnapshot(
        net_liquidation=Decimal("100"),
        cash=Decimal("50"),
        day_start_equity=Decimal("90"),
        high_watermark=Decimal("110"),
    )
    assert snapshot.timestamp.tzinfo is not None


def test_account_snapshot_refuses_a_naive_timestamp() -> None:
    """Risk v2: the halts compare this snapshot against a trading day.

    A reading with no offset would leave "how old is this?" up to the
    machine's locale, and the daily-loss ratio up to whatever the local clock
    believed.  The default is aware; a supplied one has to be too.
    """

    with pytest.raises(ValueError, match="must be timezone-aware"):
        RiskAccountSnapshot(
            net_liquidation=Decimal("100"),
            cash=Decimal("50"),
            day_start_equity=Decimal("90"),
            high_watermark=Decimal("110"),
            timestamp=datetime(2026, 9, 19, 12, 0),
        )
    # A ``tzinfo`` whose ``utcoffset()`` is ``None`` is still effectively
    # naive, so ``tzinfo is not None`` alone is not enough.
    with pytest.raises(ValueError, match="must be timezone-aware"):
        RiskAccountSnapshot(
            net_liquidation=Decimal("100"),
            cash=Decimal("50"),
            day_start_equity=Decimal("90"),
            high_watermark=Decimal("110"),
            timestamp=datetime(
                2026, 9, 19, 12, 0, tzinfo=_OffsetlessTz()
            ),
        )


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
        session_id="s-1",
        strategy_version_id="v-1",
        signal_symbol="AAPL",
        execution_symbol="AAPL",
        side=Side.BUY,
        quantity=2,
        limit_price=Decimal("100"),
        reason="entry",
    )
    assert intent.client_order_id == f"uq-{intent.order_id}"
    assert isinstance(intent.quantity, int)
    assert isinstance(intent.limit_price, Decimal)
    # The idempotency key is per intent, not derived from the order id: a key
    # derived from the identity could never collide, which would make the
    # duplicate-key guard vacuous.
    assert intent.idempotency_key
    assert intent.idempotency_key != intent.client_order_id


def test_order_intent_carries_the_context_an_order_needs() -> None:
    """Session, strategy, reason and price are recorded, not re-derivable."""

    intent = OrderIntent.create(
        session_id="s-1",
        strategy_version_id="v-1",
        signal_symbol="aapl",
        execution_symbol="aapl",
        side=Side.SELL,
        quantity=1,
        limit_price=Decimal("101.5"),
        reason="trimmed 3 → 1",
    )
    assert intent.session_id == "s-1"
    assert intent.strategy_version_id == "v-1"
    assert intent.signal_symbol == "AAPL"
    assert intent.execution_symbol == "AAPL"
    assert intent.side is Side.SELL
    assert intent.reason == "trimmed 3 → 1"
    assert intent.created_at.tzinfo is not None


def test_side_round_trips_the_order_text() -> None:
    assert Side.BUY.order_text == "BUY"
    assert Side.SELL.order_text == "SELL"
    assert Side.from_order_text(" buy ") is Side.BUY
    assert Side.from_order_text("SELL") is Side.SELL
    with pytest.raises(ValueError, match="unsupported order side"):
        Side.from_order_text("SHORT")


def test_order_status_terminality_is_declared_not_guessed() -> None:
    assert OrderStatus.FILLED.is_terminal is True
    assert OrderStatus.CANCELED.is_terminal is True
    assert OrderStatus.INACTIVE.is_terminal is True
    assert OrderStatus.BROKER_REJECTED.is_terminal is True
    assert OrderStatus.ACKNOWLEDGED.is_terminal is False
    assert OrderStatus.PARTIALLY_FILLED.is_terminal is False
    # "I do not know what the broker did" is not "finished".
    assert OrderStatus.UNKNOWN.is_terminal is False


def test_order_intent_requires_positive_whole_shares() -> None:
    base = {
        "order_id": "o-1",
        "client_order_id": "uq-o-1",
        "session_id": "s-1",
        "strategy_version_id": "v-1",
        "signal_symbol": "AAPL",
        "execution_symbol": "AAPL",
        "side": Side.BUY,
        "limit_price": Decimal("100"),
        "reason": "r",
        "idempotency_key": "k-1",
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
        "session_id": "s-1",
        "strategy_version_id": "v-1",
        "signal_symbol": "AAPL",
        "execution_symbol": "AAPL",
        "side": Side.BUY,
        "quantity": 1,
        "reason": "r",
        "idempotency_key": "k-1",
    }
    with pytest.raises(ValueError, match="limit price must be positive"):
        OrderIntent(**base, limit_price=ZERO)
    with pytest.raises(ValueError, match="limit price must be positive"):
        OrderIntent(**base, limit_price=Decimal("-5"))
    with pytest.raises(ValueError, match="exposure multiplier must be positive"):
        OrderIntent(
            **base,
            limit_price=Decimal("10"),
            exposure_multiplier=ZERO,
        )


def test_order_event_carries_the_status_facts_as_fields() -> None:
    """A runtime policy must not have to spell ``payload["filled"]``."""

    event = OrderEvent(
        order_id="o-1",
        status=OrderStatus.PARTIALLY_FILLED,
        broker_order_id=17,
        broker_status="Submitted",
        filled=Decimal("2"),
        remaining=Decimal("3"),
        average_fill_price=Decimal("101"),
        last_fill_price=Decimal("101.25"),
        message="held",
        idempotency_key="k-1",
    )
    assert event.filled == Decimal("2")
    assert event.remaining == Decimal("3")
    assert event.broker_status == "Submitted"
    assert event.status is OrderStatus.PARTIALLY_FILLED
    # ``payload`` stays for stores that persist a whole snapshot, but the
    # status facts above are fields -- reading them out of a dict is how a
    # policy comes to treat a missing key as zero.
    assert event.payload == {}
    other = OrderEvent(
        order_id="o-2",
        status=OrderStatus.CREATED,
        broker_order_id=18,
        broker_status="Submitted",
    )
    assert other.payload is not event.payload


def test_execution_fill_keeps_the_broker_quantity_at_full_precision() -> None:
    """A fractional fill is a fact to halt on, never a number to round down."""

    fill = ExecutionFill(
        execution_id="e-1",
        order_id="o-1",
        broker_order_id=17,
        symbol="AAPL",
        side=Side.BUY,
        quantity=Decimal("1.5"),
        price=Decimal("101.25"),
        occurred_at=_AT,
    )
    assert isinstance(fill.quantity, Decimal)
    assert fill.quantity == Decimal("1.5")
    assert isinstance(fill.price, Decimal)


# -- RiskDecision ---------------------------------------------------------
#
# The full invariant matrix lives in ``test_trading_risk_domain``.  What is
# pinned here is the shape the rest of the domain depends on: a verdict names
# whole-share quantities, and the two named constructors produce consistent
# ones.


def test_risk_decision_approve_and_reject() -> None:
    approved = RiskDecision.approve(requested_quantity=8)
    assert approved.approved is True
    assert approved.approved_quantity == 8
    assert approved.reasons == ()
    assert approved.adjustments == ()

    trimmed = RiskDecision.approve(
        requested_quantity=8,
        approved_quantity=3,
        adjustments=("cash cap reduced quantity 8 → 3",),
    )
    assert trimmed.approved is True
    assert trimmed.approved_quantity == 3

    rejected = RiskDecision.reject(
        "too large", "no cash", requested_quantity=8
    )
    assert rejected.approved is False
    assert rejected.approved_quantity == 0
    assert rejected.requested_quantity == 8
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
        bid_size=None,
        ask_size=None,
        mode=MarketDataMode.REALTIME,
        updated_at=_AT,
        age_seconds=0.5,
        stale=False,
        stale_reason=None,
        generation=1,
        source_id="ibkr",
        source_label="IBKR",
        coverage="由 IBKR 订阅权限决定",
    )
    assert quote.bid is not None
    # The domain type carries no vendor market-data number: the adapter maps
    # 1/2/3/4 onto the mode before the quote crosses the boundary.
    assert not hasattr(quote, "effective_market_data_type")
    assert not hasattr(quote, "request_id")
    assert not hasattr(quote, "__dict__")  # slots
    with pytest.raises(Exception):
        quote.symbol = "MSFT"  # type: ignore[misc]


def test_market_quote_realtime_ready_requires_every_condition() -> None:
    """The gate is preservation: a wrong upgrade would let stale data trade."""

    def quote(**overrides) -> MarketQuote:
        base = dict(
            symbol="AAPL",
            bid=Decimal("100"),
            ask=Decimal("100.02"),
            last=Decimal("100.01"),
            close=None,
            bid_size=None,
            ask_size=None,
            mode=MarketDataMode.REALTIME,
            updated_at=_AT,
            age_seconds=0.5,
            stale=False,
            stale_reason=None,
            generation=1,
            source_id="ibkr",
            source_label="IBKR",
            coverage="coverage",
        )
        base.update(overrides)
        return MarketQuote(**base)

    assert quote().realtime_ready is True
    # Each condition on its own is enough to disqualify.
    assert quote(stale=True, stale_reason="old").realtime_ready is False
    assert quote(mode=MarketDataMode.DELAYED).realtime_ready is False
    assert quote(bid=None).realtime_ready is False
    assert quote(ask=None).realtime_ready is False
    assert quote(bid=Decimal("0")).realtime_ready is False
    # A crossed book is not usable.
    assert (
        quote(bid=Decimal("100.02"), ask=Decimal("100")).realtime_ready
        is False
    )


def test_market_snapshot_subscription_and_health_shapes() -> None:
    snapshot = MarketSnapshot(
        generation=0,
        connected=False,
        ready=False,
        reconnect_attempt=0,
        quotes=(),
        error_code=None,
        message="idle",
        observed_at=_AT,
        source_id="ibkr",
        source_label="IBKR",
        coverage="coverage",
    )
    assert snapshot.quotes == ()
    assert snapshot.realtime_ready is False
    assert snapshot.quote_for("AAPL") is None
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
