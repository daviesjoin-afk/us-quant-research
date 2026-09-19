"""Execution composition root.

The only module allowed to know both the execution application and the concrete
adapters it runs against.  Everything above this file -- the window, the
strategy runtime, the session coordinator -- names the ports and the domain
types only, which is what lets the SQLite store and the IBKR channel be replaced
without touching a caller.

Two builders, because the runtime needs the two halves at different moments:

* ``build_order_repository`` opens the store once per window.  It is the same
  file the Paper order journal used, so an existing database keeps its orders.
* ``build_execution_candidate`` builds a broker channel for one candidate
  session, which ``PaperTradingService`` connects, arms and eventually promotes.
  ``build_execution_application`` then binds that channel to the store: the
  application is stateless beyond those two references, so binding it at launch
  time cannot drift from the channel the session is actually using.
"""

from __future__ import annotations

from pathlib import Path

from us_quant.ibkr import IBKRConnectionConfig
from us_quant.trading.adapters.ibkr.execution import IBKRExecutionAdapter
from us_quant.trading.adapters.sqlite.order_repository import (
    SQLiteOrderRepository,
)
from us_quant.trading.application.execution import ExecutionApplication
from us_quant.trading.ports.broker_execution import BrokerExecutionPort
from us_quant.trading.ports.order_repository import OrderRepositoryPort


def build_order_repository(path: str | Path) -> SQLiteOrderRepository:
    """Open the order store; the schema is the frozen Paper one."""

    return SQLiteOrderRepository(path)


def build_execution_candidate(
    config: IBKRConnectionConfig,
    *,
    repository: SQLiteOrderRepository,
    extended_hours_enabled: bool = False,
) -> IBKRExecutionAdapter:
    """Build one IBKR Paper execution channel over the shared order store."""

    return IBKRExecutionAdapter(
        config,
        repository=repository,
        extended_hours_enabled=extended_hours_enabled,
    )


def build_execution_application(
    *,
    repository: OrderRepositoryPort,
    broker: BrokerExecutionPort,
) -> ExecutionApplication:
    """Bind the execution application to one channel and store."""

    return ExecutionApplication(repository=repository, broker=broker)


__all__ = [
    "build_execution_application",
    "build_execution_candidate",
    "build_order_repository",
]