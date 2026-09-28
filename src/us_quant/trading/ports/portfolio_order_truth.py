"""Read-only durable order facts required by portfolio reconciliation."""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.portfolio_reconciliation import PortfolioOrderTruth


class PortfolioOrderTruthSource(Protocol):
    def portfolio_order_truth(self) -> PortfolioOrderTruth: ...


__all__ = ["PortfolioOrderTruthSource"]
