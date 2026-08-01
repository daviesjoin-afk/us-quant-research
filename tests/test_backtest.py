from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from us_quant.backtest import BacktestEngine, CostModel
from us_quant.domain import Bar, MarketSlice
from us_quant.portfolio import IntegerPositionSizer, SubstitutionRule
from us_quant.strategy import TargetAllocation


class _BuyAfterFirstClose:
    def __init__(self, symbol: str, weight: str) -> None:
        self.symbol = symbol
        self.weight = Decimal(weight)
        self.called = False

    def on_close(self, market_slice, close_history):
        if not self.called:
            self.called = True
            return TargetAllocation(
                signal_symbol=self.symbol,
                target_weight=self.weight,
                reason="test signal",
            )
        return None


def _slice(
    timestamp: datetime,
    values: dict[str, tuple[str, str]],
) -> MarketSlice:
    bars = {}
    for symbol, (open_price, close_price) in values.items():
        open_value = Decimal(open_price)
        close_value = Decimal(close_price)
        bars[symbol] = Bar(
            symbol=symbol,
            timestamp=timestamp,
            open=open_value,
            high=max(open_value, close_value),
            low=min(open_value, close_value),
            close=close_value,
            volume=1000,
        )
    return MarketSlice(timestamp=timestamp, bars=bars)


class BacktestEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.costs = CostModel(
            per_share_commission=Decimal("0"),
            minimum_commission=Decimal("0"),
            slippage_bps=Decimal("0"),
        )
        self.start = datetime(2026, 1, 2, 21, 0, tzinfo=timezone.utc)

    def test_close_signal_executes_at_next_bar_open(self) -> None:
        slices = [
            _slice(self.start, {"X": ("100", "110")}),
            _slice(
                self.start + timedelta(days=1),
                {"X": ("120", "125")},
            ),
        ]
        engine = BacktestEngine(
            initial_equity=Decimal("1000"),
            strategy=_BuyAfterFirstClose("X", "0.50"),
            position_sizer=IntegerPositionSizer({}),
            cost_model=self.costs,
        )
        result = engine.run(slices)
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].fill_price, Decimal("120"))
        self.assertEqual(result.trades[0].raw_price, Decimal("120"))
        self.assertEqual(
            result.trades[0].signal_timestamp, self.start
        )
        self.assertEqual(result.trades[0].reason, "test signal")
        self.assertEqual(result.trades[0].position_after, 4)
        self.assertEqual(result.trades[0].cash_after, Decimal("520"))
        self.assertEqual(
            result.trades[0].timestamp,
            self.start + timedelta(days=1),
        )

    def test_mu_signal_uses_muu_when_mu_is_unaffordable(self) -> None:
        slices = [
            _slice(
                self.start,
                {"MU": ("980", "990"), "MUU": ("35", "36")},
            ),
            _slice(
                self.start + timedelta(days=1),
                {"MU": ("995", "1000"), "MUU": ("36", "37")},
            ),
        ]
        engine = BacktestEngine(
            initial_equity=Decimal("1500"),
            strategy=_BuyAfterFirstClose("MU", "0.10"),
            position_sizer=IntegerPositionSizer(
                {
                    "MU": SubstitutionRule(
                        source_symbol="MU",
                        execution_symbol="MUU",
                        exposure_multiplier=Decimal("2"),
                        holding_mode="intraday_or_short_term",
                    )
                }
            ),
            cost_model=self.costs,
        )
        result = engine.run(slices)
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.signal_symbol, "MU")
        self.assertEqual(trade.execution_symbol, "MUU")
        self.assertEqual(trade.quantity, 2)
        self.assertTrue(trade.used_substitution)


if __name__ == "__main__":
    unittest.main()
