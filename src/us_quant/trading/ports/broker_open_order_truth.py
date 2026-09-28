"""Read-only provider-neutral broker open-order observations."""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.portfolio_reconciliation import BrokerOpenOrderTruth


class BrokerOpenOrderTruthSource(Protocol):
    def broker_open_order_truth(self) -> BrokerOpenOrderTruth: ...


__all__ = ["BrokerOpenOrderTruthSource"]
