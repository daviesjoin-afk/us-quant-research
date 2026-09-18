"""Broker execution port: order submission and execution reporting.

This is the interface a future IBKR Paper execution adapter must satisfy.
This change does not adapt IBKR to it -- the live Paper stack stays exactly as
it is.

``submit`` takes an ``OrderIntent``, which only the execution service may
construct from a risk-approved proposal.  A strategy cannot reach this port,
because a strategy only ever produces a ``TradeProposal``.
"""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.orders import ExecutionFill, OrderIntent


class BrokerExecutionPort(Protocol):
    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def submit(self, intent: OrderIntent) -> str: ...

    def cancel(self, order_id: str) -> None: ...

    def fills(self) -> tuple[ExecutionFill, ...]: ...
