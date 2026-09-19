"""IBKR read-only account adapter.

This is where ``us_quant.ibkr_readonly`` went.  It keeps the part of that
module which was genuinely about the *account* -- the account summary,
positions, and account/position P&L -- and drops everything that was not:

* **no market data.**  The v1 collector called ``reqMarketDataType`` and
  ``reqMktData``, parsed ``tickPrice``/``marketDataType`` callbacks and even
  ran a SPY/QQQ readiness check.  All of that belongs to Market Data v2, so
  none of it is here.  The account path asks the broker about the account and
  nothing else.  There is a structural guard on this file in the test suite.
* **no contract details.**  ``reqContractDetails`` existed only to resolve a
  contract for the market-data requests above.  The position callback already
  supplies ``conId``/``symbol``/``localSymbol``/``secType``/``exchange``/
  ``currency``, so the account path never needed it.
* **no raw account id above this file.**  The unmasked id is used for
  ``reqPnL``/``reqPnLSingle`` because the API requires it, and is masked by
  :func:`mask_account_id` before any domain object is built.  The domain type
  has no field for the raw id, so it cannot leak even by accident.

Per-position market value comes from ``reqPnLSingle``'s ``value`` field --
the broker's own valuation of the position.  The v1 code instead derived a
mark from a market-data quote, which is exactly the account/market-data
coupling this migration removes.  Broker-reported P&L is likewise taken from
``reqPnL``/``reqPnLSingle`` and never estimated locally.

The read is one-shot: connect, read, disconnect.  There is no persistent
account socket and no background reconnect loop, because the implementation
has none -- the port says what this actually does.  A durable account session
belongs to a future Trading Runtime / Broker Session Manager.

Vendor errors are translated at this boundary.  Callers see
``BrokerAccountUnavailable`` / ``BrokerAccountValidationError`` and never an
IBKR exception name.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from threading import Event, Thread
from time import monotonic
from typing import Any

from us_quant.ibkr import (
    IBKRClientConnectError,
    IBKRConnectionConfig,
    connect_ibkr_client,
)
from us_quant.trading.adapters.ibkr.support import (
    ACCOUNT_SUMMARY_TAGS,
    IBKRAPIUnavailable,
    IBKRReadOnlyError,
    INFORMATIONAL_ERROR_CODES,
    ensure_readonly_paper_config,
    mask_account_id,
    optional_decimal,
)
from us_quant.trading.domain.account import (
    BrokerAccountPortfolio,
    BrokerAccountSnapshot,
    BrokerDiagnostic,
    BrokerPositionSnapshot,
)
from us_quant.trading.domain.common import Environment
from us_quant.trading.ports.broker_account import (
    BrokerAccountUnavailable,
    BrokerAccountValidationError,
)


#: Shared grace window for the *optional* P&L replies (``reqPnL`` and every
#: ``reqPnLSingle``).  It is one window for all of them, not one per request:
#: a per-request wait would make a many-position account slower in proportion
#: to its holdings.  Whatever has not arrived by then is reported as ``None``
#: -- an absent P&L never fails an account read, because the balances and
#: positions are already valid broker truth.
OPTIONAL_PNL_GRACE_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class _AccountMetric:
    """One ``accountSummary`` row, raw and unparsed.

    Adapter-private: the ``value`` is still the vendor's string, and the
    ``account`` is still the raw id.  Neither may leave this module.
    """

    account: str
    tag: str
    value: str
    currency: str


@dataclass(frozen=True, slots=True)
class _RawBrokerPosition:
    """One ``position`` callback row, before masking or P&L attachment."""

    account: str
    con_id: int
    symbol: str
    local_symbol: str
    security_type: str
    exchange: str
    currency: str
    quantity: Decimal
    average_cost: Decimal


@dataclass(frozen=True, slots=True)
class _RawAccountPnl:
    """One ``pnl`` callback row."""

    account: str
    daily_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class _RawPositionPnl:
    """One ``pnlSingle`` callback row.

    ``market_value`` is IBKR's ``value`` argument: the broker's own
    valuation of the position.
    """

    account: str
    con_id: int
    quantity: Decimal | None
    daily_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None
    market_value: Decimal | None


@dataclass(frozen=True, slots=True)
class _BrokerMessage:
    """One ``error`` callback row, kept as a diagnostic."""

    code: int
    message: str


class IBKRAccountAdapter:
    """Reads IBKR Paper account truth once, over a read-only connection."""

    def __init__(self, config: IBKRConnectionConfig) -> None:
        self.config = config

    def refresh(
        self,
        *,
        timeout_seconds: float,
    ) -> BrokerAccountPortfolio:
        """Connect, read the account, disconnect, and return domain truth.

        Fails closed on every unacceptable condition -- bad config, missing
        API, failed connection, a managed-account count other than one, or a
        non-``DU`` account -- by raising a provider-neutral error and
        publishing nothing.
        """

        if timeout_seconds <= 0:
            raise ValueError("account refresh timeout must be positive")
        _ensure_paper_readonly(self.config)

        try:
            from ibapi.client import EClient
            from ibapi.wrapper import EWrapper
        except ModuleNotFoundError as error:
            raise BrokerAccountUnavailable(
                "official IBKR Python API is not installed"
            ) from error

        handshake_complete = Event()
        accounts_ready = Event()
        account_summary_complete = Event()
        positions_complete = Event()
        account_pnl_event = Event()

        class AccountApp(EWrapper, EClient):
            def __init__(self) -> None:
                EWrapper.__init__(self)
                EClient.__init__(self, self)
                self.accounts: list[str] = []
                self.metrics: list[_AccountMetric] = []
                self.positions: list[_RawBrokerPosition] = []
                self.messages: list[_BrokerMessage] = []
                self.account_pnl: dict[int, _RawAccountPnl] = {}
                self.position_pnl: dict[int, _RawPositionPnl] = {}

            def nextValidId(self, orderId: int) -> None:
                del orderId
                handshake_complete.set()

            def managedAccounts(self, accountsList: str) -> None:
                self.accounts = [
                    account.strip()
                    for account in accountsList.split(",")
                    if account.strip()
                ]
                accounts_ready.set()

            def accountSummary(
                self,
                reqId: int,
                account: str,
                tag: str,
                value: str,
                currency: str,
            ) -> None:
                del reqId
                self.metrics.append(
                    _AccountMetric(
                        account=account,
                        tag=tag,
                        value=value,
                        currency=currency,
                    )
                )

            def accountSummaryEnd(self, reqId: int) -> None:
                del reqId
                account_summary_complete.set()

            def position(
                self,
                account: str,
                contract: Any,
                position: Any,
                avgCost: float,
            ) -> None:
                self.positions.append(
                    _RawBrokerPosition(
                        account=account,
                        con_id=int(contract.conId),
                        symbol=contract.symbol,
                        local_symbol=contract.localSymbol,
                        security_type=contract.secType,
                        exchange=contract.exchange,
                        currency=contract.currency,
                        # ``Decimal(str(...))``, never ``int(...)``: a
                        # fractional position the broker reports is real
                        # account truth and must survive the read.
                        quantity=Decimal(str(position)),
                        average_cost=Decimal(str(avgCost)),
                    )
                )

            def positionEnd(self) -> None:
                positions_complete.set()

            def pnl(
                self,
                reqId: int,
                dailyPnL: float,
                unrealizedPnL: float,
                realizedPnL: float,
            ) -> None:
                account = self.accounts[0] if self.accounts else ""
                self.account_pnl[reqId] = _RawAccountPnl(
                    account=account,
                    daily_pnl=optional_decimal(dailyPnL),
                    unrealized_pnl=optional_decimal(unrealizedPnL),
                    realized_pnl=optional_decimal(realizedPnL),
                )
                account_pnl_event.set()

            def pnlSingle(
                self,
                reqId: int,
                pos: Any,
                dailyPnL: float,
                unrealizedPnL: float,
                realizedPnL: float,
                value: float,
            ) -> None:
                request = position_pnl_requests.get(reqId)
                if request is None:
                    return
                account, con_id = request
                self.position_pnl[reqId] = _RawPositionPnl(
                    account=account,
                    con_id=con_id,
                    quantity=optional_decimal(pos),
                    daily_pnl=optional_decimal(dailyPnL),
                    unrealized_pnl=optional_decimal(unrealizedPnL),
                    realized_pnl=optional_decimal(realizedPnL),
                    market_value=optional_decimal(value),
                )
                # Store first, signal second.  The waiting thread reads
                # ``self.position_pnl`` the moment the event is set, so
                # setting it before the data is visible would let the read
                # race the write and observe a missing row.
                event = position_pnl_events.get(reqId)
                if event is not None:
                    event.set()

            def error(
                self,
                reqId: int,
                *args: Any,
            ) -> None:
                if len(args) >= 3:
                    _, error_code, error_string, *_ = args
                elif len(args) == 2:
                    error_code, error_string = args
                else:
                    return
                del reqId
                self.messages.append(
                    _BrokerMessage(
                        code=int(error_code),
                        message=str(error_string),
                    )
                )

        app = AccountApp()
        network_thread: Thread | None = None
        account_request_id = 9001
        account_pnl_request_id = 9300
        position_pnl_requests: dict[int, tuple[str, int]] = {}
        #: One event per outstanding ``reqPnLSingle``, set by the matching
        #: ``pnlSingle`` callback.  Populated before each request is issued
        #: so an early reply cannot be missed.
        position_pnl_events: dict[int, Event] = {}
        try:
            try:
                connect_ibkr_client(app, self.config)
            except IBKRClientConnectError as error:
                raise BrokerAccountUnavailable(str(error)) from error

            network_thread = Thread(
                target=app.run,
                name="ibkr-account-network",
                daemon=True,
            )
            network_thread.start()
            deadline = monotonic() + timeout_seconds

            _wait_for(
                handshake_complete,
                deadline,
                "IBKR protocol handshake",
            )
            _wait_for(accounts_ready, deadline, "managed accounts")

            raw_account = _single_managed_account(app.accounts)

            app.reqAccountSummary(
                account_request_id,
                "All",
                ACCOUNT_SUMMARY_TAGS,
            )
            app.reqPositions()
            app.reqPnL(account_pnl_request_id, raw_account, "")

            _wait_for(
                account_summary_complete,
                deadline,
                "account summary",
            )
            _wait_for(positions_complete, deadline, "positions")

            for position in app.positions:
                if position.account != raw_account:
                    continue
                request_id = 9400 + len(position_pnl_requests)
                position_pnl_requests[request_id] = (
                    position.account,
                    position.con_id,
                )
                # Register the event *before* issuing the request.  The
                # reply is asynchronous, so a callback that arrives before
                # this line would find no event to set and the wait below
                # would then burn the whole grace period on a row that is
                # already present.
                position_pnl_events[request_id] = Event()
                app.reqPnLSingle(
                    request_id,
                    position.account,
                    "",
                    position.con_id,
                )

            # Account P&L and position P&L are *optional evidence*: the
            # balances and positions above are already valid broker truth.
            # They share one bounded grace window, so a slow reply costs a
            # fixed wait rather than one grace period per position.  A
            # missing reply leaves ``None`` -- never a failure, and never a
            # guessed number.
            optional_deadline = min(
                deadline,
                monotonic() + OPTIONAL_PNL_GRACE_SECONDS,
            )
            _wait_for_optional_events(
                (
                    account_pnl_event,
                    *position_pnl_events.values(),
                ),
                deadline=optional_deadline,
            )

            return _to_portfolio(
                raw_account=raw_account,
                metrics=app.metrics,
                positions=app.positions,
                account_pnl=app.account_pnl,
                position_pnl=app.position_pnl,
                messages=app.messages,
            )
        finally:
            # Whatever happened -- success, timeout, callback error or a
            # validation refusal -- the read-only socket is closed and the
            # network thread is joined.  A leaked account socket would hold a
            # gateway client id and block the next refresh.
            _close_account_connection(
                app,
                account_request_id=account_request_id,
                account_pnl_request_id=account_pnl_request_id,
                position_pnl_requests=position_pnl_requests,
            )
            if network_thread is not None:
                network_thread.join(timeout=2)


def _ensure_paper_readonly(config: IBKRConnectionConfig) -> None:
    """Apply the shared read-only Paper gate, translated to a port error."""

    try:
        ensure_readonly_paper_config(config)
    except IBKRReadOnlyError as error:
        raise BrokerAccountValidationError(str(error)) from error


def _single_managed_account(accounts: list[str]) -> str:
    """The one managed account, or a refusal.

    Zero or several accounts is never guessed at: the desktop, the ledger and
    the preflight all key on a single account alias, so picking one
    arbitrarily would attribute one account's balances to another.
    """

    if len(accounts) != 1:
        raise BrokerAccountValidationError(
            "broker account read requires exactly one managed account, "
            f"got {len(accounts)}"
        )
    return accounts[0]


def _to_portfolio(
    *,
    raw_account: str,
    metrics: list[_AccountMetric],
    positions: list[_RawBrokerPosition],
    account_pnl: dict[int, _RawAccountPnl],
    position_pnl: dict[int, _RawPositionPnl],
    messages: list[_BrokerMessage],
) -> BrokerAccountPortfolio:
    """Convert raw IBKR callbacks into domain account truth.

    This is the masking boundary: everything downstream of here is keyed on
    ``account_alias`` and the raw id is gone.
    """

    if not raw_account.upper().startswith("DU"):
        # The read-only channel is locked to the Paper port, so a non-``DU``
        # account here means the gateway is logged into a live account.  That
        # must never be published as Paper truth.
        raise BrokerAccountValidationError(
            "拒绝把非 DU 账户标记为 Paper；请登录 IBKR 模拟账户后重试"
        )

    account_alias = mask_account_id(raw_account)
    observed_at = datetime.now(timezone.utc)
    values = _metric_values(metrics, raw_account)
    pnl = next(
        (
            row
            for row in account_pnl.values()
            if row.account == raw_account
        ),
        None,
    )
    pnl_by_con_id = {
        row.con_id: row
        for row in position_pnl.values()
        if row.account == raw_account
    }

    account = BrokerAccountSnapshot(
        environment=Environment.PAPER,
        account_alias=account_alias,
        net_liquidation=values.get("NetLiquidation"),
        cash=values.get("TotalCashValue"),
        available_funds=values.get("AvailableFunds"),
        buying_power=values.get("BuyingPower"),
        gross_position_value=values.get("GrossPositionValue"),
        excess_liquidity=values.get("ExcessLiquidity"),
        maintenance_margin=values.get("MaintMarginReq"),
        cushion=values.get("Cushion"),
        daily_pnl=pnl.daily_pnl if pnl is not None else None,
        unrealized_pnl=pnl.unrealized_pnl if pnl is not None else None,
        realized_pnl=pnl.realized_pnl if pnl is not None else None,
        observed_at=observed_at,
        pnl_source="IBKR reqPnL" if pnl is not None else "unavailable",
    )

    rows: list[BrokerPositionSnapshot] = []
    for position in positions:
        if position.account != raw_account:
            continue
        if position.quantity == 0:
            # A flat row is not a position.  The broker keeps reporting
            # closed contracts with zero quantity; carrying them would make
            # the position table show holdings the account does not have.
            continue
        row_pnl = pnl_by_con_id.get(position.con_id)
        rows.append(
            BrokerPositionSnapshot(
                account_alias=account_alias,
                con_id=position.con_id,
                symbol=position.symbol,
                local_symbol=position.local_symbol,
                security_type=position.security_type,
                exchange=position.exchange,
                currency=position.currency,
                quantity=position.quantity,
                average_cost=position.average_cost,
                market_value=(
                    row_pnl.market_value if row_pnl is not None else None
                ),
                daily_pnl=(
                    row_pnl.daily_pnl if row_pnl is not None else None
                ),
                unrealized_pnl=(
                    row_pnl.unrealized_pnl if row_pnl is not None else None
                ),
                realized_pnl=(
                    row_pnl.realized_pnl if row_pnl is not None else None
                ),
                observed_at=observed_at,
            )
        )

    diagnostics = tuple(
        BrokerDiagnostic(
            code=message.code,
            message=message.message,
            informational=message.code in INFORMATIONAL_ERROR_CODES,
        )
        for message in messages
    )
    return BrokerAccountPortfolio(
        account=account,
        positions=tuple(rows),
        diagnostics=diagnostics,
    )


def _metric_values(
    metrics: list[_AccountMetric],
    raw_account: str,
) -> dict[str, Decimal | None]:
    """Account-summary tags as Decimals, preferring the USD row.

    IBKR reports the same tag once per currency.  ``USD`` wins; an empty
    currency is the fallback.  Any other currency is *not* treated as USD --
    reading a EUR balance as dollars would misstate the account.  A tag that
    did not parse is ``None``, never ``0``.
    """

    parsed: dict[tuple[str, str], Decimal | None] = {}
    for metric in metrics:
        if metric.account != raw_account:
            continue
        parsed[(metric.tag, metric.currency)] = _decimal_or_none(
            metric.value
        )

    values: dict[str, Decimal | None] = {}
    for tag, currency in parsed:
        if tag in values:
            continue
        if (tag, "USD") in parsed:
            values[tag] = parsed[(tag, "USD")]
        elif (tag, "") in parsed:
            values[tag] = parsed[(tag, "")]
    return values


def _decimal_or_none(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except (ArithmeticError, TypeError, ValueError):
        return None


def _close_account_connection(
    app: Any,
    *,
    account_request_id: int,
    account_pnl_request_id: int,
    position_pnl_requests: dict[int, tuple[str, int]],
) -> None:
    """Cancel the outstanding requests, then disconnect.

    Cancelling first is what tells the gateway to release the subscriptions;
    disconnecting without it can leave the server side streaming to a socket
    that no longer exists.
    """

    if not app.isConnected():
        return
    try:
        app.cancelAccountSummary(account_request_id)
        app.cancelPositions()
        if app.accounts:
            app.cancelPnL(account_pnl_request_id)
        for request_id in position_pnl_requests:
            app.cancelPnLSingle(request_id)
    finally:
        app.disconnect()


def _wait_for(event: Event, deadline: float, label: str) -> None:
    remaining = deadline - monotonic()
    if remaining <= 0 or not event.wait(remaining):
        raise BrokerAccountUnavailable(
            f"timed out waiting for {label}"
        )


def _wait_for_optional_events(
    events: Iterable[Event],
    *,
    deadline: float,
) -> None:
    """Wait for optional callbacks until they all arrive or ``deadline`` passes.

    Never raises.  These are the P&L replies: the account balances and
    positions are already valid broker truth without them, so a slow or
    absent reply degrades to ``None`` fields rather than failing the read.

    The whole sequence shares one absolute ``deadline``, which is what keeps
    the cost bounded.  Waiting per event with a fresh timeout would make the
    total grow linearly with the number of positions -- ten holdings would
    cost ten grace periods.  Because the callbacks arrive on the network
    thread, waiting on the first one also gives every other event time to
    complete, so a single pass is enough.
    """

    for event in events:
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        event.wait(remaining)


__all__ = ["IBKRAccountAdapter"]
