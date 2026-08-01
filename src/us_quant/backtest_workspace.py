from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json
from math import sqrt
import os
from pathlib import Path
from statistics import mean, pstdev
import tempfile
from typing import Mapping
from uuid import uuid4

from us_quant.backtest import BacktestEngine, BacktestResult, CostModel
from us_quant.market_data import (
    build_aligned_market_slices,
    load_latest_normalized_series,
)
from us_quant.portfolio import IntegerPositionSizer
from us_quant.strategy import (
    BuyAndHoldStrategy,
    DonchianBreakoutStrategy,
    MovingAverageTrendStrategy,
    RSIMeanReversionStrategy,
    Strategy,
)


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_id: str
    name: str
    description: str
    default_parameters: Mapping[str, int | str]


@dataclass(frozen=True, slots=True)
class BacktestRequest:
    strategy_id: str
    strategy_version_id: str
    parameter_hash: str
    code_hash: str
    parameters: Mapping[str, object]
    symbol: str
    start_date: date | None
    end_date: date | None
    initial_equity: Decimal
    target_weight: Decimal
    per_share_commission: Decimal
    minimum_commission: Decimal
    slippage_bps: Decimal


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    annualized_return: Decimal
    annualized_sharpe: Decimal
    annualized_sortino: Decimal
    calmar_ratio: Decimal
    annualized_volatility: Decimal
    turnover: Decimal
    worst_day: Decimal
    positive_day_ratio: Decimal


@dataclass(frozen=True, slots=True)
class BacktestRun:
    run_id: str
    request: BacktestRequest
    strategy: StrategySpec
    data_source: str
    data_hash: str
    price_basis: str
    first_date: date
    last_date: date
    result: BacktestResult
    metrics: BacktestMetrics


STRATEGY_SPECS = (
    StrategySpec(
        strategy_id="buy-hold",
        name="买入并持有基准",
        description="首个可执行开盘买入并持有，用作成本基准。",
        default_parameters={},
    ),
    StrategySpec(
        strategy_id="dual-ma-trend",
        name="双均线趋势",
        description="20/100 日均线多头时持有，否则现金。",
        default_parameters={"short_window": 20, "long_window": 100},
    ),
    StrategySpec(
        strategy_id="donchian-breakout",
        name="唐奇安突破",
        description="突破 55 日高点进入，跌破 20 日低点退出。",
        default_parameters={"entry_window": 55, "exit_window": 20},
    ),
    StrategySpec(
        strategy_id="rsi-mean-reversion",
        name="RSI 均值回归",
        description="RSI(5) 不高于 25 进入，不低于 55 退出。",
        default_parameters={
            "window": 5,
            "entry_threshold": "25",
            "exit_threshold": "55",
        },
    ),
)


def strategy_spec(strategy_id: str) -> StrategySpec:
    for spec in STRATEGY_SPECS:
        if spec.strategy_id == strategy_id:
            return spec
    raise ValueError(f"unsupported strategy: {strategy_id}")


def run_backtest(
    request: BacktestRequest,
    *,
    data_root: str | Path,
    fallback_data_root: str | Path | None = None,
) -> BacktestRun:
    symbol = request.symbol.strip().upper()
    if not symbol or not symbol.replace(".", "").replace("-", "").isalnum():
        raise ValueError("回测代码格式无效")
    if request.initial_equity <= 0:
        raise ValueError("初始资金必须大于 0")
    if request.target_weight <= 0 or request.target_weight > 1:
        raise ValueError("目标仓位必须在 (0, 100%] 内")
    loaded = load_latest_normalized_series(
        symbol,
        data_root=data_root,
        fallback_data_root=fallback_data_root,
    )
    bars = tuple(
        bar
        for bar in loaded.bars
        if (
            request.start_date is None
            or bar.trading_date >= request.start_date
        )
        and (
            request.end_date is None
            or bar.trading_date <= request.end_date
        )
    )
    if len(bars) < 30:
        raise ValueError("所选区间有效日 K 少于 30 根")
    filtered = type(loaded)(
        symbol=loaded.symbol,
        source_sha256=loaded.source_sha256,
        path=loaded.path,
        bars=bars,
        source=loaded.source,
        price_basis=loaded.price_basis,
    )
    slices = build_aligned_market_slices((filtered,))
    spec = strategy_spec(request.strategy_id)
    strategy = _build_strategy(
        spec,
        symbol,
        request.target_weight,
        request.parameters,
    )
    result = BacktestEngine(
        initial_equity=request.initial_equity,
        strategy=strategy,
        position_sizer=IntegerPositionSizer({}),
        cost_model=CostModel(
            per_share_commission=request.per_share_commission,
            minimum_commission=request.minimum_commission,
            slippage_bps=request.slippage_bps,
        ),
    ).run(slices)
    return BacktestRun(
        run_id=str(uuid4()),
        request=request,
        strategy=spec,
        data_source=loaded.source,
        data_hash=loaded.source_sha256,
        price_basis=loaded.price_basis,
        first_date=bars[0].trading_date,
        last_date=bars[-1].trading_date,
        result=result,
        metrics=_metrics(result),
    )


def _build_strategy(
    spec: StrategySpec,
    symbol: str,
    target_weight: Decimal,
    parameters: Mapping[str, object],
) -> Strategy:
    effective = {**spec.default_parameters, **parameters}
    if spec.strategy_id == "buy-hold":
        return BuyAndHoldStrategy(
            signal_symbol=symbol,
            target_weight=target_weight,
        )
    if spec.strategy_id == "dual-ma-trend":
        return MovingAverageTrendStrategy(
            signal_symbol=symbol,
            short_window=int(effective["short_window"]),
            long_window=int(effective["long_window"]),
            target_weight=target_weight,
        )
    if spec.strategy_id == "donchian-breakout":
        return DonchianBreakoutStrategy(
            signal_symbol=symbol,
            entry_window=int(effective["entry_window"]),
            exit_window=int(effective["exit_window"]),
            target_weight=target_weight,
        )
    if spec.strategy_id == "rsi-mean-reversion":
        return RSIMeanReversionStrategy(
            signal_symbol=symbol,
            window=int(effective["window"]),
            entry_threshold=Decimal(str(effective["entry_threshold"])),
            exit_threshold=Decimal(str(effective["exit_threshold"])),
            target_weight=target_weight,
        )
    raise ValueError(f"unsupported strategy: {spec.strategy_id}")


def _metrics(result: BacktestResult) -> BacktestMetrics:
    values = [float(value) for _, value in result.equity_curve]
    daily_returns = [
        values[index] / values[index - 1] - 1
        for index in range(1, len(values))
        if values[index - 1] > 0
    ]
    years = max((len(values) - 1) / 252, 1 / 252)
    annualized_return = (
        (float(result.final_equity / result.initial_equity) ** (1 / years))
        - 1
    )
    deviation = pstdev(daily_returns) if daily_returns else 0
    sharpe = (
        mean(daily_returns) / deviation * sqrt(252)
        if deviation > 0
        else 0
    )
    downside_deviation = (
        (
            sum(min(value, 0) ** 2 for value in daily_returns)
            / len(daily_returns)
        )
        ** 0.5
        if daily_returns
        else 0
    )
    sortino = (
        mean(daily_returns) / downside_deviation * sqrt(252)
        if downside_deviation > 0
        else 0
    )
    max_drawdown = float(result.max_drawdown)
    calmar = (
        annualized_return / max_drawdown
        if max_drawdown > 0
        else 0
    )
    average_equity = mean(values) if values else 0
    turnover = (
        sum(float(trade.notional) for trade in result.trades)
        / average_equity
        if average_equity > 0
        else 0
    )
    worst_day = min(daily_returns, default=0)
    positive_ratio = (
        sum(value > 0 for value in daily_returns) / len(daily_returns)
        if daily_returns
        else 0
    )
    return BacktestMetrics(
        annualized_return=Decimal(str(annualized_return)),
        annualized_sharpe=Decimal(str(sharpe)),
        annualized_sortino=Decimal(str(sortino)),
        calmar_ratio=Decimal(str(calmar)),
        annualized_volatility=Decimal(str(deviation * sqrt(252))),
        turnover=Decimal(str(turnover)),
        worst_day=Decimal(str(worst_day)),
        positive_day_ratio=Decimal(str(positive_ratio)),
    )


def save_backtest_run(
    run: BacktestRun,
    *,
    output_root: str | Path,
) -> Path:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{run.run_id}.json"
    payload = {
        "schema_version": 1,
        "run_id": run.run_id,
        "strategy": {
            "strategy_id": run.request.strategy_id,
            "strategy_version_id": run.request.strategy_version_id,
            "parameter_hash": run.request.parameter_hash,
            "code_hash": run.request.code_hash,
            "parameters": dict(run.request.parameters),
            "name": run.strategy.name,
        },
        "request": {
            "symbol": run.request.symbol,
            "start_date": (
                run.request.start_date.isoformat()
                if run.request.start_date
                else None
            ),
            "end_date": (
                run.request.end_date.isoformat()
                if run.request.end_date
                else None
            ),
            "initial_equity": str(run.request.initial_equity),
            "target_weight": str(run.request.target_weight),
            "per_share_commission": str(
                run.request.per_share_commission
            ),
            "minimum_commission": str(
                run.request.minimum_commission
            ),
            "slippage_bps": str(run.request.slippage_bps),
        },
        "data": {
            "source": run.data_source,
            "price_basis": run.price_basis,
            "data_hash": run.data_hash,
            "first_date": run.first_date.isoformat(),
            "last_date": run.last_date.isoformat(),
        },
        "metrics": {
            "initial_equity": str(run.result.initial_equity),
            "final_equity": str(run.result.final_equity),
            "total_return": str(run.result.total_return),
            "annualized_return": str(run.metrics.annualized_return),
            "annualized_sharpe": str(run.metrics.annualized_sharpe),
            "annualized_sortino": str(
                run.metrics.annualized_sortino
            ),
            "calmar_ratio": str(run.metrics.calmar_ratio),
            "annualized_volatility": str(
                run.metrics.annualized_volatility
            ),
            "turnover": str(run.metrics.turnover),
            "max_drawdown": str(run.result.max_drawdown),
            "worst_day": str(run.metrics.worst_day),
            "positive_day_ratio": str(
                run.metrics.positive_day_ratio
            ),
            "total_commission": str(run.result.total_commission),
            "trade_count": len(run.result.trades),
        },
        "equity_curve": [
            {
                "timestamp": timestamp.isoformat(),
                "equity": str(equity),
            }
            for timestamp, equity in run.result.equity_curve
        ],
        "trades": [
            {
                "signal_timestamp": trade.signal_timestamp.isoformat(),
                "timestamp": trade.timestamp.isoformat(),
                "signal_symbol": trade.signal_symbol,
                "execution_symbol": trade.execution_symbol,
                "side": trade.side.value,
                "quantity": trade.quantity,
                "raw_price": str(trade.raw_price),
                "fill_price": str(trade.fill_price),
                "notional": str(trade.notional),
                "slippage_cost": str(trade.slippage_cost),
                "commission": str(trade.commission),
                "position_after": trade.position_after,
                "cash_after": str(trade.cash_after),
                "reason": trade.reason,
                "used_substitution": trade.used_substitution,
            }
            for trade in run.result.trades
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    payload_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    payload["run_hash"] = payload_hash
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=root,
            prefix=f".{run.run_id}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, target)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return target
