"""IBKR Paper callback transport.

This module owns exactly one job: turn IBKR's ``EWrapper`` / ``EClient``
callbacks into plain method calls on a sink.  It holds no connection, no
trading state, no persistence and no workflow knowledge.  Every callback is
forwarded verbatim and then forgotten; what a callback *means* for the Paper
session is decided by ``us_quant.ibkr_paper_orders``.

The official IBKR Python API stays a lazy dependency: importing this module
must never require ``ibapi``, and only ``create_paper_gateway_app`` touches it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
from typing import Any, Protocol


class IBKRPaperGatewayError(RuntimeError):
    """The IBKR callback bridge could not be built."""


@dataclass(slots=True)
class PaperGatewayHandshake:
    """The single connection-attempt truth for one ``connect()`` call.

    The events and the error list describe one handshake attempt and nothing
    else.  The bridge does not create or read them: the service owns the
    instance so that a callback can never update a second, competing copy.
    """

    ready: Event = field(default_factory=Event)
    accounts_ready: Event = field(default_factory=Event)
    summary_ready: Event = field(default_factory=Event)
    positions_ready: Event = field(default_factory=Event)
    open_orders_ready: Event = field(default_factory=Event)
    completed_orders_ready: Event = field(default_factory=Event)
    executions_ready: Event = field(default_factory=Event)
    errors: list[str] = field(default_factory=list)


class PaperGatewaySink(Protocol):
    """Everything the transport is allowed to say to the Paper order service.

    ``app`` is the bridge instance itself and ``epoch`` is the physical
    connection epoch it was built for, so the sink can reject a callback from a
    superseded connection.  No method returns trading state and none of them
    accepts a UI, workflow or strategy type.
    """

    def gateway_is_current(self, app: Any, epoch: int) -> bool: ...

    def gateway_next_valid_id(
        self, app: Any, epoch: int, order_id: int
    ) -> None: ...

    def gateway_managed_accounts(
        self, app: Any, epoch: int, accounts_list: str
    ) -> None: ...

    def gateway_error(
        self, app: Any, epoch: int, req_id: int, args: tuple[Any, ...]
    ) -> None: ...

    def gateway_order_status(
        self,
        app: Any,
        epoch: int,
        order_id: int,
        status: str,
        filled: Any,
        remaining: Any,
        average_fill_price: Any,
        perm_id: int,
        parent_id: int,
        last_fill_price: Any,
        client_id: int,
        why_held: str,
        market_cap_price: Any,
    ) -> None: ...

    def gateway_open_order(
        self,
        app: Any,
        epoch: int,
        order_id: int,
        contract: Any,
        order: Any,
        order_state: Any,
    ) -> None: ...

    def gateway_open_order_end(self, app: Any, epoch: int) -> None: ...

    def gateway_account_summary(
        self,
        app: Any,
        epoch: int,
        req_id: int,
        account: str,
        tag: str,
        value: str,
        currency: str,
    ) -> None: ...

    def gateway_account_summary_end(
        self, app: Any, epoch: int, req_id: int
    ) -> None: ...

    def gateway_position(
        self,
        app: Any,
        epoch: int,
        account: str,
        contract: Any,
        position: Any,
        average_cost: Any,
    ) -> None: ...

    def gateway_position_end(self, app: Any, epoch: int) -> None: ...

    def gateway_pnl(
        self,
        app: Any,
        epoch: int,
        req_id: int,
        daily_pnl: Any,
        unrealized_pnl: Any,
        realized_pnl: Any,
    ) -> None: ...

    def gateway_exec_details(
        self,
        app: Any,
        epoch: int,
        req_id: int,
        contract: Any,
        execution: Any,
    ) -> None: ...

    def gateway_exec_details_end(
        self, app: Any, epoch: int, req_id: int
    ) -> None: ...

    def gateway_completed_order(
        self,
        app: Any,
        epoch: int,
        contract: Any,
        order: Any,
        order_state: Any,
    ) -> None: ...

    def gateway_completed_orders_end(self, app: Any, epoch: int) -> None: ...

    def gateway_connection_closed(self, app: Any, epoch: int) -> None: ...


def create_paper_gateway_app(
    *,
    sink: PaperGatewaySink,
    epoch: int,
) -> Any:
    """Build the IBKR ``EWrapper`` / ``EClient`` bridge for one epoch.

    The IBKR API is imported here and nowhere else, so importing this module
    never needs it.  The returned object only forwards: it issues no request,
    places no order and keeps no state beyond the sink and the epoch it was
    built with.
    """

    try:
        from ibapi.client import EClient
        from ibapi.wrapper import EWrapper
    except ImportError as error:
        raise IBKRPaperGatewayError(
            "未安装 IBKR 官方 Python API"
        ) from error

    class PaperApp(EWrapper, EClient):
        def __init__(self) -> None:
            EWrapper.__init__(self)
            EClient.__init__(self, wrapper=self)
            self._sink = sink
            self._epoch = epoch

        def _current(self) -> bool:
            return self._sink.gateway_is_current(self, self._epoch)

        def nextValidId(self, orderId: int) -> None:
            if not self._current():
                return
            self._sink.gateway_next_valid_id(
                self,
                self._epoch,
                orderId,
            )

        def managedAccounts(self, accountsList: str) -> None:
            if not self._current():
                return
            self._sink.gateway_managed_accounts(
                self,
                self._epoch,
                accountsList,
            )

        def error(
            self,
            reqId: int,
            *args: Any,
        ) -> None:
            if not self._current():
                return
            self._sink.gateway_error(
                self,
                self._epoch,
                reqId,
                args,
            )

        def orderStatus(
            self,
            orderId: int,
            status: str,
            filled,
            remaining,
            avgFillPrice: float,
            permId: int,
            parentId: int,
            lastFillPrice: float,
            clientId: int,
            whyHeld: str,
            mktCapPrice: float,
        ) -> None:
            if not self._current():
                return
            self._sink.gateway_order_status(
                self,
                self._epoch,
                orderId,
                status,
                filled,
                remaining,
                avgFillPrice,
                permId,
                parentId,
                lastFillPrice,
                clientId,
                whyHeld,
                mktCapPrice,
            )

        def openOrder(
            self,
            orderId: int,
            contract,
            order,
            orderState,
        ) -> None:
            if not self._current():
                return
            self._sink.gateway_open_order(
                self,
                self._epoch,
                orderId,
                contract,
                order,
                orderState,
            )

        def openOrderEnd(self) -> None:
            if not self._current():
                return
            self._sink.gateway_open_order_end(self, self._epoch)

        def accountSummary(
            self,
            reqId: int,
            account: str,
            tag: str,
            value: str,
            currency: str,
        ) -> None:
            if not self._current():
                return
            self._sink.gateway_account_summary(
                self,
                self._epoch,
                reqId,
                account,
                tag,
                value,
                currency,
            )

        def accountSummaryEnd(self, reqId: int) -> None:
            if not self._current():
                return
            self._sink.gateway_account_summary_end(
                self,
                self._epoch,
                reqId,
            )

        def position(
            self,
            account: str,
            contract,
            pos,
            avgCost: float,
        ) -> None:
            if not self._current():
                return
            self._sink.gateway_position(
                self,
                self._epoch,
                account,
                contract,
                pos,
                avgCost,
            )

        def positionEnd(self) -> None:
            if not self._current():
                return
            self._sink.gateway_position_end(self, self._epoch)

        def pnl(
            self,
            reqId: int,
            dailyPnL: float,
            unrealizedPnL: float,
            realizedPnL: float,
        ) -> None:
            if not self._current():
                return
            self._sink.gateway_pnl(
                self,
                self._epoch,
                reqId,
                dailyPnL,
                unrealizedPnL,
                realizedPnL,
            )

        def execDetails(
            self, reqId: int, contract, execution
        ) -> None:
            if not self._current():
                return
            self._sink.gateway_exec_details(
                self,
                self._epoch,
                reqId,
                contract,
                execution,
            )

        def execDetailsEnd(self, reqId: int) -> None:
            if not self._current():
                return
            self._sink.gateway_exec_details_end(
                self,
                self._epoch,
                reqId,
            )

        def completedOrder(
            self, contract, order, orderState
        ) -> None:
            if not self._current():
                return
            self._sink.gateway_completed_order(
                self,
                self._epoch,
                contract,
                order,
                orderState,
            )

        def completedOrdersEnd(self) -> None:
            if not self._current():
                return
            self._sink.gateway_completed_orders_end(
                self,
                self._epoch,
            )

        def connectionClosed(self) -> None:
            if not self._current():
                return
            self._sink.gateway_connection_closed(self, self._epoch)

    return PaperApp()
