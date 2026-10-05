"""Fail-closed operations gate for the portfolio runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import re
from typing import Callable
from uuid import uuid4

from us_quant.trading.application.portfolio_runtime import PortfolioRuntime
from us_quant.trading.domain.portfolio import (
    PortfolioCapitalPolicy,
    PortfolioStrategyAllocation,
)
from us_quant.trading.domain.portfolio_ledger import PortfolioStoreUnreadable
from us_quant.trading.domain.portfolio_runtime import PortfolioCycleResult
from us_quant.trading.ports.portfolio_repository import PortfolioStateRepositoryPort
from us_quant.trading.application.strategies import StrategyApplication
from us_quant.trading.domain.portfolio_operations import (
    PortfolioOperatingPlan,
    PortfolioPlanAuditEvent,
)
from us_quant.trading.domain.strategy import StrategyMode, StrategyStatus
from us_quant.trading.ports.portfolio_operating_plan import (
    PortfolioOperatingPlanConflict,
    PortfolioOperatingPlanRepositoryPort,
)


@dataclass(frozen=True, slots=True)
class PortfolioExecutionGates:
    """Fresh safety facts required before a portfolio cycle may submit."""

    reconciliation_clear: bool
    execution_clear: bool
    paper_clear: bool
    live_kill_clear: bool
    live_recovery_clear: bool
    ledger_readable: bool

    @property
    def may_open_exposure(self) -> bool:
        return all((
            self.reconciliation_clear,
            self.execution_clear,
            self.paper_clear,
            self.live_kill_clear,
            self.live_recovery_clear,
            self.ledger_readable,
        ))


@dataclass(frozen=True, slots=True)
class PortfolioCycleRequest:
    portfolio_cycle_id: str
    observed_at: datetime
    proposal_cutoff: datetime
    snapshot_identity: str
    policy: PortfolioCapitalPolicy
    policy_identity: str
    policy_revision: str
    selected_version_ids: frozenset[str]


class PortfolioOperationsApplication:
    """One operations facade for one account's already-composed runtime.

    It owns no portfolio facts and never creates a second allocator/runtime.  A
    cycle is admitted only when all independent safety owners report clear.
    """

    def __init__(
        self,
        *,
        runtime: PortfolioRuntime,
        repository: PortfolioStateRepositoryPort,
    ) -> None:
        if not isinstance(runtime, PortfolioRuntime):
            raise TypeError("runtime must be PortfolioRuntime")
        self._runtime = runtime
        self._repository = repository

    @property
    def runtime(self) -> PortfolioRuntime:
        """The single runtime this account's composition supplied."""

        return self._runtime

    def evaluate_cycle(
        self,
        *,
        gates: PortfolioExecutionGates,
        request: PortfolioCycleRequest,
    ) -> PortfolioCycleResult | None:
        """Run an admitted cycle; closed gates produce no allocation or action."""

        if not isinstance(gates, PortfolioExecutionGates):
            raise TypeError("gates must be PortfolioExecutionGates")
        if not gates.may_open_exposure:
            return None
        try:
            # Check durable state before reaching proposal, Risk, or dispatch.
            self._repository.decisions()
        except Exception as error:  # noqa: BLE001 - unreadable is a hard stop
            raise PortfolioStoreUnreadable("portfolio ledger is unreadable") from error
        return self._runtime.evaluate_cycle(
            portfolio_cycle_id=request.portfolio_cycle_id,
            observed_at=request.observed_at,
            proposal_cutoff=request.proposal_cutoff,
            snapshot_identity=request.snapshot_identity,
            policy=request.policy,
            policy_identity=request.policy_identity,
            policy_revision=request.policy_revision,
            selected_version_ids=request.selected_version_ids,
        )


from us_quant.trading.application.portfolio_plan import (
    PortfolioOperatingPlanApplication,
    PortfolioPlanRefused,
)

__all__ = [
    "PortfolioCycleRequest",
    "PortfolioExecutionGates",
    "PortfolioOperatingPlanApplication",
    "PortfolioPlanRefused",
    "PortfolioOperationsApplication",
]
