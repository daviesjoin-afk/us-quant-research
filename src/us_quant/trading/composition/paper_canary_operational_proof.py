"""Read-only composition of the existing runtime SQLite repositories."""

from pathlib import Path
from contextlib import closing

from us_quant.paths import ApplicationPaths
from us_quant.sqlite_support import connect_sqlite_readonly
from us_quant.trading.adapters.sqlite.order_repository import SQLiteOrderRepository
from us_quant.trading.adapters.sqlite.portfolio_repository import SQLitePortfolioRepository
from us_quant.trading.adapters.sqlite.strategy_paper_performance_repository import SQLiteStrategyPaperPerformanceRepository
from us_quant.trading.adapters.sqlite.strategy_repository import SQLiteStrategyRepository
from us_quant.trading.application.paper_canary_operational_proof import PaperCanaryOperationalProofApplication
from us_quant.trading.domain.portfolio_reconciliation import PortfolioOrderTruth
from us_quant.trading.ports.strategy_paper_performance_repository import StrategyPaperPerformanceRepositoryNotFound
from us_quant.trading.ports.strategy_repository import StrategyRepositoryNotFound


class _EmptyTruth:
    def decisions(self):
        return ()

    def execution_attributions(self):
        return ()

    def portfolio_order_truth(self):
        return PortfolioOrderTruth(())

    def get_evaluation(self, evaluation_id):
        raise StrategyPaperPerformanceRepositoryNotFound(evaluation_id)

    def get_version(self, version_id):
        raise StrategyRepositoryNotFound(version_id)


def build_paper_canary_operational_proof_application(
    spec, *, runtime_root: str | Path | None = None, broker_order_truth=None,
):
    root = Path(runtime_root) if runtime_root is not None else ApplicationPaths.discover().runtime_root
    portfolio_path = root / "portfolio_execution.sqlite3"
    orders_path = root / "ibkr_paper_orders.sqlite3"
    performance_path = root / "strategy_governance.sqlite3"
    strategy_path = root / "strategies.sqlite3"
    performance = _EmptyTruth()
    if performance_path.exists():
        with closing(connect_sqlite_readonly(performance_path)) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_paper_performance_evaluation'"
            ).fetchone()
        if exists:
            performance = SQLiteStrategyPaperPerformanceRepository(performance_path, read_only=True)
    return PaperCanaryOperationalProofApplication(
        spec=spec,
        portfolio_repository=SQLitePortfolioRepository(portfolio_path, read_only=True) if portfolio_path.exists() else _EmptyTruth(),
        order_truth=SQLiteOrderRepository(orders_path, read_only=True) if orders_path.exists() else _EmptyTruth(),
        evaluations=performance, broker_order_truth=broker_order_truth,
        strategies=SQLiteStrategyRepository(strategy_path, read_only=True) if strategy_path.exists() else _EmptyTruth(),
    )
