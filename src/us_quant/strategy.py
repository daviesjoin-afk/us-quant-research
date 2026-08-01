from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Protocol, Sequence

from us_quant.domain import MarketSlice


@dataclass(frozen=True, slots=True)
class TargetAllocation:
    signal_symbol: str
    target_weight: Decimal
    reason: str

    def __post_init__(self) -> None:
        if self.target_weight < Decimal("0") or self.target_weight > Decimal(
            "1"
        ):
            raise ValueError("target weight must be between zero and one")


class Strategy(Protocol):
    def on_close(
        self,
        market_slice: MarketSlice,
        close_history: Mapping[str, Sequence[Decimal]],
    ) -> TargetAllocation | None:
        """Return a target after the close; it executes on the next slice."""


class DelayedActivationStrategy:
    """Keep a strategy inactive until a shared research boundary."""

    def __init__(self, *, base: Strategy, activation_index: int) -> None:
        if activation_index < 0:
            raise ValueError("activation index cannot be negative")
        self.base = base
        self.activation_index = activation_index
        self._index = 0

    def on_close(
        self,
        market_slice: MarketSlice,
        close_history: Mapping[str, Sequence[Decimal]],
    ) -> TargetAllocation | None:
        current_index = self._index
        self._index += 1
        if current_index < self.activation_index:
            return None
        return self.base.on_close(market_slice, close_history)


class MovingAverageTrendStrategy:
    def __init__(
        self,
        *,
        signal_symbol: str,
        short_window: int,
        long_window: int,
        target_weight: Decimal,
        rebalance_interval_days: int = 1,
    ) -> None:
        if short_window <= 0 or long_window <= short_window:
            raise ValueError("require 0 < short_window < long_window")
        if rebalance_interval_days <= 0:
            raise ValueError("rebalance interval must be positive")
        self.signal_symbol = signal_symbol
        self.short_window = short_window
        self.long_window = long_window
        self.target_weight = target_weight
        self.rebalance_interval_days = rebalance_interval_days
        self._last_weight: Decimal | None = None
        self._days_since_rebalance = rebalance_interval_days

    def on_close(
        self,
        market_slice: MarketSlice,
        close_history: Mapping[str, Sequence[Decimal]],
    ) -> TargetAllocation | None:
        closes = close_history.get(self.signal_symbol, ())
        if len(closes) < self.long_window:
            return None
        short_average = sum(closes[-self.short_window :]) / Decimal(
            self.short_window
        )
        long_average = sum(closes[-self.long_window :]) / Decimal(
            self.long_window
        )
        weight = (
            self.target_weight
            if short_average > long_average
            else Decimal("0")
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
                f"short_ma={short_average};long_ma={long_average}"
            ),
        )


class DonchianBreakoutStrategy:
    """Long/cash breakout using closes available at the signal close."""

    def __init__(
        self,
        *,
        signal_symbol: str,
        entry_window: int,
        exit_window: int,
        target_weight: Decimal,
    ) -> None:
        if exit_window <= 0 or entry_window <= exit_window:
            raise ValueError("require 0 < exit_window < entry_window")
        self.signal_symbol = signal_symbol
        self.entry_window = entry_window
        self.exit_window = exit_window
        self.target_weight = target_weight
        self._invested = False

    def on_close(
        self,
        market_slice: MarketSlice,
        close_history: Mapping[str, Sequence[Decimal]],
    ) -> TargetAllocation | None:
        closes = close_history.get(self.signal_symbol, ())
        if len(closes) <= self.entry_window:
            return None
        current = closes[-1]
        prior_entry = closes[-self.entry_window - 1 : -1]
        prior_exit = closes[-self.exit_window - 1 : -1]
        next_invested = self._invested
        reason = ""
        if not self._invested and current > max(prior_entry):
            next_invested = True
            reason = f"close={current};entry_high={max(prior_entry)}"
        elif self._invested and current < min(prior_exit):
            next_invested = False
            reason = f"close={current};exit_low={min(prior_exit)}"
        if next_invested == self._invested:
            return None
        self._invested = next_invested
        return TargetAllocation(
            signal_symbol=self.signal_symbol,
            target_weight=(
                self.target_weight if self._invested else Decimal("0")
            ),
            reason=reason,
        )


class RSIMeanReversionStrategy:
    """Long/cash close-to-close RSI mean reversion."""

    def __init__(
        self,
        *,
        signal_symbol: str,
        window: int,
        entry_threshold: Decimal,
        exit_threshold: Decimal,
        target_weight: Decimal,
    ) -> None:
        if window < 2:
            raise ValueError("RSI window must be at least 2")
        if not (
            Decimal("0")
            < entry_threshold
            < exit_threshold
            < Decimal("100")
        ):
            raise ValueError(
                "require 0 < entry_threshold < exit_threshold < 100"
            )
        self.signal_symbol = signal_symbol
        self.window = window
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.target_weight = target_weight
        self._invested = False

    def on_close(
        self,
        market_slice: MarketSlice,
        close_history: Mapping[str, Sequence[Decimal]],
    ) -> TargetAllocation | None:
        closes = close_history.get(self.signal_symbol, ())
        if len(closes) < self.window + 1:
            return None
        changes = [
            closes[index] - closes[index - 1]
            for index in range(
                len(closes) - self.window,
                len(closes),
            )
        ]
        average_gain = (
            sum(
                (max(change, Decimal("0")) for change in changes),
                start=Decimal("0"),
            )
            / Decimal(self.window)
        )
        average_loss = (
            sum(
                (max(-change, Decimal("0")) for change in changes),
                start=Decimal("0"),
            )
            / Decimal(self.window)
        )
        if average_loss == 0:
            rsi = Decimal("100")
        else:
            relative_strength = average_gain / average_loss
            rsi = Decimal("100") - (
                Decimal("100") / (Decimal("1") + relative_strength)
            )
        next_invested = self._invested
        if not self._invested and rsi <= self.entry_threshold:
            next_invested = True
        elif self._invested and rsi >= self.exit_threshold:
            next_invested = False
        if next_invested == self._invested:
            return None
        self._invested = next_invested
        return TargetAllocation(
            signal_symbol=self.signal_symbol,
            target_weight=(
                self.target_weight if self._invested else Decimal("0")
            ),
            reason=f"rsi={rsi:.2f}",
        )


class ConstantAllocationStrategy:
    """Maintain a fixed target after the first observed close."""

    def __init__(
        self,
        *,
        signal_symbol: str,
        target_weight: Decimal,
        rebalance_interval_days: int = 1,
    ) -> None:
        if target_weight < Decimal("0") or target_weight > Decimal("1"):
            raise ValueError("target weight must be between zero and one")
        if rebalance_interval_days <= 0:
            raise ValueError("rebalance interval must be positive")
        self.signal_symbol = signal_symbol
        self.target_weight = target_weight
        self.rebalance_interval_days = rebalance_interval_days
        self._days_since_rebalance = rebalance_interval_days

    def on_close(
        self,
        market_slice: MarketSlice,
        close_history: Mapping[str, Sequence[Decimal]],
    ) -> TargetAllocation | None:
        if self.signal_symbol not in market_slice.bars:
            return None
        self._days_since_rebalance += 1
        if self._days_since_rebalance < self.rebalance_interval_days:
            return None
        self._days_since_rebalance = 0
        return TargetAllocation(
            signal_symbol=self.signal_symbol,
            target_weight=self.target_weight,
            reason="constant target allocation",
        )


class BuyAndHoldStrategy:
    """Create one target allocation and never rebalance it."""

    def __init__(
        self,
        *,
        signal_symbol: str,
        target_weight: Decimal,
    ) -> None:
        if target_weight < Decimal("0") or target_weight > Decimal("1"):
            raise ValueError("target weight must be between zero and one")
        self.signal_symbol = signal_symbol
        self.target_weight = target_weight
        self._submitted = False

    def on_close(
        self,
        market_slice: MarketSlice,
        close_history: Mapping[str, Sequence[Decimal]],
    ) -> TargetAllocation | None:
        if self._submitted or self.signal_symbol not in market_slice.bars:
            return None
        self._submitted = True
        return TargetAllocation(
            signal_symbol=self.signal_symbol,
            target_weight=self.target_weight,
            reason="one-time buy-and-hold allocation",
        )
