"""Read-only composition for the supervised Paper canary inspector."""

from __future__ import annotations

from contextlib import closing

from us_quant.minute_data import MinuteQuoteStore
from us_quant.paths import ApplicationPaths
from us_quant.sqlite_support import connect_sqlite_readonly
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
from us_quant.trading.adapters.sqlite.strategy_paper_performance_repository import (
    SQLiteStrategyPaperPerformanceRepository,
)
from us_quant.trading.adapters.sqlite.strategy_repository import (
    SQLiteStrategyRepository,
)
from us_quant.trading.application.market_evidence_readiness import (
    MarketEvidenceReadinessApplication,
)
from us_quant.trading.application.paper_authorization import PaperLaunchAuthorizer
from us_quant.trading.application.paper_canary_readiness import (
    PaperCanaryInspectionSpec,
    PaperCanaryReadinessApplication,
)
from us_quant.trading.application.portfolio_plan import (
    PortfolioOperatingPlanApplication,
)
from us_quant.trading.application.strategies import StrategyApplication
from us_quant.trading.application.strategy_coverage_validity import (
    StrategyCoverageCurrentValidator,
)


class _EmptyQuoteReadPort:
    def load(
        self, symbol: str, *, provider: str | None = None, usable_only: bool = True
    ):
        del symbol, provider, usable_only
        return ()


class _MissingPlanReadPort:
    def load(self):
        return None


class _EmptyPerformanceReadPort:
    def latest_for_version(self, version_id: str):
        del version_id


def _performance_read_port(governance_path):
    if not governance_path.exists():
        return _EmptyPerformanceReadPort()
    with closing(connect_sqlite_readonly(governance_path)) as connection:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' "
            "AND name = 'strategy_paper_performance_evaluation'"
        ).fetchone()
    if row is None:
        return _EmptyPerformanceReadPort()
    return SQLiteStrategyPaperPerformanceRepository(governance_path, read_only=True)


def build_paper_canary_readiness_application(
    spec: PaperCanaryInspectionSpec,
    *,
    paths: ApplicationPaths | None = None,
) -> PaperCanaryReadinessApplication:
    """Compose existing authorities over read-only SQLite connections."""

    app_paths = paths or ApplicationPaths.discover()
    runtime = app_paths.runtime_root
    strategy_repository = SQLiteStrategyRepository(
        runtime / "strategies.sqlite3", read_only=True
    )
    strategies = StrategyApplication(strategy_repository)
    governance_path = runtime / "strategy_governance.sqlite3"
    lifecycle = SQLiteStrategyLifecycleRepository(governance_path, read_only=True)
    authentications = SQLiteEvidenceAuthenticationRepository(
        governance_path, read_only=True
    )
    gates = SQLiteStrategyGateRepository(governance_path, read_only=True)
    coverages = SQLiteStrategyCoverageRepository(governance_path, read_only=True)
    validity = StrategyCoverageCurrentValidator(
        authentications=authentications,
        gates=gates,
        key_source=FileEvidenceVerificationKeySource(
            app_paths.strategy_evidence_trust_store_path
        ),
    )
    authorizer = PaperLaunchAuthorizer(
        decisions=lifecycle,
        coverages=coverages,
        lifecycle_policies=lifecycle,
        coverage_validity=validity,
    )
    quote_path = runtime / "minute_quotes.sqlite3"
    quote_store = (
        MinuteQuoteStore(quote_path, read_only=True)
        if quote_path.exists()
        else _EmptyQuoteReadPort()
    )
    plan_path = runtime / "portfolio_operating_plan.sqlite3"
    plan_repository = (
        SQLitePortfolioOperatingPlanRepository(plan_path, read_only=True)
        if plan_path.exists()
        else _MissingPlanReadPort()
    )
    plan_application = PortfolioOperatingPlanApplication(
        repository=plan_repository,
        strategies=strategies,
        active_session=lambda: False,
        paper_authorization=authorizer.authorises,
    )
    return PaperCanaryReadinessApplication(
        spec=spec,
        strategies=strategies,
        authentications=authentications,
        gates=gates,
        coverages=coverages,
        lifecycle_decisions=lifecycle,
        paper_performance=_performance_read_port(governance_path),
        paper_launch_authorizer=authorizer,
        market_evidence_readiness=MarketEvidenceReadinessApplication(quote_store),
        portfolio_plan_repository=plan_repository,
        portfolio_plan_application=plan_application,
    )


def build_live_broker_account_application(config):
    """Expose the existing read-only broker account composition on explicit opt-in."""

    from us_quant.trading.composition.accounts import build_broker_account_application

    return build_broker_account_application(config)


__all__ = [
    "build_live_broker_account_application",
    "build_paper_canary_readiness_application",
]
