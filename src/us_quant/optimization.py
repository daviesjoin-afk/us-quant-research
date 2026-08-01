from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import sqrt
from statistics import fmean, pstdev
from typing import Mapping, Sequence

from us_quant.backtest import BacktestEngine, BacktestResult, CostModel
from us_quant.domain import MarketSlice, ZERO
from us_quant.portfolio import IntegerPositionSizer
from us_quant.strategy import (
    DelayedActivationStrategy,
    MovingAverageTrendStrategy,
    TargetAllocation,
)


@dataclass(frozen=True, slots=True)
class MovingAverageCandidate:
    short_window: int
    long_window: int

    def __post_init__(self) -> None:
        if self.short_window <= 0 or self.long_window <= self.short_window:
            raise ValueError("require 0 < short_window < long_window")

    @property
    def name(self) -> str:
        return f"{self.short_window}/{self.long_window}"


@dataclass(frozen=True, slots=True)
class CandidateScore:
    candidate: MovingAverageCandidate
    annualized_sharpe: float
    total_return: Decimal
    max_drawdown: Decimal
    commission: Decimal


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold_number: int
    train_start: object
    train_end: object
    test_start: object
    test_end: object
    selected: CandidateScore
    out_of_sample_return: Decimal
    out_of_sample_commission: Decimal
    out_of_sample_trade_count: int


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    folds: tuple[WalkForwardFold, ...]
    compounded_out_of_sample_return: Decimal
    out_of_sample_max_drawdown: Decimal
    out_of_sample_commission: Decimal
    out_of_sample_trade_count: int
    out_of_sample_days: int
    excluded_tail_days: int
    out_of_sample_equity_curve: tuple[tuple[object, Decimal], ...]


def walk_forward_moving_average(
    slices: tuple[MarketSlice, ...],
    *,
    signal_symbol: str,
    initial_equity: Decimal,
    position_sizer: IntegerPositionSizer,
    cost_model: CostModel,
    candidates: tuple[MovingAverageCandidate, ...],
    target_weight: Decimal,
    minimum_train_days: int = 252,
    test_days: int = 63,
    include_partial_test_fold: bool = False,
    rebalance_interval_days: int = 1,
) -> WalkForwardResult:
    if not signal_symbol:
        raise ValueError("walk-forward requires a signal symbol")
    if not candidates:
        raise ValueError("walk-forward requires candidates")
    longest_window = max(
        candidate.long_window for candidate in candidates
    )
    if minimum_train_days <= longest_window:
        raise ValueError(
            "minimum training window must exceed every long window"
        )
    if test_days <= 0:
        raise ValueError("test days must be positive")
    if rebalance_interval_days <= 0:
        raise ValueError("rebalance interval must be positive")
    if len(slices) <= minimum_train_days:
        raise ValueError("not enough data for walk-forward evaluation")

    fold_boundaries: list[tuple[int, int]] = []
    test_start_index = minimum_train_days
    while test_start_index < len(slices):
        remaining = len(slices) - test_start_index
        if remaining < test_days and not include_partial_test_fold:
            break
        test_end_index = min(test_start_index + test_days, len(slices))
        fold_boundaries.append((test_start_index, test_end_index))
        test_start_index = test_end_index
    if not fold_boundaries:
        raise ValueError("not enough data for one complete test fold")

    common_activation_index = longest_window - 1
    selected_by_activation: dict[object, CandidateScore] = {}
    selections: list[CandidateScore] = []
    for test_start_index, _ in fold_boundaries:
        train_slices = slices[:test_start_index]
        candidate_scores = tuple(
            _score_candidate(
                train_slices,
                signal_symbol=signal_symbol,
                candidate=candidate,
                initial_equity=initial_equity,
                position_sizer=position_sizer,
                cost_model=cost_model,
                target_weight=target_weight,
                activation_index=common_activation_index,
                rebalance_interval_days=rebalance_interval_days,
            )
            for candidate in candidates
        )
        selected = max(
            candidate_scores,
            key=lambda score: (
                score.annualized_sharpe,
                float(score.total_return),
                -float(score.commission),
                -score.candidate.long_window,
            ),
        )
        selected_by_activation[
            slices[test_start_index - 1].timestamp
        ] = selected
        selections.append(selected)

    evaluated_end_index = fold_boundaries[-1][1]
    evaluation = BacktestEngine(
        initial_equity=initial_equity,
        strategy=_ScheduledMovingAverageStrategy(
            signal_symbol=signal_symbol,
            target_weight=target_weight,
            selected_by_activation=selected_by_activation,
            rebalance_interval_days=rebalance_interval_days,
        ),
        position_sizer=position_sizer,
        cost_model=cost_model,
    ).run(slices[:evaluated_end_index])

    folds: list[WalkForwardFold] = []
    total_commission = ZERO
    total_trades = 0
    for fold_number, (
        (test_start_index, test_end_index),
        selected,
    ) in enumerate(zip(fold_boundaries, selections), start=1):
        boundary_equity = evaluation.equity_curve[
            test_start_index - 1
        ][1]
        if boundary_equity <= ZERO:
            raise ValueError("walk-forward boundary equity is not positive")
        segment_end_equity = evaluation.equity_curve[
            test_end_index - 1
        ][1]
        segment_return = segment_end_equity / boundary_equity - Decimal(
            "1"
        )

        test_start_timestamp = slices[test_start_index].timestamp
        test_end_timestamp = slices[test_end_index - 1].timestamp
        test_trades = tuple(
            trade
            for trade in evaluation.trades
            if test_start_timestamp <= trade.timestamp <= test_end_timestamp
        )
        test_commission = sum(
            (trade.commission for trade in test_trades),
            start=ZERO,
        )
        total_commission += test_commission
        total_trades += len(test_trades)
        folds.append(
            WalkForwardFold(
                fold_number=fold_number,
                train_start=slices[0].timestamp,
                train_end=slices[test_start_index - 1].timestamp,
                test_start=test_start_timestamp,
                test_end=test_end_timestamp,
                selected=selected,
                out_of_sample_return=segment_return,
                out_of_sample_commission=test_commission,
                out_of_sample_trade_count=len(test_trades),
            )
        )

    out_of_sample_curve = evaluation.equity_curve[
        minimum_train_days:evaluated_end_index
    ]
    peak = initial_equity
    max_drawdown = ZERO
    for _, equity in out_of_sample_curve:
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak if peak > ZERO else ZERO
        max_drawdown = max(max_drawdown, drawdown)
    final_equity = out_of_sample_curve[-1][1]

    return WalkForwardResult(
        folds=tuple(folds),
        compounded_out_of_sample_return=(
            final_equity / initial_equity - Decimal("1")
        ),
        out_of_sample_max_drawdown=max_drawdown,
        out_of_sample_commission=total_commission,
        out_of_sample_trade_count=total_trades,
        out_of_sample_days=len(out_of_sample_curve),
        excluded_tail_days=len(slices) - evaluated_end_index,
        out_of_sample_equity_curve=out_of_sample_curve,
    )


def _score_candidate(
    slices: tuple[MarketSlice, ...],
    *,
    signal_symbol: str,
    candidate: MovingAverageCandidate,
    initial_equity: Decimal,
    position_sizer: IntegerPositionSizer,
    cost_model: CostModel,
    target_weight: Decimal,
    activation_index: int = 0,
    rebalance_interval_days: int = 1,
) -> CandidateScore:
    result = run_moving_average_candidate(
        slices,
        signal_symbol=signal_symbol,
        candidate=candidate,
        initial_equity=initial_equity,
        position_sizer=position_sizer,
        cost_model=cost_model,
        target_weight=target_weight,
        activation_index=activation_index,
        rebalance_interval_days=rebalance_interval_days,
    )
    return CandidateScore(
        candidate=candidate,
        annualized_sharpe=_annualized_sharpe(result),
        total_return=result.total_return,
        max_drawdown=result.max_drawdown,
        commission=result.total_commission,
    )


def run_moving_average_candidate(
    slices: tuple[MarketSlice, ...],
    *,
    signal_symbol: str,
    candidate: MovingAverageCandidate,
    initial_equity: Decimal,
    position_sizer: IntegerPositionSizer,
    cost_model: CostModel,
    target_weight: Decimal,
    activation_index: int = 0,
    rebalance_interval_days: int = 1,
) -> BacktestResult:
    strategy = DelayedActivationStrategy(
        base=MovingAverageTrendStrategy(
            signal_symbol=signal_symbol,
            short_window=candidate.short_window,
            long_window=candidate.long_window,
            target_weight=target_weight,
            rebalance_interval_days=rebalance_interval_days,
        ),
        activation_index=activation_index,
    )
    return BacktestEngine(
        initial_equity=initial_equity,
        strategy=strategy,
        position_sizer=position_sizer,
        cost_model=cost_model,
    ).run(slices)


def _annualized_sharpe(result: BacktestResult) -> float:
    equities = [result.initial_equity] + [
        equity for _, equity in result.equity_curve
    ]
    returns = [
        float(current / previous - Decimal("1"))
        for previous, current in zip(equities, equities[1:])
        if previous > ZERO
    ]
    if len(returns) < 2:
        return float("-inf")
    volatility = pstdev(returns)
    if volatility == 0:
        return 0.0
    return fmean(returns) / volatility * sqrt(252)


class _ScheduledMovingAverageStrategy:
    """Change selected parameters only at walk-forward boundaries."""

    def __init__(
        self,
        *,
        signal_symbol: str,
        target_weight: Decimal,
        selected_by_activation: Mapping[object, CandidateScore],
        rebalance_interval_days: int,
    ) -> None:
        if rebalance_interval_days <= 0:
            raise ValueError("rebalance interval must be positive")
        self.signal_symbol = signal_symbol
        self.target_weight = target_weight
        self.selected_by_activation = dict(selected_by_activation)
        self.rebalance_interval_days = rebalance_interval_days
        self._active: MovingAverageCandidate | None = None
        self._last_weight: Decimal | None = None
        self._days_since_rebalance = rebalance_interval_days

    def on_close(
        self,
        market_slice: MarketSlice,
        close_history: Mapping[str, Sequence[Decimal]],
    ) -> TargetAllocation | None:
        selected = self.selected_by_activation.get(market_slice.timestamp)
        if selected is not None:
            self._active = selected.candidate
        if self._active is None:
            return None
        closes = close_history.get(self.signal_symbol, ())
        if len(closes) < self._active.long_window:
            return None
        short_average = sum(
            closes[-self._active.short_window :]
        ) / Decimal(self._active.short_window)
        long_average = sum(
            closes[-self._active.long_window :]
        ) / Decimal(self._active.long_window)
        weight = (
            self.target_weight
            if short_average > long_average
            else ZERO
        )
        state_changed = self._last_weight is None or (
            weight != self._last_weight
        )
        should_rebalance = (
            state_changed
            or (
                self._days_since_rebalance + 1
                >= self.rebalance_interval_days
            )
        )
        self._days_since_rebalance += 1
        if not should_rebalance:
            return None
        self._last_weight = weight
        self._days_since_rebalance = 0
        return TargetAllocation(
            signal_symbol=self.signal_symbol,
            target_weight=weight,
            reason=(
                f"walk_forward={self._active.name};"
                f"short_ma={short_average};long_ma={long_average}"
            ),
        )
