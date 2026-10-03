"""Freeze the existing independent-session and quality behavior before extraction."""

from dataclasses import replace
from decimal import Decimal
import unittest

from test_minute_replay import PARAMETERS, _multi_session_records
from us_quant.targeted_data_quality import run_targeted_data_quality
from us_quant.targeted_robustness import (
    group_regular_sessions,
    run_targeted_robustness,
)


class MinuteEvidenceQualityCharacterizationTests(unittest.TestCase):
    def test_complete_session_is_robustness_usable_and_high_quality(self) -> None:
        records = _enriched_records(1)
        robustness = _run_robustness(records)
        quality = run_targeted_data_quality(robustness, records)

        self.assertEqual(robustness.total_sessions, 1)
        self.assertEqual(robustness.usable_sessions, 1)
        self.assertEqual(quality.sessions[0].raw_rows, 346)
        self.assertEqual(quality.sessions[0].usable_rows, 346)
        self.assertEqual(quality.sessions[0].completeness, Decimal("1"))
        self.assertEqual(quality.sessions[0].maximum_consecutive_missing, 0)
        self.assertEqual(quality.sessions[0].p95_source_age_seconds, Decimal("1.25"))
        self.assertTrue(quality.sessions[0].high_quality)

    def test_robustness_requires_start_and_end_of_evaluation_window(self) -> None:
        first_session = _enriched_records(1)
        second_session = tuple(
            row
            for row in _enriched_records(2)
            if row.minute[:10] == "2026-07-21" and row.minute[11:16] != "14:00"
        )
        records = first_session + second_session
        result = _run_robustness(records)

        self.assertEqual(result.total_sessions, 2)
        self.assertEqual(result.usable_sessions, 1)
        self.assertEqual(result.skipped_sessions, ("2026-07-21",))

    def test_three_consecutive_missing_minutes_fail_quality(self) -> None:
        records = _enriched_records(1)
        degraded = tuple(
            row for index, row in enumerate(records)
            if index not in {100, 101, 102}
        )
        robustness = _run_robustness(degraded)
        quality = run_targeted_data_quality(robustness, degraded)

        session = quality.sessions[0]
        self.assertEqual(session.maximum_consecutive_missing, 3)
        self.assertFalse(session.high_quality)
        self.assertIn("连续缺口超过 2 分钟", session.failure_reasons)

    def test_one_stale_minute_is_retained_and_existing_quality_rule_is_frozen(self) -> None:
        records = _enriched_records(1)
        degraded = tuple(
            replace(row, stale=True, stale_reason="stale")
            if index == 100 else row
            for index, row in enumerate(records)
        )
        robustness = _run_robustness(degraded)
        quality = run_targeted_data_quality(robustness, degraded)

        session = quality.sessions[0]
        self.assertEqual(session.stale_rows, 1)
        self.assertEqual(session.usable_rows, 345)
        self.assertTrue(session.high_quality)

    def test_age_over_five_seconds_fails_quality(self) -> None:
        records = tuple(
            replace(row, source_age_seconds=6.0)
            for row in _enriched_records(1)
        )
        robustness = _run_robustness(records)
        quality = run_targeted_data_quality(robustness, records)

        session = quality.sessions[0]
        self.assertEqual(session.p95_source_age_seconds, Decimal("6"))
        self.assertFalse(session.high_quality)
        self.assertIn("行情年龄 P95 超过 5 秒", session.failure_reasons)

    def test_regular_session_grouping_uses_new_york_trading_dates(self) -> None:
        sessions = group_regular_sessions(_enriched_records(2))
        self.assertEqual(
            tuple(session_date for session_date, _rows in sessions),
            ("2026-07-20", "2026-07-21"),
        )


def _enriched_records(days: int):
    return tuple(
        replace(row, source_age_seconds=1.25)
        for row in _multi_session_records(days)
    )


def _run_robustness(records):
    return run_targeted_robustness(
        records,
        strategy_version_id="characterization-version",
        strategy_semver="1.0.0-research",
        parameter_hash="characterization-parameters",
        parameters=PARAMETERS,
        initial_equity=Decimal("1500"),
    )


if __name__ == "__main__":
    unittest.main()
