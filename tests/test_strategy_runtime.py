"""The strategy runtime on its own: signals, sizing and exit gates.

Nothing in this file builds a ``RiskApplication`` or an ``ExecutionApplication``,
and that is the assertion: if testing the strategy needed either, the boundary
would not be split.  What is pinned here is what the strategy *wants* -- which
candidates pass the signal gates and in what order, how many whole shares it
would like, which holdings breach an exit gate and why, and what price a
reduction is sent at.
"""

from datetime import datetime, timedelta, time, timezone
from decimal import Decimal
import unittest

from us_quant.trading.runtime.config import TradingSessionConfig
from us_quant.trading.domain.market import MarketDataMode, MarketQuote
from us_quant.trading.domain.strategy import (
    StrategyIdentity,
    TradeAction,
)
from us_quant.trading.runtime.models import (
    AutoQuantCandidate,
    StrategyPositionView,
    StrategySessionPolicy,
)
from us_quant.trading.runtime.strategy import StrategyRuntime


_STRATEGY = StrategyIdentity(
    strategy_id="intraday-auto-rotation",
    version_id="version",
    parameter_hash="hash",
)

_NOW = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)

#: Wide open, so a gate under test is the only thing that can refuse.
_POLICY = StrategySessionPolicy(
    entry_start=time(0, 0),
    last_entry=time(23, 59),
    maximum_trades_per_day=100,
    daily_loss_limit=Decimal("1000000"),
)


def _quote(symbol: str, price: Decimal, *, spread: Decimal = Decimal("0.02")):
    return MarketQuote(
        symbol=symbol,
        bid=price,
        ask=price + spread,
        last=price,
        close=None,
        bid_size=None,
        ask_size=None,
        mode=MarketDataMode.REALTIME,
        updated_at=_NOW,
        age_seconds=0,
        stale=False,
        stale_reason=None,
        generation=1,
        source_id="test_feed",
        source_label="TestFeed",
        coverage="unit test",
    )


def _config(**overrides) -> TradingSessionConfig:
    """A permissive configuration: one gate under test refuses at a time."""

    settings = {
        "initial_cash": Decimal("10000"),
        "capital_source": "test",
        "max_position_fraction": Decimal("0.5"),
        "minimum_momentum": Decimal("0.001"),
        "maximum_momentum": Decimal("0.5"),
        "minimum_positive_steps": 0,
        "maximum_one_minute_move": Decimal("1"),
        "maximum_spread_fraction": Decimal("0.01"),
        "warmup_minutes": 0,
        "momentum_lookback_minutes": 1,
        "slippage_bps": Decimal("0"),
        "commission_per_order": Decimal("0"),
        "profit_target": Decimal("0.01"),
        "stop_loss": Decimal("0.01"),
        "trailing_stop": Decimal("0.01"),
        "maximum_hold_minutes": 45,
    }
    settings.update(overrides)
    return TradingSessionConfig(**settings)


def _candidate(symbol: str, score: str = "80") -> AutoQuantCandidate:
    return AutoQuantCandidate(
        symbol=symbol,
        name=symbol,
        sector="T",
        leader_tier=1,
        scan_score=Decimal(score),
        signal="UP",
    )


def _strategy(
    *,
    candidates: tuple[AutoQuantCandidate, ...] = (_candidate("AAA"),),
    config: TradingSessionConfig | None = None,
    references: tuple[str, ...] = (),
) -> StrategyRuntime:
    return StrategyRuntime(
        candidates=candidates,
        config=config or _config(),
        strategy=_STRATEGY,
        market_reference_symbols=references,
    )


def _warm(
    strategy: StrategyRuntime,
    symbol: str,
    prices: tuple[str, ...],
    *,
    minute: int = 0,
) -> None:
    """Feed one mark per minute so the history has the shape under test.

    The quotes are two-sided at the same price, so the mark the history records
    is exactly the price written here: a history built from a spread would
    shift every mark by half of it and make the momentum assertions inexact.
    """

    for index, price in enumerate(prices):
        observed = _NOW + timedelta(minutes=minute + index)
        strategy.observe(
            now=observed,
            ready={
                symbol: _quote(
                    symbol, Decimal(price), spread=Decimal("0")
                )
            },
            reference_ready={},
        )


def _entry(
    strategy: StrategyRuntime,
    *,
    prices: dict[str, str],
    positions: dict[str, StrategyPositionView] | None = None,
    trades_today: int = 0,
    realized_pnl: Decimal = Decimal("0"),
    policy: StrategySessionPolicy | None = None,
    references: dict[str, str] | None = None,
):
    return strategy.entry_evaluation(
        now=_NOW,
        ready={
            symbol: _quote(symbol, Decimal(price))
            for symbol, price in prices.items()
        },
        reference_ready={
            symbol: _quote(symbol, Decimal(price))
            for symbol, price in (references or {}).items()
        },
        positions=positions or {},
        trades_today=trades_today,
        realized_pnl=realized_pnl,
        policy=policy or _POLICY,
    )


class StrategyEntryTests(unittest.TestCase):
    def test_a_candidate_without_enough_history_is_warming_up(self) -> None:
        strategy = _strategy()
        _warm(strategy, "AAA", ("10",))

        evaluation = _entry(strategy, prices={"AAA": "10"})

        self.assertEqual(evaluation.proposals, ())
        self.assertIn("候选预热", evaluation.status)

    def test_a_wide_spread_is_skipped(self) -> None:
        strategy = _strategy(
            config=_config(maximum_spread_fraction=Decimal("0.001"))
        )
        _warm(strategy, "AAA", ("9.99", "10"))

        evaluation = _entry(strategy, prices={"AAA": "10"})

        self.assertEqual(evaluation.proposals, ())

    def test_momentum_outside_the_band_is_skipped(self) -> None:
        too_small = _strategy(
            config=_config(minimum_momentum=Decimal("0.05"))
        )
        _warm(too_small, "AAA", ("10", "10.01"))
        self.assertEqual(
            _entry(too_small, prices={"AAA": "10.01"}).proposals, ()
        )

        too_large = _strategy(
            config=_config(maximum_momentum=Decimal("0.0005"))
        )
        _warm(too_large, "AAA", ("10", "10.01"))
        self.assertEqual(
            _entry(too_large, prices={"AAA": "10.01"}).proposals, ()
        )

    def test_a_signal_below_the_running_average_is_skipped(self) -> None:
        """Rising into the lookback but under the session average is not a signal."""

        strategy = _strategy()
        _warm(strategy, "AAA", ("12", "11", "11.5"))

        evaluation = _entry(strategy, prices={"AAA": "11.5"})

        self.assertEqual(evaluation.proposals, ())

    def test_positive_steps_gate_rejects_a_down_minute(self) -> None:
        strategy = _strategy(
            config=_config(
                momentum_lookback_minutes=2,
                minimum_positive_steps=2,
                maximum_one_minute_move=Decimal("0.2"),
            )
        )
        _warm(strategy, "AAA", ("10", "10.3", "10.2"))

        evaluation = _entry(strategy, prices={"AAA": "10.2"})

        self.assertEqual(evaluation.proposals, ())

    def test_a_one_minute_spike_is_filtered(self) -> None:
        strategy = _strategy(
            config=_config(
                momentum_lookback_minutes=2,
                minimum_positive_steps=0,
                maximum_one_minute_move=Decimal("0.05"),
            )
        )
        _warm(strategy, "AAA", ("10", "13", "13.1"))

        evaluation = _entry(strategy, prices={"AAA": "13.1"})

        self.assertEqual(evaluation.proposals, ())

    def test_the_regime_gate_blocks_entries_and_says_why(self) -> None:
        strategy = _strategy(references=("SPY",))
        _warm(strategy, "AAA", ("10", "10.01"))
        strategy.observe(
            now=_NOW,
            ready={},
            reference_ready={
                "SPY": _quote("SPY", Decimal("400"), spread=Decimal("0"))
            },
        )

        blocked = _entry(
            strategy, prices={"AAA": "10.01"}, references={"SPY": "400"}
        )
        self.assertEqual(blocked.proposals, ())
        self.assertIn("regime gate blocked", blocked.status)

        strategy.observe(
            now=_NOW + timedelta(minutes=1),
            ready={},
            reference_ready={
                "SPY": _quote("SPY", Decimal("401"), spread=Decimal("0"))
            },
        )
        allowed = _entry(
            strategy,
            prices={"AAA": "10.01"},
            references={"SPY": "401"},
        )
        self.assertEqual(len(allowed.proposals), 1)

    def test_the_session_policy_gates_the_scan(self) -> None:
        strategy = _strategy()
        _warm(strategy, "AAA", ("10", "10.01"))

        outside_window = _entry(
            strategy,
            prices={"AAA": "10.01"},
            policy=StrategySessionPolicy(
                entry_start=time(22, 0),
                last_entry=time(23, 0),
                maximum_trades_per_day=100,
                daily_loss_limit=Decimal("1000000"),
            ),
        )
        self.assertEqual(outside_window.proposals, ())
        self.assertIn("入场时段", outside_window.status)

        trades_reached = _entry(
            strategy,
            prices={"AAA": "10.01"},
            policy=StrategySessionPolicy(
                entry_start=time(0, 0),
                last_entry=time(23, 59),
                maximum_trades_per_day=1,
                daily_loss_limit=Decimal("1000000"),
            ),
            trades_today=1,
        )
        self.assertEqual(trades_reached.proposals, ())
        self.assertIn("最大交易次数", trades_reached.status)

        losses_reached = _entry(
            strategy,
            prices={"AAA": "10.01"},
            policy=StrategySessionPolicy(
                entry_start=time(0, 0),
                last_entry=time(23, 59),
                maximum_trades_per_day=100,
                daily_loss_limit=Decimal("10"),
            ),
            realized_pnl=Decimal("-10"),
        )
        self.assertEqual(losses_reached.proposals, ())
        self.assertIn("单日亏损停机线", losses_reached.status)

    def test_candidates_come_back_strongest_first(self) -> None:
        strategy = _strategy(
            candidates=(_candidate("AAA", "80"), _candidate("BBB", "80"))
        )
        first = _NOW
        for symbol, price in (("AAA", "10"), ("BBB", "20")):
            strategy.observe(
                now=first,
                ready={
                    symbol: _quote(
                        symbol, Decimal(price), spread=Decimal("0")
                    )
                },
                reference_ready={},
            )
        for symbol, price in (("AAA", "10.05"), ("BBB", "20.2")):
            strategy.observe(
                now=first + timedelta(minutes=1),
                ready={
                    symbol: _quote(
                        symbol, Decimal(price), spread=Decimal("0")
                    )
                },
                reference_ready={},
            )

        evaluation = _entry(
            strategy, prices={"AAA": "10.05", "BBB": "20.2"}
        )

        self.assertEqual(
            [proposal.symbol for proposal in evaluation.proposals],
            ["BBB", "AAA"],
        )

    def test_the_desired_quantity_is_whole_shares_within_the_position_cap(
        self,
    ) -> None:
        strategy = _strategy(
            config=_config(
                initial_cash=Decimal("1000"),
                max_position_fraction=Decimal("0.1"),
                commission_per_order=Decimal("10"),
            )
        )
        _warm(strategy, "AAA", ("10", "10.01"))

        evaluation = _entry(strategy, prices={"AAA": "10.01"})

        # 1000 × 0.10 = 100, less the 10 fee, at 10.01 → 8 whole shares.
        proposal = evaluation.proposals[0]
        self.assertEqual(proposal.desired_quantity, 8)
        self.assertIs(proposal.action, TradeAction.BUY)
        self.assertIs(proposal.strategy, _STRATEGY)

    def test_an_existing_holding_reduces_the_desired_quantity(self) -> None:
        strategy = _strategy(
            config=_config(
                initial_cash=Decimal("1000"),
                max_position_fraction=Decimal("0.1"),
                commission_per_order=Decimal("0"),
            )
        )
        _warm(strategy, "AAA", ("10", "10.01"))
        held = StrategyPositionView(
            symbol="AAA",
            quantity=5,
            average_price=Decimal("10"),
            opened_at=_NOW,
            high_water=Decimal("10"),
        )

        evaluation = _entry(
            strategy, prices={"AAA": "10.01"}, positions={"AAA": held}
        )

        # 100 − (10 × 5) = 50 affordable → 4 whole shares.
        self.assertEqual(evaluation.proposals[0].desired_quantity, 4)


class StrategyExitTests(unittest.TestCase):
    def _held(
        self,
        *,
        entry: str = "10",
        quantity: int = 5,
        high_water: str = "10",
        minutes_ago: int = 0,
    ) -> StrategyPositionView:
        return StrategyPositionView(
            symbol="AAA",
            quantity=quantity,
            average_price=Decimal(entry),
            opened_at=_NOW - timedelta(minutes=minutes_ago),
            high_water=Decimal(high_water),
        )

    def _exit(self, strategy: StrategyRuntime, *, bid: str, mark=None, **kw):
        return strategy.exit_evaluation(
            now=_NOW,
            positions={"AAA": kw.pop("position", self._held())},
            bids={"AAA": Decimal(bid)},
            marks=(
                {"AAA": Decimal(mark)} if mark is not None else {}
            ),
            reason=kw.pop("reason", None),
            skip=kw.pop("skip", frozenset()),
        )

    def test_the_profit_target_proposes_a_sell_at_the_bid(self) -> None:
        strategy = _strategy()

        evaluation = self._exit(strategy, bid="10.2")

        self.assertEqual(len(evaluation.proposals), 1)
        proposal = evaluation.proposals[0]
        self.assertIs(proposal.action, TradeAction.SELL)
        self.assertEqual(proposal.reason, "达到止盈门")
        self.assertEqual(proposal.desired_quantity, 5)
        self.assertEqual(proposal.reference_price, Decimal("10.20"))

    def test_the_stop_loss_proposes_a_sell(self) -> None:
        strategy = _strategy()

        evaluation = self._exit(strategy, bid="9.8")

        self.assertEqual(evaluation.proposals[0].reason, "触发止损门")

    def test_the_trailing_stop_uses_the_high_water_mark(self) -> None:
        """A price under the high-water mark but above the entry is trailing.

        The gate order matters: a price above ``entry × (1 + profit_target)``
        is a take profit first, so the trailing case is deliberately inside
        that band and outside the stop-loss band.
        """

        strategy = _strategy()

        evaluation = self._exit(
            strategy, bid="10", position=self._held(high_water="11")
        )

        self.assertEqual(evaluation.proposals[0].reason, "触发移动止损")

    def test_the_maximum_hold_proposes_an_exit(self) -> None:
        strategy = _strategy()

        evaluation = self._exit(
            strategy,
            bid="10",
            position=self._held(minutes_ago=45),
        )

        self.assertEqual(evaluation.proposals[0].reason, "达到最长持有时间")

    def test_no_gate_firing_proposes_nothing(self) -> None:
        strategy = _strategy()

        evaluation = self._exit(strategy, bid="10")

        self.assertEqual(evaluation.proposals, ())

    def test_a_forced_exit_needs_no_gate_and_is_priced_off_the_bid(
        self,
    ) -> None:
        strategy = _strategy(config=_config(slippage_bps=Decimal("10")))

        evaluation = self._exit(
            strategy, bid="10", reason="用户停止；提交 Paper 限价平仓"
        )

        proposal = evaluation.proposals[0]
        self.assertEqual(proposal.reason, "用户停止；提交 Paper 限价平仓")
        # A sell slips down: 10 × (1 − 0.001), rounded to the cent.
        self.assertEqual(proposal.reference_price, Decimal("9.99"))

    def test_a_forced_exit_without_a_bid_is_reported_not_priced(self) -> None:
        strategy = _strategy()

        evaluation = strategy.exit_evaluation(
            now=_NOW,
            positions={"AAA": self._held()},
            bids={},
            marks={},
            reason="收盘前提交 Paper 限价平仓",
        )

        self.assertEqual(evaluation.proposals, ())
        self.assertEqual(len(evaluation.notes), 1)
        self.assertIn("缺少 fresh bid", evaluation.notes[0])

    def test_a_skipped_symbol_produces_no_second_sell(self) -> None:
        """CR-1 lives here too: a symbol already exiting is not proposed again."""

        strategy = _strategy()

        evaluation = self._exit(
            strategy, bid="10.2", skip=frozenset({"AAA"})
        )

        self.assertEqual(evaluation.proposals, ())

    def test_the_strategy_holds_no_risk_execution_or_order_state(self) -> None:
        """The boundary, asserted on the object rather than on the source."""

        strategy = _strategy()
        for forbidden in (
            "risk",
            "execution",
            "pending",
            "fills",
            "positions",
            "order_id",
        ):
            self.assertFalse(hasattr(strategy, forbidden), forbidden)


if __name__ == "__main__":
    unittest.main()