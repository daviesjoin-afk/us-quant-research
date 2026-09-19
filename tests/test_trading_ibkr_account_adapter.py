"""IBKR account adapter tests.

The adapter is the boundary where raw IBKR callbacks become domain account
truth, so these tests drive it through a *fake IBKR API* rather than mocking
its internals.  The fake supplies ``ibapi.client.EClient`` and
``ibapi.wrapper.EWrapper``, which the adapter subclasses; the subclass's
``run()`` then delivers the scripted callbacks exactly as the real API
delivers them, and what comes back is a domain portfolio.

That shape matters: it exercises the adapter's real request/wait
choreography, its real callback bodies and its real ``finally`` teardown,
none of which a mock of the adapter could reach.

Three properties matter more than the field mapping and are pinned
explicitly:

* the raw account id is masked before it can reach the domain, and the
  domain type has no field that could carry it;
* a fractional position quantity survives the read -- whole shares are an
  execution policy, not an observation policy;
* the read-only socket is closed on success *and* on every failure path.
"""

from __future__ import annotations

import ast
import importlib.abc
import importlib.machinery
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Event, Thread
from types import ModuleType
from unittest.mock import patch

import pytest

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.adapters.ibkr.account import IBKRAccountAdapter
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
    BrokerPositionSnapshot,
)
from us_quant.trading.domain.common import Environment
from us_quant.trading.ports.broker_account import (
    BrokerAccountUnavailable,
    BrokerAccountValidationError,
)


RAW_ACCOUNT = "DU1234567"
ALIAS = "DU***67"

#: The script the stub ``run()`` replays.  A dict rather than an argument
#: because the adapter constructs the app itself.
_SCRIPT: dict = {}

#: Every fake client the current refresh constructed, so a test can reach the
#: instance the adapter built for itself.
_CREATED_CLIENTS: list["_FakeEClient"] = []


def _config(**overrides) -> IBKRConnectionConfig:
    values = {
        "host": "127.0.0.1",
        "port": 4002,
        "client_id": 17,
        "api_read_only": True,
        "paper_order_submission_enabled": False,
        "connection_timeout_seconds": 2.0,
    }
    values.update(overrides)
    return IBKRConnectionConfig(**values)


class _Contract:
    def __init__(
        self,
        *,
        conId: int = 1,  # noqa: N803 - the vendor's own attribute name
        symbol: str = "AAPL",
        localSymbol: str = "AAPL",  # noqa: N803
        secType: str = "STK",  # noqa: N803
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> None:
        self.conId = conId
        self.symbol = symbol
        self.localSymbol = localSymbol
        self.secType = secType
        self.exchange = exchange
        self.currency = currency


def _script(**overrides) -> dict:
    values = {
        "accounts": RAW_ACCOUNT,
        "metrics": [],
        "positions": [],
        "account_pnl": None,
        "position_pnl": {},
        "errors": [],
        "server_version": 180,
        "connection_time": b"20260724 20:00:00 CST",
        "hang": False,
        "connect_error": None,
        # Real IBKR delivers ``pnlSingle`` asynchronously, after the request
        # call has already returned.  The default fake replies synchronously
        # for brevity in the mapping tests; setting this makes it behave like
        # the real socket, which is what the race regression needs.
        "deferred_position_pnl": False,
    }
    values.update(overrides)
    return values


class _FakeEClient:
    """The stub transport.  ``run`` replays :data:`_SCRIPT`."""

    def __init__(self, wrapper=None) -> None:
        self.wrapper = wrapper
        self.connected = False
        self.disconnected = False
        self.requests: list[tuple[str, tuple]] = []
        self.cancelled: list[tuple[str, int]] = []
        self._script = _SCRIPT
        #: Signalled from the network thread when a deferred ``reqPnLSingle``
        #: has been issued, so the test can prove the adapter is still
        #: waiting rather than having already built the portfolio.
        self.position_pnl_requested = Event()
        #: Released by the test to let the deferred reply through.
        self.release_position_pnl = Event()
        #: Safety valve so a buggy implementation cannot hang the suite.
        self.deferred_release_timeout = 10.0
        _CREATED_CLIENTS.append(self)

    # -- connection -----------------------------------------------------

    def connect(self, host: str, port: int, client_id: int) -> None:
        error = self._script.get("connect_error")
        if error is not None:
            raise error
        self.connected = True

    def isConnected(self) -> bool:  # noqa: N802
        return self.connected

    def disconnect(self) -> None:
        self.disconnected = True
        self.connected = False

    def serverVersion(self) -> int:  # noqa: N802
        return self._script["server_version"]

    def twsConnectionTime(self):  # noqa: N802
        return self._script["connection_time"]

    # -- the scripted session -------------------------------------------

    def run(self) -> None:
        if self._script.get("hang"):
            while not self.disconnected:
                pass
            return
        self.nextValidId(1)
        self.managedAccounts(self._script["accounts"])
        for index, (tag, value, currency) in enumerate(
            self._script["metrics"]
        ):
            self.accountSummary(
                9001 + index,
                self._script["accounts"].split(",")[0],
                tag,
                value,
                currency,
            )
        if not self._script.get("skip_account_summary_end"):
            # A mandatory callback that never arrives, when the flag is set:
            # the read must fail rather than publish a half-populated
            # account.  Guarded here because ``run`` drives the session.
            self.accountSummaryEnd(9001)
        for account, contract, position, avg_cost in self._script[
            "positions"
        ]:
            self.position(account, contract, position, avg_cost)
        self.positionEnd()
        for code, message in self._script["errors"]:
            self.error(-1, 0, code, message)

    # -- requests -------------------------------------------------------

    def reqAccountSummary(self, reqId, group, tags) -> None:  # noqa: N802
        self.requests.append(("reqAccountSummary", (reqId, group, tags)))

    def reqPositions(self) -> None:  # noqa: N802
        self.requests.append(("reqPositions", ()))

    def reqPnL(self, reqId, account, model) -> None:  # noqa: N802
        self.requests.append(("reqPnL", (reqId, account, model)))
        # The reply arrives *after* the request, as it does on a real
        # socket.  Delivering it any earlier would be dropped by the
        # adapter's own request-id bookkeeping, which is exactly the
        # bookkeeping under test.
        pnl = self._script["account_pnl"]
        if pnl is not None:
            self.pnl(reqId, *pnl)

    def reqPnLSingle(  # noqa: N802
        self, reqId, account, model, conId
    ) -> None:
        self.requests.append(
            ("reqPnLSingle", (reqId, account, model, conId))
        )
        reply = self._script["position_pnl"].get(conId)
        if reply is None:
            return
        if not self._script["deferred_position_pnl"]:
            # The simple mode: reply inline, so the mapping tests read as
            # straight-line code.
            self.pnlSingle(reqId, *reply)
            return

        # The realistic mode: the reply comes from another thread, *after*
        # this request method has returned -- exactly what a real socket
        # does, and what the adapter must wait for.
        self.position_pnl_requested.set()

        def deliver() -> None:
            self.release_position_pnl.wait(
                self.deferred_release_timeout
            )
            self.pnlSingle(reqId, *reply)

        Thread(target=deliver, daemon=True).start()

    def cancelAccountSummary(self, reqId) -> None:  # noqa: N802
        self.cancelled.append(("cancelAccountSummary", reqId))

    def cancelPositions(self) -> None:  # noqa: N802
        self.cancelled.append(("cancelPositions", 0))

    def cancelPnL(self, reqId) -> None:  # noqa: N802
        self.cancelled.append(("cancelPnL", reqId))

    def cancelPnLSingle(self, reqId) -> None:  # noqa: N802
        self.cancelled.append(("cancelPnLSingle", reqId))

    # -- callbacks the adapter overrides --------------------------------

    def nextValidId(self, orderId: int) -> None:  # noqa: N802
        pass

    def managedAccounts(self, accountsList: str) -> None:  # noqa: N802
        pass

    def accountSummary(  # noqa: N802
        self, reqId, account, tag, value, currency
    ) -> None:
        pass

    def accountSummaryEnd(self, reqId: int) -> None:  # noqa: N802
        pass

    def position(  # noqa: N802
        self, account, contract, position, avgCost
    ) -> None:
        pass

    def positionEnd(self) -> None:  # noqa: N802
        pass

    def pnl(  # noqa: N802
        self, reqId, dailyPnL, unrealizedPnL, realizedPnL
    ) -> None:
        pass

    def pnlSingle(  # noqa: N802
        self, reqId, pos, dailyPnL, unrealizedPnL, realizedPnL, value
    ) -> None:
        pass

    def error(self, reqId, *args) -> None:  # noqa: N802
        pass


class _FakeEWrapper:
    def __init__(self) -> None:
        pass


def _stub_modules() -> dict[str, ModuleType]:
    client_module = ModuleType("ibapi.client")
    client_module.EClient = _FakeEClient
    wrapper_module = ModuleType("ibapi.wrapper")
    wrapper_module.EWrapper = _FakeEWrapper
    ibapi_module = ModuleType("ibapi")
    return {
        "ibapi": ibapi_module,
        "ibapi.client": client_module,
        "ibapi.wrapper": wrapper_module,
    }


class _BlockIbapi(importlib.abc.MetaPathFinder):
    """Make ``import ibapi`` fail as if the vendor package were absent."""

    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001
        if fullname == "ibapi" or fullname.startswith("ibapi."):
            raise ModuleNotFoundError(
                f"No module named {fullname!r}", name=fullname
            )
        return None


def _refresh(script: dict, *, config=None, timeout=2.0):
    """Run one refresh against ``script`` and return the domain portfolio."""

    _SCRIPT.clear()
    _SCRIPT.update(script)
    _CREATED_CLIENTS.clear()
    adapter = IBKRAccountAdapter(config or _config())
    with patch.dict(sys.modules, _stub_modules()):
        return adapter.refresh(timeout_seconds=timeout)


def _refresh_error(script: dict, *, config=None, timeout=2.0) -> Exception:
    with pytest.raises(Exception) as excinfo:
        _refresh(script, config=config, timeout=timeout)
    return excinfo.value


class _DeferredRefresh:
    """One ``refresh`` running on its own thread against a deferred fake.

    The adapter is driven exactly as production drives it; only the fake's
    reply timing differs.  Holding the refresh on a thread is what lets the
    test observe *when* the portfolio was built relative to the callback.
    """

    def __init__(self, script: dict, *, timeout: float = 5.0) -> None:
        self.script = script
        self.timeout = timeout
        self.result: list = []
        self.error: list[BaseException] = []
        self.done = Event()
        self.thread = Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            self.result.append(_refresh(self.script, timeout=self.timeout))
        except BaseException as error:  # noqa: BLE001 - reported to the test
            self.error.append(error)
        finally:
            self.done.set()

    def start(self) -> "_DeferredRefresh":
        self.thread.start()
        return self

    @property
    def client(self) -> "_FakeEClient":
        assert _CREATED_CLIENTS, "the fake client was not captured"
        return _CREATED_CLIENTS[-1]

    def wait_for_request(self, timeout: float = 5.0) -> None:
        """Block until the fake has issued a deferred ``reqPnLSingle``."""

        assert self.client.position_pnl_requested.wait(timeout), (
            "the adapter never issued reqPnLSingle"
        )

    @property
    def finished(self) -> bool:
        return self.done.is_set()

    def release(self) -> None:
        self.client.release_position_pnl.set()

    def join(self, timeout: float = 10.0) -> None:
        self.thread.join(timeout)


# -- the read-only Paper gate ------------------------------------------


def test_the_live_gateway_port_is_rejected() -> None:
    with pytest.raises(BrokerAccountValidationError) as excinfo:
        IBKRAccountAdapter(_config(port=4001)).refresh(timeout_seconds=2)
    assert "4002" in str(excinfo.value)


def test_a_writable_api_is_rejected() -> None:
    with pytest.raises(BrokerAccountValidationError) as excinfo:
        IBKRAccountAdapter(
            _config(api_read_only=False)
        ).refresh(timeout_seconds=2)
    assert "read-only" in str(excinfo.value)


def test_enabled_order_submission_is_rejected() -> None:
    config = _config(
        api_read_only=False, paper_order_submission_enabled=True
    )
    with pytest.raises(BrokerAccountValidationError):
        IBKRAccountAdapter(config).refresh(timeout_seconds=2)


def test_a_non_positive_timeout_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        IBKRAccountAdapter(_config()).refresh(timeout_seconds=0)


def test_the_missing_ibkr_api_is_unavailable_not_a_validation_error() -> None:
    """A missing vendor package is "cannot read", not "data is wrong"."""

    adapter = IBKRAccountAdapter(_config())
    blocked = {
        name: None
        for name in list(sys.modules)
        if name == "ibapi" or name.startswith("ibapi.")
    }
    finder = _BlockIbapi()
    sys.meta_path.insert(0, finder)
    try:
        with patch.dict(sys.modules, blocked, clear=False):
            for name in blocked:
                sys.modules.pop(name, None)
            with pytest.raises(BrokerAccountUnavailable):
                adapter.refresh(timeout_seconds=1)
    finally:
        sys.meta_path.remove(finder)


def test_a_failed_socket_connection_is_unavailable() -> None:
    from us_quant.ibkr import IBKRClientConnectError

    adapter = IBKRAccountAdapter(_config())
    with patch.dict(sys.modules, _stub_modules()):
        with patch(
            "us_quant.trading.adapters.ibkr.account.connect_ibkr_client",
            side_effect=IBKRClientConnectError("refused"),
        ):
            with pytest.raises(BrokerAccountUnavailable):
                adapter.refresh(timeout_seconds=1)


# -- managed account cardinality ---------------------------------------


def test_zero_managed_accounts_is_refused() -> None:
    assert isinstance(
        _refresh_error(_script(accounts="")),
        BrokerAccountValidationError,
    )


def test_multiple_managed_accounts_are_refused() -> None:
    error = _refresh_error(_script(accounts="DU1111111,DU2222222"))
    assert isinstance(error, BrokerAccountValidationError)
    assert "exactly one" in str(error)


def test_a_non_du_account_is_refused() -> None:
    """The read-only channel is Paper-only, so a live id must fail closed."""

    assert isinstance(
        _refresh_error(_script(accounts="U1234567")),
        BrokerAccountValidationError,
    )


# -- masking -----------------------------------------------------------


def test_the_raw_account_id_never_reaches_the_domain() -> None:
    portfolio = _refresh(
        _script(
            metrics=[("NetLiquidation", "10000", "USD")],
            positions=[(RAW_ACCOUNT, _Contract(), "2", 36.0)],
        )
    )

    assert isinstance(portfolio, BrokerAccountPortfolio)
    assert portfolio.account.account_alias == ALIAS
    assert portfolio.positions[0].account_alias == ALIAS
    # The raw id appears nowhere in the published value.
    assert RAW_ACCOUNT not in repr(portfolio)
    assert RAW_ACCOUNT not in str(portfolio)
    assert not hasattr(portfolio.account, "account_id")


def test_the_alias_is_the_shared_masked_form() -> None:
    from us_quant.trading.adapters.ibkr.support import mask_account_id

    portfolio = _refresh(_script())
    assert portfolio.account.account_alias == mask_account_id(RAW_ACCOUNT)


# -- metric mapping ----------------------------------------------------


def test_account_metrics_map_to_the_domain_fields() -> None:
    account = _refresh(
        _script(
            metrics=[
                ("NetLiquidation", "10000.50", "USD"),
                ("TotalCashValue", "5000.25", "USD"),
                ("AvailableFunds", "4000", "USD"),
                ("BuyingPower", "8000", "USD"),
                ("GrossPositionValue", "2000", "USD"),
                ("ExcessLiquidity", "3000", "USD"),
                ("MaintMarginReq", "1000", "USD"),
                ("Cushion", "0.75", "USD"),
            ]
        )
    ).account

    assert account.net_liquidation == Decimal("10000.50")
    assert account.cash == Decimal("5000.25")
    assert account.available_funds == Decimal("4000")
    assert account.buying_power == Decimal("8000")
    assert account.gross_position_value == Decimal("2000")
    assert account.excess_liquidity == Decimal("3000")
    assert account.maintenance_margin == Decimal("1000")
    assert account.cushion == Decimal("0.75")
    assert account.environment is Environment.PAPER
    assert account.observed_at.tzinfo is not None
    assert account.observed_at.utcoffset() == timezone.utc.utcoffset(None)


def test_an_unreported_metric_is_none_not_zero() -> None:
    """A missing metric must not become a confident ``0``."""

    account = _refresh(
        _script(metrics=[("NetLiquidation", "10000", "USD")])
    ).account

    assert account.net_liquidation == Decimal("10000")
    assert account.cash is None
    assert account.buying_power is None


def test_an_unparseable_metric_is_none() -> None:
    account = _refresh(
        _script(
            metrics=[
                ("NetLiquidation", "not-a-number", "USD"),
                ("TotalCashValue", "", "USD"),
            ]
        )
    ).account

    assert account.net_liquidation is None
    assert account.cash is None


def test_usd_is_preferred_over_a_blank_currency() -> None:
    account = _refresh(
        _script(
            metrics=[
                ("NetLiquidation", "100", ""),
                ("NetLiquidation", "200", "USD"),
            ]
        )
    ).account
    assert account.net_liquidation == Decimal("200")


def test_a_blank_currency_is_used_when_usd_is_absent() -> None:
    account = _refresh(
        _script(metrics=[("NetLiquidation", "100", "")])
    ).account
    assert account.net_liquidation == Decimal("100")


def test_a_foreign_currency_is_not_mistaken_for_usd() -> None:
    """Reading a EUR balance as dollars would misstate the account."""

    account = _refresh(
        _script(metrics=[("NetLiquidation", "100", "EUR")])
    ).account
    assert account.net_liquidation is None


# -- account P&L -------------------------------------------------------


def test_account_pnl_is_taken_from_reqpnl() -> None:
    account = _refresh(
        _script(
            metrics=[("NetLiquidation", "10000", "USD")],
            account_pnl=(12.5, 30.25, -4.0),
        )
    ).account

    assert account.daily_pnl == Decimal("12.5")
    assert account.unrealized_pnl == Decimal("30.25")
    assert account.realized_pnl == Decimal("-4.0")
    assert account.pnl_source == "IBKR reqPnL"


def test_absent_account_pnl_is_reported_as_unavailable() -> None:
    account = _refresh(
        _script(metrics=[("NetLiquidation", "10000", "USD")])
    ).account

    assert account.daily_pnl is None
    assert account.pnl_source == "unavailable"


# -- positions ---------------------------------------------------------


def test_position_quantity_keeps_decimal_precision() -> None:
    """Whole shares are an execution policy, not an observation policy."""

    position = _refresh(
        _script(
            positions=[(RAW_ACCOUNT, _Contract(), "0.5", 100.0)],
            position_pnl={1: (0.5, 1.0, 2.0, 3.0, 50.0)},
        )
    ).positions[0]

    assert position.quantity == Decimal("0.5")
    assert isinstance(position.quantity, Decimal)
    assert position.quantity != 0


def test_an_integral_quantity_is_still_a_decimal() -> None:
    position = _refresh(
        _script(positions=[(RAW_ACCOUNT, _Contract(), "2", 36.0)])
    ).positions[0]

    assert position.quantity == Decimal("2")
    assert isinstance(position.quantity, Decimal)


def test_position_pnl_and_market_value_come_from_reqpnsingle() -> None:
    position = _refresh(
        _script(
            positions=[(RAW_ACCOUNT, _Contract(), "2", 36.0)],
            position_pnl={1: (2, 5.5, 7.25, -1.5, 72.0)},
        )
    ).positions[0]

    assert position.market_value == Decimal("72.0")
    assert position.daily_pnl == Decimal("5.5")
    assert position.unrealized_pnl == Decimal("7.25")
    assert position.realized_pnl == Decimal("-1.5")
    # The broker's own mark, derived rather than quoted.
    assert position.mark == Decimal("36.0")


def test_a_position_without_pnl_has_none_not_zero() -> None:
    position = _refresh(
        _script(positions=[(RAW_ACCOUNT, _Contract(), "2", 36.0)])
    ).positions[0]

    assert position.market_value is None
    assert position.daily_pnl is None
    assert position.mark is None


def test_position_identity_fields_are_carried_through() -> None:
    contract = _Contract(
        conId=42,
        symbol="MSFT",
        localSymbol="MSFT",
        secType="STK",
        exchange="NASDAQ",
        currency="USD",
    )
    position = _refresh(
        _script(positions=[(RAW_ACCOUNT, contract, "3", 250.0)])
    ).positions[0]

    assert position.con_id == 42
    assert position.symbol == "MSFT"
    assert position.local_symbol == "MSFT"
    assert position.security_type == "STK"
    assert position.exchange == "NASDAQ"
    assert position.currency == "USD"
    assert position.average_cost == Decimal("250.0")


def test_a_flat_position_is_not_reported() -> None:
    """The broker keeps closed contracts at zero; they are not holdings."""

    positions = _refresh(
        _script(
            positions=[
                (RAW_ACCOUNT, _Contract(symbol="AAPL"), "2", 36.0),
                (RAW_ACCOUNT, _Contract(conId=9, symbol="QQQ"), "0", 400.0),
            ]
        )
    ).positions

    assert [row.symbol for row in positions] == ["AAPL"]


def test_a_position_from_another_account_is_ignored() -> None:
    """Only the single managed account's positions may be published."""

    positions = _refresh(
        _script(
            positions=[
                (RAW_ACCOUNT, _Contract(symbol="AAPL"), "2", 36.0),
                ("DU9999999", _Contract(conId=9, symbol="QQQ"), "5", 400.0),
            ]
        )
    ).positions

    assert [row.symbol for row in positions] == ["AAPL"]


def test_a_foreign_account_metric_is_ignored() -> None:
    account = _refresh(
        _script(metrics=[("NetLiquidation", "10000", "USD")])
    ).account
    assert account.net_liquidation == Decimal("10000")


# -- diagnostics -------------------------------------------------------


def test_ibkr_messages_become_domain_diagnostics() -> None:
    portfolio = _refresh(
        _script(errors=[(2104, "market data farm connection is OK")])
    )

    assert len(portfolio.diagnostics) == 1
    diagnostic = portfolio.diagnostics[0]
    assert diagnostic.code == 2104
    assert diagnostic.message == "market data farm connection is OK"
    assert diagnostic.informational is True


def test_a_real_error_is_not_marked_informational() -> None:
    diagnostic = _refresh(
        _script(errors=[(200, "no such contract")])
    ).diagnostics[0]

    assert diagnostic.informational is False


def test_diagnostics_default_to_empty() -> None:
    portfolio = _refresh(_script())
    assert portfolio.diagnostics == ()


def test_the_portfolio_shape_is_account_positions_diagnostics() -> None:
    assert set(BrokerAccountPortfolio.__dataclass_fields__) == {
        "account",
        "positions",
        "diagnostics",
    }
    assert RAW_ACCOUNT not in repr(_refresh(_script()).diagnostics)


# -- lifecycle ---------------------------------------------------------


def test_the_socket_is_closed_on_success() -> None:
    """A leaked account socket would hold a gateway client id."""

    observed: dict = {}
    original = _FakeEClient.disconnect

    def record(self):
        observed["disconnected"] = True
        original(self)

    with patch.object(_FakeEClient, "disconnect", record):
        _refresh(_script(metrics=[("NetLiquidation", "1", "USD")]))

    assert observed.get("disconnected") is True


def test_the_requests_are_cancelled_before_disconnecting() -> None:
    cancelled: list[tuple[str, int]] = []
    original = _FakeEClient.cancelAccountSummary

    def record(self, reqId):
        cancelled.append(("cancelAccountSummary", reqId))
        original(self, reqId)

    with patch.object(_FakeEClient, "cancelAccountSummary", record):
        _refresh(_script(metrics=[("NetLiquidation", "1", "USD")]))

    assert ("cancelAccountSummary", 9001) in cancelled


def test_the_socket_is_closed_on_a_validation_failure() -> None:
    """A refused read must still release the socket."""

    observed: dict = {}
    original = _FakeEClient.disconnect

    def record(self):
        observed["disconnected"] = True
        original(self)

    with patch.object(_FakeEClient, "disconnect", record):
        _refresh_error(_script(accounts="DU1111111,DU2222222"))

    assert observed.get("disconnected") is True


def test_the_socket_is_closed_on_a_timeout() -> None:
    observed: dict = {}
    original = _FakeEClient.disconnect

    def record(self):
        observed["disconnected"] = True
        original(self)

    with patch.object(_FakeEClient, "disconnect", record):
        error = _refresh_error(_script(hang=True), timeout=0.2)

    assert isinstance(error, BrokerAccountUnavailable)
    assert observed.get("disconnected") is True


# -- the account path never touches market data ------------------------


def test_the_adapter_requests_no_market_data() -> None:
    """The structural guarantee, checked on the request log.

    An account refresh that subscribed to quotes is how the v1 code came to
    own market readiness; Market Data v2 owns it now.
    """

    observed: dict = {}
    original = _FakeEClient.reqPositions

    def record(self):
        # Snapshot *after* the adapter has issued every request: the
        # position P&L requests are issued after this one returns.
        observed["client"] = self
        original(self)

    with patch.object(_FakeEClient, "reqPositions", record):
        _refresh(
            _script(
                metrics=[("NetLiquidation", "1", "USD")],
                positions=[(RAW_ACCOUNT, _Contract(), "2", 36.0)],
            )
        )

    client = observed["client"]
    names = {name for name, _ in client.requests}
    assert names == {
        "reqAccountSummary",
        "reqPositions",
        "reqPnL",
        "reqPnLSingle",
    }


def test_the_adapter_module_has_no_market_data_surface() -> None:
    """Belt and braces on the source, so the log cannot be the only guard."""

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src"
        / "us_quant"
        / "trading"
        / "adapters"
        / "ibkr"
        / "account.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    } | {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    for forbidden in (
        "reqMktData",
        "reqMarketDataType",
        "cancelMktData",
        "reqContractDetails",
        "tickPrice",
        "marketDataType",
    ):
        assert forbidden not in names


# -- domain construction invariants ------------------------------------


def _position(**overrides) -> BrokerPositionSnapshot:
    values = {
        "account_alias": ALIAS,
        "con_id": 1,
        "symbol": "AAPL",
        "local_symbol": "AAPL",
        "security_type": "STK",
        "exchange": "SMART",
        "currency": "USD",
        "quantity": Decimal("0.5"),
        "average_cost": Decimal("100"),
        "market_value": Decimal("50"),
        "daily_pnl": None,
        "unrealized_pnl": None,
        "realized_pnl": None,
        "observed_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return BrokerPositionSnapshot(**values)


def test_a_fractional_position_is_a_valid_domain_value() -> None:
    """Constructed directly: the domain must not reject honest quantity."""

    position = _position()

    assert position.quantity == Decimal("0.5")
    assert position.cost_basis == Decimal("50.0")
    assert position.mark == Decimal("100")
    assert position.local_unrealized_pnl == Decimal("0.0")


def test_the_broker_account_snapshot_has_no_raw_id_field() -> None:
    fields = set(BrokerAccountSnapshot.__dataclass_fields__)
    for forbidden in (
        "account_id",
        "account_number",
        "raw_account",
        "managed_account",
    ):
        assert forbidden not in fields
    assert "account_alias" in fields


def test_the_broker_position_snapshot_has_no_risk_fields() -> None:
    """Risk projections belong to Risk/Application, not to broker facts."""

    fields = set(BrokerPositionSnapshot.__dataclass_fields__)
    for forbidden in (
        "risk_multiplier",
        "risk_exposure",
        "max_position_fraction",
    ):
        assert forbidden not in fields


def test_mark_is_none_without_a_market_value() -> None:
    position = _position(market_value=None)

    assert position.mark is None
    assert position.local_unrealized_pnl is None


def test_mark_is_none_for_a_flat_position() -> None:
    """A mark is undefined without a position to divide by."""

    position = _position(quantity=Decimal("0"), market_value=Decimal("10"))
    assert position.mark is None


def test_the_domain_types_are_frozen_and_slotted() -> None:
    for dataclass_type in (
        BrokerAccountSnapshot,
        BrokerPositionSnapshot,
        BrokerAccountPortfolio,
    ):
        assert dataclass_type.__dataclass_params__.frozen is True
        assert dataclass_type.__slots__ != ()

# -- the async position P&L race ---------------------------------------
#
# Real IBKR delivers ``pnlSingle`` on its network thread, after
# ``reqPnLSingle`` has already returned.  The fake above replies inline by
# default, which is convenient for the mapping tests but hides exactly this
# timing.  These tests use the deferred mode so the adapter has to wait for a
# callback that genuinely has not happened yet.


def _deferred_script(**overrides) -> dict:
    values = {
        "deferred_position_pnl": True,
        "metrics": [("NetLiquidation", "10000", "USD")],
        "positions": [(RAW_ACCOUNT, _Contract(), "2", 36.0)],
        "position_pnl": {1: (2, 5.5, 7.25, -1.5, 72.0)},
        # The account P&L reply arrives promptly -- that is the realistic
        # race: the account callback is back while the position callback is
        # still outstanding.  Leaving this ``None`` would let a buggy
        # implementation hide behind its own wait on the account event, and
        # the regression below would pass on the broken code.
        "account_pnl": (12.5, 30.25, -4.0),
    }
    values.update(overrides)
    return _script(**values)


def test_a_deferred_position_pnl_reply_is_waited_for() -> None:
    """The regression: the portfolio must not be built before the callback.

    The assertion that makes this non-vacuous is the *negative* one below.
    The callback is withheld, so a correct adapter is still blocked on it,
    while the previous implementation returned as soon as the mandatory
    callbacks landed.  Checking the result alone would not catch that: the
    released callback can land between the broken return and the assertion,
    which is a race that passes by luck.

    On the pre-fix implementation this test fails, deterministically.
    """

    refresh = _DeferredRefresh(_deferred_script()).start()
    try:
        refresh.wait_for_request()
        # The reply is being withheld.  A correct adapter is still inside
        # ``refresh``; the broken one has already returned with ``None``
        # P&L.  The window is generous because the difference is not close:
        # the broken path needs microseconds, the correct one is blocked for
        # the whole grace period.
        completed_without_waiting = refresh.done.wait(0.5)
        assert completed_without_waiting is False, (
            "the adapter built the portfolio without waiting for pnlSingle"
        )
        refresh.release()
    finally:
        refresh.release()
        refresh.join()

    assert refresh.error == []
    assert len(refresh.result) == 1
    portfolio = refresh.result[0]
    position = portfolio.positions[0]

    # The evidence the callback carried is present, which is only possible
    # if the adapter actually waited.
    assert position.market_value == Decimal("72.0")
    assert position.daily_pnl == Decimal("5.5")
    assert position.unrealized_pnl == Decimal("7.25")
    assert position.realized_pnl == Decimal("-1.5")
    assert position.mark == Decimal("36.0")


def test_the_deferred_reply_is_not_required_to_be_fast() -> None:
    """A reply that arrives well after the request still lands.

    The callback is released only once the test has confirmed the adapter is
    still waiting, so the data can only be present if the wait happened.
    """

    refresh = _DeferredRefresh(_deferred_script()).start()
    try:
        refresh.wait_for_request()
        assert refresh.done.wait(0.5) is False
        # Some unrelated work while the reply is still withheld.
        for _ in range(1000):
            pass
        assert refresh.finished is False
        refresh.release()
    finally:
        refresh.release()
        refresh.join()

    assert refresh.result[0].positions[0].market_value == Decimal("72.0")


def test_a_deferred_reply_that_never_arrives_leaves_none() -> None:
    """Optional evidence may be absent; the read must still succeed."""

    script = _deferred_script(
        positions=[
            (RAW_ACCOUNT, _Contract(symbol="AAPL"), "2", 36.0),
            (RAW_ACCOUNT, _Contract(conId=9, symbol="MSFT"), "3", 250.0),
        ],
        position_pnl={},
    )
    refresh = _DeferredRefresh(script, timeout=3.0).start()
    refresh.join()

    assert refresh.error == [], "an absent P&L must not fail the refresh"
    portfolio = refresh.result[0]
    assert portfolio.account.net_liquidation == Decimal("10000")
    assert len(portfolio.positions) == 2
    for position in portfolio.positions:
        assert position.market_value is None
        assert position.daily_pnl is None
        assert position.mark is None


def test_no_pnl_callback_at_all_still_returns_a_portfolio() -> None:
    """No ``reqPnL`` and no ``reqPnLSingle`` reply: still a valid read."""

    script = _script(
        metrics=[("NetLiquidation", "10000", "USD")],
        positions=[(RAW_ACCOUNT, _Contract(), "2", 36.0)],
        account_pnl=None,
        position_pnl={},
    )
    portfolio = _refresh(script)

    assert isinstance(portfolio, BrokerAccountPortfolio)
    assert portfolio.account.net_liquidation == Decimal("10000")
    assert portfolio.account.daily_pnl is None
    assert portfolio.account.pnl_source == "unavailable"
    assert portfolio.positions[0].market_value is None


def test_an_optional_pnl_timeout_does_not_raise() -> None:
    """The optional window expiring is not a refresh failure."""

    script = _script(
        metrics=[("NetLiquidation", "10000", "USD")],
        positions=[(RAW_ACCOUNT, _Contract(), "2", 36.0)],
        position_pnl={},
    )
    # No exception: this would raise BrokerAccountUnavailable if the
    # optional wait had been wired through the mandatory ``_wait_for``.
    portfolio = _refresh(script, timeout=1.0)

    assert portfolio.positions[0].market_value is None


def test_the_optional_wait_never_exceeds_the_global_timeout() -> None:
    """The grace period is a *ceiling*, not an addition.

    With ``timeout_seconds=0.5`` the optional window must be clamped to the
    global deadline; otherwise a short timeout would silently become
    ``0.5 + grace``.
    """

    from time import monotonic

    from us_quant.trading.adapters.ibkr.account import (
        OPTIONAL_PNL_GRACE_SECONDS,
    )

    script = _script(
        metrics=[("NetLiquidation", "10000", "USD")],
        positions=[(RAW_ACCOUNT, _Contract(), "2", 36.0)],
        position_pnl={},
    )
    started = monotonic()
    _refresh(script, timeout=0.5)
    elapsed = monotonic() - started

    assert elapsed < 0.5 + OPTIONAL_PNL_GRACE_SECONDS, (
        "the optional grace period was added on top of the global timeout"
    )


def test_many_positions_share_one_grace_period() -> None:
    """The wait is bounded by the window, not by the position count.

    Ten silent positions must not cost ten grace periods.
    """

    from time import monotonic

    from us_quant.trading.adapters.ibkr.account import (
        OPTIONAL_PNL_GRACE_SECONDS,
    )

    positions = [
        (RAW_ACCOUNT, _Contract(conId=index, symbol=f"S{index}"), "1", 10.0)
        for index in range(10)
    ]
    script = _script(
        metrics=[("NetLiquidation", "10000", "USD")],
        positions=positions,
        position_pnl={},
    )
    started = monotonic()
    portfolio = _refresh(script, timeout=30.0)
    elapsed = monotonic() - started

    assert len(portfolio.positions) == 10
    assert elapsed < OPTIONAL_PNL_GRACE_SECONDS * 2, (
        f"ten silent positions cost {elapsed:.2f}s; the window is not shared"
    )


def test_mandatory_and_optional_timeouts_are_distinct() -> None:
    """The two classes of callback fail differently, on purpose.

    A missing *mandatory* callback is an unavailable account.  A missing
    *optional* one is ``None`` fields on an otherwise valid portfolio.
    """

    # Mandatory: the account summary never completes.
    with pytest.raises(BrokerAccountUnavailable):
        _refresh(
            _script(
                metrics=[("NetLiquidation", "10000", "USD")],
                skip_account_summary_end=True,
            ),
            timeout=0.3,
        )

    # Optional: no P&L at all, still a portfolio.
    portfolio = _refresh(
        _script(
            metrics=[("NetLiquidation", "10000", "USD")],
            positions=[(RAW_ACCOUNT, _Contract(), "2", 36.0)],
            position_pnl={},
        ),
        timeout=1.0,
    )
    assert portfolio.account.daily_pnl is None


def test_the_teardown_cancels_and_disconnects_after_a_deferred_reply() -> None:
    """Whatever the callbacks did, the socket is released."""

    refresh = _DeferredRefresh(_deferred_script()).start()
    try:
        refresh.wait_for_request()
        refresh.release()
    finally:
        refresh.release()
        refresh.join()

    client = refresh.client
    names = [name for name, _ in client.cancelled]
    assert "cancelAccountSummary" in names
    assert "cancelPositions" in names
    assert "cancelPnL" in names
    assert "cancelPnLSingle" in names
    assert client.disconnected is True


def test_the_pnl_single_event_is_registered_before_the_request() -> None:
    """Otherwise an early reply would be dropped and burn the grace period.

    Asserted structurally on the source: the ``position_pnl_events[...]``
    assignment must appear before the ``app.reqPnLSingle(...)`` call.
    """

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src"
        / "us_quant"
        / "trading"
        / "adapters"
        / "ibkr"
        / "account.py"
    )
    text = path.read_text(encoding="utf-8")
    registration = text.index("position_pnl_events[request_id] = Event()")
    request = text.index("app.reqPnLSingle(")
    assert registration < request, (
        "the event must be registered before the request is issued"
    )


def test_the_callback_stores_before_it_signals() -> None:
    """The waiter reads the stored row the moment the event is set."""

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src"
        / "us_quant"
        / "trading"
        / "adapters"
        / "ibkr"
        / "account.py"
    )
    text = path.read_text(encoding="utf-8")
    store = text.index("self.position_pnl[reqId] = _RawPositionPnl(")
    signal = text.index("event.set()", store)
    assert store < signal, "the event must be set after the data is stored"


def test_the_optional_helper_never_raises() -> None:
    """It returns on the deadline instead of raising."""

    from threading import Event

    from us_quant.trading.adapters.ibkr.account import (
        _wait_for_optional_events,
    )

    never = Event()
    _wait_for_optional_events((never,), deadline=0.0)  # returns immediately
    _wait_for_optional_events((), deadline=1.0)
    _wait_for_optional_events((never,), deadline=-1.0)


# -- the timezone contract on the broker domain ------------------------


def test_the_broker_account_snapshot_rejects_a_naive_timestamp() -> None:
    """A naive ``observed_at`` must not be silently promoted to UTC.

    Guessing a timezone is how a stale account would come to look fresh at
    the preflight's 300-second gate.
    """

    naive = datetime(2026, 9, 19, 10, 0)
    with pytest.raises(ValueError) as excinfo:
        BrokerAccountSnapshot(
            environment=Environment.PAPER,
            account_alias=ALIAS,
            net_liquidation=Decimal("1"),
            cash=None,
            available_funds=None,
            buying_power=None,
            gross_position_value=None,
            excess_liquidity=None,
            maintenance_margin=None,
            cushion=None,
            daily_pnl=None,
            unrealized_pnl=None,
            realized_pnl=None,
            observed_at=naive,
            pnl_source="test",
        )
    assert "timezone-aware" in str(excinfo.value)


def test_the_broker_position_snapshot_rejects_a_naive_timestamp() -> None:
    with pytest.raises(ValueError) as excinfo:
        _position(observed_at=datetime(2026, 9, 19, 10, 0))
    assert "timezone-aware" in str(excinfo.value)


def test_an_aware_utc_timestamp_is_accepted() -> None:
    account = BrokerAccountSnapshot(
        environment=Environment.PAPER,
        account_alias=ALIAS,
        net_liquidation=Decimal("1"),
        cash=None,
        available_funds=None,
        buying_power=None,
        gross_position_value=None,
        excess_liquidity=None,
        maintenance_margin=None,
        cushion=None,
        daily_pnl=None,
        unrealized_pnl=None,
        realized_pnl=None,
        observed_at=datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc),
        pnl_source="test",
    )
    assert account.observed_at.tzinfo is timezone.utc


def test_an_aware_non_utc_timestamp_is_accepted() -> None:
    """The contract is *aware*, not *UTC-only*.

    A broker may report in a local offset; the domain keeps it as given and
    the consumer converts.
    """

    offset = timezone(timedelta(hours=8))
    account = BrokerAccountSnapshot(
        environment=Environment.PAPER,
        account_alias=ALIAS,
        net_liquidation=Decimal("1"),
        cash=None,
        available_funds=None,
        buying_power=None,
        gross_position_value=None,
        excess_liquidity=None,
        maintenance_margin=None,
        cushion=None,
        daily_pnl=None,
        unrealized_pnl=None,
        realized_pnl=None,
        observed_at=datetime(2026, 9, 19, 10, 0, tzinfo=offset),
        pnl_source="test",
    )
    assert account.observed_at.utcoffset() == timedelta(hours=8)

    position = _position(observed_at=datetime(2026, 9, 19, 10, 0, tzinfo=offset))
    assert position.observed_at.utcoffset() == timedelta(hours=8)


def test_the_adapter_produces_an_aware_timestamp() -> None:
    """The production path is unaffected by the new strictness."""

    portfolio = _refresh(
        _script(metrics=[("NetLiquidation", "10000", "USD")])
    )
    assert portfolio.account.observed_at.tzinfo is not None
    assert portfolio.account.observed_at.utcoffset() is not None


def test_the_preflight_fails_closed_on_a_naive_timestamp() -> None:
    """``_age_seconds`` must not guess UTC."""

    from us_quant.targeted_preflight import _age_seconds

    now = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)
    assert _age_seconds(datetime(2026, 9, 19, 10, 0), now) is None
    assert _age_seconds(None, now) is None
    assert _age_seconds("2026-09-19T10:00:00+00:00", now) is None
    # An aware value still ages normally, including a non-UTC one.
    assert _age_seconds(
        datetime(2026, 9, 19, 9, 59, 55, tzinfo=timezone.utc), now
    ) == Decimal("5.0")


def test_the_preflight_still_enforces_the_300_second_rule() -> None:
    """The rule is unchanged: 0 <= age <= 300 passes, beyond fails."""

    from us_quant.targeted_preflight import MAXIMUM_ACCOUNT_AGE_SECONDS

    assert MAXIMUM_ACCOUNT_AGE_SECONDS == Decimal("300")
