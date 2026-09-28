"""Portfolio decision persistence boundary; contains no SQL or adapter imports."""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.portfolio_ledger import (
    PortfolioDecisionRecord,
    PortfolioExecutionAttribution,
)


class PortfolioStateRepositoryPort(Protocol):
    def decision(self, decision_id: str) -> PortfolioDecisionRecord | None: ...

    def decisions(self) -> tuple[PortfolioDecisionRecord, ...]: ...

    def execution_attribution(self, order_id: str) -> PortfolioExecutionAttribution | None: ...

    def execution_attributions(self) -> tuple[PortfolioExecutionAttribution, ...]: ...

    def record_decision(self, record: PortfolioDecisionRecord) -> PortfolioDecisionRecord:
        """Insert idempotently; conflicting reuse of a decision ID must fail."""

        ...

    def update_decision(
        self,
        record: PortfolioDecisionRecord,
        *,
        expected_revision: int,
    ) -> PortfolioDecisionRecord:
        """CAS update of risk/execution linkage; stale revisions must fail."""

        ...

    def record_dispatch_outcome(
        self,
        record: PortfolioDecisionRecord,
        execution_attribution: PortfolioExecutionAttribution | None,
        *,
        expected_revision: int,
    ) -> PortfolioDecisionRecord:
        """CAS the dispatch outcome and its execution attribution atomically."""

        ...
