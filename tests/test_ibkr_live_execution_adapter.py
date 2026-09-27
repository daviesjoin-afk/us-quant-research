from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.adapters.ibkr.live_execution import (
    IBKRLiveExecutionAdapter,
    IBKRLiveExecutionError,
    IBKRLiveSubmissionUncertain,
    _aware_execution_time,
)
from us_quant.trading.domain.live_safety import LiveAccountFingerprint
from us_quant.trading.domain.orders import OrderIntent, OrderStatus, Side

ACCOUNT = "U1234567"
ENDPOINT = "ibkr-live-loopback:127.0.0.1:4001"


class _Repository:
    def __init__(
        self,
        maximum: int = 42,
        unreconciled: tuple[int, ...] = (),
    ) -> None:
        self.maximum = maximum
        self.unreconciled = unreconciled

    def max_broker_order_id(self) -> int:
        return self.maximum

    def unreconciled_broker_order_ids(self, account_alias: str) -> tuple[int, ...]:
        del account_alias
        return self.unreconciled


class _FakeGateway:
    def __init__(
        self,
        sink,
        epoch: int,
        *,
        accounts: tuple[str, ...] = (ACCOUNT,),
        positions: tuple[tuple[str, Decimal], ...] = (("AAPL", Decimal("2")),),
        open_orders: tuple[tuple[int, str], ...] = (),
        submit_error: Exception | None = None,
        cancel_error: Exception | None = None,
    ) -> None:
        self.sink = sink
        self.epoch = epoch
        self.accounts = accounts
        self.positions = positions
        self.open_orders = open_orders
        self.submit_error = submit_error
        self.cancel_error = cancel_error
        self.connected = False
        self.position_cancel_calls = 0
        self.placed: list[tuple[int, object, object]] = []
        self.cancelled: list[tuple[int, str]] = []
        self.connect_calls: list[tuple[str, int, int]] = []

    def connect(self, host: str, port: int, client_id: int) -> None:
        self.connect_calls.append((host, port, client_id))
        self.connected = True

    def run(self) -> None:
        self.sink.gateway_next_valid_id(self, self.epoch, 40)
        self.sink.gateway_managed_accounts(self, self.epoch, ",".join(self.accounts))

    def reqPositions(self) -> None:
        for row in self.positions:
            symbol, quantity = row[:2]
            security_type = row[2] if len(row) > 2 else "STK"
            currency = row[3] if len(row) > 3 else "USD"
            self.sink.gateway_position(
                self,
                self.epoch,
                self.accounts[0],
                SimpleNamespace(symbol=symbol, secType=security_type, currency=currency),
                quantity,
                Decimal("100"),
            )
        self.sink.gateway_position_end(self, self.epoch)

    def reqAllOpenOrders(self) -> None:
        for order_id, account in self.open_orders:
            self.sink.gateway_open_order(
                self,
                self.epoch,
                order_id,
                SimpleNamespace(symbol="AAPL", secType="STK", currency="USD"),
                SimpleNamespace(account=account),
            )
        self.sink.gateway_open_order_end(self, self.epoch)

    def isConnected(self) -> bool:
        return self.connected

    def disconnect(self) -> None:
        self.connected = False
        self.sink.gateway_connection_closed(self, self.epoch)

    def cancelPositions(self) -> None:
        self.position_cancel_calls += 1

    def placeOrder(self, order_id: int, contract: object, order: object) -> None:
        self.placed.append((order_id, contract, order))
        if self.submit_error is not None:
            raise self.submit_error

    def cancelOrder(self, order_id: int, manual_time: str) -> None:
        self.cancelled.append((order_id, manual_time))
        if self.cancel_error is not None:
            raise self.cancel_error


def _fingerprint(account_id: str = ACCOUNT) -> LiveAccountFingerprint:
    return LiveAccountFingerprint.from_identity(
        provider="IBKR",
        environment="LIVE",
        account_id=account_id,
        endpoint_identity=ENDPOINT,
    )


def _config(
    *,
    host: str = "127.0.0.1",
    port: int = 4001,
    api_read_only: bool = False,
    paper_order_submission_enabled: bool = False,
) -> IBKRConnectionConfig:
    return IBKRConnectionConfig(
        host=host,
        port=port,
        client_id=83,
        api_read_only=api_read_only,
        paper_order_submission_enabled=paper_order_submission_enabled,
        connection_timeout_seconds=1,
    )


def _adapter(
    *,
    accounts: tuple[str, ...] = (ACCOUNT,),
    positions: tuple[tuple[str, Decimal], ...] = (("AAPL", Decimal("2")),),
    open_orders: tuple[tuple[int, str], ...] = (),
    submit_error: Exception | None = None,
    cancel_error: Exception | None = None,
    repository: _Repository | None = None,
) -> tuple[IBKRLiveExecutionAdapter, _FakeGateway, _Repository]:
    store = repository or _Repository()
    holder: list[_FakeGateway] = []

    def gateway_factory(*, sink, epoch):
        app = _FakeGateway(
            sink,
            epoch,
            accounts=accounts,
            positions=positions,
            open_orders=open_orders,
            submit_error=submit_error,
            cancel_error=cancel_error,
        )
        holder.append(app)
        return app

    adapter = IBKRLiveExecutionAdapter(
        _config(),
        repository=store,
        expected_account_fingerprint=_fingerprint(),
        allowed_symbols=frozenset({"AAPL"}),
        allowed_strategy_version_ids=frozenset({"strategy-v1"}),
        gateway_factory=gateway_factory,
        client_connector=lambda app, config: app.connect(
            config.host, config.port, config.client_id
        ),
        contract_factory=lambda: SimpleNamespace(),
        order_factory=lambda: SimpleNamespace(),
    )
    return adapter, _FakeGatewayProxy(holder), store


class _FakeGatewayProxy:
    """Stable reference populated by the asynchronous fake network thread."""

    def __init__(self, holder: list[_FakeGateway]) -> None:
        self._holder = holder

    def __getattr__(self, name: str):
        if not self._holder:
            raise AttributeError(name)
        return getattr(self._holder[0], name)


def _intent(
    *,
    side: Side = Side.BUY,
    quantity: int = 1,
    symbol: str = "AAPL",
    strategy: str = "strategy-v1",
    limit_price: Decimal = Decimal("200"),
) -> OrderIntent:
    return OrderIntent.create(
        session_id="live-session",
        strategy_version_id=strategy,
        signal_symbol=symbol,
        execution_symbol=symbol,
        side=side,
        quantity=quantity,
        limit_price=limit_price,
        reason="test order",
    )


def _connect(adapter: IBKRLiveExecutionAdapter) -> None:
    adapter.connect()


def test_live_adapter_implements_the_frozen_provider_neutral_port():
    adapter = IBKRLiveExecutionAdapter(
        _config(),
        repository=_Repository(),
        expected_account_fingerprint=_fingerprint(),
        allowed_symbols=frozenset({"AAPL"}),
        allowed_strategy_version_ids=frozenset({"strategy-v1"}),
        gateway_factory=lambda **_: None,
        client_connector=lambda *args: None,
    )
    assert all(callable(getattr(adapter, name)) for name in (
        "connect", "disconnect", "reserve", "submit", "cancel", "events", "fills"
    ))
    assert {
        name
        for name in ("connect", "disconnect", "reserve", "submit", "cancel", "events", "fills")
        if callable(getattr(IBKRLiveExecutionAdapter, name, None))
    } == {"connect", "disconnect", "reserve", "submit", "cancel", "events", "fills"}


@pytest.mark.parametrize(
    "config",
    [
        _config(port=4002),
        _config(host="::1"),
        _config(api_read_only=True),
        _config(paper_order_submission_enabled=True),
    ],
)
def test_only_the_non_readonly_loopback_live_profile_is_accepted(config):
    with pytest.raises(IBKRLiveExecutionError):
        IBKRLiveExecutionAdapter(
            config,
            repository=_Repository(),
            expected_account_fingerprint=_fingerprint(),
            allowed_symbols=frozenset({"AAPL"}),
            allowed_strategy_version_ids=frozenset({"strategy-v1"}),
        )


@pytest.mark.parametrize(
    "accounts",
    [(), ("DU7654321",), (ACCOUNT, "DU7654321")],
)
def test_managed_account_must_be_one_exact_bound_live_account(accounts):
    adapter, _, _ = _adapter(accounts=accounts)

    with pytest.raises(IBKRLiveExecutionError):
        _connect(adapter)

    assert not adapter.connected


def test_startup_refuses_any_existing_open_order_for_bound_account():
    adapter, gateway, _ = _adapter(open_orders=((17, ACCOUNT),))

    with pytest.raises(IBKRLiveExecutionError, match="未完成订单"):
        _connect(adapter)

    assert not adapter.connected
    assert not adapter.halted
    assert gateway.placed == []


def test_startup_refuses_durable_orders_missing_completed_reconciliation():
    adapter, gateway, _ = _adapter(repository=_Repository(unreconciled=(43,)))

    with pytest.raises(IBKRLiveExecutionError, match="持久化订单"):
        _connect(adapter)

    assert not adapter.connected
    assert not adapter.halted
    assert gateway.placed == []


@pytest.mark.parametrize(
    "positions",
    [(("AAPL", Decimal("-1")),), (("AAPL", Decimal("1.5")),)],
)
def test_short_or_fractional_startup_position_fails_closed(positions):
    adapter, _, _ = _adapter(positions=positions)

    with pytest.raises(IBKRLiveExecutionError):
        _connect(adapter)

    assert not adapter.connected


@pytest.mark.parametrize(
    "positions",
    [
        (("AAPL", Decimal("10"), "OPT"),),
        (("AAPL", Decimal("10"), "STK", "EUR"),),
    ],
)
def test_non_usd_stock_startup_position_fails_closed(positions):
    adapter, _, _ = _adapter(positions=positions)

    with pytest.raises(IBKRLiveExecutionError):
        _connect(adapter)

    assert not adapter.connected


def test_duplicate_stock_symbol_rows_fail_closed():
    adapter, _, _ = _adapter(
        positions=(
            ("AAPL", Decimal("-1")),
            ("AAPL", Decimal("2")),
        )
    )

    with pytest.raises(IBKRLiveExecutionError, match="duplicate stock symbols"):
        _connect(adapter)

    assert not adapter.connected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("20260115  12:30:00", datetime(2026, 1, 15, 12, 30, tzinfo=timezone.utc)),
        (
            "20260115  12:30:00 US/Eastern",
            datetime(2026, 1, 15, 17, 30, tzinfo=timezone.utc),
        ),
        (
            "20260715 12:30:00 America/New_York",
            datetime(2026, 7, 15, 16, 30, tzinfo=timezone.utc),
        ),
    ],
)
def test_broker_execution_time_parses_naive_and_named_timezones(value, expected):
    assert _aware_execution_time(value) == expected


def test_reserve_only_allocates_id_and_submit_builds_one_live_lmt_order():
    adapter, gateway, _ = _adapter(repository=_Repository(maximum=42))
    _connect(adapter)
    intent = _intent()

    reservation = adapter.reserve(intent)
    assert reservation.broker_order_id == 43
    assert reservation.account_alias.endswith("67")
    assert gateway.placed == []

    adapter.submit(reservation)

    assert len(gateway.placed) == 1
    order_id, contract, order = gateway.placed[0]
    assert order_id == reservation.broker_order_id
    assert (contract.symbol, contract.secType, contract.exchange, contract.currency) == (
        "AAPL",
        "STK",
        "SMART",
        "USD",
    )
    assert (order.action, order.orderType, order.totalQuantity, order.lmtPrice) == (
        "BUY",
        "LMT",
        1,
        200.0,
    )
    assert order.tif == "DAY" and order.outsideRth is False
    assert order.account == ACCOUNT and order.transmit is True
    assert gateway.position_cancel_calls == 1
    with pytest.raises(IBKRLiveExecutionError, match="不得再次提交"):
        adapter.submit(reservation)
    assert len(gateway.placed) == 1
    adapter.disconnect()


def test_unapproved_symbol_strategy_fractional_or_oversell_is_refused():
    adapter, _, _ = _adapter()
    _connect(adapter)

    with pytest.raises(IBKRLiveExecutionError, match="标的"):
        adapter.reserve(_intent(symbol="MSFT"))
    with pytest.raises(IBKRLiveExecutionError, match="策略"):
        adapter.reserve(_intent(strategy="unbound-v2"))
    with pytest.raises(IBKRLiveExecutionError, match="已确认"):
        adapter.reserve(_intent(side=Side.SELL, quantity=3))
    fractional = _intent()
    object.__setattr__(fractional, "quantity", 1.5)
    with pytest.raises(IBKRLiveExecutionError, match="整股"):
        adapter.reserve(fractional)
    adapter.disconnect()


@pytest.mark.parametrize("price", [Decimal("1e1000"), Decimal("1e-1000")])
def test_limit_price_outside_ibkr_float_range_is_refused(price):
    adapter, _, _ = _adapter()
    _connect(adapter)

    with pytest.raises(IBKRLiveExecutionError, match="IBKR 数值范围"):
        adapter.reserve(_intent(limit_price=price))

    adapter.disconnect()


def test_sell_reservations_prevent_overselling_and_cancel_only_tracked_orders():
    adapter, gateway, _ = _adapter()
    _connect(adapter)
    intent = _intent(side=Side.SELL, quantity=2)
    reservation = adapter.reserve(intent)

    with pytest.raises(IBKRLiveExecutionError, match="超过"):
        adapter.reserve(_intent(side=Side.SELL, quantity=1))
    with pytest.raises(IBKRLiveExecutionError, match="tracked"):
        adapter.cancel("not-a-live-order")
    assert adapter.cancel(intent.order_id)
    assert not adapter.cancel(intent.order_id)
    assert gateway.cancelled == [(reservation.broker_order_id, "")]
    adapter.disconnect()


def test_broker_order_status_and_fill_callbacks_are_normalized_and_deduplicated():
    adapter, _, _ = _adapter()
    _connect(adapter)
    gateway = adapter._client
    intent = _intent()
    reservation = adapter.reserve(intent)
    adapter.submit(reservation)
    adapter.gateway_order_status(
        gateway,
        adapter._epoch,
        (reservation.broker_order_id, "Submitted", Decimal("0"), Decimal("1"), 0, 0, 0, 0),
    )
    execution = SimpleNamespace(
        orderId=reservation.broker_order_id,
        acctNumber=ACCOUNT,
        execId="exec-1",
        shares=Decimal("1"),
        price=Decimal("201"),
        side="BOT",
        time="20260928  08:00:00",
    )
    contract = SimpleNamespace(symbol="AAPL", secType="STK")
    adapter.gateway_exec_details(gateway, adapter._epoch, contract, execution)
    adapter.gateway_exec_details(gateway, adapter._epoch, contract, execution)

    events = adapter.events()
    fills = adapter.fills()
    assert len(events) == 1 and events[0].status is OrderStatus.ACKNOWLEDGED
    assert len(fills) == 1
    assert fills[0].execution_id == "exec-1"
    assert fills[0].quantity == Decimal("1") and fills[0].price == Decimal("201")
    assert fills[0].occurred_at.tzinfo is timezone.utc
    assert fills[0].occurred_at.isoformat() == "2026-09-28T08:00:00+00:00"
    assert adapter._positions["AAPL"] == Decimal("3")
    adapter.gateway_position(
        gateway,
        adapter._epoch,
        ACCOUNT,
        SimpleNamespace(symbol="AAPL", secType="STK", currency="USD"),
        Decimal("2"),
        Decimal("100"),
    )
    assert adapter._positions["AAPL"] == Decimal("3")
    assert adapter._errors == []
    adapter.disconnect()


def test_invalid_order_status_quantities_halt_and_publish_unknown():
    adapter, _, _ = _adapter()
    _connect(adapter)
    intent = _intent()
    reservation = adapter.reserve(intent)
    adapter.submit(reservation)

    adapter.gateway_order_status(
        adapter._client,
        adapter._epoch,
        (reservation.broker_order_id, "Filled", Decimal("1.5"), Decimal("0")),
    )

    event, = adapter.events()
    assert adapter.halted
    assert event.status is OrderStatus.UNKNOWN
    assert event.filled == Decimal("0")
    assert event.remaining == Decimal("1")
    adapter.disconnect()


@pytest.mark.parametrize(
    ("contract", "account", "quantity"),
    [
        (SimpleNamespace(symbol="MSFT", secType="STK"), ACCOUNT, "1"),
        (SimpleNamespace(symbol="AAPL", secType="OPT"), ACCOUNT, "1"),
        (SimpleNamespace(symbol="AAPL", secType="STK"), "U7654321", "1"),
        (SimpleNamespace(symbol="AAPL", secType="STK"), ACCOUNT, "2"),
    ],
)
def test_execution_fill_mismatch_halts_without_emitting_fill(contract, account, quantity):
    adapter, _, _ = _adapter()
    _connect(adapter)
    intent = _intent()
    reservation = adapter.reserve(intent)
    adapter.submit(reservation)
    execution = SimpleNamespace(
        orderId=reservation.broker_order_id,
        acctNumber=account,
        execId="bad-exec",
        shares=Decimal(quantity),
        price=Decimal("201"),
        side="BOT",
        time="20260928  08:00:00 UTC",
    )

    adapter.gateway_exec_details(adapter._client, adapter._epoch, contract, execution)

    assert adapter.halted
    assert adapter.fills() == ()
    adapter.disconnect()


def test_uncertain_submit_halts_adapter_and_never_retries():
    adapter, gateway, _ = _adapter(submit_error=RuntimeError("socket dropped"))
    _connect(adapter)
    intent = _intent()
    reservation = adapter.reserve(intent)

    with pytest.raises(IBKRLiveSubmissionUncertain):
        adapter.submit(reservation)
    assert adapter.halted
    assert adapter.events()[0].status is OrderStatus.UNKNOWN
    with pytest.raises(IBKRLiveExecutionError, match="HALTED"):
        adapter.submit(reservation)
    with pytest.raises(IBKRLiveExecutionError, match="HALTED"):
        adapter.reserve(_intent())
    assert len(gateway.placed) == 1
    adapter.disconnect()


def test_order_construction_failure_is_refused_before_submit_without_halting():
    adapter, gateway, _ = _adapter()
    _connect(adapter)
    intent = _intent()
    reservation = adapter.reserve(intent)

    def broken_contract_factory():
        raise RuntimeError("invalid contract factory")

    adapter._contract_factory = broken_contract_factory
    with pytest.raises(IBKRLiveExecutionError, match="尚未提交"):
        adapter.submit(reservation)

    assert not adapter.halted
    assert gateway.placed == []
    adapter.disconnect()


def test_reusing_an_order_id_for_different_intent_is_refused():
    adapter, _, _ = _adapter()
    _connect(adapter)
    intent = _intent()
    adapter.reserve(intent)

    with pytest.raises(IBKRLiveExecutionError, match="内容冲突"):
        adapter.reserve(replace(intent, quantity=2))
    adapter.disconnect()


@pytest.mark.parametrize(
    ("callback_args", "expected_status", "expected_message"),
    [
        ((0, 202, "Order cancelled", ""), OrderStatus.CANCELED, "Order cancelled"),
        ((201, "Order rejected"), OrderStatus.BROKER_REJECTED, "Order rejected"),
        ((201, "Order rejected", "advanced json"), OrderStatus.BROKER_REJECTED, "Order rejected"),
    ],
)
def test_gateway_error_supports_ibapi_callback_signatures(
    callback_args, expected_status, expected_message
):
    adapter, _, _ = _adapter()
    _connect(adapter)
    intent = _intent()
    reservation = adapter.reserve(intent)
    adapter.submit(reservation)

    adapter.gateway_error(
        adapter._client,
        adapter._epoch,
        reservation.broker_order_id,
        callback_args,
    )

    event, = adapter.events()
    assert event.status is expected_status
    assert event.message == expected_message
    adapter.disconnect()


def test_post_handshake_gateway_error_halts_and_cancel_is_blocked():
    adapter, gateway, _ = _adapter()
    _connect(adapter)
    intent = _intent()
    adapter.reserve(intent)
    adapter.submit(adapter.reserve(intent))

    adapter.gateway_error(
        adapter._client,
        adapter._epoch,
        -1,
        (1727452800000, 1100, "Connectivity between IB and TWS has been lost", ""),
    )

    assert adapter.halted
    assert not adapter.connected
    with pytest.raises(IBKRLiveExecutionError, match="disconnected"):
        adapter.cancel(intent.order_id)
    assert gateway.cancelled == []
    adapter.disconnect()


def test_uncertain_submit_halt_blocks_another_broker_cancel():
    adapter, gateway, _ = _adapter(submit_error=RuntimeError("submit uncertain"))
    _connect(adapter)
    intent = _intent()
    reservation = adapter.reserve(intent)

    with pytest.raises(IBKRLiveSubmissionUncertain):
        adapter.submit(reservation)
    with pytest.raises(IBKRLiveExecutionError, match="disconnected"):
        adapter.cancel(intent.order_id)

    assert len(gateway.placed) == 1
    assert gateway.cancelled == []
    adapter.disconnect()


def test_uncertain_cancel_halts_and_connection_loss_requires_reconciliation():
    adapter, gateway, _ = _adapter(cancel_error=RuntimeError("cancel ack missing"))
    _connect(adapter)
    intent = _intent()
    reservation = adapter.reserve(intent)
    adapter.submit(reservation)

    with pytest.raises(IBKRLiveSubmissionUncertain):
        adapter.cancel(intent.order_id)
    assert adapter.halted
    adapter.disconnect()

    fresh, _, _ = _adapter()
    _connect(fresh)
    fresh.gateway_connection_closed(fresh._client, fresh._epoch)
    assert not fresh.connected
    assert fresh.halted
    with pytest.raises(IBKRLiveExecutionError):
        fresh.reserve(_intent())
    fresh.disconnect()


def test_intentional_disconnect_does_not_halt_and_adapter_can_reconnect():
    adapter, _, _ = _adapter()
    _connect(adapter)
    adapter.disconnect()

    assert not adapter.halted
    _connect(adapter)
    assert adapter.connected
    assert not adapter.halted
    adapter.disconnect()


def test_failed_handshake_disconnect_does_not_prevent_a_later_connection():
    adapter, _, _ = _adapter(accounts=())
    with pytest.raises(IBKRLiveExecutionError):
        _connect(adapter)
    assert not adapter.halted

    adapter._gateway_factory = lambda *, sink, epoch: _FakeGateway(sink, epoch)
    _connect(adapter)

    assert adapter.connected
    adapter.disconnect()


def test_partial_fill_cancel_event_reports_remaining_quantity():
    adapter, _, _ = _adapter()
    _connect(adapter)
    intent = _intent(side=Side.SELL, quantity=2)
    reservation = adapter.reserve(intent)
    adapter.submit(reservation)
    adapter.gateway_exec_details(
        adapter._client,
        adapter._epoch,
        SimpleNamespace(symbol="AAPL", secType="STK"),
        SimpleNamespace(
            orderId=reservation.broker_order_id,
            acctNumber=ACCOUNT,
            execId="partial-sell",
            shares=Decimal("1"),
            price=Decimal("199"),
            side="SLD",
            time="20260928 08:00:00",
        ),
    )

    adapter.gateway_error(
        adapter._client,
        adapter._epoch,
        reservation.broker_order_id,
        (1727452800000, 202, "Order cancelled", ""),
    )

    event, = adapter.events()
    assert event.status is OrderStatus.CANCELED
    assert event.filled == Decimal("1")
    assert event.remaining == Decimal("1")
    assert adapter.halted
    assert adapter._reserved_sell_by_symbol["AAPL"] == 0
    adapter.disconnect()


def test_definitive_rejection_releases_sell_reservation():
    adapter, _, _ = _adapter()
    _connect(adapter)
    intent = _intent(side=Side.SELL, quantity=2)
    reservation = adapter.reserve(intent)
    adapter.submit(reservation)

    adapter.gateway_error(
        adapter._client,
        adapter._epoch,
        reservation.broker_order_id,
        (1727452800000, 201, "Order rejected", ""),
    )

    event, = adapter.events()
    assert event.status is OrderStatus.BROKER_REJECTED
    assert adapter._reserved_sell_by_symbol["AAPL"] == 0
    assert not adapter.halted
    next_reservation = adapter.reserve(_intent(side=Side.SELL, quantity=2))
    assert next_reservation.broker_order_id != reservation.broker_order_id
    adapter.disconnect()


@pytest.mark.parametrize(
    ("status", "filled", "remaining"),
    [("Canceled", "0", "0"), ("Filled", "0", "2")],
)
def test_inconsistent_terminal_status_halts_without_releasing_sell_capacity(
    status, filled, remaining
):
    adapter, _, _ = _adapter()
    _connect(adapter)
    reservation = adapter.reserve(_intent(side=Side.SELL, quantity=2))
    adapter.submit(reservation)

    adapter.gateway_order_status(
        adapter._client,
        adapter._epoch,
        (reservation.broker_order_id, status, Decimal(filled), Decimal(remaining)),
    )

    event, = adapter.events()
    assert adapter.halted
    assert event.status is OrderStatus.UNKNOWN
    assert adapter._reserved_sell_by_symbol["AAPL"] == 2
    adapter.disconnect()


def test_ibkr_unset_fill_prices_are_reported_as_missing():
    adapter, _, _ = _adapter()
    _connect(adapter)
    reservation = adapter.reserve(_intent())
    adapter.submit(reservation)

    adapter.gateway_order_status(
        adapter._client,
        adapter._epoch,
        (
            reservation.broker_order_id,
            "Submitted",
            Decimal("0"),
            Decimal("1"),
            1.7976931348623157e308,
            0,
            0,
            1.7976931348623157e308,
        ),
    )

    event, = adapter.events()
    assert event.average_fill_price is None
    assert event.last_fill_price is None
    adapter.disconnect()
