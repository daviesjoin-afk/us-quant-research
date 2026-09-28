from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from pathlib import Path
import tomllib

from us_quant.trading.domain.common import Environment, decimal
from us_quant.ibkr import IBKRConnectionConfig
from us_quant.portfolio import SubstitutionRule
from us_quant.trading.domain.risk import RiskLimits
from us_quant.trading.domain.paper_autonomy_supervisor import PaperAutonomyPolicy
from us_quant.trading.domain.portfolio import (
    PortfolioCapitalPolicy,
    PortfolioStrategyAllocation,
)


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    per_share_commission: Decimal
    minimum_commission: Decimal
    slippage_bps: Decimal


@dataclass(frozen=True, slots=True)
class ResearchPortfolioConfig:
    max_gross_exposure_pct: Decimal
    max_position_exposure_pct: Decimal

    def __post_init__(self) -> None:
        if not (
            Decimal("0")
            < self.max_position_exposure_pct
            <= self.max_gross_exposure_pct
            <= Decimal("1")
        ):
            raise ValueError(
                "research portfolio exposure limits are invalid"
            )


@dataclass(frozen=True, slots=True)
class AppConfig:
    environment: Environment
    database_path: Path
    live_trading_enabled: bool
    initial_equity: Decimal
    base_currency: str
    whole_shares_only: bool
    allow_margin_borrowing: bool
    risk_limits: RiskLimits
    research_portfolio: ResearchPortfolioConfig
    execution: ExecutionConfig
    ibkr: IBKRConnectionConfig
    substitutions: dict[str, SubstitutionRule]
    paper_autonomy_policy: PaperAutonomyPolicy | None = None
    paper_autonomy_config_error: str | None = None
    portfolio_capital_policy: PortfolioCapitalPolicy = PortfolioCapitalPolicy()
    portfolio_policy_config_error: str | None = None


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open("rb") as file:
        raw = tomllib.load(file)

    app = raw["app"]
    account = raw["account"]
    risk = raw["risk"]
    research = raw.get("research", {})
    execution = raw["execution"]
    broker = raw["broker"]
    autonomy_policy, autonomy_error = _paper_autonomy_policy(raw)
    portfolio_policy, portfolio_error = _portfolio_capital_policy(raw)

    substitutions = {
        source_symbol: SubstitutionRule(
            source_symbol=source_symbol,
            execution_symbol=values["execution_symbol"],
            exposure_multiplier=decimal(values["exposure_multiplier"]),
            holding_mode=values["holding_mode"],
        )
        for source_symbol, values in raw.get("substitutions", {}).items()
    }

    return AppConfig(
        environment=Environment(app["environment"]),
        database_path=Path(app["database_path"]),
        live_trading_enabled=bool(app["live_trading_enabled"]),
        initial_equity=decimal(account["initial_equity"]),
        base_currency=account["base_currency"],
        whole_shares_only=bool(account["whole_shares_only"]),
        allow_margin_borrowing=bool(account["allow_margin_borrowing"]),
        risk_limits=RiskLimits(
            max_gross_exposure_pct=decimal(risk["max_gross_exposure_pct"]),
            max_position_exposure_pct=decimal(
                risk["max_position_exposure_pct"]
            ),
            daily_loss_halt_pct=decimal(risk["daily_loss_halt_pct"]),
            drawdown_halt_pct=decimal(risk["drawdown_halt_pct"]),
            allow_margin_borrowing=bool(account["allow_margin_borrowing"]),
        ),
        research_portfolio=ResearchPortfolioConfig(
            max_gross_exposure_pct=decimal(
                research.get(
                    "portfolio_max_gross_exposure_pct",
                    risk["max_gross_exposure_pct"],
                )
            ),
            max_position_exposure_pct=decimal(
                research.get(
                    "portfolio_max_position_exposure_pct",
                    risk["max_position_exposure_pct"],
                )
            ),
        ),
        execution=ExecutionConfig(
            per_share_commission=decimal(
                execution["per_share_commission"]
            ),
            minimum_commission=decimal(execution["minimum_commission"]),
            slippage_bps=decimal(execution["slippage_bps"]),
        ),
        ibkr=IBKRConnectionConfig(
            host=broker["host"],
            port=int(broker["port"]),
            client_id=int(broker["client_id"]),
            api_read_only=bool(broker["api_read_only"]),
            paper_order_submission_enabled=bool(
                broker["paper_order_submission_enabled"]
            ),
            connection_timeout_seconds=float(
                broker["connection_timeout_seconds"]
            ),
        ),
        substitutions=substitutions,
        paper_autonomy_policy=autonomy_policy,
        paper_autonomy_config_error=autonomy_error,
        portfolio_capital_policy=portfolio_policy,
        portfolio_policy_config_error=portfolio_error,
    )


def _portfolio_capital_policy(
    raw: dict,
) -> tuple[PortfolioCapitalPolicy, str | None]:
    """Load explicit portfolio limits; absence stays zero and disabled."""

    section = raw.get("portfolio")
    if not isinstance(section, dict):
        return PortfolioCapitalPolicy(), "required [portfolio] policy is missing"
    try:
        allocations = tuple(
            PortfolioStrategyAllocation(
                strategy_version_id=str(item["strategy_version_id"]),
                capital_weight=Decimal(str(item["capital_weight"])),
                max_capital=Decimal(str(item["max_capital"])),
                max_gross_exposure=Decimal(str(item["max_gross_exposure"])),
                enabled=item["enabled"],
            )
            for item in section.get("allocations", ())
        )
        policy = PortfolioCapitalPolicy(
            total_capital_limit=Decimal(str(section["total_capital_limit"])),
            max_gross_exposure=Decimal(str(section["max_gross_exposure"])),
            max_net_exposure=Decimal(str(section["max_net_exposure"])),
            max_single_position_notional=Decimal(
                str(section["max_single_position_notional"])
            ),
            max_symbol_concentration=Decimal(
                str(section["max_symbol_concentration"])
            ),
            max_strategy_concentration=Decimal(
                str(section["max_strategy_concentration"])
            ),
            max_positions=int(section["max_positions"]),
            max_open_orders=int(section["max_open_orders"]),
            allocations=allocations,
        )
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return PortfolioCapitalPolicy(), "[portfolio] policy is invalid or incomplete"
    if not policy.is_configured:
        return policy, "[portfolio] policy has zero limits or no enabled allocations"
    return policy, None


def _paper_autonomy_policy(
    raw: dict,
) -> tuple[PaperAutonomyPolicy | None, str | None]:
    """Load an explicitly configured policy without inventing production times."""

    section = raw.get("paper")
    autonomy = section.get("autonomy") if isinstance(section, dict) else None
    if not isinstance(autonomy, dict):
        return None, "required [paper.autonomy] policy is missing"
    try:
        policy = PaperAutonomyPolicy(
            prepare_not_before_et=time.fromisoformat(
                str(autonomy["prepare_not_before_et"])
            ),
            start_not_before_et=time.fromisoformat(
                str(autonomy["start_not_before_et"])
            ),
            latest_start_et=time.fromisoformat(
                str(autonomy["latest_start_et"])
            ),
            orderly_stop_at_et=time.fromisoformat(
                str(autonomy["orderly_stop_at_et"])
            ),
            candidate_limit=int(autonomy["candidate_limit"]),
            requested_capital_limit=Decimal(
                str(autonomy["requested_capital_limit"])
            ),
            tick_interval_seconds=int(autonomy["tick_interval_seconds"]),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None, "[paper.autonomy] policy is invalid or incomplete"
    if any(
        value.tzinfo is not None
        for value in (
            policy.prepare_not_before_et,
            policy.start_not_before_et,
            policy.latest_start_et,
            policy.orderly_stop_at_et,
        )
    ):
        return None, "[paper.autonomy] times must be Eastern wall-clock values"
    return policy, None
