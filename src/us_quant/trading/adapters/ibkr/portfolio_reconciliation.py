"""Read a fresh IBKR Paper open-order set through the narrow runtime port."""

from __future__ import annotations

from dataclasses import replace

from us_quant.paper_order_models import (
    PaperOrderConnection,
    PaperReconciliationSnapshot,
)
from us_quant.trading.adapters.ibkr.portfolio_reconciliation_mapping import (
    broker_open_order_truth_from_snapshot,
)
from us_quant.trading.domain.portfolio_reconciliation import BrokerOpenOrderTruth
from us_quant.trading.runtime.paper_contracts import PaperOrderPort


class IBKRPaperOpenOrderTruthSource:
    """Bridge the verified Paper refresh into a provider-neutral truth value."""

    def __init__(self, *, orders: PaperOrderPort, session_id: str) -> None:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        self._orders = orders
        self._session_id = session_id

    def broker_open_order_truth(self) -> BrokerOpenOrderTruth:
        snapshot = self._orders.refresh_reconciliation_snapshot(self._session_id)
        connection = self._orders.connection_snapshot()
        if not isinstance(snapshot, PaperReconciliationSnapshot):
            raise TypeError(
                "Paper order port returned an invalid reconciliation snapshot"
            )
        if not isinstance(connection, PaperOrderConnection):
            raise TypeError("Paper order port returned an invalid connection snapshot")
        truth = broker_open_order_truth_from_snapshot(
            snapshot, account_alias=connection.account_alias
        )
        complete = (
            truth.snapshot_complete
            and connection.connected
            and connection.snapshot_complete
            and connection.connection_generation == snapshot.connection_generation
            and self._orders.reconciliation_snapshot_is_current(snapshot)
        )
        return truth if complete else replace(truth, snapshot_complete=False)


__all__ = ["IBKRPaperOpenOrderTruthSource"]
