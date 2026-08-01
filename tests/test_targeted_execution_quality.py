from dataclasses import replace
from decimal import Decimal
from tempfile import TemporaryDirectory
import unittest

from test_minute_replay import PARAMETERS, _multi_session_records
from us_quant.targeted_data_quality import (
    load_targeted_data_quality,
    run_targeted_data_quality,
    save_targeted_data_quality,
)
from us_quant.targeted_execution_stress import (
    load_targeted_execution_stress,
    run_targeted_execution_stress,
    save_targeted_execution_stress,
)
from us_quant.targeted_robustness import run_targeted_robustness


class TargetedExecutionQualityTests(unittest.TestCase):
    def test_complete_low_latency_sessions_pass_quality(self) -> None:
        records = _enriched_records(5)
        robustness = _robustness(records)
        result = run_targeted_data_quality(robustness, records)
        self.assertEqual(result.session_count, 5)
        self.assertEqual(result.high_quality_sessions, 5)
        self.assertEqual(result.minimum_completeness, Decimal("1"))
        self.assertEqual(
            result.p95_source_age_seconds, Decimal("1.25")
        )
        self.assertEqual(result.size_coverage_fraction, Decimal("1"))

        with TemporaryDirectory() as directory:
            path = save_targeted_data_quality(result, directory)
            loaded = load_targeted_data_quality(directory)
            content = path.read_text(encoding="utf-8")
        self.assertEqual(loaded[0], result)
        self.assertIn('"orders_submitted": false', content)

    def test_long_gap_is_visible_even_when_session_exists(self) -> None:
        records = _enriched_records(5)
        robustness = _robustness(records)
        first_date = records[0].minute[:10]
        degraded = tuple(
            row
            for index, row in enumerate(records)
            if not (
                row.minute[:10] == first_date
                and 100 <= index <= 109
            )
        )
        result = run_targeted_data_quality(
            robustness, degraded
        )
        first = result.sessions[0]
        self.assertFalse(first.high_quality)
        self.assertEqual(first.maximum_consecutive_missing, 10)
        self.assertIn(
            "连续缺口超过 2 分钟", first.failure_reasons
        )

    def test_cost_stress_reports_capacity_and_persists(self) -> None:
        records = _enriched_records(5)
        robustness = _robustness(records)
        result = run_targeted_execution_stress(
            robustness,
            records,
            parameters=PARAMETERS,
            initial_equity=Decimal("1500"),
        )
        self.assertEqual(len(result.scenarios), 3)
        self.assertGreater(result.size_observations, 0)
        self.assertIsNotNone(
            result.p95_top_of_book_participation
        )
        self.assertLessEqual(
            result.p95_top_of_book_participation,
            Decimal("0.10"),
        )
        with TemporaryDirectory() as directory:
            path = save_targeted_execution_stress(
                result, directory
            )
            loaded = load_targeted_execution_stress(directory)
            content = path.read_text(encoding="utf-8")
        self.assertEqual(loaded[0], result)
        self.assertIn('"automatic_strategy_promotion": false', content)

    def test_missing_depth_makes_capacity_unavailable(self) -> None:
        records = tuple(
            replace(row, bid_size=None, ask_size=None)
            for row in _enriched_records(1)
        )
        robustness = _robustness(records)
        result = run_targeted_execution_stress(
            robustness,
            records,
            parameters=PARAMETERS,
            initial_equity=Decimal("1500"),
        )
        self.assertIsNone(result.p95_top_of_book_participation)
        self.assertIn("不可估计", result.capacity_status)
        self.assertFalse(result.stress_resilient)


def _enriched_records(days: int):
    return tuple(
        replace(
            row,
            source_age_seconds=1.25,
            bid_size=Decimal("1000"),
            ask_size=Decimal("1000"),
        )
        for row in _multi_session_records(days)
    )


def _robustness(records):
    return run_targeted_robustness(
        records,
        strategy_version_id="version-quality",
        strategy_semver="1.3.0-research",
        parameter_hash="quality-parameters",
        parameters=PARAMETERS,
        initial_equity=Decimal("1500"),
    )


if __name__ == "__main__":
    unittest.main()
