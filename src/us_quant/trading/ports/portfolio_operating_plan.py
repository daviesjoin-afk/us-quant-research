"""Narrow persistence boundary for operator portfolio plans and their audit."""

from __future__ import annotations

from typing import Protocol

from us_quant.trading.domain.portfolio_operations import PortfolioOperatingPlan, PortfolioPlanAuditEvent


class PortfolioOperatingPlanConflict(RuntimeError):
    pass


class PortfolioOperatingPlanRepositoryPort(Protocol):
    def load(self) -> PortfolioOperatingPlan | None: ...

    def save(
        self,
        plan: PortfolioOperatingPlan,
        *,
        expected_revision: int,
        audit: PortfolioPlanAuditEvent,
    ) -> PortfolioOperatingPlan: ...

    def audit_events(self) -> tuple[PortfolioPlanAuditEvent, ...]: ...
