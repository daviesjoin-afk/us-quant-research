from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from math import log, sqrt
from pathlib import Path
from statistics import fmean, pstdev
import tempfile

from us_quant.config import AppConfig
from us_quant.cross_sectional import (
    DEFAULT_CANDIDATES,
    CrossSectionalCandidate,
    _load_data,
    _rank_signals,
)
from us_quant.market_data import DailyBar
from us_quant.portfolio import IntegerPositionSizer, SubstitutionRule
from us_quant.universe import UniverseRecord, UniverseSnapshot


@dataclass(frozen=True, slots=True)
class AllocationTarget:
    signal_symbol: str
    execution_symbol: str
    sector: str
    quantity: int
    price: float
    risk_multiplier: float
    market_value: float
    risk_exposure: float
    used_substitution: bool


@dataclass(frozen=True, slots=True)
class ExecutableSimulation:
    candidate: CrossSectionalCandidate
    initial_equity: float
    final_equity: float
    total_return: float
    max_drawdown: float
    annualized_sharpe: float
    worst_day: float
    commission: float
    trade_count: int
    average_cash_pct: float
    max_risk_exposure_pct: float
    unaffordable_signal_count: int
    rebalance_count: int
    substitution_forced_exit_count: int
    max_substitution_holding_days_observed: int
    equity_curve: tuple[tuple[date, float], ...]


def allocate_whole_share_targets(
    ranked_symbols: list[str],
    *,
    records: dict[str, UniverseRecord],
    prices: dict[str, float],
    substitutions: dict[str, SubstitutionRule],
    equity: float,
    max_gross_risk_pct: float,
    max_position_risk_pct: float,
    max_holdings: int,
    minimum_commission: float,
    allow_short_term_substitutions: bool,
    blocked_execution_symbols: frozenset[str] = frozenset(),
) -> tuple[tuple[AllocationTarget, ...], int]:
    if equity <= 0:
        return (), 0
    if not 0 < max_gross_risk_pct <= 1:
        raise ValueError("gross risk percentage must be in (0, 1]")
    if not 0 < max_position_risk_pct <= max_gross_risk_pct:
        raise ValueError("position risk percentage is invalid")
    active_substitutions = (
        substitutions if allow_short_term_substitutions else {}
    )
    sizer = IntegerPositionSizer(active_substitutions)
    decimal_prices = {
        symbol: Decimal(str(price))
        for symbol, price in prices.items()
        if price > 0
    }
    gross_budget = equity * max_gross_risk_pct
    position_budget = equity * max_position_risk_pct
    remaining = gross_budget
    selected: list[AllocationTarget] = []
    used_sectors: set[str] = set()
    used_executions: set[str] = set()
    unaffordable = 0
    for signal_symbol in ranked_symbols:
        record = records.get(signal_symbol)
        if record is None or record.sector in used_sectors:
            continue
        if signal_symbol not in decimal_prices:
            continue
        target_risk = min(position_budget, remaining)
        if target_risk <= 0:
            break
        try:
            resolved = sizer.resolve(
                signal_symbol=signal_symbol,
                target_risk_exposure=Decimal(str(target_risk)),
                prices=decimal_prices,
            )
        except KeyError:
            resolved = None
        if resolved is None:
            unaffordable += 1
            continue
        if resolved.execution_symbol in blocked_execution_symbols:
            continue
        if resolved.execution_symbol in used_executions:
            continue
        quantity = resolved.quantity
        unit_price = float(resolved.price)
        while (
            quantity > 0
            and quantity * unit_price + minimum_commission > equity
        ):
            quantity -= 1
        if quantity <= 0:
            unaffordable += 1
            continue
        market_value = quantity * unit_price
        risk_multiplier = float(resolved.exposure_multiplier)
        risk_exposure = market_value * risk_multiplier
        if risk_exposure > remaining + 1e-8:
            unaffordable += 1
            continue
        selected.append(
            AllocationTarget(
                signal_symbol=signal_symbol,
                execution_symbol=resolved.execution_symbol,
                sector=record.sector,
                quantity=quantity,
                price=unit_price,
                risk_multiplier=risk_multiplier,
                market_value=market_value,
                risk_exposure=risk_exposure,
                used_substitution=resolved.used_substitution,
            )
        )
        used_sectors.add(record.sector)
        used_executions.add(resolved.execution_symbol)
        remaining -= risk_exposure
        if len(selected) >= max_holdings:
            break
    gross = sum(row.risk_exposure for row in selected)
    if gross > gross_budget + 1e-6:
        raise AssertionError("whole-share allocation exceeded gross risk")
    if any(
        row.risk_exposure > position_budget + 1e-6
        for row in selected
    ):
        raise AssertionError(
            "whole-share allocation exceeded position risk"
        )
    return tuple(selected), unaffordable


def simulate_executable_cross_sectional(
    candidate: CrossSectionalCandidate,
    *,
    data: dict[str, tuple[DailyBar, ...]],
    records: dict[str, UniverseRecord],
    substitutions: dict[str, SubstitutionRule],
    start_date: date,
    end_date: date,
    initial_equity: float,
    max_gross_risk_pct: float,
    max_position_risk_pct: float,
    per_share_commission: float,
    minimum_commission: float,
    slippage_bps: float,
    max_substitution_holding_days: int = 5,
    liquidate_at_end: bool = False,
) -> ExecutableSimulation:
    calendar = [
        bar.trading_date
        for bar in data["SPY"]
        if start_date <= bar.trading_date <= end_date
    ]
    if len(calendar) < 2:
        raise ValueError("simulation window is too short")
    indexed = {
        symbol: {
            bar.trading_date: (index, bar)
            for index, bar in enumerate(bars)
        }
        for symbol, bars in data.items()
    }
    cash = initial_equity
    positions: dict[str, int] = {}
    multipliers: dict[str, float] = {}
    holding_start_indices: dict[str, int] = {}
    last_close: dict[str, float] = {}
    curve: list[tuple[date, float]] = []
    cash_ratios: list[float] = []
    risk_ratios: list[float] = []
    total_commission = 0.0
    trade_count = 0
    unaffordable_count = 0
    rebalance_count = 0
    substitution_forced_exit_count = 0
    max_substitution_holding_days_observed = 0
    previous_equity = initial_equity
    daily_returns: list[float] = []
    price_symbols = set(records) | {
        rule.execution_symbol
        for rule in substitutions.values()
    }
    for day_index, trading_date in enumerate(calendar):
        prices = {
            symbol: float(indexed[symbol][trading_date][1].open)
            for symbol in price_symbols
            if trading_date in indexed.get(symbol, {})
        }
        expired_today: set[str] = set()
        for symbol, quantity in tuple(positions.items()):
            if multipliers.get(symbol, 1.0) <= 1:
                continue
            start_index = holding_start_indices.get(
                symbol, day_index
            )
            held_days = day_index - start_index
            max_substitution_holding_days_observed = max(
                max_substitution_holding_days_observed,
                held_days,
            )
            if held_days < max_substitution_holding_days:
                continue
            price = prices.get(symbol, 0.0)
            if price <= 0:
                raise ValueError(
                    "cannot enforce substitution holding limit "
                    f"without an opening price for {symbol}"
                )
            executed = price * (1 - slippage_bps / 10_000)
            commission = max(
                minimum_commission,
                per_share_commission * quantity,
            )
            cash += quantity * executed - commission
            positions.pop(symbol, None)
            multipliers.pop(symbol, None)
            holding_start_indices.pop(symbol, None)
            total_commission += commission
            trade_count += 1
            substitution_forced_exit_count += 1
            expired_today.add(symbol)
        if day_index % candidate.rebalance_days == 0:
            rebalance_count += 1
            ranked = _rank_signals(
                trading_date,
                candidate,
                data=data,
                records=records,
                indexed=indexed,
            )
            current_prices = {
                symbol: prices.get(
                    symbol, last_close.get(symbol, 0.0)
                )
                for symbol in positions
            }
            marked_equity = cash + sum(
                quantity * current_prices.get(symbol, 0.0)
                for symbol, quantity in positions.items()
            )
            targets, rejected = allocate_whole_share_targets(
                [symbol for symbol, _ in ranked],
                records=records,
                prices=prices,
                substitutions=substitutions,
                equity=marked_equity,
                max_gross_risk_pct=max_gross_risk_pct,
                max_position_risk_pct=max_position_risk_pct,
                max_holdings=candidate.top_n,
                minimum_commission=minimum_commission,
                allow_short_term_substitutions=(
                    candidate.rebalance_days
                    <= max_substitution_holding_days
                ),
                blocked_execution_symbols=frozenset(expired_today),
            )
            unaffordable_count += rejected
            desired = {
                row.execution_symbol: row.quantity
                for row in targets
            }
            target_multipliers = {
                row.execution_symbol: row.risk_multiplier
                for row in targets
            }
            for symbol, quantity in tuple(positions.items()):
                sell_quantity = max(
                    0, quantity - desired.get(symbol, 0)
                )
                price = prices.get(
                    symbol, last_close.get(symbol, 0.0)
                )
                if sell_quantity and price > 0:
                    executed = price * (
                        1 - slippage_bps / 10_000
                    )
                    commission = max(
                        minimum_commission,
                        per_share_commission * sell_quantity,
                    )
                    cash += sell_quantity * executed - commission
                    positions[symbol] -= sell_quantity
                    if positions[symbol] == 0:
                        positions.pop(symbol)
                        multipliers.pop(symbol, None)
                        holding_start_indices.pop(symbol, None)
                    total_commission += commission
                    trade_count += 1
            for target in targets:
                symbol = target.execution_symbol
                buy_quantity = max(
                    0,
                    target.quantity - positions.get(symbol, 0),
                )
                price = prices[symbol] * (
                    1 + slippage_bps / 10_000
                )
                while buy_quantity > 0:
                    commission = max(
                        minimum_commission,
                        per_share_commission * buy_quantity,
                    )
                    if buy_quantity * price + commission <= cash:
                        break
                    buy_quantity -= 1
                if buy_quantity:
                    commission = max(
                        minimum_commission,
                        per_share_commission * buy_quantity,
                    )
                    cash -= buy_quantity * price + commission
                    was_held = symbol in positions
                    positions[symbol] = positions.get(
                        symbol, 0
                    ) + buy_quantity
                    multipliers[symbol] = target_multipliers[symbol]
                    if not was_held:
                        holding_start_indices[symbol] = day_index
                    total_commission += commission
                    trade_count += 1
        for symbol in positions:
            location = indexed.get(symbol, {}).get(trading_date)
            if location is not None:
                last_close[symbol] = float(location[1].close)
        equity = cash + sum(
            quantity * last_close.get(symbol, 0.0)
            for symbol, quantity in positions.items()
        )
        gross_risk = sum(
            quantity
            * last_close.get(symbol, 0.0)
            * multipliers.get(symbol, 1.0)
            for symbol, quantity in positions.items()
        )
        curve.append((trading_date, equity))
        cash_ratios.append(cash / equity if equity > 0 else 0)
        risk_ratios.append(
            gross_risk / equity if equity > 0 else 0
        )
        if previous_equity > 0:
            daily_returns.append(equity / previous_equity - 1)
        previous_equity = equity
    if liquidate_at_end and positions:
        for symbol, quantity in tuple(positions.items()):
            price = last_close.get(symbol, 0.0) * (
                1 - slippage_bps / 10_000
            )
            commission = max(
                minimum_commission,
                per_share_commission * quantity,
            )
            cash += quantity * price - commission
            total_commission += commission
            trade_count += 1
        curve[-1] = (curve[-1][0], cash)
    metrics = _curve_metrics(initial_equity, tuple(curve))
    return ExecutableSimulation(
        candidate=candidate,
        initial_equity=initial_equity,
        final_equity=curve[-1][1],
        total_return=metrics["total_return"],
        max_drawdown=metrics["max_drawdown"],
        annualized_sharpe=metrics["annualized_sharpe"],
        worst_day=metrics["worst_day"],
        commission=total_commission,
        trade_count=trade_count,
        average_cash_pct=fmean(cash_ratios),
        max_risk_exposure_pct=max(risk_ratios, default=0),
        unaffordable_signal_count=unaffordable_count,
        rebalance_count=rebalance_count,
        substitution_forced_exit_count=(
            substitution_forced_exit_count
        ),
        max_substitution_holding_days_observed=(
            max_substitution_holding_days_observed
        ),
        equity_curve=tuple(curve),
    )


def run_executable_cross_sectional_research(
    config: AppConfig,
    universe: UniverseSnapshot,
    *,
    data_root: Path,
    fallback_data_root: Path | None = None,
    candidates: tuple[
        CrossSectionalCandidate, ...
    ] = DEFAULT_CANDIDATES,
    minimum_train_days: int = 504,
    test_days: int = 126,
) -> dict:
    data, records = _load_data(
        universe,
        data_root=data_root,
        substitutions=config.substitutions,
        fallback_data_root=fallback_data_root,
    )
    calendar = tuple(bar.trading_date for bar in data["SPY"])
    if len(calendar) < minimum_train_days + test_days:
        raise ValueError("not enough history for executable research")
    common = {
        "data": data,
        "records": records,
        "substitutions": config.substitutions,
        "initial_equity": float(config.initial_equity),
        "max_gross_risk_pct": float(
            config.research_portfolio.max_gross_exposure_pct
        ),
        "max_position_risk_pct": float(
            config.research_portfolio.max_position_exposure_pct
        ),
        "per_share_commission": float(
            config.execution.per_share_commission
        ),
        "minimum_commission": float(
            config.execution.minimum_commission
        ),
        "slippage_bps": float(config.execution.slippage_bps),
    }
    folds: list[dict] = []
    oos_curve: list[tuple[date, float]] = []
    stressed_curve: list[tuple[date, float]] = []
    oos_equity = float(config.initial_equity)
    stress_equity = float(config.initial_equity)
    test_start_index = minimum_train_days
    fold_number = 1
    while test_start_index + test_days <= len(calendar):
        train_start = calendar[0]
        train_end = calendar[test_start_index - 1]
        test_start = calendar[test_start_index]
        test_end = calendar[test_start_index + test_days - 1]
        training = [
            simulate_executable_cross_sectional(
                candidate,
                start_date=train_start,
                end_date=train_end,
                **common,
            )
            for candidate in candidates
        ]
        selected = max(
            training,
            key=lambda row: (
                row.annualized_sharpe,
                row.total_return,
                -row.max_drawdown,
            ),
        )
        test_common = dict(common)
        test_common["initial_equity"] = oos_equity
        test_result = simulate_executable_cross_sectional(
            selected.candidate,
            start_date=test_start,
            end_date=test_end,
            liquidate_at_end=True,
            **test_common,
        )
        stress_common = dict(common)
        stress_common["initial_equity"] = stress_equity
        stress_common["per_share_commission"] *= 2
        stress_common["minimum_commission"] *= 2
        stress_common["slippage_bps"] *= 2
        stress = simulate_executable_cross_sectional(
            selected.candidate,
            start_date=test_start,
            end_date=test_end,
            liquidate_at_end=True,
            **stress_common,
        )
        oos_equity = test_result.final_equity
        stress_equity = stress.final_equity
        oos_curve.extend(test_result.equity_curve)
        stressed_curve.extend(stress.equity_curve)
        folds.append(
            {
                "fold": fold_number,
                "train_start": train_start.isoformat(),
                "train_end": train_end.isoformat(),
                "test_start": test_start.isoformat(),
                "test_end": test_end.isoformat(),
                "selected": selected.candidate.name,
                "training_sharpe": selected.annualized_sharpe,
                "oos_return": test_result.total_return,
                "oos_max_drawdown": test_result.max_drawdown,
                "oos_commission": test_result.commission,
                "oos_trade_count": test_result.trade_count,
                "average_cash_pct": test_result.average_cash_pct,
                "max_risk_exposure_pct": (
                    test_result.max_risk_exposure_pct
                ),
                "unaffordable_signal_count": (
                    test_result.unaffordable_signal_count
                ),
                "substitution_forced_exit_count": (
                    test_result.substitution_forced_exit_count
                ),
                "max_substitution_holding_days_observed": (
                    test_result.max_substitution_holding_days_observed
                ),
                "cost_2x_return": stress.total_return,
            }
        )
        fold_number += 1
        test_start_index += test_days
    metrics = _curve_metrics(
        float(config.initial_equity), tuple(oos_curve)
    )
    stress_metrics = _curve_metrics(
        float(config.initial_equity), tuple(stressed_curve)
    )
    contributions = [
        log(1 + row["oos_return"])
        for row in folds
        if row["oos_return"] > -1
    ]
    positive_total = sum(value for value in contributions if value > 0)
    concentration = (
        max((value for value in contributions if value > 0), default=0)
        / positive_total
        if positive_total > 0
        else 1.0
    )
    gate_reasons = [
        "历史 OHLC 为事后复权研究代理，不能用于历史整股成交",
        "历史时点股票池与退市样本尚未补齐",
        "DSR/PBO 尚未完成",
    ]
    if (
        max(
            (
                row["max_risk_exposure_pct"]
                for row in folds
            ),
            default=0,
        )
        > common["max_gross_risk_pct"] + 1e-9
    ):
        gate_reasons.append("每日风险暴露曾超过配置硬上限")
    if stress_metrics["total_return"] <= 0:
        gate_reasons.append("2倍成本压力下净收益不为正")
    if max(
        (
            row["max_substitution_holding_days_observed"]
            for row in folds
        ),
        default=0,
    ) > 5:
        gate_reasons.append("短期替代品持有期曾超过5个交易日")
    if concentration > 0.40:
        gate_reasons.append("单折正对数收益贡献超过40%")
    return {
        "research_version": 2,
        "status": "research_exploratory",
        "deployable": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "universe_size": len(records),
            "initial_equity": float(config.initial_equity),
            "last_completed_date": calendar[-1].isoformat(),
            "whole_shares_only": True,
            "max_gross_risk_pct": common["max_gross_risk_pct"],
            "max_position_risk_pct": common[
                "max_position_risk_pct"
            ],
            "risk_limits_role": (
                "research-only; Paper/Live limits remain unchanged"
            ),
            "margin_borrowing": False,
            "china_concept_policy": "fail_closed",
            "historical_price_model": (
                "total_return_adjusted_research_proxy"
            ),
        },
        "execution_policy": {
            "shared_integer_position_sizer": True,
            "unaffordable_rank_backfill": True,
            "approved_short_term_substitutions": (
                "enabled only for rebalance <=5 days; "
                "risk exposure uses the configured multiplier; "
                "hard exit after 5 trading days"
            ),
            "longer_horizon_substitution_policy": (
                "direct signal symbol if affordable, otherwise "
                "backfill/cash"
            ),
        },
        "out_of_sample": {
            "strategy": metrics,
            "cost_2x": stress_metrics,
            "folds": folds,
            "positive_log_return_concentration": concentration,
        },
        "chart_data": [
            {
                "date": base[0].isoformat(),
                "strategy_equity": base[1],
                "cost_2x_equity": stress[1],
            }
            for base, stress in zip(oos_curve, stressed_curve)
            if base[0] == stress[0]
        ],
        "promotion_gate": {
            "passed": False,
            "reasons": gate_reasons,
        },
        "limitations": [
            (
                "当前复权 OHLC 仅适合收益信号研究；拆股前名义价格、"
                "整股数量、现金和佣金不具备历史时点可执行性。"
            ),
            "当前上市成分仍有幸存者偏差，只能作为探索性 OOS。",
            "日 K 无法验证日内做 T、点差、部分成交或止损。",
            "策略尚未通过 DSR/PBO 与历史成分压力测试。",
            "历史收益不保证未来盈利。",
        ],
    }


def save_executable_research(result: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary.flush()
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)
    return path


def _curve_metrics(
    initial_equity: float,
    curve: tuple[tuple[date, float], ...],
) -> dict[str, float]:
    if not curve:
        raise ValueError("equity curve is empty")
    equities = [value for _, value in curve]
    returns = [
        current / previous - 1
        for previous, current in zip(
            [initial_equity] + equities[:-1], equities
        )
        if previous > 0
    ]
    peak = initial_equity
    max_drawdown = 0.0
    for equity in equities:
        peak = max(peak, equity)
        max_drawdown = max(
            max_drawdown,
            (peak - equity) / peak if peak > 0 else 0,
        )
    deviation = pstdev(returns) if len(returns) > 1 else 0
    sharpe = (
        fmean(returns) / deviation * sqrt(252)
        if deviation > 0
        else 0.0
    )
    return {
        "initial_equity": initial_equity,
        "final_equity": equities[-1],
        "total_return": equities[-1] / initial_equity - 1,
        "max_drawdown": max_drawdown,
        "annualized_sharpe": sharpe,
        "worst_day": min(returns, default=0.0),
    }
