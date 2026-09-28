"""Portfolio decision persistence boundary; contains no SQL or adapter imports."""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.portfolio_ledger import PortfolioDecisionRecord


class PortfolioStateRepositoryPort(Protocol):
    def decision(self, decision_id: str) -> PortfolioDecisionRecord | None: ...

    def decisions(self) -> tuple[PortfolioDecisionRecord, ...]: ...

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
