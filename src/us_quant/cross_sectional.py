from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from math import floor, sqrt
from pathlib import Path
from statistics import fmean, pstdev
import tempfile

from us_quant.config import AppConfig
from us_quant.market_data import DailyBar, load_latest_normalized_series
from us_quant.universe import UniverseRecord, UniverseSnapshot


@dataclass(frozen=True, slots=True)
class CrossSectionalCandidate:
    lookback_days: int
    rebalance_days: int
    top_n: int

    @property
    def name(self) -> str:
        frequency = (
            "weekly" if self.rebalance_days == 5 else "monthly"
        )
        return (
            f"momentum_{self.lookback_days}_{frequency}_top{self.top_n}"
        )


@dataclass(frozen=True, slots=True)
class SimulationResult:
    candidate: CrossSectionalCandidate
    start_date: date
    end_date: date
    initial_equity: float
    final_equity: float
    total_return: float
    max_drawdown: float
    annualized_sharpe: float
    worst_day: float
    commission: float
    trade_count: int
    equity_curve: tuple[tuple[date, float], ...]


DEFAULT_CANDIDATES = tuple(
    CrossSectionalCandidate(lookback, rebalance, top_n)
    for lookback in (63, 126)
    for rebalance in (5, 21)
    for top_n in (3, 5)
)


def run_cross_sectional_research(
    config: AppConfig,
    universe: UniverseSnapshot,
    *,
    data_root: str | Path = "data",
    candidates: tuple[
        CrossSectionalCandidate, ...
    ] = DEFAULT_CANDIDATES,
    minimum_train_days: int = 504,
    test_days: int = 126,
) -> dict:
    data, records = _load_data(
        universe,
        data_root=Path(data_root),
        substitutions=config.substitutions,
    )
    if "SPY" not in data:
        raise ValueError("SPY history is required for the research calendar")
    calendar = tuple(bar.trading_date for bar in data["SPY"])
    if len(calendar) < minimum_train_days + test_days:
        raise ValueError("not enough history for walk-forward research")

    folds: list[dict] = []
    oos_curve: list[tuple[date, float]] = []
    oos_equity = float(config.initial_equity)
    benchmark_equity = float(config.initial_equity)
    benchmark_curve: list[tuple[date, float]] = []
    test_start_index = minimum_train_days
    fold_number = 1
    while test_start_index + test_days <= len(calendar):
        train_start = calendar[0]
        train_end = calendar[test_start_index - 1]
        test_start = calendar[test_start_index]
        test_end = calendar[test_start_index + test_days - 1]
        training_results = [
            simulate_cross_sectional(
                candidate,
                data=data,
                records=records,
                substitutions=config.substitutions,
                start_date=train_start,
                end_date=train_end,
                initial_equity=float(config.initial_equity),
                per_share_commission=float(
                    config.execution.per_share_commission
                ),
                minimum_commission=float(
                    config.execution.minimum_commission
                ),
                slippage_bps=float(
                    config.execution.slippage_bps
                ),
            )
            for candidate in candidates
        ]
        selected = max(
            training_results,
            key=lambda row: (
                row.annualized_sharpe,
                row.total_return,
                -row.max_drawdown,
                -row.trade_count,
            ),
        )
        test_result = simulate_cross_sectional(
            selected.candidate,
            data=data,
            records=records,
            substitutions=config.substitutions,
            start_date=test_start,
            end_date=test_end,
            initial_equity=oos_equity,
            per_share_commission=float(
                config.execution.per_share_commission
            ),
            minimum_commission=float(
                config.execution.minimum_commission
            ),
            slippage_bps=float(config.execution.slippage_bps),
            liquidate_at_end=True,
        )
        benchmark = simulate_buy_and_hold(
            data["SPY"],
            start_date=test_start,
            end_date=test_end,
            initial_equity=benchmark_equity,
            per_share_commission=float(
                config.execution.per_share_commission
            ),
            minimum_commission=float(
                config.execution.minimum_commission
            ),
            slippage_bps=float(config.execution.slippage_bps),
        )
        oos_equity = test_result.final_equity
        benchmark_equity = benchmark.final_equity
        oos_curve.extend(test_result.equity_curve)
        benchmark_curve.extend(benchmark.equity_curve)
        folds.append(
            {
                "fold": fold_number,
                "train_start": train_start.isoformat(),
                "train_end": train_end.isoformat(),
                "test_start": test_start.isoformat(),
                "test_end": test_end.isoformat(),
                "selected": selected.candidate.name,
                "training_sharpe": selected.annualized_sharpe,
                "training_return": selected.total_return,
                "oos_return": test_result.total_return,
                "oos_max_drawdown": test_result.max_drawdown,
                "oos_commission": test_result.commission,
                "oos_trade_count": test_result.trade_count,
                "spy_return": benchmark.total_return,
            }
        )
        fold_number += 1
        test_start_index += test_days

    strategy_metrics = _curve_metrics(
        float(config.initial_equity),
        tuple(oos_curve),
    )
    benchmark_metrics = _curve_metrics(
        float(config.initial_equity),
        tuple(benchmark_curve),
    )
    full_candidate_results = [
        simulate_cross_sectional(
            candidate,
            data=data,
            records=records,
            substitutions=config.substitutions,
            start_date=calendar[minimum_train_days],
            end_date=calendar[-1],
            initial_equity=float(config.initial_equity),
            per_share_commission=float(
                config.execution.per_share_commission
            ),
            minimum_commission=float(
                config.execution.minimum_commission
            ),
            slippage_bps=float(config.execution.slippage_bps),
        )
        for candidate in candidates
    ]
    excluded_tail = len(calendar) - (
        minimum_train_days + len(folds) * test_days
    )
    return {
        "research_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "universe_size": len(records),
            "calendar_symbol": "SPY",
            "first_date": calendar[0].isoformat(),
            "last_completed_date": calendar[-1].isoformat(),
            "initial_equity": float(config.initial_equity),
            "whole_shares_only": True,
            "margin_borrowing": False,
            "china_concept_policy": "strictly excluded upstream",
            "leader_policy": (
                "only tier 1 leaders and tier 2 quality names can be held"
            ),
            "substitution_policy": (
                "disabled unless the user explicitly configures a "
                "symbol mapping"
            ),
        },
        "methodology": {
            "signal": (
                "positive cross-sectional momentum divided by "
                "63-day volatility, above 200-day moving average"
            ),
            "sector_constraint": "maximum one selected signal per sector",
            "execution": "prior close signal, next session open",
            "selection": (
                "anchored walk-forward; training Sharpe after costs"
            ),
            "minimum_train_days": minimum_train_days,
            "test_days": test_days,
            "full_test_folds_only": True,
            "excluded_tail_days": excluded_tail,
            "candidates": [row.name for row in candidates],
            "costs": {
                "per_share_commission": float(
                    config.execution.per_share_commission
                ),
                "minimum_commission": float(
                    config.execution.minimum_commission
                ),
                "slippage_bps": float(
                    config.execution.slippage_bps
                ),
            },
        },
        "out_of_sample": {
            "strategy": strategy_metrics,
            "spy_whole_share_benchmark": benchmark_metrics,
            "folds": folds,
        },
        "candidate_period_results": [
            {
                "candidate": row.candidate.name,
                "return": row.total_return,
                "max_drawdown": row.max_drawdown,
                "sharpe": row.annualized_sharpe,
                "commission": row.commission,
                "trade_count": row.trade_count,
            }
            for row in full_candidate_results
        ],
        "chart_data": [
            {
                "date": strategy_row[0].isoformat(),
                "strategy_equity": strategy_row[1],
                "spy_equity": benchmark_row[1],
            }
            for strategy_row, benchmark_row in zip(
                oos_curve,
                benchmark_curve,
            )
            if strategy_row[0] == benchmark_row[0]
        ],
        "limitations": [
            (
                "The universe is based on current listings and reviewed "
                "leaders, so survivorship bias remains."
            ),
            (
                "Secondary public adjusted daily bars are used where IBKR "
                "history was blocked by another-IP session state."
            ),
            (
                "Daily bars cannot validate intraday T-trading, spreads, "
                "partial fills, or stop execution."
            ),
            (
                "Fold boundaries reset positions to cash; this is "
                "conservative for continuity but changes turnover."
            ),
            "Historical results do not guarantee future profitability.",
        ],
    }


def simulate_cross_sectional(
    candidate: CrossSectionalCandidate,
    *,
    data: dict[str, tuple[DailyBar, ...]],
    records: dict[str, UniverseRecord],
    substitutions: dict,
    start_date: date,
    end_date: date,
    initial_equity: float,
    per_share_commission: float,
    minimum_commission: float,
        slippage_bps: float,
    liquidate_at_end: bool = False,
) -> SimulationResult:
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
    last_close: dict[str, float] = {}
    curve: list[tuple[date, float]] = []
    total_commission = 0.0
    trade_count = 0
    previous_equity = initial_equity
    daily_returns: list[float] = []
    peak = initial_equity
    max_drawdown = 0.0
    for day_index, trading_date in enumerate(calendar):
        if day_index % candidate.rebalance_days == 0:
            ranked = _rank_signals(
                trading_date,
                candidate,
                data=data,
                records=records,
                indexed=indexed,
            )
            selected = _sector_diversified(
                ranked,
                records=records,
                top_n=candidate.top_n,
            )
            target_executions: list[str] = []
            for symbol in selected:
                execution = (
                    substitutions[symbol].execution_symbol
                    if symbol in substitutions
                    else symbol
                )
                if (
                    execution not in target_executions
                    and execution in indexed
                    and trading_date in indexed[execution]
                ):
                    target_executions.append(execution)
            current_prices = {
                symbol: float(indexed[symbol][trading_date][1].open)
                for symbol in set(positions) | set(target_executions)
                if trading_date in indexed.get(symbol, {})
            }
            marked_equity = cash + sum(
                quantity
                * current_prices.get(
                    symbol,
                    last_close.get(symbol, 0.0),
                )
                for symbol, quantity in positions.items()
            )
            desired: dict[str, int] = {}
            if target_executions:
                target_value = marked_equity / len(target_executions)
                for symbol in target_executions:
                    price = current_prices[symbol] * (
                        1 + slippage_bps / 10_000
                    )
                    desired[symbol] = max(
                        0,
                        floor(
                            (
                                target_value - minimum_commission
                            )
                            / price
                        ),
                    )
            for symbol, quantity in tuple(positions.items()):
                sell_quantity = max(
                    0,
                    quantity - desired.get(symbol, 0),
                )
                if sell_quantity and symbol in current_prices:
                    price = current_prices[symbol] * (
                        1 - slippage_bps / 10_000
                    )
                    commission = max(
                        minimum_commission,
                        per_share_commission * sell_quantity,
                    )
                    cash += sell_quantity * price - commission
                    positions[symbol] -= sell_quantity
                    if positions[symbol] == 0:
                        positions.pop(symbol)
                    total_commission += commission
                    trade_count += 1
            for symbol in target_executions:
                buy_quantity = max(
                    0,
                    desired[symbol] - positions.get(symbol, 0),
                )
                price = current_prices[symbol] * (
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
                    positions[symbol] = (
                        positions.get(symbol, 0) + buy_quantity
                    )
                    total_commission += commission
                    trade_count += 1
        for symbol in positions:
            if trading_date in indexed.get(symbol, {}):
                last_close[symbol] = float(
                    indexed[symbol][trading_date][1].close
                )
        equity = cash + sum(
            quantity * last_close.get(symbol, 0.0)
            for symbol, quantity in positions.items()
        )
        curve.append((trading_date, equity))
        if previous_equity > 0:
            daily_returns.append(equity / previous_equity - 1)
        previous_equity = equity
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    if liquidate_at_end and positions:
        for symbol, quantity in positions.items():
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
    final_equity = curve[-1][1]
    return SimulationResult(
        candidate=candidate,
        start_date=start_date,
        end_date=end_date,
        initial_equity=initial_equity,
        final_equity=final_equity,
        total_return=final_equity / initial_equity - 1,
        max_drawdown=metrics["max_drawdown"],
        annualized_sharpe=metrics["annualized_sharpe"],
        worst_day=metrics["worst_day"],
        commission=total_commission,
        trade_count=trade_count,
        equity_curve=tuple(curve),
    )


def simulate_buy_and_hold(
    bars: tuple[DailyBar, ...],
    *,
    start_date: date,
    end_date: date,
    initial_equity: float,
    per_share_commission: float,
    minimum_commission: float,
    slippage_bps: float,
) -> SimulationResult:
    window = [
        bar for bar in bars if start_date <= bar.trading_date <= end_date
    ]
    if len(window) < 2:
        raise ValueError("benchmark window is too short")
    buy_price = float(window[0].open) * (
        1 + slippage_bps / 10_000
    )
    quantity = floor(
        (initial_equity - minimum_commission) / buy_price
    )
    commission = (
        max(minimum_commission, per_share_commission * quantity)
        if quantity
        else 0.0
    )
    cash = initial_equity - quantity * buy_price - commission
    curve = [
        (bar.trading_date, cash + quantity * float(bar.close))
        for bar in window
    ]
    if quantity:
        sell_price = float(window[-1].close) * (
            1 - slippage_bps / 10_000
        )
        sell_commission = max(
            minimum_commission,
            per_share_commission * quantity,
        )
        cash += quantity * sell_price - sell_commission
        commission += sell_commission
        curve[-1] = (curve[-1][0], cash)
    curve_tuple = tuple(curve)
    metrics = _curve_metrics(initial_equity, curve_tuple)
    return SimulationResult(
        candidate=CrossSectionalCandidate(0, 0, 1),
        start_date=start_date,
        end_date=end_date,
        initial_equity=initial_equity,
        final_equity=curve_tuple[-1][1],
        total_return=metrics["total_return"],
        max_drawdown=metrics["max_drawdown"],
        annualized_sharpe=metrics["annualized_sharpe"],
        worst_day=metrics["worst_day"],
        commission=commission,
        trade_count=2 if quantity else 0,
        equity_curve=curve_tuple,
    )


def save_cross_sectional_research(
    result: dict,
    path: str | Path = (
        "research/results/cross_sectional_research.json"
    ),
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary.flush()
        temporary_path = Path(temporary.name)
    temporary_path.replace(target)
    return target


def _load_data(
    universe: UniverseSnapshot,
    *,
    data_root: Path,
    substitutions: dict,
    fallback_data_root: Path | None = None,
) -> tuple[
    dict[str, tuple[DailyBar, ...]],
    dict[str, UniverseRecord],
]:
    records = {
        row.symbol: row
        for row in universe.records
        if row.eligible_for_trading and row.leader_tier in {1, 2}
    }
    substitution_targets = {
        rule.execution_symbol for rule in substitutions.values()
    }
    symbols = set(records) | substitution_targets | {"SPY"}
    data: dict[str, tuple[DailyBar, ...]] = {}
    for symbol in symbols:
        try:
            loaded = load_latest_normalized_series(
                symbol,
                data_root=data_root,
                fallback_data_root=fallback_data_root,
            )
        except (FileNotFoundError, ValueError):
            continue
        if len(loaded.bars) >= 200:
            data[symbol] = loaded.bars
    records = {
        symbol: row
        for symbol, row in records.items()
        if symbol in data
        and (
            symbol not in substitutions
            or substitutions[symbol].execution_symbol in data
        )
        and symbol not in substitution_targets
    }
    return data, records


def _rank_signals(
    trading_date: date,
    candidate: CrossSectionalCandidate,
    *,
    data: dict[str, tuple[DailyBar, ...]],
    records: dict[str, UniverseRecord],
    indexed: dict[str, dict[date, tuple[int, DailyBar]]],
) -> list[tuple[str, float]]:
    scores: list[tuple[str, float]] = []
    for symbol in records:
        location = indexed[symbol].get(trading_date)
        if location is None:
            continue
        index, _ = location
        required = max(200, candidate.lookback_days + 1)
        if index < required:
            continue
        bars = data[symbol]
        closes = [
            float(bar.close)
            for bar in bars[index - required : index]
        ]
        latest = closes[-1]
        past = closes[-candidate.lookback_days - 1]
        sma_200 = sum(closes[-200:]) / 200
        returns = [
            current / previous - 1
            for previous, current in zip(
                closes[-64:-1],
                closes[-63:],
            )
            if previous > 0
        ]
        volatility = pstdev(returns) * sqrt(252)
        momentum = latest / past - 1
        if latest > sma_200 and momentum > 0 and volatility > 0:
            scores.append((symbol, momentum / volatility))
    return sorted(scores, key=lambda item: item[1], reverse=True)


def _sector_diversified(
    ranked: list[tuple[str, float]],
    *,
    records: dict[str, UniverseRecord],
    top_n: int,
) -> tuple[str, ...]:
    selected: list[str] = []
    sectors: set[str] = set()
    for symbol, _ in ranked:
        sector = records[symbol].sector
        if sector in sectors:
            continue
        selected.append(symbol)
        sectors.add(sector)
        if len(selected) == top_n:
            break
    return tuple(selected)


def _curve_metrics(
    initial_equity: float,
    curve: tuple[tuple[date, float], ...],
) -> dict[str, float]:
    if not curve:
        raise ValueError("equity curve is empty")
    values = [initial_equity] + [value for _, value in curve]
    returns = [
        current / previous - 1
        for previous, current in zip(values, values[1:])
        if previous > 0
    ]
    peak = initial_equity
    max_drawdown = 0.0
    for value in values[1:]:
        peak = max(peak, value)
        max_drawdown = max(
            max_drawdown,
            (peak - value) / peak if peak else 0.0,
        )
    volatility = pstdev(returns) if len(returns) > 1 else 0.0
    return {
        "days": float(len(curve)),
        "initial_equity": initial_equity,
        "final_equity": curve[-1][1],
        "total_return": curve[-1][1] / initial_equity - 1,
        "max_drawdown": max_drawdown,
        "annualized_sharpe": (
            fmean(returns) / volatility * sqrt(252)
            if volatility > 0
            else 0.0
        ),
        "worst_day": min(returns, default=0.0),
    }
