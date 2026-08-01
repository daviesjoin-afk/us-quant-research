from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.minute_data import MinuteQuoteRecord, MinuteQuoteStore
from us_quant.targeted_robustness import (
    PerturbationSummary,
    SessionReplayOutcome,
    TargetedRobustnessResult,
)
from us_quant.targeted_validation import (
    build_intraday_benchmark_outcomes,
    load_targeted_walk_forwards,
    run_targeted_walk_forward,
    save_targeted_walk_forward,
)


PARAMETERS = {
    "momentum_lookback_minutes": 5,
    "warmup_minutes": 10,
    "maximum_hold_minutes": 45,
    "maximum_trades_per_day": 4,
    "max_position_fraction": "0.10",
    "min_order_notional": "50",
    "commission_per_order": "0.35",
    "slippage_bps": "2",
    "maximum_spread_fraction": "0.002",
    "minimum_momentum": "0.0035",
    "maximum_momentum": "0.025",
    "profit_target": "0.012",
    "stop_loss": "0.007",
    "trailing_stop": "0.006",
    "whole_shares": True,
}
SCENARIOS = (
    "基准",
    "入场阈值 -20%",
    "入场阈值 +20%",
    "退出距离 -20%",
    "退出距离 +20%",
)


class TargetedValidationTests(unittest.TestCase):
    def test_walk_forward_is_time_ordered_and_persisted(self) -> None:
        records, robustness = _evidence(25)
        result = run_targeted_walk_forward(
            robustness,
            records,
            parameters=PARAMETERS,
            initial_equity=Decimal("1500"),
        )
        self.assertEqual(len(result.folds), 2)
        self.assertEqual(result.folds[0].train_sessions, 10)
        self.assertEqual(result.folds[0].validation_sessions, 5)
        self.assertEqual(result.folds[0].test_sessions, 5)
        self.assertFalse(
            any(fold.test_used_for_selection for fold in result.folds)
        )
        self.assertEqual(
            result.out_of_sample_metrics.session_count, 10
        )
        with TemporaryDirectory() as directory:
            path = save_targeted_walk_forward(result, directory)
            loaded = load_targeted_walk_forwards(directory)
            content = path.read_text(encoding="utf-8")
        self.assertEqual(loaded[0], result)
        self.assertIn('"test_used_for_selection": false', content)
        self.assertIn('"orders_submitted": false', content)

    def test_future_test_returns_cannot_change_selected_scenario(
        self,
    ) -> None:
        records, robustness = _evidence(25)
        original = run_targeted_walk_forward(
            robustness,
            records,
            parameters=PARAMETERS,
            initial_equity=Decimal("1500"),
        )
        final_dates = sorted(
            {
                row.session_date
                for row in robustness.session_outcomes
            }
        )[-5:]
        changed_outcomes = tuple(
            replace(row, total_return=Decimal("0.50"))
            if (
                row.scenario == "退出距离 +20%"
                and row.session_date in final_dates
            )
            else row
            for row in robustness.session_outcomes
        )
        changed = replace(
            robustness, session_outcomes=changed_outcomes
        )
        rerun = run_targeted_walk_forward(
            changed,
            records,
            parameters=PARAMETERS,
            initial_equity=Decimal("1500"),
        )
        self.assertEqual(
            [fold.selected_scenario for fold in original.folds],
            [fold.selected_scenario for fold in rerun.folds],
        )

    def test_benchmark_uses_whole_shares_and_same_costs(self) -> None:
        records, robustness = _evidence(20)
        dates = tuple(
            sorted(
                {
                    row.session_date
                    for row in robustness.session_outcomes
                    if row.scenario == "基准"
                }
            )
        )
        outcomes = build_intraday_benchmark_outcomes(
            records,
            usable_dates=dates,
            parameters=PARAMETERS,
            initial_equity=Decimal("1500"),
        )
        self.assertEqual(len(outcomes), 20)
        self.assertTrue(all(row.fill_count == 2 for row in outcomes))
        self.assertTrue(
            all(
                row.commission_cost == Decimal("0.70")
                for row in outcomes
            )
        )

    def test_walk_forward_requires_twenty_sessions(self) -> None:
        records, robustness = _evidence(19)
        with self.assertRaisesRegex(ValueError, "至少需要 20"):
            run_targeted_walk_forward(
                robustness,
                records,
                parameters=PARAMETERS,
                initial_equity=Decimal("1500"),
            )


def _evidence(
    session_count: int,
) -> tuple[
    tuple[MinuteQuoteRecord, ...], TargetedRobustnessResult
]:
    dates = _weekdays(session_count)
    records = []
    outcomes = []
    scenario_returns = {
        "基准": Decimal("0.0010"),
        "入场阈值 -20%": Decimal("0.0020"),
        "入场阈值 +20%": Decimal("0.0015"),
        "退出距离 -20%": Decimal("0.0005"),
        "退出距离 +20%": Decimal("0.0010"),
    }
    for day_index, day in enumerate(dates):
        start = datetime(
            day.year, day.month, day.day, 14, 0, tzinfo=timezone.utc
        )
        base = Decimal("50") + Decimal(day_index) / Decimal("10")
        session_rows = []
        for minute in range(346):
            price = base + Decimal(minute) / Decimal("1000")
            row = MinuteQuoteRecord(
                symbol="AAPL",
                minute=(start + timedelta(minutes=minute)).isoformat(),
                provider="TestFeed",
                coverage="unit-test Level-I",
                bid=price,
                ask=price + Decimal("0.02"),
                last=price,
                market_data_type=1,
                realtime_ready=True,
                stale=False,
                stale_reason=None,
                generation=1,
            )
            session_rows.append(row)
            records.append(row)
        data_hash = MinuteQuoteStore.fingerprint(session_rows)
        for scenario in SCENARIOS:
            outcomes.append(
                SessionReplayOutcome(
                    scenario=scenario,
                    session_date=day.isoformat(),
                    parameter_hash=f"hash-{scenario}",
                    data_hash=data_hash,
                    row_count=346,
                    gap_count=0,
                    total_return=scenario_returns[scenario],
                    maximum_drawdown=Decimal("0.001"),
                    realized_pnl=(
                        Decimal("1500") * scenario_returns[scenario]
                    ),
                    commission_cost=Decimal("0.70"),
                    fill_count=2,
                )
            )
    summaries = tuple(
        PerturbationSummary(
            scenario=scenario,
            parameter_hash=f"hash-{scenario}",
            changed_parameters={},
            session_count=session_count,
            compounded_return=Decimal("0"),
            mean_session_return=scenario_returns[scenario],
            median_session_return=scenario_returns[scenario],
            worst_session_return=scenario_returns[scenario],
            best_session_return=scenario_returns[scenario],
            maximum_drawdown=Decimal("0.001"),
            profitable_session_fraction=Decimal("1"),
            total_fills=session_count * 2,
            commission_cost=Decimal("0.70") * session_count,
        )
        for scenario in SCENARIOS
    )
    robustness = TargetedRobustnessResult(
        run_id="robustness-run",
        symbol="AAPL",
        strategy_version_id="version-1",
        strategy_semver="1.3.0-research",
        base_parameter_hash="base-hash",
        data_hash=MinuteQuoteStore.fingerprint(records),
        provider="TestFeed",
        coverages=("unit-test Level-I",),
        first_session=dates[0].isoformat(),
        last_session=dates[-1].isoformat(),
        total_sessions=session_count,
        usable_sessions=session_count,
        skipped_sessions=(),
        minimum_required_minutes=300,
        scenario_summaries=summaries,
        session_outcomes=tuple(outcomes),
        sign_stability_fraction=Decimal("1"),
        evidence_grade="test",
        review_ready=False,
        status="research_robustness",
    )
    return tuple(records), robustness


def _weekdays(count: int):
    current = datetime(2026, 6, 1).date()
    dates = []
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return tuple(dates)


if __name__ == "__main__":
    unittest.main()
