from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from us_quant.domain import Bar, MarketSlice
from us_quant.strategy import (
    BuyAndHoldStrategy,
    ConstantAllocationStrategy,
    DonchianBreakoutStrategy,
    RSIMeanReversionStrategy,
)


def _market_slice(index: int) -> MarketSlice:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
        days=index
    )
    bar = Bar(
        symbol="MU",
        timestamp=timestamp,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=1_000,
    )
    return MarketSlice(timestamp=timestamp, bars={"MU": bar})


class StrategyTests(unittest.TestCase):
    def test_constant_allocation_respects_rebalance_interval(self) -> None:
        strategy = ConstantAllocationStrategy(
            signal_symbol="MU",
            target_weight=Decimal("0.10"),
            rebalance_interval_days=5,
        )
        signals = [
            strategy.on_close(_market_slice(index), {"MU": [Decimal("100")]})
            for index in range(11)
        ]
        self.assertEqual(
            [index for index, signal in enumerate(signals) if signal],
            [0, 5, 10],
        )

    def test_buy_and_hold_emits_only_one_target(self) -> None:
        strategy = BuyAndHoldStrategy(
            signal_symbol="MU",
            target_weight=Decimal("0.10"),
        )
        signals = [
            strategy.on_close(_market_slice(index), {"MU": [Decimal("100")]})
            for index in range(5)
        ]
        self.assertEqual(sum(signal is not None for signal in signals), 1)

    def test_donchian_enters_and_exits_on_prior_channel(self) -> None:
        strategy = DonchianBreakoutStrategy(
            signal_symbol="MU",
            entry_window=3,
            exit_window=2,
            target_weight=Decimal("0.8"),
        )
        closes: list[Decimal] = []
        targets = []
        for index, value in enumerate(("10", "11", "12", "13", "9")):
            closes.append(Decimal(value))
            targets.append(
                strategy.on_close(
                    _market_slice(index),
                    {"MU": tuple(closes)},
                )
            )
        self.assertEqual(targets[3].target_weight, Decimal("0.8"))
        self.assertEqual(targets[4].target_weight, Decimal("0"))

    def test_rsi_mean_reversion_enters_weakness_and_exits_strength(self) -> None:
        strategy = RSIMeanReversionStrategy(
            signal_symbol="MU",
            window=3,
            entry_threshold=Decimal("25"),
            exit_threshold=Decimal("55"),
            target_weight=Decimal("0.7"),
        )
        closes: list[Decimal] = []
        targets = []
        for index, value in enumerate(
            ("10", "9", "8", "7", "8", "9", "10")
        ):
            closes.append(Decimal(value))
            targets.append(
                strategy.on_close(
                    _market_slice(index),
                    {"MU": tuple(closes)},
                )
            )
        self.assertEqual(targets[3].target_weight, Decimal("0.7"))
        self.assertEqual(targets[5].target_weight, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
