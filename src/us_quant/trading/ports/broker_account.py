"""Broker account port: read-only account and position access.

This is the interface a future IBKR account adapter must satisfy.  It is
strictly read-only -- there is no submit or cancel here.  Order submission
belongs to ``BrokerExecutionPort`` so that a read-only account link can never
be mistaken for a channel that can trade, which is the same separation the
current code enforces by keeping the read-only connection and the Paper order
connection distinct.
"""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.account import (
    AccountSnapshot,
    BrokerConnectionState,
    Position,
)


class BrokerAccountPort(Protocol):
    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def connection_state(self) -> BrokerConnectionState: ...

    def account_snapshot(self) -> AccountSnapshot | None: ...

    def positions(self) -> tuple[Position, ...]: ...
