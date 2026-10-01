"""Bind the Paper evidence application to read ports and two durable stores."""
from dataclasses import dataclass
from pathlib import Path

from us_quant.trading.adapters.sqlite.strategy_paper_performance_repository import SQLiteStrategyPaperPerformanceRepository
from us_quant.trading.application.strategy_paper_performance import StrategyPaperPerformanceApplication
from us_quant.trading.ports.strategy_repository import StrategyRepositoryPort
from us_quant.trading.ports.portfolio_repository import PortfolioStateRepositoryPort
from us_quant.trading.ports.portfolio_order_truth import PortfolioOrderTruthSource
from us_quant.trading.ports.broker_open_order_truth import BrokerOpenOrderTruthSource


@dataclass(frozen=True, slots=True)
class StrategyPaperPerformanceComponents:
    application: StrategyPaperPerformanceApplication
    repository: SQLiteStrategyPaperPerformanceRepository


def build_strategy_paper_performance_components(
    *, database_path: str | Path, strategies: StrategyRepositoryPort,
    portfolio_repository: PortfolioStateRepositoryPort,
    order_truth: PortfolioOrderTruthSource, broker_order_truth: BrokerOpenOrderTruthSource,
) -> StrategyPaperPerformanceComponents:
    repository = SQLiteStrategyPaperPerformanceRepository(database_path)
    return StrategyPaperPerformanceComponents(
        application=StrategyPaperPerformanceApplication(
            strategies=strategies, portfolio_repository=portfolio_repository,
            order_truth=order_truth, broker_order_truth=broker_order_truth,
            policies=repository, evaluations=repository,
        ), repository=repository,
    )
