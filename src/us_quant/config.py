from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import tomllib

from us_quant.domain import Environment, decimal
from us_quant.ibkr import IBKRConnectionConfig
from us_quant.portfolio import SubstitutionRule
from us_quant.risk import RiskLimits


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
    )
