from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from us_quant.ibkr_readonly import (
    IBKRReadOnlySnapshot,
    MarketQuote,
    mask_account_id,
)


@dataclass(frozen=True, slots=True)
class AccountView:
    environment: str
    account_alias: str
    net_liquidation: Decimal | None
    cash: Decimal | None
    available_funds: Decimal | None
    buying_power: Decimal | None
    gross_position_value: Decimal | None
    excess_liquidity: Decimal | None
    maintenance_margin: Decimal | None
    cushion: Decimal | None
    daily_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None
    observed_at: str
    pnl_source: str


@dataclass(frozen=True, slots=True)
class PositionView:
    environment: str
    account_alias: str
    con_id: int
    symbol: str
    quantity: Decimal
    average_cost: Decimal
    mark: Decimal | None
    mark_source: str
    market_data_type: int | None
    market_value: Decimal | None
    cost_basis: Decimal
    local_unrealized_pnl: Decimal | None
    broker_daily_pnl: Decimal | None
    broker_unrealized_pnl: Decimal | None
    broker_realized_pnl: Decimal | None
    risk_multiplier: Decimal
    risk_exposure: Decimal | None
    stale: bool


@dataclass(frozen=True, slots=True)
class PortfolioView:
    account: AccountView
    positions: tuple[PositionView, ...]


def build_portfolio_view(
    snapshot: IBKRReadOnlySnapshot,
    *,
    environment: str = "paper",
    observed_at: str | None = None,
    exposure_multipliers: Mapping[str, Decimal] | None = None,
) -> PortfolioView:
    if len(snapshot.accounts) != 1:
        raise ValueError(
            "portfolio view requires exactly one managed account"
        )
    raw_account = snapshot.accounts[0]
    normalized_environment = environment.strip().lower()
    if normalized_environment == "paper" and not raw_account.upper().startswith(
        "DU"
    ):
        raise ValueError(
            "拒绝把非 DU 账户标记为 Paper；请登录 IBKR 模拟账户后重试"
        )
    if normalized_environment == "live" and raw_account.upper().startswith(
        "DU"
    ):
        raise ValueError("拒绝把 DU 模拟账户标记为 Live")
    account_alias = mask_account_id(raw_account)
    metrics = {
        (metric.tag, metric.currency): _decimal_or_none(metric.value)
        for metric in snapshot.metrics
        if metric.account == raw_account
    }

    def metric(tag: str) -> Decimal | None:
        return (
            metrics.get((tag, "USD"))
            or metrics.get((tag, ""))
        )

    account_pnl = next(
        (
            row
            for row in snapshot.account_pnl
            if row.account == raw_account
        ),
        None,
    )
    observed = observed_at or datetime.now(timezone.utc).isoformat()
    account = AccountView(
        environment=environment,
        account_alias=account_alias,
        net_liquidation=metric("NetLiquidation"),
        cash=metric("TotalCashValue"),
        available_funds=metric("AvailableFunds"),
        buying_power=metric("BuyingPower"),
        gross_position_value=metric("GrossPositionValue"),
        excess_liquidity=metric("ExcessLiquidity"),
        maintenance_margin=metric("MaintMarginReq"),
        cushion=metric("Cushion"),
        daily_pnl=(
            account_pnl.daily_pnl if account_pnl is not None else None
        ),
        unrealized_pnl=(
            account_pnl.unrealized_pnl
            if account_pnl is not None
            else None
        ),
        realized_pnl=(
            account_pnl.realized_pnl
            if account_pnl is not None
            else None
        ),
        observed_at=observed,
        pnl_source="IBKR reqPnL" if account_pnl else "unavailable",
    )

    quotes = {quote.symbol: quote for quote in snapshot.quotes}
    broker_pnl = {
        (row.account, row.con_id): row
        for row in snapshot.position_pnl
    }
    positions: list[PositionView] = []
    multipliers = exposure_multipliers or {}
    for position in snapshot.positions:
        if position.account != raw_account or position.quantity == 0:
            continue
        quote = quotes.get(position.symbol)
        mark, mark_source = _select_mark(quote)
        market_value = (
            mark * position.quantity if mark is not None else None
        )
        cost_basis = position.average_cost * position.quantity
        local_unrealized = (
            market_value - cost_basis
            if market_value is not None
            else None
        )
        pnl = broker_pnl.get((raw_account, position.con_id))
        multiplier = multipliers.get(position.symbol, Decimal("1"))
        positions.append(
            PositionView(
                environment=environment,
                account_alias=account_alias,
                con_id=position.con_id,
                symbol=position.symbol,
                quantity=position.quantity,
                average_cost=position.average_cost,
                mark=mark,
                mark_source=mark_source,
                market_data_type=(
                    quote.market_data_type if quote is not None else None
                ),
                market_value=market_value,
                cost_basis=cost_basis,
                local_unrealized_pnl=local_unrealized,
                broker_daily_pnl=(
                    pnl.daily_pnl if pnl is not None else None
                ),
                broker_unrealized_pnl=(
                    pnl.unrealized_pnl if pnl is not None else None
                ),
                broker_realized_pnl=(
                    pnl.realized_pnl if pnl is not None else None
                ),
                risk_multiplier=multiplier,
                risk_exposure=(
                    market_value * multiplier
                    if market_value is not None
                    else None
                ),
                stale=(
                    quote is None
                    or quote.market_data_type != 1
                    or mark is None
                ),
            )
        )
    return PortfolioView(account=account, positions=tuple(positions))


def _select_mark(
    quote: MarketQuote | None,
) -> tuple[Decimal | None, str]:
    if quote is None:
        return None, "unavailable"
    if quote.last is not None:
        return quote.last, "last"
    if quote.bid is not None and quote.ask is not None:
        return (quote.bid + quote.ask) / Decimal("2"), "mid"
    if quote.close is not None:
        return quote.close, "close"
    return None, "unavailable"


def _decimal_or_none(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except Exception:
        return None
