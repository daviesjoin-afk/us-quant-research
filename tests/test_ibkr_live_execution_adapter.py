from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.adapters.ibkr.live_execution import (
    IBKRLiveExecutionAdapter,
    IBKRLiveExecutionError,
    IBKRLiveSubmissionUncertain,
)
from us_quant.trading.domain.live_safety import LiveAccountFingerprint
from us_quant.trading.domain.orders import OrderIntent, OrderStatus, Side

ACCOUNT = "U1234567"
ENDPOINT = "ibkr-live-loopback:127.0.0.1:4001"


class _Repository:
    def __init__(self, maximum: int = 42) -> None:
        self.maximum = maximum

    def max_broker_order_id(self) -> int:
        return self.maximum


class _FakeGateway:
    def __init__(
        self,
        sink,
        epoch: int,
        *,
        accounts: tuple[str, ...] = (ACCOUNT,),
        positions: tuple[tuple[str, Decimal], ...] = (("AAPL", Decimal("2")),),
        submit_error: Exception | None = None,
        cancel_error: Exception | None = None,
    ) -> None:
        self.sink = sink
        self.epoch = epoch
        self.accounts = accounts
        self.positions = positions
        self.submit_error = submit_error
        self.cancel_error = cancel_error
        self.connected = False
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
        for symbol, quantity in self.positions:
            self.sink.gateway_position(
                self,
                self.epoch,
                self.accounts[0],
                SimpleNamespace(symbol=symbol),
                quantity,
                Decimal("100"),
            )
        self.sink.gateway_position_end(self, self.epoch)

    def isConnected(self) -> bool:
        return self.connected

    def disconnect(self) -> None:
        self.connected = False

    def cancelPositions(self) -> None:
        pass

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
) -> OrderIntent:
    return OrderIntent.create(
        session_id="live-session",
        strategy_version_id=strategy,
        signal_symbol=symbol,
        execution_symbol=symbol,
        side=side,
        quantity=quantity,
        limit_price=Decimal("200"),
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


@pytest.mark.parametrize(
    "positions",
    [(("AAPL", Decimal("-1")),), (("AAPL", Decimal("1.5")),)],
)
def test_short_or_fractional_startup_position_fails_closed(positions):
    adapter, _, _ = _adapter(positions=positions)

    with pytest.raises(IBKRLiveExecutionError):
        _connect(adapter)

    assert not adapter.connected


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
        time="20260928  08:00:00 UTC",
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


@pytest.mark.parametrize(
    ("callback_args", "expected_status", "expected_message"),
    [
        ((0, 202, "Order cancelled", ""), OrderStatus.CANCELED, "Order cancelled"),
        ((201, "Order rejected"), OrderStatus.BROKER_REJECTED, "Order rejected"),
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
    fresh.disconnect()\n