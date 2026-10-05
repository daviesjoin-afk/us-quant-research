"""Composition for the durable portfolio operating plan.

Also the place where the Paper launch gate is given teeth: the plan validator
needs to know whether the lifecycle decision that put a version into Paper is
still valid *now*, so this module is the only one that names both the plan
application and the governance stores the answer comes from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from us_quant.trading.adapters.evidence_trust_store import (
    FileEvidenceVerificationKeySource,
)
from us_quant.trading.adapters.sqlite.evidence_authentication_repository import (
    SQLiteEvidenceAuthenticationRepository,
)
from us_quant.trading.adapters.sqlite.portfolio_operating_plan_repository import (
    SQLitePortfolioOperatingPlanRepository,
)
from us_quant.trading.adapters.sqlite.strategy_coverage_repository import (
    SQLiteStrategyCoverageRepository,
)
from us_quant.trading.adapters.sqlite.strategy_gate_repository import (
    SQLiteStrategyGateRepository,
)
from us_quant.trading.adapters.sqlite.strategy_lifecycle_repository import (
    SQLiteStrategyLifecycleRepository,
)
from us_quant.trading.application.strategy_coverage_validity import (
    StrategyCoverageCurrentValidator,
)
from us_quant.trading.application.paper_authorization import PaperLaunchAuthorizer
from us_quant.trading.application.portfolio_plan import (
    PortfolioOperatingPlanApplication,
)


def build_portfolio_operating_plan_application(
    *,
    database_path: str | Path,
    governance_database_path: str | Path,
    trust_store_path: str | Path,
    strategies,
    active_session: Callable[[], bool],
    read_only: bool = False,
) -> PortfolioOperatingPlanApplication:
    """Wire the plan service to its store and to its Paper launch gate.

    ``governance_database_path`` holds the lifecycle, authentication and
    coverage records; ``trust_store_path`` holds the public keys, and is a
    separate file precisely so it can live outside the runtime store.
    """

    lifecycle_repository = SQLiteStrategyLifecycleRepository(
        governance_database_path, read_only=read_only
    )
    # One validator, built once, shared with the lifecycle controller: two
    # instances over the same stores would be two answers to one question, and
    # the launch boundary is exactly where the two must agree.
    coverage_validity = StrategyCoverageCurrentValidator(
        authentications=SQLiteEvidenceAuthenticationRepository(
            governance_database_path, read_only=read_only
        ),
        gates=SQLiteStrategyGateRepository(
            governance_database_path, read_only=read_only
        ),
        key_source=FileEvidenceVerificationKeySource(trust_store_path),
    )
    authorizer = PaperLaunchAuthorizer(
        decisions=lifecycle_repository,
        coverages=SQLiteStrategyCoverageRepository(
            governance_database_path, read_only=read_only
        ),
        lifecycle_policies=lifecycle_repository,
        coverage_validity=coverage_validity,
    )
    return PortfolioOperatingPlanApplication(
        repository=SQLitePortfolioOperatingPlanRepository(
            database_path, read_only=read_only
        ),
        strategies=strategies,
        active_session=active_session,
        paper_authorization=authorizer.authorises,
    )


__all__ = ["build_portfolio_operating_plan_application"]
